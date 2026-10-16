# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake selected evaluated meshes into Picoware RGB332 billboard views."""
import struct
import math
from pathlib import Path
import shutil
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector


def pack_rgba(rgba, width, height, pixel_art=False, outline=False, scene_linear=True):
    """Convert bottom-up RGBA to RGB332/mask; byte PNG buffers are already sRGB."""
    from .codec import linear_to_srgb
    if len(rgba) != width * height * 4:
        raise ValueError('Unexpected rendered pixel count')
    pixels = bytearray(width * height)
    mask = bytearray((width * height + 7) // 8)
    for y in range(height):
        for x in range(width):
            src = ((height - 1 - y) * width + x) * 4
            dst = y * width + x
            if rgba[src + 3] >= 0.5:
                r, g, b = ((linear_to_srgb(rgba[src + k]) if scene_linear else max(0.0, min(1.0, rgba[src + k]))) for k in range(3))
                pixels[dst] = (min(7, int(r * 7 + .5)) << 5 |
                               min(7, int(g * 7 + .5)) << 2 |
                               min(3, int(b * 3 + .5)))
                mask[dst >> 3] |= 0x80 >> (dst & 7)
    if pixel_art:
        from .pixelart import stylize
        return stylize(pixels, mask, width, height, outline)
    return bytes(pixels), bytes(mask)


def sample_frames(start, end, poses, loop=True):
    """Loop ranges exclude the closing frame; one-shot ranges include it."""
    if not 1 <= poses <= 8 or end < start or (poses > 1 and end == start):
        raise ValueError('Choose 1–8 poses and an increasing frame range')
    if poses == 1:
        return [float(start)]
    divisor = poses if loop else poses - 1
    return [start + (end - start) * i / divisor for i in range(poses)]


def set_sample_frame(scene, frame):
    whole = math.floor(frame)
    scene.frame_set(whole, subframe=frame-whole)


def payload_bytes(resolution, directions, poses):
    return directions * poses * (resolution**2 + (resolution**2 + 7)//8)


def bake(context, filepath, resolution=16, directions=8, forward='NEG_Y',
         animated=False, frame_start=1, frame_end=25, poses=4, playback_fps=8.0, loop=True,
         pixel_art=False, outline=False, azimuth=0.0, elevation=0.0):
    if resolution not in (8, 16) or directions not in (1, 4, 8):
        raise ValueError('Unsupported billboard resolution or direction count')
    angles = {'POS_X': 0, 'POS_Y': math.pi / 2, 'NEG_X': math.pi, 'NEG_Y': -math.pi / 2}
    if forward not in angles:
        raise ValueError('Invalid forward axis')
    if not all(math.isfinite(v) for v in (azimuth, elevation)) or not -45 <= elevation <= 45:
        raise ValueError('Camera elevation must be between -45 and 45 degrees')
    source_scene = context.scene
    original_frame = source_scene.frame_current + source_scene.frame_subframe
    frames = sample_frames(frame_start, frame_end, poses, loop) if animated else [original_frame]
    if not math.isfinite(playback_fps) or not 0 < playback_fps <= 60:
        raise ValueError('Playback rate must be greater than zero and at most 60')
    objects = [ob for ob in context.selected_objects if ob.type == 'MESH']
    if not objects:
        raise ValueError('Select the meshes to bake')
    destination = Path(bpy.path.abspath(filepath))
    if destination.suffix.lower() not in ('.bin', '.billboard'):
        destination = destination.with_suffix('.bin')
    if destination.exists():
        raise ValueError('Output file already exists; choose a new name')
    if not destination.parent.is_dir():
        raise ValueError('Output parent directory does not exist')
    scene = None
    meshes, lights, cameras, materials = [], [], [], []
    material_map = {}
    temporary = Path(tempfile.mkdtemp(prefix='.billboard-', dir=destination.parent))
    try:
        scene = bpy.data.scenes.new('Picoware billboard bake')
        deps = context.evaluated_depsgraph_get()
        bounds = []
        for ob in objects:
            if ob.mode == 'EDIT':
                ob.update_from_editmode()
        context.view_layer.update()
        # First evaluate every exported pose for one shared camera volume.
        for frame in frames:
            set_sample_frame(source_scene, frame)
            context.view_layer.update()
            for ob in objects:
                evaluated = ob.evaluated_get(deps)
                bounds.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
        low = Vector(tuple(min(v[k] for v in bounds) for k in range(3)))
        high = Vector(tuple(max(v[k] for v in bounds) for k in range(3)))
        center = (low + high) * .5
        # One projection volume across ALL poses and views prevents frame jitter.
        view_vectors = []
        projected = []
        view_ups = []
        # Blender Y maps to negative engine ground Y: engine view indices
        # advance clockwise in Blender, matching the mesh coordinate conversion.
        for direction in range(directions):
            angle = angles[forward] + math.radians(azimuth) - direction * 2 * math.pi / directions
            elev = math.radians(elevation)
            outward = Vector((math.cos(angle)*math.cos(elev), math.sin(angle)*math.cos(elev), math.sin(elev)))
            right = Vector((-math.sin(angle), math.cos(angle), 0))
            up = outward.cross(right)
            view_vectors.append(outward)
            view_ups.append(up)
            projected.extend(((v-center).dot(right), (v-center).dot(up)) for v in bounds)
        margin = 1 if pixel_art and outline else .5
        bottom = min(y for _, y in projected)
        top = max(y for _, y in projected)
        width = 2 * max(abs(x) for x, _ in projected)
        extent = max(width, top-bottom) / (1 - 2*margin/resolution)
        if not math.isfinite(extent) or extent <= 0:
            raise ValueError('Selected meshes have no finite volume')
        # Align the shared bottom across all poses/views with the ground anchor.
        # Centering a wide, short model would leave a large transparent gap below it.
        vertical_offset = bottom + extent * (.5-margin/resolution)
        targets = [center + up*vertical_offset for up in view_ups]
        if not pixel_art and azimuth == 0 and elevation == 0:
            radius = max(math.hypot(v.x-center.x, v.y-center.y) for v in bounds)
            extent = max(2*radius, high.z-low.z)*1.1
            targets = [Vector((center.x, center.y, low.z+extent*.47)) for _ in view_vectors]
        camera = bpy.data.cameras.new('Billboard camera'); cameras.append(camera)
        cam = bpy.data.objects.new('Billboard camera', camera); scene.collection.objects.link(cam)
        camera.type = 'ORTHO'; camera.ortho_scale = extent; camera.clip_end = extent * 30
        scene.camera = cam
        # Camera-relative illumination gives consistent readable views.
        for name, energy, size in [('Key', 1000, 5), ('Fill', 500, 5)]:
            data = bpy.data.lights.new(name, 'AREA'); lights.append(data)
            light = bpy.data.objects.new(name, data); scene.collection.objects.link(light)
            light.parent = cam; light.location = (-extent*.3 if name=='Key' else extent*.3, extent*.4, extent*.1)
            data.energy = energy * (extent / 5) ** 2; data.shape = 'DISK'; data.size = extent
        world = bpy.data.worlds.new('Billboard world'); scene.world = world
        world.use_nodes = True; world.node_tree.nodes['Background'].inputs[1].default_value = .5
        scene.render.engine = 'CYCLES'; scene.cycles.samples = 16; scene.cycles.use_denoising = True
        if pixel_art:
            scene.cycles.samples = 1
            scene.cycles.use_denoising = False
            scene.cycles.filter_width = .01
            scene.cycles.seed = 0
            scene.cycles.use_animated_seed = False
            scene.render.dither_intensity = 0
        scene.render.film_transparent = True
        scene.render.resolution_x = scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.view_settings.view_transform = 'Standard'
        scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGBA'
        packed = temporary / 'asset.billboard'
        with packed.open('wb') as stream:
            stream.write(struct.pack('<4sBBBBHHffI', b'PWBB', 1, directions, len(frames),
                                     int(bool(loop)), resolution, resolution, playback_fps, extent,
                                     payload_bytes(resolution, directions, len(frames))))
        for pose, frame in enumerate(frames):
            set_sample_frame(source_scene, frame)
            context.view_layer.update()
            clones = []
            material_map.clear()  # Capture material colors separately at each sampled pose.
            for ob in objects:
                evaluated = ob.evaluated_get(deps)
                mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=deps)
                meshes.append(mesh)
                clone = bpy.data.objects.new(ob.name + ' bake', mesh)
                scene.collection.objects.link(clone)
                clone.matrix_world = evaluated.matrix_world.copy()
                for i, slot in enumerate(ob.material_slots):
                    if slot.link == 'OBJECT' and i < len(mesh.materials):
                        mesh.materials[i] = slot.material
                if pixel_art:
                    # Copy shaders, preserving linked base-color textures. Source materials stay intact.
                    if not mesh.materials:
                        mesh.materials.append(None)
                    for i, original in enumerate(list(mesh.materials)):
                        key = original.as_pointer() if original else 0
                        if key not in material_map:
                            flat = original.copy() if original else bpy.data.materials.new('Pixel default')
                            materials.append(flat); flat.use_nodes = True
                            tree = flat.node_tree
                            output = tree.get_output_node('CYCLES')
                            base = None
                            if original and original.use_nodes and output and output.inputs['Surface'].is_linked:
                                shader = output.inputs['Surface'].links[0].from_node
                                seen = set()
                                while shader.type == 'REROUTE':
                                    if shader.as_pointer() in seen or not shader.inputs[0].is_linked:
                                        raise ValueError('Invalid surface reroute in material ' + original.name)
                                    seen.add(shader.as_pointer())
                                    shader = shader.inputs[0].links[0].from_node
                                if shader.type != 'BSDF_PRINCIPLED':
                                    raise ValueError('Pixel Art needs a Principled shader connected to Material Output: ' + original.name)
                                base = shader.inputs['Base Color']
                            emission = tree.nodes.new('ShaderNodeEmission')
                            emission.inputs['Color'].default_value = base.default_value if base else (original.diffuse_color if original else (.8,.8,.8,1))
                            if base and base.is_linked:
                                tree.links.new(base.links[0].from_socket, emission.inputs['Color'])
                            if output is None: output = tree.nodes.new('ShaderNodeOutputMaterial')
                            tree.links.new(emission.outputs[0], output.inputs['Surface'])
                            material_map[key] = flat
                        mesh.materials[i] = material_map[key]
                clones.append(clone)
            set_sample_frame(scene, frame)
            for direction in range(directions):
                target = targets[direction]
                cam.location = target + view_vectors[direction] * extent * 3
                cam.rotation_euler = (target-cam.location).to_track_quat('-Z','Y').to_euler()
                stem = ('pose_%02d_view_%02d' % (pose,direction) if animated else 'view_%02d' % direction)
                scene.render.filepath = str(temporary / (stem + '.png'))
                bpy.ops.render.render(write_still=True, scene=scene.name)
                image = bpy.data.images.load(scene.render.filepath, check_existing=False)
                try:
                    pixels, mask = pack_rgba(image.pixels[:], resolution, resolution, pixel_art, outline, scene_linear=image.is_float)
                finally:
                    bpy.data.images.remove(image)
                if not any(mask):
                    raise ValueError('Rendered view is empty')
                with packed.open('ab') as stream:
                    stream.write(pixels)
                    stream.write(mask)
                Path(scene.render.filepath).unlink()
            for clone in clones:
                mesh = clone.data
                bpy.data.objects.remove(clone, do_unlink=True)
                meshes.remove(mesh)
                bpy.data.meshes.remove(mesh)
        metadata = {'format': 'picoware-rgb332-mask', 'width': resolution, 'height': resolution,
                    'directions': directions, 'poses': len(frames), 'forward_axis': forward,
                    'sample_frames': frames, 'playback_fps': playback_fps, 'loop': bool(loop),
                    'view_order': 'camera azimuth decreases clockwise around Blender +Z from forward axis',
                    'world_height': extent, 'pixel_art': pixel_art, 'outline': outline,
                    'azimuth': azimuth, 'elevation': elevation,
                    'mask_order': 'row-major top-down, MSB-first, no row padding',
                    'payload_bytes': payload_bytes(resolution, directions, len(frames))}
        packed.rename(destination)
        return destination, metadata
    finally:
        if scene is not None:
            world = scene.world
            for ob in list(scene.objects): bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.scenes.remove(scene)
            if world is not None and world.users == 0: bpy.data.worlds.remove(world)
        for collection, items in ((bpy.data.meshes,meshes),(bpy.data.cameras,cameras),(bpy.data.lights,lights),(bpy.data.materials,materials)):
            for item in items:
                if item.users == 0: collection.remove(item)
        if temporary.exists(): shutil.rmtree(temporary)
        set_sample_frame(source_scene, original_frame)


_preview_state = None


def stop_preview():
    """Stop the preview and restore the timeline; also called on add-on unload."""
    from .pixel_preview import stop_pixel_preview
    stop_pixel_preview()
    global _preview_state
    state, _preview_state = _preview_state, None
    if bpy.app.timers.is_registered(_preview_tick):
        bpy.app.timers.unregister(_preview_tick)
    if state and state['scene'].name in bpy.data.scenes:
        set_sample_frame(state['scene'],state['original'])


def _preview_tick():
    global _preview_state
    state = _preview_state
    if state is None:
        return None
    scene = state['scene']
    if scene.name not in bpy.data.scenes or bpy.context.scene != scene:
        _preview_state = None
        if scene.name in bpy.data.scenes:
            set_sample_frame(scene,state['original'])
        return None
    state['index'] += 1
    if state['index'] >= len(state['frames']):
        if not state['loop']:
            _preview_state = None
            set_sample_frame(scene,state['original'])
            return None
        state['index'] = 0
    set_sample_frame(scene,state['frames'][state['index']])
    return 1/state['fps']


class SPRITE3D_OT_stop_preview(bpy.types.Operator):
    bl_idname = 'sprite3d.stop_billboard_preview'
    bl_label = 'Stop Preview'
    bl_description = 'Stop sampled-pose playback and restore the original timeline frame'
    def execute(self, context):
        stop_preview()
        return {'FINISHED'}


class SPRITE3D_OT_preview_billboard(bpy.types.Operator):
    bl_idname = 'sprite3d.preview_billboard_poses'
    bl_label = 'Preview Sampled Poses'
    bl_description = 'Play exactly the exported samples; Stop Preview restores the original frame'
    frame_start: IntProperty(default=1)
    frame_end: IntProperty(default=25)
    poses: IntProperty(default=4, min=1, max=8)
    playback_fps: FloatProperty(default=8, min=.1, max=60)
    loop: BoolProperty(default=True)

    def execute(self, context):
        global _preview_state
        if context.screen is not None and context.screen.is_animation_playing:
            self.report({'ERROR'}, 'Stop timeline playback before previewing sampled poses')
            return {'CANCELLED'}
        try:
            frames = sample_frames(self.frame_start,self.frame_end,self.poses,self.loop)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        stop_preview()
        scene = context.scene
        _preview_state = {'scene':scene,'original':scene.frame_current+scene.frame_subframe,
                          'frames':frames,'index':0,'fps':self.playback_fps,'loop':self.loop}
        set_sample_frame(scene,frames[0])
        bpy.app.timers.register(_preview_tick,first_interval=1/self.playback_fps)
        return {'FINISHED'}


class SPRITE3D_OT_export_billboard(bpy.types.Operator, ExportHelper):
    bl_idname = 'export_scene.picoware_billboard'
    bl_label = 'Export Picoware Billboard'
    bl_description = 'Bake selected meshes into one static or animated .bin asset'
    filename_ext = '.bin'
    filter_glob: StringProperty(default='*.bin;*.billboard', options={'HIDDEN'})
    resolution: EnumProperty(name='Resolution', items=[(str(n),str(n)+' x '+str(n),'') for n in (16,8)], default='16')
    directions: EnumProperty(name='Views', items=[(str(n),str(n),'') for n in (1,4,8)], default='8')
    forward: EnumProperty(name='Model Forward', items=[('POS_X','+X',''),('NEG_X','-X',''),('POS_Y','+Y',''),('NEG_Y','-Y','')], default='NEG_Y')
    pixel_art: BoolProperty(name='Pixel Art', default=True, description='Flat base colors, a fixed 16-color RGB332 palette, and crisp pixel edges')
    outline: BoolProperty(name='Dark Outline', default=False, description='Add a one-pixel outside outline; reserves room in the shared framing')
    azimuth: FloatProperty(name='View Angle', default=0, min=-180, max=180, description='Rotate baked views relative to Model Forward; zero preserves engine-facing alignment')
    elevation: FloatProperty(name='Camera Height Angle', default=0, min=-45, max=45, description='Degrees above the model; try 10 to separate overlapping feet')
    animated: BoolProperty(name='Animated', default=False)
    frame_start: IntProperty(name='Frame Start', default=1)
    frame_end: IntProperty(name='Frame End', default=25)
    poses: IntProperty(name='Exported Poses', default=4, min=1, max=8)
    playback_fps: FloatProperty(name='Poses per Second', default=8, min=.1, max=60)
    loop: BoolProperty(name='Loop (Exclude Closing Frame)', default=True)

    use_timeline: BoolProperty(name='Use Timeline Range and Speed', default=True)
    advanced: BoolProperty(name='Advanced', default=False)

    def settings(self, context):
        if self.animated and self.use_timeline:
            start, end = context.scene.frame_start, context.scene.frame_end
            frames = sample_frames(start,end,self.poses,self.loop)
            source_fps = context.scene.render.fps / context.scene.render.fps_base
            intervals = self.poses if self.loop else max(1,self.poses-1)
            rate = intervals * source_fps / max(1,end-start)
            if not .1 <= rate <= 60:
                raise ValueError('Timeline speed is outside 0.1–60 poses/sec; adjust range or use Advanced timing')
            return start,end,rate
        return self.frame_start,self.frame_end,self.playback_fps

    def check(self, context):
        if Path(self.filepath).suffix.lower() in ('.bin', '.billboard'):
            return False
        return ExportHelper.check(self, context)

    def invoke(self, context, event):
        if not self.properties.is_property_set('frame_start'):
            self.frame_start = context.scene.frame_start
        if not self.properties.is_property_set('frame_end'):
            self.frame_end = context.scene.frame_end
        if not self.properties.is_property_set('forward'):
            self.forward = context.scene.get('picoware_billboard_forward','NEG_Y')
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self,'pixel_art')
        layout.prop(self,'resolution')
        if self.pixel_art:
            layout.prop(self,'outline')
        layout.prop(self,'azimuth')
        layout.prop(self,'elevation')
        preview = layout.operator('sprite3d.preview_pixel_art', text='Preview Exported Pixels', icon='IMAGE_DATA')
        try:
            start, end, rate = self.settings(context)
            preview.settings_json = __import__('json').dumps(dict(resolution=int(self.resolution), directions=int(self.directions), forward=self.forward, animated=self.animated, frame_start=start, frame_end=end, poses=self.poses, playback_fps=rate, loop=self.loop, pixel_art=self.pixel_art, outline=self.outline, azimuth=self.azimuth, elevation=self.elevation))
        except ValueError as exc:
            preview.settings_json = __import__('json').dumps({'error': str(exc)})
        layout.label(text='Preview cycles poses: native size + enlarged, all views')
        layout.prop(self,'animated')
        if self.animated:
            layout.prop(self,'poses')
            layout.prop(self,'loop')
            try:
                start,end,rate = self.settings(context)
                frames = sample_frames(start,end,self.poses,self.loop)
                layout.label(text='Timeline %g–%g · %.2f poses/sec' % (start,end,rate))
                preview = layout.operator('sprite3d.preview_billboard_poses', icon='PLAY')
                preview.frame_start=start; preview.frame_end=end; preview.poses=self.poses
                preview.playback_fps=rate; preview.loop=self.loop
                layout.operator('sprite3d.stop_billboard_preview',icon='PAUSE')
            except ValueError as exc:
                layout.label(text=str(exc),icon='ERROR')
            layout.label(text='For loops, end with the same pose you started with')
        count = self.poses if self.animated else 1
        size = payload_bytes(int(self.resolution),int(self.directions),count)
        layout.label(text='Single .bin asset file')
        layout.label(text='Image + mask payload: %.1f KiB' % (size/1024))
        layout.label(text='16 x 16 default; 8 x 8 optional')
        layout.prop(self,'advanced',icon='PREFERENCES')
        if self.advanced:
            for name in ('directions','forward'):
                layout.prop(self,name)
            if self.animated:
                layout.prop(self,'use_timeline')
                if not self.use_timeline:
                    for name in ('frame_start','frame_end','playback_fps'):
                        layout.prop(self,name)
            layout.label(text='Memory excludes loading copies and object overhead')

    def execute(self, context):
        try:
            stop_preview()
            start,end,rate = self.settings(context)
            path, info = bake(context, self.filepath, int(self.resolution), int(self.directions), self.forward,
                              self.animated,start,end,self.poses,rate,self.loop,
                              self.pixel_art,self.outline,self.azimuth,self.elevation)
            self.report({'INFO'}, 'Exported %d views x %d poses (%d bytes) to %s' % (info['directions'],info['poses'],info['payload_bytes'],path))
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

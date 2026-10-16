# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake selected evaluated meshes into Picoware RGB332 billboard views."""
import json
import math
from pathlib import Path
import shutil
import tempfile

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector


def pack_rgba(rgba, width, height):
    """Convert bottom-up scene-linear RGBA to top-down RGB332/MSB-first mask."""
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
                r, g, b = (linear_to_srgb(rgba[src + k]) for k in range(3))
                pixels[dst] = (min(7, int(r * 7 + .5)) << 5 |
                               min(7, int(g * 7 + .5)) << 2 |
                               min(3, int(b * 3 + .5)))
                mask[dst >> 3] |= 0x80 >> (dst & 7)
    return bytes(pixels), bytes(mask)


def bake(context, filepath, resolution=64, directions=8, forward='NEG_Y'):
    if resolution not in (32, 64, 96, 128) or directions not in (1, 4, 8):
        raise ValueError('Unsupported billboard resolution or direction count')
    angles = {'POS_X': 0, 'POS_Y': math.pi / 2, 'NEG_X': math.pi, 'NEG_Y': -math.pi / 2}
    if forward not in angles:
        raise ValueError('Invalid forward axis')
    objects = [ob for ob in context.selected_objects if ob.type == 'MESH']
    if not objects:
        raise ValueError('Select the meshes to bake')
    destination = Path(bpy.path.abspath(filepath)).with_suffix('')
    if destination.exists():
        raise ValueError('Output folder already exists; choose a new name')
    if not destination.parent.is_dir():
        raise ValueError('Output parent directory does not exist')
    scene = None
    meshes, lights, cameras = [], [], []
    temporary = Path(tempfile.mkdtemp(prefix='.billboard-', dir=destination.parent))
    try:
        scene = bpy.data.scenes.new('Picoware billboard bake')
        deps = context.evaluated_depsgraph_get()
        bounds = []
        for ob in objects:
            if ob.mode == 'EDIT':
                ob.update_from_editmode()
        context.view_layer.update()
        for ob in objects:
            evaluated = ob.evaluated_get(deps)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=deps)
            meshes.append(mesh)
            clone = bpy.data.objects.new(ob.name + ' bake', mesh)
            scene.collection.objects.link(clone)
            clone.matrix_world = evaluated.matrix_world.copy()
            # Copy material overrides; never mutate source materials.
            for i, slot in enumerate(ob.material_slots):
                if slot.link == 'OBJECT' and i < len(mesh.materials):
                    mesh.materials[i] = slot.material
            bounds.extend(clone.matrix_world @ Vector(corner) for corner in clone.bound_box)
        low = Vector(tuple(min(v[k] for v in bounds) for k in range(3)))
        high = Vector(tuple(max(v[k] for v in bounds) for k in range(3)))
        center = (low + high) * .5
        radius = max(math.hypot(v.x-center.x, v.y-center.y) for v in bounds)
        extent = max(2 * radius, high.z-low.z) * 1.1
        if not math.isfinite(extent) or extent <= 0:
            raise ValueError('Selected meshes have no finite volume')
        # All views share framing. Bottom includes 3% ground clearance.
        target = Vector((center.x, center.y, low.z + extent * .47))
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
        scene.render.film_transparent = True
        scene.render.resolution_x = scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.view_settings.view_transform = 'Standard'
        scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGBA'
        files = []
        for direction in range(directions):
            angle = angles[forward] + direction * 2 * math.pi / directions
            cam.location = target + Vector((math.cos(angle), math.sin(angle), 0)) * extent * 3
            cam.rotation_euler = (target-cam.location).to_track_quat('-Z','Y').to_euler()
            stem = 'view_%02d' % direction
            scene.render.filepath = str(temporary / (stem + '.png'))
            bpy.ops.render.render(write_still=True, scene=scene.name)
            # Loading the saved PNG applies its color-space conversion consistently.
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            try:
                pixels, mask = pack_rgba(image.pixels[:], resolution, resolution)
            finally:
                bpy.data.images.remove(image)
            if not any(mask):
                raise ValueError('Rendered view is empty')
            (temporary / (stem + '.rgb332')).write_bytes(pixels)
            (temporary / (stem + '.mask')).write_bytes(mask)
            files.append({'direction': direction, 'pose': 0, 'pixels': stem+'.rgb332', 'mask': stem+'.mask'})
        metadata = {'format': 'picoware-rgb332-mask', 'width': resolution, 'height': resolution,
                    'directions': directions, 'poses': 1, 'forward_axis': forward,
                    'view_order': 'camera azimuth increases counterclockwise around Blender +Z from forward axis',
                    'world_height': extent, 'ground_padding_fraction': .03,
                    'mask_order': 'row-major top-down, MSB-first, no row padding',
                    'payload_bytes': directions * (resolution**2 + (resolution**2+7)//8), 'views': files}
        (temporary / 'billboard.json').write_text(json.dumps(metadata, indent=2)+'\n')
        (temporary / 'load_billboard.py').write_text(LOADER)
        temporary.rename(destination)
        return destination, metadata
    finally:
        if scene is not None:
            world = scene.world
            for ob in list(scene.objects): bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.scenes.remove(scene)
            if world is not None and world.users == 0: bpy.data.worlds.remove(world)
        for collection, items in ((bpy.data.meshes,meshes),(bpy.data.cameras,cameras),(bpy.data.lights,lights)):
            for item in items:
                if item.users == 0: collection.remove(item)
        if temporary.exists(): shutil.rmtree(temporary)


LOADER = '''"""Load adjacent exported files on Picoware. Attach with entity.set_billboard(asset, height)."""
def load_billboard(directory):
    import json
    from picoware.engine.billboard import Billboard
    from picoware.engine.image import Image
    from picoware.system.vector import Vector
    with open(directory + '/billboard.json') as stream:
        info = json.load(stream)
    result = Billboard(info['directions'], info['poses'])
    for view in info['views']:
        with open(directory + '/' + view['pixels'], 'rb') as stream:
            pixels = stream.read()
        with open(directory + '/' + view['mask'], 'rb') as stream:
            mask = stream.read()
        image = Image(Vector(info['width'], info['height']), True, pixels, None, mask)
        result.set_direction_pose(view['direction'], view['pose'], image)
        del image, pixels, mask
    return result, info['world_height']
'''


class SPRITE3D_OT_export_billboard(bpy.types.Operator, ExportHelper):
    bl_idname = 'export_scene.picoware_billboard'
    bl_label = 'Export Picoware Billboard'
    bl_description = 'Bake selected meshes at the current frame into directional RGB332 images and masks'
    filename_ext = '.billboard'
    filter_glob: StringProperty(default='*.billboard', options={'HIDDEN'})
    resolution: EnumProperty(name='Resolution', items=[(str(n),str(n)+' x '+str(n),'') for n in (32,64,96,128)], default='64')
    directions: EnumProperty(name='Views', items=[(str(n),str(n),'') for n in (1,4,8)], default='8')
    forward: EnumProperty(name='Model Forward', items=[('POS_X','+X',''),('NEG_X','-X',''),('POS_Y','+Y',''),('NEG_Y','-Y','')], default='NEG_Y')
    def execute(self, context):
        try:
            path, info = bake(context, self.filepath, int(self.resolution), int(self.directions), self.forward)
            self.report({'INFO'}, 'Exported %d views (%d bytes) to %s' % (info['directions'],info['payload_bytes'],path))
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

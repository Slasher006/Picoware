# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender 5 extension for the specific binary layout found in knight.sprite3d.

Do not add 'from __future__ import annotations': Blender RNA properties below
must be evaluated when the classes are created, not stored as strings.
"""
import hashlib
import math
from pathlib import Path
import re
import traceback

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import codec

ATTR_COLOR = 's3d_color565'
ATTR_EXTRA = 's3d_extra16'
ATTR_VALID = 's3d_metadata_valid'
SOURCE_PATH = 'sprite3d_source_path'
SOURCE_AXES = 'sprite3d_source_axes'
SOURCE_SCALE = 'sprite3d_import_scale'
SOURCE_HASH = 'sprite3d_source_sha256'
MAT_COLOR = 'sprite3d_color565'
MAT_EXTRA = 'sprite3d_extra16'
OLD_MATERIAL = re.compile(r'^s3d_([0-9a-fA-F]{8})(?:\.\d+)?$')

AXES_IMPORT = (
    ('Y_UP', 'Source Y Up (knight)', 'Convert source (X, Y, Z) to Blender (X, -Z, Y)'),
    ('BLENDER', 'Already Blender Z Up', 'Keep coordinates in their original axes'),
)
AXES_EXPORT = (
    ('AUTO', 'Automatic / Match Import', 'Use each object\'s saved import axes and undo its import scale; new meshes use source Y Up'),
) + AXES_IMPORT
COLOR_MODES = (
    ('AUTO', 'Material Colors / Keep Extra Data', 'Read flat Principled Base Color as RGB565; preserve wireframe and padding per face; missing materials retain original data'),
    ('PRESERVE', 'Preserve Original Face Data', 'Ignore material edits and assignments; preserve both original uint16 fields per face'),
    ('MATERIAL', 'Require Material Colors', 'Every face must have a supported flat-color material; preserve wireframe and padding per face'),
)


def _material_word(material):
    """Read our properties; accept the earlier OBJ converter's material names."""
    if material is None:
        return None
    if MAT_COLOR in material and MAT_EXTRA in material:
        color, extra = material[MAT_COLOR], material[MAT_EXTRA]
        if isinstance(color, int) and isinstance(extra, int) and 0 <= color <= 65535 and 0 <= extra <= 65535:
            return color, extra
        raise codec.FormatError(f'Material {material.name!r} has invalid Sprite3D custom data.')
    match = OLD_MATERIAL.fullmatch(material.name)
    if match:
        word = int(match.group(1), 16)
        return word & 65535, word >> 16
    return None


def _principled(material):
    """Find the Principled shader directly feeding the active material output.

    Reroutes are accepted. Shader groups/mixes and procedural colors are not
    approximated silently: this file format only stores a flat face color.
    """
    tree = material.node_tree
    if tree is None:
        return None
    outputs = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL']
    output = next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)
    if output is None or not output.inputs['Surface'].is_linked:
        return None
    node = output.inputs['Surface'].links[0].from_node
    visited = set()
    while node.type == 'REROUTE' and node.as_pointer() not in visited:
        visited.add(node.as_pointer())
        if not node.inputs[0].is_linked:
            return None
        node = node.inputs[0].links[0].from_node
    return node if node.type == 'BSDF_PRINCIPLED' else None


def _flat_material_color(material):
    if material.node_tree is None or getattr(material, 'use_nodes', True) is False:
        return tuple(material.diffuse_color[:3])
    shader = _principled(material)
    if shader is None:
        raise codec.FormatError(
            f'Material {material.name!r}: use a Principled BSDF connected to Material Output, '
            'or export with Preserve Original Face Data.')
    socket = shader.inputs.get('Base Color')
    if socket is None or socket.is_linked:
        raise codec.FormatError(
            f'Material {material.name!r}: linked/procedural Base Color cannot be exported. '
            'Use a solid Base Color or Preserve Original Face Data; textures are not baked.')
    return tuple(socket.default_value[:3])


def _new_material(color, extra):
    material = bpy.data.materials.new(f'S3D_{color:04X}_{extra:04X}')
    linear = codec.rgb565_to_linear(color)
    material.diffuse_color = (*linear, 1.0)
    # Blender versions with the switch need it; versions where nodes are always
    # enabled may expose it as read-only or remove it.
    if hasattr(material, 'use_nodes'):
        try:
            material.use_nodes = True
        except (AttributeError, TypeError):
            pass
    if material.node_tree is None:
        bpy.data.materials.remove(material)
        raise codec.FormatError('Blender could not create the material node tree.')
    tree = material.node_tree
    tree.nodes.clear()
    shader = tree.nodes.new('ShaderNodeBsdfPrincipled')
    shader.inputs['Base Color'].default_value = (*linear, 1.0)
    shader.inputs['Roughness'].default_value = 1.0
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    tree.links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    shader.location = (-240, 0)
    material[MAT_COLOR] = color
    material[MAT_EXTRA] = extra
    material['sprite3d_color_note'] = 'RGB565 color; extra low byte is wireframe, high byte is preserved padding'
    return material


def _dispose_object(obj):
    """Remove only this object's newly-created unused data; never scene orphans."""
    mesh = obj.data
    materials = list(mesh.materials)
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for material in materials:
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)


def load_sprite(context, filepath, *, axes='Y_UP', scale=1.0, weld=True):
    path = Path(bpy.path.abspath(str(filepath))).expanduser().resolve()
    with path.open('rb') as stream:
        data = stream.read(codec.MAX_BYTES + 1)
    triangles = codec.decode(data)
    vertices, faces = codec.build_mesh_arrays(triangles, axes, scale, weld)
    mesh = bpy.data.meshes.new(path.stem)
    obj = None
    made_materials = []
    try:
        mesh.from_pydata(vertices, [], faces)
        # Do not run mesh.validate(), weld by distance, recalculate winding, or
        # reorder triangles: these may discard/reorder valid original records.
        mesh.update()
        colors = mesh.attributes.new(name=ATTR_COLOR, type='INT', domain='FACE')
        extras = mesh.attributes.new(name=ATTR_EXTRA, type='INT', domain='FACE')
        valid = mesh.attributes.new(name=ATTR_VALID, type='INT', domain='FACE')
        # Re-fetch after adding attributes; collection storage may be reallocated.
        colors = mesh.attributes[ATTR_COLOR]
        extras = mesh.attributes[ATTR_EXTRA]
        valid = mesh.attributes[ATTR_VALID]
        colors.data.foreach_set('value', [t.color for t in triangles])
        extras.data.foreach_set('value', [t.extra for t in triangles])
        valid.data.foreach_set('value', [1] * len(triangles))
        slots = {}
        for polygon, triangle in zip(mesh.polygons, triangles):
            key = (triangle.color, triangle.extra)
            if key not in slots:
                material = _new_material(*key)
                made_materials.append(material)
                slots[key] = len(mesh.materials)
                mesh.materials.append(material)
            polygon.material_index = slots[key]
            polygon.use_smooth = False
        obj = bpy.data.objects.new(path.stem, mesh)
        context.collection.objects.link(obj)
        obj[SOURCE_PATH] = str(path)
        obj[SOURCE_AXES] = axes
        obj[SOURCE_SCALE] = float(scale)
        obj[SOURCE_HASH] = hashlib.sha256(data).hexdigest()
        obj['sprite3d_original_triangle_count'] = len(triangles)
        obj['sprite3d_format'] = 'headerless_le_9f_2u16_v1'
        return obj
    except Exception:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for material in made_materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        raise


def _face_metadata(mesh, polygon_index):
    attrs = [mesh.attributes.get(name) for name in (ATTR_COLOR, ATTR_EXTRA, ATTR_VALID)]
    if any(a is None or a.domain != 'FACE' or a.data_type != 'INT' for a in attrs):
        return None
    if attrs[2].data[polygon_index].value != 1:
        return None
    color, extra = (a.data[polygon_index].value for a in attrs[:2])
    if not 0 <= color <= 65535 or not 0 <= extra <= 65535:
        raise codec.FormatError(f'Face {polygon_index + 1} has invalid Sprite3D data.')
    return color, extra


def _is_identity(matrix):
    return all(matrix[r][c] == (1.0 if r == c else 0.0) for r in range(4) for c in range(4))


def collect_triangles(context, objects, *, apply_modifiers=True, apply_transform=True,
                      axes='AUTO', export_scale=1.0, color_mode='AUTO', default_extra=1,
                      fix_mirrors=True):
    """Extract without editing the original mesh, modifiers, or selection."""
    codec.check_scale(export_scale)
    objects = list(objects)
    if not objects:
        raise codec.FormatError('Select at least one mesh object to export.')
    for obj in objects:
        if obj.type != 'MESH':
            raise codec.FormatError(f'{obj.name!r} is not a mesh.')
        if obj.mode == 'EDIT':
            obj.update_from_editmode()
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    result, warnings = [], set()
    material_colors = {}
    for obj in objects:
        source_axes = obj.get(SOURCE_AXES, 'Y_UP') if axes == 'AUTO' else axes
        import_scale = float(obj.get(SOURCE_SCALE, 1.0)) if axes == 'AUTO' else 1.0
        codec.check_scale(import_scale)
        evaluated = None
        if apply_modifiers:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        else:
            mesh = obj.data
        try:
            if mesh is None:
                raise codec.FormatError(f'{obj.name!r}: no evaluated mesh could be obtained.')
            mesh.calc_loop_triangles()
            world = (evaluated.matrix_world if evaluated is not None else obj.matrix_world).copy()
            transform = apply_transform and not _is_identity(world)
            mirrored = apply_transform and world.to_3x3().determinant() < 0
            if any(mod.type == 'NODES' and mod.show_viewport for mod in obj.modifiers) and apply_modifiers:
                warnings.add('Geometry Nodes: only realized mesh geometry is exported; realize instances first')
            vertex_cache = {}
            no_metadata = 0
            for triangle in mesh.loop_triangles:
                polygon = mesh.polygons[triangle.polygon_index]
                face_data = _face_metadata(mesh, polygon.index)
                if face_data is None:
                    no_metadata += 1
                # Object-linked material overrides take precedence; evaluated
                # mesh slots also support materials created by modifiers.
                material = mesh.materials[polygon.material_index] if polygon.material_index < len(mesh.materials) else None
                if polygon.material_index < len(obj.material_slots):
                    slot = obj.material_slots[polygon.material_index]
                    if slot.link == 'OBJECT':
                        material = slot.material
                imported_material = _material_word(material)
                extra = (face_data[1] if face_data is not None else
                         imported_material[1] if imported_material is not None else default_extra)
                if color_mode == 'PRESERVE':
                    preserved = face_data if face_data is not None else imported_material
                    if preserved is None:
                        raise codec.FormatError(
                            f'{obj.name!r}, face {polygon.index + 1}: no original data to preserve. '
                            'Assign an imported material or choose Material Colors for new geometry.')
                    color = preserved[0]
                elif material is not None:
                    key = material.as_pointer()
                    if key not in material_colors:
                        material_colors[key] = codec.linear_to_rgb565(_flat_material_color(material))
                    color = material_colors[key]
                elif color_mode == 'MATERIAL':
                    raise codec.FormatError(f'{obj.name!r}, face {polygon.index + 1}: no material assigned.')
                elif face_data is not None:
                    color = face_data[0]
                else:
                    color = 0xFFFF
                    warnings.add('Faces without materials/original colors exported as white')
                indices = list(triangle.vertices)
                if mirrored and fix_mirrors:
                    indices[1], indices[2] = indices[2], indices[1]
                coordinates = []
                for index in indices:
                    if index not in vertex_cache:
                        value = mesh.vertices[index].co
                        if transform:
                            value = world @ value
                        xyz = codec.blender_to_source(value, source_axes, import_scale)
                        if export_scale != 1.0:
                            xyz = tuple(v * export_scale for v in xyz)
                        vertex_cache[index] = xyz
                    coordinates.extend(vertex_cache[index])
                result.append(codec.Triangle(tuple(coordinates), color, extra))
                if len(result) > codec.MAX_TRIANGLES:
                    raise codec.FormatError(f'Safety limit: {codec.MAX_TRIANGLES:,} triangles.')
            if no_metadata:
                warnings.add(
                    f'{obj.name}: {no_metadata} triangles lack original face data; '
                    'wireframe and padding use imported material data or the New Face Wireframe setting')
        finally:
            if evaluated is not None:
                evaluated.to_mesh_clear()
    if not result:
        raise codec.FormatError('The selected meshes contain no triangle faces.')
    return result, sorted(warnings)


def export_sprite(context, objects, filepath, *, backup=True, **settings):
    triangles, warnings = collect_triangles(context, objects, **settings)
    data = codec.encode(triangles)
    # Validate the complete result before touching an existing output.
    path = Path(bpy.path.abspath(str(filepath))).expanduser().absolute()
    backup_path = codec.write_atomic(path, data, backup=backup)
    return len(triangles), warnings, backup_path, hashlib.sha256(data).hexdigest()


def _report_export(operator, count, warnings, backup_path):
    for warning in warnings:
        print('Sprite3D warning:', warning)
    if warnings:
        operator.report({'WARNING'}, '; '.join(warnings)[:900])
    suffix = ' (backup created)' if backup_path is not None else ''
    operator.report({'INFO'}, f'Exported {count:,} triangles{suffix}')


def _error(operator, exc):
    # Keep an actionable short UI error; put the full exception in the console.
    traceback.print_exc()
    operator.report({'ERROR'}, str(exc)[:900])
    return {'CANCELLED'}


class SPRITE3D_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = 'import_scene.sprite3d_mesh'
    bl_label = 'Import Sprite3D Mesh'
    bl_description = 'Import the headerless 40-byte-per-triangle layout used by knight.sprite3d'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.sprite3d'
    filter_glob: StringProperty(default='*.sprite3d', options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    axes: EnumProperty(name='File Coordinates', items=AXES_IMPORT, default='Y_UP')
    scale: FloatProperty(name='Import Scale', default=1.0, min=0.000001, max=1000000.0)
    weld: BoolProperty(name='Merge Exact Duplicate Positions', default=True,
                      description='Makes connected editing easier; may join touching surfaces. Disable to keep every triangle independent')
    material_preview: BoolProperty(name='Show Material Colors', default=True,
                                  description='Switch 3D viewports to Material Preview so Base Color edits are visible')

    def draw(self, context):
        layout = self.layout
        layout.label(text='Headerless triangle mesh format only', icon='INFO')
        layout.prop(self, 'axes')
        layout.prop(self, 'scale')
        layout.prop(self, 'weld')
        layout.prop(self, 'material_preview')

    def execute(self, context):
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, 'Switch to Object Mode before importing.')
            return {'CANCELLED'}
        paths = [str(Path(self.directory) / entry.name) for entry in self.files] if self.files else [self.filepath]
        created = []
        try:
            for path in paths:
                created.append(load_sprite(context, path, axes=self.axes, scale=self.scale, weld=self.weld))
        except Exception as exc:
            for obj in created:
                _dispose_object(obj)
            return _error(self, exc)
        for obj in list(context.selected_objects):
            obj.select_set(False)
        for obj in created:
            obj.select_set(True)
        context.view_layer.objects.active = created[-1]
        if self.material_preview:
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.spaces.active.shading.color_type = 'MATERIAL'
                        area.spaces.active.shading.type = 'MATERIAL'
        count = sum(len(obj.data.polygons) for obj in created)
        self.report({'INFO'}, f'Imported {len(created)} mesh(es), {count:,} triangles')
        return {'FINISHED'}


class SPRITE3D_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = 'export_scene.sprite3d_mesh'
    bl_label = 'Export Sprite3D Mesh'
    bl_description = 'Export static geometry to the knight.sprite3d binary layout'
    bl_options = {'PRESET'}
    filename_ext = '.sprite3d'
    filter_glob: StringProperty(default='*.sprite3d', options={'HIDDEN'})
    selected_only: BoolProperty(name='Selected Objects Only', default=True)
    apply_modifiers: BoolProperty(name='Apply Modifiers', default=True,
                                 description='Export the evaluated mesh at the current frame; the modifier stack is not changed')
    apply_transform: BoolProperty(name='Apply Object Transforms', default=True,
                                 description='Include world-space location, rotation, scale and parenting')
    axes: EnumProperty(name='File Coordinates', items=AXES_EXPORT, default='AUTO')
    export_scale: FloatProperty(name='Additional Export Scale', default=1.0, min=0.000001, max=1000000.0)
    color_mode: EnumProperty(name='Colors / Data', items=COLOR_MODES, default='AUTO')
    default_wireframe: BoolProperty(name='New Face Wireframe', default=True,
                                    description='Draw outlines on new faces without imported data; padding is zero')
    fix_mirrors: BoolProperty(name='Correct Mirrored Winding', default=True,
                             description='Reverse winding when an applied object transform has a negative determinant')
    backup: BoolProperty(name='Back Up Existing File', default=True,
                         description='Make a unique timestamped .bak before replacing a file; failure stops the export')

    def invoke(self, context, event):
        if not self.filepath:
            obj = context.active_object
            source = obj.get(SOURCE_PATH) if obj is not None else None
            if source:
                path = Path(source)
                self.filepath = str(path.with_name(path.stem + '_edited.sprite3d'))
            else:
                self.filepath = bpy.path.clean_name(obj.name if obj else 'mesh') + '.sprite3d'
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'selected_only')
        layout.prop(self, 'apply_modifiers')
        layout.prop(self, 'apply_transform')
        layout.prop(self, 'axes')
        layout.prop(self, 'export_scale')
        layout.prop(self, 'color_mode')
        layout.prop(self, 'default_wireframe')
        layout.prop(self, 'fix_mirrors')
        layout.prop(self, 'backup')
        layout.label(text=f'Picoware limit: {codec.MAX_TRIANGLES:,} triangles', icon='INFO')
        layout.label(text='No textures, rigs or animation data')

    def execute(self, context):
        objects = list(context.selected_objects) if self.selected_only else list(context.scene.objects)
        objects = sorted((obj for obj in objects if obj.type == 'MESH'), key=lambda obj: obj.name)
        try:
            count, warnings, backup_path, _digest = export_sprite(
                context, objects, self.filepath, backup=self.backup,
                apply_modifiers=self.apply_modifiers, apply_transform=self.apply_transform,
                axes=self.axes, export_scale=self.export_scale, color_mode=self.color_mode,
                default_extra=int(self.default_wireframe), fix_mirrors=self.fix_mirrors)
            _report_export(self, count, warnings, backup_path)
            return {'FINISHED'}
        except Exception as exc:
            return _error(self, exc)


class SPRITE3D_OT_save_source(bpy.types.Operator):
    bl_idname = 'sprite3d.save_source'
    bl_label = 'Save Back to Source'
    bl_description = 'Export the active mesh to its imported path, always making a backup before replacing existing data'
    bl_options = set()  # External writes are not undoable.
    color_mode: EnumProperty(name='Colors / Data', items=COLOR_MODES, default='AUTO')
    apply_modifiers: BoolProperty(name='Apply Modifiers', default=True)
    apply_transform: BoolProperty(name='Apply Object Transforms', default=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and bool(obj.get(SOURCE_PATH))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        obj = context.active_object
        layout = self.layout
        layout.label(text='Replace the source file for the ACTIVE mesh only.', icon='ERROR')
        layout.label(text=Path(obj[SOURCE_PATH]).name)
        layout.label(text='A unique .bak copy will be made first.')
        layout.label(text='File writes cannot be undone with Ctrl+Z.')
        layout.prop(self, 'color_mode')
        layout.prop(self, 'apply_modifiers')
        layout.prop(self, 'apply_transform')

    def execute(self, context):
        obj = context.active_object
        try:
            path = Path(bpy.path.abspath(obj[SOURCE_PATH]))
            old_hash = obj.get(SOURCE_HASH)
            if path.is_file() and old_hash:
                with path.open('rb') as stream:
                    current_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
                if current_hash != old_hash:
                    raise codec.FormatError(
                        'The source file changed on disk since import/last save. '
                        'Use File > Export to a different name, or re-import the current source.')
            count, warnings, backup_path, digest = export_sprite(
                context, [obj], path, backup=True, axes='AUTO',
                color_mode=self.color_mode, apply_modifiers=self.apply_modifiers,
                apply_transform=self.apply_transform)
            obj[SOURCE_HASH] = digest
            _report_export(self, count, warnings, backup_path)
            return {'FINISHED'}
        except Exception as exc:
            return _error(self, exc)


def run_self_test(context):
    """Actual Blender mesh tests, invoked only by the user's explicit action.

    Uses bundled knight bytes. Does not write files or change existing objects.
    Temporary objects, materials and meshes are removed in finally blocks.
    """
    fixture = Path(__file__).parent / 'tests' / 'fixtures' / 'knight.sprite3d'
    original = fixture.read_bytes()
    before_active = context.view_layer.objects.active
    before_selected = list(context.selected_objects)
    objects = []
    checks = []
    try:
        obj = load_sprite(context, fixture)
        objects.append(obj)
        for evaluated in (False, True):
            triangles, _ = collect_triangles(context, [obj], apply_modifiers=evaluated,
                                             color_mode='PRESERVE')
            if codec.encode(triangles) != original:
                raise AssertionError(f'Blender mesh byte round-trip failed (modifiers={evaluated}).')
        checks.append('Knight: 224 triangles; exact byte round-trip, raw and evaluated mesh')
        triangles, _ = collect_triangles(context, [obj], color_mode='AUTO')
        if codec.encode(triangles) != original:
            raise AssertionError('Unedited material colors did not round-trip exactly.')
        checks.append('Unedited material-color export preserves exact bytes')
        material = obj.data.materials[0]
        material.name = 'Renamed by Sprite3D self-test'
        shader = _principled(material)
        shader.inputs['Base Color'].default_value = (0.0, 1.0, 0.0, 1.0)
        triangles, _ = collect_triangles(context, [obj], color_mode='AUTO')
        if triangles[0].color != 0x07E0 or any(t.extra != 1 for t in triangles):
            raise AssertionError('Recolor or wireframe/padding preservation failed.')
        checks.append('Renamed material recolors to green; wireframe/padding preserved')
        obj.location.x = 0.25
        context.view_layer.update()
        triangles, _ = collect_triangles(context, [obj], color_mode='PRESERVE')
        if abs(triangles[0].coordinates[0] - 0.25) > 1e-6:
            raise AssertionError('Object transform export failed.')
        checks.append('Object translation is exported')
        mesh = bpy.data.meshes.new('Sprite3D self-test quad')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        quad = bpy.data.objects.new('Sprite3D self-test quad', mesh)
        context.collection.objects.link(quad)
        objects.append(quad)
        triangles, _ = collect_triangles(context, [quad], apply_modifiers=False)
        if len(triangles) != 2 or any(t.extra != 1 or t.color != 0xFFFF for t in triangles):
            raise AssertionError('New-geometry triangulation/fallback failed.')
        checks.append('New quad automatically triangulated with documented fallback data')
        return checks
    finally:
        for obj in reversed(objects):
            _dispose_object(obj)
        for obj in before_selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        context.view_layer.objects.active = before_active


class SPRITE3D_OT_self_test(bpy.types.Operator):
    bl_idname = 'sprite3d.self_test'
    bl_label = 'Run Self-Test'
    bl_description = 'Test import, export, recoloring, transforms and triangulation in this Blender version using a temporary knight mesh'

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        try:
            checks = run_self_test(context)
            print('Sprite3D self-test:', bpy.app.version_string)
            for check in checks:
                print('PASS:', check)
            self.report({'INFO'}, f'PASS: {len(checks)} checks in Blender {bpy.app.version_string}; exact knight byte round-trip')
            return {'FINISHED'}
        except Exception as exc:
            return _error(self, exc)


class SPRITE3D_PT_tools(bpy.types.Panel):
    bl_label = 'Sprite3D Mesh'
    bl_idname = 'SPRITE3D_PT_tools'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Sprite3D'

    def draw(self, context):
        layout = self.layout
        layout.operator('import_scene.sprite3d_mesh', text='Import .sprite3d', icon='IMPORT')
        op = layout.operator('export_scene.sprite3d_mesh', text='Export Selected...', icon='EXPORT')
        op.selected_only = True
        obj = context.active_object
        if obj is not None and obj.type == 'MESH':
            box = layout.box()
            box.label(text=obj.name, icon='MESH_DATA')
            box.label(text=f'Base mesh: {len(obj.data.polygons):,} faces')
            source = obj.get(SOURCE_PATH)
            if source:
                box.label(text='Source: ' + Path(source).name)
                box.operator('sprite3d.save_source', icon='FILE_TICK')
            material = obj.active_material
            if material is not None:
                shader = _principled(material)
                if shader is not None:
                    socket = shader.inputs.get('Base Color')
                    if socket is not None and not socket.is_linked:
                        box.label(text='Active Material: ' + material.name)
                        box.prop(socket, 'default_value', text='Face Color')
            box.label(text='Picoware RGB565 triangle colors', icon='INFO')
        layout.separator()
        layout.operator('sprite3d.self_test', icon='CHECKMARK')


def _menu_import(self, context):
    self.layout.operator(SPRITE3D_OT_import.bl_idname, text='Sprite3D Mesh (.sprite3d)')


def _menu_export(self, context):
    self.layout.operator(SPRITE3D_OT_export.bl_idname, text='Sprite3D Mesh (.sprite3d)')


CLASSES = (SPRITE3D_OT_import, SPRITE3D_OT_export, SPRITE3D_OT_save_source,
           SPRITE3D_OT_self_test, SPRITE3D_PT_tools)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

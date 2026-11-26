"""Run with Blender --background --factory-startup --python this_file.py."""
import importlib.util,sys
from pathlib import Path
import bpy,bmesh
from mathutils import Vector
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('toolbox_test',root/'picoware-blender-1.6.0/__init__.py')
addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon;spec.loader.exec_module(addon);addon.register()
from toolbox_test.colliders import split_objects,encode_boxes

def select(*objects,active=None):
    for o in bpy.context.selected_objects:o.select_set(False)
    for o in objects:o.select_set(True)
    bpy.context.view_layer.objects.active=active or objects[-1]

def rejected(op,fragment):
    try:result=op()
    except RuntimeError as exc:assert fragment in str(exc),str(exc)
    else:assert result=={'CANCELLED'}

bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(location=(3,1,0));asset=bpy.context.object;asset.name='Asset';asset.scale=(2,3,1)
bpy.ops.mesh.primitive_cube_add(location=(4,2,1));box=bpy.context.object
assert bpy.ops.sprite3d.convert_collider()=={'FINISHED'}
bpy.context.view_layer.update();before=box.matrix_world.copy()
select(box,asset)
assert bpy.ops.sprite3d.assign_collider()=={'FINISHED'}
bpy.context.view_layer.update()
assert box.parent==asset
assert max(abs(a-b) for r,s in zip(before,box.matrix_world) for a,b in zip(r,s))<1e-5
select(asset)
assert bpy.ops.sprite3d.validate_colliders()=={'FINISHED'}
data=encode_boxes(bpy.context,*split_objects([asset]),axes='Y_UP')
assert bpy.ops.sprite3d.show_colliders(hide=True)=={'FINISHED'} and box.hide_get()
assert encode_boxes(bpy.context,*split_objects([asset]),axes='Y_UP')==data
assert bpy.ops.sprite3d.show_colliders(hide=False)=={'FINISHED'} and not box.hide_get()
assert bpy.ops.sprite3d.select_colliders()=={'FINISHED'} and bpy.context.active_object==box
box.rotation_euler.x=.25
rejected(bpy.ops.sprite3d.validate_colliders,'upright')
box.rotation_euler.x=0
assert bpy.ops.sprite3d.remove_collider_tag()=={'FINISHED'}
assert not box.name.startswith('COLLIDER_') and not box.hide_render and box.display_type=='TEXTURED'
assert len(split_objects([asset])[1])==0
# Fit one face of a transformed asset without editing its geometry or leaving Edit Mode.
select(asset)
bpy.ops.object.mode_set(mode='EDIT')
bm=bmesh.from_edit_mesh(asset.data)
for f in bm.faces:f.select_set(False)
for e in bm.edges:e.select_set(False)
for v in bm.verts:v.select_set(v.co.z>.5)
bmesh.update_edit_mesh(asset.data)
points=[asset.matrix_world@v.co for v in bm.verts if v.select]
old=[tuple(v.co) for v in bm.verts]
assert bpy.ops.sprite3d.fit_collider()=={'FINISHED'} and bpy.context.mode=='EDIT_MESH'
assert old==[tuple(v.co) for v in bm.verts]
fitted=split_objects([asset])[1][0]
bpy.context.view_layer.update()
assert abs(fitted.dimensions.z-.01)<1e-5
assert abs(fitted.matrix_world.translation.z-1)<1e-5
assert len(encode_boxes(bpy.context,*split_objects([asset]),axes='Y_UP'))==36
# No selected geometry fails without adding a helper.
for v in bm.verts:v.select_set(False)
bmesh.update_edit_mesh(asset.data)
rejected(bpy.ops.sprite3d.fit_collider,'Select vertices')
assert len(split_objects([asset])[1])==1
bpy.ops.object.mode_set(mode='OBJECT')
# Assigning a fifth box fails before reparenting anything.
for i in range(3):
    bpy.ops.mesh.primitive_cube_add(location=(i,0,0));new=bpy.context.object
    bpy.ops.sprite3d.convert_collider();select(new,asset);bpy.ops.sprite3d.assign_collider()
bpy.ops.mesh.primitive_cube_add();fifth=bpy.context.object;bpy.ops.sprite3d.convert_collider()
select(fifth,asset)
rejected(bpy.ops.sprite3d.assign_collider,'maximum is 4')
assert fifth.parent is None
# Recovery tools still work when invalid files already contain more than four.
fifth.parent=asset
select(asset)
rejected(bpy.ops.sprite3d.validate_colliders,'maximum is 4')
assert bpy.ops.sprite3d.select_colliders()=={'FINISHED'}
assert len(bpy.context.selected_objects)==5
bpy.ops.sprite3d.remove_collider_tag()
assert len(split_objects([asset])[1])==0
print('COLLISION_TOOLBOX_PASS: assignment, visibility/export parity, validation, untagging, edit-face fitting, limit and recovery')

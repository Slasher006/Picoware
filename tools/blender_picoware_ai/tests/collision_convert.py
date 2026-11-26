"""Run in Blender background: conversion uses evaluated bounds and respects limits."""
import importlib.util,sys,math
from pathlib import Path
import bpy
from mathutils import Vector
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('convert_test',root/'picoware-blender-1.6.0/__init__.py')
addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon;spec.loader.exec_module(addon);addon.register()
from convert_test.colliders import encode_boxes,split_objects
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add();parent=bpy.context.object;parent.scale=(2,3,1)
bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=8,location=(2,3,1));obj=bpy.context.object
obj.rotation_euler=(.2,.1,.5);obj.scale=(.4,.7,.9)
bpy.context.view_layer.update();world=obj.matrix_world.copy();obj.parent=parent;obj.matrix_parent_inverse=parent.matrix_world.inverted();obj.matrix_world=world
mod=obj.modifiers.new('Evaluated bounds','SOLIDIFY');mod.thickness=.1
shared=bpy.data.objects.new('Shared original',obj.data);bpy.context.collection.objects.link(shared)
bpy.context.view_layer.update()
eval_obj=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
points=[eval_obj.matrix_world@Vector(c) for c in eval_obj.bound_box]
expected=[tuple(min(v[k] for v in points) for k in range(3)),tuple(max(v[k] for v in points) for k in range(3))]
original_vertices=len(shared.data.vertices)
assert bpy.ops.sprite3d.convert_collider()=={'FINISHED'}
assert len(obj.data.vertices)==8 and len(obj.data.polygons)==6 and not obj.modifiers
assert len(shared.data.vertices)==original_vertices>8 and obj.data!=shared.data
assert obj.name.startswith('COLLIDER_') and obj.hide_render and obj.show_in_front
bpy.context.view_layer.update()
actual=[obj.matrix_world@v.co for v in obj.data.vertices]
for k in range(3):
    assert abs(min(v[k] for v in actual)-expected[0][k])<1e-5
    assert abs(max(v[k] for v in actual)-expected[1][k])<1e-5
assert len(encode_boxes(bpy.context,*split_objects([parent]),axes='Y_UP'))==36
# Repeated conversion does not add boxes or another name prefix.
assert bpy.ops.sprite3d.convert_collider()=={'FINISHED'}
assert obj.name.count('COLLIDER_')==1
for i in range(3):
    bpy.ops.mesh.primitive_cube_add();helper=bpy.context.object;helper.parent=parent
    assert bpy.ops.sprite3d.convert_collider()=={'FINISHED'}
assert len(encode_boxes(bpy.context,*split_objects([parent]),axes='Y_UP'))==120
bpy.ops.mesh.primitive_cube_add();fifth=bpy.context.object;fifth.parent=parent;old=fifth.data
try:
    result=bpy.ops.sprite3d.convert_collider()
except RuntimeError as exc:
    assert 'Maximum four' in str(exc)
else:assert result=={'CANCELLED'}
assert fifth.data==old and not fifth.name.startswith('COLLIDER_')
print('COLLISION_CONVERT_PASS: evaluated bounds, transformed parent, shared mesh, idempotence, four-box limit')

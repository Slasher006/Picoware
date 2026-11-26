"""Blender background integration test for authored collision boxes."""
import importlib.util
import sys
import struct
import math
from pathlib import Path
import bpy
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('picoware_collision_test',root/'picoware-blender-1.6.0/__init__.py')
addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon;spec.loader.exec_module(addon);addon.register()
from picoware_collision_test.colliders import split_objects,encode_boxes
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2,location=(2,3,1))
asset=bpy.context.object;asset.name='Asset';asset.scale=(1,.25,.5)
bpy.context.view_layer.update()
assert bpy.ops.sprite3d.add_collider()=={'FINISHED'}
visible,helpers=split_objects([asset]);assert len(helpers)==1 and visible==[asset]
helper=helpers[0];assert helper.hide_render and helper.display_type=='WIRE'
helper.rotation_euler.z=math.pi/4
bpy.context.view_layer.update()
data=encode_boxes(bpy.context,visible,helpers,axes='Y_UP')
assert len(data)==36
v=struct.unpack('<7f',data[8:]);assert all(abs(a-b)<1e-5 for a,b in zip(v[:3],(2,1,-3)))
out=Path('/tmp/picoware-collision-tests');out.mkdir(exist_ok=True)
path=out/'asset.sprite3d'
addon.export_sprite(bpy.context,[asset],path,axes='Y_UP')
assert len(addon.read(path))==12,'Collider leaked into geometry'
assert Path(str(path)+'.colliders').read_bytes()==data
# Textured export must exclude helpers, too.
addon.export_sprite(bpy.context,[asset,helper],out/'textured.sprite3d',axes='Y_UP',export_textures=True,texture_size=64)
assert len(addon.read(out/'textured.sprite3d'))==12
# Bake respects helper exclusion even if the helper is enormous.
helper.scale *= 100
bpy.context.view_layer.update()
bill=out/'billboard.bin'
for p in (bill,Path(str(bill)+'.colliders')):p.unlink(missing_ok=True)
bp,info=addon.bake(bpy.context,str(bill),resolution=8,directions=1)
assert info['world_height']<10,'Collider leaked into billboard bounds'
assert Path(str(bp)+'.colliders').exists()
helper.scale /= 100
bpy.context.view_layer.update()
# Limit validation happens before modifying existing exports.
original=path.read_bytes()
for i in range(4):
    dup=helper.copy();dup.data=helper.data.copy();bpy.context.collection.objects.link(dup);dup.parent=asset;dup.matrix_parent_inverse=asset.matrix_world.inverted();dup.matrix_world=helper.matrix_world.copy()
    if i==2:
        four=encode_boxes(bpy.context,*split_objects([asset]),axes='Y_UP')
        assert len(four)==120 and struct.unpack('<I',four[4:8])[0]==4
try:addon.export_sprite(bpy.context,[asset],path,axes='Y_UP')
except ValueError as e:assert 'maximum is 4' in str(e)
else:raise AssertionError('Five colliders accepted')
assert path.read_bytes()==original
for child in list(asset.children):
    if child!=helper:bpy.data.objects.remove(child,do_unlink=True)
helper.rotation_euler.x=.2
try:encode_boxes(bpy.context,*split_objects([asset]),axes='Y_UP')
except ValueError as e:assert 'upright' in str(e)
else:raise AssertionError('Tilt accepted')
helper.rotation_euler.x=0
# Removing all boxes replaces stale collision data with an explicit empty set.
bpy.data.objects.remove(helper,do_unlink=True)
addon.export_sprite(bpy.context,[asset],out/'empty.sprite3d',axes='Y_UP')
assert Path(str(out/'empty.sprite3d')+'.colliders').read_bytes()==b'PCB1\0\0\0\0'
print('COLLISION_EXPORT_PASS',out)

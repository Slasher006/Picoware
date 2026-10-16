# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with Blender --background --factory-startup --python-exit-code 1."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import bpy

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('animation_test_addon',root/'__init__.py',submodule_search_locations=[str(root)])
a = importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
from animation_test_addon import billboard as b
assert b.sample_frames(1,25,4,True)==[1,7,13,19]
assert b.sample_frames(1,25,4,False)==[1,9,17,25]
assert b.sample_frames(1,2,8,True)==[1+i/8 for i in range(8)]
assert b.sample_frames(5,5,1)==[5]
for args in [(1,1,4),(10,1,4),(1,25,9),(1,25,0)]:
    try:b.sample_frames(*args)
    except ValueError:pass
    else:raise AssertionError(args)
assert b.payload_bytes(64,8,4)==147456
loader={};exec(b.LOADER,loader)
class Entity:billboard_pose=0
entity=Entity();player=loader['BillboardAnimation'](entity,4,8,True)
for dt,expected in [(0,0),(.125,1),(.25,3),(.125,0),(.875,3)]:
    player.update(dt);assert entity.billboard_pose==expected
player.reset();assert entity.billboard_pose==0
player=loader['BillboardAnimation'](entity,4,8,False)
player.update(10);assert entity.billboard_pose==3 and player.finished
player.update(1);assert entity.billboard_pose==3
for dt in (-1,float('nan'),float('inf')):
    try:player.update(dt)
    except ValueError:pass
    else:raise AssertionError(dt)

bpy.ops.object.select_all(action='DESELECT')
bpy.ops.mesh.primitive_cube_add()
obj=bpy.context.object
obj.scale=(1,1,1);obj.keyframe_insert('scale',frame=1)
obj.scale=(1,2,1);obj.keyframe_insert('scale',frame=5)
scene=bpy.context.scene;scene.frame_set(3,subframe=.25)
a.register()
try:
    before=(len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.scenes),len(bpy.data.worlds))
    with tempfile.TemporaryDirectory() as directory:
        output,info=b.bake(bpy.context,str(Path(directory)/'animated.billboard'),32,1,'NEG_X',True,1,5,2,8,False)
        assert info['poses']==2 and info['sample_frames']==[1,5]
        assert info['payload_bytes']==2304
        assert scene.frame_current==3 and abs(scene.frame_subframe-.25)<1e-6
        assert before==(len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.scenes),len(bpy.data.worlds))
        assert json.loads((output/'billboard.json').read_text())['poses']==2
        masks=[(output/v['mask']).read_bytes() for v in info['views']]
        assert masks[0]!=masks[1], 'Animated geometry was not evaluated per pose'
        # A failed render must clean temporary geometry/output and restore timeline.
        with patch.object(b,'pack_rgba',side_effect=RuntimeError('injected pixel conversion failure')):
            try:b.bake(bpy.context,str(Path(directory)/'failure.billboard'),32,1,'NEG_X',True,1,5,2,8,False)
            except RuntimeError as exc:assert 'injected' in str(exc)
            else:raise AssertionError('Expected failure')
        assert not (Path(directory)/'failure').exists()
        assert not list(Path(directory).glob('.billboard-*'))
        assert before==(len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.scenes),len(bpy.data.worlds))
        assert scene.frame_current==3 and abs(scene.frame_subframe-.25)<1e-6
    print('ANIMATION_TEST_PASS: sampling, timing, bounds, evaluated poses, success/failure cleanup, frame restoration')
finally:a.unregister()

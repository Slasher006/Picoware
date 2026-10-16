# Run with blender --background --factory-startup --python-exit-code 1 --python <this file>
import importlib.util, sys, tempfile
from pathlib import Path
from unittest.mock import patch
import bpy
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pixel_test_addon',root/'__init__.py',submodule_search_locations=[str(root)])
a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
from pixel_test_addon import billboard as b, pixelart, pixel_preview
from pixel_test_addon.codec import srgb_to_linear
bpy.ops.object.select_all(action='DESELECT');bpy.ops.mesh.primitive_cube_add()
obj=bpy.context.object;obj.scale=(1,.5,2)
mat=bpy.data.materials.new('Original');mat.use_nodes=True
mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(.05,.8,.15,1)
obj.data.materials.append(mat)
scene=bpy.context.scene;scene.frame_set(7,subframe=.25)
a.register()
def counts():return tuple(len(c) for c in (bpy.data.objects,bpy.data.meshes,bpy.data.materials,bpy.data.scenes,bpy.data.worlds,bpy.data.images))
before=counts();original_material=mat.node_tree.nodes.get('Material Output').inputs['Surface'].links[0].from_node.type
try:
 with tempfile.TemporaryDirectory() as folder:
  # Byte PNG readback is sRGB; a second gamma conversion turns brown orange.
  mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=tuple(srgb_to_linear(v/255) for v in pixelart.rgb(104))+(1,)
  color_path,_=b.bake(bpy.context,str(Path(folder)/'brown.bin'),resolution=16,directions=1,pixel_art=True)
  color_data=color_path.read_bytes();color_mask=color_data[280:312]
  opaque=[c for i,c in enumerate(color_data[24:280]) if color_mask[i//8]&(128>>(i%8))]
  assert opaque and set(opaque)=={104},set(opaque)
  for size in (8,16):
   for outline in (False,True):
    opts=dict(resolution=size,directions=4,pixel_art=True,outline=outline,elevation=15,azimuth=20)
    p,_=b.bake(bpy.context,str(Path(folder)/('%d-%s.bin'%(size,outline))),**opts)
    q,_=b.bake(bpy.context,str(Path(folder)/('%d-%s-repeat.bin'%(size,outline))),**opts)
    assert p.read_bytes()==q.read_bytes(),'Non-deterministic export'
    data=p.read_bytes();stride=size*size+(size*size+7)//8
    for d in range(4):
     pixels=data[24+d*stride:24+d*stride+size*size];mask=data[24+d*stride+size*size:24+(d+1)*stride]
     assert set(pixels)<=set(pixelart.PALETTE)
     assert any(mask)
    images,_,_=pixel_preview.preview_images(p)
    assert len(images)==1
    for image in images:bpy.data.images.remove(image)
    assert counts()==before,(counts(),before)
  with patch.object(b,'pack_rgba',side_effect=RuntimeError('test failure')):
   try:b.bake(bpy.context,str(Path(folder)/'fail.bin'),pixel_art=True)
   except RuntimeError:pass
   else:raise AssertionError('Expected injected failure')
  assert counts()==before
  assert scene.frame_current==7 and abs(scene.frame_subframe-.25)<1e-6
  assert mat.node_tree.nodes.get('Material Output').inputs['Surface'].links[0].from_node.type==original_material
 print('PIXEL_ART_PASS: deterministic exports, 8/16, outlines, camera angles, palette, preview decode, material and failure cleanup')
finally:a.unregister()

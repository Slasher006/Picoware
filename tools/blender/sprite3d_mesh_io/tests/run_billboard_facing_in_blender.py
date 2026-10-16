# SPDX-License-Identifier: GPL-3.0-or-later
import bpy,sys,importlib.util,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('facing_test',root/'__init__.py',submodule_search_locations=[str(root)])
a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
from facing_test.billboard import bake
from facing_test.codec import srgb_to_linear
from facing_test.pixelart import rgb
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
markers=[]
for label,x,code in (('Forward red',1,224),('Rear blue',-1,11)):
 bpy.ops.mesh.primitive_cube_add(size=.7,location=(x,0,0))
 ob=bpy.context.object;ob.name=label
 mat=bpy.data.materials.new(label);mat.use_nodes=True
 mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=tuple(srgb_to_linear(c/255) for c in rgb(code))+(1,)
 ob.data.materials.append(mat);markers.append(ob)
for ob in markers:ob.select_set(True)
with tempfile.TemporaryDirectory() as folder:
 path,_=bake(bpy.context,str(Path(folder)/'facing.bin'),resolution=16,directions=4,forward='POS_X',pixel_art=True)
 data=path.read_bytes();stride=288
 for direction in (1,3):
  pixels=data[24+direction*stride:24+direction*stride+256]
  centroids={code:sum(i%16 for i,c in enumerate(pixels) if c==code)/sum(c==code for c in pixels) for code in (224,11)}
  # Mesh mapping: Blender (x,y,z) -> engine (x,z,-y).
  # At engine bearing +Y (view 1), engine screen-right is +X.
  # Therefore the red +X marker must be on the RIGHT; at -Y it must be LEFT.
  correct=(centroids[224]>centroids[11]) if direction==1 else (centroids[224]<centroids[11])
  print('FACING_REFERENCE',direction,centroids,'MATCH',correct)
  assert correct,'Exported side view is reversed relative to engine geometry'
print('BILLBOARD_FACING_PASS')

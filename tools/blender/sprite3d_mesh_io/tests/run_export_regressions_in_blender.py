# SPDX-License-Identifier: GPL-3.0-or-later
import bpy,sys,importlib.util,tempfile,json
from pathlib import Path
r=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('audit_addon',r/'__init__.py',submodule_search_locations=[str(r)])
a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
from audit_addon.billboard import bake
from audit_addon.codec import srgb_to_linear
from audit_addon.pixelart import rgb
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,.5));ob=bpy.context.object;ob.scale=(4,.5,1)
mat=bpy.data.materials.new('Audit material');mat.use_nodes=True;ob.data.materials.append(mat)
first=mat.node_tree.nodes.get('Principled BSDF')
def color(node,code):node.inputs['Base Color'].default_value=tuple(srgb_to_linear(v/255) for v in rgb(code))+(1,)
color(first,224)
with tempfile.TemporaryDirectory() as folder:
 path,meta=bake(bpy.context,str(Path(folder)/'ground.bin'),resolution=16,directions=1,forward='NEG_Y',pixel_art=True)
 data=path.read_bytes();mask=data[280:312];ys=[i//16 for i in range(256) if mask[i//8]&(128>>(i%8))]
 assert max(ys)>=14, 'Wide model floats above the ground anchor'
 print('AUDIT_GROUND',json.dumps({'top_row':min(ys),'bottom_row':max(ys),'empty_bottom_rows':15-max(ys),'world_height':meta['world_height'],'source_bottom':0}))
 # Keep the original Principled disconnected; connect a new blue Principled.
 second=mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled');color(second,11)
 out=mat.node_tree.nodes.get('Material Output');mat.node_tree.links.new(second.outputs['BSDF'],out.inputs['Surface'])
 path,_=bake(bpy.context,str(Path(folder)/'shader.bin'),resolution=16,directions=1,forward='NEG_Y',pixel_art=True)
 data=path.read_bytes();mask=data[280:312];colors=sorted(set(c for i,c in enumerate(data[24:280]) if mask[i//8]&(128>>(i%8))))
 assert colors==[11], 'Unused shader affected exported color'
 print('AUDIT_CONNECTED_SHADER',json.dumps({'expected':[11],'actual':colors}))
 # Reroutes must resolve to the connected shader too.
 reroute=mat.node_tree.nodes.new('NodeReroute')
 mat.node_tree.links.new(second.outputs['BSDF'],reroute.inputs[0])
 mat.node_tree.links.new(reroute.outputs[0],out.inputs['Surface'])
 path,_=bake(bpy.context,str(Path(folder)/'reroute.bin'),resolution=16,directions=1,forward='NEG_Y',pixel_art=True)
 data=path.read_bytes();mask=data[280:312]
 assert set(c for i,c in enumerate(data[24:280]) if mask[i//8]&(128>>(i%8)))=={11}
 # A material with nodes disabled must use its viewport color.
 mat.use_nodes=False;mat.diffuse_color=tuple(srgb_to_linear(v/255) for v in rgb(104))+(1,)
 path,_=bake(bpy.context,str(Path(folder)/'viewport.bin'),resolution=16,directions=1,forward='NEG_Y',pixel_art=True)
 data=path.read_bytes();mask=data[280:312]
 # Blender 5.3 keeps use_nodes=True even after assigning False.
 expected=11 if mat.use_nodes else 104
 assert set(c for i,c in enumerate(data[24:280]) if mask[i//8]&(128>>(i%8)))=={expected}
 mat.use_nodes=True
 mat.use_fake_user=True
 before=len(bpy.data.materials)
 path,_=bake(bpy.context,str(Path(folder)/'fake-user.bin'),resolution=16,directions=1,forward='NEG_Y',pixel_art=True,animated=True,frame_start=1,frame_end=9,poses=8)
 print('AUDIT_MATERIAL_CLEANUP',json.dumps({'before':before,'after':len(bpy.data.materials),'leftovers':[(m.name,m.users,m.use_fake_user) for m in bpy.data.materials if m.name.startswith('Audit material.')]}))

assert len(bpy.data.materials)==before
print("EXPORT_AUDIT_REGRESSION_PASS")

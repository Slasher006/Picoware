"""Run with Blender --background --factory-startup --python this_file.py."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import bpy

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('picoware_texture_test', root / 'picoware-blender-1.6.0/__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
from picoware_texture_test.textures import read_bundle, bundle_paths

out = Path(tempfile.mkdtemp(prefix='picoware-blender-texture-test-'))
mesh = bpy.data.meshes.new('texture fixture')
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
uv = mesh.uv_layers.new(name='Source UV')
for loop in mesh.loops:
    uv.data[loop.index].uv = mesh.vertices[loop.vertex_index].co[:2]
image = bpy.data.images.new('four corners', width=2, height=2, float_buffer=True)
# Blender image rows are bottom-up: red/green below blue/white.
image.pixels[:] = [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1]
material = bpy.data.materials.new('four colors')
material.use_nodes = True
shader = material.node_tree.nodes.get('Principled BSDF')
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
texture.interpolation = 'Closest'
material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
mesh.materials.append(material)
obj = bpy.data.objects.new('fixture', mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)

def snapshot():
    return (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials),
            len(bpy.data.images), len(bpy.data.scenes),
            tuple(tuple(v.uv) for v in uv.data), mesh.uv_layers.active.name,
            tuple((n.name, n.select) for n in material.node_tree.nodes),
            tuple(o.name for o in bpy.context.selected_objects), bpy.context.view_layer.objects.active)

before = snapshot()
path = out / 'fixture.sprite3d'
addon.export_sprite(bpy.context, [obj], path, export_textures=True, texture_size=64,
                    axes='BLENDER', apply_transform=False, default_extra=0)
assert snapshot() == before, 'Export changed scene or leaked temporary data'
triangles = addon.read(path)
w, h, uvs, pixels = read_bundle(path, len(triangles))
assert len(triangles) == 2 and all(t.color == 0xffff for t in triangles)
assert len(pixels) == 64 * 64 * 2
assert set((0xf800, 0x07e0, 0x001f, 0xffff)).issubset(set(struct.unpack('<4096H', pixels)))
# Check UV association and vertical orientation against original source geometry.
def verify_samples(triangles, uvs, pixels, w, h):
    for triangle, atlas_uv in zip(triangles, uvs):
        for weights in ((.7, .2, .1), (.1, .7, .2), (.2, .1, .7)):
            x = sum(weights[i] * triangle.coordinates[i * 3] for i in range(3))
            y = sum(weights[i] * triangle.coordinates[i * 3 + 1] for i in range(3))
            u = sum(weights[i] * atlas_uv[i * 2] for i in range(3))
            v = sum(weights[i] * atlas_uv[i * 2 + 1] for i in range(3))
            actual = struct.unpack_from('<H', pixels, (min(h-1,int(v*h))*w + min(w-1,int(u*w))) * 2)[0]
            expected = ((0xf800, 0x07e0), (0x001f, 0xffff))[y >= .5][x >= .5]
            assert actual == expected, (x, y, u, v, hex(actual), hex(expected))
verify_samples(triangles, uvs, pixels, w, h)
loaded = addon.load_sprite(bpy.context, path, axes='BLENDER')
assert loaded.get('sprite3d_textured') and loaded.data.uv_layers.active
second = out / 'roundtrip.sprite3d'
addon.export_sprite(bpy.context, [loaded], second, export_textures=True, texture_size=64,
                    axes='BLENDER', apply_transform=False, default_extra=0)
w, h, uvs, pixels = read_bundle(second, 2)
verify_samples(addon.read(second), uvs, pixels, w, h)
# A mirrored second object/material must occupy a different part of the atlas.
yellow = bpy.data.materials.new('yellow')
yellow.use_nodes = True
yellow.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (1, 1, 0, 1)
other = bpy.data.objects.new('mirrored fixture', mesh.copy())
bpy.context.collection.objects.link(other)
other.data.materials.clear()
other.data.materials.append(yellow)
other.scale.x = -1
other.location.x = 3
multi = out / 'multi.sprite3d'
before = snapshot()
addon.export_sprite(bpy.context, [obj, other], multi, export_textures=True, texture_size=128,
                    axes='BLENDER', apply_transform=True, default_extra=0)
assert snapshot() == before
triangles = addon.read(multi)
w, h, uvs, pixels = read_bundle(multi, 4)
verify_samples(triangles[:2], uvs[:2], pixels, w, h)
for triangle, uv_values in zip(triangles[2:], uvs[2:]):
    x = int(sum(uv_values[::2]) / 3 * w)
    y = int(sum(uv_values[1::2]) / 3 * h)
    assert struct.unpack_from('<H', pixels, (y*w+x)*2)[0] == 0xffe0
    a, b, c = [triangle.coordinates[i:i+3] for i in (0, 3, 6)]
    assert (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]) > 0
# Partial write failure restores all members to their prior bytes.
from picoware_texture_test import textures
originals = {p: p.read_bytes() for p in bundle_paths(multi)}
original_write = addon.write_atomic
calls = 0
def fail_third(*args, **kwargs):
    global calls
    calls += 1
    if calls == 3:
        raise OSError('injected write failure')
    return original_write(*args, **kwargs)
addon.write_atomic = fail_third
try:
    try:
        textures.write_bundle(multi, originals[multi], b'new uv', b'new pixels', 128, False)
    except OSError:
        pass
    else:
        raise AssertionError('Expected injected failure')
finally:
    addon.write_atomic = original_write
assert all(p.read_bytes() == blob for p, blob in originals.items())
# Missing UVs fail without changing the scene or writing output files.
other.data.uv_layers.remove(other.data.uv_layers.active)
other.data.update()
assert len(other.data.uv_layers) == 0
before = snapshot()
try:
    addon.export_sprite(bpy.context, [other], out / 'invalid.sprite3d', export_textures=True)
except addon.FormatError:
    pass
else:
    raise AssertionError('Mesh without UVs accepted')
assert snapshot() == before
assert not (out / 'invalid.sprite3d').exists()
# Refuse stale mixed bundles and corrupted metadata without replacing mesh data.
old = path.read_bytes()
try:
    addon.export_sprite(bpy.context, [obj], path, color_mode='PRESERVE')
except addon.FormatError:
    pass
else:
    raise AssertionError('Flat export accepted a textured destination')
assert path.read_bytes() == old
uv_path = bundle_paths(path)[1]
uv_path.write_bytes(b'bad')
try:
    addon.load_sprite(bpy.context, path)
except addon.FormatError:
    pass
else:
    raise AssertionError('Corrupt bundle accepted')
# Flat-color export/import remains usable, and the codec accepts the new limit.
flat = out / 'flat.sprite3d'
addon.export_sprite(bpy.context, [other], flat, axes='BLENDER', default_extra=0)
assert all(t.color == 0xffe0 for t in addon.read(flat))
flat_obj = addon.load_sprite(bpy.context, flat, axes='BLENDER')
assert not flat_obj.get('sprite3d_textured')
record = addon.read(flat)[0]
assert len(addon.decode(addon.encode([record] * 65535))) == 65535
try:
    addon.encode([record] * 65536)
except addon.FormatError:
    pass
else:
    raise AssertionError('Triangle limit not enforced')
print('TEXTURE_EXPORT_PASS', out)

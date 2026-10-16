# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional integration suite; the sidebar's Run Self-Test needs no terminal.

blender --background --factory-startup --python-exit-code 1 --python /path/to/tests/run_in_blender.py

This script is intended ONLY for a fresh headless Blender process. For testing
inside your open project, use the add-on's non-destructive Run Self-Test button.
"""
from pathlib import Path
import importlib.util
import json
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
NAME = 'sprite3d_integration_test'
spec = importlib.util.spec_from_file_location(NAME, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[NAME] = addon
spec.loader.exec_module(addon)
fixture = ROOT / 'tests' / 'fixtures' / 'knight.sprite3d'

if bpy.app.version < (5, 0, 0):
    raise RuntimeError('This integration suite targets Blender 5.0 or newer.')

addon.register()
try:
    checks = addon.run_self_test(bpy.context)
    from sprite3d_integration_test.billboard import pack_rgba
    pixels, mask = pack_rgba([1,0,0,1, 0,1,0,0, 0,0,1,1, 1,1,1,.49], 2, 2)
    assert pixels == bytes([3,0,224,0]) and mask == bytes([0xA0])
    pixels, mask = pack_rgba([1,1,1,.5], 1, 1)
    assert pixels == bytes([255]) and mask == bytes([0x80])
    checks.append('Billboard RGB332 colors, alpha threshold, top-down rows and MSB mask packing')
    with tempfile.TemporaryDirectory(prefix='sprite3d_blender_test_') as directory:
        output = Path(directory) / 'roundtrip.bin'
        status = bpy.ops.import_scene.sprite3d_mesh(filepath=str(fixture), material_preview=False)
        assert status == {'FINISHED'}, status
        obj = bpy.context.active_object
        assert len(obj.data.polygons) == 224
        status = bpy.ops.export_scene.sprite3d_mesh(filepath=str(output), color_mode='PRESERVE')
        assert status == {'FINISHED'}, status
        assert output.read_bytes() == fixture.read_bytes()
        legacy = Path(directory) / 'legacy.sprite3d'
        assert bpy.ops.export_scene.sprite3d_mesh(filepath=str(legacy),color_mode='PRESERVE')=={'FINISHED'}
        assert legacy.read_bytes()==output.read_bytes()
        assert bpy.ops.import_scene.sprite3d_mesh(filepath=str(output),material_preview=False)=={'FINISHED'}
        addon._dispose_object(bpy.context.active_object)
        obj.select_set(True);bpy.context.view_layer.objects.active=obj
        from types import SimpleNamespace
        for cls, legacy_suffix in ((addon.SPRITE3D_OT_export,'.sprite3d'),(addon.SPRITE3D_OT_export_billboard,'.billboard')):
            assert cls.filename_ext=='.bin'
            for name,expected in (('asset','asset.bin'),('asset.bin','asset.bin'),('asset'+legacy_suffix,'asset'+legacy_suffix)):
                test=SimpleNamespace(filepath=name,filename_ext=cls.filename_ext,check_extension=True)
                cls.check(test,bpy.context)
                assert test.filepath==expected,(name,test.filepath)
        checks.append('Default .bin and legacy extensions preserve byte-exact mesh files and dialog names')

        # Export an already-existing target and verify that its previous bytes
        # remain in the timestamped backup created by the actual operator.
        output.write_bytes(b'previous output bytes')
        status = bpy.ops.export_scene.sprite3d_mesh(filepath=str(output), color_mode='PRESERVE')
        assert status == {'FINISHED'}, status
        backups = list(Path(directory).glob('*.bak'))
        assert len(backups) == 1 and backups[0].read_bytes() == b'previous output bytes'
        checks.append('Registered export operator makes an intact overwrite backup')

        original_faces = len(obj.data.polygons)
        solidify = obj.modifiers.new('Sprite3D test solidify', 'SOLIDIFY')
        solidify.thickness = 0.015
        triangles, _ = addon.collect_triangles(bpy.context, [obj], apply_modifiers=True)
        assert len(triangles) > original_faces
        assert len(obj.data.polygons) == original_faces
        obj.modifiers.remove(solidify)
        checks.append('Evaluated topology-changing modifier exports without changing the source mesh')
        addon._dispose_object(obj)
    print(json.dumps({'blender': bpy.app.version_string, 'result': 'PASS', 'checks': checks}, indent=2))
finally:
    addon.unregister()

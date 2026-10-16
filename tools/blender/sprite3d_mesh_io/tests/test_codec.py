# SPDX-License-Identifier: GPL-3.0-or-later
"""Run outside Blender: python -m unittest discover -s tests -v"""
from pathlib import Path
import hashlib
import importlib.util
import math
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sprite3d_test_codec', ROOT / 'codec.py')
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)
FIXTURE = ROOT / 'tests' / 'fixtures' / 'knight.sprite3d'


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.raw = FIXTURE.read_bytes()
        self.triangles = c.decode(self.raw)

    def test_fixture_fingerprint(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),
                         'cb1f16553a9e259d6cb08b5b5fd0358bc3c0bf97dad227b14da7fa350b5dbc91')
        self.assertEqual(len(self.raw), 8960)
        self.assertEqual(len(self.triangles), 224)
        self.assertEqual({t.extra for t in self.triangles}, {1})
        self.assertEqual(len({(t.color, t.extra) for t in self.triangles}), 36)

    def test_exact_binary_roundtrip(self):
        self.assertEqual(c.encode(self.triangles), self.raw)
        self.assertEqual(c.encode(c.read(FIXTURE)), self.raw)

    def arrays_roundtrip(self, weld=True, axes='Y_UP', scale=1.0):
        vertices, faces = c.build_mesh_arrays(self.triangles, axes, scale, weld)
        # Simulate Blender's float32 vertex storage (not the Blender mesh API).
        vertices = [c.VERTEX.unpack(c.VERTEX.pack(*v)) for v in vertices]
        back = []
        for face, original in zip(faces, self.triangles):
            xyz = tuple(value for index in face
                        for value in c.blender_to_source(vertices[index], axes, scale))
            back.append(c.Triangle(xyz, original.color, original.extra))
        self.assertEqual(c.encode(back), self.raw)
        return vertices, faces

    def test_welded_y_up_float32_roundtrip(self):
        vertices, faces = self.arrays_roundtrip()
        self.assertEqual(len(vertices), 155)
        self.assertEqual(len(faces), 224)

    def test_unwelded_float32_roundtrip(self):
        vertices, _ = self.arrays_roundtrip(weld=False)
        self.assertEqual(len(vertices), 672)

    def test_no_axis_conversion_roundtrip(self):
        self.arrays_roundtrip(axes='BLENDER')

    def test_power_of_two_import_scale_roundtrip(self):
        self.arrays_roundtrip(scale=2.0)
        self.arrays_roundtrip(scale=0.5)

    def test_axis_orientation(self):
        self.assertEqual(c.source_to_blender((1, 2, 3)), (1, -3, 2))
        self.assertEqual(c.blender_to_source((1, -3, 2)), (1, 2, 3))

    def test_signed_zeros_preserved(self):
        for xyz in ((0., -0., 0.), (-0., 0., -0.), (-0., -0., -0.)):
            converted = c.source_to_blender(xyz)
            stored = c.VERTEX.unpack(c.VERTEX.pack(*converted))
            back = c.blender_to_source(stored)
            self.assertEqual(c.VERTEX.pack(*xyz), c.VERTEX.pack(*back))

    def test_all_65536_rgb565_values_roundtrip(self):
        for value in range(65536):
            rgb = c.rgb565_to_linear(value)
            rgb = struct.unpack('<3f', struct.pack('<3f', *rgb))
            self.assertEqual(c.linear_to_rgb565(rgb), value)

    def test_recolor_known_values(self):
        self.assertEqual(c.linear_to_rgb565((1, 0, 0)), 0xF800)
        self.assertEqual(c.linear_to_rgb565((0, 1, 0)), 0x07E0)
        self.assertEqual(c.linear_to_rgb565((0, 0, 1)), 0x001F)
        self.assertEqual(c.linear_to_rgb565((1, 1, 1)), 0xFFFF)
        self.assertEqual(c.linear_to_rgb565((0, 0, 0)), 0)

    def test_wireframe_and_padding_roundtrip(self):
        t = self.triangles[0]
        for extra in (0, 1, 256, 32768, 65281):
            data = c.encode([c.Triangle(t.coordinates, 65535, extra)])
            self.assertEqual(data[-4:], struct.pack('<HH', 65535, extra))
            self.assertEqual(c.decode(data)[0].extra, extra)

    def test_invalid_wireframe_rejected_on_read_and_write(self):
        t = self.triangles[0]
        for extra in (2, 255, 65535):
            with self.assertRaisesRegex(c.FormatError, 'wireframe'):
                c.encode([c.Triangle(t.coordinates, t.color, extra)])
            with self.assertRaisesRegex(c.FormatError, 'wireframe'):
                c.decode(c.RECORD.pack(*t.coordinates, t.color, extra))

    def test_coordinate_bounds(self):
        for value in (-1e12, 1e12):
            self.assertEqual(len(c.decode(c.encode([c.Triangle((value,) * 9, 1, 0)]))), 1)
        for value in (-1e13, 1e13):
            with self.assertRaisesRegex(c.FormatError, 'coordinates'):
                c.encode([c.Triangle((value,) * 9, 1, 0)])
            with self.assertRaisesRegex(c.FormatError, 'coordinates'):
                c.decode(c.RECORD.pack(*((value,) * 9), 1, 0))

    def test_picoware_triangle_boundary(self):
        t = self.triangles[0]
        self.assertEqual(len(c.decode(c.encode([t] * 4096))), 4096)
        with self.assertRaises(c.FormatError):
            c.encode([t] * 4097)
        with self.assertRaises(c.FormatError):
            c.decode(self.raw[:40] * 4097)

    def test_degenerate_triangle_does_not_repeat_indices(self):
        t = c.Triangle((0., 0., 0.) * 3, 1, 65281)
        vertices, faces = c.build_mesh_arrays([t])
        self.assertEqual(len(set(faces[0])), 3)
        self.assertEqual(len(vertices), 3)
        xyz = tuple(v for i in faces[0] for v in c.blender_to_source(vertices[i]))
        self.assertEqual(c.encode([c.Triangle(xyz, 1, 65281)]), c.encode([t]))

    def test_empty_and_truncated_files_rejected(self):
        for data in (b'', b'x', self.raw[:-1], self.raw + b'header'):
            with self.subTest(length=len(data)), self.assertRaises(c.FormatError):
                c.decode(data)

    def test_non_finite_input_rejected(self):
        for value in (math.inf, -math.inf, math.nan):
            data = bytearray(self.raw[:40])
            struct.pack_into('<f', data, 0, value)
            with self.assertRaises(c.FormatError):
                c.decode(bytes(data))

    def test_encode_invalid_coordinates_rejected(self):
        for xyz in ((0,) * 8, (math.inf,) * 9, (1e100,) * 9):
            with self.assertRaises(c.FormatError):
                c.encode([c.Triangle(xyz, 0, 1)])
        with self.assertRaises(c.FormatError):
            c.encode([])

    def test_metadata_range_validated(self):
        for color, extra in ((-1, 1), (65536, 1), (1, -1), (1, 65536), (1.5, 1)):
            with self.assertRaises(c.FormatError):
                c.encode([c.Triangle(self.triangles[0].coordinates, color, extra)])

    def test_invalid_axes_scales_colors(self):
        with self.assertRaises(c.FormatError):
            c.source_to_blender((1, 2, 3), 'WRONG')
        with self.assertRaises(c.FormatError):
            c.blender_to_source((1, 2, 3), 'WRONG')
        for value in (0, -1, math.nan, math.inf):
            with self.assertRaises(c.FormatError):
                c.source_to_blender((1, 2, 3), scale=value)
        with self.assertRaises(c.FormatError):
            c.linear_to_rgb565((math.nan, 0, 0))

    def test_safety_limits(self):
        with patch.object(c, 'MAX_BYTES', 40):
            with self.assertRaises(c.FormatError):
                c.decode(self.raw)
            with self.assertRaises(c.FormatError):
                c.read(FIXTURE)
        with patch.object(c, 'MAX_TRIANGLES', 1):
            with self.assertRaises(c.FormatError):
                c.encode(self.triangles)

    def test_write_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'output.sprite3d'
            self.assertIsNone(c.write_atomic(target, self.raw))
            self.assertEqual(target.read_bytes(), self.raw)
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_overwrite_backup_and_unique_names(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'output.sprite3d'
            target.write_bytes(b'old data, do not lose')
            first = c.write_atomic(target, self.raw)
            second = c.write_atomic(target, self.raw[:40])
            self.assertEqual(first.read_bytes(), b'old data, do not lose')
            self.assertEqual(second.read_bytes(), self.raw)
            self.assertNotEqual(first, second)
            self.assertEqual(target.read_bytes(), self.raw[:40])
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_invalid_export_leaves_existing_file_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'output.sprite3d'
            target.write_bytes(b'original')
            with self.assertRaises(c.FormatError):
                c.write_atomic(target, b'invalid')
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_backup_failure_does_not_replace_original(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'output.sprite3d'
            target.write_bytes(b'original')
            real_open = Path.open
            def failing_open(path, *args, **kwargs):
                if str(path).endswith('.bak'):
                    raise PermissionError('simulated backup failure')
                return real_open(path, *args, **kwargs)
            with patch.object(Path, 'open', failing_open), self.assertRaises(PermissionError):
                c.write_atomic(target, self.raw)
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_replace_failure_keeps_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'output.sprite3d'
            target.write_bytes(b'original')
            with patch.object(c.os, 'replace', side_effect=PermissionError('simulated')), self.assertRaises(PermissionError):
                c.write_atomic(target, self.raw)
            self.assertEqual(target.read_bytes(), b'original')
            self.assertFalse(list(Path(folder).glob('*.tmp')))
            self.assertEqual(len(list(Path(folder).glob('*.bak'))), 1)

    def test_no_backup_option(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'output.sprite3d'
            target.write_bytes(b'original')
            self.assertIsNone(c.write_atomic(target, self.raw, backup=False))
            self.assertEqual(target.read_bytes(), self.raw)
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_symlinks_directories_missing_folders_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'original.sprite3d'
            target.write_bytes(b'original')
            link = root / 'symlink.sprite3d'
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(c.FormatError):
                    c.write_atomic(link, self.raw)
                self.assertEqual(target.read_bytes(), b'original')
            with self.assertRaises(c.FormatError):
                c.write_atomic(root, self.raw)
            with self.assertRaises(c.FormatError):
                c.write_atomic(root / 'nonexistent' / 'test.sprite3d', self.raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)

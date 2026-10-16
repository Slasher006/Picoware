import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('pixelart',Path(__file__).resolve().parents[1]/'pixelart.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class PixelTests(unittest.TestCase):
    def test_fixed_palette(self):
        self.assertEqual(set(p.LOOKUP),set(p.PALETTE))
        for color in p.PALETTE:self.assertEqual(p.LOOKUP[color],color)
    def test_outline_does_not_grow_recursively_or_wrap(self):
        pixels,mask=p.stylize(bytes([255]*9),bytes([0x08,0]),3,3,True)
        self.assertEqual(mask,bytes([0x5d,0]))
        self.assertEqual(pixels[4],255)
        self.assertEqual(sum(bool(c) for c in pixels),1)
        _,edge=p.stylize(bytes(9),bytes([0x20,0]),3,3,True)
        self.assertEqual(edge,bytes([0x64,0]))
    def test_transparency(self):
        self.assertEqual(p.stylize(bytes([255]*8),bytes([0]),8,1,True),(bytes(8),bytes(1)))
    def test_length_validation(self):
        with self.assertRaises(ValueError):p.stylize(bytes(3),bytes(1),2,2)

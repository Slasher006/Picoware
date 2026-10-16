"""Packed asset validation and frame mapping without Blender/native allocations."""
import importlib.util
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[4] / 'src/MicroPython/picoware/engine/billboard_asset.py'
spec = importlib.util.spec_from_file_location('packed_loader_test', path)
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

class PackedAssetTests(unittest.TestCase):
    def setUp(self):
        class Billboard:
            def __init__(self, dirs, poses): self.frames = {}; self.counts = (dirs, poses)
            def set_direction_pose(self, direction, pose, image): self.frames[direction,pose] = image
        self.billboard = Billboard
        self.modules = {
            'picoware.engine.billboard': types.SimpleNamespace(Billboard=Billboard),
            'picoware.engine.image': types.SimpleNamespace(Image=lambda size, rgb, pixels, unused, mask:(size,pixels,mask)),
            'picoware.system.vector': types.SimpleNamespace(Vector=lambda w,h:(w,h)),
        }
        self.header = [b'PWBB',1,4,2,1,32,32,8.0,2.0,9216]
        self.payload = b''.join(bytes([i])*1024 + bytes([255-i])*128 for i in range(8))

    def load(self, data):
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules,self.modules):
            p = Path(directory)/'asset.billboard'; p.write_bytes(data)
            return loader._load(str(p))

    def test_frame_order_and_metadata(self):
        asset,height,poses,fps,loop = self.load(struct.pack('<4sBBBBHHffI',*self.header)+self.payload)
        self.assertEqual((height,poses,fps,loop),(2.0,2,8.0,True))
        for pose in range(2):
            for direction in range(4):
                size,pixels,mask=asset.frames[direction,pose]
                i=pose*4+direction
                self.assertEqual((size,pixels,mask),((32,32),bytes([i])*1024,bytes([255-i])*128))

    def test_small_frames_and_legacy_sizes(self):
        for size in (8,16,32,64,96,128):
            count=size*size; payload=bytes([17])*count+bytes([255])*((count+7)//8)
            header=struct.pack('<4sBBBBHHffI',b'PWBB',1,1,1,1,size,size,8.0,2.0,len(payload))
            asset,*_=self.load(header+payload)
            self.assertEqual(asset.frames[0,0],((size,size),bytes([17])*count,bytes([255])*((count+7)//8)))

    def test_native_buffer_path_avoids_intermediate_image(self):
        class NativeBillboard:
            def __init__(self, dirs, poses): self.frames = {}
            def set_direction_pose_data(self, direction, pose, width, height, pixels, mask):
                self.frames[direction,pose] = ((width,height),bytes(pixels),bytes(mask))
        self.modules['picoware.engine.billboard'] = types.SimpleNamespace(Billboard=NativeBillboard)
        self.modules['picoware.engine.image'] = types.SimpleNamespace(Image=lambda *args:self.fail('Intermediate image allocated'))
        asset,*_ = self.load(struct.pack('<4sBBBBHHffI',*self.header)+self.payload)
        self.assertEqual(len(asset.frames),8)
        self.assertEqual(asset.frames[3,1][1],bytes([7])*1024)

    def test_reject_corrupt_headers_before_allocating(self):
        cases = [(0,b'NOPE'),(1,2),(2,0),(2,9),(3,0),(3,9),(4,2),(5,1024),(6,64),
                 (7,0),(7,float('nan')),(7,float('inf')),(8,-1),(8,float('inf')),(9,1)]
        for index,value in cases:
            header=self.header[:];header[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(ValueError):
                self.load(struct.pack('<4sBBBBHHffI',*header)+self.payload)

    def test_reject_short_and_trailing_data(self):
        data=struct.pack('<4sBBBBHHffI',*self.header)+self.payload
        for bad in (b'',data[:23],data[:-1],data+b'x'):
            with self.assertRaises(ValueError):self.load(bad)

if __name__ == '__main__': unittest.main()

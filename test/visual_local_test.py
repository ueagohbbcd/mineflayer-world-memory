"""Synthetic mesh/atlas tests. No private world data or downloaded textures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'renderers'))
spec = importlib.util.spec_from_file_location('local_texture', Path(__file__).parents[1] / 'renderers/local_texture.py')
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


class LocalTextureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.meshdir = self.root / 'mesh'; self.meshdir.mkdir()
        self.assets = self.root / 'assets'; (self.assets / 'textures').mkdir(parents=True)
        atlas = self.assets / 'textures/1.21.1.png'
        Image.new('RGBA', (2, 2), (210, 120, 65, 255)).save(atlas)
        self.g = {'schema': 'world-memory.local-mesh.v1', 'positions': [-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0],
                  'normals': [0, 0, 1]*4, 'uvs': [0, 1, 1, 1, 1, 0, 0, 0], 'colors': [1, 1, 1]*4, 'indices': [0, 1, 2, 0, 2, 3]}
        self.m = {'schema': 'world-memory.local-mesh-metadata.v1', 'version': '1.21.1', 'assets': str(self.assets),
                  'atlasSha256': hashlib.sha256(atlas.read_bytes()).hexdigest(), 'lo': [-1, -1, 0], 'hi': [0, 0, 0]}
        (self.meshdir / 'mesh.json').write_text(json.dumps(self.g))
        (self.meshdir / 'metadata.json').write_text(json.dumps(self.m))

    def test_raster_nonempty_and_negative_coordinates(self):
        mesh, m, texture, _ = local.load_mesh(self.meshdir)
        im, info = local.render(mesh, texture, m['lo'], m['hi'], eye=[0, 0, 4], target=[0, 0, 0], width=256, height=256)
        self.assertGreater(info['geometryPixels'], 1000)
        self.assertEqual(info['nearPlaneSkippedTriangles'], 0)
        self.assertNotEqual(im.getpixel((128, 128)), (22, 30, 42))

    def test_png_metadata_and_source_unchanged(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        output = self.root / 'render.png'
        local.main(['--mesh-dir', str(self.meshdir), '--output', str(output), '--width', '256', '--height', '256'])
        with Image.open(output) as im:
            meta = json.loads(im.info['world-memory'])
            self.assertEqual(meta['source']['version'], '1.21.1')
            self.assertGreater(meta['render']['geometryPixels'], 0)
            self.assertEqual(im.size, (256, 256))
        sidecar=json.loads(output.with_name(output.name+'.json').read_text())
        self.assertEqual(sidecar['render']['imageSize'], [256,256])
        self.assertFalse(sidecar['presentation'])
        self.assertEqual(sidecar['units']['world'],'Minecraft blocks')
        self.assertIn('azimuthDegrees',sidecar['render'])
        for p, b in before.items():
            self.assertEqual(p.read_bytes(), b)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            local.main(['--mesh-dir', str(self.meshdir), '--output', str(output)])

    def test_presentation_is_explicit(self):
        output=self.root/'report.png'
        local.main(['--mesh-dir',str(self.meshdir),'--output',str(output),'--width','256','--height','256','--presentation'])
        with Image.open(output) as im:self.assertEqual(im.size,(256,322))
        self.assertTrue(json.loads(output.with_name(output.name+'.json').read_text())['presentation'])

    def test_output_safety_and_atlas_hash(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            local.safe_output(self.meshdir / 'out.png', [self.meshdir])
        alias = self.root / 'alias'; alias.symlink_to(self.meshdir, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'outside'):
            local.safe_output(alias / 'out.png', [self.meshdir])
        dangling = self.root / 'out.png'; dangling.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            local.safe_output(dangling, [self.meshdir])
        (self.assets / 'textures/1.21.1.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash'):
            local.load_mesh(self.meshdir)

    def test_air_only_empty_mesh_and_camera_validation(self):
        empty = {k: np.empty((0, n), dtype=float if k != 'indices' else int) for k, n in [('positions', 3), ('normals', 3), ('colors', 3), ('uvs', 2), ('indices', 3)]}
        texture = np.full((2, 2, 4), 255, dtype=np.uint8)
        _, info = local.render(empty, texture, [-2, -2, -2], [-1, -1, -1], width=64, height=64)
        self.assertEqual(info['geometryPixels'], 0)
        with self.assertRaisesRegex(ValueError, 'must differ'):
            local.render(empty, texture, [0, 0, 0], [1, 1, 1], eye=[0, 0, 0], target=[0, 0, 0])
        with self.assertRaisesRegex(ValueError, 'limits'):
            local.render(empty, texture, [0, 0, 0], [1, 1, 1], width=999999)

    def test_invalid_mesh_indices(self):
        self.g['indices'][0] = -1
        (self.meshdir / 'mesh.json').write_text(json.dumps(self.g))
        with self.assertRaisesRegex(ValueError, 'indices'):
            local.load_mesh(self.meshdir)


if __name__ == '__main__':
    unittest.main()

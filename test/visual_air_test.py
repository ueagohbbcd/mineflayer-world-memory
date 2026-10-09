import json,tempfile,unittest,sys,subprocess,shutil,hashlib
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'renderers'))
from extract_air_cast import extract,read_region,render_settings,ensure_output_paths,load_air_cast
class ExtractionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.src=self.root/'source';self.src.mkdir();self.a=np.ones((3,3,3),dtype='<u2');self.a[1,1,1]=2;self.a[2,1,1]=2;self.a[0,1,1]=0
  self.meta={'schema':'world-memory.region.v1','lo':[0,0,0],'hi':[2,2,2],'shape':[3,3,3],'palette':['unknown','stone','air'],'worldId':'synthetic','dimension':'overworld','version':'synthetic-version','stamps':{},'unknownVoxels':1,'unknownIsAir':False};self.write()
 def write(self):
  (self.src/'region.u16').write_bytes(self.a.tobytes());(self.src/'region.json').write_text(json.dumps(self.meta))
 def tearDown(self):self.tmp.cleanup()
 def run_extract(self,minimum=[0,0,0],maximum=[2,2,2],seed=[1,1,1],output=None):return extract(self.src,output or self.root/'out',minimum,maximum,seed)
 def test_unknown_never_air_and_shared_faces_removed(self):
  m=self.run_extract();g=json.loads((self.root/'out/geometry.json').read_text());self.assertEqual(m['voxel_count'],2);self.assertEqual(len(g['faces']),10);self.assertEqual(g['voxels'],[[1,1,1],[2,1,1]]);self.assertEqual(len(g['face_classes']),10);self.assertEqual(m['boundary_counts']['unknown_boundary'],2)
 def test_known_continuation_is_recorded_at_crop(self):
  m=self.run_extract(maximum=[1,2,2]);self.assertEqual(m['voxel_count'],1);self.assertEqual(m['boundary_counts']['crop_known_air'],1);self.assertTrue(any(c['known_air_continues'] for c in m['cropped_faces']))
 def test_unknown_seed_rejected(self):
  with self.assertRaises(ValueError):self.run_extract(seed=[0,1,1])
 def test_roi_outside_source_rejected(self):
  with self.assertRaises(ValueError):self.run_extract(minimum=[-1,0,0])
 def test_schema_rejected(self):
  self.meta['schema']='other';self.write()
  with self.assertRaises(ValueError):read_region(self.src)
 def test_shape_rejected(self):
  self.meta['shape']=[3,3,2];self.write()
  with self.assertRaises(ValueError):read_region(self.src)
 def test_binary_size_rejected(self):
  (self.src/'region.u16').write_bytes(b'xx')
  with self.assertRaises(ValueError):read_region(self.src)
 def test_palette_duplicates_rejected(self):
  self.meta['palette']=['unknown','air','air'];self.write()
  with self.assertRaises(ValueError):read_region(self.src)
 def test_palette_index_rejected(self):
  self.a[1,1,1]=99;self.write()
  with self.assertRaises(ValueError):read_region(self.src)
 def test_unknown_count_rejected(self):
  self.meta['unknownVoxels']=0;self.write()
  with self.assertRaises(ValueError):read_region(self.src)
 def test_native_state_unknown_disagreement_rejected(self):
  (self.src/'states.i32').write_bytes(np.zeros((3,3,3),dtype='<i4').tobytes())
  with self.assertRaises(ValueError):read_region(self.src)
 def test_boolean_coordinate_rejected(self):
  with self.assertRaises(ValueError):self.run_extract(seed=[True,1,1])
 def test_output_in_source_or_parent_rejected(self):
  for dest in [self.src,self.src/'cast',self.root]:
   with self.subTest(dest=dest),self.assertRaises(ValueError):self.run_extract(output=dest)
 def test_output_symlink_rejected(self):
  dest=self.root/'out';dest.mkdir();(dest/'geometry.json').symlink_to(self.src/'region.json')
  before=(self.src/'region.json').read_bytes()
  with self.assertRaises(ValueError):self.run_extract()
  self.assertEqual((self.src/'region.json').read_bytes(),before)
 def test_output_never_overwrites(self):
  self.run_extract();before=(self.root/'out/geometry.json').read_bytes()
  with self.assertRaises(ValueError):self.run_extract()
  self.assertEqual((self.root/'out/geometry.json').read_bytes(),before)
 def test_source_unchanged(self):
  before={p.name:p.read_bytes() for p in self.src.iterdir()};self.run_extract();self.assertEqual(before,{p.name:p.read_bytes() for p in self.src.iterdir()})
 def test_default_camera_contract(self):
  s=render_settings({},[[0,0,0],[3,4,5]]);self.assertEqual(s['azimuths_degrees'],[35,215]);self.assertEqual(s['elevation_degrees'],33);self.assertTrue(s['world_light_fixed']);self.assertTrue(s['grid']);self.assertEqual(s['grid_pitch_blocks'],1)
 def test_invalid_camera_pair_rejected(self):
  for pair in [[35,170],[35],[35,215,300]]:
   with self.subTest(pair=pair),self.assertRaises(ValueError):render_settings({'azimuths_degrees':pair},[[0,0,0],[3,4,5]])
 def test_single_azimuth_generates_opposite(self):
  self.assertEqual(render_settings({'azimuth_degrees':350},[[0,0,0],[3,4,5]])['azimuths_degrees'],[350,170])
 def test_nonfinite_elevation_rejected(self):
  with self.assertRaises(ValueError):render_settings({'elevation_degrees':float('nan')},[[0,0,0],[3,4,5]])
 def test_nonfinite_world_light_rejected(self):
  with self.assertRaises(ValueError):render_settings({'lights_blender':[['Key',[0,float('inf'),0],100,3]]},[[0,0,0],[3,4,5]])
 def test_malformed_center_rejected(self):
  with self.assertRaises(ValueError):render_settings({'center_blender':[0,1]},[[0,0,0],[3,4,5]])
 def test_chunk_stamp_array_validation(self):
  self.meta['stamps']=[{'x':0,'z':-1,'observedAt':123,'sha256':'a'*64}];self.write();m,_,_=read_region(self.src);self.assertIsInstance(m['stamps'],list)
  self.meta['stamps'][0]['x']=0.5;self.write()
  with self.assertRaises(ValueError):read_region(self.src)
 @unittest.skipUnless(shutil.which('node'),'Node is needed for the native exporter integration test')
 def test_real_js_region_exporter_contract(self):
  repo=Path(__file__).resolve().parents[1];folder=self.root/'native-demo';folder.mkdir()
  script=r"""
const fs=require('node:fs/promises'),path=require('node:path'),zlib=require('node:zlib');
const {exportRegion}=require('./lib/visual');
const registry=require('prismarine-registry')('1.21.1'),Chunk=require('prismarine-chunk')('1.21.1');
(async()=>{const root=process.argv[1],directory=path.join(root,'cache'),worldId='synthetic-integration',scope=path.join(directory,Buffer.from(worldId).toString('base64url'));await fs.mkdir(scope,{recursive:true});const c=new Chunk({minY:-16,worldHeight:32});for(let x=0;x<16;x++)for(let z=0;z<16;z++)c.setBlockStateId({x,y:-4,z},registry.blocksByName.stone.minStateId);const file=path.join(scope,Buffer.from('overworld').toString('base64url')+'_-1_0.json.gz');await fs.writeFile(file,zlib.gzipSync(JSON.stringify({version:'1.21.1',dimension:'overworld',x:-1,z:0,observedAt:123456,json:c.toJson()})));await exportRegion({directory,worldId,dimension:'overworld',version:'1.21.1',min:[-16,-4,0],max:[-15,-3,1],output:path.join(root,'region')});})().catch(e=>{console.error(e);process.exit(1)});
"""
  subprocess.run(['node','-e',script,str(folder)],cwd=repo,check=True,capture_output=True,text=True)
  native=list((folder/'cache').rglob('*.json.gz'))[0];before=hashlib.sha256(native.read_bytes()).hexdigest();m=extract(folder/'region',folder/'cast',[-16,-4,0],[-15,-3,1],[-16,-3,0]);self.assertEqual(m['voxel_count'],4);self.assertIsInstance(m['source_chunk_stamps'],list);self.assertEqual(m['source_chunk_stamps'][0]['observedAt'],123456);self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(),before)
 def test_source_budget_early_rejection(self):
  with patch('extract_air_cast.MAX_SOURCE_VOXELS',26),self.assertRaisesRegex(ValueError,'source budget'):self.run_extract()
  self.assertFalse((self.root/'out').exists())
 def test_component_budget_early_rejection(self):
  with patch('extract_air_cast.MAX_COMPONENT_VOXELS',1),self.assertRaisesRegex(ValueError,'component exceeds budget'):self.run_extract()
  self.assertFalse((self.root/'out').exists())
 def test_face_budget_early_rejection(self):
  with patch('extract_air_cast.MAX_EXPOSED_FACES',9),self.assertRaisesRegex(ValueError,'exposed faces'):self.run_extract()
  self.assertFalse((self.root/'out').exists())
 def test_component_and_face_budget_exact_boundary(self):
  with patch('extract_air_cast.MAX_COMPONENT_VOXELS',2),patch('extract_air_cast.MAX_EXPOSED_FACES',10):self.assertEqual(self.run_extract()['voxel_count'],2)
 def test_long_narrow_camera_clip_and_fit(self):
  for resolution in [[64,1024],[1024,64],[512,512]]:
   with self.subTest(resolution=resolution):
    s=render_settings({'resolution':resolution},[[0,0,0],[1,1,500]]);self.assertGreater(s['clip_end'],s['camera_distance']+s['bounding_radius']);self.assertLess(s['clip_start'],s['camera_distance']-s['bounding_radius']);self.assertEqual(s['sensor_fit'],'HORIZONTAL');self.assertFalse(s['manual_scale_may_clip']);self.assertEqual(s['orthographic_scale'],s['default_fit_scale'])
 def test_manual_scale_is_explicitly_marked(self):
  s=render_settings({'orthographic_scale':1},[[0,0,0],[1,1,500]]);self.assertTrue(s['manual_scale_may_clip']);self.assertEqual(s['orthographic_scale'],1);self.assertGreater(s['default_fit_scale'],1)
 def test_json_byte_budget_before_parsing(self):
  with patch('extract_air_cast.MAX_JSON_BYTES',10),self.assertRaisesRegex(ValueError,'JSON input exceeds byte budget'):read_region(self.src)
 def test_render_geometry_cannot_bypass_face_budget(self):
  self.run_extract()
  with patch('extract_air_cast.MAX_EXPOSED_FACES',9),self.assertRaisesRegex(ValueError,'producer budget'):load_air_cast(self.root/'out')
 def test_render_geometry_cannot_bypass_voxel_budget(self):
  self.run_extract()
  with patch('extract_air_cast.MAX_COMPONENT_VOXELS',1),self.assertRaisesRegex(ValueError,'producer budget'):load_air_cast(self.root/'out')
 def test_render_resolution_budget(self):
  with self.assertRaisesRegex(ValueError,'pixel budget'):render_settings({'resolution':[8192,8192]},[[0,0,0],[1,1,500]])
if __name__=='__main__':unittest.main()

"""Synthetic-only renderer tests: no real world data or Minecraft assets."""
import copy,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
import numpy as np

SCRIPT=Path(__file__).resolve().parents[1]/'renderers'/'surface.py'
spec=importlib.util.spec_from_file_location('surface_renderer',SCRIPT);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class SurfaceRendererTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.assets=self.root/'assets';(self.assets/'blocksStates').mkdir(parents=True);(self.assets/'textures').mkdir()
  self.atlas=Image.new('RGBA',(32,16));p=self.atlas.load()
  for y in range(16):
   for x in range(16):p[x,y]=(220 if (x+y)%2 else 150,60,20,255);p[x+16,y]=(20,80,220,255 if x<4 else 0)
  self.atlas.save(self.assets/'textures/1.21.1.png')
  def block(u):return {'variants':{'':{'model':{'textures':{},'elements':[{'from':[0,0,0],'to':[16,16,16],'faces':{'up':{'texture':{'u':u,'v':0,'su':.5,'sv':1}}}}]}}}}
  self.models={'test_wood':block(0),'test_glass':block(.5)};self.save_models()
  self.snapshot={'schema':'world-memory.surface-map.v1','version':'1.21.1','X0':-1,'X1':1,'Z0':0,'Z1':0,'states':{'1':{'name':'test_wood','properties':{}},'2':{'name':'test_glass','properties':{}}},'cells':[[-1,0,[[64,1]],None,64],[0,0,[[65,2],[64,1]],None,64]],'columnStatus':[[-1,0,'surface'],[0,0,'surface'],[1,0,'unknown']],'biomeRegistry':{}}
  self.source=self.root/'surface.json';self.output=self.root/'map.png'
 def tearDown(self):self.temp.cleanup()
 def save_models(self):(self.assets/'blocksStates/1.21.1.json').write_text(json.dumps(self.models))
 def run_cli(self,*extra,success=True,output=None):
  self.source.write_text(json.dumps(self.snapshot));env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
  r=subprocess.run([sys.executable,str(SCRIPT),'--input',str(self.source),'--assets',str(self.assets),'--output',str(output or self.output),'--scale','16',*extra],text=True,capture_output=True,env=env)
  self.assertEqual(r.returncode==0,success,r.stdout+r.stderr);return r
 def test_real_texture_alpha_and_unknown_mask(self):
  self.run_cli();a=np.array(Image.open(self.output));self.assertEqual(a.shape,(16,48,3));self.assertTrue(np.all(a[:,32:]==[204,210,211]));self.assertFalse(np.array_equal(a[0,0],a[0,1]));self.assertGreater(a[0,16,2],a[0,16,0]);self.assertGreater(a[0,22,0],a[0,22,2])
 def test_unknown_biomes_do_not_create_boundary(self):
  self.run_cli();base=self.output.read_bytes();self.output.unlink();self.run_cli('--biomes');self.assertEqual(base,self.output.read_bytes())
 def test_hillshade_uses_natural_height_and_preserves_unknown(self):
  self.run_cli('--mode','hillshade');a=np.array(Image.open(self.output));self.assertTrue(np.all(a[:,:16]==a[:,16:32]));self.assertTrue(np.all(a[:,32:]==[204,210,211]))
 def test_missing_material_is_error_not_fake_color(self):
  self.models.pop('test_wood');self.save_models();r=self.run_cli(success=False);self.assertIn('Missing material',r.stderr);self.assertFalse(self.output.exists())
 def test_missing_atlas_is_error(self):
  (self.assets/'textures/1.21.1.png').unlink();r=self.run_cli(success=False);self.assertIn('Missing version-matched asset',r.stderr)
 def test_bad_uv_rejected(self):
  self.models['test_wood']['variants']['']['model']['elements'][0]['faces']['up']['texture']['u']=2;self.save_models();self.run_cli(success=False);self.assertFalse(self.output.exists())
 def test_version_mismatch(self):self.assertIn('does not match',self.run_cli('--version','1.20.4',success=False).stderr)
 def test_invalid_schema(self):self.snapshot['schema']='wrong';self.run_cli(success=False)
 def test_invalid_bounds(self):self.snapshot['X1']=-2;self.run_cli(success=False)
 def test_large_allocation_prevented(self):self.snapshot['X1']=1000000;self.run_cli(success=False)
 def test_invalid_scale(self):self.run_cli('--scale','0',success=False)
 def test_no_surface_grid_option(self):self.assertIn('unrecognized arguments',self.run_cli('--grid',success=False).stderr)
 def test_input_output_overlap(self):self.run_cli(output=self.source,success=False);self.assertEqual(json.loads(self.source.read_text())['schema'],'world-memory.surface-map.v1')
 def test_existing_output_preserved(self):self.output.write_bytes(b'preserve');self.run_cli(success=False);self.assertEqual(self.output.read_bytes(),b'preserve')
 def test_symlink_existing_file_preserved(self):
  target=self.root/'important';target.write_bytes(b'preserve');self.output.symlink_to(target);self.run_cli(success=False);self.assertEqual(target.read_bytes(),b'preserve')
 def test_asset_output_disallowed(self):self.run_cli(output=self.assets/'new.png',success=False)
 def test_non_png_disallowed(self):self.run_cli(output=self.root/'cache.json.gz',success=False)
 def test_duplicate_cell(self):self.snapshot['cells'].append(copy.deepcopy(self.snapshot['cells'][0]));self.run_cli(success=False)
 def test_unknown_state(self):self.snapshot['cells'][0][2][0][1]=999;self.run_cli(success=False)
 def test_unknown_status_cannot_contain_layers(self):self.snapshot['columnStatus'][0][2]='unknown';self.run_cli(success=False)
 def test_chinese_requires_font(self):self.run_cli('--biomes','--label-language','zh',success=False)
 def test_biome_component_anchor_without_scipy(self):
  a=np.full((16,16),-1);a[1:14,1:14]=4;a[15,15]=4;region,anchor=module.largest_region_anchor(a,4);self.assertEqual(len(region),169);self.assertEqual(anchor,(7,7))
 def test_biome_sparse_overlay(self):
  self.snapshot.update(X0=0,X1=23,Z0=0,Z1=11);self.snapshot['cells']=[[x,z,[[64,1]],4 if x<12 else 5,64] for z in range(12) for x in range(24)];self.snapshot['columnStatus']=[];self.snapshot['biomeRegistry']={'4':{'name':'forest'},'5':{'name':'plains'}}
  self.run_cli('--biomes')
  with Image.open(self.output) as image:self.assertEqual(image.size,(384,192))
 def validate_with_budget(self,key,value):
  self.source.write_text(json.dumps(self.snapshot))
  args=SimpleNamespace(input=self.source,output=self.output,assets=self.assets,font=None,label_language='en',version=None,scale=16)
  with patch.object(module,key,value):return module.validate(args)
 def test_column_budget_exact_boundary(self):
  self.validate_with_budget('MAX_COLUMNS',3)
  with self.assertRaisesRegex(ValueError,'size limit'):self.validate_with_budget('MAX_COLUMNS',2)
 def test_total_layer_budget_exact_boundary(self):
  self.validate_with_budget('MAX_TOTAL_LAYERS',3)
  with self.assertRaisesRegex(ValueError,'layer count'):self.validate_with_budget('MAX_TOTAL_LAYERS',2)
 def test_input_byte_budget_exact_boundary(self):
  self.source.write_text(json.dumps(self.snapshot));size=self.source.stat().st_size
  self.validate_with_budget('MAX_INPUT_BYTES',size)
  with self.assertRaisesRegex(ValueError,'64 MiB'):self.validate_with_budget('MAX_INPUT_BYTES',size-1)
 def test_column_cache_is_bounded_lru(self):
  tiles={str(i):Image.new('RGBA',(16,16),(i*50,10,20,255)) for i in range(1,4)}
  compose=module.make_column_renderer(tiles,capacity=2)
  compose((1,));compose((2,));compose((1,));compose((3,))
  self.assertEqual(compose.cache_info().currsize,2);self.assertEqual(compose.cache_info().hits,1)
  compose((2,));self.assertEqual(compose.cache_info().misses,4);self.assertEqual(compose.cache_info().currsize,2)
 def test_column_cache_rejects_excess_capacity(self):
  with self.assertRaises(ValueError):module.make_column_renderer({},capacity=module.COLUMN_CACHE_SIZE+1)
if __name__=='__main__':unittest.main()

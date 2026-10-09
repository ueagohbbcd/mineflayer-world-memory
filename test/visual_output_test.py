"""Synthetic bare-output contract tests; no external textures or world cache."""
import contextlib,io,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'renderers'))
from image_output import output_paths,image_metadata,write_pair,summarize

class OutputTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_same_basename_source_remains_intact(self):
  source=self.root/'surface.json';source.write_text('source-data');output=self.root/'surface.png'
  result=write_pair(Image.new('RGB',(4,3)),output,image_metadata('surface',False,{},{}),protected_files=[source])
  self.assertEqual(source.read_text(),'source-data');self.assertEqual(result['metadata'],str(self.root/'surface.png.json'))
  with Image.open(output) as im:self.assertEqual(im.size,(4,3))
 def test_sidecar_collision_blocks_png_creation(self):
  output=self.root/'map.png';sidecar=self.root/'map.png.json';sidecar.write_text('preserve')
  with self.assertRaisesRegex(ValueError,'exists'):write_pair(Image.new('RGB',(4,3)),output,image_metadata('surface',False,{},{}))
  self.assertFalse(output.exists());self.assertEqual(sidecar.read_text(),'preserve')
 def test_sidecar_symlink_and_source_directory_alias_rejected(self):
  source=self.root/'source';source.mkdir();important=source/'important';important.write_text('keep')
  (self.root/'map.png.json').symlink_to(important)
  with self.assertRaisesRegex(ValueError,'exists'):output_paths(self.root/'map.png')
  alias=self.root/'alias';alias.symlink_to(source,target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'outside'):output_paths(alias/'new.png',protected_dirs=[source])
  self.assertEqual(important.read_text(),'keep')
 def test_pair_metadata_json_is_finite(self):
  with self.assertRaises(ValueError):write_pair(Image.new('RGB',(1,1)),self.root/'bad.png',image_metadata('surface',False,{}, {'bad':float('nan')}))
  self.assertFalse((self.root/'bad.png').exists())
 def test_summary_is_compact_with_large_provenance(self):
  source={'worldId':'w','chunks':[{'observedAt':i} for i in range(2000)],'columnStatus':[[i,0,'unknown'] for i in range(2000)],'boundsXZ':[0,1999,0,0]}
  result=summarize(image_metadata('surface',False,source,{'orientation':'north up','grid':False,'lighting':'display hillshade'}))
  self.assertEqual(result['freshness']['observedAtMax'],1999);self.assertEqual(result['unknown']['columnCounts']['unknown'],2000);self.assertLess(len(json.dumps(result)),3000)
 def cave_fixture(self):
  source=self.root/'region';source.mkdir();d=self.root/'cast';d.mkdir()
  m={'source':str(source),'worldId':'synthetic','dimension':'overworld','version':'1.21.1','bounds_xyz_inclusive':[[0,0,0],[2,2,2]],'voxel_count':2,'source_chunk_stamps':[{'x':0,'z':0,'observedAt':123,'sha256':'a'*64}],'boundary_counts':{'crop_known_air':1,'unknown_boundary':2},'cropped_faces':[{'kind':'crop_known_air'}],'unknown_is_air':False}
  r={'azimuths_degrees':[35,215],'elevation_degrees':33,'orthographic_scale':7,'grid':True,'grid_pitch_blocks':1,'world_light_fixed':True,'same_camera_scale':True,'lighting':'Fixed world studio lighting','resolution':[64,48]}
  (d/'metadata.json').write_text(json.dumps(m));(d/'render-metadata.json').write_text(json.dumps(r))
  for i,color in [(1,(120,140,160,255)),(2,(170,180,190,255))]:
   Image.new('RGBA',(64,48),color).save(d/f'view-{i}-raw.png')
   (d/f'view-{i}-labels.json').write_text(json.dumps({'P':[12,12]}));(d/f'view-{i}-axes.json').write_text('[]')
  return d
 def compose(self,d,*args):
  p=Path(__file__).resolve().parents[1]/'renderers/compose_air_cast.py'
  return subprocess.run([sys.executable,str(p),'--workdir',str(d),*args],check=True,text=True,capture_output=True)
 def test_cave_default_is_direct_join_with_sidecar(self):
  d=self.cave_fixture();result=json.loads(self.compose(d).stdout);image=Image.open(result['output'])
  self.assertEqual(image.size,(128,48));self.assertEqual(image.getpixel((0,0)),(120,140,160));self.assertEqual(image.getpixel((64,0)),(170,180,190))
  self.assertEqual(result['summary']['azimuthsDegrees'],[35,215]);self.assertTrue(result['summary']['grid']);self.assertEqual(len(image.getcolors()),2) # no text, outlines, ruler or extra canvas
  record=json.loads(Path(result['metadata']).read_text());self.assertFalse(record['presentation'])
  self.assertEqual(record['render']['panels'][1]['imageRectangle'],[64,0,64,48]);self.assertEqual(record['render']['azimuths_degrees'],[35,215])
  self.assertEqual(record['source']['source_chunk_stamps'][0]['observedAt'],123);self.assertNotIn('cropped_faces',record['source']);self.assertEqual(record['source']['details']['metadataFile'],str(d/'metadata.json'));self.assertEqual(len(record['source']['details']['sha256']),64)
  self.assertEqual(record['render']['panels'][0]['annotations'],{'P':[12,12]})
 def test_cave_presentation_remains_available(self):
  d=self.cave_fixture();result=json.loads(self.compose(d,'--presentation').stdout)
  self.assertTrue(result['presentation'])
  with Image.open(result['output']) as im:self.assertGreater(im.height,48)
  self.assertEqual(Path(result['output']).name,'air-cast-presentation.png')

if __name__=='__main__':unittest.main()

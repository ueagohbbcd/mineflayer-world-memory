import bpy,json,math,pathlib,argparse,sys
from mathutils import Vector
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from extract_air_cast import ensure_output_paths,render_settings,read_json_limited,load_air_cast
parser=argparse.ArgumentParser();parser.add_argument('--workdir',required=True);parser.add_argument('--config');args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
D=pathlib.Path(args.workdir).resolve();g,metadata=load_air_cast(D);cfg=read_json_limited(args.config,1024*1024) if args.config else {}
if g.get('schema')!='world-memory.air-cast.v1' or metadata.get('schema')!='world-memory.air-cast.metadata.v1':raise ValueError('Unsupported air-cast schema')
ensure_output_paths(D,['view-1-raw.png','view-2-raw.png','view-1-labels.json','view-2-labels.json','view-1-axes.json','view-2-axes.json','air-cast.blend','render-metadata.json'],metadata['source'])
if not g.get('vertices') or not g.get('faces'):raise ValueError('Empty geometry')
if any(not isinstance(v,list) or len(v)!=3 or any(type(c) not in (int,float) or not math.isfinite(c) for c in v) for v in g['vertices']):raise ValueError('Invalid geometry vertex')
if any(not isinstance(f,list) or len(f)!=4 or any(type(i) is not int or not 0<=i<len(g['vertices']) for i in f) for f in g['faces']):raise ValueError('Invalid quad index')
if len(g.get('face_classes',[]))!=len(g['faces']) or any(c not in ('known_solid_boundary','crop_known_air','unknown_boundary') for c in g['face_classes']):raise ValueError('Invalid face classification')
vertices=g['vertices'];bounds=[[min(p[i] for p in vertices) for i in range(3)],[max(p[i] for p in vertices) for i in range(3)]]
settings=render_settings(cfg,bounds)
center=Vector(cfg.get('center_blender',[(bounds[0][i]+bounds[1][i])/2 for i in range(3)]));span=max(bounds[1][i]-bounds[0][i] for i in range(3))
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
mesh=bpy.data.meshes.new('Measured known-air boundary');mesh.from_pydata(g['vertices'],[],g['faces']);mesh.update();obj=bpy.data.objects.new('Air cast | not solid rock',mesh);bpy.context.collection.objects.link(obj)
mat=bpy.data.materials.new('Neutral white clay');mat.diffuse_color=(.83,.83,.83,1);mat.use_nodes=True;p=mat.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(.83,.83,.83,1);p.inputs['Roughness'].default_value=.77;obj.data.materials.append(mat)
# Tint only artificial continuation/unknown caps. White is known-air cast, never rock.
for name,color in [('Known-air continues outside ROI',(.70,.49,.19,1)),('Unknown beyond observed volume',(.48,.31,.66,1))]:
 cap=bpy.data.materials.new(name);cap.use_nodes=True;cap.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=color;cap.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.85;obj.data.materials.append(cap)
for polygon,category in zip(mesh.polygons,g['face_classes']):polygon.material_index={'known_solid_boundary':0,'crop_known_air':1,'unknown_boundary':2}[category]
# Tiny bevel only helps edges read; no smoothing of the voxel-scale topology.
b=obj.modifiers.new('Subpixel edge highlight','BEVEL');bb=cfg.get("bevel_blocks",.025);bb and setattr(b,'width',bb);b.segments=1
# True voxel-face edges only: quads have no diagonal. Solid cast occludes back edges.
edges=set()
for f in g['faces']:
 for j,a in enumerate(f):
  aa=tuple(g['vertices'][a]);bb=tuple(g['vertices'][f[(j+1)%len(f)]]);edges.add(tuple(sorted((aa,bb))))
cv=bpy.data.curves.new('One-block surface grid','CURVE');cv.dimensions='3D';cv.bevel_depth=cfg.get("grid_radius_blocks",.012);cv.bevel_resolution=1;cv.resolution_u=1
for aa,bb in sorted(edges):
 sp=cv.splines.new('POLY');sp.points.add(1);sp.points[0].co=(*aa,1);sp.points[1].co=(*bb,1)
gr=bpy.data.objects.new('Grid | 1 block pitch | opaque depth tested',cv);bpy.context.collection.objects.link(gr);gr.hide_render=not cfg.get('grid',True)
gm=bpy.data.materials.new('Fine neutral gray grid');gm.use_nodes=True;gp=gm.node_tree.nodes.get('Principled BSDF');gp.inputs['Base Color'].default_value=(.32,.32,.32,1);gp.inputs['Roughness'].default_value=.9;cv.materials.append(gm)
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=settings["samples"];scene.cycles.use_denoising=False
scene.world.color=(.20,.20,.20);scene.render.film_transparent=True
width,height=settings["resolution"];scene.render.resolution_x=width;scene.render.resolution_y=height;scene.render.resolution_percentage=100
scene.view_settings.view_transform='Standard';scene.view_settings.look='Medium High Contrast';scene.view_settings.exposure=0;scene.view_settings.gamma=1

def aim(o,p):o.rotation_euler=(p-o.location).to_track_quat('-Z','Y').to_euler()
lights=cfg.get('lights_blender',[[name,list(center+Vector(offset)*(span/30)),power*(span/30)**2,size*span/30] for name,offset,power,size in [('Key',(-18,-15,33),2600,18),('Fill',(27,20,13),1700,15),('Rim',(-18,25,13),1800,12)]])
for name,loc,power,size in lights:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.name=name;o.data.energy=power;o.data.shape='DISK';o.data.size=size;aim(o,center)
bpy.ops.object.camera_add();cam=bpy.context.object;scene.camera=cam;cam.data.type='ORTHO';cam.data.sensor_fit='HORIZONTAL';cam.data.ortho_scale=settings['orthographic_scale'];cam.data.clip_start=settings['clip_start'];cam.data.clip_end=settings['clip_end']
azimuths=settings["azimuths_degrees"];elevation=settings["elevation_degrees"]
projected_geometry_bounds=[]
for i,az in enumerate(azimuths):
 el=math.radians(elevation);azr=math.radians(az);cam.location=center+Vector((math.cos(el)*math.cos(azr),math.cos(el)*math.sin(azr),math.sin(el)))*settings['camera_distance'];aim(cam,center)
 bpy.context.view_layer.update()
 from bpy_extras.object_utils import world_to_camera_view
 import itertools
 check=[world_to_camera_view(scene,cam,Vector(p)) for p in itertools.product(*[(bounds[0][j],bounds[1][j]) for j in range(3)])]
 extent={'min':[min(q[j] for q in check) for j in range(3)],'max':[max(q[j] for q in check) for j in range(3)]};projected_geometry_bounds.append(extent)
 if not settings['manual_scale_may_clip'] and (any(extent['min'][j]<-1e-5 or extent['max'][j]>1+1e-5 for j in (0,1)) or extent['min'][2]<cam.data.clip_start or extent['max'][2]>cam.data.clip_end):raise ValueError('Default camera fitting failed; refusing clipped output')
 scene.render.filepath=str(D/f'view-{i+1}-raw.png');bpy.ops.render.render(write_still=True)
 # Record projections for truthful annotations.
 from bpy_extras.object_utils import world_to_camera_view
 pts=cfg.get('annotation_points_blender',{})
 projection={k:[float(q.x)*width,(1-float(q.y))*height] for k,p in pts.items() for q in [world_to_camera_view(scene,cam,Vector(p))]}
 json.dump(projection,open(D/f'view-{i+1}-labels.json','x'))
 lo,hi=metadata['actual_bounds_xyz'];axes=[]
 for axis in range(3):
  step=max(1,math.ceil((hi[axis]-lo[axis]+1)/4));values=list(range(lo[axis],hi[axis]+2,step))
  if values[-1]!=hi[axis]+1:values.append(hi[axis]+1)
  ticks=[]
  for value in values:
   point=[lo[0]-1,lo[1]-1,lo[2]-1];point[axis]=value;x,y,z=point;q=world_to_camera_view(scene,cam,Vector((x,-z,y)));ticks.append({'value':value,'screen':[float(q.x)*width,(1-float(q.y))*height]})
  axes.append({'axis':'XYZ'[axis],'ticks':ticks})
 json.dump(axes,open(D/f'view-{i+1}-axes.json','x'))
bpy.ops.wm.save_as_mainfile(filepath=str(D/'air-cast.blend'))

(D/'render-metadata.json').write_text(json.dumps({'azimuths_degrees':azimuths,'elevation_degrees':elevation,'orthographic_scale':cam.data.ortho_scale,'center_blender':list(center),'lights_blender':lights,'grid':not gr.hide_render,'grid_pitch_blocks':1,'grid_radius_blocks':cv.bevel_depth,'resolution':[width,height],'azimuth_reference':'East=0, North=90','lighting':'Fixed world studio lighting, not measured Minecraft light','world_light_fixed':True,'same_camera_scale':True,'projected_geometry_bounds':projected_geometry_bounds,'camera_distance':settings['camera_distance'],'bounding_radius':settings['bounding_radius'],'clip_start':settings['clip_start'],'clip_end':settings['clip_end'],'sensor_fit':settings['sensor_fit'],'default_fit_scale':settings['default_fit_scale'],'manual_scale_may_clip':settings['manual_scale_may_clip'],'boundary_colors':{'crop_known_air':'gold','unknown_boundary':'purple'},'boundary_counts':metadata['boundary_counts']},indent=2))

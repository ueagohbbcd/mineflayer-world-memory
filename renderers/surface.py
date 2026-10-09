"""Read-only surface map renderer. Requires NumPy and Pillow only.
Real atlas/model textures; native biome boundaries; no surface-grid mode.
Plant symbols, fixed tints and partial-model approximations are deliberate.
"""
import argparse,json,math,re,sys,time,hashlib
from pathlib import Path
from image_output import output_paths, image_metadata, write_pair
from collections import Counter,deque
from functools import lru_cache
import numpy as np
from PIL import Image,ImageDraw,ImageFont

CHINESE={'forest':'森林','birch_forest':'桦木森林','old_growth_birch_forest':'原始桦木森林','grove':'雪林','jagged_peaks':'尖峭山峰','plains':'平原','ocean':'海洋','river':'河流','stony_shore':'石岸'}
MAX_COLUMNS=262144
MAX_TOTAL_LAYERS=1048576
MAX_INPUT_BYTES=64*1024*1024
COLUMN_CACHE_SIZE=4096
MAX_PIXELS=64*1024*1024

def integer(value):return isinstance(value,int) and not isinstance(value,bool)
def validate(args):
 source=args.input.resolve(strict=True);output=args.output.resolve();assets=args.assets.resolve(strict=True)
 if not source.is_file() or source.stat().st_size>MAX_INPUT_BYTES:raise ValueError('Input must be a JSON file no larger than 64 MiB')
 if output==source or args.output.exists() or args.output.is_symlink():raise ValueError('Output already exists or overlaps input; choose a new PNG')
 if args.output.suffix.lower()!='.png':raise ValueError('Output must have .png suffix; cache files must never be overwritten')
 if output==assets or assets in output.parents:raise ValueError('Output must be outside asset directory')
 if not assets.is_dir():raise ValueError('Assets must be a Prismarine-viewer public directory')
 if args.font is not None and not args.font.is_file():raise ValueError('Font file does not exist')
 if args.presentation and args.label_language=='zh' and args.font is None:raise ValueError('Chinese labels require an explicit CJK --font file')
 j=json.loads(source.read_text())
 if not isinstance(j,dict) or j.get('schema')!='world-memory.surface-map.v1':raise ValueError('Unsupported surface snapshot schema')
 version=j.get('version')
 if not isinstance(version,str) or not re.fullmatch(r'1\.\d+(?:\.\d+)?',version):raise ValueError('Invalid snapshot Minecraft version')
 if args.version is not None and args.version!=version:raise ValueError('Requested version does not match snapshot version')
 args.version=version
 bounds=[j.get(k) for k in ('X0','X1','Z0','Z1')]
 if not all(integer(v) and abs(v)<=30000000 for v in bounds):raise ValueError('Bounds must be supported integer coordinates')
 x0,x1,z0,z1=bounds;w,h=x1-x0+1,z1-z0+1
 if w<1 or h<1 or w*h>MAX_COLUMNS or w*h*args.scale**2>MAX_PIXELS:raise ValueError('Invalid bounds or requested image exceeds size limit')
 states=j.get('states');cells=j.get('cells')
 if not isinstance(states,dict) or not isinstance(cells,list) or len(cells)>w*h:raise ValueError('Invalid surface states/cells')
 for id,state in states.items():
  if not isinstance(id,str) or not id.isdigit() or not isinstance(state,dict) or not re.fullmatch(r'[a-z0-9_]+',state.get('name','')) or not isinstance(state.get('properties'),dict):raise ValueError('Invalid state definition')
 seen=set();total_layers=0
 for c in cells:
  if not isinstance(c,list) or len(c)!=5:raise ValueError('Cell must be [x,z,layers,biome,naturalGround]')
  x,z,layers,biome,ground=c
  if not integer(x) or not integer(z) or not(x0<=x<=x1 and z0<=z<=z1) or (x,z) in seen:raise ValueError('Duplicate or out-of-bounds cell')
  seen.add((x,z))
  if not isinstance(layers,list) or len(layers)>64:raise ValueError('Invalid layer count')
  total_layers+=len(layers)
  if total_layers>MAX_TOTAL_LAYERS:raise ValueError('Total layer count exceeds memory budget')
  last=None
  for layer in layers:
   if not isinstance(layer,list) or len(layer)!=2 or not all(integer(v) for v in layer):raise ValueError('Invalid layer')
   y,id=layer
   if abs(y)>30000000 or id<0 or str(id) not in states or (last is not None and y>=last):raise ValueError('Invalid/unsorted layer height or unknown state ID')
   last=y
  if biome is not None and (not integer(biome) or biome<0):raise ValueError('Invalid biome ID')
  if ground is not None and (not integer(ground) or abs(ground)>30000000):raise ValueError('Invalid ground height')
  if layers and ground is not None and ground>layers[0][0]:raise ValueError('Natural ground cannot exceed visible surface')
  if not layers and (biome is not None or ground is not None):raise ValueError('Empty column cannot have a surface biome/ground')
 status=j.get('columnStatus',[])
 if not isinstance(status,list) or len(status)>w*h:raise ValueError('Invalid columnStatus')
 status_seen=set();cell_layers={(c[0],c[1]):c[2] for c in cells}
 for item in status:
  if not isinstance(item,list) or len(item) not in (3,4):raise ValueError('Invalid columnStatus entry')
  x,z,kind=item[:3]
  if not integer(x) or not integer(z) or not(x0<=x<=x1 and z0<=z<=z1) or (x,z) in status_seen or kind not in ('unknown','partial','empty','surface'):raise ValueError('Invalid columnStatus coordinate/status')
  if kind in ('unknown','empty') and cell_layers.get((x,z)):raise ValueError('Unknown/empty status contradicts surface layers')
  status_seen.add((x,z))
 registry=j.get('biomeRegistry',{})
 if not isinstance(registry,dict) or any(not isinstance(v,dict) or (v.get('name') is not None and not isinstance(v['name'],str)) for v in registry.values()):raise ValueError('Invalid biome registry')
 for f in (assets/f'blocksStates/{version}.json',assets/f'textures/{version}.png'):
  if not f.is_file():raise ValueError(f'Missing version-matched asset: {f.name}')
 output_paths(args.output, protected_files=[source], protected_dirs=[assets])
 return j,assets

def largest_region_anchor(a,id):
 """Four-neighbor components and boundary-distance anchor, no SciPy dependency."""
 mask=a==id;seen=np.zeros(a.shape,bool);height,width=a.shape;largest=[]
 for z,x in zip(*np.where(mask)):
  if seen[z,x]:continue
  queue=deque([(int(z),int(x))]);seen[z,x]=True;region=[]
  while queue:
   zz,xx=queue.popleft();region.append((zz,xx))
   for nz,nx in ((zz-1,xx),(zz+1,xx),(zz,xx-1),(zz,xx+1)):
    if 0<=nz<height and 0<=nx<width and mask[nz,nx] and not seen[nz,nx]:seen[nz,nx]=True;queue.append((nz,nx))
  if len(region)>len(largest):largest=region
 if not largest:return [],(0,0)
 points=set(largest);dist={};queue=deque()
 for z,x in largest:
  if any(p not in points for p in ((z-1,x),(z+1,x),(z,x-1),(z,x+1))):dist[z,x]=0;queue.append((z,x))
 while queue:
  z,x=queue.popleft()
  for p in ((z-1,x),(z+1,x),(z,x-1),(z,x+1)):
   if p in points and p not in dist:dist[p]=dist[z,x]+1;queue.append(p)
 return largest,max(largest,key=lambda p:dist[p])

def make_column_renderer(tiles,capacity=COLUMN_CACHE_SIZE):
 """Cache at most 4096 composed 16x16 tiles, independent of map size."""
 if not integer(capacity) or not 0<=capacity<=COLUMN_CACHE_SIZE:raise ValueError('Invalid column cache capacity')
 @lru_cache(maxsize=capacity)
 def compose(key):
  im=Image.new('RGBA',(16,16),(204,210,211,255))
  for id in reversed(key):im=Image.alpha_composite(im,tiles[str(id)])
  return im.convert('RGB')
 return compose

def main():
 p=argparse.ArgumentParser(description='Render native cached surfaces without a live game connection')
 p.add_argument('--input',required=True,type=Path);p.add_argument('--assets',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
 p.add_argument('--version');p.add_argument('--scale',type=int,choices=range(1,17),default=8)
 p.add_argument('--mode',choices=['texture','hillshade'],default='texture');p.add_argument('--biomes',action='store_true');p.add_argument('--font',type=Path);p.add_argument('--label-language',choices=['en','zh'],default='en');p.add_argument('--presentation',action='store_true',help='Draw biome names; default puts spatial labels in the JSON sidecar')
 args=p.parse_args();j,assets=validate(args)
 X0,X1,Z0,Z1=[j[k] for k in ['X0','X1','Z0','Z1']];shape=(Z1-Z0+1,X1-X0+1)
 models=json.loads((assets/f'blocksStates/{args.version}.json').read_text())
 if not isinstance(models,dict):raise ValueError('Block-state model document must be an object')
 atlas=Image.open(assets/f'textures/{args.version}.png').convert('RGBA');aw,ah=atlas.size;t0=time.perf_counter()
 def matches(cond,props):
  if 'OR' in cond:return any(matches(v,props) for v in cond['OR'])
  if 'AND' in cond:return all(matches(v,props) for v in cond['AND'])
  return all(str(props.get(k,'')).lower() in str(v).lower().split('|') for k,v in cond.items())
 def entries(name,props):
  m=models.get(name,{})
  if 'variants' in m:
   for key,choice in m['variants'].items():
    cond=dict(part.split('=',1) for part in key.split(',') if '=' in part)
    if matches(cond,props):return [choice[0] if isinstance(choice,list) else choice]
  if 'multipart' in m:
   return [c['apply'][0] if isinstance(c['apply'],list) else c['apply'] for c in m['multipart'] if matches(c.get('when',{}),props)]
  return []
 def tex(t,tint=None):
  if not isinstance(t,dict) or not all(isinstance(t.get(k),(int,float)) and math.isfinite(t[k]) for k in ('u','v','su','sv')):raise ValueError('Missing/invalid model texture coordinates')
  u,v,su,sv=[t[k] for k in ('u','v','su','sv')];box=(round(min(u,u+su)*aw),round(min(v,v+sv)*ah),round(max(u,u+su)*aw),round(max(v,v+sv)*ah));im=atlas.crop(box)
  if not im.width or not im.height or min(box)<0 or box[2]>aw or box[3]>ah:raise ValueError('Invalid or out-of-atlas texture UV')
  if su<0:im=im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
  if sv<0:im=im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
  if tint:
   a=np.array(im);a[:,:,:3]=(a[:,:,:3].astype(float)*np.array(tint)/255).astype('uint8');im=Image.fromarray(a)
  return im
 fixed_green=(125,175,75)
 # All faces represented by their corners in outward-view texture orientation.
 faces={'up':([0,1,0],lambda f,t:[[f[0],t[1],f[2]],[t[0],t[1],f[2]],[t[0],t[1],t[2]],[f[0],t[1],t[2]]]),'down':([0,-1,0],lambda f,t:[[f[0],f[1],t[2]],[t[0],f[1],t[2]],[t[0],f[1],f[2]],[f[0],f[1],f[2]]]),'north':([0,0,-1],lambda f,t:[[t[0],t[1],f[2]],[f[0],t[1],f[2]],[f[0],f[1],f[2]],[t[0],f[1],f[2]]]),'south':([0,0,1],lambda f,t:[[f[0],t[1],t[2]],[t[0],t[1],t[2]],[t[0],f[1],t[2]],[f[0],f[1],t[2]]]),'west':([-1,0,0],lambda f,t:[[f[0],t[1],f[2]],[f[0],t[1],t[2]],[f[0],f[1],t[2]],[f[0],f[1],f[2]]]),'east':([1,0,0],lambda f,t:[[t[0],t[1],t[2]],[t[0],t[1],f[2]],[t[0],f[1],f[2]],[t[0],f[1],t[2]]])}
 def rot(x,y):
  x,y=math.radians(x),math.radians(y);rx=np.array([[1,0,0],[0,math.cos(x),-math.sin(x)],[0,math.sin(x),math.cos(x)]]);ry=np.array([[math.cos(y),0,math.sin(y)],[0,1,0],[-math.sin(y),0,math.cos(y)]]);return ry@rx
 fallbacks=Counter();symbols=Counter()
 def tile(state):
  name=state['name'];props=state['properties'];result=Image.new('RGBA',(16,16));elements=[];es=entries(name,props)
  if name in ('water','bubble_column','kelp','kelp_plant','seagrass','tall_seagrass'):
   im=Image.open(assets/f'textures/{args.version}/blocks/water_still.png').convert('RGBA').crop((0,0,16,16));a=np.array(im);a[:,:,:3]=(a[:,:,:3].astype(float)*np.array([65,115,205])/255).astype('uint8');a[:,:,3]=255;return Image.fromarray(a)
  for entry in es:
   model=entry.get('model',{});r=rot(entry.get('x',0),entry.get('y',0))
   for e in model.get('elements',[]):
    for face,v in e.get('faces',{}).items():
     if face not in faces:continue
     normal,fn=faces[face]
     if (r@normal)[1]<.99:continue
     corners=(np.array(fn(e['from'],e['to']))-8)@r.T+8
     if e.get('rotation'):continue # slanted geometry uses map-symbol fallback
     elements.append((float(corners[:,1].mean()),corners[:,[0,2]],v))
  for _,q,v in sorted(elements,key=lambda e:e[0]):
   tinted=v.get('tintindex') is not None; tint=fixed_green if tinted and name in ('grass_block','oak_leaves','birch_leaves','spruce_leaves','jungle_leaves','acacia_leaves','dark_oak_leaves','mangrove_leaves','vine','short_grass','tall_grass','fern','large_fern') else None
   if name=='birch_leaves':tint=(128,167,85)
   if name=='spruce_leaves':tint=(97,153,97)
   texture=tex(v['texture'],tint)
   if v.get('rotation'):texture=texture.rotate(-v['rotation'])
   # Affine maps projected x/z coordinates to texture coordinates.
   src=np.array([[q[0,0],q[0,1],1],[q[1,0],q[1,1],1],[q[3,0],q[3,1],1]])
   try:
    cu=np.linalg.solve(src,[0,texture.width,0]);cv=np.linalg.solve(src,[0,0,texture.height])
   except np.linalg.LinAlgError:continue
   layer=texture.transform((16,16),Image.Transform.AFFINE,tuple(cu)+tuple(cv),resample=Image.Resampling.NEAREST)
   result=Image.alpha_composite(result,layer)
  if not elements:
   ts=es[0].get('model',{}).get('textures',{}) if es else {}
   key=next((k for k in ['cross','crop','texture','plant','all','top','up','particle'] if k in ts),None)
   if key:
    tint=fixed_green if name in ('short_grass','tall_grass','fern','large_fern','vine','sugar_cane') else None
    result=tex(ts[key],tint).resize((16,16),Image.Resampling.NEAREST);symbols[name]+=1
   else:
    fp=assets/f'textures/{args.version}/blocks/{name}.png'
    if fp.exists():result=Image.open(fp).convert('RGBA').crop((0,0,16,16));symbols[name]+=1
    else:raise ValueError(f'Missing material/model texture for {name}; refusing to fabricate color')
  return result
 used={str(layer[1]) for _,_,layers,*extra in j['cells'] for layer in layers};tiles={id:tile(j['states'][id]) for id in used}
 h=np.full(shape,np.nan);cols={};counts=Counter()
 for x,z,layers,*extra in j['cells']:
  if layers:h[z-Z0,x-X0]=layers[0][0];counts[j['states'][str(layers[0][1])]['name']]+=1
  cols[x,z]=layers
 # Soft hillshade modifies luminance only; unknown neighbors never act as zero-height terrain.
 def deriv(a,axis):
  a=np.moveaxis(a,axis,0);d=np.zeros_like(a);n=np.zeros_like(a);delta=a[1:]-a[:-1];ok=np.isfinite(delta);delta=np.where(ok,delta,0);d[:-1]+=delta;d[1:]+=delta;n[:-1]+=ok;n[1:]+=ok;return np.moveaxis(d/np.maximum(n,1),0,axis)
 dx,dz=deriv(h,1),deriv(h,0);light=(.5*dx+.5*dz+2**-.5)/np.sqrt(dx*dx+dz*dz+1);shade=.79+.24*np.clip(light,0,1)
 compose_column=make_column_renderer(tiles)
 def column(layers):
  return compose_column(tuple(id for y,id in layers))
 def render(bounds,scale):
  x0,x1,z0,z1=bounds;result=Image.new('RGB',((x1-x0+1)*scale,(z1-z0+1)*scale),(204,210,211))
  for z in range(z0,z1+1):
   for x in range(x0,x1+1):
    layers=cols.get((x,z),[])
    if not layers:continue
    im=column(layers);a=np.array(im).astype(float);s=shade[z-Z0,x-X0]
    if j['states'][str(layers[0][1])]['name'] in ('water','kelp','seagrass','tall_seagrass'):s=1
    im=Image.fromarray(np.uint8(np.clip(a*s,0,255)))
    if scale!=16:im=im.resize((scale,scale),Image.Resampling.BOX)
    result.paste(im,((x-x0)*scale,(z-z0)*scale))
  return result

 main=render((X0,X1,Z0,Z1),args.scale)
 if args.mode=='hillshade':
  ground=np.full(shape,np.nan);water=np.zeros(shape,bool)
  for x,z,layers,*extra in j['cells']:
   if len(extra)>1 and extra[1] is not None:ground[z-Z0,x-X0]=extra[1]
   if layers and j['states'][str(layers[0][1])]['name'] in ('water','kelp','seagrass','tall_seagrass'):
    water[z-Z0,x-X0]=True;ground[z-Z0,x-X0]=layers[0][0]
  gx,gz=deriv(ground,1),deriv(ground,0);li=(.5*gx+.5*gz+2**-.5)/np.sqrt(gx*gx+gz*gz+1)
  levels=[55,62,78,96,120,145,180,260,320];colors=np.array([[91,130,86],[107,148,93],[140,163,100],[182,177,129],[193,176,151],[219,210,192],[239,237,219],[247,245,237],[255,255,255]])
  rgb=np.stack([np.interp(np.nan_to_num(ground,nan=62),levels,colors[:,i]) for i in range(3)],axis=-1)*(.56+.44*np.clip(li,0,1))[:,:,None]
  rgb[water]=[73,136,173];rgb[~np.isfinite(ground)]=[204,210,211]
  main=Image.fromarray(np.uint8(rgb)).resize(main.size,Image.Resampling.NEAREST)
 S=args.scale
 biome_regions=[]
 if args.biomes:
  a=np.full(shape,-1,int)
  for x,z,layers,*extra in j['cells']:
   if extra and extra[0] is not None:a[z-Z0,x-X0]=extra[0]
  d=ImageDraw.Draw(main)
  for z,x in zip(*np.where((a[:,1:]!=a[:,:-1])&(a[:,1:]>=0)&(a[:,:-1]>=0))):
   d.line(((x+1)*S,z*S,(x+1)*S,(z+1)*S),fill='#182c33',width=3);d.line(((x+1)*S,z*S,(x+1)*S,(z+1)*S),fill='#e9dfb1',width=1)
  for z,x in zip(*np.where((a[1:,:]!=a[:-1,:])&(a[1:,:]>=0)&(a[:-1,:]>=0))):
   d.line((x*S,(z+1)*S,(x+1)*S,(z+1)*S),fill='#182c33',width=3);d.line((x*S,(z+1)*S,(x+1)*S,(z+1)*S),fill='#e9dfb1',width=1)
  font=ImageFont.truetype(str(args.font) if args.font else 'DejaVuSans.ttf',max(10,3*S)) if args.presentation else None
  placed=[]
  for id in sorted(set(a.ravel())-{-1}):
   region,point=largest_region_anchor(a,id)
   z,x=point
   name=j.get('biomeRegistry',{}).get(str(id),{}).get('name') or f'biome_id_{id}'
   if args.label_language=='zh':name=CHINESE.get(name,name)
   biome_regions.append({'id':int(id),'name':name,'scope':'largest connected component for this biome ID','columns':len(region),'anchorXZ':[X0+int(x),Z0+int(z)],'anchorPixel':[(int(x)+.5)*S,(int(z)+.5)*S],'centroidXZ':[X0+sum(p[1] for p in region)/len(region),Z0+sum(p[0] for p in region)/len(region)]})
   if not args.presentation or len(region)<100:continue
   w=d.textlength(name,font=font);fh=font.getbbox(name)[3]+4
   if w+8>main.width:continue
   px=max(w/2+4,min(main.width-w/2-4,(x+.5)*S));py=max(2,min(main.height-fh-2,(z+.5)*S))
   box=(px-w/2-4,py-2,px+w/2+4,py+fh+2)
   if any(not(box[2]<q[0] or box[0]>q[2] or box[3]<q[1] or box[1]>q[3]) for q in placed):continue
   d.text((px-w/2,py),name,font=font,fill='#fff5cf',stroke_width=2,stroke_fill='#182c33');placed.append(box)
 source={k:j.get(k) for k in ('worldId','dimension','version','y','chunks','biomeRegistry','maxLayers')}
 source['columnCounts']=dict(Counter(c[2] for c in j.get('columnStatus') or []))
 if not source['columnCounts']:
  known=sum(bool(c[2]) for c in j['cells']);source['columnCounts']={'surface':known,'empty':len(j['cells'])-known,'partial':0,'unknown':shape[0]*shape[1]-len(j['cells'])}
 source['columnDetails']='Read cells and columnStatus in the referenced snapshot for exact per-column data'
 source['snapshot']=str(args.input)
 source['snapshotSha256']=hashlib.sha256(args.input.read_bytes()).hexdigest()
 source['boundsXZ']=[X0,X1,Z0,Z1]
 source['unknownIsAir']=False
 render={'mode':args.mode,'pixels':list(main.size),'pixelsPerBlock':S,'orientation':'north=-Z (up), east=+X (right)',
         'lighting':'display hillshade','biomeSampling':j.get('biomeSampling'),'biomeBoundaries':args.biomes,
         'biomeRegions':biome_regions,'grid':False,'unknownColor':[204,210,211],
         'description':'Highest visible cached surfaces; grey marks unknown or empty columns, exact column status is in the referenced snapshot.',
         'textureApproximation':'Static real textures, fixed vegetation/water tint, top-face projection and plant symbols',
         'unresolvedMaterials':dict(fallbacks),'modelSymbolTypes':dict(symbols)}
 result=write_pair(main,args.output,image_metadata('surface',args.presentation,source,render),protected_files=[args.input],protected_dirs=[assets])
 print(json.dumps({**result,'seconds':round(time.perf_counter()-t0,3),'unresolved_materials':dict(fallbacks),'surface_y_biomes':args.biomes,'model_symbol_types':dict(symbols)}))

if __name__=='__main__':
 try:main()
 except (ValueError,OSError,KeyError,TypeError,IndexError) as exc:
  print(f'surface renderer: {exc}',file=sys.stderr);sys.exit(2)

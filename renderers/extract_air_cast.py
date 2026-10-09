#!/usr/bin/env python3
"""Extract a known-air connected component from an offline palette region. No game I/O."""
import argparse,collections,hashlib,json
from pathlib import Path
import numpy as np

MAX_JSON_BYTES=64*1024*1024
MAX_SOURCE_VOXELS=8_000_000
MAX_COMPONENT_VOXELS=100_000
MAX_EXPOSED_FACES=200_000

AIR_NAMES=('air','cave_air','torch','wall_torch','glow_lichen')
DIRECTIONS=((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1))
FACE_OFFSETS=(((1,0,0),(1,1,0),(1,1,1),(1,0,1)),((0,0,1),(0,1,1),(0,1,0),(0,0,0)),((0,1,0),(0,1,1),(1,1,1),(1,1,0)),((0,0,1),(0,0,0),(1,0,0),(1,0,1)),((1,0,1),(1,1,1),(0,1,1),(0,0,1)),((0,0,0),(0,1,0),(1,1,0),(1,0,0)))
def read_json_limited(path,limit=None):
    path=Path(path);limit=MAX_JSON_BYTES if limit is None else limit
    if path.stat().st_size>limit:raise ValueError(f'JSON input exceeds byte budget of {limit}: {path.name}')
    return json.loads(path.read_text())

def load_air_cast(directory):
    directory=Path(directory);geometry=read_json_limited(directory/'geometry.json');metadata=read_json_limited(directory/'metadata.json')
    if not isinstance(geometry,dict) or geometry.get('schema')!='world-memory.air-cast.v1':raise ValueError('Unsupported air-cast schema')
    if not isinstance(metadata,dict) or metadata.get('schema')!='world-memory.air-cast.metadata.v1':raise ValueError('Unsupported air-cast metadata schema')
    for key,limit in [('vertices',MAX_EXPOSED_FACES*4),('faces',MAX_EXPOSED_FACES),('voxels',MAX_COMPONENT_VOXELS)]:
        value=geometry.get(key)
        if not isinstance(value,list) or not 1<=len(value)<=limit:raise ValueError(f'Geometry {key} exceeds producer budget or is empty')
    if not isinstance(geometry.get('face_classes'),list) or len(geometry['face_classes'])!=len(geometry['faces']):raise ValueError('Invalid face classifications')
    if type(metadata.get('voxel_count')) is not int or metadata['voxel_count']!=len(geometry['voxels']):raise ValueError('Voxel count mismatch')
    for key in ('cropped_faces','unknown_boundary_faces'):
        if not isinstance(metadata.get(key),list) or len(metadata[key])>MAX_EXPOSED_FACES:raise ValueError('Boundary metadata exceeds producer budget')
    return geometry,metadata

def triplet(value, label):
    if not isinstance(value,(list,tuple)) or len(value)!=3 or any(type(v) is not int for v in value):
        raise ValueError(label+' must be exactly three integers')
    return list(value)

def ensure_output_paths(directory,names,source=None):
    directory=Path(directory).resolve()
    if source:
        source=Path(source).resolve()
        if directory==source or directory in source.parents or source in directory.parents:
            raise ValueError('Output must be separate from source region/cache, not a parent or child')
    for name in names:
        target=directory/name
        if target.exists() or target.is_symlink():raise ValueError('Refusing to overwrite existing output: '+str(target))
    return directory

def read_region(region_dir):
    region_dir=Path(region_dir).resolve();m=read_json_limited(region_dir/'region.json')
    if not isinstance(m,dict) or m.get('schema')!='world-memory.region.v1':raise ValueError('Unsupported region schema')
    lo=triplet(m.get('lo'),'lo');hi=triplet(m.get('hi'),'hi');shape=triplet(m.get('shape'),'shape')
    if any(h<l or n!=h-l+1 for l,h,n in zip(lo,hi,shape)):raise ValueError('Region bounds/shape mismatch')
    count=int(np.prod(shape,dtype=object))
    if count>MAX_SOURCE_VOXELS:raise ValueError(f'Region exceeds source budget of {MAX_SOURCE_VOXELS} voxels')
    pal=m.get('palette')
    if not isinstance(pal,list) or not 1<=len(pal)<=65536 or any(not isinstance(n,str) or not n or n.strip()!=n for n in pal) or len(set(pal))!=len(pal):raise ValueError('Palette must contain unique nonempty names')
    if pal[0]!='unknown':raise ValueError('Palette index zero must be unknown')
    for key in ('worldId','dimension','version'):
        if not isinstance(m.get(key),str) or not m[key]:raise ValueError(key+' must be a nonempty string')
    import math,re
    stamps=m.get('stamps')
    if not isinstance(stamps,(list,dict)):raise ValueError('stamps must be an array or legacy object')
    for stamp in (stamps if isinstance(stamps,list) else stamps.values()):
        if not isinstance(stamp,dict):raise ValueError('Invalid chunk stamp')
        if isinstance(stamps,list) and any(type(stamp.get(k)) is not int for k in ('x','z')):raise ValueError('Chunk stamp x/z must be integers')
        timestamp=stamp.get('observedAt')
        if timestamp is not None and (type(timestamp) not in (int,float) or not math.isfinite(timestamp) or timestamp<0):raise ValueError('Invalid observation timestamp')
        if not isinstance(stamp.get('sha256'),str) or not re.fullmatch(r'[a-f0-9]{64}',stamp['sha256']):raise ValueError('Invalid chunk content hash')
    if type(m.get('unknownVoxels')) is not int or m['unknownVoxels']<0:raise ValueError('unknownVoxels must be a nonnegative integer')
    if m.get('unknownIsAir',False) is not False:raise ValueError('Unknown cannot be air')
    path=region_dir/'region.u16'
    if path.stat().st_size!=count*2:raise ValueError('Binary size does not match shape')
    raw=path.read_bytes();a=np.frombuffer(raw,dtype='<u2').reshape(shape)
    if int(a.max())>=len(pal):raise ValueError('Palette index out of bounds')
    if int(np.count_nonzero(a==0))!=m['unknownVoxels']:raise ValueError('unknownVoxels does not match binary')
    states=region_dir/'states.i32'
    if states.exists():
        if states.stat().st_size!=count*4:raise ValueError('Native state binary size mismatch')
        state=np.frombuffer(states.read_bytes(),dtype='<i4').reshape(shape)
        if np.any(state < -1) or not np.array_equal(state==-1,a==0):raise ValueError('Native known/unknown states disagree with palette volume')
    return m,a,raw

def render_settings(config,bounds):
    import math
    if not isinstance(config,dict):raise ValueError('Render config must be an object')
    def number(v,label,low=None,high=None):
        if type(v) not in (float,int) or not math.isfinite(v) or (low is not None and v<low) or (high is not None and v>high):raise ValueError('Invalid '+label)
        return v
    def vector(value,label):
        if not isinstance(value,(list,tuple)) or len(value)!=3:raise ValueError('Invalid '+label)
        for v in value:number(v,label)
    if 'center_blender' in config:vector(config['center_blender'],'center_blender')
    if 'lights_blender' in config:
        lights=config['lights_blender']
        if not isinstance(lights,list) or not 1<=len(lights)<=16:raise ValueError('Invalid world light rig')
        for light in lights:
            if not isinstance(light,list) or len(light)!=4 or not isinstance(light[0],str):raise ValueError('Invalid world light')
            vector(light[1],'light position');number(light[2],'light power',0);number(light[3],'light size',.001)
    if 'annotation_points_blender' in config:
        points=config['annotation_points_blender']
        if not isinstance(points,dict) or len(points)>256:raise ValueError('Annotation points must be an object with at most 256 points')
        for label,point in points.items():
            if not isinstance(label,str) or len(label)>32:raise ValueError('Invalid annotation label')
            vector(point,'annotation point')
    az=config.get('azimuth_degrees',35)
    if 'azimuths_degrees' in config:
        pair=config['azimuths_degrees']
        if not isinstance(pair,list) or len(pair)!=2:raise ValueError('Exactly two azimuths required')
        number(pair[0],'azimuth');number(pair[1],'azimuth')
        if abs(((pair[1]-pair[0])%360)-180)>1e-8:raise ValueError('Camera azimuths must differ by exactly 180 degrees')
        az=pair[0]
    number(az,'azimuth');el=number(config.get('elevation_degrees',33),'elevation',1,89)
    span=max(bounds[1][i]-bounds[0][i] for i in range(3))
    resolution=config.get('resolution',[1300,1050])
    if not isinstance(resolution,list) or len(resolution)!=2 or any(type(v) is not int or not 64<=v<=8192 for v in resolution) or resolution[0]*resolution[1]>16_000_000:raise ValueError('Invalid resolution or image exceeds 16 million pixel budget')
    if type(config.get('grid',True)) is not bool:raise ValueError('grid must be boolean')
    samples=config.get('samples',40)
    if type(samples) is not int or not 1<=samples<=4096:raise ValueError('Invalid samples')
    number(config.get('grid_radius_blocks',.012),'grid_radius_blocks',.001,.08);number(config.get('bevel_blocks',.025),'bevel_blocks',0,.1)
    # Horizontal sensor fit makes scale a world-space width regardless of aspect ratio.
    # Fit both views, including projected XYZ ruler bounds; use one shared scale.
    import itertools
    center=np.array(config.get('center_blender',[(bounds[0][i]+bounds[1][i])/2 for i in range(3)]),dtype=float)
    corners=np.array(list(itertools.product(*[(bounds[0][i]-1.2,bounds[1][i]+1.2) for i in range(3)])),dtype=float)
    relative=corners-center;required=0.;aspect=resolution[0]/resolution[1]
    for angle in (az,az+180):
        ar=math.radians(angle);er=math.radians(el);forward=-np.array([math.cos(er)*math.cos(ar),math.cos(er)*math.sin(ar),math.sin(er)])
        right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right);up=np.cross(right,forward)
        required=max(required,2*float(np.max(np.abs(relative@right))),2*float(np.max(np.abs(relative@up)))*aspect)
    default_scale=max(.1,required*1.12);scale=number(config.get('orthographic_scale',default_scale),'orthographic_scale',.1)
    radius=float(np.max(np.linalg.norm(relative,axis=1)));distance=max(radius*3,70);clip_start=max(.01,(distance-radius)/10);clip_end=distance+radius+10
    return {'azimuths_degrees':[az%360,(az+180)%360],'elevation_degrees':el,'orthographic_scale':scale,'resolution':resolution,'grid':config.get('grid',True),'samples':samples,'world_light_fixed':True,'grid_pitch_blocks':1,'default_fit_scale':default_scale,'manual_scale_may_clip':'orthographic_scale' in config,'camera_distance':distance,'bounding_radius':radius,'clip_start':clip_start,'clip_end':clip_end,'sensor_fit':'HORIZONTAL'}

def extract(region_dir,out_dir,minimum,maximum,seed):
    region_dir=Path(region_dir).resolve();out_dir=ensure_output_paths(out_dir,['geometry.json','metadata.json'],region_dir)
    m,a,raw=read_region(region_dir);pal=m['palette'];lo=np.array(m['lo']);hi=np.array(m['hi']);mn=np.array(triplet(minimum,'minimum'));mx=np.array(triplet(maximum,'maximum'));seed=tuple(triplet(seed,'seed'))
    if np.any(mn>mx) or np.any(mn<lo) or np.any(mx>hi):raise ValueError('ROI must fit decoded region; unknown/out-of-range is never inferred as air.')
    ids={pal.index(n) for n in AIR_NAMES if n in pal}
    def inside(p,l=mn,h=mx):return all(l[i]<=p[i]<=h[i] for i in range(3))
    def block_id(p):return int(a[tuple(np.array(p)-lo)]) if inside(p,lo,hi) else 0
    def is_air(p):return block_id(p) in ids
    if not inside(seed) or not is_air(seed):raise ValueError('Seed must be a known empty cell inside ROI.')
    seen={seed};q=collections.deque([seed])
    while q:
        p=q.popleft()
        for d in DIRECTIONS:
            t=tuple(v+dv for v,dv in zip(p,d))
            if t not in seen and inside(t) and is_air(t):
                if len(seen)>=MAX_COMPONENT_VOXELS:raise ValueError(f'Air component exceeds budget of {MAX_COMPONENT_VOXELS} voxels; choose a smaller ROI')
                seen.add(t);q.append(t)
    verts=[];faces=[];face_classes=[];cropped=[];unknown_faces=[]
    for p in sorted(seen):
        for d,offs in zip(DIRECTIONS,FACE_OFFSETS):
            neighbor=tuple(v+dv for v,dv in zip(p,d))
            if neighbor in seen:continue
            if len(faces)>=MAX_EXPOSED_FACES:raise ValueError(f'Air surface exceeds budget of {MAX_EXPOSED_FACES} exposed faces; choose a smaller ROI')
            category='unknown_boundary' if block_id(neighbor)==0 else ('crop_known_air' if not inside(neighbor) and is_air(neighbor) else 'known_solid_boundary')
            face=[]
            for off in offs:
                x,y,z=(v+dv for v,dv in zip(p,off));face.append(len(verts));verts.append([x,-z,y])
            face_index=len(faces);faces.append(face);face_classes.append(category)
            info={'face_index':face_index,'voxel_xyz':p,'direction_xyz':d,'kind':category,'known_air_continues':bool(is_air(neighbor)),'neighbor_in_source':bool(inside(neighbor,lo,hi))}
            if not inside(neighbor):cropped.append(info)
            if category=='unknown_boundary':unknown_faces.append(info)
    geometry={'schema':'world-memory.air-cast.v1','vertices':verts,'faces':faces,'face_classes':face_classes,'voxels':sorted(seen),'coordinate_transform':'Minecraft(x,y,z) -> Blender(x,-z,y)'}
    metadata={'schema':'world-memory.air-cast.metadata.v1','bounds_xyz_inclusive':[mn.tolist(),mx.tolist()],'seed_xyz':seed,'voxel_count':len(seen),'actual_bounds_xyz':[[min(p[i] for p in seen) for i in range(3)],[max(p[i] for p in seen) for i in range(3)]],'source':str(region_dir),'source_sha256':hashlib.sha256(raw).hexdigest(),'source_chunk_stamps':m['stamps'],'worldId':m['worldId'],'dimension':m['dimension'],'version':m['version'],'air_names':AIR_NAMES,'cropped_faces':cropped,'unknown_boundary_faces':unknown_faces,'boundary_counts':dict(collections.Counter(face_classes)),'unknown_is_air':False,'extraction_limits':{'source_voxels':MAX_SOURCE_VOXELS,'component_voxels':MAX_COMPONENT_VOXELS,'exposed_faces':MAX_EXPOSED_FACES},'model':'6-connected known-air voxel cast; NOT stone, exact collision mesh, verified navigability, or measured game lighting. Stairs/slabs within-cell air omitted; small decoration cells treated as empty.'}
    geometry_text=json.dumps(geometry,separators=(',',':'));metadata_text=json.dumps(metadata,separators=(',',':'))
    if max(len(geometry_text.encode()),len(metadata_text.encode()))>MAX_JSON_BYTES:raise ValueError('Derived JSON exceeds byte budget; choose a smaller ROI')
    out_dir.mkdir(parents=True,exist_ok=True)
    with (out_dir/'geometry.json').open('x') as f:f.write(geometry_text)
    with (out_dir/'metadata.json').open('x') as f:f.write(metadata_text)
    return metadata

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--region-dir',required=True);p.add_argument('--output-dir',required=True);p.add_argument('--min',nargs=3,type=int,required=True,dest='minimum',metavar=('X','Y','Z'));p.add_argument('--max',nargs=3,type=int,required=True,dest='maximum',metavar=('X','Y','Z'));p.add_argument('--seed',nargs=3,type=int,required=True,metavar=('X','Y','Z'));a=p.parse_args();m=extract(a.region_dir,a.output_dir,a.minimum,a.maximum,a.seed);print(json.dumps({'voxel_count':m['voxel_count'],'actual_bounds_xyz':m['actual_bounds_xyz'],'output_dir':a.output_dir}))
if __name__=='__main__':main()

#!/usr/bin/env python3
"""Compose offline air-cast camera PNGs with consistent framing and metadata."""
import argparse,json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from extract_air_cast import ensure_output_paths,render_settings,read_json_limited

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workdir',required=True);p.add_argument('--output');p.add_argument('--font',help='TTF/OTF/TTC path; optional');p.add_argument('--title',default='UNDERGROUND | ONE-BLOCK GRID AIR CAST');args=p.parse_args();d=Path(args.workdir)
    m=read_json_limited(d/'metadata.json');r=read_json_limited(d/'render-metadata.json');angles=r['azimuths_degrees'];render_settings({'azimuths_degrees':angles,'elevation_degrees':r['elevation_degrees']},[[0,0,0],[1,1,1]]);raw=[Image.open(d/f'view-{i+1}-raw.png').convert('RGBA') for i in range(len(angles))]
    width,height=raw[0].size
    if any(im.size!=(width,height) for im in raw):raise ValueError('Views must have the same resolution and scale.')
    f=lambda size: ImageFont.truetype(args.font or 'DejaVuSans.ttf',size)
    W=max(width+80,1050);ox=(W-width)//2;panelh=height+240;H=220+panelh*len(raw)+180;out=Image.new('RGB',(W,H),'#101821');dr=ImageDraw.Draw(out)
    dr.text((30,24),args.title,font=f(30),fill='white');dr.text((30,74),'White = known empty volume, not stone. Grid pitch = 1 block.',font=f(22),fill='#c1cbd3')
    dr.text((30,116),f"Elevation {r['elevation_degrees']} deg | East=0, North=90 | fixed world light",font=f(21),fill='#a1afbd')
    dr.text((30,151),'Gold cap = known air continues beyond crop | Purple = unknown beyond observed air',font=f(20),fill='#d5c5e0')
    if r.get('manual_scale_may_clip'):dr.text((30,177),'Manual scale may clip geometry; default camera scale fits both views.',font=f(17),fill='#e0c590')
    for i,im in enumerate(raw):
        y=205+i*panelh;dr.rounded_rectangle((20,y,W-20,y+panelh-15),radius=12,fill='#1b2631');dr.text((40,y+17),f'{i+1:02}   Azimuth {angles[i]} deg',font=f(24),fill='white');out.paste(im,(ox,y+55),im)
        points=read_json_limited(d/f'view-{i+1}-labels.json')
        for label,(x,z) in points.items():
            x+=ox;z+=y+55;dr.ellipse((x-14,z-14,x+14,z+14),fill='#e8edf1');dr.text((x-7,z-12),label,font=f(17),fill='#101821')
        # XYZ rulers are projected from actual world coordinates, not decorative grid counts.
        axes=read_json_limited(d/f'view-{i+1}-axes.json')
        for axis in axes:
            points=[(tick['screen'][0]+ox,tick['screen'][1]+y+55) for tick in axis['ticks']]
            dr.line(points,fill='#8eacbe',width=1)
            for tick,(tx,ty) in zip(axis['ticks'],points):
                dr.line((tx-3,ty-3,tx+3,ty+3),fill='#bccfda',width=1)
                dr.text((tx+4,ty+3),f"{axis['axis']}{tick['value']}",font=f(13),fill='#c8d8e2')
        counts=m['boundary_counts'];dr.text((40,y+panelh-40),f"Crop continuation faces: {counts.get('crop_known_air',0)} | Unknown-facing: {counts.get('unknown_boundary',0)}",font=f(18),fill='#b4c2cb')
    y=215+len(raw)*panelh;mn,mx=m['bounds_xyz_inclusive'];dr.text((30,y),f'ROI block XYZ: {mn} to {mx} | {m["voxel_count"]} connected known-air cells',font=f(20),fill='#c1cbd3')
    dr.text((30,y+38),'ROI boundary closures are cropping caps, not tunnel ends. Unknown is never air.',font=f(20),fill='#c1cbd3')
    dr.text((30,y+76),'Not exact stair/slab clearance, verified navigability, or measured game brightness.',font=f(20),fill='#c1cbd3')
    target=Path(args.output) if args.output else d/'air-cast-comparison.png';ensure_output_paths(target.parent,[target.name],m['source']);target.parent.mkdir(parents=True,exist_ok=True);out.save(target);print(target)
if __name__=='__main__':main()

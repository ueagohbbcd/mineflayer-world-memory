"""Offline CPU rasterizer for local-mesh.v1 (optional NumPy and Pillow).
No network/game access. Asset files are external, read-only, and hash checked.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin


def safe_output(output, sources):
    output = Path(output)
    resolved = output.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a new PNG')
    for source in sources:
        source = Path(source).resolve()
        if resolved == source or source in resolved.parents:
            raise ValueError('Output must be outside source input/assets directories')
    return resolved


def load_mesh(directory, assets=None):
    directory = Path(directory).resolve(strict=True)
    for name in ('mesh.json', 'metadata.json'):
        p = directory / name
        if p.is_symlink() or p.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Unsafe mesh input')
    g = json.loads((directory / 'mesh.json').read_text())
    m = json.loads((directory / 'metadata.json').read_text())
    if g.get('schema') != 'world-memory.local-mesh.v1' or m.get('schema') != 'world-memory.local-mesh-metadata.v1':
        raise ValueError('Unsupported mesh schema')
    version = m['version']
    if not isinstance(version, str) or not all(v.isdigit() for v in version.split('.')) or len(version.split('.')) not in (2, 3):
        raise ValueError('Invalid Minecraft version')
    assets = Path(assets or m['assets']).resolve(strict=True)
    atlas = assets / 'textures' / (version + '.png')
    if hashlib.sha256(atlas.read_bytes()).hexdigest() != m['atlasSha256']:
        raise ValueError('Texture atlas does not match mesh asset hash')
    with Image.open(atlas) as im:
        if im.width * im.height > 64 * 1024 * 1024:
            raise ValueError('Atlas is too large')
        texture = np.asarray(im.convert('RGBA'))
    for name, limit in [('positions', 786432), ('normals', 786432), ('colors', 786432), ('uvs', 524288), ('indices', 786432)]:
        if not isinstance(g.get(name), list) or len(g[name]) > limit:
            raise ValueError('Mesh geometry budget exceeded')
    arrays = {}
    for name, columns in [('positions', 3), ('normals', 3), ('colors', 3), ('uvs', 2)]:
        a = np.asarray(g[name], dtype=float).reshape(-1, columns)
        if not np.isfinite(a).all():
            raise ValueError('Nonfinite mesh values')
        arrays[name] = a
    n = len(arrays['positions'])
    if n > 262_144 or any(len(a) != n for a in arrays.values()):
        raise ValueError('Invalid mesh attribute lengths')
    indices = np.asarray(g['indices'])
    if indices.size and (not np.issubdtype(indices.dtype, np.integer) or indices.min() < 0 or indices.max() >= n):
        raise ValueError('Invalid mesh indices')
    arrays['indices'] = indices.astype(np.int64).reshape(-1, 3)
    return arrays, m, texture, assets


def render(mesh, texture, lo, hi, *, eye=None, target=None, width=960, height=720, scale=1, fov=40):
    if not all(isinstance(v, int) and 64 <= v <= 4096 for v in (width, height)) or scale not in (1, 2, 3) or width * height * scale ** 2 > 4_000_000:
        raise ValueError('Image dimensions/scale exceed limits')
    if not math.isfinite(fov) or not 10 <= fov <= 120:
        raise ValueError('FOV must be 10..120 degrees')
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    if lo.shape != (3,) or hi.shape != (3,) or not np.isfinite([lo, hi]).all() or np.any(hi < lo):
        raise ValueError('Invalid mesh bounds')
    center = (lo + hi + 1) / 2
    radius = max(1., np.linalg.norm(hi + 1 - lo) / 2)
    target = np.asarray(center if target is None else target, dtype=float)
    direction = np.array([1., .8, 1.]); direction /= np.linalg.norm(direction)
    half_angle = min(math.radians(fov) / 2, math.atan(math.tan(math.radians(fov) / 2) * width / height))
    eye = np.asarray(center + direction * radius / math.sin(half_angle) * 1.2 if eye is None else eye, dtype=float)
    if eye.shape != (3,) or target.shape != (3,) or not np.isfinite([eye, target]).all():
        raise ValueError('Camera must contain three finite coordinates')
    forward = target - eye
    if np.linalg.norm(forward) < 1e-8:
        raise ValueError('Camera eye and target must differ')
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 1, 0])
    if np.linalg.norm(right) < 1e-8:
        right = np.cross(forward, [0, 0, 1])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    w, h = width * scale, height * scale
    pos, norm, uv, color = [mesh[k] for k in ('positions', 'normals', 'uvs', 'colors')]
    rel = pos - eye
    camera = np.column_stack([rel @ right, rel @ up, rel @ forward])
    z = camera[:, 2]
    focal = h / 2 / math.tan(math.radians(fov) / 2)
    safe_z = np.where(np.abs(z) > 1e-12, z, 1e-12)
    screen = np.column_stack([camera[:, 0] / safe_z * focal + w / 2, h / 2 - camera[:, 1] / safe_z * focal])
    output = np.full((h, w, 3), [22, 30, 42], dtype=np.uint8)
    zbuf = np.full((h, w), np.inf)
    light = np.array([1., 1., .5]); light /= np.linalg.norm(light)
    drawn, clipped = 0, 0
    for ids in mesh['indices']:
        if np.any(z[ids] <= .05):
            clipped += 1
            continue
        a, b, c = pos[ids]
        if np.dot(np.cross(b-a, c-a), eye-(a+b+c)/3) <= 0:
            continue
        p = screen[ids]
        low = np.maximum(np.floor(p.min(0)), 0).astype(int)
        high = np.minimum(np.ceil(p.max(0)), [w-1, h-1]).astype(int)
        if np.any(high < low):
            continue
        triangle_drawn = False
        # Rasterize bounded tiles, never allocate barycentric arrays for the full
        # screen-sized bounding box of one triangle.
        for tile_y in range(low[1], high[1] + 1, 128):
            for tile_x in range(low[0], high[0] + 1, 128):
                tile_low = (tile_x, tile_y)
                tile_high = (min(tile_x + 127, high[0]), min(tile_y + 127, high[1]))
                xx, yy = np.meshgrid(np.arange(tile_low[0], tile_high[0]+1)+.5, np.arange(tile_low[1], tile_high[1]+1)+.5)
                den = (p[1, 1]-p[2, 1])*(p[0, 0]-p[2, 0])+(p[2, 0]-p[1, 0])*(p[0, 1]-p[2, 1])
                if abs(den) < 1e-12:
                    continue
                b0 = ((p[1, 1]-p[2, 1])*(xx-p[2, 0])+(p[2, 0]-p[1, 0])*(yy-p[2, 1]))/den
                b1 = ((p[2, 1]-p[0, 1])*(xx-p[2, 0])+(p[0, 0]-p[2, 0])*(yy-p[2, 1]))/den
                bary = np.stack([b0, b1, 1-b0-b1], axis=-1)
                mask = np.all(bary >= -1e-8, axis=-1)
                weights = bary / z[ids]
                total = weights.sum(axis=-1)
                depth = np.divide(1., total, out=np.full_like(total, np.inf), where=np.abs(total) > 1e-15)
                subz = zbuf[tile_low[1]:tile_high[1]+1, tile_low[0]:tile_high[0]+1]
                mask &= depth < subz
                if not mask.any():
                    continue
                weights /= np.where(np.abs(total) > 1e-15, total, 1)[..., None]
                st = weights @ uv[ids]
                tx = np.clip((st[:, :, 0]*texture.shape[1]).astype(int), 0, texture.shape[1]-1)
                ty = np.clip((st[:, :, 1]*texture.shape[0]).astype(int), 0, texture.shape[0]-1)
                rgba = texture[ty, tx]
                mask &= rgba[:, :, 3] >= 26
                lights = .78 + .22 * np.clip(norm[ids] @ light, 0, 1)
                rgb = np.clip(rgba[:, :, :3] * (weights @ (color[ids] * lights[:, None])), 0, 255).astype(np.uint8)
                sub = output[tile_low[1]:tile_high[1]+1, tile_low[0]:tile_high[0]+1]
                sub[mask] = rgb[mask]; subz[mask] = depth[mask]
                triangle_drawn = True
        if triangle_drawn:
            drawn += 1
    im = Image.fromarray(output).resize((width, height), Image.Resampling.LANCZOS)
    info = {'eye': eye.tolist(), 'target': target.tolist(), 'fov': fov, 'imageSize': [width, height], 'scale': scale,
            'rasterizedTriangles': drawn, 'nearPlaneSkippedTriangles': clipped, 'geometryPixels': int(np.isfinite(zbuf).sum()),
            'alpha': 'alpha test >= 26/255; translucent pixels are opaque, no blending',
            'nearPlane': 'Triangles intersecting near plane omitted; move camera out if nonzero'}
    return im, info


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mesh-dir', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--assets', type=Path)
    p.add_argument('--eye', nargs=3, type=float)
    p.add_argument('--target', nargs=3, type=float)
    p.add_argument('--width', type=int, default=960)
    p.add_argument('--height', type=int, default=720)
    p.add_argument('--scale', type=int, default=1)
    p.add_argument('--fov', type=float, default=40)
    a = p.parse_args(argv)
    mesh, metadata, texture, assets = load_mesh(a.mesh_dir, a.assets)
    output = safe_output(a.output, [a.mesh_dir, assets])
    im, info = render(mesh, texture, metadata['lo'], metadata['hi'], eye=a.eye, target=a.target, width=a.width, height=a.height, scale=a.scale, fov=a.fov)
    # Always visible provenance, even if the PNG metadata is stripped downstream.
    labelled = Image.new('RGB', (im.width, im.height + 66), '#101722')
    labelled.paste(im, (0, 0)); draw = ImageDraw.Draw(labelled)
    for i, line in enumerate(['ROI cut-away | outside remains unobserved in this view',
                              'Fixed display lighting / illustrative biome tint',
                              'Native cache is source of truth | Check clearance separately']):
        draw.text((8, im.height+6+i*18), line, fill='#d6dfeb')
    info['imageSize'] = list(labelled.size)
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text('world-memory', json.dumps({'source': metadata, 'render': info}))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as f:
        labelled.save(f, format='PNG', pnginfo=pnginfo)
    print(json.dumps({'output': str(output), **info}))
    return info


if __name__ == '__main__':
    main()

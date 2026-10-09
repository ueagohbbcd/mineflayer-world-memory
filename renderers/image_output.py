"""Small shared PNG/JSON delivery contract for offline views."""
import json
import hashlib
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def output_paths(output, protected_files=(), protected_dirs=()):
    output = Path(output)
    if output.suffix.lower() != '.png':
        raise ValueError('Output must have .png suffix')
    sidecar = output.with_name(output.name + '.json')
    for target in (output, sidecar):
        if target.exists() or target.is_symlink():
            raise ValueError('Output already exists: ' + str(target))
        resolved = target.resolve()
        if any(resolved == Path(source).resolve() for source in protected_files):
            raise ValueError('Output overlaps source input')
        if any(resolved == Path(source).resolve() or Path(source).resolve() in resolved.parents for source in protected_dirs):
            raise ValueError('Output must be outside source/assets directories')
    return output, sidecar


def image_metadata(kind, presentation, source, render):
    return {'schema': 'world-memory.image.v1', 'view': kind,
            'presentation': presentation,
            'generatedAt': datetime.now(timezone.utc).isoformat(),
            'units': {'world': 'Minecraft blocks', 'image': 'pixels', 'angles': 'degrees', 'observedAt': 'Unix milliseconds'},
            'source': source, 'render': render}


def compact_air_source(source, metadata_file):
    keys = ('source','worldId','dimension','version','bounds_xyz_inclusive','seed_xyz',
            'voxel_count','actual_bounds_xyz','source_sha256','source_chunk_stamps',
            'air_names','boundary_counts','unknown_is_air','model')
    result = {k: source[k] for k in keys if k in source}
    result['details'] = {'metadataFile': str(metadata_file),
                         'sha256': hashlib.sha256(Path(metadata_file).read_bytes()).hexdigest(),
                         'fields': ['cropped_faces','unknown_boundary_faces']}
    return result


def summarize(metadata):
    """Bounded facts needed to interpret the attached PNG without another read."""
    source, render = metadata['source'], metadata['render']
    stamps = source.get('chunks') or source.get('stamps') or source.get('source_chunk_stamps') or []
    if isinstance(stamps, dict): stamps = list(stamps.values())
    times = [s.get('observedAt') for s in stamps if isinstance(s, dict)]
    times = [t for t in times if type(t) in (int, float) and math.isfinite(t)]
    columns = Counter(source.get('columnCounts') or {})
    if not columns: columns = Counter(c[2] for c in source.get('columnStatus') or [] if isinstance(c, list) and len(c) >= 3)
    def short(value): return value[:256] if isinstance(value, str) else value
    result = {'view': metadata['view'], 'presentation': metadata['presentation'],
              'meaning': short(render.get('description')),
              'worldId': short(source.get('worldId')), 'dimension': short(source.get('dimension')), 'version': short(source.get('version')),
              'range': {'xz': source.get('boundsXZ'), 'xyz': source.get('bounds_xyz_inclusive') or ([source['lo'], source['hi']] if 'lo' in source and 'hi' in source else None), 'y': source.get('y')},
              'axes': short(render.get('orientation') or render.get('worldAxes') or render.get('azimuth_reference')),
              'azimuthDegrees': render.get('azimuthDegrees'), 'azimuthsDegrees': render.get('azimuths_degrees'),
              'elevationDegrees': render.get('elevationDegrees', render.get('elevation_degrees')),
              'pixelsPerBlock': render.get('pixelsPerBlock'), 'orthographicScale': render.get('orthographicScale', render.get('orthographic_scale')),
              'grid': render.get('grid', False), 'gridPitchBlocks': render.get('grid_pitch_blocks'),
              'lighting': short(render.get('lighting') or source.get('lighting')),
              'freshness': {'observedChunks': len(stamps), 'observedAtMin': min(times) if times else None, 'observedAtMax': max(times) if times else None, 'unit': 'Unix milliseconds', 'generatedAt': metadata['generatedAt']},
              'unknown': {'isAir': False, 'voxels': source.get('unknownVoxels'), 'columnCounts': {k: columns[k] for k in ('surface','partial','empty','unknown')}, 'boundaryCounts': {k: source.get('boundary_counts', {}).get(k,0) for k in ('known_solid_boundary','crop_known_air','unknown_boundary')}},
              'manualScaleMayClip': render.get('manual_scale_may_clip', False)}
    return result


def write_pair(image, output, metadata, protected_files=(), protected_dirs=(), pnginfo=None):
    output, sidecar = output_paths(output, protected_files, protected_dirs)
    text = json.dumps(metadata, ensure_ascii=False, allow_nan=False, indent=2)
    if len(text.encode('utf-8')) > 64 * 1024 * 1024:
        raise ValueError('Image metadata exceeds 64 MiB')
    output.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        with output.open('xb') as png:
            created.append(output)
            with sidecar.open('x', encoding='utf-8') as data:
                created.append(sidecar)
                image.save(png, format='PNG', pnginfo=pnginfo)
                data.write(text)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return {'output': str(output), 'metadata': str(sidecar),
            'presentation': metadata['presentation'], 'pixels': list(image.size), 'summary': summarize(metadata)}

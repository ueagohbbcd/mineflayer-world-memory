# Visual-first world memory

Use a real-texture surface overview to choose a region, inspect its local 3D or
underground air shape, then ask specific state/count questions. The old summary
API is auxiliary and remains compatible. The consumer calls the CLI/JS exporter,
calls an optional renderer, and displays the resulting PNG in its own UI.
These tools run locally on cached data; `summary()` remains a text-only query.

## Setup and assets

Core: Node 22+ recommended (library itself declares Node >=18; Mineflayer 4.39.0
used by tests needs Node 22+), `npm install`. The exporter uses the same native
Prismarine chunk/block/registry packages as Mineflayer. Tested: prismarine-chunk
1.41.0, prismarine-block 1.23.0, Minecraft 1.21.1. No separate npm visual framework
is required for surface export or the white air cast.

Optional PNG renderers: Python 3.12 recommended and NumPy/Pillow. The pinned requirements were
tested on Python 3.12; use a compatible Python environment for their wheels:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r renderers/requirements.txt
```

Textured surface/local views need a matching **locally installed**
Prismarine-viewer `public` directory, tested with 1.33.0. If you choose to install
it, install it explicitly in your renderer environment (`npm install
prismarine-viewer@1.33.0`), according to that package's documented prerequisites.
Find its package via Node `require.resolve('prismarine-viewer/package.json')`; the
`public` directory is beside that file. Pass `--assets` explicitly to the surface
renderer. Required layout includes `blocksStates/1.21.1.json`,
`textures/1.21.1.png`, and per-block tiles such as
`textures/1.21.1/blocks/water_still.png` for water. Use assets you are entitled to
use. The library ships no Minecraft textures, models or private world data and
makes no claim to license those separately owned resources.

Cave scene rendering additionally requires an explicit local Blender installation
with Cycles CPU support. There is no runtime download or permanent rendering
service. NumPy/Pillow are for extraction/composition; Blender uses its bundled
Python. Missing executables, modules, fonts or assets produce explicit errors.
A caller-supplied CJK font supports Chinese biome labels; standard biome IDs/names remain available with the default font.

## Read a real cache

Create `surface-config.json` (paths resolve relative to this config, coordinates
are inclusive blocks):

```json
{
  "directory": "./memory",
  "worldId": "my-server/my-world",
  "version": "1.21.1",
  "dimension": "overworld",
  "bounds": [-128, 127, -128, 127],
  "maxLayers": 18,
  "output": "./views/surface.json"
}
```

`directory` is the same base directory passed to `createWorldMemory`. The exporter
selects only `base64url(worldId)` and checks every native record's version, dimension and chunk coordinates before decoding. It
rejects mismatched/corrupt data. Missing chunks and sections remain unknown. `y: [min,max]` optionally defines a vertical
slice; omit it to scan the full recorded height. A sliced map shows the highest
known surface within that slice.

```sh
node bin/world-memory.js surface --config surface-config.json
node bin/world-memory.js render-surface --input views/surface.json \
  --assets /path/to/prismarine-viewer/public --output views/overview.png --scale 8 --biomes
# A 16 px/block detail, same exact cells:
node bin/world-memory.js render-surface --input views/surface.json \
  --assets /path/to/prismarine-viewer/public --output views/detail.png --scale 16
```

The surface map is grid-free, with textures as the default mode. `--mode
hillshade` shows estimated natural-ground relief. Soil provenance and clear
building footprints need separate local checks.

Exports refuse existing outputs and all paths within the source cache, including
symlink aliases. Renderers also refuse existing outputs. Choose new output names
for each revision, outside the native cache. Exports include world identity,
version, dimension, original per-chunk timestamps and content hashes. The cache
has no cross-chunk transaction: pause writing or copy an atomically consistent
snapshot yourself when that distinction matters.

### JavaScript API

```js
const { visual } = require('mineflayer-world-memory')
await visual.exportSurface({
  directory: './memory', worldId: 'my-server/my-world', version: '1.21.1',
  dimension: 'overworld', bounds: [-128, 127, -128, 127], output: './surface.json'
})
await visual.exportRegion({
  directory: './memory', worldId: 'my-server/my-world', version: '1.21.1',
  dimension: 'overworld', min: [-16, 0, -16], max: [15, 31, 15], output: './region'
})
```

JS paths resolve relative to the process CWD. `openCache(config)` plus
`surface(cache, options)` / `region(cache, options)` return data without writing.
For a server with a custom registry, pass its matching Prismarine registry as
`registry` in JS; supply it again when reading the cache. The generic CLI assumes the
standard registry for its explicit version. Unsupported native formats fail;
only Java 1.21.1 has been end-to-end tested.

## Underground white air cast

Region config uses `min` and `max` inclusive XYZ:

```json
{
  "directory": "./memory", "worldId": "my-server/my-world",
  "version": "1.21.1", "dimension": "overworld",
  "min": [-16, 0, -16], "max": [15, 31, 15], "output": "./views/region"
}
```

Pick an actual known-air seed after inspecting the region; a seed in rock, water,
unknown or outside the ROI is rejected. Keep the ROI below sky or otherwise
bounded to the cavity you intend to inspect. Extraction follows the seed-connected
air component within that ROI; classify building interiors separately.

An entirely synthetic, reproducible example:

```sh
node examples/make-demo-cache.js /tmp/world-memory-demo
node bin/world-memory.js region --config /tmp/world-memory-demo/region-config.json
node bin/world-memory.js cave --region-dir /tmp/world-memory-demo/region \
  --output-dir /tmp/world-memory-demo/cast \
  --min -14 4 4 --max -5 7 6 --seed -10 5 5
node bin/world-memory.js render-cave --workdir /tmp/world-memory-demo/cast
node bin/world-memory.js compose-cave --workdir /tmp/world-memory-demo/cast
```

The broad source region extends beyond the selected cast so crop metadata can
say whether known air continues beyond it. A selection ending at source limits
leaves tunnel continuation unknown. White is the selected six-connected known-air
volume. Small passable decorations are included according to the explicit
`air_names` metadata; stairs/slabs remain whole occupied voxels. Collision clearance
and walkability require separate checks.

Source regions are limited to 8,000,000 voxels, selected connected air components
to 100,000 voxels and exposed faces to 200,000. The extractor stops before
exceeding those budgets; choose a smaller ROI if needed.

The default is a fine one-block quadrilateral grid without diagonal wireframe,
ordinary depth-tested lines, two orthographic views 180 degrees apart, equal
33-degree elevation/scale and the same world-space lights. Cropped faces and
unknown contacts have distinct metadata/annotations: gold marks known air
continuing beyond the crop, and purple marks unknown space. Scene settings are
recorded in `render-metadata.json`.

## Data contracts and bounds

- `world-memory.surface-map.v1`: `X0,X1,Z0,Z1`; cells `[x,z,layers,biomeId|null,
  naturalGroundY|null]`; layers are descending `[y,stateId]`; `states` records
  names/properties; `columnStatus` distinguishes `surface`, `partial`, `empty`,
  `unknown` and an explicit layer-limit flag. Missing columns have no cell and an
  unknown status. All-air columns have a cell with no layers. Unknown above a
  known block prevents claiming a top; unknown below transparent layers remains
  partial. For grey/masked cells, use `columnStatus` to distinguish unknown
  coverage from known empty columns.
- Up to 18 non-air alpha layers by default, maximum 64. Unknown/limit truncation
  stops the layer stack at the recorded boundary. Water uses a tinted real static
  tile; its bottom is left unresolved. Roofs, leaves, crops and water remain visible.
- Native biomes are sampled at the highest visible block Y from 4×4×4 palettes,
  including roofs/canopies. Missing biome data stays null. Boundaries connect
  differing known IDs, so they can look more stepped than client
  Voronoi-smoothed boundaries. Materials stay visible;
  vegetation/water use fixed illustrative tint.
- `world-memory.region.v1`: `lo,hi,shape` in XYZ; `region.u16` little-endian uint16
  name-palette indexes, `states.i32` little-endian int32 native state IDs. C-order
  XYZ means Z varies fastest. Palette entry 0 is `unknown`; native state -1 is
  unknown. Known air has its own palette entry/state. Both binaries have exactly
  `shape[0]*shape[1]*shape[2]` entries. Provenance lives in `stamps`.
- Maximum 262,144 horizontal columns and eight million region voxels; native
  height <=4096. Surface accumulation also stops at 1,048,576 layers or
  134,217,728 state samples; JS/config callers may lower `maxTotalLayers` /
  `maxSamples` within those ceilings. Requests beyond bounds fail before output. Surface extraction
  releases each decoded chunk after use; use bounded detail regions instead of
  exporting an entire server into one volume.

## Rendering limits and verification

Projected top faces use real atlas/model textures. Plants without top faces use
real alpha sprites as explicit map symbols. Some rotated/special geometries use
model material/particle texture. These views are approximate reconstructions.
Static texture frame, simplified transparency, no entities/particles/dynamic
shadows. The optional local 3D adapter has its own documented meshing limits.

All views use presentation lighting or hillshade. Measured game lighting remains
unavailable: the cache omits some light-only packets, and the dependency's
`fromJson` empty-light-mask issue remains. Its chunk getter can return zero for
absent light sections; treat that value as unknown lighting.

`npm test` covers the old API and synthetic native cache identity/unknown/range/
output rules. `npm run test:visual` checks synthetic PNGs, materials, orientation,
biome boundaries and air-cast geometry without a Minecraft server. The optional
Blender smoke is the demo above, with a low-resolution/samples config for fast QA.
`node benchmark/visual.js` reports bounded synthetic surface/region extraction
timing and peak RSS on synthetic data. `node benchmark/terrain.js` measures the
original summary path. Real rendering performance depends on ROI,
resolution and assets. No private worlds/images are committed to this repository.

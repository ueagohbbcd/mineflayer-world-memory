# mineflayer-world-memory

Persistent native world memory for Mineflayer, tested with Minecraft Java 1.21.1.
It remembers chunks the bot has received, including unloaded chunks and other
dimensions. Recording is passive: exploration and movement stay with the caller.

## Look first, query when needed

1. **Broad world view:** export a bounded cache area and render its real-texture
   top-down map. North is up, unknown areas stay masked. Optional thin native
   biome boundaries preserve the materials; the surface stays grid-free.
2. **Local detail:** export a small XYZ region for optional textured 3D inspection,
   or select a known-air seed for an underground white air-volume cast. Cave
   views use a fine **one-block quad grid**, two opposite views and fixed display
   lighting. White represents known air volume; crop caps mark its selected extent.
3. **Specific questions:** use `block()` / `chunk()` / `find()` for exact cached
   state; use `summary()` for auxiliary filtering, coverage and freshness.
   Use counts and height statistics to guide visual inspection.

The native cache is the source of truth. All views are derived, offline and
read-only. Unknown areas stay distinct from known air. Cached observations may
be stale; check their timestamps. Text summaries remain API-compatible, with
rendering invoked separately.

### Model-facing images

Renderers default to bare PNGs. Cave comparisons join the two views directly at
the same scale. Image meaning, coordinates, orientation, source observation times,
biome anchors and legends are written beside each PNG as `<image>.png.json`.
The CLI returns both paths. Use `--presentation` for human-facing labels or
report layouts. Surface maps stay grid-free; cave grids remain on by default.

### Start a visual inspection

```sh
npm install
node bin/world-memory.js --help
node examples/make-demo-cache.js /tmp/world-memory-demo
node bin/world-memory.js surface --config /tmp/world-memory-demo/surface-config.json
# Optional renderer setup: Python 3.12, NumPy/Pillow; see docs/visual.md.
node bin/world-memory.js render-surface \
  --input /tmp/world-memory-demo/surface.json \
  --assets /path/to/your/prismarine-viewer/public \
  --output /tmp/world-memory-demo/map.png --scale 8 --biomes
```

The demo uses synthetic blocks. Supply locally installed, legally obtained,
version-matching viewer assets. Missing assets produce an explicit error.

See **[visual workflow, dependencies and full examples](docs/visual.md)** for real
cache configuration, JS exports, region/cave rendering, schema and limitations.
See **[optional local textured 3D](docs/local-3d.md)** for the existing Prismarine
mesher and CPU renderer. Consumers must explicitly call these tools and present
the resulting image in their UI.

## Record received chunks

```js
const { createWorldMemory } = require('mineflayer-world-memory')
const memory = createWorldMemory(bot, {
  directory: './memory',
  worldId: 'my-server/my-world' // unique, stable identity; change on world reset
})
await memory.refresh() // wait for current analysis/persistence
// Keep memory attached while the bot receives updates.
// Later, on shutdown:
await memory.dispose()
```

The cache lives below `directory/base64url(worldId)`, scoped by dimension and
Minecraft version. Use a distinct identity for each world and after a reset.
Snapshot the cache for a fully consistent multi-chunk export while a bot writes;
atomic writes apply to individual files.

## Auxiliary queries and compatibility

`summary()`, `find()`, `block()`, `chunk()`, `refresh()` and `dispose()` retain their
existing contracts. Every positive exposed block count is preserved, including
rare blocks. Per-chunk height/relief statistics remain lightweight hints, with
null statistics for empty/unknown surfaces. Check buildability locally.

See [memory API, persistence, exposure and height definitions](docs/memory-api.md).
Live bot installation, consumer dependency upgrades and deployment are separate steps.

## Watchdog

`watchdog(bot, { report })` (from `watchdog.js` / `watchdog.mjs`) reports a bot that is stuck, a program that keeps retrying the same thing, and a program blocked by a synchronous loop. It only reads Mineflayer state. See [watchdog](docs/watchdog.md).

## Tests

```sh
npm test
python3 -m pip install -r renderers/requirements.txt  # explicit optional install
npm run test:visual
node benchmark/terrain.js
```

Verification uses synthetic worlds/assets; live-server behavior remains untested. Blender
rendering is a separate optional smoke test described in the visual guide. Core
Node installation has no Python, Blender, browser or GPU requirement.

### On-demand perception helpers

Use [live CPU views](docs/live-visual.md) for bounded surface/local snapshots,
bare PNGs, real top-face textures and explicit unknown/history metadata from an
already connected bot. They are exposed through `visual.live` or `live.mjs`;
no background observer or strategy loop is installed.

[Perception queries](docs/perception-queries.md) include an on-demand tree-candidate
query: connected log coordinates, nearby leaf evidence and observation boundaries.
Import `queryTreeCandidate` from `queries.mjs`; control code chooses which blocks to harvest.

### Full-column readers without automatic exposure analysis

For a read service or archival observer, use `analysis: 'lazy'`:

```js
const memory = createWorldMemory(bot, {
  analysis: 'lazy',
  directory: './memory',
  worldId: 'world/reset/player' // keep independent worlds/resets/players isolated
})
await memory.ready // restored files and current received columns, no voxel scan
const info = memory.chunkInfo({ dimension: 'overworld', x: -1, z: 0 })
const block = memory.block({ dimension: 'overworld', x: -1, y: -40, z: 3 })
const columns = memory.chunks({ dimension: 'overworld' })
await memory.flush() // persist native columns without exposure analysis
```

`chunkInfo` and `chunks` return detached metadata: chunk coordinates, dimension,
`loaded`, `observedAt`, version, minimum Y and height. `revision` is the live
memory revision; `summary().revision` remains the separately computed exposure
revision. A missing column is unknown; known air is a real block. Loaded records
retain native column updates; unload keeps the last column, and the next load
replaces the complete record. Metadata `observedAt` records the latest captured
chunk/block event, not the time every unchanged cell was individually received.

Lazy mode still saves dirty native columns automatically. It skips automatic
exposure classification, including on disposal. `refresh()` and `find()` remain
explicit, potentially expensive exposure-analysis requests. `find()` searches
exposed blocks only; `block()` and `chunk()` include all received blocks, even
sealed blocks. `ready`, exact reads and metadata reads do not request analysis.
`chunk()` returns the underlying native object for existing compatibility;
services should serialize it or wrap getters rather than expose mutation.

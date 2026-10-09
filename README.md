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

# mineflayer-world-memory

Persistent native world memory for Mineflayer, tested with Minecraft Java 1.21.1.
It remembers chunks the bot has received, including unloaded chunks and other
dimensions. It never requests unknown chunks or moves the bot.

## Look first, query when needed

1. **Broad world view:** export a bounded cache area and render its real-texture
   top-down map. North is up, unknown areas stay masked. Optional thin native
   biome boundaries preserve the materials. **No surface grid.**
2. **Local detail:** export a small XYZ region for optional textured 3D inspection,
   or select a known-air seed for an underground white air-volume cast. Cave
   views use a fine **one-block quad grid**, two opposite views and fixed display
   lighting. White represents air, not rock. Cut surfaces are not dead ends.
3. **Specific questions:** use `block()` / `chunk()` / `find()` for exact cached
   state; use `summary()` for auxiliary filtering, coverage and freshness.
   Counts and height statistics are not a substitute for looking at the map.

The native cache is the source of truth. All views are derived, offline and
read-only. Unknown is never air; remembered is never a promise of current live
state. Text summaries remain API-compatible and do not automatically launch a
renderer, start a server or upload maps.

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

The demo is generated entirely from synthetic blocks. The tool does not download
textures: use your own locally installed, legally obtained, version-matching
viewer assets. Missing assets fail clearly; there is no fake-material fallback.

See **[visual workflow, dependencies and full examples](docs/visual.md)** for real
cache configuration, JS exports, region/cave rendering, schema and limitations.
See **[optional local textured 3D](docs/local-3d.md)** for the existing Prismarine
mesher and CPU renderer. Consumers must explicitly call these tools and present
the resulting image; upgrading this library alone does not change a bot UI.

## Record received chunks

```js
const { createWorldMemory } = require('mineflayer-world-memory')
const memory = createWorldMemory(bot, {
  directory: './memory',
  worldId: 'my-server/my-world' // unique, stable identity; change on world reset
})
await memory.refresh() // wait for current analysis/persistence, not rendering
// Keep memory attached while the bot receives updates.
// Later, on shutdown:
await memory.dispose()
```

The cache lives below `directory/base64url(worldId)`, scoped by dimension and
Minecraft version. Never share an identity between unrelated/reset worlds.
Snapshot the cache for a fully consistent multi-chunk export while a bot writes;
files are individually atomic, not a world-wide transaction.

## Auxiliary queries and compatibility

`summary()`, `find()`, `block()`, `chunk()`, `refresh()` and `dispose()` retain their
existing contracts. Every positive exposed block count is preserved, including
rare blocks. Per-chunk height/relief statistics remain lightweight hints, with
null statistics for empty/unknown surfaces; they do not establish buildability.

See [memory API, persistence, exposure and height definitions](docs/memory-api.md).
No live bot installation, consumer dependency upgrade or deployment is performed
by these tools.

## Tests

```sh
npm test
python3 -m pip install -r renderers/requirements.txt  # explicit optional install
npm run test:visual
node benchmark/terrain.js
```

Tests use synthetic worlds/assets, no Minecraft server or private cache. Blender
rendering is a separate optional smoke test described in the visual guide. Core
Node installation has no Python, Blender, browser or GPU requirement.

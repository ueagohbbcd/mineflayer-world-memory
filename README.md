# mineflayer-world-memory

Exploration memory for Mineflayer, tested with Minecraft 1.21.1. Remembers native chunk columns the bot has received, including unloaded columns and other dimensions. It never requests unknown chunks or moves the bot.

```js
const { createWorldMemory } = require('mineflayer-world-memory')
const memory = createWorldMemory(bot, {
  directory: './memory', // optional; requires an explicit world identity
  worldId: 'my-server/my-world',
  entities: () => entityFacts.summary() // supplied by your entity service
})
await memory.refresh()
console.log(memory.summary())
console.log(await memory.find({ name: 'nether_portal', dimension: 'overworld' }))
await memory.dispose()
```

`summary()` is synchronous and returns the last completed result plus `pending`. Received changes automatically schedule background analysis. Call `refresh()` to await the current analysis pass; updates arriving during that pass schedule another pass and remain visible as `pending`. It identifies `currentDimension` and reports cumulative `blockCounts` across all remembered dimensions, and per-dimension `blockCounts`, `coverage`, `currentCoverage`, `chunks`, `loadedChunks`, and `lastObservedAt`. Revisions change when received world state changes; reading a summary does not produce a new timestamp. Entity facts come directly from the supplied provider.

Coverage rectangles have minimum **block** `x,z` and `width,depth` measured in **chunks**. They exactly cover known chunk coordinates without filling gaps. Horizontal runs are merged vertically, and the analogous transposed result is used if it needs fewer rectangles. This is a deterministic compact cover, not a claim of global minimum rectangle count.

## Chunk surface heights

Each dimension also has `chunkSummaries`, one entry per remembered chunk:

```js
await memory.refresh()
const chunks = memory.summary().dimensions.overworld?.chunkSummaries || []
const candidates = chunks.filter(c => c.loaded && c.surface.coverage === 1 && c.surface.relief <= 2)
// Example entry (x,z are CHUNK coordinates):
// { x: -2, z: 3, loaded: true, observedAt: 1790000000000,
//   surface: { columns: 256, emptyColumns: 0, unknownColumns: 0, coverage: 1,
//              minY: 64, maxY: 66, meanY: 65, relief: 2, stddev: 0.7071 } }
```

`surface` measures the highest **non-air block Y** in each of the chunk's 256
horizontal columns, over the native column's `minY` and `worldHeight`. Only
`air`, `cave_air`, and `void_air` are skipped. This deliberately includes tree
canopies, water/lava surfaces, snow layers, plants and artificial roofs/platforms.
It is not natural ground, water depth, fractional collision height or a safe
standing position. Cave floors below an overlying roof do not lower that column's
surface; in a roofed dimension this can measure the dimension ceiling.

- `columns`: columns with a known top non-air block; these alone contribute to statistics.
- `emptyColumns`: known all-air columns in the scanned vertical range.
- `unknownColumns`: columns with an unrecognized/missing block state above any known non-air block. These are excluded, never treated as zero height or air. Unknown states below a known top do not affect its height.
- `coverage`: `columns / 256`. The three column counts sum to 256. Missing chunks have no entry; unloaded remembered chunks retain their last observations with `loaded: false`.
- `minY`, `maxY`, `meanY`: minimum, maximum and arithmetic mean block Y.
- `relief`: `maxY - minY`; `stddev`: population standard deviation in blocks.
  All five height statistics are `null` when `columns === 0`; empty and unknown
  columns are not evidence of flat terrain.

These are lightweight first-pass hints for choosing which chunks to inspect or
render. Low relief does not establish a clear building footprint, accessibility,
ownership or permission to build. A footprint spanning chunks needs local checks;
the summary contains no per-column height map. Existing exposed `blockCounts`
remain independent and include every positive count without truncation.

Heights are collected during the existing classification scan, with no additional
native voxel reads; only 256 temporary height/state slots are added per scan.
Unchanged chunks reuse their surface statistics. Received block/chunk updates
invalidate them along with the existing analysis. The same `revision`, `pending`,
`loaded` and `observedAt` freshness rules apply as for other memory results.
Native gzip persistence is unchanged: old cache files have no new required fields,
and surface statistics are recomputed from the restored native column. Summary
results and their chunk entries are read-only snapshots.

Exposed means connected by six-neighbor transparent blocks to the top of a known column, plus opaque blocks adjacent to that connected space. The native registry's transparency and empty collision box define transparent space. Connections cross chunk boundaries, including unloaded remembered columns. Unknown space at a side or bottom boundary is not assumed exposed. Every native block type with a positive exposed count is included, including air. This reports exposed world geometry, not the bot's exact camera visibility or raycast history. Closed cavities remain hidden until a received update connects them to exposed space. Air positions use compact runs rather than one index for every empty voxel.

`find({name, dimension, limit=100})` refreshes pending work and reads indexed exposed positions. Its result includes `positions`, `total`, `remaining`, `revision`, and `pending`; every position includes `loaded` and `observedAt`. All known columns contribute to the total, regardless of the result limit. `chunk({x,z,dimension})` returns the remembered native column, using chunk coordinates. `block({x,y,z,dimension})` reads a remembered native block at block coordinates, including hidden blocks; absent columns return null. Treat returned columns and snapshots as read-only.

Analysis uses typed per-column component labels, unions neighboring components, and yields to the event loop in batches. A cached unchanged column is not classified again. Exposed-component signatures let unchanged columns reuse their position indexes and counts, while connectivity changes propagate through the full known component graph. Large explored worlds consume memory proportional to remembered voxel volume. Raw native columns are gzip persisted atomically by dimension and chunk, scoped to `worldId` and checked against the Minecraft version. Never reuse a world identity for a different or reset world. Restoration happens asynchronously on creation; loading errors remain in `summary().error` for that memory instance, even after successful analysis. `dispose()` drains received updates and persistence before publishing the final unloaded index.

### Tests and synthetic benchmark

Run `npm test` for the native chunk, persistence and terrain regression tests.
Run `node benchmark/terrain.js` for seven measured fresh-analysis trials (after
one warmup), each with 16 synthetic 384-block-tall chunks, plus 1,000 cached
refreshes. Optionally pass a baseline `index.js` path for the same workload.
The benchmark reports timings and native block-state reads; it never reads a
server or saved world. Timing is environment-dependent. Both versions should
read exactly 1,572,864 states on the first pass and zero on cached refreshes.

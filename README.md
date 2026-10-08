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

Exposed means connected by six-neighbor transparent blocks to the top of a known column, plus opaque blocks adjacent to that connected space. The native registry's transparency and empty collision box define transparent space. Connections cross chunk boundaries, including unloaded remembered columns. Unknown space at a side or bottom boundary is not assumed exposed. Every native block type with a positive exposed count is included, including air. This reports exposed world geometry, not the bot's exact camera visibility or raycast history. Closed cavities remain hidden until a received update connects them to exposed space. Air positions use compact runs rather than one index for every empty voxel.

`find({name, dimension, limit=100})` refreshes pending work and reads indexed exposed positions. Its result includes `positions`, `total`, `remaining`, `revision`, and `pending`; every position includes `loaded` and `observedAt`. All known columns contribute to the total, regardless of the result limit. `chunk({x,z,dimension})` returns the remembered native column, using chunk coordinates. `block({x,y,z,dimension})` reads a remembered native block at block coordinates, including hidden blocks; absent columns return null. Treat returned columns and snapshots as read-only.

Analysis uses typed per-column component labels, unions neighboring components, and yields to the event loop in batches. A cached unchanged column is not classified again. Exposed-component signatures let unchanged columns reuse their position indexes and counts, while connectivity changes propagate through the full known component graph. Large explored worlds consume memory proportional to remembered voxel volume. Raw native columns are gzip persisted atomically by dimension and chunk, scoped to `worldId` and checked against the Minecraft version. Never reuse a world identity for a different or reset world. Restoration happens asynchronously on creation; loading errors remain in `summary().error` for that memory instance, even after successful analysis. `dispose()` drains received updates and persistence before publishing the final unloaded index.

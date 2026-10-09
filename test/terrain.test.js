const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { mkdtemp, rm, readdir, readFile, writeFile } = require('node:fs/promises')
const { tmpdir } = require('node:os')
const path = require('node:path')
const zlib = require('node:zlib')
const { createWorldMemory } = require('..')
const registry = require('prismarine-registry')('1.21.1')
const Chunk = require('prismarine-chunk')('1.21.1')
function fixture (columns = new Map()) {
  const bot = new EventEmitter()
  bot.version = '1.21.1'; bot.registry = registry; bot.game = { dimension: 'overworld' }
  bot.world = { getColumns: () => [...columns].map(([key, column]) => { const [chunkX, chunkZ] = key.split(',').map(Number); return { chunkX, chunkZ, column } }) }
  return bot
}
function column () { return new Chunk({ minY: -64, worldHeight: 128 }) }
function put (c, x, y, z, name) { c.setBlockStateId({ x, y, z }, registry.blocksByName[name].minStateId) }
function floor (c, height) { for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) put(c, x, typeof height === 'function' ? height(x, z) : height, z, 'stone'); return c }
const chunkSummary = memory => memory.summary().dimensions.overworld.chunkSummaries[0]

test('flat negative surface, slopes and population standard deviation', async () => {
  for (const [height, expected] of [[-32, { minY: -32, maxY: -32, meanY: -32, relief: 0, stddev: 0 }], [(x) => x - 32, { minY: -32, maxY: -17, meanY: -24.5, relief: 15, stddev: Math.sqrt(21.25) }]]) {
    const c = floor(column(), height), memory = createWorldMemory(fixture(new Map([['-2,3', c]])), { now: () => 123 })
    try {
      await memory.refresh()
      const { surface, ...metadata } = chunkSummary(memory)
      assert.deepEqual(metadata, { x: -2, z: 3, loaded: true, observedAt: 123 })
      assert.equal(surface.columns, 256); assert.equal(surface.coverage, 1)
      assert.equal(surface.emptyColumns, 0); assert.equal(surface.unknownColumns, 0)
      for (const [key, value] of Object.entries(expected)) assert(Math.abs(surface[key] - value) < 1e-10, key)
    } finally { await memory.dispose() }
  }
})

test('surface includes tree canopy, water, snow, plants and roofs rather than cave floors or natural ground', async () => {
  const c = floor(column(), -20)
  put(c, 5, -20, 0, 'emerald_ore')
  put(c, 0, -10, 0, 'oak_leaves'); put(c, 1, -15, 0, 'water')
  put(c, 2, -19, 0, 'snow'); put(c, 3, -19, 0, 'short_grass')
  put(c, 4, 10, 0, 'stone'); put(c, 4, -40, 0, 'diamond_ore')
  // All three air variants are excluded, even above a roof.
  put(c, 4, 11, 0, 'cave_air'); put(c, 4, 12, 0, 'void_air')
  const memory = createWorldMemory(fixture(new Map([['0,0', c]])))
  try {
    await memory.refresh()
    const s = chunkSummary(memory).surface
    assert.equal(s.minY, -20); assert.equal(s.maxY, 10); assert.equal(s.relief, 30)
    assert(Math.abs(s.meanY - (-20 + (10 + 5 + 1 + 1 + 30) / 256)) < 1e-10)
    assert.equal(memory.summary().blockCounts.diamond_ore, undefined) // sealed below the floor
    assert.equal(memory.summary().blockCounts.emerald_ore, 1) // rare exposed counts are not truncated
  } finally { await memory.dispose() }
})

test('known empty and unknown columns never contribute zero heights; unknown below a known top is harmless', async () => {
  const c = column(), original = c.getBlockStateId.bind(c)
  put(c, 0, -30, 0, 'stone'); put(c, 1, -30, 0, 'stone'); put(c, 2, -30, 0, 'stone')
  c.getBlockStateId = p => (p.z === 0 && ((p.x === 0 && p.y === -29) || (p.x === 1 && p.y === -31))) ? undefined : original(p)
  const columns = new Map([['0,0', c]]), bot = fixture(columns), memory = createWorldMemory(bot)
  try {
    await memory.refresh()
    assert.deepEqual(chunkSummary(memory).surface, { columns: 2, emptyColumns: 253, unknownColumns: 1, coverage: 2 / 256, minY: -30, maxY: -30, meanY: -30, relief: 0, stddev: 0 })
    assert.equal(memory.chunk({ x: 1, z: 0 }), null)
    assert.equal(memory.summary().dimensions.overworld.chunkSummaries.length, 1)
    columns.delete('0,0'); bot.emit('chunkColumnUnload', { x: 0, z: 0 })
    await memory.refresh()
    assert.equal(chunkSummary(memory).loaded, false)
    assert.equal(chunkSummary(memory).surface.columns, 2)
  } finally { await memory.dispose() }
  const empty = createWorldMemory(fixture(new Map([['0,0', column()]])))
  try {
    await empty.refresh()
    assert.deepEqual(chunkSummary(empty).surface, { columns: 0, emptyColumns: 256, unknownColumns: 0, coverage: 0, minY: null, maxY: null, meanY: null, relief: null, stddev: null })
  } finally { await empty.dispose() }
})

test('block additions and removal update heights, old snapshots remain immutable and cached reads avoid voxel scans', async () => {
  const c = floor(column(), -32), original = c.getBlockStateId.bind(c); let reads = 0
  c.getBlockStateId = p => { reads++; return original(p) }
  const bot = fixture(new Map([['0,0', c]])), memory = createWorldMemory(bot)
  try {
    await memory.refresh()
    assert.equal(reads, 128 * 256)
    const before = chunkSummary(memory).surface
    await memory.refresh(); memory.summary(); bot.emit('spawn'); await memory.refresh()
    assert.equal(reads, 128 * 256)
    const update = async (oldName, newName) => {
      put(c, 0, 10, 0, newName)
      bot.emit('blockUpdate', { stateId: registry.blocksByName[oldName].minStateId }, { stateId: registry.blocksByName[newName].minStateId, position: { x: 0, y: 10, z: 0 } })
      assert.equal(memory.summary().pending, true); await memory.refresh()
    }
    await update('air', 'stone')
    assert.equal(chunkSummary(memory).surface.maxY, 10)
    assert(Math.abs(chunkSummary(memory).surface.meanY - (-32 + 42 / 256)) < 1e-10)
    assert.equal(before.maxY, -32)
    await update('stone', 'air')
    assert.equal(chunkSummary(memory).surface.maxY, -32)
    assert.equal(reads, 3 * 128 * 256)
  } finally { await memory.dispose() }
})

test('native old-format persistence without terrain fields recomputes stats and preserves dimension and freshness', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'terrain-memory-'))
  try {
    const c = floor(column(), -32), memory = createWorldMemory(fixture(new Map([['-1,1', c]])), { directory, worldId: 'terrain', now: () => 456 })
    await memory.refresh(); const expected = chunkSummary(memory).surface; await memory.dispose()
    const scope = path.join(directory, Buffer.from('terrain').toString('base64url'))
    const file = path.join(scope, (await readdir(scope))[0])
    const data = JSON.parse(zlib.gunzipSync(await readFile(file)))
    assert.equal(data.surface, undefined); assert.equal(data.analysis, undefined)
    await writeFile(file, zlib.gzipSync(JSON.stringify(data)))
    const bot = fixture(); bot.game.dimension = 'the_nether'
    const restored = createWorldMemory(bot, { directory, worldId: 'terrain' })
    try {
      await restored.refresh()
      assert.deepEqual(chunkSummary(restored).surface, expected)
      assert.equal(chunkSummary(restored).loaded, false); assert.equal(chunkSummary(restored).observedAt, 456)
      assert.equal(restored.summary().dimensions.the_nether, undefined)
      assert.deepEqual(JSON.parse(JSON.stringify(restored.summary())).dimensions.overworld.chunkSummaries[0].surface, expected)
    } finally { await restored.dispose() }
  } finally { await rm(directory, { recursive: true, force: true }) }
})

test('all-unknown columns have null statistics, and custom dimension ceilings count the top block', async () => {
  const unknown = { minY: 0, worldHeight: 16, getBlockStateId: () => undefined }
  const c = new Chunk({ minY: 0, worldHeight: 16 }); put(c, 0, 15, 0, 'stone')
  const bot = fixture(new Map([['0,0', unknown], ['1,0', c]])); bot.game.dimension = 'custom'
  const memory = createWorldMemory(bot)
  try {
    await memory.refresh()
    const entries = memory.summary().dimensions.custom.chunkSummaries
    assert.deepEqual(entries[0].surface, { columns: 0, emptyColumns: 0, unknownColumns: 256, coverage: 0, minY: null, maxY: null, meanY: null, relief: null, stddev: null })
    assert.equal(entries[1].surface.maxY, 15); assert.equal(entries[1].surface.meanY, 15)
    assert.equal(entries[1].surface.columns, 1); assert.equal(entries[1].surface.emptyColumns, 255)
  } finally { await memory.dispose() }
})

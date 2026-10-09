const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { mkdtemp, rm, readdir } = require('node:fs/promises')
const { tmpdir } = require('node:os')
const path = require('node:path')
const { createWorldMemory, coverageRectangles } = require('..')
const registry = require('prismarine-registry')('1.21.1')
const Chunk = require('prismarine-chunk')('1.21.1')
function fixture (columns) {
  const bot = new EventEmitter()
  bot.version = '1.21.1'; bot.registry = registry; bot.game = { dimension: 'overworld' }
  bot.world = { getColumns: () => [...columns].map(([key, column]) => { const [chunkX, chunkZ] = key.split(',').map(Number); return { chunkX, chunkZ, column } }), getColumnAt: p => columns.get(`${Math.floor(p.x / 16)},${Math.floor(p.z / 16)}`) }
  return bot
}
function column () { return new Chunk({ minY: 80, worldHeight: 32 }) }
function put (c, x, y, z, name) { c.setBlockStateId({ x, y, z }, registry.blocksByName[name].minStateId) }
test('exact coverage of irregular negative chunks', () => {
  const chunks = [{ x: -2, z: 0 }, { x: -1, z: 0 }, { x: -2, z: 1 }, { x: -1, z: 1 }, { x: 0, z: 1 }]
  const covered = new Set()
  for (const r of coverageRectangles(chunks)) for (let x = r.x / 16; x < r.x / 16 + r.width; x++) for (let z = r.z / 16; z < r.z / 16 + r.depth; z++) { assert(!covered.has(`${x},${z}`)); covered.add(`${x},${z}`) }
  assert.deepEqual(covered, new Set(chunks.map(c => `${c.x},${c.z}`)))
})
test('counts ordinary exposed blocks, hides sealed blocks, follows boundary connectivity and historical chunks', async () => {
  const a = column(), b = column()
  // A sealed stone box straddles the chunk seam, with a diamond in its air pocket.
  for (let x = 14; x <= 17; x++) for (let y = 82; y <= 86; y++) for (let z = 4; z <= 8; z++) {
    if (x === 14 || x === 17 || y === 82 || y === 86 || z === 4 || z === 8) put(x < 16 ? a : b, x % 16, y, z, 'stone')
  }
  put(a, 15, 84, 6, 'diamond_ore')
  put(a, 5, 86, 8, 'dirt')
  const columns = new Map([['-1,1', a], ['0,1', b]])
  const bot = fixture(columns), memory = createWorldMemory(bot, { entities: () => [{ name: 'cow' }] })
  let summary = await memory.refresh()
  assert.equal(summary.blockCounts.diamond_ore, undefined)
  assert.equal(summary.blockCounts.dirt, 1)
  assert(summary.blockCounts.air > 0)
  const air = await memory.find({ name: 'air', limit: 2 })
  assert.equal(air.positions.length, 2)
  assert.equal(air.total, summary.blockCounts.air)
  assert.equal(air.remaining, air.total - 2)
  assert.deepEqual((await memory.find({ name: 'dirt' })).positions.map(({ loaded, observedAt, ...p }) => p), [{ x: -11, y: 86, z: 24, dimension: 'overworld' }])
  put(b, 1, 84, 6, 'air')
  bot.emit('blockUpdate', { stateId: registry.blocksByName.stone.minStateId }, { stateId: 0, position: { x: 1, y: 84, z: 22 } })
  summary = await memory.refresh()
  assert.equal(summary.blockCounts.diamond_ore, 1)
  columns.delete('0,1'); bot.emit('chunkColumnUnload', { x: 0, z: 16 })
  summary = await memory.refresh()
  assert.equal(summary.dimensions.overworld.chunks, 2)
  assert.equal(summary.dimensions.overworld.loadedChunks, 1)
  assert.equal(summary.blockCounts.diamond_ore, 1)
  assert.equal(summary.pending, false)
  await memory.dispose()
  assert.deepEqual(memory.summary().dimensions.overworld.currentCoverage, [])
  assert.equal(memory.summary().dimensions.overworld.loadedChunks, 0)
  assert.equal(memory.summary().dimensions.overworld.chunks, 2)
})
test('native persistence restores exposed portal at a distant coordinate in isolated dimension', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'world-memory-'))
  try {
    const c = column(); put(c, 4, 106, 14, 'nether_portal')
    const bot = fixture(new Map([['-6,1', c]])), memory = createWorldMemory(bot, { directory, worldId: 'fixture' })
    await memory.refresh(); await memory.dispose()
    const nextBot = fixture(new Map()); nextBot.game.dimension = 'the_nether'
    const restored = createWorldMemory(nextBot, { directory, worldId: 'fixture' })
    const summary = await restored.refresh()
    assert.equal(summary.dimensions.overworld.loadedChunks, 0)
    assert.deepEqual((await restored.find({ name: 'nether_portal', dimension: 'overworld' })).positions.map(({ loaded, observedAt, ...p }) => p), [{ x: -92, y: 106, z: 30, dimension: 'overworld' }])
    assert.equal(restored.block({ x: -92, y: 106, z: 30, dimension: 'overworld' }).name, 'nether_portal')
    await restored.dispose()
  } finally { await rm(directory, { recursive: true, force: true }) }
})
test('background work completes and updates received during refresh do not invalidate in-flight analysis', async () => {
  const c = new Chunk(), bot = fixture(new Map([['0,0', c]])), memory = createWorldMemory(bot)
  const initial = memory.refresh()
  setImmediate(() => {
    put(c, 3, 90, 3, 'dirt')
    bot.emit('blockUpdate', { stateId: 0 }, { stateId: registry.blocksByName.dirt.minStateId, position: { x: 3, y: 90, z: 3 } })
  })
  await initial
  for (let i = 0; i < 100 && memory.summary().pending; i++) await new Promise(resolve => setTimeout(resolve, 10))
  assert.equal(memory.summary().pending, false)
  assert.equal(memory.summary().blockCounts.dirt, 1)
  assert.equal(memory.summary().dimensions.overworld.chunkSummaries[0].surface.meanY, 90)
  const before = memory.summary().revision
  assert.equal(memory.summary().revision, before)
  await memory.dispose()
})
test('repeated dispose shares a promise and waits for pending native persistence', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'world-memory-close-'))
  try {
    const c = column(); put(c, 4, 106, 14, 'nether_portal')
    const bot = fixture(new Map([['-6,1', c]]))
    const memory = createWorldMemory(bot, { directory, worldId: 'closing' })
    const first = memory.dispose(), second = memory.dispose()
    assert.equal(first, second)
    await second
    const saved = await readdir(path.join(directory, Buffer.from('closing').toString('base64url')))
    assert.equal(saved.filter(file => file.endsWith('.json.gz')).length, 1)
    const restored = createWorldMemory(fixture(new Map()), { directory, worldId: 'closing' })
    assert.equal((await restored.find({ name: 'nether_portal' })).total, 1)
    await restored.dispose()
  } finally { await rm(directory, { recursive: true, force: true }) }
})

test('received same-object native map_chunk invalidates indexes and persistence but ordinary synchronization reuses analysis', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'world-memory-reload-'))
  const bot = new EventEmitter(); bot._client = new EventEmitter()
  bot.version = '1.21.1'; bot.registry = registry; bot.supportFeature = registry.supportFeature
  bot.game = { dimension: 'overworld', minY: 80, height: 32 }
  require('mineflayer/lib/plugins/blocks')(bot, { version: '1.21.1' })
  bot._client.emit('login', { worldState: { dimension: 0, name: 'minecraft:overworld' } })
  const memory = createWorldMemory(bot, { directory, worldId: 'reload' })
  try {
    bot._client.emit('map_chunk', { x: 0, z: 0, chunkData: column().dump() })
    await memory.refresh()
    const original = bot.world.getColumn(0, 0), before = memory.summary().revision
    bot.emit('spawn'); await memory.refresh()
    assert.equal(memory.summary().revision, before)
    const updated = column(); put(updated, 2, 90, 3, 'nether_portal')
    bot._client.emit('map_chunk', { x: 0, z: 0, chunkData: updated.dump() })
    assert.equal(bot.world.getColumn(0, 0), original)
    assert.equal(memory.summary().pending, true)
    assert.equal((await memory.find({ name: 'nether_portal' })).total, 1)
    assert(memory.summary().revision > before)
    assert.equal(memory.summary().dimensions.overworld.chunkSummaries[0].surface.maxY, 90)
    await memory.dispose()
    const restored = createWorldMemory(fixture(new Map()), { directory, worldId: 'reload' })
    try { assert.equal((await restored.find({ name: 'nether_portal' })).total, 1) } finally { await restored.dispose() }
  } finally { await memory.dispose(); await rm(directory, { recursive: true, force: true }) }
})

test('restore corruption remains observable after successful analysis and later updates', async () => {
  const { mkdir, writeFile } = require('node:fs/promises')
  const directory = await mkdtemp(path.join(tmpdir(), 'world-memory-corrupt-'))
  const scope = path.join(directory, Buffer.from('corrupt').toString('base64url'))
  await mkdir(scope); await writeFile(path.join(scope, 'broken.json.gz'), 'invalid gzip')
  const c = column(), bot = fixture(new Map([['0,0', c]]))
  const memory = createWorldMemory(bot, { directory, worldId: 'corrupt' })
  try {
    await memory.refresh()
    assert.match(memory.summary().error, /Cannot restore broken.json.gz/)
    put(c, 2, 90, 3, 'dirt')
    bot.emit('blockUpdate', { stateId: 0 }, { stateId: registry.blocksByName.dirt.minStateId, position: { x: 2, y: 90, z: 3 } })
    assert.equal((await memory.find({ name: 'dirt' })).total, 1)
    assert.match(memory.summary().error, /Cannot restore broken.json.gz/)
    await memory.dispose()
    assert.match(memory.summary().error, /Cannot restore broken.json.gz/)
  } finally { await memory.dispose(); await rm(directory, { recursive: true, force: true }) }
})

test('dispose drains pending block updates before marking the final index current', async () => {
  const c = column(), bot = fixture(new Map([['0,0', c]])), memory = createWorldMemory(bot)
  await memory.refresh()
  put(c, 2, 90, 3, 'nether_portal')
  bot.emit('blockUpdate', { stateId: 0 }, { stateId: registry.blocksByName.nether_portal.minStateId, position: { x: 2, y: 90, z: 3 } })
  await memory.dispose()
  const summary = memory.summary(), found = await memory.find({ name: 'nether_portal' })
  assert.equal(summary.pending, false)
  assert.equal(summary.blockCounts.nether_portal, 1)
  assert.equal(summary.dimensions.overworld.loadedChunks, 0)
  assert.equal(found.total, 1); assert.equal(found.pending, false)
  assert.equal(found.positions[0].loaded, false)
})

'use strict'
// Synthetic-only: no server, saved world, or user coordinates are read.
// node benchmark/terrain.js [path/to/baseline-index.js]
const { EventEmitter } = require('node:events')
const { performance } = require('node:perf_hooks')
const path = require('node:path')
const registry = require('prismarine-registry')('1.21.1')
const Chunk = require('prismarine-chunk')('1.21.1')
const { createWorldMemory } = require(process.argv[2] ? path.resolve(process.argv[2]) : '..')
const median = values => values.sort((a, b) => a - b)[Math.floor(values.length / 2)]
async function run () {
  const fresh = [], cached = []; let reads = 0, cachedReads = 0
  for (let round = 0; round < 8; round++) {
    const columns = []
    let calls = 0
    for (let chunkX = 0; chunkX < 16; chunkX++) {
      const column = new Chunk({ minY: -64, worldHeight: 384 })
      for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) {
        column.setBlockStateId({ x, y: 60 + (x % 4), z }, registry.blocksByName.stone.minStateId)
      }
      const read = column.getBlockStateId.bind(column)
      column.getBlockStateId = p => { calls++; return read(p) }
      columns.push({ chunkX, chunkZ: 0, column })
    }
    const bot = new EventEmitter()
    bot.version = '1.21.1'; bot.registry = registry; bot.game = { dimension: 'overworld' }
    bot.world = { getColumns: () => columns }
    const memory = createWorldMemory(bot)
    let start = performance.now(); await memory.refresh(); const elapsed = performance.now() - start
    reads = calls
    start = performance.now(); for (let i = 0; i < 1000; i++) await memory.refresh()
    const cachedElapsed = performance.now() - start; cachedReads = calls - reads
    if (round) { fresh.push(elapsed); cached.push(cachedElapsed) }
    await memory.dispose()
  }
  console.log(JSON.stringify({ chunks: 16, height: 384, trials: 7, freshMedianMs: median(fresh), cached1000MedianMs: median(cached), nativeReads: reads, cachedNativeReads: cachedReads }))
}
run().catch(e => { console.error(e); process.exitCode = 1 })

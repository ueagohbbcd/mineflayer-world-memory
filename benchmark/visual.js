'use strict'
// Synthetic-only exporter benchmark. No assets, saved world, bot or network.
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), zlib = require('node:zlib')
const { performance } = require('node:perf_hooks')
const { openCache, surface, region } = require('../lib/visual')
const registry = require('prismarine-registry')('1.21.1'), Chunk = require('prismarine-chunk')('1.21.1')
async function main () {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'visual-benchmark-')), worldId = 'synthetic-benchmark'
  try {
    const scope = path.join(directory, Buffer.from(worldId).toString('base64url')); await fs.mkdir(scope)
    for (let x = 0; x < 4; x++) for (let z = 0; z < 4; z++) {
      const c = new Chunk()
      for (let lx = 0; lx < 16; lx++) for (let lz = 0; lz < 16; lz++) c.setBlockStateId({ x: lx, y: 64 + (lx % 4), z: lz }, registry.blocksByName.stone.defaultState)
      const data = { version: '1.21.1', dimension: 'overworld', x, z, observedAt: 0, json: c.toJson() }
      await fs.writeFile(path.join(scope, `${Buffer.from('overworld').toString('base64url')}_${x}_${z}.json.gz`), zlib.gzipSync(JSON.stringify(data)))
    }
    const config = { directory, worldId, version: '1.21.1' }
    let start = performance.now(); const s = await surface(await openCache(config), { bounds: [0, 63, 0, 63] }), surfaceMs = performance.now() - start
    start = performance.now(); const r = await region(await openCache(config), { min: [0, 60, 0], max: [63, 75, 63] }), regionMs = performance.now() - start
    console.log(JSON.stringify({ synthetic: true, chunks: 16, columns: s.cells.length, layers: s.totalLayers, samples: s.samples,
      surfaceMs, regionMs, regionVoxels: r.data.length / 2, maxRssKiB: process.resourceUsage().maxRSS }))
  } finally { await fs.rm(directory, { recursive: true, force: true }) }
}
main().catch(e => { console.error(e); process.exitCode = 1 })

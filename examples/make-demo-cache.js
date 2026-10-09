#!/usr/bin/env node
'use strict'
// Entirely synthetic. Never reads a real cache or connects to Minecraft.
const fs = require('node:fs/promises'), path = require('node:path'), zlib = require('node:zlib')
const registry = require('prismarine-registry')('1.21.1'), Chunk = require('prismarine-chunk')('1.21.1')
async function main () {
  if (process.argv.length !== 3) throw new Error('Usage: node examples/make-demo-cache.js NEW_DIRECTORY')
  const output = path.resolve(process.argv[2]); await fs.mkdir(output)
  const worldId = 'synthetic-demo', cache = path.join(output, 'cache', Buffer.from(worldId).toString('base64url'))
  await fs.mkdir(cache, { recursive: true })
  for (const cx of [-1, 0]) {
    const c = new Chunk({ minY: 0, worldHeight: 32 })
    const put = (x, y, z, name) => c.setBlockStateId({ x, y, z }, registry.blocksByName[name].defaultState)
    for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) {
      for (let y = 0; y < 12; y++) put(x, y, z, 'stone')
      put(x, 12, z, 'grass_block'); c.setBiome({ x, y: 12, z }, registry.biomesByName[x < 8 ? 'plains' : 'forest'].id)
      if (cx === -1 && x >= 2 && x <= 12 && z >= 4 && z <= 6) for (let y = 4; y <= 7; y++) put(x, y, z, 'air')
    }
    if (cx === 0) { put(3, 13, 3, 'oak_log'); put(3, 14, 3, 'oak_leaves'); put(6, 13, 5, 'short_grass') }
    const data = { version: '1.21.1', dimension: 'overworld', x: cx, z: 0, observedAt: 0, json: c.toJson() }
    await fs.writeFile(path.join(cache, `${Buffer.from('overworld').toString('base64url')}_${cx}_0.json.gz`), zlib.gzipSync(JSON.stringify(data)), { flag: 'wx' })
  }
  const common = { directory: './cache', worldId, version: '1.21.1', dimension: 'overworld' }
  await fs.writeFile(path.join(output, 'surface-config.json'), JSON.stringify({ ...common, bounds: [-16, 19, 0, 15], output: './surface.json' }, null, 2), { flag: 'wx' })
  await fs.writeFile(path.join(output, 'region-config.json'), JSON.stringify({ ...common, min: [-16, 2, 2], max: [-1, 10, 8], output: './region' }, null, 2), { flag: 'wx' })
  console.log(JSON.stringify({ output, synthetic: true, seed: [-10, 5, 5] }))
}
main().catch(e => { console.error(e.message); process.exitCode = 1 })

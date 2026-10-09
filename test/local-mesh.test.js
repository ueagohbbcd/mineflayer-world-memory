'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const { exportLocalMesh, readRegion, variants } = require('../lib/local-mesh')
const registry = require('prismarine-registry')('1.21.1')
const Block = require('prismarine-block')(registry)
let viewerRoot = process.env.WORLD_MEMORY_VIEWER_ROOT
if (!viewerRoot) { try { viewerRoot = path.dirname(require.resolve('prismarine-viewer/package.json')) } catch (_) {} }
async function fixture (t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'world-memory-local-'))
  t.after(() => fs.rm(root, { recursive: true, force: true }))
  const dir = path.join(root, 'region'); await fs.mkdir(dir)
  const names = ['stone', 'air', 'oak_stairs', 'air'], palette = ['unknown', ...new Set(names)], states = {}, u16 = Buffer.alloc(8), i32 = Buffer.alloc(16)
  names.forEach((name, i) => {
    const id = registry.blocksByName[name].defaultState, b = Block.fromStateId(id, 0)
    states[id] = { name, properties: b.getProperties() }
    u16.writeUInt16LE(palette.indexOf(name), i * 2); i32.writeInt32LE(id, i * 4)
  })
  const metadata = { schema: 'world-memory.region.v1', worldId: 'synthetic-fixture', dimension: 'overworld', version: '1.21.1', lo: [-1, -2, -1], hi: [0, -2, 0], shape: [2, 1, 2], palette, states, stamps: [], unknownIsAir: false, unknownVoxels: 0 }
  await fs.writeFile(path.join(dir, 'region.json'), JSON.stringify(metadata)); await fs.writeFile(path.join(dir, 'region.u16'), u16); await fs.writeFile(path.join(dir, 'states.i32'), i32)
  return { root, dir, metadata, i32 }
}
test('local region reader preserves known air and negative XYZ ordering', async t => {
  const f = await fixture(t), r = await readRegion(f.dir)
  assert.deepEqual(r.metadata.lo, [-1, -2, -1]); assert.equal(r.states.length, 4)
  assert.equal(r.states[1], registry.blocksByName.air.defaultState)
})
test('unknown or inconsistent voxels are rejected rather than filled as air', async t => {
  const f = await fixture(t)
  f.i32.writeInt32LE(-1, 4); await fs.writeFile(path.join(f.dir, 'states.i32'), f.i32)
  await assert.rejects(readRegion(f.dir), /Unknown or inconsistent/)
})
test('region symlinks and invalid buffers are rejected', async t => {
  const f = await fixture(t), input = path.join(f.dir, 'states.i32')
  await fs.writeFile(input, Buffer.alloc(1)); await assert.rejects(readRegion(f.dir), /length mismatch/)
  await fs.rename(input, path.join(f.root, 'external')); await fs.symlink(path.join(f.root, 'external'), input)
  await assert.rejects(readRegion(f.dir), /symbolic links/)
})
test('output cannot overwrite inputs, existing paths, or aliases into input', async t => {
  const f = await fixture(t)
  await assert.rejects(exportLocalMesh({ regionDir: f.dir, output: f.dir }), /outside/)
  await assert.rejects(exportLocalMesh({ regionDir: f.dir, output: f.root }), /already exists/)
  const alias = path.join(f.root, 'alias'); await fs.symlink(f.dir, alias)
  await assert.rejects(exportLocalMesh({ regionDir: f.dir, output: path.join(alias, 'mesh') }), /outside/)
})
test('region rejects malformed provenance, palette and state metadata', async t => {
  const f = await fixture(t)
  for (const change of [{ worldId: '' }, { dimension: '' }, { unknownIsAir: true }, { stamps: {} }, { palette: ['unknown', 'air', 'air'] }, { states: { 0: { name: 'air', properties: [] } } }]) {
    await fs.writeFile(path.join(f.dir, 'region.json'), JSON.stringify({ ...f.metadata, ...change }))
    await assert.rejects(readRegion(f.dir), /Invalid/)
  }
})
test('blockstates preserve stairs and compound multipart; absent assets reject', () => {
  const model = { elements: [] }
  assert.deepEqual(variants('oak_stairs', { facing: 'east' }, { oak_stairs: { variants: { 'facing=east': { model } } } }), [{ model }])
  assert.deepEqual(variants('air', {}, {}), [])
  assert.throws(() => variants('stone', {}, {}), /Missing model/)
  assert.equal(variants('fence', { east: true }, { fence: { multipart: [{ when: { AND: [{ east: 'true' }, { OR: [{ east: 'true' }] }] }, apply: { model } }] } }).length, 1)
})
test('official viewer emits nonempty geometry across negative chunk boundaries, without source changes', { skip: !viewerRoot }, async t => {
  const f = await fixture(t), before = await readRegion(f.dir), output = path.join(f.root, 'mesh')
  const result = await exportLocalMesh({ regionDir: f.dir, output, viewerRoot })
  assert(result.vertices > 24); assert(result.triangles > 12); assert.equal(result.sections, 4)
  const mesh = JSON.parse(await fs.readFile(path.join(output, 'mesh.json'))), meta = JSON.parse(await fs.readFile(path.join(output, 'metadata.json')))
  assert(mesh.positions.some(v => v < 0)); assert.equal(meta.unknownIsAir, false); assert.match(meta.boundary, /cut-away/)
  assert.deepEqual((await readRegion(f.dir)).hashes, before.hashes)
  await assert.rejects(exportLocalMesh({ regionDir: f.dir, output: path.join(viewerRoot, 'public', 'unused-test-output'), viewerRoot }), /outside/)
  await assert.rejects(exportLocalMesh({ regionDir: f.dir, output, viewerRoot }), /already exists/)
})

test('official viewer preserves fully known all-air ROI as empty mesh', { skip: !viewerRoot }, async t => {
  const f = await fixture(t), air = registry.blocksByName.air.defaultState
  for (let i = 0; i < 4; i++) f.i32.writeInt32LE(air, i * 4)
  const palette = Buffer.alloc(8); for (let i = 0; i < 4; i++) palette.writeUInt16LE(f.metadata.palette.indexOf('air'), i * 2)
  await fs.writeFile(path.join(f.dir, 'states.i32'), f.i32); await fs.writeFile(path.join(f.dir, 'region.u16'), palette)
  const result = await exportLocalMesh({ regionDir: f.dir, output: path.join(f.root, 'mesh'), viewerRoot })
  assert.equal(result.vertices, 0); assert.equal(result.triangles, 0)
})

test('mesh accumulation refuses oversized vertex/index budgets before appending', () => {
  const { checkMeshBudget } = require('../lib/local-mesh')
  checkMeshBudget(262144, 786432)
  assert.throws(() => checkMeshBudget(262145, 1), /geometry budget/)
  assert.throws(() => checkMeshBudget(1, 786433), /geometry budget/)
})

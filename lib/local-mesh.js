'use strict'
// Optional renderer adapter. Core imports do not load viewer or its assets.
const fs = require('node:fs/promises')
const path = require('node:path')
const crypto = require('node:crypto')
const { createRequire } = require('node:module')
const { safeOutput, boundsXYZ } = require('./visual')
const AIR = new Set(['air', 'cave_air', 'void_air'])
const MAX_VOXELS = 128 * 1024
const MAX_VERTICES = 262144, MAX_INDICES = 786432
function checkMeshBudget (vertices, indices) {
  if (vertices > MAX_VERTICES || indices > MAX_INDICES) throw new Error('Local mesh geometry budget exceeded; choose a smaller ROI')
}
const sha = b => crypto.createHash('sha256').update(b).digest('hex')
async function readRegion (regionDir) {
  const root = await fs.realpath(regionDir)
  const files = {}
  for (const name of ['region.json', 'region.u16', 'states.i32']) {
    const file = path.join(root, name)
    if ((await fs.lstat(file)).isSymbolicLink()) throw new Error('Region inputs must not be symbolic links')
    if ((await fs.stat(file)).size > 16 * 1024 * 1024) throw new Error('Region input too large')
    files[name] = await fs.readFile(file)
  }
  const m = JSON.parse(files['region.json'])
  if (m.schema !== 'world-memory.region.v1' || typeof m.version !== 'string' || !/^\d+\.\d+(\.\d+)?$/.test(m.version)) throw new Error('Unsupported region schema/version')
  if (typeof m.worldId !== 'string' || !m.worldId || typeof m.dimension !== 'string' || !m.dimension || m.unknownIsAir !== false || !Array.isArray(m.stamps)) throw new Error('Invalid region identity/provenance')
  if (!Array.isArray(m.palette) || m.palette[0] !== 'unknown' || m.palette.some(v => typeof v !== 'string' || !v) || new Set(m.palette).size !== m.palette.length) throw new Error('Invalid region palette')
  if (!m.states || typeof m.states !== 'object' || Array.isArray(m.states)) throw new Error('Invalid native state metadata')
  for (const [id, b] of Object.entries(m.states)) {
    if (!/^\d+$/.test(id) || !Number.isSafeInteger(Number(id)) || Number(id) > 2147483647 || !b || typeof b.name !== 'string' || !b.name || !b.properties || typeof b.properties !== 'object' || Array.isArray(b.properties)) throw new Error('Invalid native state metadata')
  }
  const shape = boundsXYZ(m.lo, m.hi), n = shape.reduce((a, b) => a * b)
  if (n > MAX_VOXELS) throw new Error(`Local mesh ROI exceeds ${MAX_VOXELS} voxels`)
  if (JSON.stringify(shape) !== JSON.stringify(m.shape) || !Array.isArray(m.palette) || !m.states) throw new Error('Invalid region layout')
  if (files['states.i32'].length !== n * 4 || files['region.u16'].length !== n * 2) throw new Error('Region buffer length mismatch')
  const states = new Int32Array(n)
  for (let i = 0; i < n; i++) {
    const id = files['states.i32'].readInt32LE(i * 4), p = files['region.u16'].readUInt16LE(i * 2)
    if (id < 0 || p === 0 || !m.states[id] || m.palette[p] !== m.states[id].name) throw new Error(`Unknown or inconsistent voxel inside ROI at index ${i}; export a fully known ROI`)
    states[i] = id
  }
  if (m.unknownVoxels !== 0) throw new Error('Region reports unknown voxels; no unknown-as-air rendering')
  return { root, metadata: m, states, hashes: Object.fromEntries(Object.entries(files).map(([k, v]) => [k, sha(v)])) }
}
function matches (condition, props) {
  if (!condition) return true
  if (typeof condition === 'string') condition = Object.fromEntries(condition.split(',').filter(Boolean).map(v => v.split('=')))
  return Object.entries(condition).every(([k, v]) => k === 'OR' ? v.some(c => matches(c, props)) : k === 'AND' ? v.every(c => matches(c, props)) : String(v).split('|').includes(String(props[k])))
}
function variants (name, props, models) {
  if (AIR.has(name)) return []
  const state = models[name]
  if (!state) throw new Error(`Missing model assets for ${name}`)
  const first = v => Array.isArray(v) ? v[0] : v
  let result = []
  if (state.variants) {
    const entry = Object.entries(state.variants).find(([cond]) => matches(cond, props))
    if (entry) result = [first(entry[1])]
  } else if (state.multipart) result = state.multipart.filter(v => matches(v.when, props)).map(v => first(v.apply))
  if (!result.length || result.some(v => !v?.model || !Array.isArray(v.model.elements))) throw new Error(`No supported model variant for ${name}`)
  return result
}
async function loadViewer (viewerRoot, assets, version) {
  let packageFile
  try { packageFile = viewerRoot ? path.join(await fs.realpath(viewerRoot), 'package.json') : require.resolve('prismarine-viewer/package.json') } catch (_) { throw new Error('Optional prismarine-viewer 1.33.0 is not installed; provide viewerRoot') }
  const pkg = JSON.parse(await fs.readFile(packageFile))
  if (pkg.name !== 'prismarine-viewer' || pkg.version !== '1.33.0') throw new Error('Local renderer requires prismarine-viewer 1.33.0')
  const root = path.dirname(packageFile), req = createRequire(packageFile)
  assets = await fs.realpath(assets || path.join(root, 'public'))
  const modelBytes = await fs.readFile(path.join(assets, 'blocksStates', `${version}.json`))
  const atlasBytes = await fs.readFile(path.join(assets, 'textures', `${version}.png`))
  return { getSectionGeometry: req('./viewer/lib/models').getSectionGeometry, Block: req('prismarine-block')(version), models: JSON.parse(modelBytes), assets, root,
    modelSha256: sha(modelBytes), atlasSha256: sha(atlasBytes) }
}
async function exportLocalMesh ({ regionDir, output, viewerRoot, assets } = {}) {
  const region = await readRegion(regionDir), m = region.metadata
  output = await safeOutput(output, region.root)
  const viewer = await loadViewer(viewerRoot, assets, m.version)
  // Viewer 1.33.0 suppresses neighbors below Y=0. Translate section-aligned
  // rendering coordinates, then restore absolute world Y in the mesh.
  const yOffset = Math.max(0, 16 - Math.floor(m.lo[1] / 16) * 16)
  await safeOutput(output, viewer.assets); await safeOutput(output, viewer.root)
  const sectionCount = m.hi.reduce((n, v, i) => n * (Math.floor(v / 16) - Math.floor(m.lo[i] / 16) + 1), 1)
  if (sectionCount > 512) throw new Error('Local mesh exceeds 512 sections; choose a smaller ROI')
  const templates = new Map()
  for (const id of new Set(region.states)) {
    const b = viewer.Block.fromStateId(id, 0)
    if (!b || b.name !== m.states[id].name || !matches(m.states[id].properties, b.getProperties())) throw new Error(`Native state registry mismatch for ${id}`)
    b.variant = variants(b.name, b.getProperties(), viewer.models)
    b.isCube = b.shapes.length === 1 && b.shapes[0].join(',') === '0,0,0,1,1,1'
    // Biomes are not exported by region.v1: fixed display tint, explicitly disclosed.
    b.biome = { name: 'plains' }
    templates.set(id, b)
  }
  const cut = viewer.Block.fromStateId(0, 0); cut.variant = []; cut.isCube = false; cut.biome = { name: 'plains' }
  const world = { getBlock (p) {
    const c = [Math.floor(p.x + 1e-7), Math.floor(p.y + 1e-7) - yOffset, Math.floor(p.z + 1e-7)]
    const outside = c.some((v, i) => v < m.lo[i] || v > m.hi[i])
    const i = ((c[0] - m.lo[0]) * m.shape[1] + c[1] - m.lo[1]) * m.shape[2] + c[2] - m.lo[2]
    const b = Object.create(outside ? cut : templates.get(region.states[i]))
    b.position = p.clone ? p.clone() : p
    return b
  } }
  const mesh = { schema: 'world-memory.local-mesh.v1', positions: [], normals: [], colors: [], uvs: [], indices: [] }
  let sections = 0
  for (let x = Math.floor(m.lo[0] / 16) * 16; x <= m.hi[0]; x += 16) for (let y = Math.floor(m.lo[1] / 16) * 16; y <= m.hi[1]; y += 16) for (let z = Math.floor(m.lo[2] / 16) * 16; z <= m.hi[2]; z += 16) {
    const g = viewer.getSectionGeometry(x, y + yOffset, z, world, viewer.models), offset = mesh.positions.length / 3
    checkMeshBudget(offset + g.positions.length / 3, mesh.indices.length + g.indices.length)
    for (let i = 0; i < g.positions.length; i++) mesh.positions.push(g.positions[i] + [g.sx, g.sy - yOffset, g.sz][i % 3])
    for (const key of ['normals', 'colors', 'uvs']) for (const v of g[key]) mesh[key].push(v)
    for (const i of g.indices) mesh.indices.push(i + offset)
    sections++; await new Promise(resolve => setImmediate(resolve))
  }
  const metadata = { schema: 'world-memory.local-mesh-metadata.v1', worldId: m.worldId, dimension: m.dimension, version: m.version, lo: m.lo, hi: m.hi, shape: m.shape,
    sourceHashes: region.hashes, stamps: m.stamps, assets: viewer.assets, modelSha256: viewer.modelSha256, atlasSha256: viewer.atlasSha256,
    renderer: 'prismarine-viewer 1.33.0 getSectionGeometry', viewerYOffset: yOffset, sections, vertices: mesh.positions.length / 3, triangles: mesh.indices.length / 3,
    unknownIsAir: false, boundary: 'ROI cut-away: synthetic empty exterior only for clipping; exposed cut faces do not establish actual air',
    lighting: 'Neutral display lighting and model ambient occlusion; not measured Minecraft light', tint: 'Fixed plains display biome; region.v1 does not export biome samples',
    limitations: ['Native complete cache remains source of truth; this is a derived inspection view, not a clearance or traversability proof.', 'Static blockstate models and first texture frame; no entities, block entities, particles or animation.', 'Viewer fluid and translucent rendering is approximate; no game-client fidelity claim.', 'Deterministic first weighted model variant; not the client random variant.'] }
  const meshJson = JSON.stringify(mesh)
  if (Buffer.byteLength(meshJson) > 32 * 1024 * 1024) throw new Error('Local mesh JSON exceeds 32 MiB; choose a smaller ROI')
  await fs.mkdir(path.dirname(output), { recursive: true }); await fs.mkdir(output)
  await fs.writeFile(path.join(output, 'mesh.json'), meshJson, { flag: 'wx' })
  await fs.writeFile(path.join(output, 'metadata.json'), JSON.stringify(metadata, null, 2), { flag: 'wx' })
  return { output, vertices: metadata.vertices, triangles: metadata.triangles, sections }
}
module.exports = { exportLocalMesh, readRegion, matches, variants, checkMeshBudget }

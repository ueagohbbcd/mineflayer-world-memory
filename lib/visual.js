'use strict'
// Read-only, bounded views over the native cache. No bot, sockets or summary pass.
const fs = require('node:fs/promises')
const path = require('node:path')
const zlib = require('node:zlib')
const crypto = require('node:crypto')
const { promisify } = require('node:util')
const gunzip = promisify(zlib.gunzip)
const AIR = new Set(['air', 'cave_air', 'void_air'])
const NATURAL = new Set('grass_block dirt coarse_dirt rooted_dirt podzol mycelium moss_block stone andesite diorite granite sand red_sand sandstone gravel clay snow_block snow terracotta calcite tuff deepslate'.split(' '))
const MAX_COLUMNS = 256 * 1024, MAX_VOXELS = 8 * 1024 * 1024
function integers (values, length, label) {
  if (!Array.isArray(values) || values.length !== length || !values.every(Number.isSafeInteger)) throw new Error(`${label} must contain ${length} safe integers`)
  if (values.some(v => Math.abs(v) > 30000000)) throw new Error(`${label} exceeds supported coordinate bounds`)
  return values
}
function boundsXZ (bounds) {
  const [x0, x1, z0, z1] = integers(bounds, 4, 'bounds [xMin,xMax,zMin,zMax]')
  if (x0 > x1 || z0 > z1 || (x1 - x0 + 1) * (z1 - z0 + 1) > MAX_COLUMNS) throw new Error(`bounds must be ordered and contain at most ${MAX_COLUMNS} columns`)
  return bounds
}
function boundsXYZ (lo, hi) {
  integers(lo, 3, 'min'); integers(hi, 3, 'max')
  const shape = hi.map((v, i) => v - lo[i] + 1)
  if (shape.some(v => v < 1) || shape.reduce((a, b) => a * b, 1) > MAX_VOXELS) throw new Error(`region must be ordered and contain at most ${MAX_VOXELS} voxels`)
  return shape
}
async function canonical (p) {
  p = path.resolve(p)
  try { return await fs.realpath(p) } catch (e) {
    if (e.code !== 'ENOENT') throw e
    const parent = path.dirname(p)
    if (parent === p) throw e
    return path.join(await canonical(parent), path.basename(p))
  }
}
function within (child, parent) { const r = path.relative(parent, child); return r === '' || (!r.startsWith('..' + path.sep) && r !== '..' && !path.isAbsolute(r)) }
async function safeOutput (output, source) {
  if (typeof output !== 'string' || !output) throw new Error('output is required')
  const resolved = await canonical(output)
  if (source && within(resolved, await canonical(source))) throw new Error('Output must be outside the source cache directory')
  try { await fs.lstat(output); throw new Error('Output already exists; choose a new file or directory') } catch (e) { if (e.code !== 'ENOENT') throw e }
  return resolved
}
async function openCache ({ directory, worldId, dimension = 'overworld', version, registry } = {}) {
  if (typeof directory !== 'string' || !directory || typeof worldId !== 'string' || !worldId || typeof version !== 'string' || !version || typeof dimension !== 'string' || !dimension) throw new Error('directory, worldId, version and dimension must be nonempty strings')
  const root = await fs.realpath(directory), scope = await fs.realpath(path.join(root, Buffer.from(worldId).toString('base64url')))
  if (!within(scope, root)) throw new Error('World cache scope escapes directory')
  registry = registry || require('prismarine-registry')(version)
  if (registry.version?.minecraftVersion !== version || registry.version?.type !== 'pc') throw new Error('Registry Minecraft version/type does not match requested cache version')
  const Chunk = require('prismarine-chunk')(registry), Block = require('prismarine-block')(registry)
  const records = new Map(), states = {}
  async function chunk (x, z) {
    const key = `${x},${z}`
    if (records.has(key)) return records.get(key)
    const file = path.join(scope, `${Buffer.from(dimension).toString('base64url')}_${x}_${z}.json.gz`)
    let bytes
    try {
      if (!within(await fs.realpath(file), scope)) throw new Error('Chunk path escapes world scope')
      const stat = await fs.stat(file)
      if (stat.size > 32 * 1024 * 1024) throw new Error('Compressed chunk exceeds 32 MiB')
      bytes = await fs.readFile(file)
    } catch (e) { if (e.code === 'ENOENT') { records.set(key, null); return null }; throw e }
    const data = JSON.parse((await gunzip(bytes, { maxOutputLength: 32 * 1024 * 1024 })).toString())
    if (data.version !== version || data.dimension !== dimension || data.x !== x || data.z !== z) throw new Error(`Cache identity/version mismatch in ${path.basename(file)}`)
    if (typeof data.json !== 'string') throw new Error('Cache json must be serialized native chunk data')
    const raw = JSON.parse(data.json)
    if (!Number.isSafeInteger(raw.minY) || !Number.isSafeInteger(raw.worldHeight) || raw.worldHeight <= 0 || raw.worldHeight > 4096 || raw.worldHeight % 16 || raw.minY % 16 || Math.abs(raw.minY) > 30000000 || !Array.isArray(raw.sections) || !Array.isArray(raw.biomes)) throw new Error('Unsupported or invalid native chunk schema')
    const n = raw.worldHeight / 16
    if (raw.sections.length > n || raw.biomes.length > n) throw new Error('Native section count exceeds height')
    // Native fromJson expects non-null palette sections. Placeholders are used only
    // to decode available sections; original masks below always gate every read.
    const defaults = JSON.parse(new Chunk({ minY: raw.minY, worldHeight: raw.worldHeight }).toJson())
    const decoded = { ...defaults, ...raw,
      sections: Array.from({ length: n }, (_, i) => raw.sections[i] ?? defaults.sections[i]),
      biomes: Array.from({ length: n }, (_, i) => raw.biomes[i] ?? defaults.biomes[i]) }
    const column = Chunk.fromJson(JSON.stringify(decoded))
    if (data.observedAt != null && (typeof data.observedAt !== 'number' || !Number.isFinite(data.observedAt) || data.observedAt < 0)) throw new Error('Invalid chunk observation timestamp')
    const record = { column, raw, x, z, observedAt: data.observedAt ?? null }
    records.set(key, record)
    record.stamp = { x, z, observedAt: record.observedAt, sha256: crypto.createHash('sha256').update(bytes).digest('hex') }
    return record
  }
  function stateAt (r, x, y, z) {
    if (!r || y < r.raw.minY || y >= r.raw.minY + r.raw.worldHeight || r.raw.sections[Math.floor((y - r.raw.minY) / 16)] == null) return null
    const p = { x: ((x % 16) + 16) % 16, y, z: ((z % 16) + 16) % 16 }
    const id = r.column.getBlockStateId(p)
    if (!Number.isInteger(id) || !registry.blocksByStateId[id]) return null
    if (!states[id]) {
      const b = Block.fromStateId(id, 0)
      states[id] = { name: b.name, properties: b.getProperties(), transparent: b.transparent, shapes: b.shapes }
    }
    return id
  }
  function biomeAt (r, x, y, z) {
    const section = Math.floor((y - r.raw.minY) / 16)
    if (r.raw.biomes[section] == null) return null
    const id = r.column.getBiome({ x: ((x % 16) + 16) % 16, y, z: ((z % 16) + 16) % 16 })
    return Number.isInteger(id) && id >= 0 ? id : null
  }
  return { root, worldId, dimension, version, registry, chunk, stateAt, biomeAt, states, release: (x, z) => records.delete(`${x},${z}`) }
}
async function surface (cache, { bounds, y, maxLayers = 18, maxSamples = 128 * 1024 * 1024, maxTotalLayers = 1024 * 1024 } = {}) {
  const [X0, X1, Z0, Z1] = boundsXZ(bounds)
  if (y !== undefined) { integers(y, 2, 'y [min,max]'); if (y[0] > y[1] || y[1] - y[0] >= 4096) throw new Error('Invalid y range') }
  if (!Number.isInteger(maxLayers) || maxLayers < 1 || maxLayers > 64) throw new Error('maxLayers must be 1..64')
  if (!Number.isInteger(maxSamples) || maxSamples < 1 || maxSamples > 128 * 1024 * 1024 || !Number.isInteger(maxTotalLayers) || maxTotalLayers < 1 || maxTotalLayers > 1024 * 1024) throw new Error('Invalid surface sample/layer budget')
  let samples = 0, totalLayers = 0
  const read = (r, x, y, z) => { if (++samples > maxSamples) throw new Error('Surface sample budget exceeded; select a smaller area or y range'); return cache.stateAt(r, x, y, z) }
  const cells = [], columnStatus = [], biomeRegistry = {}, viewStates = {}, stamps = []
  for (let cx = Math.floor(X0 / 16); cx <= Math.floor(X1 / 16); cx++) for (let cz = Math.floor(Z0 / 16); cz <= Math.floor(Z1 / 16); cz++) {
    const r = await cache.chunk(cx, cz)
    if (r) stamps.push(r.stamp)
    for (let x = Math.max(X0, cx * 16); x <= Math.min(X1, cx * 16 + 15); x++) for (let z = Math.max(Z0, cz * 16); z <= Math.min(Z1, cz * 16 + 15); z++) {
      if (!r) { columnStatus.push([x, z, 'unknown']); continue }
      const bottom = y?.[0] ?? r.raw.minY, top = y?.[1] ?? r.raw.minY + r.raw.worldHeight - 1
      const layers = []; let status = 'empty', ground = null, biome = null, truncated = false
      for (let height = top; height >= bottom; height--) {
        const id = read(r, x, height, z)
        if (id === null) { status = layers.length ? 'partial' : 'unknown'; break }
        const b = cache.states[id]
        if (AIR.has(b.name)) continue
        if (++totalLayers > maxTotalLayers) throw new Error('Surface layer budget exceeded; select a smaller area or fewer layers')
        layers.push([height, id]); viewStates[id] = cache.states[id]; status = 'surface'
        if (b.name === 'water' || b.name === 'lava' || (!b.transparent && b.shapes?.some(s => s.join(',') === '0,0,0,1,1,1'))) break
        if (layers.length === maxLayers) { truncated = true; break }
      }
      if (layers.length) {
        biome = cache.biomeAt(r, x, layers[0][0], z)
        if (biome !== null) biomeRegistry[biome] = cache.registry.biomes[biome] || { id: biome, name: null }
        for (let height = layers[0][0]; height >= bottom; height--) {
          const id = read(r, x, height, z); if (id === null) break
          const name = cache.states[id].name
          if (NATURAL.has(name) || name.endsWith('_ore')) { ground = height; break }
        }
      }
      cells.push([x, z, layers, biome, ground]); columnStatus.push([x, z, status, truncated])
    }
    cache.release(cx, cz)
    await new Promise(resolve => setImmediate(resolve))
  }
  return { schema: 'world-memory.surface-map.v1', X0, X1, Z0, Z1, worldId: cache.worldId, dimension: cache.dimension, version: cache.version, y: y || null, cells, columnStatus,
    states: viewStates, chunks: stamps, biomeRegistry, maxLayers, samples, totalLayers,
    orientation: 'north=-Z (up), east=+X (right)', biomeSampling: 'highest visible block Y; native 4x4x4 palette, no client smoothing',
    lighting: 'display hillshade only; not measured game light', unknownIsAir: false }
}
async function region (cache, { min, max } = {}) {
  const shape = boundsXYZ(min, max), size = shape.reduce((a, b) => a * b, 1)
  const data = Buffer.alloc(size * 2), stateData = Buffer.alloc(size * 4, 255), palette = ['unknown'], paletteIds = new Map([['unknown', 0]])
  let unknownVoxels = 0
  const viewStates = {}, stamps = []
  boundsXZ([min[0], max[0], min[2], max[2]])
  for (let cx = Math.floor(min[0] / 16); cx <= Math.floor(max[0] / 16); cx++) for (let cz = Math.floor(min[2] / 16); cz <= Math.floor(max[2] / 16); cz++) {
    const r = await cache.chunk(cx, cz)
    if (r) stamps.push(r.stamp)
    for (let x = Math.max(min[0], cx * 16); x <= Math.min(max[0], cx * 16 + 15); x++) for (let z = Math.max(min[2], cz * 16); z <= Math.min(max[2], cz * 16 + 15); z++) {
      for (let y = min[1]; y <= max[1]; y++) {
        const i = ((x - min[0]) * shape[1] + y - min[1]) * shape[2] + z - min[2], state = cache.stateAt(r, x, y, z)
        if (state === null) { unknownVoxels++; continue }
        viewStates[state] = cache.states[state]
        const name = cache.states[state].name
        if (!paletteIds.has(name)) { if (palette.length >= 65536) throw new Error('Too many block names'); paletteIds.set(name, palette.length); palette.push(name) }
        data.writeUInt16LE(paletteIds.get(name), i * 2); stateData.writeInt32LE(state, i * 4)
      }
    }
    cache.release(cx, cz)
    await new Promise(resolve => setImmediate(resolve))
  }
  return { data, stateData, metadata: { schema: 'world-memory.region.v1', worldId: cache.worldId, dimension: cache.dimension, version: cache.version,
    lo: min, hi: max, shape, palette, states: viewStates, stamps, unknownVoxels, unknownIsAir: false,
    storage: 'region.u16: LE uint16 name palette; states.i32: LE int32 native states (-1 unknown); XYZ order, Z fastest', lighting: 'not exported; unknown is not darkness' } }
}
async function exportSurface (config) {
  const cache = await openCache(config), output = await safeOutput(config.output, cache.root)
  const result = await surface(cache, config)
  await fs.mkdir(path.dirname(output), { recursive: true }); await fs.writeFile(output, JSON.stringify(result), { flag: 'wx' })
  return { output, schema: result.schema, columns: result.columnStatus.length, chunks: result.chunks.length, next: 'world-memory render-surface --input <output> --assets <viewer-public> --output <new-map.png>' }
}
async function exportRegion (config) {
  const cache = await openCache(config), output = await safeOutput(config.output, cache.root), result = await region(cache, config)
  await fs.mkdir(path.dirname(output), { recursive: true }); await fs.mkdir(output)
  await fs.writeFile(path.join(output, 'region.u16'), result.data, { flag: 'wx' })
  await fs.writeFile(path.join(output, 'states.i32'), result.stateData, { flag: 'wx' })
  await fs.writeFile(path.join(output, 'region.json'), JSON.stringify(result.metadata), { flag: 'wx' })
  return { output, schema: result.metadata.schema, shape: result.metadata.shape, unknownVoxels: result.metadata.unknownVoxels, next: 'world-memory cave --region-dir <output> --output-dir <new-cast-dir> --min X Y Z --max X Y Z --seed X Y Z' }
}
module.exports = { openCache, surface, region, exportSurface, exportRegion, safeOutput, boundsXZ, boundsXYZ }

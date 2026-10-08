'use strict'
const fs = require('node:fs/promises')
const path = require('node:path')
const zlib = require('node:zlib')
const { promisify } = require('node:util')
const gzip = promisify(zlib.gzip), gunzip = promisify(zlib.gunzip)
const yieldTurn = () => new Promise(resolve => setImmediate(resolve))
const key = (x, z) => `${x},${z}`

function coverageRectangles (coordinates) {
  function runs (swap) {
    const rows = new Map(), result = [], active = new Map()
    for (const { x, z } of coordinates) {
      const a = swap ? z : x, b = swap ? x : z
      if (!rows.has(b)) rows.set(b, new Set())
      rows.get(b).add(a)
    }
    let previous
    for (const b of [...rows.keys()].sort((a, b) => a - b)) {
      if (b !== previous + 1) active.clear()
      const sorted = [...rows.get(b)].sort((a, b) => a - b), next = new Map()
      for (let i = 0; i < sorted.length;) {
        const start = sorted[i++]; let end = start
        while (sorted[i] === end + 1) end = sorted[i++]
        const id = `${start}:${end}`
        let r = active.get(id)
        if (r) r.depth++
        else { r = { x: start, z: b, width: end - start + 1, depth: 1 }; result.push(r) }
        next.set(id, r)
      }
      active.clear(); for (const [id, r] of next) active.set(id, r)
      previous = b
    }
    return result.map(r => swap ? { x: r.z * 16, z: r.x * 16, width: r.depth, depth: r.width } : { ...r, x: r.x * 16, z: r.z * 16 })
  }
  const a = runs(false), b = runs(true)
  return b.length < a.length ? b : a
}

async function classify (column, registry) {
  const minY = column.minY ?? -64, height = column.worldHeight ?? 384, n = height * 256
  const types = new Uint16Array(n), labels = new Uint32Array(n), transparent = new Uint8Array(n)
  const p = { x: 0, y: 0, z: 0 }
  for (let i = 0; i < n; i++) {
    p.x = i & 15; p.z = (i >> 4) & 15; p.y = (i >> 8) + minY
    const state = column.getBlockStateId(p), block = registry.blocksByStateId[state]
    types[i] = block?.id ?? 65535
    transparent[i] = block && (block.transparent === true || block.boundingBox === 'empty') ? 1 : 0
    if ((i & 4095) === 4095) await yieldTurn()
  }
  const queue = new Uint32Array(n), sky = []; let components = 0
  for (let i = 0; i < n; i++) {
    if (!transparent[i] || labels[i]) continue
    const label = ++components; let head = 0, tail = 1; queue[0] = i; labels[i] = label; let top = false
    while (head < tail) {
      const v = queue[head++], x = v & 15, z = (v >> 4) & 15
      if (v >= n - 256) top = true
      const visit = j => { if (transparent[j] && !labels[j]) { labels[j] = label; queue[tail++] = j } }
      if (x) visit(v - 1); if (x < 15) visit(v + 1)
      if (z) visit(v - 16); if (z < 15) visit(v + 16)
      if (v >= 256) visit(v - 256); if (v < n - 256) visit(v + 256)
      if ((head & 4095) === 0) await yieldTurn()
    }
    if (top) sky.push(label)
  }
  return { minY, height, types, labels, components, sky }
}

function createWorldMemory (bot, options = {}) {
  if (options.directory && !options.worldId) throw new Error('worldId is required when persistence is enabled')
  const dimensions = new Map(), listeners = [], now = options.now || Date.now
  const directory = options.directory && path.join(options.directory, Buffer.from(String(options.worldId)).toString('base64url'))
  let revision = 0, computedRevision = -1, running, disposed = false, error = null, loadedDimension, scheduled, closing
  let snapshot = { revision: 0, dimensions: {}, blockCounts: {}, pending: true }
  const dimension = () => String(bot.game?.dimension || 'overworld')
  const map = d => { if (!dimensions.has(d)) dimensions.set(d, new Map()); return dimensions.get(d) }
  function schedule () {
    if (disposed || scheduled) return
    scheduled = setImmediate(() => { scheduled = null; if (!disposed) refresh().catch(() => {}) })
  }
  function capture (x, z, column) {
    if (!column) return
    const d = dimension(), records = map(d), id = key(x, z), old = records.get(id)
    if (old?.column === column && old.loaded) return
    records.set(id, { x, z, column, loaded: true, observedAt: now(), dirty: true, analysis: null, generation: 0 })
    revision++; schedule()
  }
  function synchronize () {
    const d = dimension()
    if (loadedDimension !== d) {
      for (const records of dimensions.values()) for (const r of records.values()) r.loaded = false
      loadedDimension = d; revision++
    }
    for (const c of bot.world?.getColumns?.() || []) capture(Number(c.chunkX), Number(c.chunkZ), c.column)
  }
  function listen (event, fn) { bot.on(event, fn); listeners.push([event, fn]) }
  listen('chunkColumnLoad', p => { synchronize(); capture(Math.floor(p.x / 16), Math.floor(p.z / 16), bot.world?.getColumnAt(p)) })
  listen('chunkColumnUnload', p => { const r = map(dimension()).get(key(Math.floor(p.x / 16), Math.floor(p.z / 16))); if (r?.loaded) { r.loaded = false; revision++; schedule() } })
  listen('blockUpdate', (oldBlock, block) => {
    if (!block?.position || oldBlock?.stateId === block.stateId) return
    const p = block.position, r = map(dimension()).get(key(Math.floor(p.x / 16), Math.floor(p.z / 16)))
    if (r) { r.generation = (r.generation || 0) + 1; r.analysis = null; r.dirty = true; r.observedAt = now(); revision++; schedule() }
  })
  listen('spawn', synchronize)
  listen('end', () => { dispose().catch(e => { error = e.message }) })
  const ready = (async () => {
    if (directory) {
      await fs.mkdir(directory, { recursive: true })
      for (const file of await fs.readdir(directory)) {
        if (!file.endsWith('.json.gz')) continue
        try {
          const data = JSON.parse((await gunzip(await fs.readFile(path.join(directory, file)))).toString())
          if (data.version !== bot.version) continue
          const Constructor = bot.world?.getColumns?.()[0]?.column?.constructor || require('prismarine-chunk')(bot.version)
          if (!map(data.dimension).get(key(data.x, data.z))?.loaded) map(data.dimension).set(key(data.x, data.z), { ...data, column: Constructor.fromJson(data.json), loaded: false, dirty: false, analysis: null })
          revision++
        } catch (e) { error = `Cannot restore ${file}: ${e.message}` }
        await yieldTurn()
      }
    }
    synchronize(); schedule()
  })()
  async function persist () {
    if (!directory) return
    for (const [d, records] of dimensions) for (const r of records.values()) {
      if (!r.dirty) continue
      const generation = r.generation
      const data = { dimension: d, x: r.x, z: r.z, version: bot.version, observedAt: r.observedAt, json: r.column.toJson() }
      const filename = path.join(directory, `${Buffer.from(d).toString('base64url')}_${r.x}_${r.z}.json.gz`)
      const bytes = await gzip(JSON.stringify(data)); await fs.writeFile(filename + '.tmp', bytes); await fs.rename(filename + '.tmp', filename)
      if (generation === r.generation) r.dirty = false
      await yieldTurn()
    }
  }
  async function compute () {
    await ready
    while (computedRevision !== revision) {
      const target = revision, output = {}, total = {}
      for (const [d, liveRecords] of dimensions) {
        const records = new Map()
        for (const [id, r] of liveRecords) {
          let analysis = r.analysis
          if (!r.analysis) {
            const generation = r.generation
            analysis = await classify(r.column, bot.registry)
            if (generation === r.generation) r.analysis = analysis
          }
          records.set(id, { ...r, analysis })
        }
        let count = 1
        for (const r of records.values()) { r.offset = count; count += r.analysis.components }
        const parents = new Uint32Array(count), exposed = new Uint8Array(count)
        for (let i = 0; i < count; i++) parents[i] = i
        const root = i => { while (parents[i] !== i) { parents[i] = parents[parents[i]]; i = parents[i] } return i }
        const join = (a, b) => { if (a && b) parents[root(a)] = root(b) }
        const component = (r, i) => r.analysis.labels[i] ? r.offset + r.analysis.labels[i] - 1 : 0
        for (const r of records.values()) {
          const a = r.analysis
          for (const [dx, dz] of [[1, 0], [0, 1]]) {
            const neighbor = records.get(key(r.x + dx, r.z + dz)); if (!neighbor) continue
            const b = neighbor.analysis
            for (let y = Math.max(a.minY, b.minY); y < Math.min(a.minY + a.height, b.minY + b.height); y++) for (let v = 0; v < 16; v++) {
              const i = (y - a.minY) * 256 + (dx ? v * 16 + 15 : 240 + v)
              const j = (y - b.minY) * 256 + (dx ? v * 16 : v)
              join(component(r, i), component(neighbor, j))
            }
          }
          await yieldTurn()
        }
        for (const r of records.values()) for (const label of r.analysis.sky) exposed[root(r.offset + label - 1)] = 1
        const counts = {}, coordinates = []
        for (const r of records.values()) {
          coordinates.push({ x: r.x, z: r.z }); const a = r.analysis, lists = new Map(), airRuns = []
          const peers = [r, ...[[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dz]) => records.get(key(r.x + dx, r.z + dz)))]
          const references = peers.map(peer => peer?.analysis)
          const signature = []
          for (const peer of peers) {
            signature.push(peer?.analysis.components ?? -1)
            if (peer) for (let label = 1; label <= peer.analysis.components; label++) signature.push(exposed[root(peer.offset + label - 1)])
          }
          const unchanged = r.exposureReferences?.every((ref, i) => ref === references[i]) && r.exposureSignature?.length === signature.length && r.exposureSignature.every((v, i) => v === signature[i])
          if (unchanged && r.exposedCounts) {
            for (const [name, value] of Object.entries(r.exposedCounts)) counts[name] = (counts[name] || 0) + value
            await yieldTurn(); continue
          }
          const chunkCounts = {}
          const visible = (x, y, z) => {
            const other = records.get(key(r.x + Math.floor(x / 16), r.z + Math.floor(z / 16)))
            if (!other || y < other.analysis.minY || y >= other.analysis.minY + other.analysis.height) return false
            const i = (y - other.analysis.minY) * 256 + ((z + 16) % 16) * 16 + ((x + 16) % 16), c = component(other, i)
            return c && exposed[root(c)]
          }
          for (let i = 0; i < a.types.length; i++) {
            const type = a.types[i]; if (type === 65535) continue
            const x = i & 15, z = (i >> 4) & 15, y = (i >> 8) + a.minY, c = component(r, i)
            if (!(c ? exposed[root(c)] : (y === a.minY + a.height - 1 || visible(x - 1, y, z) || visible(x + 1, y, z) || visible(x, y - 1, z) || visible(x, y + 1, z) || visible(x, y, z - 1) || visible(x, y, z + 1)))) continue
            const name = bot.registry.blocks[type]?.name || String(type)
            counts[name] = (counts[name] || 0) + 1
            chunkCounts[name] = (chunkCounts[name] || 0) + 1
            if (type === 0) {
              const last = airRuns.length - 2
              if (last >= 0 && airRuns[last] + airRuns[last + 1] === i) airRuns[last + 1]++
              else airRuns.push(i, 1)
            } else {
              if (!lists.has(name)) lists.set(name, [])
              lists.get(name).push(i)
            }
            if ((i & 4095) === 0) await yieldTurn()
          }
          r.positions = new Map([...lists].map(([name, indices]) => [name, Uint32Array.from(indices)]))
          const original = liveRecords.get(key(r.x, r.z))
          if (original) {
            original.positions = r.positions; original.positionsMinY = a.minY; original.airRuns = Uint32Array.from(airRuns)
            original.exposedCounts = chunkCounts; original.exposureReferences = references; original.exposureSignature = signature
          }
          await yieldTurn()
        }
        for (const [name, value] of Object.entries(counts)) total[name] = (total[name] || 0) + value
        const current = [...records.values()].filter(r => r.loaded)
        output[d] = { blockCounts: counts, coverage: coverageRectangles(coordinates), currentCoverage: coverageRectangles(current), chunks: records.size, loadedChunks: current.length, lastObservedAt: Math.max(0, ...[...records.values()].map(r => r.observedAt)) }
      }
      snapshot = { revision: target, dimensions: output, blockCounts: total }; computedRevision = target
      await persist()
      error = null
      break
    }
    return summary()
  }
  function refresh () {
    if (!running) running = compute().catch(e => { error = e.message; throw e }).finally(() => { running = null; if (computedRevision !== revision && !error) schedule() })
    return running
  }
  function summary () { return { entities: options.entities?.() || [], blockCounts: snapshot.blockCounts, dimensions: snapshot.dimensions, revision: snapshot.revision, currentDimension: dimension(), pending: computedRevision !== revision, error } }
  async function find ({ name, dimension: requested = dimension(), limit = 100 } = {}) {
    await refresh(); const positions = []; let total = 0
    limit = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : Infinity
    for (const r of map(requested).values()) {
      total += r.exposedCounts?.[name] || 0
      const append = i => positions.push({ x: r.x * 16 + (i & 15), y: r.positionsMinY + (i >> 8), z: r.z * 16 + ((i >> 4) & 15), dimension: requested, loaded: r.loaded, observedAt: r.observedAt })
      if (name === 'air') {
        for (let run = 0; run < (r.airRuns?.length || 0) && positions.length < limit; run += 2) {
          const start = r.airRuns[run], end = start + r.airRuns[run + 1]
          for (let i = start; i < end && positions.length < limit; i++) append(i)
        }
      } else for (const i of r.positions?.get(name) || []) {
        if (positions.length >= limit) break
        append(i)
      }
    }
    return { positions, total, remaining: total - positions.length, revision: computedRevision, pending: computedRevision !== revision }
  }
  function dispose () {
    if (closing) return closing
    disposed = true; if (scheduled) clearImmediate(scheduled)
    for (const [e, fn] of listeners) bot.off(e, fn)
    closing = (async () => {
      await ready; if (running) await running; await persist()
      let changed = false
      for (const records of dimensions.values()) for (const r of records.values()) if (r.loaded) { r.loaded = false; changed = true }
      if (changed) revision++
      snapshot = { ...snapshot, revision, dimensions: Object.fromEntries(Object.entries(snapshot.dimensions).map(([d, facts]) => [d, { ...facts, loadedChunks: 0, currentCoverage: [] }])) }
      computedRevision = revision
    })()
    return closing
  }
  return { summary, refresh, find, chunk: ({ x, z, dimension: d = dimension() }) => map(d).get(key(x, z))?.column || null,
    block: ({ x, y, z, dimension: d = dimension() }) => {
      const column = map(d).get(key(Math.floor(x / 16), Math.floor(z / 16)))?.column
      if (!column) return null
      if (y < (column.minY ?? -64) || y >= (column.minY ?? -64) + (column.worldHeight ?? 384)) return null
      const block = column.getBlock({ x: ((x % 16) + 16) % 16, y, z: ((z % 16) + 16) % 16 })
      block.position = { x, y, z }; return block
    },
    dispose }
}
module.exports = { createWorldMemory, coverageRectangles }

#!/usr/bin/env node
'use strict'
const fs = require('node:fs/promises')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { exportSurface, exportRegion } = require('../lib/visual')
const help = `World memory: look at a broad surface map, inspect a bounded volume, query only as needed.

world-memory surface --config surface.json     Export cached top textures/biomes (no grid)
world-memory region --config region.json       Export known/unknown XYZ volume for 3D/caves
world-memory local-mesh --config mesh.json      Optional Prismarine model mesh of a known region
world-memory render-local --mesh-dir DIR --output NEW.png  CPU textured local 3D
world-memory render-surface --input FILE --assets VIEWER_PUBLIC --output NEW.png [--biomes]
world-memory cave --region-dir DIR --output-dir NEW_DIR --min X Y Z --max X Y Z --seed X Y Z
world-memory render-cave --workdir CAST_DIR [--config FILE]  Optional Blender two-view white air cast
world-memory compose-cave --workdir CAST_DIR              Combine annotated cave views

Exports are read-only and refuse existing outputs or outputs inside the cache.
Cache config: directory, worldId, version, dimension, output; surface: bounds [xMin,xMax,zMin,zMax],
optional y [min,max], maxLayers; region: min/max [X,Y,Z] inclusive. Paths resolve relative to config.
Python renderers require NumPy/Pillow. Blender is optional for render-cave. No automatic downloads.
Use <command> --help for renderer flags; see docs/visual.md for installation and complete examples.`
async function main () {
  const [command, ...args] = process.argv.slice(2)
  if (!command || command === '--help' || command === 'help') { console.log(help); return }
  if (['surface', 'region', 'local-mesh'].includes(command)) {
    if (args.length === 1 && args[0] === '--help') { console.log(help); return }
    if (args.length !== 2 || args[0] !== '--config') throw new Error('Expected --config FILE; use world-memory --help')
    const configPath = path.resolve(args[1]), config = JSON.parse(await fs.readFile(configPath, 'utf8'))
    for (const key of ['directory', 'output', 'regionDir', 'viewerRoot', 'assets']) if (typeof config[key] === 'string') config[key] = path.resolve(path.dirname(configPath), config[key])
    const exporter = command === 'surface' ? exportSurface : command === 'region' ? exportRegion : require('../lib/local-mesh').exportLocalMesh
    console.log(JSON.stringify(await exporter(config)))
    return
  }
  const scripts = { 'render-local': 'local_texture.py', 'render-surface': 'surface.py', cave: 'extract_air_cast.py', 'render-cave': 'render_air_cast.py', 'compose-cave': 'compose_air_cast.py' }
  if (!scripts[command]) throw new Error(`Unknown command ${command}; use world-memory --help`)
  const script = path.join(__dirname, '..', 'renderers', scripts[command]), blender = command === 'render-cave'
  const executable = blender ? 'blender' : 'python3'
  const argv = blender ? ['--background', '--threads', '2', '--python-exit-code', '1', '--python', script, '--', ...args] : [script, ...args]
  const child = spawn(executable, argv, { stdio: 'inherit', shell: false })
  await new Promise((resolve, reject) => {
    child.on('error', e => reject(new Error(`${executable} unavailable: ${e.message}. Install the optional renderer dependencies in docs/visual.md`)))
    child.on('close', (code, signal) => { if (code === 0) resolve(); else reject(new Error(`${command} failed (${signal || code})`)) })
  })
}
main().catch(e => { console.error(`world-memory: ${e.message}`); process.exitCode = 1 })

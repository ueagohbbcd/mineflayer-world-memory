# Optional local textured 3D

This is an offline, bounded reconstruction from a `world-memory.region.v1`
export. It uses Prismarine-viewer 1.33.0 blockstate/model geometry and its matching
texture atlas, then rasterizes on the CPU with NumPy/Pillow. The native complete
cache remains the source of truth. Check clearance and traversability separately.

## Dependencies and paths

Install the optional renderer dependencies explicitly: a legally obtained local
`prismarine-viewer@1.33.0` plus Python 3 with NumPy and Pillow. The core memory
package is independent of the viewer. Assets are read in place. Follow the relevant asset
licenses for your installation and redistribution; no right to redistribute
Minecraft assets is implied.

The adapter discovers the package through Node resolution by default, or accepts
`viewerRoot` (the package directory containing its `package.json`). `assets` is
optional and defaults to that package's `public` directory. It must contain
`blocksStates/<version>.json` and `textures/<version>.png`. Mesh metadata records
both asset hashes; the Python stage rejects an atlas with a different hash.

## Two stages

The unified CLI exposes `world-memory local-mesh --config mesh-config.json`
(the JSON object uses the JS option names below), then
`world-memory render-local --mesh-dir ./new-mesh --output ./new-view.png`.
Direct module/script calls are equivalent.

Export a small, fully known ROI first using the region exporter. Every cell,
including actual air, must have an explicit valid native state ID. Then:

```js
const { exportLocalMesh } = require('mineflayer-world-memory/lib/local-mesh')
await exportLocalMesh({
  regionDir: './derived-region',
  output: './new-mesh',
  // Optional if Node cannot discover the installed package:
  viewerRoot: '/path/to/prismarine-viewer',
  // assets: '/path/to/matching/public'
})
```

```sh
python3 renderers/local_texture.py --mesh-dir ./new-mesh --output ./new-view.png
```

Mesh stage: `exportLocalMesh({regionDir, output, viewerRoot?, assets?})` returns
`{output, vertices, triangles, sections}` and writes `mesh.json` plus
`metadata.json` to a newly created directory. Mesh positions are absolute world
XYZ, including negative coordinates and cross-chunk geometry. Native state
properties select model variants, including stair geometry.
A section-aligned internal Y translation avoids the viewer's pre-1.18 Y=0
culling assumption; output positions are translated back to original world Y.

Raster stage accepts `--mesh-dir`, `--output`, optional `--assets`, `--eye X Y Z`,
`--target X Y Z`, `--width`, `--height`, `--scale` (1–3 supersampling), and `--fov`
(10–120 degrees). Defaults are a bounds-derived overview, 960×720 viewport,
scale 1, and 40° vertical field of view. A provenance footer is added beneath the
viewport. PNG text metadata (`world-memory`) includes camera coordinates,
source/asset hashes, observation stamps, lighting, triangle/pixel counts, and
limitations. stdout reports render metrics. This view is grid-free.
For underground shape inspection with a grid, use the separate air-cast renderer.

## Meaning and limits

- Every voxel inside the ROI must be known, including air. Export a smaller fully known region if
  the first export contains unknown cells.
- Outside the ROI is deliberately omitted for a **cut-away**. The adapter uses
  empty exterior samples to expose cut faces. Space beyond the ROI remains
  unobserved in this view; passages and world boundaries there need further data.
- Region v1 has no biome/light arrays. Tint uses a fixed plains display biome;
  neutral directional light and model ambient occlusion provide display lighting.
  Measured game lighting is unavailable. The view omits entities, block entities,
  particles, animated textures, or live changes.
- Weighted variants deterministically select their first model. Model/atlas
  versions must match the export version. Missing block models or unmatched
  variants produce explicit errors.
- The CPU rasterizer uses perspective-correct UVs, depth testing, and back-face
  culling. Alpha below 26/255 is discarded; remaining alpha is treated as opaque.
  Water, stained glass, and other translucency are approximate, without blending.
  Triangles intersecting the near plane are omitted and counted. Move the camera
  out if `nearPlaneSkippedTriangles` is nonzero.
- Work is bounded to 131,072 voxels and 512 sections; mesh accumulation is capped at 262,144 vertices / 786,432 indices and 32 MiB
  JSON. The rasterizer uses 128×128 temporary tiles; images are capped at 4M
  supersampled pixels. Large regions should be split into inspection ROIs.
- Inputs and assets are read-only. Both stages require new output paths, reject
  overwrites and symlink aliases into their source directories. Put derived
  files outside the native cache.
  Partial outputs from an interrupted write must be discarded or a new path used.

## Synthetic verification

```sh
node --test test/local-mesh.test.js
# Enable the optional integration test with an existing installed viewer:
WORLD_MEMORY_VIEWER_ROOT=/path/to/prismarine-viewer node --test test/local-mesh.test.js
python3 -m unittest discover -s test -p visual_local_test.py
```

Without an installed viewer, only its explicit integration test is skipped;
input validation and output-safety tests still run. Fixtures contain synthetic
coordinates/states and a generated atlas only. Tests cover negative coordinates,
cross-chunk geometry, known air, stairs, missing/unknown cells, immutable source
hashes, output safety, camera validation, nonempty raster output, and PNG metadata.

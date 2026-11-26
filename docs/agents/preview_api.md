# Server-rendered structure previews

A host can show a structure immediately as a cached PNG or WebP, then
open one Plotly viewer for that same scene. The raster is not a 3D
session. It does not create a WebGL context, and it is not
pixel-identical to Plotly shading.

Matplotlib stays the projected 2D PNG/PDF/SVG exporter. It is not this
preview, and it cannot write interactive HTML. The native GPU backend
is an explicit PNG path. It does not silently fall back to the CPU.

Embedding several live viewers, a fragment API (`setCamera`,
`setStructure`, `setStyle`), and shared-script multi-instance isolation
remain [issue 331](https://github.com/SchrodingersCattt/Atomaestro/issues/331).
This preview activates one complete Plotly document for a plan that was
already previewed. The public `mat-vis render --backend plotly` document
still inlines Plotly.js. The legacy `write_interactive_html` helper still
writes a CDN script and is not the preview activator.

## Request

`POST /api/v2/preview` takes one JSON object. The Python call is
`mat_viewer.render_preview`.

| Field | Required | Meaning |
|---|---|---|
| `namespace` | yes | Caller partition, 1–128 characters. Not part of the cache key. |
| `scene` | one of three | Scene object: `atoms`, `bonds`, `matrix`. |
| `cif` | one of three | CIF text. |
| `xyz` | one of three | XYZ text. |
| `camera` | no | `CameraSpec` fields. Omitted cameras are fitted and returned. |
| `style` | no | `RenderSpec` fields. `style` is accepted as an alias of `representation`. |
| `view` | no | `ViewSpec` (`display`, `include_boundary_replicas`). |
| `width`, `height` | no | Pixel size. Default 900×720. These override `style.width` / `style.height`. |
| `scale` | no | CPU supersample factor. Default **1** (the CLI PNG default remains 2). |
| `format` | no | `png` (default) or `webp`. |
| `backend` | no | `cpu` (default) or `gpu`. |
| `atom_groups`, `bond_groups`, `cell_overlays`, `vector_overlays`, `topology_data`, `atom_property_color` | no | The same optional inputs as `prepare_render`. |

Exactly one of `scene`, `cif`, or `xyz` is accepted. Filesystem paths are
a Python-only source (`render_preview(path, ...)`). The HTTP body does
not take a path.

`width * height * scale^2` must be at most 8,388,608, and each edge must
be in `1..4096`. `scale` is at most 4.

Unknown JSON fields are rejected. `backend: "matplotlib"` and
`backend: "plotly"` are rejected. `backend: "gpu"` with `format: "webp"`
is rejected. GPU failures (`GPUUnavailableError`, `GPUUnsupportedError`)
are returned as HTTP 503 and 400. The CPU raster is not run on those
failures.

The default response is JSON (`mattervis.preview-image/v1`) with
`image_path` and `activation.path`. `Accept: image/png` or
`Accept: image/webp` returns the bytes instead. Image and HTML responses
use `Cache-Control: private, no-store`. The ticket is a bearer capability
for this process only. A restart, or eviction, makes the ticket 404.

```python
from mat_viewer import render_preview
from mat_viewer.render.preview import PreviewCache

cache = PreviewCache()
image = render_preview(
    {"atoms": [...], "bonds": [...], "matrix": [...]},
    cache=cache,
    namespace="review-1",
    camera={
        "position": [7.5, 7.5, 45.0],
        "target": [7.5, 7.5, 7.5],
        "up": [0.0, 1.0, 0.0],
        "projection": "orthographic",
        "ortho_scale": 10.0,
    },
    style={"representation": "ball_stick", "show_cell": True, "show_hydrogen": True},
    width=900,
    height=720,
    backend="cpu",
    image_format="png",
)
```

`image.summary` reports displayed atom primitives, bond count, and
whether the periodic cell primitive is present. `image.camera` is the
camera stored on the `RenderPlan`, including a fitted camera when the
request omitted one.

`image.timing_s` has `prepare`, `render_encode`, and `total`. A cache
hit reports `prepare: 0` and `render_encode: 0`. A request that misses
the exact JSON key but matches a retained plan fingerprint still pays
`prepare` and reports `render_encode: 0`. These spans are
`time.perf_counter` in the server process. They are not GPU timestamps,
first contentful paint, or time to interactive.

## Camera convention

`CameraSpec` is the preview camera and the camera on the activated
viewer’s plan.

- `position`, `target`, and `up` are world-space Cartesian coordinates.
  The camera looks from `position` toward `target`.
- In camera space the view looks along `-z`. Visible depth is `-z`.
- `up` must not be parallel to the view direction.
- `projection` is `orthographic` or `perspective`.
- Orthographic `ortho_scale` is the half-height of the view in world
  units. Half-width is `ortho_scale * width / height`.
- Perspective `fov_y_deg` is the vertical field of view in degrees.
- `near` and `far` are positive camera-space clipping distances,
  `0 < near < far`.
- CPU pixels have `y` increasing downward.

The CPU raster uses that transform directly. The Plotly document uses
the existing adapter: it keeps the target, direction, up vector,
projection, and deterministic axis ranges. Plotly does not expose the
`CameraSpec` near/far planes or field of view, so the interactive frame
is the same plan, not a pixel copy of the PNG.

Sending the returned `camera` object back is a different request key
when the original request omitted `camera`. The plan fingerprint matches,
so the raster is reused instead of drawn again.

## Cache key

The cache key is the SHA-256 hex digest of canonical JSON
(`sort_keys`, separators `(',', ':')`):

```json
{
  "schema": "mattervis.preview-cache/v1",
  "package_version": "<mat_viewer.__version__>",
  "backend": "cpu",
  "format": "png",
  "encoder": {"format": "png", "compress_level": 9, "optimize": false},
  "width": 900,
  "height": 720,
  "scale": 1,
  "view": null,
  "camera": null,
  "style": {"representation": "ball_stick", "show_cell": true, "show_hydrogen": true},
  "source": {"kind": "scene", "scene": {}},
  "atom_groups": null,
  "bond_groups": null,
  "cell_overlays": null,
  "vector_overlays": null,
  "topology_data": null,
  "atom_property_color": null
}
```

`mat_viewer.render.preview.preview_cache_key` computes the digest.
`package_version` comes from the installed package version, not a
hard-coded string.

PNG encoder settings match `render_png`: compress level 9, `optimize`
false. WebP uses Pillow quality 80, method 4, and `lossless` false, and
it re-encodes that PNG so the pixels are the CPU raster. GPU WebP is
not offered.

The namespace is not in the key. Storage is still partitioned:
`(namespace, cache_key)` is the entry, and a second namespace that
submits the same request gets its own miss, its own ticket, and its own
copy of the bytes. There is no HTTP lookup by cache key. `GET
/api/v2/preview/image/<ticket>` redeems a ticket minted for the caller
that rendered the image. Tickets are unguessable. Eviction drops the
ticket.

A live Python object that has no JSON form (for example a
`MolecularCrystal` already in memory) is keyed by the plan fingerprint
plus backend, format, encoder, and package version. The object is kept
on that namespace’s entry until eviction.

Limits are 32 entries and 64 MiB per cache. The byte count is the PNG
or WebP plus the retained replay JSON, which includes the structure
text, plus a live in-memory source when the request has no JSON form.
An entry over that budget is returned and not retained (`retained:
false`, `ticket: null`). Same-namespace callers that submit the same
request while a raster is in flight share that raster (`cache_status:
"coalesced"`).

The response also includes `plan_sha256`, which is
`RenderPlan.fingerprint()`. That digest covers dimensions, background,
metadata, the resolved camera, and the primitive geometry (atoms, bonds,
and the periodic cell). Activation prepares the request again and
returns HTML only when the new fingerprint equals `plan_sha256`.

Path sources include the resolved path, size, and mtime in the cache
key. A changed file is a new key. An old ticket replays the path and
fails if the fingerprint moved, instead of serving a viewer for a
different structure.

The cache is process memory. Another worker does not honor the ticket.

## Activation

`GET /api/v2/preview/activate/<ticket>` or
`POST /api/v2/preview/activate` with `{"ticket": "..."}` returns one
complete HTML document. Plotly.js is inlined, so the document does not
need a network fetch for the library. The figure is
`build_figure(plan, interactive=True)` for the preview plan. Trace
defaults are unchanged: no flat shading, reduced tessellation,
wireframe, float32 positions, `hoverinfo: "skip"`, or batched traces are
introduced here.

`write_preview_host(directory, images, cache=cache)` writes a page whose
panels are `<img>` elements. The host document does not call
`Plotly.newPlot` and does not reference `plotly.min.js`. An
“Inspect in 3D” control sets one iframe `src`. That sibling document
references one local `plotly.min.js` (`include_plotlyjs="directory"`).
Closing the viewer sets the iframe to unload. At most one viewer is
active (`MAX_ACTIVE_VIEWERS` is 1).

`write_camera_swap_page` writes a page that only replaces an `<img>`
`src`. It has no Plotly script, iframe, or canvas.

```python
from mat_viewer.render.preview import activation_html, write_preview_host

html = activation_html(cache, image.ticket)  # inline Plotly.js
write_preview_host("host", [image], cache=cache)
```

## Benchmark

`python scripts/benchmark_structure_preview.py` renders an explicit
6×6×5 grid of C–H pairs (360 atoms, 180 bonds) at 900×720, scale 1,
ball-and-stick, hydrogens and the periodic cell on, axes off. Coordinates
and bonds are written by the fixture. Nothing is bond-perceived, and the
grid is not a research structure.

The run records, for one camera, a cold raster and a warm hit, then four
cameras (`plus-z`, `plus-x`, `plus-y`, `diagonal`) in a fresh namespace.
`four_view.initial_transfer_bytes` is the four PNG files plus the
image-only host document. The Plotly document and `plotly.min.js` are
reported separately as activation payload.

`--browser` loads those local files in headless Chrome when a binary is
on `PATH` and records Performance paint entries, resource timing, JS
heap when `performance.memory` exists, and a count of
`getContext("webgl"|"webgl2")` calls. Paint timestamps are whatever
Chrome returns. They are not GPU times. `--inline-html` also writes one
self-contained document. The pytest that runs the raster side is
`tests/render/test_structure_preview_benchmark.py` and does not launch
Chrome.

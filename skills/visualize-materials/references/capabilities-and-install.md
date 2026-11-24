# Capabilities and Installation

Read this only when the direct base render reports a missing command/capability
or the request explicitly needs an optional frontend. The capability resolver is
authoritative; do not maintain a second dependency guess in an agent script.

The command remains `mat-vis`. Python imports remain `mat_viewer`.

Install `atomaestro` from PyPI in a new environment or uninstall the
legacy `matter-vis` package first. Do not install both packages together.

## Resolve on demand

```bash
mat-vis capabilities --json
mat-vis capabilities --require png ortep --json
```

Do not run a separate render preflight before ordinary CPU output. Use the exact
`install` command in the resolver result only after a real missing-capability
error. Never install `[all]` when a smaller reported extra is sufficient.


<!-- capability-matrix:start -->
| What you need | Resolver requirement | Required installation |
|---|---|---|
| CPU 3D or Matplotlib projected 2D PNG/PDF/SVG; structure inspection; ORTEP; rings; polyhedra | `core` | `python -m pip install "atomaestro"` |
| Interactive Plotly/WebGL HTML | `plotly` | `python -m pip install "atomaestro[plotly]"` |
| Plotly PNG/PDF/SVG through Kaleido | `plotly-export` | `python -m pip install "atomaestro[plotly-export]"` |
| Dash viewer, REST, and WebSocket service | `web` | `python -m pip install "atomaestro[web]"` |
| Interactive terminal UI | `tui` | `python -m pip install "atomaestro[tui]"` |
| Cube input inspection and isosurfaces | `cube` | `python -m pip install "atomaestro[cube]"` |
| PyVista offscreen cube figures | `vtk` | `python -m pip install "atomaestro[vtk]"` |
| GIF/MP4 encoding | `animation` | `python -m pip install "atomaestro[animation]"` |
| Native offscreen opaque PNG via wgpu | `gpu` | `python -m pip install "atomaestro[gpu]"` |
<!-- capability-matrix:end -->

The `vtk` extra does not install a system OpenGL library. Windows and macOS
use the native VTK window class and do not need `DISPLAY`. Headless Linux
needs OSMesa (Debian/Ubuntu package `libosmesa6`) or EGL. On Linux the figure
call raises `RuntimeError` when neither a display nor one of those libraries
is loadable. Atomaestro does not run `apt` or download that library.

Combined Web operations intentionally cross two optional boundaries:

| What you need | Resolver requirement | Required installation |
|---|---|---|
| REST/API browser screenshot | `web-screenshot` | `python -m pip install "atomaestro[plotly-export,web]"` |
| Web UI static Plotly export | `static-web-export` | `python -m pip install "atomaestro[plotly-export,web]"` |

These are requirement aliases, not new extras. They combine the Web service
with the Plotly/Kaleido encoder and never substitute a CPU image or placeholder
PNG when export fails.

Combine only the extras the request needs, for example:

```bash
python -m pip install "atomaestro[cube,animation]"
python -m pip install "atomaestro[web,tui]"
```

`python -m pip install "atomaestro[all]"` is reserved for development or a
caller who explicitly wants every frontend.

## Chrome and Kaleido

The CPU backend needs neither Chrome nor Kaleido. HTML needs Plotly but not
Chrome. Only Plotly static PNG/PDF/SVG needs `[plotly-export]`; Kaleido may also
need a working Chrome installation. Atomaestro never downloads Chrome, runs
`apt`, or changes system libraries automatically. If an explicit Plotly static
render fails after the Python extra is installed, preserve the original error
and ask the caller to provide or authorize a compatible Chrome runtime. Do not
switch backend or representation silently.

## Development checkout

The checkout requires `molcrys-kit[desktop]>=0.9.0,<0.10.0`.
The desktop extra supplies ASE, pymatgen, SciPy, spglib, and defusedxml.
Install the checkout with `pip install -e .`.
Released users should follow the resolver's ordinary `atomaestro[...]` command;
do not put a Git URL into generated runtime requirements.

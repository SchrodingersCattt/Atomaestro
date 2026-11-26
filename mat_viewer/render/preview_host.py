"""One Plotly activation document and image-only host pages.

These writers do not change the public interactive HTML defaults. The
complete document still inlines Plotly.js unless the caller asks for a
sibling ``plotly.min.js``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import RenderPlan
from .preview_cache import PreviewCache, _Entry
from .preview_request import (
    DEFAULT_PREVIEW_SCALE,
    MAX_ACTIVE_VIEWERS,
    PreviewError,
    PreviewImage,
    PreviewRequest,
    PreviewUnsupported,
    TextStructure,
    json_ready,
)


def activation_html(
    cache: PreviewCache,
    ticket: str,
    *,
    include_plotlyjs: bool | str = True,
) -> bytes:
    """One complete Plotly document for the retained preview plan.

    The document is built from a freshly prepared plan. The call refuses
    to return HTML when that plan's fingerprint differs from the preview.
    ``include_plotlyjs=True`` inlines Plotly.js. ``"directory"`` references
    a sibling ``plotly.min.js``.
    """

    entry = cache.redeem(ticket)
    plan = _replay_plan(entry)
    if plan.fingerprint() != entry.plan_sha256:
        raise PreviewError(
            "replayed plan does not match the cached preview",
            hint="Request a new preview. The source or renderer changed.",
        )
    html = _plan_html(plan, include_plotlyjs=include_plotlyjs)
    marker = f"<!-- mattervis-preview-plan:{plan.fingerprint()} -->\n".encode()
    return marker + html


def _replay_plan(entry: _Entry) -> RenderPlan:
    record = entry.replay.get("request")
    if not isinstance(record, Mapping):
        raise PreviewError("cached preview is missing its replay request")
    if entry.replay.get("kind") == "live":
        if entry.live_source is None:
            raise PreviewError("live preview source is no longer retained")
        source: Any = entry.live_source
    else:
        source = _source_from_record(entry.replay.get("source"))
    request = PreviewRequest(
        namespace=entry.namespace,
        source=source,
        camera=record.get("camera"),
        view=record.get("view"),
        style=record.get("style"),
        width=int(record.get("width", entry.width)),
        height=int(record.get("height", entry.height)),
        scale=int(record.get("scale", DEFAULT_PREVIEW_SCALE)),
        backend=entry.backend,
        image_format=entry.image_format,
        atom_groups=record.get("atom_groups"),
        bond_groups=record.get("bond_groups"),
        cell_overlays=record.get("cell_overlays"),
        vector_overlays=record.get("vector_overlays"),
        topology_data=record.get("topology_data"),
        atom_property_color=record.get("atom_property_color"),
    )
    return request.prepare()


def _source_from_record(source: Any) -> Any:
    if not isinstance(source, Mapping):
        raise PreviewError("cached preview source is missing")
    kind = source.get("kind")
    if kind == "scene":
        scene = source.get("scene")
        if not isinstance(scene, Mapping):
            raise PreviewError("cached scene preview is missing")
        return json_ready(scene)
    if kind in {"cif", "xyz"}:
        return TextStructure(fmt=str(kind), text=str(source.get("text") or ""))
    if kind == "path":
        return str(source.get("path"))
    raise PreviewError(f"cannot replay preview source kind {kind!r}")


def _plan_html(plan: RenderPlan, *, include_plotlyjs: bool | str) -> bytes:
    try:
        from .plotly import build_figure, interactive_document
    except ImportError as exc:
        from ..capabilities import resolve_requirements

        install = resolve_requirements("plotly").install_command
        raise PreviewUnsupported(
            "Activating a preview requires the Plotly extra.",
            hint=f"Install with: {install}",
        ) from exc
    figure = build_figure(plan, interactive=True)
    return interactive_document(figure, include_plotlyjs=include_plotlyjs)


def write_shared_plotlyjs(directory: Path) -> Path:
    """Write one local Plotly.js bundle. The file is not a CDN URL."""

    try:
        from plotly.offline import get_plotlyjs
    except ImportError as exc:
        from ..capabilities import resolve_requirements

        install = resolve_requirements("plotly").install_command
        raise PreviewUnsupported(
            "A shared Plotly.js file requires the Plotly extra.",
            hint=f"Install with: {install}",
        ) from exc
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "plotly.min.js"
    path.write_text(get_plotlyjs(), encoding="utf-8")
    return path


def write_preview_host(
    directory: str | Path,
    images: Sequence[PreviewImage],
    *,
    cache: PreviewCache,
    activate: bool = True,
) -> Path:
    """Write a host page that shows rasters and can mount one viewer.

    Inactive panels are ``<img>`` elements. An activation control sets one
    iframe ``src`` to a sibling HTML document that references
    ``plotly.min.js``. The host document does not call ``Plotly.newPlot``.
    """

    if not images:
        raise PreviewError("preview host needs at least one image")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    panels: list[str] = []
    if activate:
        write_shared_plotlyjs(root)
    for index, image in enumerate(images):
        suffix = image.image_format
        filename = f"panel-{index}.{suffix}"
        (root / filename).write_bytes(image.data)
        frame = ""
        button = ""
        if activate:
            if not image.ticket or not image.retained:
                raise PreviewError(
                    "activation requires a retained preview ticket",
                    hint="Raise the preview cache budget or request a smaller image.",
                )
            document = activation_html(
                cache,
                image.ticket,
                include_plotlyjs="directory",
            )
            (root / f"panel-{index}.html").write_bytes(document)
            frame = (
                f'<iframe data-src="panel-{index}.html" title="3D viewer {index}"'
                f' hidden></iframe>'
            )
            button = (
                f'<button type="button" data-activate="{index}">Inspect in 3D</button>'
                f'<button type="button" data-close="{index}" hidden>Close 3D</button>'
            )
        panels.append(
            f'<section class="panel" data-panel="{index}">'
            f'<img alt="Static structure preview {index}" src="{filename}" '
            f'width="{image.width}" height="{image.height}">'
            f"{frame}{button}</section>"
        )
    script = _HOST_SCRIPT if activate else ""
    blurb = (
        "Raster previews are static images. They are not a continuous 3D viewer."
    )
    if activate:
        blurb += (
            " Inspect in 3D loads one Plotly viewer "
            f"(maximum {MAX_ACTIVE_VIEWERS})."
        )
    html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>MatterVis previews</title>\n<style>{_HOST_CSS}</style>\n"
        "</head>\n<body>\n"
        f"<p data-mattervis-preview=\"static\">{blurb}</p>\n"
        + "\n".join(panels)
        + "\n"
        + script
        + "\n</body>\n</html>\n"
    )
    path = root / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def write_camera_swap_page(
    path: str | Path,
    frames: Sequence[tuple[str, bytes]],
    *,
    width: int,
    height: int,
) -> Path:
    """Write a page that replaces one image when the camera changes.

    The document has no Plotly script, no iframe, and no canvas.
    """

    if not frames:
        raise PreviewError("camera swap page needs at least one image")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    buttons: list[str] = []
    for index, (label, data) in enumerate(frames):
        filename = f"{destination.stem}-{index}.png"
        (destination.parent / filename).write_bytes(data)
        safe_label = (
            str(label)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        buttons.append(
            f'<button type="button" data-frame="{filename}">{safe_label}</button>'
        )
    first = f"{destination.stem}-0.png"
    html = (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>MatterVis camera previews</title></head><body>\n"
        "<p>Choosing a camera replaces the image. This page does not create "
        "a WebGL context.</p>\n"
        f"<img id=\"preview\" alt=\"Static structure preview\" src=\"{first}\" "
        f"width=\"{int(width)}\" height=\"{int(height)}\">\n"
        + "\n".join(buttons)
        + "\n<script>\n"
        "(function () {\n"
        "  var image = document.getElementById(\"preview\");\n"
        "  document.addEventListener(\"click\", function (event) {\n"
        "    var frame = event.target.getAttribute && "
        "event.target.getAttribute(\"data-frame\");\n"
        "    if (!frame) return;\n"
        "    image.src = frame;\n"
        "  });\n"
        "})();\n"
        "</script>\n</body></html>\n"
    )
    destination.write_text(html, encoding="utf-8")
    return destination


_HOST_CSS = (
    "body{font-family:sans-serif;margin:16px;}"
    ".panel{position:relative;display:inline-block;margin:0 12px 12px 0;}"
    ".panel img,.panel iframe{display:block;border:1px solid #ccc;background:#fff;}"
    ".panel iframe{position:absolute;inset:0;width:100%;height:100%;}"
    "button{margin:4px 4px 0 0;}"
)

_HOST_SCRIPT = """<script>
(function () {
  "use strict";
  var maxActive = __MAX_ACTIVE__;
  var active = [];
  function panel(index) {
    return document.querySelector('[data-panel="' + index + '"]');
  }
  function closeViewer(index) {
    var root = panel(index);
    if (!root) return;
    var frame = root.querySelector("iframe");
    var image = root.querySelector("img");
    if (frame) {
      frame.hidden = true;
      frame.src = "about:blank";
      frame.removeAttribute("src");
    }
    if (image) image.hidden = false;
    var close = root.querySelector("[data-close]");
    if (close) close.hidden = true;
    var at = active.indexOf(index);
    if (at !== -1) active.splice(at, 1);
  }
  function activate(index) {
    if (active.indexOf(index) !== -1) return;
    while (active.length >= maxActive) closeViewer(active[0]);
    var root = panel(index);
    var frame = root.querySelector("iframe");
    var image = root.querySelector("img");
    if (!frame || !frame.getAttribute("data-src")) return;
    frame.src = frame.getAttribute("data-src");
    frame.hidden = false;
    if (image) image.hidden = true;
    var close = root.querySelector("[data-close]");
    if (close) close.hidden = false;
    active.push(index);
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.getAttribute) return;
    var open = target.getAttribute("data-activate");
    if (open !== null) activate(open);
    var shut = target.getAttribute("data-close");
    if (shut !== null) closeViewer(shut);
  });
})();
</script>""".replace("__MAX_ACTIVE__", str(MAX_ACTIVE_VIEWERS))

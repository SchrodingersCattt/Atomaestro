"""Matched-size synthetic benchmark for structure previews.

The fixture is an explicit C-H pair grid. It is not a researched
structure. Timings from ``time.perf_counter`` are server-process spans.
Browser paint entries are recorded only when a headless Chrome run
actually returns them, and they are not reported as hardware-GPU time.
"""

from __future__ import annotations

import json
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .contracts import CameraSpec
from .preview import (
    DEFAULT_PREVIEW_HEIGHT,
    DEFAULT_PREVIEW_WIDTH,
    PreviewCache,
    activation_html,
    render_preview,
    synthetic_ch_pair_grid,
    write_camera_swap_page,
    write_preview_host,
    write_shared_plotlyjs,
)

BENCHMARK_SCHEMA = "mattervis.preview-benchmark/v1"
BENCHMARK_NX = 6
BENCHMARK_NY = 6
BENCHMARK_NZ = 5
BENCHMARK_WIDTH = DEFAULT_PREVIEW_WIDTH
BENCHMARK_HEIGHT = DEFAULT_PREVIEW_HEIGHT
BENCHMARK_SCALE = 1

_STYLE = {
    "representation": "ball_stick",
    "shading": "smooth",
    "show_cell": True,
    "show_hydrogen": True,
    "show_axes": False,
}

_PARENT_PROBE = """
<script>
window.__mattervisWebglContexts = 0;
(function () {
  var original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind) {
    if (kind === "webgl" || kind === "webgl2" || kind === "experimental-webgl") {
      window.__mattervisWebglContexts += 1;
    }
    return original.apply(this, arguments);
  };
})();
window.addEventListener("load", function () {
  var paints = performance.getEntriesByType("paint").map(function (entry) {
    return {name: entry.name, startTime: entry.startTime};
  });
  var resources = performance.getEntriesByType("resource").map(function (entry) {
    return {
      name: entry.name,
      transferSize: entry.transferSize,
      decodedBodySize: entry.decodedBodySize,
      duration: entry.duration,
      responseEnd: entry.responseEnd
    };
  });
  var navigation = performance.getEntriesByType("navigation")[0] || null;
  var memory = performance.memory ? {
    usedJSHeapSize: performance.memory.usedJSHeapSize,
    totalJSHeapSize: performance.memory.totalJSHeapSize
  } : null;
  var node = document.createElement("pre");
  node.id = "benchmark-result";
  node.textContent = JSON.stringify({
    paints: paints,
    resources: resources,
    webglContexts: window.__mattervisWebglContexts || 0,
    memory: memory,
    navigation: navigation ? {
      domContentLoadedEventEnd: navigation.domContentLoadedEventEnd,
      loadEventEnd: navigation.loadEventEnd,
      responseEnd: navigation.responseEnd
    } : null
  });
  document.body.appendChild(node);
});
</script>
"""

_CHILD_PRELUDE = """
<script>
window.__mattervisWebglContexts = 0;
(function () {
  var original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind) {
    if (kind === "webgl" || kind === "webgl2" || kind === "experimental-webgl") {
      window.__mattervisWebglContexts += 1;
    }
    return original.apply(this, arguments);
  };
})();
</script>
"""

_CHILD_REPORT = """
<script>
document.documentElement.setAttribute(
  "data-mattervis-webgl",
  String(window.__mattervisWebglContexts || 0)
);
</script>
"""


def benchmark_scene() -> dict[str, Any]:
    """360-atom explicit grid: 6 x 6 x 5 C-H pairs."""

    return synthetic_ch_pair_grid(nx=BENCHMARK_NX, ny=BENCHMARK_NY, nz=BENCHMARK_NZ)


def benchmark_cameras(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Four orthographic cameras around the cell center."""

    matrix = scene["matrix"]
    center = (
        float(matrix[0][0]) * 0.5,
        float(matrix[1][1]) * 0.5,
        float(matrix[2][2]) * 0.5,
    )
    extent = max(center[0], center[1], center[2]) * 2.0
    ortho_scale = extent * 0.65
    distance = extent * 3.0
    specs = (
        ("plus-z", (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
        ("plus-x", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        ("plus-y", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        ("diagonal", (1.0, 1.0, 1.0), (0.0, 0.0, 1.0)),
    )
    cameras = []
    for name, direction, up in specs:
        camera = CameraSpec.looking_along(
            direction,
            target=center,
            up=up,
            distance=distance,
            projection="orthographic",
            ortho_scale=ortho_scale,
        )
        cameras.append({"name": name, "camera": _camera_dict(camera)})
    return cameras


def _camera_dict(camera: CameraSpec) -> dict[str, Any]:
    return {
        "position": list(camera.position),
        "target": list(camera.target),
        "up": list(camera.up),
        "projection": camera.projection,
        "fov_y_deg": camera.fov_y_deg,
        "near": camera.near,
        "far": camera.far,
        "ortho_scale": camera.ortho_scale,
    }


def _image_record(image) -> dict[str, Any]:
    return {
        "bytes": len(image.data),
        "sha256": image.sha256,
        "cache_key": image.cache_key,
        "cache_status": image.cache_status,
        "rasterized": image.rasterized,
        "plan_sha256": image.plan_sha256,
        "width": image.width,
        "height": image.height,
        "prepare_s": image.timing_s.get("prepare"),
        "render_encode_s": image.timing_s.get("render_encode"),
        "total_s": image.timing_s.get("total"),
        "timing_note": image.timing_note,
        "summary": image.summary,
        "camera": image.camera,
    }


def run_preview_benchmark(
    output: str | Path,
    *,
    browser: bool = False,
    inline_html: bool = False,
) -> dict[str, Any]:
    """Render the matched fixture and write a JSON report plus HTML pages.

    ``browser=True`` loads the image-only host and one activation document
    in headless Chrome when a binary is on ``PATH``. A missing browser is
    recorded and is not treated as a zero WebGL count.
    """

    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    scene = benchmark_scene()
    cameras = benchmark_cameras(scene)
    single_cache = PreviewCache()
    primary = cameras[0]["camera"]
    cold = render_preview(
        scene,
        cache=single_cache,
        namespace="benchmark-single",
        camera=primary,
        style=_STYLE,
        width=BENCHMARK_WIDTH,
        height=BENCHMARK_HEIGHT,
        scale=BENCHMARK_SCALE,
        backend="cpu",
        image_format="png",
    )
    warm = render_preview(
        scene,
        cache=single_cache,
        namespace="benchmark-single",
        camera=primary,
        style=_STYLE,
        width=BENCHMARK_WIDTH,
        height=BENCHMARK_HEIGHT,
        scale=BENCHMARK_SCALE,
        backend="cpu",
        image_format="png",
    )
    four_cache = PreviewCache()
    panels = []
    images = []
    for item in cameras:
        image = render_preview(
            scene,
            cache=four_cache,
            namespace="benchmark-four",
            camera=item["camera"],
            style=_STYLE,
            width=BENCHMARK_WIDTH,
            height=BENCHMARK_HEIGHT,
            scale=BENCHMARK_SCALE,
            backend="cpu",
            image_format="png",
        )
        images.append(image)
        record = _image_record(image)
        record["name"] = item["name"]
        panels.append(record)
    host = write_preview_host(
        root / "four-view",
        images,
        cache=four_cache,
        activate=False,
    )
    swap = write_camera_swap_page(
        root / "camera-swap.html",
        [(item["name"], image.data) for item, image in zip(cameras, images)],
        width=BENCHMARK_WIDTH,
        height=BENCHMARK_HEIGHT,
    )
    activation_started = time.perf_counter()
    activation = activation_html(
        single_cache,
        cold.ticket or "",
        include_plotlyjs="directory",
    )
    activation_s = time.perf_counter() - activation_started
    activation_path = root / "single-view.html"
    activation_path.write_bytes(activation)
    plotly_path = write_shared_plotlyjs(activation_path.parent)
    inline_bytes = None
    inline_note = "inline Plotly document was not generated in this run"
    if inline_html:
        inline_started = time.perf_counter()
        inline = activation_html(single_cache, cold.ticket or "", include_plotlyjs=True)
        inline_s = time.perf_counter() - inline_started
        inline_path = root / "single-view-inline.html"
        inline_path.write_bytes(inline)
        inline_bytes = {
            "bytes": len(inline),
            "python_build_s": inline_s,
            "timing_note": (
                "python_build_s is time.perf_counter around activation_html "
                "with Plotly.js inlined. It is not first contentful paint."
            ),
        }
        inline_note = None
    image_bytes = sum(len(image.data) for image in images)
    host_bytes = host.stat().st_size
    report: dict[str, Any] = {
        "schema": BENCHMARK_SCHEMA,
        "fixture": {
            "description": (
                "Explicit C-H pairs on a cubic grid. Bonds and coordinates "
                "are written by the fixture. No bond perception and no "
                "research structure."
            ),
            "nx": BENCHMARK_NX,
            "ny": BENCHMARK_NY,
            "nz": BENCHMARK_NZ,
            "atoms": len(scene["atoms"]),
            "bonds": len(scene["bonds"]),
            "width": BENCHMARK_WIDTH,
            "height": BENCHMARK_HEIGHT,
            "scale": BENCHMARK_SCALE,
            "backend": "cpu",
            "format": "png",
            "style": dict(_STYLE),
            "matplotlib": "not used",
        },
        "single_view": {
            "cold": _image_record(cold),
            "warm": _image_record(warm),
        },
        "four_view": {
            "panels": panels,
            "image_bytes": image_bytes,
            "host_html_bytes": host_bytes,
            "initial_transfer_bytes": image_bytes + host_bytes,
            "initial_transfer_note": (
                "Sum of the four PNG files and the image-only host document. "
                "Those are the bytes a client downloads to show the previews. "
                "Plotly.js and the activation document are not included."
            ),
        },
        "activation": {
            "bytes": len(activation),
            "plotlyjs_bytes": plotly_path.stat().st_size,
            "python_build_s": activation_s,
            "include_plotlyjs": "directory",
            "plan_sha256": cold.plan_sha256,
            "timing_note": (
                "python_build_s is time.perf_counter around building one "
                "complete HTML document from the cached preview. It is not "
                "browser first contentful paint, GPU time, or time to interactive."
            ),
        },
        "inline_html": inline_bytes,
        "camera_swap_html_bytes": swap.stat().st_size,
        "notes": [
            (
                "Warm cache_status hit skips raster and encode. "
                "prepare_s is 0 on that hit."
            ),
            "CPU PNG bytes are the preview. Matplotlib was not called.",
            (
                "The activation document uses the public Plotly figure "
                "for the same plan."
            ),
            (
                "Software WebGL timings, when present, are headless-Chrome "
                "observations only."
            ),
            inline_note,
        ],
        "browser": {
            "measured": False,
            "reason": "browser measurement was not requested",
        },
    }
    report["notes"] = [note for note in report["notes"] if note]
    if browser:
        report["browser"] = _browser_measurements(root, host, activation_path)
    path = root / "preview-benchmark.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(path)
    return report


def _browser_measurements(
    root: Path,
    host: Path,
    activation_path: Path,
) -> dict[str, Any]:
    chrome = _chrome_binary()
    if chrome is None:
        return {"measured": False, "reason": "chrome or chromium was not on PATH"}
    version = subprocess.run(
        [chrome, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    instrumented_host = root / "four-view-instrumented.html"
    instrumented_host.write_text(
        _inject(host.read_text(encoding="utf-8"), parent=True),
        encoding="utf-8",
    )
    # The image-only host references panel-N.png beside itself. The
    # instrumented copy lives next to those files.
    sibling = host.parent / "instrumented.html"
    sibling.write_text(instrumented_host.read_text(encoding="utf-8"), encoding="utf-8")
    instrumented_viewer = root / "single-view-instrumented.html"
    instrumented_viewer.write_text(
        _inject(activation_path.read_text(encoding="utf-8"), parent=False),
        encoding="utf-8",
    )
    try:
        image_only = _dump_page(chrome, sibling)
        viewer = _dump_page(chrome, instrumented_viewer)
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        return {
            "measured": False,
            "reason": f"headless Chrome failed: {type(exc).__name__}: {exc}",
            "chrome_version": (version.stdout or version.stderr).strip(),
        }
    return {
        "measured": True,
        "chrome_version": (version.stdout or version.stderr).strip(),
        "headless": True,
        "gpu": "unknown; this is not a hardware-GPU measurement",
        "image_only": _parse_browser_dump(image_only),
        "one_activation_document": _parse_browser_dump(viewer),
        "notes": [
            "image_only loads the four-view host. It does not set an iframe src.",
            "one_activation_document loads one Plotly HTML file.",
            "webglContexts counts getContext('webgl'|'webgl2') calls in that document.",
            "paints are the PerformancePaintTiming entries Chrome returned, if any.",
            (
                "An empty paints list means this dump did not report "
                "first contentful paint."
            ),
            (
                "navigation.loadEventEnd is read inside the load listener "
                "and can still be 0."
            ),
            "A paint startTime is not a GPU timestamp.",
            (
                "resource transferSize is the browser's resource timing value. "
                "File lengths in this report are the preview transfer measurement."
            ),
        ],
    }


def _chrome_binary() -> str | None:
    names = (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    )
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _inject(html: str, *, parent: bool) -> str:
    if parent:
        probe = _PARENT_PROBE
        if "<head>" in html:
            return html.replace("<head>", "<head>" + probe, 1)
        return probe + html
    if "<head>" in html:
        html = html.replace("<head>", "<head>" + _CHILD_PRELUDE, 1)
    else:
        html = _CHILD_PRELUDE + html
    if "</body>" in html:
        return html.replace("</body>", _CHILD_REPORT + "</body>", 1)
    return html + _CHILD_REPORT


def _dump_page(chrome: str, path: Path) -> str:
    profile = path.parent / f".chrome-{path.stem}"
    if profile.exists():
        shutil.rmtree(profile)
    command = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--user-data-dir={profile}",
        "--dump-dom",
        path.resolve().as_uri(),
    ]
    # This headless build prints the DOM and then does not exit, so the
    # reader stops once the measurement node is present.
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready:
                chunk = process.stdout.read1(65536)
                if not chunk:
                    break
                output.extend(chunk)
                text = output.decode("utf-8", errors="replace")
                if "benchmark-result" in text and "</pre>" in text:
                    break
                if 'data-mattervis-webgl="' in text and "</html>" in text:
                    break
            elif process.poll() is not None:
                break
    finally:
        process.kill()
        process.wait(timeout=5)
    text = output.decode("utf-8", errors="replace")
    if "benchmark-result" not in text and "data-mattervis-webgl" not in text:
        raise OSError("headless Chrome did not produce a measurement DOM")
    return text


def _parse_browser_dump(dom: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "webgl_contexts": None,
        "paints": None,
        "resources": None,
        "memory": None,
        "navigation": None,
        "first_contentful_paint_ms": None,
    }
    marker = 'id="benchmark-result"'
    if marker in dom:
        start = dom.find(">", dom.find(marker))
        end = dom.find("</pre>", start)
        if start != -1 and end != -1:
            payload = json.loads(dom[start + 1 : end])
            result["webgl_contexts"] = payload.get("webglContexts")
            result["paints"] = payload.get("paints")
            result["resources"] = payload.get("resources")
            memory = payload.get("memory")
            if (
                isinstance(memory, dict)
                and memory.get("usedJSHeapSize") == 10_000_000
                and memory.get("totalJSHeapSize") == 10_000_000
            ):
                # Headless Chrome reports this pair when precise heap
                # info is unavailable. It is not a measured allocation.
                memory = None
                result["memory_note"] = (
                    "performance.memory returned the 10000000-byte placeholder"
                )
            result["memory"] = memory
            result["navigation"] = payload.get("navigation")
            for paint in payload.get("paints") or []:
                if paint.get("name") == "first-contentful-paint":
                    result["first_contentful_paint_ms"] = paint.get("startTime")
    attribute = 'data-mattervis-webgl="'
    if attribute in dom and result["webgl_contexts"] is None:
        at = dom.find(attribute) + len(attribute)
        result["webgl_contexts"] = int(dom[at : dom.find('"', at)] or "0")
    if marker not in dom and attribute not in dom:
        result["parse_note"] = "Chrome dump did not include the measurement node"
    result["dom_bytes"] = len(dom.encode("utf-8"))
    return result


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Synthetic structure-preview benchmark"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scripts/_outputs/preview_benchmark"),
    )
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--inline-html", action="store_true")
    args = parser.parse_args(argv)
    report = run_preview_benchmark(
        args.output,
        browser=args.browser,
        inline_html=args.inline_html,
    )
    summary = {
        "report_path": report["report_path"],
        "atoms": report["fixture"]["atoms"],
        "cold_bytes": report["single_view"]["cold"]["bytes"],
        "warm_status": report["single_view"]["warm"]["cache_status"],
        "four_view_initial_transfer_bytes": report["four_view"][
            "initial_transfer_bytes"
        ],
        "activation_bytes": report["activation"]["bytes"],
        "browser_measured": report["browser"].get("measured"),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

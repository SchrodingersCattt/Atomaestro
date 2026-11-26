"""Server-rendered preview cache, camera preservation, and host pages."""

from __future__ import annotations

import re
import threading
from contextlib import suppress
from pathlib import Path

import pytest
from PIL import Image

from mat_viewer.agent import prepare_render
from mat_viewer.render.cpu import render_png
from mat_viewer.render.gpu_errors import GPUUnavailableError, GPUUnsupportedError
from mat_viewer.render.preview import (
    PreviewCache,
    PreviewNotFound,
    PreviewUnsupported,
    activation_html,
    build_preview_request,
    preview_cache_key,
    render_preview,
    synthetic_ch_pair_grid,
    write_camera_swap_page,
    write_preview_host,
)
from mat_viewer.render.preview_benchmark import benchmark_scene

_SCENE = {
    "atoms": [
        {"elem": "C", "cart": [0.0, 0.0, 0.0], "atom_radius": 0.35},
        {"elem": "H", "cart": [1.09, 0.0, 0.0], "atom_radius": 0.20},
    ],
    "bonds": [{"i": 0, "j": 1}],
    "matrix": [[4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 4.0]],
}
_CAMERA = {
    "position": [2.0, 2.0, 8.0],
    "target": [2.0, 2.0, 2.0],
    "up": [0.0, 1.0, 0.0],
    "projection": "orthographic",
    "ortho_scale": 3.0,
}
_STYLE = {"representation": "ball_stick", "show_cell": True, "show_hydrogen": True}


def _preview(cache, **overrides):
    payload = {
        "source": _SCENE,
        "cache": cache,
        "namespace": "alpha",
        "camera": _CAMERA,
        "style": _STYLE,
        "width": 96,
        "height": 64,
        "scale": 1,
        "backend": "cpu",
        "image_format": "png",
    }
    payload.update(overrides)
    return render_preview(**payload)


def test_cpu_preview_matches_render_png_and_keeps_cell_bonds_camera():
    cache = PreviewCache()
    image = _preview(cache)
    plan = prepare_render(
        _SCENE,
        camera=_CAMERA,
        render_spec={
            **_STYLE,
            "width": 96,
            "height": 64,
            "scale": 1,
            "backend": "cpu",
        },
    )

    assert image.data == render_png(plan).data
    assert image.plan_sha256 == plan.fingerprint()
    assert image.summary["atoms"] == 2
    assert image.summary["bonds"] == 1
    assert image.summary["periodic_cell"] is True
    assert image.camera["projection"] == "orthographic"
    assert image.camera["position"] == pytest.approx(_CAMERA["position"])
    assert image.camera["target"] == pytest.approx(_CAMERA["target"])
    assert image.camera["up"] == pytest.approx(_CAMERA["up"])
    assert image.camera["ortho_scale"] == pytest.approx(3.0)
    with Image.open(__import__("io").BytesIO(image.data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == (96, 64)
    material = build_preview_request(
        _SCENE,
        namespace="ignored",
        camera=_CAMERA,
        style=_STYLE,
        width=96,
        height=64,
        scale=1,
    ).cache_material()
    assert material is not None
    assert "namespace" not in material
    assert preview_cache_key(material) == image.cache_key


def test_cache_hit_skips_raster_and_namespaces_do_not_share_entries():
    cache = PreviewCache()
    first = _preview(cache)
    second = _preview(cache)
    other = _preview(cache, namespace="beta")

    assert first.cache_status == "miss"
    assert first.rasterized is True
    assert second.cache_status == "hit"
    assert second.rasterized is False
    assert second.timing_s["render_encode"] == 0.0
    assert second.timing_s["prepare"] == 0.0
    assert second.data == first.data
    assert second.ticket == first.ticket
    assert other.cache_status == "miss"
    assert other.ticket != first.ticket
    assert other.cache_key == first.cache_key
    assert cache.redeem(first.ticket).namespace == "alpha"
    with pytest.raises(PreviewNotFound):
        cache.redeem("not-a-ticket")


def test_camera_style_and_format_change_the_cache_key():
    cache = PreviewCache()
    original = _preview(cache)
    moved = _preview(
        cache,
        camera={**_CAMERA, "position": [2.0, -6.0, 2.0], "up": [0.0, 0.0, 1.0]},
    )
    hidden = _preview(cache, style={**_STYLE, "show_cell": False})
    webp = _preview(cache, image_format="webp")

    assert moved.cache_key != original.cache_key
    assert moved.camera["position"] == pytest.approx([2.0, -6.0, 2.0])
    assert moved.summary["periodic_cell"] is True
    assert hidden.summary["periodic_cell"] is False
    assert hidden.cache_key != original.cache_key
    assert webp.cache_key != original.cache_key
    assert webp.media_type == "image/webp"
    with Image.open(__import__("io").BytesIO(webp.data)) as decoded:
        assert decoded.format == "WEBP"
        assert decoded.size == (96, 64)


def test_explicit_fitted_camera_reuses_the_raster():
    cache = PreviewCache()
    fitted = _preview(cache, camera=None)
    again = _preview(cache, camera=fitted.camera)

    assert again.plan_sha256 == fitted.plan_sha256
    assert again.cache_key != fitted.cache_key
    assert again.rasterized is False
    assert again.cache_status == "hit"
    assert again.data == fitted.data


def test_cache_budget_counts_retained_source_bytes():
    scene = {**_SCENE, "preview_padding": "x" * 50_000}
    roomy = PreviewCache(max_entries=2, max_bytes=8_000_000)
    image = render_preview(
        scene,
        cache=roomy,
        namespace="budget",
        camera=_CAMERA,
        style=_STYLE,
        width=48,
        height=32,
        scale=1,
    )
    assert image.retained is True
    assert len(image.data) < 8_000
    tight = PreviewCache(max_entries=4, max_bytes=len(image.data) + 1_000)
    kept_out = render_preview(
        scene,
        cache=tight,
        namespace="budget",
        camera=_CAMERA,
        style=_STYLE,
        width=48,
        height=32,
        scale=1,
    )
    assert kept_out.retained is False
    assert kept_out.ticket is None
    assert kept_out.data == image.data
    again = render_preview(
        scene,
        cache=tight,
        namespace="budget",
        camera=_CAMERA,
        style=_STYLE,
        width=48,
        height=32,
        scale=1,
    )
    assert again.cache_status == "miss"
    assert again.retained is False


def test_eviction_drops_tickets_and_oversized_images_are_not_retained():
    cache = PreviewCache(max_entries=1, max_bytes=8_000_000)
    first = _preview(cache)
    _preview(
        cache,
        camera={**_CAMERA, "position": [8.0, 2.0, 2.0], "up": [0.0, 0.0, 1.0]},
    )
    with pytest.raises(PreviewNotFound):
        cache.redeem(first.ticket)

    tiny = PreviewCache(max_entries=4, max_bytes=32)
    overflow = _preview(tiny)
    assert overflow.retained is False
    assert overflow.ticket is None
    assert overflow.data.startswith(b"\x89PNG")


def test_concurrent_requests_rasterize_once():
    cache = PreviewCache()
    waiter_ready = threading.Event()
    calls = []
    preview_module = __import__(
        "mat_viewer.render.preview_cache", fromlist=["encode_preview_raster"]
    )
    original_encode = preview_module.encode_preview_raster
    original_join = PreviewCache._join

    def slow(plan, *, backend, image_format):
        calls.append(1)
        assert waiter_ready.wait(timeout=5)
        return original_encode(plan, backend=backend, image_format=image_format)

    def join(self, namespace, request_key):
        flight, role = original_join(self, namespace, request_key)
        if role == "wait":
            waiter_ready.set()
        return flight, role

    monkey = pytest.MonkeyPatch()
    monkey.setattr(preview_module, "encode_preview_raster", slow)
    monkey.setattr(PreviewCache, "_join", join)
    results = []

    def run():
        results.append(_preview(cache))

    try:
        threads = [threading.Thread(target=run), threading.Thread(target=run)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        monkey.undo()

    assert calls == [1]
    assert {item.cache_status for item in results} == {"miss", "coalesced"}
    assert results[0].data == results[1].data


def test_matplotlib_and_plotly_are_not_preview_backends_and_gpu_does_not_fall_back():
    cache = PreviewCache()
    with pytest.raises(PreviewUnsupported, match="Matplotlib"):
        _preview(cache, backend="matplotlib")
    with pytest.raises(PreviewUnsupported, match="Plotly"):
        _preview(cache, backend="plotly")
    with pytest.raises(PreviewUnsupported, match="WebP"):
        _preview(cache, backend="gpu", image_format="webp")

    def fail(*args, **kwargs):
        raise AssertionError("CPU raster was used for a GPU preview")

    monkey = pytest.MonkeyPatch()
    monkey.setattr("mat_viewer.render.cpu.render_png", fail)
    try:
        with suppress(GPUUnavailableError, GPUUnsupportedError):
            _preview(cache, backend="gpu")
    finally:
        monkey.undo()


def test_activation_html_replays_the_same_plan_without_a_cdn_script(tmp_path):
    cache = PreviewCache()
    image = _preview(cache)
    html = activation_html(cache, image.ticket).decode("utf-8")
    host = write_preview_host(tmp_path, [image], cache=cache, activate=True)
    page = host.read_text(encoding="utf-8")
    panel = (tmp_path / "panel-0.html").read_text(encoding="utf-8")
    script_src = re.compile(r"<script[^>]+src=['\"][^'\"]*cdn\.plot\.ly", re.I)

    assert f"mattervis-preview-plan:{image.plan_sha256}" in html
    assert "Plotly.newPlot" in html
    assert script_src.search(html) is None
    assert "Plotly.newPlot" not in page
    assert "plotly.min.js" not in page
    assert 'data-src="panel-0.html"' in page
    assert re.search(r"<iframe[^>]*\ssrc=", page) is None
    assert "Plotly.newPlot" in panel
    assert re.search(r"src=['\"]plotly\.min\.js['\"]", panel)
    assert script_src.search(panel) is None
    assert (tmp_path / "plotly.min.js").is_file()
    assert image.plan_sha256 in panel


def test_camera_swap_page_has_no_webgl_host(tmp_path):
    cache = PreviewCache()
    first = _preview(cache)
    second = _preview(
        cache,
        camera={**_CAMERA, "position": [8.0, 2.0, 2.0], "up": [0.0, 0.0, 1.0]},
    )
    path = write_camera_swap_page(
        tmp_path / "swap.html",
        [("plus-z", first.data), ("plus-x", second.data)],
        width=first.width,
        height=first.height,
    )
    text = path.read_text(encoding="utf-8")

    assert "Plotly" not in text
    assert "<canvas" not in text
    assert "<iframe" not in text
    assert 'data-frame="swap-0.png"' in text
    assert (tmp_path / "swap-1.png").read_bytes() == second.data


def test_benchmark_fixture_is_an_explicit_several_hundred_atom_grid():
    scene = benchmark_scene()

    assert len(scene["atoms"]) == 360
    assert len(scene["bonds"]) == 180
    assert scene["atoms"][0]["elem"] == "C"
    assert scene["atoms"][1]["elem"] == "H"
    assert scene["bonds"][0] == {"i": 0, "j": 1}
    grid = synthetic_ch_pair_grid(nx=6, ny=6, nz=5)
    assert grid == scene


def test_preview_http_contract(tmp_path):
    from flask import Blueprint, Flask

    from mat_viewer.api.v2_preview import register_preview_routes

    app = Flask(__name__)
    blueprint = Blueprint("mat_viewer_api_v2", __name__, url_prefix="/api/v2")
    register_preview_routes(blueprint, None)
    app.register_blueprint(blueprint)
    client = app.test_client()
    body = {
        "namespace": "host-a",
        "scene": _SCENE,
        "camera": _CAMERA,
        "style": _STYLE,
        "width": 80,
        "height": 48,
        "scale": 1,
        "backend": "cpu",
        "format": "png",
    }
    created = client.post("/api/v2/preview", json=body)
    assert created.status_code == 200
    payload = created.get_json()
    assert payload["cache_status"] == "miss"
    assert payload["summary"]["periodic_cell"] is True
    assert payload["summary"]["bonds"] == 1
    assert payload["interactive"] is False
    image = client.get(payload["image_path"])
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    assert image.data.startswith(b"\x89PNG")
    assert image.headers["Cache-Control"] == "private, no-store"
    warmed = client.post("/api/v2/preview", json=body)
    assert warmed.get_json()["cache_status"] == "hit"
    other = client.post("/api/v2/preview", json={**body, "namespace": "host-b"})
    assert other.get_json()["cache_status"] == "miss"
    assert other.get_json()["ticket"] != payload["ticket"]
    raw = client.post(
        "/api/v2/preview",
        json=body,
        headers={"Accept": "image/png"},
    )
    assert raw.mimetype == "image/png"
    assert raw.headers["X-MatterVis-Cache-Status"] == "hit"
    missing = client.get("/api/v2/preview/image/missing-ticket")
    assert missing.status_code == 404
    activated = client.get(payload["activation"]["path"])
    assert activated.status_code == 200
    assert activated.mimetype == "text/html"
    assert payload["plan_sha256"].encode() in activated.data
    assert b"Plotly.newPlot" in activated.data
    assert not re.search(br"<script[^>]+src=['\"][^'\"]*cdn\.plot\.ly", activated.data)
    rejected = client.post("/api/v2/preview", json={**body, "backend": "matplotlib"})
    assert rejected.status_code == 400
    assert "Matplotlib" in rejected.get_json()["error"]
    unknown = client.post("/api/v2/preview", json={**body, "orbit": 1})
    assert unknown.status_code == 400
    cif_path = (
        Path(__file__).resolve().parents[1] / "fixtures/visible_frame/two-atom.cif"
    )
    cif = cif_path.read_text(encoding="utf-8")
    cif_response = client.post(
        "/api/v2/preview",
        json={
            "namespace": "cif-host",
            "cif": cif,
            "width": 64,
            "height": 48,
            "style": {"show_cell": True, "show_hydrogen": True},
            "camera": _CAMERA,
        },
    )
    assert cif_response.status_code == 200
    cif_payload = cif_response.get_json()
    assert cif_payload["summary"]["periodic_cell"] is True
    assert cif_payload["summary"]["atoms"] >= 1


def test_dash_app_registers_the_preview_route(tmp_path):
    from mat_viewer.app import create_app

    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    response = app.server.test_client().post("/api/v2/preview", json={})
    assert response.status_code == 400

"""Matched-size synthetic preview benchmark.

The render is the public CPU raster at 900x720 for a 360-atom explicit
grid. It is marked slow because four views plus one Plotly document do
not belong in the quick subset. The test does not change the timeout.
"""

from __future__ import annotations

import re

import pytest

from mat_viewer.render.preview_benchmark import run_preview_benchmark


@pytest.mark.slow
def test_matched_size_preview_benchmark(tmp_path):
    report = run_preview_benchmark(tmp_path, browser=False, inline_html=False)
    cold = report["single_view"]["cold"]
    warm = report["single_view"]["warm"]
    panels = report["four_view"]["panels"]
    plus_z = next(item for item in panels if item["name"] == "plus-z")

    assert report["fixture"]["atoms"] == 360
    assert report["fixture"]["bonds"] == 180
    assert report["fixture"]["matplotlib"] == "not used"
    assert cold["cache_status"] == "miss"
    assert cold["rasterized"] is True
    assert cold["width"] == 900
    assert cold["height"] == 720
    assert cold["summary"]["atoms"] == 360
    assert cold["summary"]["bonds"] == 180
    assert cold["summary"]["periodic_cell"] is True
    assert cold["bytes"] > 0
    assert warm["cache_status"] == "hit"
    assert warm["rasterized"] is False
    assert warm["render_encode_s"] == 0.0
    assert warm["prepare_s"] == 0.0
    assert warm["bytes"] == cold["bytes"]
    assert warm["plan_sha256"] == cold["plan_sha256"]
    assert len(panels) == 4
    assert {item["name"] for item in panels} == {
        "plus-z",
        "plus-x",
        "plus-y",
        "diagonal",
    }
    assert len({item["cache_key"] for item in panels}) == 4
    assert all(item["rasterized"] is True for item in panels)
    assert all(item["summary"]["periodic_cell"] is True for item in panels)
    assert plus_z["plan_sha256"] == cold["plan_sha256"]
    assert plus_z["camera"]["projection"] == "orthographic"
    assert report["four_view"]["initial_transfer_bytes"] == (
        report["four_view"]["image_bytes"] + report["four_view"]["host_html_bytes"]
    )
    activation_payload = (
        report["activation"]["bytes"] + report["activation"]["plotlyjs_bytes"]
    )
    assert report["four_view"]["initial_transfer_bytes"] < activation_payload

    host = (tmp_path / "four-view" / "index.html").read_text(encoding="utf-8")
    swap = (tmp_path / "camera-swap.html").read_text(encoding="utf-8")
    activation = (tmp_path / "single-view.html").read_text(encoding="utf-8")
    script_src = re.compile(r"<script[^>]+src=['\"][^'\"]*cdn\.plot\.ly", re.I)

    assert "Plotly.newPlot" not in host
    assert "plotly.min.js" not in host
    assert "<iframe" not in host
    assert "cdn.plot.ly" not in host
    assert "Plotly" not in swap
    assert "<canvas" not in swap
    assert "<iframe" not in swap
    assert cold["plan_sha256"] in activation
    assert "Plotly.newPlot" in activation
    assert re.search(r"src=['\"]plotly\.min\.js['\"]", activation)
    assert script_src.search(activation) is None
    assert report["browser"]["measured"] is False

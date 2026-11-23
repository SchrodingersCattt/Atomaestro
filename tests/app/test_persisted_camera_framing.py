"""A stored camera that misses the scene must not survive a restart or a drag."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mat_viewer.app import ViewerBackend, _camera_store_payload, _persist_relayout_camera
from mat_viewer.render.screen_projection import camera_frames_points
from mat_viewer.render.viewport import _plotly_camera_from_scene
from mat_viewer.render.viewport_spec import resolve_viewport


NACL = Path("benchmarks/structures/ionic_nacl.cif")
OFFSCREEN = {
    "eye": {"x": 0.04, "y": 0.02, "z": 0.01},
    "center": {"x": 0.0, "y": 1.6, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}
# A close eye at (0.5, 0.5, 0.5) leaves only one formula-unit centre inside
# the frustum, so the span test treats it as a missed scene. This nudge
# still shows both atoms.
FRAMED_ZOOM = {
    "eye": {"x": 1.3, "y": 1.2, "z": 1.1},
    "center": {"x": 0.02, "y": -0.01, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}


def _backend(tmp_path: Path) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "ionic_nacl": {
                        "cif_path": str(NACL.resolve()),
                        "title": "ionic_nacl",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def _points_and_viewport(backend: ViewerBackend, scene_id: str):
    state = backend.get_state(scene_id)
    scene = backend.scene_for_state(state)
    style = backend.style_for_state(state, scene=scene)
    viewport = resolve_viewport(scene, style)
    points = [atom["cart"] for atom in scene.get("draw_atoms") or []]
    camera = _plotly_camera_from_scene(scene, style)
    return points, viewport, camera


def _shows_atoms(backend: ViewerBackend, scene_id: str) -> bool:
    points, viewport, camera = _points_and_viewport(backend, scene_id)
    return camera_frames_points(
        camera,
        points,
        ranges=viewport.ranges,
        aspectratio=viewport.aspectratio,
    )


def test_unit_cell_nacl_default_shows_both_atoms(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        # Current inorganic defaults use the complete conventional cell.
        assert state["display_mode"] == "unit_cell"
        points, _, _ = _points_and_viewport(backend, scene_id)
        assert len(points) >= 2
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_restart_replaces_camera_that_misses_the_scene(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"camera": OFFSCREEN},
            scene_id=scene_id,
            broadcast=False,
        )
        assert not _shows_atoms(backend, scene_id)
        backend.flush_scene_store()
    finally:
        backend.close()

    restarted = _backend(tmp_path)
    try:
        assert _shows_atoms(restarted, scene_id)
        restored = restarted.get_state(scene_id)["camera"]
        assert restored["center"]["y"] != OFFSCREEN["center"]["y"]
        restarted.camera_action("reset", scene_id=scene_id, broadcast=False)
        assert _shows_atoms(restarted, scene_id)
    finally:
        restarted.close()


def test_restart_keeps_a_zoom_that_still_shows_the_atoms(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"camera": FRAMED_ZOOM}, scene_id=scene_id, broadcast=False)
        assert _shows_atoms(backend, scene_id)
        backend.flush_scene_store()
    finally:
        backend.close()

    restarted = _backend(tmp_path)
    try:
        assert _shows_atoms(restarted, scene_id)
        restored = restarted.get_state(scene_id)["camera"]
        assert restored["eye"]["x"] == FRAMED_ZOOM["eye"]["x"]
        assert restored["center"]["y"] == FRAMED_ZOOM["center"]["y"]
    finally:
        restarted.close()


def _persist(backend: ViewerBackend, scene_id: str, camera: dict) -> dict:
    state = backend.get_state(scene_id)
    return _persist_relayout_camera(
        backend,
        {
            "scene_id": scene_id,
            "scene_generation": state["scene_generation"],
            "camera_revision": state["camera_revision"],
            "relayout": {"scene.camera": camera},
        },
        _camera_store_payload(scene_id, state.get("camera")),
        now=10.0,
        last_commit=0.0,
        min_interval_s=0.25,
    )


@pytest.mark.parametrize("action", ["projection", "reset"])
def test_camera_command_journal_keeps_the_confirmed_update_kind(tmp_path: Path, action: str):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        before = backend.get_state(scene_id)
        cursor = backend.latest_figure_seq()
        camera = backend.camera_action(action, scene_id=scene_id, type="orthographic")
        events = backend.figure_broadcasts_since(cursor)
        assert len(events) == 1
        event = events[0]
        assert event["update_kind"] == "camera"
        assert event["payload"]["camera"] == camera
        assert event["camera_version"] > before["camera_version"]
        assert event["geometry_version"] == before["geometry_version"]
    finally:
        backend.close()


@pytest.mark.parametrize("include_projection", [False, True])
def test_partial_capture_after_projection_ignores_stale_camera_store(
    tmp_path: Path, include_projection: bool
):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        old_camera = _camera_store_payload(scene_id, backend.get_camera(scene_id))
        backend.set_projection("orthographic", scene_id=scene_id, broadcast=False)
        before = backend.get_state(scene_id)
        relayout = {"scene.camera.eye": FRAMED_ZOOM["eye"]}
        if include_projection:
            relayout["scene.camera.projection.type"] = "orthographic"
        result = _persist_relayout_camera(
            backend,
            {
                "scene_id": scene_id,
                "scene_generation": before["scene_generation"],
                "camera_revision": before["camera_revision"],
                "relayout": relayout,
            },
            old_camera,
            now=10.0,
            last_commit=0.0,
            min_interval_s=0.25,
        )
        assert result is not None
        after = backend.get_state(scene_id)
        assert after["projection"] == "orthographic"
        assert after["camera"]["projection"] == {"type": "orthographic"}
        assert after["camera_revision"] == before["camera_revision"]
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_orthographic_unit_cell_drag_that_misses_is_refit(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        before = backend.get_state(scene_id)
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        result = _persist(backend, scene_id, missed)
        assert result is not None
        assert result["refit"] is True
        assert result["view_update"]["payload"]["command"] == "refit"
        assert result["view_update"]["update_kind"] == "camera"
        updates = backend.figure_broadcasts_since(0)
        assert updates[-1]["reason"] == "camera-refit"
        assert updates[-1]["payload"]["camera"] == result["view_update"]["payload"]["camera"]
        state = backend.get_state(scene_id)
        assert state["projection"] == "orthographic"
        assert state["camera"]["projection"]["type"] == "orthographic"
        assert state["camera"]["center"]["x"] != missed["center"]["x"]
        assert int(state["camera_revision"]) == int(before["camera_revision"]) + 1
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_refit_advances_camera_clock_when_default_is_already_stored(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.set_projection("orthographic", scene_id=scene_id, broadcast=False)
        backend.refit_scene_camera(scene_id, force=True)
        before = backend.get_state(scene_id)
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }

        result = _persist(backend, scene_id, missed)

        assert result is not None
        assert result["refit"] is True
        after = backend.get_state(scene_id)
        assert after["camera"] == before["camera"]
        # The browser moved away from this stored default. A new delivery
        # revision is needed even though the persisted camera is unchanged.
        assert after["camera_revision"] == before["camera_revision"] + 1
        assert after["camera_version"] == before["camera_version"] + 1
        update = result["view_update"]
        assert update["update_kind"] == "camera"
        assert update["payload"]["command"] == "refit"
        assert update["camera_version"] == after["camera_version"]
        assert backend.figure_broadcasts_since(0)[-1] == update
    finally:
        backend.close()


def test_drag_that_still_frames_is_stored_unchanged(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        before = backend.get_state(scene_id)
        nudged = {
            "eye": {"x": 1.3, "y": 1.2, "z": 1.1},
            "center": {"x": 0.02, "y": -0.01, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        result = _persist(backend, scene_id, nudged)
        assert result is not None
        assert result["refit"] is False
        assert result["view_update"] is None
        state = backend.get_state(scene_id)
        assert state["camera"]["eye"]["x"] == nudged["eye"]["x"]
        assert state["camera"]["center"]["y"] == nudged["center"]["y"]
        assert state["projection"] == "orthographic"
        assert int(state["camera_revision"]) == int(before["camera_revision"])
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_late_pose_follows_visibility_for_both_projections(tmp_path: Path):
    """A late capture is judged by whether it shows the structure.

    A center of 0.4 or 0.6 still frames this cell and is stored. A center
    of 20 does not, so the server replaces it and advances the revision.
    The same decision applies to a dotted center and to both projections.
    An older revision is rejected and the stored camera is left alone.
    """
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        for projection in ("orthographic", "perspective"):
            backend.set_projection(projection, scene_id=scene_id, broadcast=False)
            before = backend.get_state(scene_id)
            eye = before["camera"]["eye"]
            up = before["camera"]["up"]

            def camera_at(
                center: tuple[float, float, float],
                eye: dict = eye,
                up: dict = up,
                projection: str = projection,
            ) -> dict:
                return {
                    "eye": eye,
                    "center": {"x": center[0], "y": center[1], "z": center[2]},
                    "up": up,
                    "projection": {"type": projection},
                }

            for center in ((0.4, 0.0, 0.0), (0.6, 0.0, 0.0)):
                result = _persist(backend, scene_id, camera_at(center))
                assert result is not None
                assert result["refit"] is False
                assert result["view_update"] is None
                state = backend.get_state(scene_id)
                assert state["camera"]["center"]["x"] == center[0]
                assert state["camera"]["projection"]["type"] == projection
                assert int(state["camera_revision"]) == int(before["camera_revision"])
                assert _shows_atoms(backend, scene_id)

            dotted_keep = _persist_relayout_camera(
                backend,
                {
                    "scene_id": scene_id,
                    "scene_generation": before["scene_generation"],
                    "camera_revision": before["camera_revision"],
                    "relayout": {"scene.camera.center": {"x": 1.0, "y": 0.0, "z": 0.0}},
                },
                _camera_store_payload(scene_id, before.get("camera")),
                now=10.0,
                last_commit=0.0,
                min_interval_s=0.25,
            )
            assert dotted_keep is not None
            assert dotted_keep["refit"] is False
            assert backend.get_state(scene_id)["camera"]["center"]["x"] == 1.0
            assert _shows_atoms(backend, scene_id)

            missed = _persist(backend, scene_id, camera_at((20.0, 20.0, 20.0)))
            assert missed is not None
            assert missed["refit"] is True
            assert missed["view_update"]["payload"]["command"] == "refit"
            fitted = backend.get_state(scene_id)
            assert fitted["camera"]["center"]["x"] != 20.0
            assert fitted["camera"]["projection"]["type"] == projection
            assert int(fitted["camera_revision"]) == int(before["camera_revision"]) + 1
            assert _shows_atoms(backend, scene_id)

            dotted_miss = _persist_relayout_camera(
                backend,
                {
                    "scene_id": scene_id,
                    "scene_generation": fitted["scene_generation"],
                    "camera_revision": fitted["camera_revision"],
                    "relayout": {"scene.camera.center": {"x": 20.0, "y": 20.0, "z": 20.0}},
                },
                _camera_store_payload(scene_id, fitted.get("camera")),
                now=20.0,
                last_commit=0.0,
                min_interval_s=0.25,
            )
            assert dotted_miss is not None
            assert dotted_miss["refit"] is True
            assert dotted_miss["view_update"]["payload"]["command"] == "refit"
            after_dotted = backend.get_state(scene_id)
            assert after_dotted["camera"]["center"]["x"] != 20.0
            assert after_dotted["camera"]["projection"]["type"] == projection
            assert _shows_atoms(backend, scene_id)

            stale = _persist_relayout_camera(
                backend,
                {
                    "scene_id": scene_id,
                    "scene_generation": after_dotted["scene_generation"],
                    "camera_revision": int(after_dotted["camera_revision"]) - 1,
                    "relayout": {"scene.camera": camera_at((0.6, 0.0, 0.0))},
                },
                _camera_store_payload(scene_id, after_dotted.get("camera")),
                now=30.0,
                last_commit=0.0,
                min_interval_s=0.25,
            )
            assert stale is not None
            assert stale["repaint"] is True
            assert stale["refit"] is False
            assert stale["view_update"] is None
            untouched = backend.get_state(scene_id)
            assert untouched["camera"]["center"] == after_dotted["camera"]["center"]
            assert int(untouched["camera_revision"]) == int(after_dotted["camera_revision"])

        other = backend.create_scene(structure="ionic_nacl", label="other")
        other_id = other["id"]
        assert other_id != scene_id
        assert backend.active_scene_id() == other_id
        owned = backend.get_state(scene_id)
        other_before = backend.get_state(other_id).get("camera")
        parked = {
            "eye": owned["camera"]["eye"],
            "center": {"x": 0.6, "y": 0.0, "z": 0.0},
            "up": owned["camera"]["up"],
            "projection": {"type": owned["camera"]["projection"]["type"]},
        }
        cross = _persist(backend, scene_id, parked)
        assert cross is not None
        assert cross["scene_id"] == scene_id
        assert backend.get_state(scene_id)["camera"]["center"]["x"] == 0.6
        assert backend.active_scene_id() == other_id
        assert backend.get_state(other_id).get("camera") == other_before
    finally:
        backend.close()


def test_figure_stamps_a_framing_camera_without_rewriting_state(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        backend.patch_state(
            {
                "camera": missed,
                "display_mode": "unit_cell",
                "projection": "orthographic",
            },
            scene_id=scene_id,
            broadcast=False,
        )
        assert not _shows_atoms(backend, scene_id)
        state = backend.get_state(scene_id)
        fig, _ = backend.figure_for_state(state)
        assert backend.get_state(scene_id)["camera"]["center"] == missed["center"]
        rendered = fig.layout.scene.camera.to_plotly_json()
        points, viewport, _camera = _points_and_viewport(backend, scene_id)
        assert camera_frames_points(
            rendered,
            points,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            projection="orthographic",
        )
        assert rendered["projection"]["type"] == "orthographic"
    finally:
        backend.close()


def test_display_capture_refits_a_camera_the_new_scope_cannot_show(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        backend.patch_state(
            {
                "camera": missed,
                "display_mode": "unit_cell",
                "projection": "orthographic",
            },
            scene_id=scene_id,
            broadcast=False,
        )
        assert not _shows_atoms(backend, scene_id)
        state = backend.refit_scene_camera(scene_id)
        assert state["projection"] == "orthographic"
        assert state["camera"]["projection"]["type"] == "orthographic"
        assert state["camera"]["center"]["y"] != missed["center"]["y"]
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_reset_recovers_an_offscreen_camera(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"camera": OFFSCREEN}, scene_id=scene_id, broadcast=False)
        assert not _shows_atoms(backend, scene_id)
        backend.camera_action("reset", scene_id=scene_id, broadcast=False)
        assert _shows_atoms(backend, scene_id)
        assert backend.get_state(scene_id)["camera"]["center"] == {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
        }
    finally:
        backend.close()

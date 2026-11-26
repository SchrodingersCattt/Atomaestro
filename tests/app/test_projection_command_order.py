"""Real callback regressions for commands accepted before an older commit."""
from contextlib import contextmanager, nullcontext
import threading
from types import SimpleNamespace

from mat_viewer.app import create_app
from mat_viewer.app import callbacks_view


def _projection_callback(app):
    return next(
        spec["callback"].__wrapped__
        for spec in app.callback_map.values()
        if spec.get("callback")
        and spec["callback"].__name__ == "sync_view_projection_from_state"
    )


def _exercise_pending_projection(tmp_path, monkeypatch, *, other_scene):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    release_old = threading.Event()
    threads = []
    errors = []
    try:
        backend = app.crystal_backend
        scene_a = backend.active_scene_id()
        scene_b = (
            backend.create_scene(structure=backend.get_state()["structure"], label="B")["id"]
            if other_scene else scene_a
        )
        assert backend.get_state(scene_a)["projection"] == "perspective"
        assert backend.get_state(scene_b)["projection"] == "perspective"
        callback = _projection_callback(app)
        monkeypatch.setattr(callbacks_view, "callback_context", SimpleNamespace(
            triggered=[{"prop_id": "view-projection-ticket.data"}],
        ))
        old_at_commit = threading.Event()
        old_committed = threading.Event()
        new_progress = threading.Event()
        new_finished = threading.Event()
        original_patch = backend.patch_state

        def hold_old_commit(payload, *args, **kwargs):
            if (threading.current_thread().name == "old-projection"
                    and payload.get("projection") == "orthographic"):
                old_at_commit.set()
                if not release_old.wait(10):
                    error = AssertionError("old command was not released")
                    errors.append(error)  # set_projection's caller catches Exception.
                    raise error
            result = original_patch(payload, *args, **kwargs)
            if (threading.current_thread().name == "old-projection"
                    and payload.get("projection") == "orthographic"):
                old_committed.set()
            return result

        monkeypatch.setattr(backend, "patch_state", hold_old_commit)
        # Observe the actual guard without replacing its lock. On pre-fix code
        # the callback never calls this helper and new_progress comes from
        # completion instead, deterministically putting the newer no-op first.
        original_guard = getattr(callbacks_view, "projection_command_lock", None)

        @contextmanager
        def observe_guard(scene_id):
            guard = original_guard(scene_id) if original_guard else nullcontext()
            if threading.current_thread().name == "new-projection":
                new_progress.set()
            with guard:
                yield

        monkeypatch.setattr(callbacks_view, "projection_command_lock", observe_guard, raising=False)

        def invoke(scene_id, projection, ticket, finished=None):
            try:
                callback(backend.get_state(scene_id), scene_id, projection,
                         {"projection": projection, "ticket": ticket})
            except BaseException as exc:
                errors.append(exc)
            finally:
                if finished is not None:
                    finished.set()
                    new_progress.set()

        old = threading.Thread(target=invoke, name="old-projection",
                               args=(scene_a, "orthographic", 1))
        threads.append(old)
        old.start()
        assert old_at_commit.wait(10), "old command never reached its real commit"
        new = threading.Thread(target=invoke, name="new-projection",
                               args=(scene_b, "orthographic" if other_scene else "perspective",
                                     2, new_finished))
        threads.append(new)
        new.start()
        if other_scene:
            # B must commit while A is still paused, not queue behind A.
            assert new_finished.wait(10), "an unrelated scene was blocked"
            assert backend.get_state(scene_b)["projection"] == "orthographic"
        else:
            assert new_progress.wait(10), "new command neither reached guard nor completed"
        release_old.set()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive(), "projection callback deadlocked"
        assert not errors, errors
        assert old_committed.is_set(), "old projection never actually committed"
        assert backend.get_state(scene_a)["projection"] == (
            "orthographic" if other_scene else "perspective"
        )
    finally:
        release_old.set()
        for thread in threads:
            thread.join(10)
        app.close_extensions()
        app.crystal_backend.close()


def test_newest_same_as_current_projection_survives_older_pending_commit(tmp_path, monkeypatch):
    _exercise_pending_projection(tmp_path, monkeypatch, other_scene=False)


def test_pending_projection_does_not_block_another_scene(tmp_path, monkeypatch):
    _exercise_pending_projection(tmp_path, monkeypatch, other_scene=True)

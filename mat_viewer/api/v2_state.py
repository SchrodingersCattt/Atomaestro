from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def _events_include_figure(events: list) -> bool:
    """A camera envelope is not a frame the canvas can paint."""
    return any(
        isinstance(event, dict) and event.get("type") == "figure" and event.get("figure")
        for event in events
    )


def _events_include_camera(events: list) -> bool:
    """The current journal already confirmed a pose for this scene."""
    return any(
        isinstance(event, dict)
        and str(event.get("update_kind") or event.get("kind") or "") == "camera"
        for event in events
    )


def register_state_routes(v2, backend) -> dict:
    @v2.get("/state")
    def get_state():
        return jsonify(backend.get_state(request.args.get("scene_id")))

    @v2.get("/healthz")
    def healthz_v2():
        return jsonify(backend.healthz())

    @v2.get("/view-updates")
    def view_updates():
        try:
            since = int(request.args.get("since", 0) or 0)
        except (TypeError, ValueError):
            since = 0
        events = backend.figure_broadcasts_since(since)
        scene_id = request.args.get("scene_id")
        if scene_id:
            scene_id = str(scene_id)
            events = [event for event in events if str(event.get("scene_id") or "") == scene_id]
            if not _events_include_figure(events):
                latest = backend.latest_figure_broadcast(scene_id)
                if (
                    isinstance(latest, dict)
                    and int(latest.get("figure_seq", 0) or 0) > since
                    and latest not in events
                ):
                    events = [latest, *events]
            if not _events_include_figure(events) and _events_include_camera(events):
                # The camera command hid the geometry body from the current
                # frame filter. Return that body only when this response also
                # carries the confirmed pose. A silent refit has no pose
                # envelope, and its old figure must not be painted alone.
                recover = getattr(backend, "reusable_scene_figure", None)
                recovered = recover(scene_id) if callable(recover) else None
                if (
                    isinstance(recovered, dict)
                    and int(recovered.get("figure_seq", 0) or 0) > since
                ):
                    events = [recovered, *events]
            pending = False
            terminal = False
            if not _events_include_figure(events):
                try:
                    try:
                        pending = bool(backend.ensure_scene_figure(scene_id, force=True))
                    except TypeError:
                        pending = bool(backend.ensure_scene_figure(scene_id))
                except KeyError:
                    # A tab can outlive its scene (for example after closing an
                    # upload).  This is a terminal state, not a render that can
                    # become ready through polling.
                    terminal = True
        else:
            pending = False
            terminal = False
        body = {
            "updates": events,
            "cursor": backend.latest_figure_seq(),
            "pending": pending,
            "terminal": terminal,
        }
        catalog = getattr(backend, "scene_catalog", None)
        if catalog is not None:
            body["scene_catalog"] = catalog(focus=False)
        return jsonify(body)

    @v2.post("/state")
    def post_state():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            state = backend.patch_state(payload, scene_id=_scene_id_from_request())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(state)

    @v2.get("/camera")
    def get_camera():
        return jsonify({"camera": backend.get_camera(scene_id=request.args.get("scene_id"))})

    @v2.post("/camera")
    def post_camera():
        payload = request.get_json(force=True, silent=True) or {}
        camera = payload.get("camera", payload)
        return jsonify({"camera": backend.set_camera(camera, scene_id=_scene_id_from_request())})

    @v2.post("/camera/action")
    def post_camera_action():
        payload = request.get_json(force=True, silent=True) or {}
        action = payload.get("action")
        if not action:
            return jsonify({"error": "action is required"}), 400
        broadcast = bool(payload.get("broadcast", True))
        rest = {key: value for key, value in payload.items() if key not in ("action", "scene_id", "broadcast")}
        return jsonify({"camera": backend.camera_action(action, scene_id=_scene_id_from_request(), broadcast=broadcast, **rest)})

    @v2.post("/upload")
    def upload_cif():
        if "file" not in request.files:
            return jsonify({"error": "missing multipart file field 'file'"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "empty filename"}), 400
        with perf_log.time_block(
            "http:upload",
            kind="http",
            filename=file.filename,
        ):
            content = file.read()
            try:
                with perf_log.time_block(
                    "upload:parse_and_register",
                    kind="event",
                    filename=file.filename,
                    bytes=len(content),
                ):
                    bundle = backend.add_uploaded_file_bytes(content, file.filename)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        meta = bundle.metadata()
        if getattr(bundle, "_upload_existing", False):
            meta["existing"] = True
        if getattr(bundle, "_upload_pending", False):
            meta["status"] = "pending"
            meta["job_id"] = getattr(bundle, "_upload_job_id", None)
        # The scene this upload owns. Callers must select this id. The
        # process-wide active scene can already belong to another browser
        # by the time the response is read.
        scene_id = backend.upload_scene_id(bundle)
        if scene_id:
            meta["scene_id"] = scene_id
        return jsonify(meta)

    @v2.get("/upload/<job_id>")
    def upload_status(job_id: str):
        """Poll completion of a native multipart upload.

        CIF parsing is deliberately asynchronous so the request thread stays
        responsive for large structures.  Returning the worker's exception via
        this small endpoint prevents a failed parser job from looking like a
        successful upload with an empty viewport.
        """
        try:
            return jsonify(backend.upload_status(job_id))
        except KeyError:
            return jsonify({"error": f"unknown upload job: {job_id}"}), 404

    @v2.get("/structures")
    def structures():
        return jsonify({"structures": backend.list_structures()})

    @v2.get("/scene/<name>")
    def scene(name: str):
        after_transforms = str(request.args.get("after_transforms", "")).lower() in {"1", "true", "yes", "on"}
        return jsonify(backend.get_scene_json(name, after_transforms=after_transforms))

    @v2.post("/topology")
    def topology():
        payload = request.get_json(force=True, silent=True) or {}
        state = backend.get_state(_scene_id_from_request())
        structure = payload.get("structure") or state.get("structure")
        center_index = payload.get("center_index")
        if center_index is None:
            return jsonify({"error": "center_index is required"}), 400
        try:
            cutoff = float(payload.get("cutoff", 10.0))
        except (TypeError, ValueError):
            return jsonify({"error": "cutoff must be numeric", "type": "ValueError"}), 400
        return jsonify(
            backend.query_topology(
                structure=structure,
                center_index=int(center_index),
                cutoff=cutoff,
                scene_id=_scene_id_from_request(),
                center_species=payload.get("center_species"),
                ligand_species=payload.get("ligand_species"),
                level=payload.get("level", "molecule"),
                enforce_enclosure=payload.get("enforce_enclosure", True),
                centroid_offset_frac=payload.get("centroid_offset_frac"),
                disorder_policy=payload.get("disorder_policy"),
            )
        )


    return locals()

"""Browser camera callbacks that must not live in the view registrar.

``camera-state-store`` is written only with ``allow_duplicate``. Reading
it from here is what records the pose the server accepted. A primary
writer beside those duplicates drops user-event dispatch on Dash 2.18.
"""

from __future__ import annotations

from dash import Input, Output


def register_camera_clientside(app) -> None:
    """Register the relayout capture and the accepted-camera book."""
    if not hasattr(app, "clientside_callback"):
        return
    app.clientside_callback(
        """
        function(value) {
          var click = window.__mvProjectionClick;
          if (!click || String(click.value) !== String(value)) {
            return window.dash_clientside.no_update;
          }
          var ticket = click.ticket;
          window.__mvProjectionClick = null;
          return {projection: String(value), ticket: ticket};
        }
        """,
        Output("view-projection-ticket", "data"),
        Input("view-projection", "value"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
            """
            function(relayout) {
          if (!relayout || typeof relayout !== "object") return window.dash_clientside.no_update;
          var keys = Object.keys(relayout);
          var touches = false;
          for (var i = 0; i < keys.length; i++) {
            var key = keys[i];
            if (key === "scene.camera" || key.indexOf("scene.camera.") === 0) touches = true;
            if (key === "scene" && relayout.scene && relayout.scene.camera) touches = true;
          }
          if (!touches) return window.dash_clientside.no_update;
          var gd = document.querySelector("#crystal-graph .js-plotly-plot");
          if (Number(window.__mvProgrammaticCameraDepth || 0) > 0) return window.dash_clientside.no_update;
          if (gd && (Number(gd.__mvInsideFrameReact || 0) > 0 || gd.__mvApplyingServerCamera ||
              gd.__mvRestoringDrag || Number(gd.__mvOwnedRelayoutDepth || 0) > 0)) {
            return window.dash_clientside.no_update;
          }
          var stamp = window.__mvProgrammaticCameraStamp;
          var stampedEye = stamp && stamp.eye;
          var relayoutEye = relayout["scene.camera.eye"] ||
            (relayout["scene.camera"] && relayout["scene.camera"].eye) ||
            (relayout.scene && relayout.scene.camera && relayout.scene.camera.eye);
          function eyeNear(left, right) {
            if (!left || !right) return false;
            var names = ["x", "y", "z"];
            for (var n = 0; n < names.length; n++) {
              var a = Number(left[names[n]] !== undefined ? left[names[n]] : left[n]);
              var b = Number(right[names[n]] !== undefined ? right[names[n]] : right[n]);
              if (!Number.isFinite(a) || !Number.isFinite(b) || Math.abs(a - b) > 1e-4) return false;
            }
            return true;
          }
          var wheelPose = window.__mvUnadoptedWheel && window.__mvUnadoptedWheel.camera;
          if (stampedEye && relayoutEye && eyeNear(relayoutEye, stampedEye) &&
              !(wheelPose && eyeNear(relayoutEye, wheelPose.eye))) {
            return window.dash_clientside.no_update;
          }
          var meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
          var canvasId = meta && meta.scene_id ? String(meta.scene_id) : "";
          var gestureId = window.__mvGestureSceneId ? String(window.__mvGestureSceneId) : "";
          var gestureKind = window.__mvGestureKind ? String(window.__mvGestureKind) : "";
          // Dash may deliver relayoutData after Plotly has already
          // painted the next scene and after the pointer gesture ended.
          // Keep the scene that owned the last pointer gesture as a
          // second fence so a late camera payload from scene A cannot be
          // reinterpreted as a drag on scene B. A new pointerdown refreshes
          // this marker for legitimate drags on B.
          var lastGestureId = window.__mvLastGestureSceneId
            ? String(window.__mvLastGestureSceneId) : "";
          if (gestureId && canvasId && gestureId !== canvasId && gestureKind !== "wheel") return window.dash_clientside.no_update;
          // A wheel still has to name the mounted scene below. The kind
          // alone does not retarget this relayout onto the current canvas.
          // A wheel timeout clears the active gesture id and kind together
          // but keeps the last scene fence. An empty kind still fails the
          // wheel exception, so a late relayout cannot move onto the next
          // canvas.
          if (!gestureId && lastGestureId && canvasId && lastGestureId !== canvasId && gestureKind !== "wheel") {
            return window.dash_clientside.no_update;
          }
          // A geometry frame emits its camera relayout after the depth
          // counter has already dropped. The canvas id is not a gesture,
          // and neither is a projection change. A pointer follow-up is a
          // pose that still names the pointer and is still inside the same
          // 1000ms release used after pointerup. Pointer cancel used to
          // leave that name in place with no release. The offscreen drag
          // is one of these poses, and a second one is still stored: the
          // first pointer-up sample is not the only commit.
          function poseRelayout(value) {
            var camera = value["scene.camera"] ||
              (value.scene && value.scene.camera) || null;
            if (camera && (camera.eye || camera.center || camera.up)) return true;
            var names = Object.keys(value);
            for (var p = 0; p < names.length; p++) {
              var item = names[p];
              if (item.indexOf("scene.camera.eye") === 0 ||
                  item.indexOf("scene.camera.center") === 0 ||
                  item.indexOf("scene.camera.up") === 0) return true;
            }
            return false;
          }
          function note(reason) {
            window.__mvCaptureDiag = {reason: reason};
          }
          if (!poseRelayout(relayout)) {
            note("projection-only");
            return window.dash_clientside.no_update;
          }
          var endedAt = Number(window.__mvGestureEndedAt || 0);
          var ownsCanvas = !!gestureId && (!canvasId || gestureId === canvasId);
          var livePointer = ownsCanvas && gestureKind !== "wheel" && !endedAt;
          var liveWheel = gestureKind === "wheel" && ownsCanvas;
          var age = endedAt ? (Date.now() - endedAt) : 0;
          var followUp = ownsCanvas && gestureKind === "pointer" && !!endedAt && age <= 1000;
          if (!livePointer && !liveWheel && !followUp) {
            note(gestureKind === "wheel" ? "wheel-other-scene" :
              (ownsCanvas && endedAt ? "follow-up-expired" : "no-gesture"));
            return window.dash_clientside.no_update;
          }
          note(liveWheel ? "wheel" : (livePointer ? "live-pointer" : "pointer-follow-up"));
          var sceneId = gestureId || canvasId;
          if (!sceneId) return window.dash_clientside.no_update;
          return {
            scene_id: sceneId,
            scene_generation: window.__mvGestureSceneGeneration,
            camera_revision: window.__mvGestureCameraRevision,
            relayout: relayout,
            seq: Date.now(),
          };
        }
            """,
        Output("camera-capture-store", "data"),
        Input("crystal-graph", "relayoutData"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(data) {
          if (!data || !data.scene_id || !data.camera) return window.dash_clientside.no_update;
          var id = String(data.scene_id);
          var book = window.__mvServerCameraByScene || (window.__mvServerCameraByScene = {});
          var previous = book[id];
          var revision = data.camera_revision;
          if (previous && revision !== undefined && revision !== null &&
              Number(revision) < Number(previous.camera_revision || 0)) {
            return window.dash_clientside.no_update;
          }
          book[id] = {
            camera: data.camera,
            camera_revision: revision === undefined || revision === null
              ? (previous ? previous.camera_revision : 0)
              : Number(revision),
            scene_generation: data.scene_generation !== undefined && data.scene_generation !== null
              ? data.scene_generation
              : (previous ? previous.scene_generation : null),
            server_started_at: data.server_started_at !== undefined && data.server_started_at !== null
              ? data.server_started_at
              : (previous ? previous.server_started_at : null)
          };
          if (typeof window.__mvApplyServerCamera === "function") {
            window.__mvApplyServerCamera(id);
          }
          return window.dash_clientside.no_update;
        }
        """,
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("camera-state-store", "data"),
        prevent_initial_call=True,
    )

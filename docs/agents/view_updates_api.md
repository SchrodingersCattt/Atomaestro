# View update protocol

Atomaestro uses one delivery envelope for Dash, HTTP fallback, and the figure
WebSocket:

```json
{
  "type": "view_update",
  "update_kind": "overlay|display|camera|geometry|analysis",
  "operation_id": "display",
  "scene_id": "...",
  "geometry_version": 3,
  "display_version": 4,
  "camera_version": 2,
  "payload": {}
}
```

`geometry_version` scopes every local patch. A display, overlay, or analysis
patch is rejected when it targets another geometry version; a camera command
is checked against `camera_version`. Complete figures carry the same fields in
`layout.meta.mattervis_render`. The browser submits these messages through
`view_updates.js`; no callback writes a competing full figure for a camera or
overlay action.

Typed envelopes default `operation_id` to their update kind. This keeps
different kinds at the same versions distinct while repeated confirmations
of the same kind deduplicate. A caller-supplied `operation_id` in the payload
overrides that default.

Camera broadcasts keep the update kind confirmed by the state transaction;
adding wire metadata must not reclassify them as display patches. Partial
browser camera captures merge with the backend camera at the captured
revision, including dotted projection updates, so a stale Dash store cannot
restore the previous projection.

A state transaction that changes both projection and lightweight display,
overlay, or analysis settings publishes separate camera and local-update
envelopes with the same committed scene and versions. Consumers must process
both kinds; the local-update envelope does not apply camera fields. Geometry
transactions continue to deliver the camera in their complete frame. The
backend enqueues that complete frame when the state write commits, including
when the caller is an external script and Dash `update_view` does not run.
A second request for the same snapshot is deduplicated by the render worker.
The browser applies a committed complete frame whose scene, generation, and
revision are current even when that frame has no visible atom traces. An
empty scene, deletion of the last atom, hidden geometry, a publication
image, a cell, or an overlay is that frame's final intent. Whether the
payload's data is non-empty is not the version check. A
`layout.meta.mattervis_pending` figure is a loading placeholder: it does not
replace a picture already committed for the selected scene and it does not
drop a queued committed frame. A full figure with no scene identity and no
traces does not clear a canvas that already belongs to a scene. A queued
frame is dropped when a newer committed frame for the same scene arrives
before it starts. Newer is a higher scene generation, or the same generation
and a higher render or camera revision. A `Plotly.react` that has already
been issued is not cancelled; the newer committed frame applied after it
writes is the picture that remains. A frame that has not been issued is
refused when its scene, generation, or revision is no longer current. The
delivered-frame identity includes scene generation beside the server
session, scene id, render revision, camera revision, and `uirevision`.
When that frame's scene id differs from the scene already on the canvas,
the browser then relayouts the figure's own camera, axis ranges, and
aspect. The gl3d scene otherwise keeps the previous axis box until a
later size relayout, and the new geometry is drawn at that leftover
scale. The follow-up is not stored as a pointer drag. A frame for the
same scene still keeps the live camera when `uirevision` matches.
Closing a scene and undoing that close reuses the scene id and can reuse
the render revision, camera revision, and `uirevision`; the new generation
still makes that frame current. A repeat of the same generation is the
duplicate that is not rebuilt.
The browser also records each complete-frame decision on the existing
`.js-plotly-plot` node as `__mvFrameWitness` and `data-mv-frame-witness`.
`frame_witness.js` owns that record. `MatterVisFrameWitness.enable(false)`
stops recording. The record is the applied or rejected frame's identity,
camera, and coordinate counts. It does not insert a DOM node, it does not
change the frame that is painted, and it is not selection state.

The same journal also carries scene-list envelopes:

```json
{
  "type": "scene_catalog",
  "scene_catalog_seq": 4,
  "active_id": "scene_a",
  "focus": false,
  "scenes": [{"id": "scene_a", "label": "DAP-4", "structure_name": "DAP-4"}]
}
```

`GET /view-updates` repeats the current catalog as `scene_catalog`, and the
WebSocket state snapshot includes the same object. `focus` is false for
create, rename, delete, reorder, duplicate, and undo. It is true only for an
explicit active-scene request. The browser applies one catalog reconciler to
the existing scene-tab callback and ignores an older `scene_catalog_seq`.
The page that clicked Duplicate selects that catalog's `active_id` in the
browser. Other pages keep the selected tab while `focus` is false.
Flat ORTEP also queues its combined image in the background because its
camera basis is rasterized into the image.
When a scene has no saved camera, the mixed camera envelope contains only
the requested projection; the saved camera remains unset and the browser
keeps its live pose.

A tab resync reads that scene's journal from the beginning. The newest row
can be a camera envelope published after the geometry frame. A scene-scoped
`GET /view-updates` puts that geometry body back in front of the camera
envelope when the figure cache key still matches, even though
`latest_figure_broadcast` leaves the body hidden so it is never painted
alone. A silent refit has no camera envelope; the old body stays hidden and
the response is `pending` until a new figure is built. The resync applies
the figure first, then the later camera envelope. A camera envelope alone
cannot paint a canvas that is still showing another scene, so the browser
keeps reading the scene journal until a figure arrives.
That geometry body can carry an older camera revision than the pose already
acknowledged for the scene. While the canvas shows another scene, the body
still replaces the traces. The camera clock remembered on the graph from
the previous visit does not reject it. The acknowledged pose is applied
after those traces are current. The geometry frame's own camera relayout
is not stored as that pose. On the scene that is already painted, an
older camera revision still does not replace a newer pose. A camera
envelope at the same revision is not applied when `camera-state-store`
already holds a different eye, center, or up for that scene and
generation. That stored pose is the drag. The envelope is the command
published before the drag, and applying it would write the command's
eye back onto the server. A later command still applies because it
advances `camera_revision`. The book entry carries
`server_started_at`. A restarted session reuses the scene id,
generation, and revision, and that older book does not hide the new
session's command. The post-react restore uses the same rule. It
reapplies `__mvLatestCamera` only when that snapshot is still the
acknowledged pose. A drag does not write `__mvLatestCamera`, so the
command from before the drag must not replace the orbit the book just
painted. A command newer than the book still applies. The geometry
frame's camera relayout is not a drag. Capture stores a camera
relayout only for a live pointer, the one pointer-up commit, or a
wheel whose eye is not the geometry frame or the server pose restore.
A wheel that is already in progress does not adopt that programmatic
eye. A later relayout on the same canvas keeps the orbit the server
already accepted.

For local baseline measurements, `window.__mv_view_diag` reports submissions,
full frames, local patches, camera updates, and stale drops.

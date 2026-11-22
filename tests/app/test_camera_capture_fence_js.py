"""A geometry-frame camera relayout is not a drag."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_settled_canvas_relayout_is_not_stored_as_the_scene_pose():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "mat_viewer" / "app" / "camera_clientside.py"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const text = fs.readFileSync(process.argv[1], 'utf8');
const start = text.indexOf('function(relayout)');
const end = text.indexOf('Output("camera-capture-store"');
assert.ok(start > 0 && end > start);
const body = text.slice(start, end);
const close = body.lastIndexOf('\n        }');
assert.ok(close > 0);
const fn = body.slice(0, close) + '\n}';
const relayout = {'scene.camera.eye': {x: 1, y: 1, z: 1}, 'scene.camera.center': {x: 0, y: 0, z: 0}, 'scene.camera.up': {x: 0, y: 0, z: 1}};
const window = {
  dash_clientside: {no_update: 'NO'},
  __mvGestureSceneId: 'ionic',
  __mvGestureKind: 'pointer',
  __mvGestureEndedAt: Date.now(),
  __mvGestureSceneGeneration: 5,
  __mvGestureCameraRevision: 1,
  __mvCaptureCommitSeen: true,
};
const document = {querySelector: () => ({layout: {meta: {mattervis_render: {scene_id: 'ionic'}}}})};
const sandbox = {window, document, Date, Number, String, Object, Math};
vm.createContext(sandbox);
const capture = vm.runInContext('(' + fn + ')', sandbox);
window.__mvProgrammaticCameraStamp = {eye: {x: 1, y: 1, z: 1}};
assert.equal(capture(relayout), 'NO', 'a settled frame relayout was stored as the drag');
window.__mvProgrammaticCameraStamp = null;
const offscreen = {'scene.camera': {eye: {x: 0, y: 0, z: 50}, center: {x: 20, y: 20, z: 20}, up: {x: 0, y: 0, z: 1}}};
const refit = capture(offscreen);
assert.equal(refit.scene_id, 'ionic', 'an offscreen camera relayout was not stored for refit');
assert.equal(refit.relayout['scene.camera'].eye.z, 50);
assert.equal(window.__mvCaptureDiag.reason, 'pointer-follow-up');
window.__mvGestureEndedAt = 0;
window.__mvCaptureCommitSeen = false;
const drag = capture(relayout);
assert.equal(drag.scene_id, 'ionic');
assert.equal(drag.camera_revision, 1);
window.__mvGestureEndedAt = Date.now();
window.__mvCaptureCommitSeen = false;
const commit = capture(relayout);
assert.equal(commit.scene_id, 'ionic', 'the pointer-up commit was dropped');
window.__mvProgrammaticCameraStamp = {eye: {x: 1, y: 1, z: 1}};
assert.equal(capture(relayout), 'NO', 'the frame eye was stored after the pointer-up commit');
window.__mvCaptureCommitSeen = true;
window.__mvProgrammaticCameraStamp = null;
window.__mvGestureEndedAt = Date.now();
const second = capture(offscreen);
assert.equal(second.scene_id, 'ionic', 'a second pointer follow-up was dropped');
assert.equal(window.__mvCaptureDiag.reason, 'pointer-follow-up');
window.__mvGestureEndedAt = Date.now() - 1500;
assert.equal(capture(offscreen), 'NO', 'a named gesture stored a pose after the follow-up window');
assert.equal(window.__mvCaptureDiag.reason, 'follow-up-expired');
window.__mvGestureEndedAt = Date.now();
const projectionDuring = {'scene.camera.projection.type': 'orthographic'};
assert.equal(capture(projectionDuring), 'NO', 'a projection relayout was stored during the pointer follow-up');
assert.equal(window.__mvCaptureDiag.reason, 'projection-only');
window.__mvGestureSceneId = '';
window.__mvGestureKind = '';
window.__mvLastGestureSceneId = '';
window.__mvGestureEndedAt = 0;
window.__mvProgrammaticCameraStamp = null;
window.__mvProgrammaticCameraDepth = 0;
window.__mvUnadoptedWheel = null;
assert.equal(capture(offscreen), 'NO', 'a frame relayout with no gesture was stored');
assert.equal(window.__mvCaptureDiag.reason, 'no-gesture');
const projectionOnly = {'scene.camera.projection.type': 'orthographic'};
assert.equal(capture(projectionOnly), 'NO', 'a projection relayout with no gesture was stored');
assert.equal(window.__mvCaptureDiag.reason, 'projection-only');
window.__mvGestureSceneId = 'ionic';
window.__mvGestureKind = 'wheel';
window.__mvLastGestureSceneId = 'ionic';
document.querySelector = () => ({layout: {meta: {mattervis_render: {scene_id: 'water'}}}});
assert.equal(capture(offscreen), 'NO', 'a wheel stored a pose onto another canvas');
assert.equal(window.__mvCaptureDiag.reason, 'wheel-other-scene');
assert.equal(capture(projectionOnly), 'NO', 'a wheel stored a projection relayout onto another canvas');
document.querySelector = () => ({layout: {meta: {mattervis_render: {scene_id: 'ionic'}}}});
window.__mvGestureSceneId = 'ionic';
window.__mvGestureKind = 'wheel';
window.__mvGestureEndedAt = 0;
window.__mvGestureSceneId = 'ionic';
window.__mvProgrammaticCameraDepth = 1;
assert.equal(capture(relayout), 'NO', 'a geometry relayout during a wheel was stored');
window.__mvProgrammaticCameraDepth = 0;
window.__mvProgrammaticCameraStamp = {eye: {x: 1, y: 1, z: 1}};
window.__mvUnadoptedWheel = {camera: {eye: {x: 0.4, y: 1.7, z: 0.3}}};
assert.equal(capture(relayout), 'NO', 'a stamped frame camera was stored as the wheel');
const zoom = {'scene.camera.eye': {x: 0.4, y: 1.7, z: 0.3}, 'scene.camera.center': {x: 0, y: 0, z: 0}, 'scene.camera.up': {x: 0, y: 0, z: 1}};
const stored = capture(zoom);
assert.equal(stored.scene_id, 'ionic', 'the wheel pose was dropped with the frame camera');
assert.equal(stored.relayout['scene.camera.eye'].x, 0.4);
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_pointercancel_releases_the_named_gesture():
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    text = source.read_text(encoding="utf-8")
    marker = 'addEventListener("pointercancel", function() {\n    window.__mvGestureEndedAt = Date.now();'
    start = text.index(marker)
    chunk = text[start:start + 700]
    assert "releaseGestureScene" in chunk
    assert "1000" in chunk

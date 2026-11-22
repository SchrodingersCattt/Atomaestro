"""The uncommitted-camera paint stays behind a real commit and a newer gesture."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_uncommitted_camera_paint_keeps_an_inside_commit_and_drops_a_stale_one():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const timers = [];
const accepted = {eye:{x:2,y:0,z:0}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}};
const panned = {eye:{x:2,y:1.4,z:0}, center:{x:0,y:1.4,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}};
let live = JSON.parse(JSON.stringify(panned));
const relayoutHandlers = [];
const gd = {
  layout: {scene: {camera: JSON.parse(JSON.stringify(accepted)), uirevision: 'scene'},
    meta: {mattervis_render: {scene_id: 'scene-a', scene_generation: 3, camera_revision: 4, server_started_at: 'epoch'}}},
  _fullLayout: {scene: {_scene: {getCamera: () => JSON.parse(JSON.stringify(live))}}},
  on(name, fn) { if (name === 'plotly_relayout') relayoutHandlers.push(fn); },
};
function emitRelayout(data) { relayoutHandlers.forEach((fn) => fn(data)); }
const graph = element();
graph.id = 'crystal-graph';
graph.querySelector = () => gd;
graph.querySelectorAll = () => [];
function element() {
  return {addEventListener(){}, removeEventListener(){}, querySelector: () => null, querySelectorAll: () => [],
    appendChild(){}, setAttribute(){}, classList: {add(){}, remove(){}, contains: () => false}, style: {}, dataset: {},
    getBoundingClientRect: () => ({width: 0, height: 0, top: 0, left: 0}), textContent: '', hidden: false};
}
const documentObj = element();
documentObj.readyState = 'complete';
documentObj.createElement = () => element();
documentObj.getElementById = (id) => {
  if (id === 'crystal-graph') return graph;
  const el = element();
  el.id = id;
  return el;
};
documentObj.querySelector = () => null;
documentObj.querySelectorAll = () => [];
const listeners = [];
documentObj.addEventListener = (type, fn, cap) => listeners.push({type, fn, capture: cap === true});
const windowObj = {
  addEventListener(type, fn) { listeners.push({type, fn, capture: false}); },
  removeEventListener(){},
  document: documentObj,
  location: {protocol: 'http:'},
  navigator: {userAgent: 'node'},
  setTimeout(fn) { timers.push(fn); return timers.length; },
  requestAnimationFrame(fn) { timers.push(fn); return timers.length; },
  clearTimeout(){},
  Plotly: {relayout(target, patch) {
    for (const part of ['eye', 'center', 'up']) {
      const value = patch['scene.camera.' + part];
      if (value) live[part] = JSON.parse(JSON.stringify(value));
    }
    return Promise.resolve(target);
  }, react() { return Promise.resolve(); }, newPlot() { return Promise.resolve(); }},
};
windowObj.window = windowObj;
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, Set, Map, RegExp, Error,
  parseInt, parseFloat, isNaN, isFinite,
  setTimeout(fn) { timers.push(fn); return timers.length; },
  clearTimeout(){},
  requestAnimationFrame(fn) { timers.push(fn); return timers.length; },
  window: windowObj, document: documentObj,
  MutationObserver: function() { this.observe = () => {}; this.disconnect = () => {}; },
  getComputedStyle: () => ({getPropertyValue: () => ''}),
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
timers.length = 0;
const root = sandbox.window;
root.MatterVisInvalidateSemanticHover = () => {};
root.__mvServerCameraByScene = {'scene-a': {
  camera: JSON.parse(JSON.stringify(accepted)), camera_revision: 4, scene_generation: 3, server_started_at: 'epoch',
}};
function press() {
  const target = {closest: (sel) => sel === '#crystal-graph' ? graph : null};
  listeners.filter((item) => item.type === 'pointerdown' && item.capture).forEach((item) => item.fn({target, button: 2}));
}
function flushAll() {
  for (let i = 0; i < 30 && timers.length; i += 1) timers.shift()();
}
function sampleKey() {
  return [
    String(root.__mvGestureSerial || 0),
    String(root.__mvGestureSceneId || ''),
    root.__mvGestureSceneGeneration == null ? '' : String(root.__mvGestureSceneGeneration),
    root.__mvGestureServerEpoch != null ? String(root.__mvGestureServerEpoch) : '',
  ].join('|');
}
function paints() { return Number(root.__mvUncommittedPaintCount || 0); }
(async () => {
  press();
  const generation = root.__mvGestureGeneration();
  root.__mvGestureEndedAt = Date.now();
  root.__mvPaintUncommittedCamera(generation, 'scene-a');
  // Canvas mouseup is dispatched before a timer queued from pointerup.
  emitRelayout({'scene.camera': JSON.parse(JSON.stringify(panned))});
  flushAll();
  assert.equal(paints(), 0, 'inside mouseup commit was painted over');
  assert.equal(live.center.y, 1.4, 'inside commit lost the panned center');

  root.__mvGestureUserRelayoutAt = 0;
  root.__mvGestureEndedAt = Date.now();
  root.__mvPaintUncommittedCamera(generation, 'scene-a');
  press();
  flushAll();
  assert.equal(paints(), 0, 'a newer gesture still received the previous paint');
  assert.equal(live.center.y, 1.4, 'a stale paint moved the newer gesture');

  const current = root.__mvGestureGeneration();
  root.__mvGestureEndedAt = Date.now();
  root.__mvGestureUserRelayoutAt = 0;
  root.__mvPaintUncommittedCamera(current, 'scene-a');
  gd.layout.meta.mattervis_render.scene_id = 'scene-b';
  flushAll();
  assert.equal(paints(), 0, 'a paint for scene-a wrote scene-b');
  assert.equal(live.center.y, 1.4, 'a cross-scene paint moved the camera');
  gd.layout.meta.mattervis_render.scene_id = 'scene-a';

  // pointercancel schedules the same paint. With no mouseup relayout the
  // accepted camera replaces the pan, and an overlapping frame must not
  // put the sampled eye back afterwards.
  press();
  const cancelGeneration = root.__mvGestureGeneration();
  root.__mvGestureEndedAt = Date.now();
  root.__mvGestureUserRelayoutAt = 0;
  live.center = {x: 0, y: 1.4, z: 0};
  live.eye = {x: 2, y: 1.4, z: 0};
  root.__mvInProgressDragCamera = {camera: JSON.parse(JSON.stringify(panned)), key: sampleKey()};
  gd.__mvFrameReactStartedAt = root.__mvGestureEndedAt - 30;
  gd.__mvGestureExternalRelayouts = Number(gd.__mvExternalRelayouts || 0);
  gd.__mvInsideFrameReact = 1;
  root.__mvPaintUncommittedCamera(cancelGeneration, 'scene-a');
  flushAll();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(paints(), 1, 'pointercancel did not paint the accepted camera');
  assert.equal(live.center.y, 0, 'pointercancel left the uncommitted pan');
  gd.__mvInsideFrameReact = 0;
  await root.__mvRestoreInProgressDrag(gd);
  assert.equal(live.center.y, 0, 'overlapping frame restored the sampled pan over the accepted camera');

  root.__mvGestureEndedAt = 0;
  live.center = {x: 0, y: 0, z: 0};
  live.eye = {x: 2, y: 0, z: 0};
  root.__mvInProgressDragCamera = {camera: JSON.parse(JSON.stringify(panned)), key: sampleKey()};
  root.__mvDragRestoreEye = null;
  await root.__mvRestoreInProgressDrag(gd);
  assert.equal(root.__mvDragRestoreEye && root.__mvDragRestoreEye.y, 1.4, 'drag restore did not remember its eye');
  assert.equal(live.center.y, 1.4, 'drag restore did not put the sample back while the pointer was down');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""A tab return paints the selected scene's geometry before its camera.

The graph remembers the camera clock from the previous visit. The journal
body for that scene can still carry the earlier camera revision. That body
has to replace the other scene's traces. An older camera replaces a newer
pose only when those traces are already the selected scene.
"""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_cross_scene_geometry_ignores_remembered_camera_clock():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const end = source.indexOf('  function stampGestureScene(');
if (end < 0) throw new Error('gesture stamp not found');
const calls = [];
const naclMeta = {
  scene_id: 'scene-nacl',
  scene_generation: 5,
  render_revision: 1,
  camera_revision: 0,
  server_started_at: 'epoch',
};
const waterMeta = {
  scene_id: 'scene-water',
  scene_generation: 4,
  render_revision: 0,
  camera_revision: 0,
  server_started_at: 'epoch',
};
const gd = {
  data: [{type: 'scatter3d', x: [0, 1], y: [0, 1], z: [0, 1]}],
  layout: {
    scene: {camera: {eye: {x: 1, y: 1, z: 1}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}}},
    meta: {mattervis_render: Object.assign({}, waterMeta)},
  },
  on() { return gd; },
  emit(name, data) {
    const keys = data && typeof data === 'object' ? Object.keys(data) : [];
    const camera = keys.some((key) => key === 'scene.camera' || key.indexOf('scene.camera.') === 0 || (key === 'scene' && data.scene && data.scene.camera));
    if (name === 'plotly_relayout' && camera) gd.__mvEscapedCameraRelayouts = (gd.__mvEscapedCameraRelayouts || 0) + 1;
    return gd;
  },
  __mvCameraRevisionScope: 'scene-nacl:5',
  __mvCameraRevisionScene: 'scene-nacl',
  __mvCameraRevisionEpoch: 'epoch',
  __mvCameraRevision: 1,
};
const crystal = {
  querySelector() { return gd; },
  querySelectorAll() { return []; },
};
const document = {
  readyState: 'complete',
  body: null,
  documentElement: null,
  getElementById(id) {
    if (id === 'crystal-graph') return crystal;
    if (id === 'fast-view-metadata') {
      return {textContent: JSON.stringify({
        scene_id: 'scene-nacl',
        scene_generation: 5,
        render_revision: 1,
        camera_revision: 1,
        server_started_at: 'epoch',
      })};
    }
    if (id === 'mv-render-status') {
      return {hidden: true, textContent: '', setAttribute() {}, getAttribute() { return ''; }};
    }
    return null;
  },
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
function react(target, data, layout) {
  calls.push({
    scene: layout && layout.meta && layout.meta.mattervis_render && layout.meta.mattervis_render.scene_id,
    traces: Array.isArray(data) ? data.length : 0,
  });
  if (target.emit) target.emit('plotly_relayout', {'scene.camera': (layout.scene && layout.scene.camera) || {}});
  return Promise.resolve(target);
}
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, Set, WeakSet, Map,
  setTimeout(fn) { if (typeof fn === 'function') fn(); return 0; },
  clearTimeout() {},
  requestAnimationFrame(fn) { if (typeof fn === 'function') fn(); return 1; },
  cancelAnimationFrame() {},
  window: {Plotly: {react, newPlot() {}, update() {}, restyle() {}, relayout() { return Promise.resolve(gd); }}},
  document,
};
sandbox.window.document = document;
vm.createContext(sandbox);
vm.runInContext(source.slice(0, end) + '\n})();', sandbox);
const plot = sandbox.window.Plotly;
const naclFigure = {
  data: [{type: 'scatter3d', x: [0, 1, 2], y: [0, 0, 0], z: [1, 1, 1], mode: 'markers'}],
  layout: {
    title: {text: 'ionic_nacl'},
    scene: {camera: {eye: {x: 1.25, y: 1.25, z: 1.25}}},
    meta: {mattervis_render: Object.assign({}, naclMeta)},
  },
};
(async () => {
  await plot.react(gd, naclFigure.data, naclFigure.layout);
  assert.equal(calls.length, 1, 'selected-scene geometry was rejected by the previous visit camera clock');
  assert.equal(calls[0].scene, 'scene-nacl');
  assert.equal(calls[0].traces, 1);
  assert.equal(gd.__mvEscapedCameraRelayouts || 0, 0, 'the geometry frame camera was stored as the scene pose');

  calls.length = 0;
  gd.layout.meta.mattervis_render = Object.assign({}, naclMeta, {camera_revision: 1});
  await plot.react(gd, naclFigure.data, naclFigure.layout);
  assert.equal(calls.length, 0, 'an older camera replaced the pose already painted for this scene');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [node, "-e", program, str(source)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

"""The selection dock must not leave observers behind a replaced panel."""

import shutil
import subprocess
from pathlib import Path

import pytest


DOCK_JS = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "selection_appearance.js"


def test_dock_watch_replaces_the_previous_observer_and_listener():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = [];
function element(id) {
  const el = {
    id: id,
    style: {},
    hidden: false,
    parentElement: null,
    childNodes: [],
    appendChild(child) { child.parentElement = el; el.childNodes.push(child); return child; },
    append() {},
    getBoundingClientRect() { return {top: 10, left: 20, right: 400, height: 300, width: 380}; },
    setAttribute() {},
    replaceChildren() { el.childNodes = []; },
    querySelector() { return null; },
  };
  return el;
}
const body = element('body');
const centers = [element('center-1'), element('center-2')];
let current = centers[0];
const observers = [];
function ResizeObserver(callback) {
  this.callback = callback;
  this.target = null;
  this.disconnected = false;
  this.observe = function (node) { this.target = node; };
  this.disconnect = function () { this.disconnected = true; };
  observers.push(this);
}
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise, Math, Error,
  setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {},
  requestAnimationFrame(fn) { if (typeof fn === 'function') fn(); },
  ResizeObserver,
  fetch() { return Promise.resolve({ok: true, json() { return Promise.resolve({items: [], supported_fields: []}); }}); },
  document: {
    body: body,
    documentElement: {},
    createElement(tag) { return element(tag); },
    createTextNode() { return {nodeType: 3}; },
    getElementById(id) {
      if (id === 'center-panel') return current;
      if (id === 'mv-status-bar' || id === 'perf-log-panel') return element(id);
      return null;
    },
    addEventListener() {},
  },
};
sandbox.window = sandbox;
sandbox.getComputedStyle = function () { return {getPropertyValue() { return '290'; }}; };
sandbox.addEventListener = function (type, fn) { listeners.push({type, fn}); };
sandbox.removeEventListener = function (type, fn) {
  const index = listeners.findIndex((item) => item.type === type && item.fn === fn);
  if (index >= 0) listeners.splice(index, 1);
};
vm.createContext(sandbox);
vm.runInContext(source + '\nthis.__dockTestReady = true;', sandbox);
const api = sandbox.MatterVisSelectionDock;
assert.equal(typeof api.teardown, 'function');
const handler = listeners.find((item) => item.type === 'mattervis-selection');
assert.ok(handler, 'selection listener installed');
function resizeCount() { return listeners.filter((item) => item.type === 'resize').length; }
async function flush() {
  for (let step = 0; step < 12; step += 1) await Promise.resolve();
}
(async function () {
  handler.fn({detail: {sceneId: 'scene-a', targets: [{kind: 'atom', id: 'a1', label: 'A'}]}});
  await flush();
  assert.equal(observers.length, 1);
  assert.equal(observers[0].disconnected, false);
  assert.equal(observers[0].target, centers[0]);
  assert.equal(resizeCount(), 1);
  current = centers[1];
  handler.fn({detail: {sceneId: 'scene-a', targets: [{kind: 'atom', id: 'a1', label: 'A'}]}});
  await flush();
  assert.equal(observers.length, 2, 'a replaced center gets a new observer');
  assert.equal(observers[0].disconnected, true);
  assert.equal(observers[1].disconnected, false);
  assert.equal(observers[1].target, centers[1]);
  assert.equal(resizeCount(), 1, 'the previous resize listener is removed');
  api.teardown();
  assert.equal(observers[1].disconnected, true);
  assert.equal(resizeCount(), 0);
  assert.equal(sandbox.__mvDockWatch, null);
  assert.equal(centers[1].__mvDockWatch, false);
})().catch(function (error) {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        [node, "-e", program, str(DOCK_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_gutter_reserve_publishes_only_after_the_relayout_settles():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    root = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const center = {
  id: 'center-panel',
  style: {},
  clientWidth: 800,
  clientHeight: 600,
};
const plot = {
  id: 'crystal-graph',
  classList: {contains(name) { return name === 'js-plotly-plot'; }},
  style: {},
  _fullLayout: {},
  querySelector() { return null; },
};
let frame = null;
let releaseRelayout = null;
let relayouts = 0;
let geometry = 'before';
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise, Math, Error,
  setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {},
  devicePixelRatio: 1,
  innerWidth: 1280,
  innerHeight: 800,
  addEventListener() {},
  removeEventListener() {},
  requestAnimationFrame(fn) { frame = fn; return 1; },
  ResizeObserver: function () { this.observe = function () {}; this.disconnect = function () {}; },
  MutationObserver: function () { this.observe = function () {}; this.disconnect = function () {}; },
  getComputedStyle(el) {
    const padding = el && el.style && el.style.paddingRight ? el.style.paddingRight : '0';
    return {
      getPropertyValue() { return '290'; },
      paddingLeft: '0',
      paddingRight: padding,
      paddingTop: '0',
      paddingBottom: '0',
    };
  },
  document: {
    readyState: 'loading',
    body: {appendChild() {}},
    documentElement: {},
    addEventListener() {},
    createElement() { return {style: {}, appendChild() {}, setAttribute() {}}; },
    createTextNode() { return {}; },
    getElementById(id) {
      if (id === 'center-panel') return center;
      if (id === 'crystal-graph') return plot;
      return null;
    },
  },
  Plotly: {
    relayout(node, layout) {
      relayouts += 1;
      return new Promise(function (resolve) {
        releaseRelayout = function () {
          geometry = layout.width + 'x' + layout.height;
          resolve();
        };
      });
    },
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const dock = sandbox.MatterVisSelectionDock;
assert.equal(typeof dock.afterGutter, 'function');
let seen = null;
let second = null;
let relayoutsBeforeSecond = 0;
dock.afterGutter(true, function () { seen = geometry; });
Promise.resolve().then(function () {
  assert.equal(seen, null, 'the pick must wait for the gutter relayout');
  assert.equal(relayouts, 1);
  assert.equal(center.style.paddingRight, '290px');
  releaseRelayout();
  return Promise.resolve();
}).then(function () {
  assert.equal(seen, null, 'the follow-up frame still has to settle');
  assert.equal(typeof frame, 'function');
  const pending = frame;
  frame = null;
  pending();
  return Promise.resolve();
}).then(function () {
  assert.equal(seen, '510x600');
  assert.equal(geometry, '510x600');
  relayoutsBeforeSecond = relayouts;
  dock.afterGutter(true, function () { second = geometry; });
  assert.equal(second, null);
  assert.equal(relayouts, relayoutsBeforeSecond, 'a reserved gutter must not relayout again');
  assert.equal(typeof frame, 'function');
  const follow = frame;
  frame = null;
  follow();
  return Promise.resolve();
}).then(function () {
  assert.equal(second, '510x600');
  assert.equal(relayouts, relayoutsBeforeSecond);
  process.exit(0);
}).catch(function (error) {
  console.error(error);
  process.exit(1);
});
setTimeout(function () {
  console.error('gutter reserve did not settle');
  process.exit(1);
}, 1000);
"""
    result = subprocess.run(
        ["node", "-e", program, str(root / "panel_resize.js"), str(root / "selection_appearance.js")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
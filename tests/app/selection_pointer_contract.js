/* Selection outline timing and the double-click gesture.
 * The first paint must not wait on a gl3d relayout when the dock column
 * width is unchanged. An open/close still refits, then re-projects the
 * same targets. Pointer events keep detail at 0. The platform click count
 * is the mousedown between pointerdown and pointerup. A second press with
 * that count must not clear on a miss. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const frames = [];
const stores = [];
const paints = [];
let invalidations = 0;
let relayouts = 0;
let releaseRelayout = null;
let released = 0;
let hit = null;
let primitives = [{}];
let inPointer = false;
const promotions = [];

function listen(target) {
  const map = new Map();
  target.addEventListener = function (type, fn) {
    if (!map.has(type)) map.set(type, []);
    map.get(type).push(fn);
  };
  target.removeEventListener = function () {};
  target.dispatchEvent = function (event) {
    (map.get(event.type) || []).slice().forEach(function (fn) { fn(event); });
    return true;
  };
}

class El {
  constructor(tag) {
    this.tagName = tag || 'div';
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.id = '';
    this.className = '';
    this.hidden = false;
    this.clientWidth = 0;
    this.clientHeight = 0;
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
      contains: (name) => this.className === name,
    };
    listen(this);
  }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      if (node !== this && matches(node, sel)) out.push(node);
      (node.children || []).forEach(walk);
    };
    walk(this);
    return out;
  }
  getBoundingClientRect() {
    return {width: this.clientWidth || 1400, height: this.clientHeight || 800, left: 0, top: 0, right: this.clientWidth || 1400, bottom: this.clientHeight || 800};
  }
  contains(node) { return this === node || (this.children || []).some((child) => child.contains && child.contains(node)); }
  setAttribute() {}
  closest() { return null; }
  prepend(node) { this.appendChild(node); }
}

function matches(node, sel) {
  if (!node || !sel || sel.indexOf(' ') >= 0 || sel.indexOf('[') >= 0) return false;
  if (sel === '.js-plotly-plot') return node.className === 'js-plotly-plot';
  if (sel.startsWith('#')) return node.id === sel.slice(1);
  return String(node.tagName || '').toLowerCase() === sel.toLowerCase();
}

const document = new El('document');
document.readyState = 'complete';
document.body = new El('body');
document.documentElement = new El('html');
document.body.appendChild(document.documentElement);
const viewer = new El('div');
viewer.id = 'viewer-root';
viewer.clientWidth = 1400;
viewer.clientHeight = 900;
const center = new El('div');
center.id = 'center-panel';
center.clientWidth = 800;
center.clientHeight = 600;
const graph = new El('div');
graph.id = 'crystal-graph';
const plot = new El('div');
plot.className = 'js-plotly-plot';
plot.layout = {meta: {}};
plot.data = [];
plot._fullLayout = {width: 0, height: 0};
plot.on = function () {};
graph.appendChild(plot);
center.appendChild(graph);
viewer.appendChild(center);
document.body.appendChild(viewer);
document.getElementById = (id) => {
  let found = null;
  const walk = (node) => {
    if (found || !node) return;
    if (node.id === id) { found = node; return; }
    (node.children || []).forEach(walk);
  };
  walk(document.body);
  walk(document.documentElement);
  return found;
};
document.querySelector = (sel) => document.body.querySelector(sel);
document.querySelectorAll = (sel) => document.body.querySelectorAll(sel);
document.createElement = (tag) => new El(tag);
document.createTextNode = () => ({nodeType: 3});
listen(document);

const window = {
  document,
  MATTERVIS_WS_FIGURE: false,
  devicePixelRatio: 1,
  innerWidth: 1280,
  innerHeight: 800,
  location: {protocol: 'http:', host: 'localhost'},
  console,
  MatterVisScenePicking: {
    hits() { return hit ? [hit] : []; },
    index() { return {primitives: primitives.slice()}; },
    paint(gd, selected) {
      paints.push({
        ids: Array.from(selected || []).map((item) => item.kind + ':' + item.id),
        relayouts,
        released,
        sync: inPointer,
      });
    },
    invalidate() { invalidations += 1; },
  },
  dash_clientside: {
    set_props(id, props) { stores.push({id, data: props.data}); },
  },
  Plotly: {
    relayout(node, layout) {
      relayouts += 1;
      return new Promise(function (resolve) {
        releaseRelayout = function () {
          released += 1;
          if (node && node._fullLayout && layout && layout.width) {
            node._fullLayout.width = layout.width;
            node._fullLayout.height = layout.height;
          }
          resolve();
        };
      });
    },
    restyle() { return Promise.resolve(); },
    react() { return Promise.resolve(); },
    newPlot() { return Promise.resolve(); },
    update() { return Promise.resolve(); },
  },
  requestAnimationFrame(fn) { frames.push(fn); return frames.length; },
  cancelAnimationFrame() {},
  setTimeout() { return 0; },
  clearTimeout() {},
  setInterval() { return 0; },
  clearInterval() {},
  fetch() { return new Promise(function () {}); },
  getComputedStyle(el) {
    const padding = el && el.style && el.style.paddingRight ? el.style.paddingRight : '0px';
    return {
      getPropertyValue(name) { return name === '--mv-selection-dock' ? '290' : ''; },
      paddingLeft: '0px',
      paddingRight: padding,
      paddingTop: '0px',
      paddingBottom: '0px',
    };
  },
  ResizeObserver: function () { this.observe = function () {}; this.disconnect = function () {}; },
  MutationObserver: function () { this.observe = function () {}; this.disconnect = function () {}; },
  CustomEvent: class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = init && init.detail; }
  },
  Event: class Event { constructor(type) { this.type = type; } },
  Map, Set, WeakMap, WeakSet, Promise, Date, Math, JSON, Number, String, Object, Array, Error,
  parseFloat, parseInt, isFinite, isNaN,
};
listen(window);
window.window = window;
const sandbox = {
  console, window, document,
  requestAnimationFrame: window.requestAnimationFrame,
  cancelAnimationFrame: window.cancelAnimationFrame,
  setTimeout: window.setTimeout,
  clearTimeout: window.clearTimeout,
  setInterval: window.setInterval,
  clearInterval: window.clearInterval,
  fetch: window.fetch,
  getComputedStyle: window.getComputedStyle,
  ResizeObserver: window.ResizeObserver,
  MutationObserver: window.MutationObserver,
  CustomEvent: window.CustomEvent,
  Event: window.Event,
  Map, Set, WeakMap, WeakSet, Promise, Date, Math, JSON, Number, String, Object, Array, Error,
  parseFloat, parseInt, isFinite, isNaN,
};
sandbox.window = window;
window.document = document;
vm.createContext(sandbox);
for (const name of ['panel', 'dock', 'mattervis']) {
  const source = fs.readFileSync(process.argv[2 + ['panel', 'dock', 'mattervis'].indexOf(name)], 'utf8');
  try { vm.runInContext(source, sandbox); }
  catch (error) { console.error('load ' + name + ' failed'); console.error(error && error.stack || error); process.exit(1); }
}

function runFrames() {
  const pending = frames.splice(0);
  pending.forEach(function (fn) { fn(); });
}
async function flush(steps) {
  for (let i = 0; i < (steps || 8); i += 1) await Promise.resolve();
}
async function settle() {
  if (typeof releaseRelayout === 'function') {
    const release = releaseRelayout;
    releaseRelayout = null;
    release();
  }
  await flush(16);
  runFrames();
  await flush(8);
  // A miss during the relayout schedules one more sample after it settles.
  runFrames();
  await flush(4);
}
function selected() { return sandbox.window.MatterVisSemantic.selected(); }
function selectionWrites() { return stores.filter((item) => item.id === 'scene-selection-store'); }
function dispatch(type, detail, buttons, x, y, extra) {
  graph.dispatchEvent(Object.assign({
    type: type,
    detail: detail,
    button: 0,
    buttons: buttons,
    clientX: x,
    clientY: y,
    target: graph,
    altKey: false,
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefault() {},
    stopPropagation() {},
  }, extra || {}));
}
// Hovering-mouse order from the Pointer Events compatibility mapping:
// pointerdown, mousedown, pointerup, mouseup, click. Pointer detail stays 0.
function press(clickCount, x, y, extra) {
  inPointer = true;
  dispatch('pointerdown', 0, 1, x, y, extra);
  dispatch('mousedown', clickCount, 1, x, y, extra);
  dispatch('pointerup', 0, 0, x, y, extra);
  inPointer = false;
  dispatch('mouseup', clickCount, 0, x, y, extra);
  dispatch('click', clickCount, 0, x, y, extra);
}
function pressPointerOnly(x, y) {
  inPointer = true;
  dispatch('pointerdown', 0, 1, x, y);
  dispatch('pointerup', 0, 0, x, y);
  inPointer = false;
}
function pressStuffedPointerDetail(x, y) {
  inPointer = true;
  dispatch('pointerdown', 2, 1, x, y);
  dispatch('mousedown', 1, 1, x, y);
  dispatch('pointerup', 2, 0, x, y);
  inPointer = false;
}
function list(value) { return Array.from(value || [], (item) => item); }
function ids() { return list(selected()).map((item) => item.id); }

(async function () {
  const installedPromote = sandbox.window.MatterVisDoubleClick && sandbox.window.MatterVisDoubleClick.promote;
  runFrames();
  const loadedRelayouts = relayouts;
  center.style.paddingRight = '290px';
  hit = {kind: 'atom', id: 'a1', label: 'A'};
  const framesBefore = frames.length;
  press(1, 40, 40);
  assert.equal(relayouts, loadedRelayouts, 'a reserved column must not relayout');
  assert.equal(frames.length, framesBefore, 'a reserved column must not schedule a frame');
  assert.equal(paints.length, 1);
  assert.equal(paints[0].sync, true, 'the outline paints during the pointerup');
  assert.deepEqual(paints[0].ids, ['atom:a1']);
  assert.deepEqual(ids(), ['a1']);
  assert.equal(selectionWrites().at(-1).data.targets[0].id, 'a1');

  center.style.paddingRight = '';
  hit = {kind: 'atom', id: 'open', label: 'Open'};
  const beforeOpen = relayouts;
  const paintsBeforeOpen = paints.length;
  press(1, 80, 80);
  assert.equal(paints.length, paintsBeforeOpen + 1);
  assert.equal(paints.at(-1).sync, true);
  assert.equal(paints.at(-1).relayouts, beforeOpen, 'the first paint happens before the relayout is requested');
  assert.deepEqual(paints.at(-1).ids, ['atom:open']);
  assert.equal(relayouts, beforeOpen + 1);
  assert.equal(released, 0, 'the outline does not wait for the relayout to finish');
  assert.deepEqual(ids(), ['open']);
  hit = null;
  const writesAfterOpen = selectionWrites().length;
  const invalidBefore = invalidations;
  press(2, 81, 81);
  assert.deepEqual(ids(), ['open'], 'a double-click miss during the open relayout must not clear');
  assert.equal(selectionWrites().length, writesAfterOpen);
  await settle();
  assert.deepEqual(ids(), ['open'], 'the settled resample must not clear a double-click miss');
  assert.notEqual(selectionWrites().at(-1).data.mode, 'clear');
  assert.ok(invalidations > invalidBefore, 'the open relayout re-projects the same selection');
  assert.deepEqual(paints.at(-1).ids, ['atom:open']);
  assert.equal(center.style.paddingRight, '290px');

  const stableRelayouts = relayouts;
  const stableFrames = frames.length;
  hit = {kind: 'atom', id: 'b2'};
  press(1, 120, 50);
  assert.equal(relayouts, stableRelayouts);
  assert.equal(frames.length, stableFrames);
  assert.equal(paints.at(-1).sync, true);
  assert.deepEqual(ids(), ['b2']);

  hit = {kind: 'atom', id: 'a2'};
  press(2, 122, 52, {shiftKey: true});
  assert.deepEqual(ids(), ['b2', 'a2'], 'shift remains an add, even inside a double-click count');

  sandbox.window.MatterVisDoubleClick = {
    promote(payload) { promotions.push(payload); },
  };
  hit = null;
  const beforeGesture = ids().slice();
  press(2, 122, 52);
  assert.deepEqual(ids(), beforeGesture);
  assert.equal(promotions.length, 1);
  assert.equal(promotions[0].target, null);
  assert.deepEqual(list(promotions[0].previous).map((item) => item.id), beforeGesture);

  sandbox.window.MatterVisDoubleClick = {
    promote(payload) {
      promotions.push(payload);
      sandbox.window.MatterVisSemantic.select({kind: 'molecule', id: 'mol-1'}, 'replace');
    },
  };
  hit = {kind: 'atom', id: 'b2'};
  press(2, 122, 52);
  assert.deepEqual(ids(), ['mol-1'], 'the hook publishes; the pointer path does not replace it');
  assert.deepEqual(list(promotions.at(-1).previous).map((item) => item.id), ['b2', 'a2']);

  sandbox.window.MatterVisDoubleClick = {
    promote() { throw new Error('promotion failed'); },
  };
  hit = {kind: 'atom', id: 'kept'};
  press(1, 30, 30);
  assert.deepEqual(ids(), ['kept']);
  hit = null;
  press(2, 30, 30);
  assert.deepEqual(ids(), ['kept']);

  hit = {kind: 'atom', id: 'c3'};
  press(2, 200, 200, {altKey: true});
  assert.deepEqual(ids(), ['c3'], 'alt-click stays a replace, including detail 2');

  primitives = [];
  hit = null;
  press(1, 15, 15);
  assert.equal(frames.length, 1, 'an unready index retries instead of clearing');
  press(2, 15, 15);
  assert.equal(frames.length, 1, 'the second press must not cancel the first attempt');
  primitives = [{}];
  hit = {kind: 'atom', id: 'late'};
  runFrames();
  assert.deepEqual(ids(), ['late']);

  center.clientWidth = 960;
  center.style.paddingRight = '';
  hit = {kind: 'atom', id: 'move'};
  const relayoutsBeforeMove = relayouts;
  press(1, 10, 10);
  assert.equal(relayouts, relayoutsBeforeMove + 1, 'opening onto a new content box starts a relayout');
  assert.deepEqual(ids(), ['move']);
  hit = null;
  press(1, 400, 400);
  assert.deepEqual(ids(), ['move'], 'a miss during the open relayout is not an empty-space clear');
  assert.equal(selectionWrites().at(-1).data.targets[0].id, 'move');
  hit = {kind: 'atom', id: 'move'};
  await settle();
  assert.deepEqual(ids(), ['move'], 'the settled sample keeps the atom that is under the pointer');
  assert.equal(center.style.paddingRight, '290px');

  document.dispatchEvent({
    type: 'keydown',
    key: 'Escape',
    target: {tagName: 'DIV', isContentEditable: false},
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    preventDefault() {},
  });
  assert.deepEqual(ids(), ['move'], 'Escape waits to clear until the column closes');
  await settle();
  assert.deepEqual(ids(), []);
  assert.equal(center.style.paddingRight, '');

  hit = {kind: 'atom', id: 'again'};
  press(1, 20, 20);
  await settle();
  assert.deepEqual(ids(), ['again']);
  center.style.paddingRight = '290px';
  hit = null;
  primitives = [{}];
  const clearWrites = selectionWrites().length;
  press(1, 15, 15);
  assert.deepEqual(ids(), ['again'], 'an empty-space clear waits for the column to close');
  assert.equal(selectionWrites().length, clearWrites);
  await settle();
  assert.deepEqual(ids(), []);
  assert.equal(selectionWrites().at(-1).data.mode, 'clear');
  assert.equal(center.style.paddingRight, '');

  hit = {kind: 'atom', id: 'kept-click'};
  press(1, 70, 70);
  await settle();
  hit = null;
  pressPointerOnly(71, 71);
  assert.deepEqual(ids(), ['kept-click'], 'a second pointer press with detail 0 still counts inside the fallback window');
  hit = {kind: 'atom', id: 'far'};
  pressStuffedPointerDetail(420, 420);
  assert.deepEqual(ids(), ['far'], 'pointer detail is not the click count when mousedown says this is a single click');

  assert.equal(typeof installedPromote, 'function');
  sandbox.window.MatterVisScenePicking.promotionTarget = function (target) {
    const parents = (target && target.metadata && target.metadata.parents) || [];
    const part = parents.find((item) => item && item.kind === 'part');
    if (part) return part;
    return parents.find((item) => item && item.kind === 'molecule') || null;
  };
  sandbox.window.MatterVisDoubleClick = {promote: installedPromote};
  center.style.paddingRight = '290px';
  hit = {kind: 'atom', id: 'seed', metadata: {parents: [
    {kind: 'molecule', id: 'mol-water'},
    {kind: 'part', id: 'mol-water|part:1'},
  ]}};
  press(1, 40, 220);
  await settle();
  assert.deepEqual(ids(), ['seed']);
  hit = null;
  pressPointerOnly(41, 221);
  assert.deepEqual(ids(), ['mol-water|part:1'], 'a pointer detail of 0 promotes the first press through the hook');

  hit = {kind: 'bond', id: 'plain-bond', metadata: {parents: [{kind: 'molecule', id: 'mol-only'}]}};
  press(1, 200, 220);
  await settle();
  hit = null;
  pressPointerOnly(201, 221);
  assert.deepEqual(ids(), ['mol-only']);

  hit = {kind: 'atom', id: 'orphan'};
  press(1, 400, 220);
  await settle();
  hit = null;
  pressPointerOnly(401, 221);
  assert.deepEqual(ids(), ['orphan'], 'no molecule membership stays the single object');

  process.exit(0);
})().catch(function (error) {
  console.error(error && error.stack || error);
  process.exit(1);
});

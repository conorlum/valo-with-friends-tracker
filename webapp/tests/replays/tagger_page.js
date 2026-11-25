/*
 * Opens the control tagger's map-features panel (scripts/control_tagger_features.js) in node, the way the
 * page does: TaggerCore first, then the page's `window.tagger` hooks, then the panel script, all in one
 * context with a stub DOM. Tests drive it through the panel's own buttons and entry points
 * (tests/replays/test_map_feature_tagger.py), so UI wiring is exercised, not only the pure model.
 *
 *   const page = openPage(DATA, {storage, map});   // DATA: the page's embedded data (control_tagger.build)
 *   page.click("featImportReplace"); page.downloads; page.api (window.taggerFeatures); page.reopen()
 *
 * Not a browser: canvas drawing is absorbed, layout is fixed, and nothing is rendered.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPTS = path.join(__dirname, "..", "..", "scripts");
const CORE = fs.readFileSync(path.join(SCRIPTS, "control_tagger_core.js"), "utf8");
const PANEL = fs.readFileSync(path.join(SCRIPTS, "control_tagger_features.js"), "utf8");
const AREAS = fs.readFileSync(path.join(SCRIPTS, "control_tagger_areas.js"), "utf8");

// A localStorage stand-in. Reads/writes can throw as a full or denied store does.
function memoryStorage(opts) {
  const m = new Map();
  return {
    m: m,
    opts: opts || {},
    getItem(k) {
      if (this.opts.failReads) throw new Error("storage read denied");
      return m.has(k) ? m.get(k) : null;
    },
    setItem(k, v) {
      if (this.opts.failWrites) { const e = new Error("quota"); e.name = "QuotaExceededError"; throw e; }
      m.set(k, String(v));
    },
    removeItem(k) { m.delete(k); },
  };
}

function context2d() {
  let matrix = [1, 0, 0, 1, 0, 0];
  const stack = [];
  const store = {
    textCalls: [],
    save() { stack.push(matrix.slice()); },
    restore() { matrix = stack.pop() || matrix; },
    setTransform(...m) { matrix = m; },
    translate(x, y) { matrix[4] += matrix[0]*x + matrix[2]*y; matrix[5] += matrix[1]*x + matrix[3]*y; },
    rotate(a) {
      const [b,c,d,e,x,y] = matrix, co = Math.cos(a), si = Math.sin(a);
      matrix = [b*co+d*si, c*co+e*si, d*co-b*si, e*co-c*si, x, y];
    },
    fillText(text, x, y) { this.textCalls.push({text, x, y, matrix:matrix.slice()}); },
  };
  const image = (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(Math.max(0, w * h * 4)) });
  return new Proxy(store, {
    get(t, p) {
      if (p in t) return t[p];
      if (p === "createImageData" || p === "getImageData") return (a, b, c, d) => image(c === undefined ? a : c, d === undefined ? b : d);
      if (p === "measureText") return () => ({ width: 0 });
      return () => ({ addColorStop() {} });
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

function element(doc, id, tag) {
  const el = {
    id: id, tagName: (tag || "div").toUpperCase(), children: [], style: {}, dataset: {}, listeners: {}, attrs: {},
    hidden: false, disabled: false, checked: false, value: "", textContent: "", innerHTML: "", className: "", title: "",
    files: [], width: 1024, height: 1024, clientWidth: 800, clientHeight: 800, open: false,
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
                 toggle(c, on) { if (on === undefined ? !this.set.has(c) : on) this.set.add(c); else this.set.delete(c); },
                 contains(c) { return this.set.has(c); } },
    appendChild(c) {
      this.children.push(c);
      if (this.tagName === "SELECT" && c.tagName === "OPTION" && (c.selected || this.children.length === 1)) this.value = c.value;
      return c;
    },
    append(...children) { children.forEach(c => this.appendChild(c)); },
    removeChild(c) { this.children = this.children.filter(x => x !== c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    replaceChildren() { this.children = Array.from(arguments); },
    addEventListener(k, cb) { (this.listeners[k] = this.listeners[k] || []).push(cb); },
    removeEventListener() {},
    dispatch(k, extra) {
      const ev = Object.assign({ type: k, target: this, currentTarget: this, preventDefault() {}, stopPropagation() {} }, extra || {});
      (this.listeners[k] || []).forEach(cb => cb.call(this, ev));
    },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    hasAttribute(k) { return k in this.attrs; },
    querySelector(selector) {
      // The panel's value editor creates these inputs through innerHTML.
      const match = /^input\[type=(number|checkbox)\]$/.exec(selector);
      if (!match) return null;
      const html = this.innerHTML.match(new RegExp('<input type="' + match[1] + '"[^>]*>'));
      if (!html) return null;
      const input = element(doc, null, "input");
      input.disabled = / disabled/.test(html[0]); input.checked = / checked/.test(html[0]);
      const value = / value="([^"]*)"/.exec(html[0]); input.value = value ? value[1] : "";
      return input;
    },
    querySelectorAll() { return []; },
    closest() { return null; },
    getContext() { return this._ctx || (this._ctx = context2d()); },
    getBoundingClientRect() { return { left: 0, top: 0, right: 800, bottom: 800, width: 800, height: 800 }; },
    capturedPointers: new Set(), releasedPointers: [],
    setPointerCapture(id) { this.capturedPointers.add(id); },
    releasePointerCapture(id) { this.capturedPointers.delete(id); this.releasedPointers.push(id); },
    hasPointerCapture(id) { return this.capturedPointers.has(id); },
    focus() { doc.activeElement = this; }, blur() {}, select() {}, scrollIntoView() { this.scrolled = true; },
    showModal() { this.open = true; }, close() { this.open = false; },
    click() { if (!this.disabled) this.dispatch("click"); },       // a disabled button takes no click
  };
  // Assigning markup removes prior children in a browser; stale nodes would hide render regressions.
  Object.defineProperty(el, "innerHTML", {get() {return this._html || "";}, set(value) {this._html = value; this.children = [];}});
  if (el.tagName === "A") el.click = function () { doc._downloads.push({ name: this.download, text: doc._blobs[this.href] }); };
  return el;
}

function openPage(data, opts) {
  opts = opts || {};
  const storage = opts.storage || memoryStorage();
  const blobs = {}, downloads = [];
  const frames = [];
  let blobN = 0;
  const els = {};
  const document = {
    _blobs: blobs, _downloads: downloads,
    getElementById(id) { return els[id] || (els[id] = element(document, id)); },
    createElement(tag) { return element(document, null, tag); },
    createTextNode(t) { return { nodeType: 3, textContent: String(t) }; },
    querySelectorAll(s) { return s === "[data-area-tool]" ? areaTools : []; },
    querySelector() { return null; },
    addEventListener() {},
  };
  document.body = element(document, "body");
  const areaTools = ["brush", "erase", "fill", "pick", "place", "shrink"].map(tool => {
    const el = element(document, "areaTool-" + tool, "button");
    el.dataset.areaTool = tool; els[el.id] = el; return el;
  });
  ["areasBrushSize", "areasOpacity", "areasAlignPairs"].forEach((id, i) => {
    document.getElementById(id).value = ["1", "50", "2"][i];
  });
  const window = {
    document: document, localStorage: storage, devicePixelRatio: 1, console: console,
    Blob: class { constructor(parts) { this.text = parts.join(""); } },
    URL: { createObjectURL(b) { const k = "blob:" + (++blobN); blobs[k] = b.text; return k; }, revokeObjectURL() {} },
    Image: class { constructor() { this.naturalWidth = this.naturalHeight = 1024; } set src(v) { this.onload(); } },
    addEventListener() {}, removeEventListener() {}, requestAnimationFrame(cb) { frames.push(cb); return frames.length; }, setTimeout() { return 0; },
    clearTimeout() {}, getComputedStyle() { return {}; }, atob: atob, btoa: btoa,
  };
  window.window = window;
  window.globalThis = window;
  vm.createContext(window);
  vm.runInContext(CORE, window, { filename: "control_tagger_core.js" });
  const T = window.TaggerCore;
  const tags = data.tags;
  const state = { map: opts.map || Object.keys(data.maps)[0], mode: "features" };
  const edits = {}, loaded = {}, maps = {};
  let saves = 0;
  Object.keys(data.maps).forEach(function (name) {
    edits[name] = T.editsFrom((tags.maps || {})[name]);
    loaded[name] = T.editsFrom((tags.maps || {})[name]);
  });
  function ensureMap(name) {
    if (!maps[name]) {
      const m = data.maps[name];
      maps[name] = { candidates: m.candidates, params: m.params, image_sha: m.image_sha, lines: m.lines, starts: m.starts,
                     labels: T.rleDecode(m.labels, Int32Array), opaque: T.rleDecode(m.opaque),
                     outline: m.outline ? T.rleDecode(m.outline) : null };
    }
    return maps[name];
  }
  window.tagger = {
    state: state, edits: edits, maps: maps, loaded: loaded, DATA: data, ensureMap: ensureMap,
    save() { saves++; }, recompute() {}, changed() {}, dab() {},
    turns() { return opts.turns || 0; },
    setMode(m) { state.mode = m; window.taggerFeatures.onMode(m); window.taggerAreas.onMode(m); },
    selectMap(n) { state.map = n; window.taggerFeatures.onMap(n); window.taggerAreas.onMap(n); },
  };
  vm.runInContext(PANEL, window, { filename: "control_tagger_features.js" });
  vm.runInContext(AREAS, window, { filename: "control_tagger_areas.js" });
  const page = {
    window: window, T: T, F: T.Features, TG: window.tagger, api: window.taggerFeatures, storage: storage,
    downloads: downloads, el: id => document.getElementById(id), saves: () => saves,
    click(id) { document.getElementById(id).click(); },
    flushFrames() { while (frames.length) frames.shift()(); },
    // the same browser, the page loaded again (same storage, fresh script state)
    reopen(more) { return openPage(data, Object.assign({}, opts, { storage: storage }, more || {})); },
    // a plain copy of an object built inside the page's context
    plain(v) { return JSON.parse(JSON.stringify(v)); },
  };
  return page;
}

module.exports = { openPage: openPage, memoryStorage: memoryStorage, element: element };

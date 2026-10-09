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

// A localStorage stand-in. `failWrites`: every write throws as a full or denied store does.
function memoryStorage(opts) {
  const m = new Map();
  return {
    m: m,
    opts: opts || {},
    getItem(k) { return m.has(k) ? m.get(k) : null; },
    setItem(k, v) {
      if (this.opts.failWrites) { const e = new Error("quota"); e.name = "QuotaExceededError"; throw e; }
      m.set(k, String(v));
    },
    removeItem(k) { m.delete(k); },
  };
}

function context2d() {
  const store = {};
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
    appendChild(c) { this.children.push(c); return c; },
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
    setPointerCapture() {}, releasePointerCapture() {}, focus() {}, blur() {}, select() {}, scrollIntoView() {},
    showModal() { this.open = true; }, close() { this.open = false; },
    click() { if (!this.disabled) this.dispatch("click"); },       // a disabled button takes no click
  };
  if (el.tagName === "A") el.click = function () { doc._downloads.push({ name: this.download, text: doc._blobs[this.href] }); };
  return el;
}

function openPage(data, opts) {
  opts = opts || {};
  const storage = opts.storage || memoryStorage();
  const blobs = {}, downloads = [];
  let blobN = 0;
  const els = {};
  const document = {
    _blobs: blobs, _downloads: downloads,
    getElementById(id) { return els[id] || (els[id] = element(document, id)); },
    createElement(tag) { return element(document, null, tag); },
    createTextNode(t) { return { nodeType: 3, textContent: String(t) }; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
  };
  document.body = element(document, "body");
  const window = {
    document: document, localStorage: storage, devicePixelRatio: 1, console: console,
    Blob: class { constructor(parts) { this.text = parts.join(""); } },
    URL: { createObjectURL(b) { const k = "blob:" + (++blobN); blobs[k] = b.text; return k; }, revokeObjectURL() {} },
    addEventListener() {}, removeEventListener() {}, requestAnimationFrame() { return 0; }, setTimeout() { return 0; },
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
                     labels: T.rleDecode(m.labels, Int32Array), opaque: T.rleDecode(m.opaque) };
    }
    return maps[name];
  }
  window.tagger = {
    state: state, edits: edits, maps: maps, loaded: loaded, DATA: data, ensureMap: ensureMap,
    save() { saves++; }, recompute() {}, changed() {}, dab() {},
    setMode(m) { state.mode = m; }, selectMap(n) { state.map = n; },
  };
  vm.runInContext(PANEL, window, { filename: "control_tagger_features.js" });
  const page = {
    window: window, T: T, F: T.Features, TG: window.tagger, api: window.taggerFeatures, storage: storage,
    downloads: downloads, el: id => document.getElementById(id), saves: () => saves,
    click(id) { document.getElementById(id).click(); },
    // the same browser, the page loaded again (same storage, fresh script state)
    reopen(more) { return openPage(data, Object.assign({}, opts, { storage: storage }, more || {})); },
    // a plain copy of an object built inside the page's context
    plain(v) { return JSON.parse(JSON.stringify(v)); },
  };
  return page;
}

module.exports = { openPage: openPage, memoryStorage: memoryStorage };

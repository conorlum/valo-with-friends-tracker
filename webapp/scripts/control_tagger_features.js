/*
 * The map-features panel of the control tagger (scripts/control_tagger.py; plan
 * docs/superpowers/plans/2026-10-04-map-interaction-tagger.md, M2-M4). Inlined after the page's own script and
 * driven by its `window.tagger` hooks. The model is TaggerCore.Features (pure, tested in node); this file only
 * draws it and turns clicks into model edits. Everything is in minimap u/v (0..10000), independent of zoom.
 *
 * - Map features mode (F): the palette creates a feature from a preset (presets and checklist come from
 *   app/replays/map_feature_schema.py through the page data); tools draw points, lines, polygons and brush
 *   paint into the selected object; Link connects a trigger to the features it operates.
 * - Every edit is one undoable snapshot (links and properties included) and is autosaved as a draft that
 *   belongs to the catalogue, map images and floor assets this page was built from (TaggerCore Drafts).
 * - Export writes the whole tags.json: untouched maps and unknown fields as loaded; it refuses while a map's
 *   annotations have structural errors. Import is transactional and shows what it would change first.
 */
(function () {
  "use strict";
  var TG = window.tagger, T = window.TaggerCore, F = T.Features, D = TG.DATA, FD = D.features || {};
  var PX = T.PX, P = T.P, UVPX = PX / F.UV_MAX;
  var $ = function (id) { return document.getElementById(id); };
  var canonical = D.tags;
  var fe = {};            // map -> {dirty, mf, hist, incompatible}
  var highs = {};         // map -> id high-water mark this page has reached: only grows (undo, imports, drafts)
  var ui = { sel: null, tool: "select", draft: null, zoom: 1, pan: [0, 0], placing: null, editState: {}, preview: {},
             saved: "saved", saveError: null, space: false, panning: null, painting: null, sim: {} };
  var PRESET_LABELS = { drop_door: "Drop-door", switch_door: "Switch door", proximity_door: "Proximity door",
    rotating_door: "Rotating door", breakable: "Breakable", zipline: "Zipline", vertical_rope: "Vertical rope",
    teleporter: "Teleporter", custom: "Custom" };
  var TRIGGER_EVENTS = { "switch": ["switch"], shoot: ["shoot"], proximity: ["proximity_enter", "proximity_leave"], other: ["activate"] };

  function esc(s) { return String(s === undefined || s === null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"); }
  function mapName() { return TG.state.map; }
  function cur() { return fe[mapName()]; }
  function mf() { return cur().mf; }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function uvOf(p) { return [Math.round(p[0] / UVPX * 100) / 100, Math.round(p[1] / UVPX * 100) / 100]; }
  function pxOf(uv) { return [uv[0] * UVPX, uv[1] * UVPX]; }

  // ---------------------------------------------------------------- storage
  var store = {
    getItem: function (k) { return window.localStorage.getItem(k); },
    setItem: function (k, v) { window.localStorage.setItem(k, v); },
    removeItem: function (k) { window.localStorage.removeItem(k); }
  };
  function sourceOf() {
    var images = {}, floors = {};
    Object.keys(D.maps).forEach(function (n) { images[n] = D.maps[n].image_sha; floors[n] = (FD.floors || {})[n] ? FD.floors[n].height_sha : null; });
    return { catalogue_digest: F.digest(D.tags), image_digests: images, floor_digests: floors, schema_version: FD.schema_version };
  }
  var drafts = new F.Drafts(store, sourceOf());

  function setSaved(state, error) {
    ui.saved = state; ui.saveError = error || null;
    var el = $("featSaved");
    el.className = "saved-" + (state === "saved" ? "ok" : state === "dirty" ? "dirty" : "error");
    el.textContent = state === "saved" ? "saved" : state === "dirty" ? "unsaved" : "NOT saved: " + (error || "storage failed") + " (download the draft)";
  }

  // What a draft holds: the catalogue it was edited from, each edited map's model and the id high-water marks.
  function draftBody() {
    var body = { canonical: canonical, features: {}, high: {} };
    Object.keys(fe).forEach(function (n) { if (fe[n].dirty && !fe[n].incompatible) body.features[n] = { dirty: true, mf: fe[n].mf }; });
    Object.keys(highs).forEach(function (n) { if (highs[n] > 1) body.high[n] = highs[n]; });
    return body;
  }

  // A map's undo history from `m`, carrying the page's high-water mark for it (and raising that mark).
  function newHistory(n, m, high) {
    var h = F.history(m, Math.max(highs[n] || 1, high || 1));
    highs[n] = h.high;
    return h;
  }

  function autosave() {
    var body = draftBody();
    setSaved("dirty");
    var r;
    try { r = drafts.save(body); } catch (e) { r = { ok: false, error: String(e) }; }
    if (r.ok) setSaved("saved"); else setSaved("error", r.error);
    return r;
  }

  // A map whose loaded map_features this page can't read (another schema version) is read-only: shown empty,
  // never edited, so its entry is exported, drafted and merged exactly as loaded.
  function initMaps() {
    Object.keys(D.maps).forEach(function (n) {
      var entry = (canonical.maps || {})[n] || {};
      var incompatible = entry.map_features !== undefined && !!F.checkVersion(entry.map_features);
      var m = entry.map_features !== undefined && !incompatible ? clone(entry.map_features) : F.emptyMf();
      var h = newHistory(n, m);
      fe[n] = { dirty: false, mf: h.present, hist: h, incompatible: incompatible };
    });
  }

  // Puts a draft body's edited models in place over a freshly initialised catalogue. Returns the maps skipped.
  function restoreModels(body) {
    var skipped = [];
    Object.keys(body.features || {}).forEach(function (n) {
      if (!fe[n] || fe[n].incompatible) { skipped.push(n); return; }
      var m = body.features[n].mf;
      fe[n] = { dirty: true, mf: m, hist: newHistory(n, m, (body.high || {})[n]), incompatible: false };
      fe[n].mf = fe[n].hist.present;
    });
    Object.keys(body.high || {}).forEach(function (n) {
      if (fe[n] && !fe[n].incompatible && !(body.features || {})[n]) {
        fe[n].hist = newHistory(n, fe[n].mf, body.high[n]);
        fe[n].mf = fe[n].hist.present;
      }
    });
    return skipped;
  }

  // Older versions of Draw sight edge wrote the tool name "line" into saved geometry.
  // Repair only otherwise valid edges after draft integrity checks, retaining every authored field.
  function repairLegacySightLines() {
    var repaired = 0;
    Object.keys(fe).forEach(function (n) {
      var c = fe[n], next = c.mf;
      if (c.incompatible) return;
      (Array.isArray(c.mf.features) ? c.mf.features : []).forEach(function (f) {
        if (!f || typeof f !== "object") return;
        (Array.isArray(f.states) ? f.states : []).forEach(function (s, si) {
          if (!s || typeof s !== "object") return;
          (Array.isArray(s.sight) ? s.sight : []).forEach(function (occ, oi) {
            var g = occ && occ.geometry;
            if (!g || g.type !== "line") return;
            var fixed = Object.assign({}, g, { type: "polyline" });
            if (F.geometryProblems(fixed, ["polyline"]).length) return;
            next = F.setField(next, f.id, ["states", si, "sight", oi, "geometry"], fixed);
            repaired++;
          });
        });
      });
      if (next !== c.mf) {
        c.hist = F.historyPush(c.hist, next);
        c.mf = c.hist.present;
        c.dirty = true;
      }
    });
    return repaired;
  }

  function loadDraft() {
    var r = drafts.load(), note = [];
    if ((r.status === "ok" || r.status === "recovered") && r.body) {
      canonical = r.body.canonical || canonical;
      initMaps();
      var skipped = restoreModels(r.body);
      rebuildLegacy(false);
      note.push(r.status === "recovered" ? "Restored the previous good draft (the newest was damaged)." : "Restored your draft (revision " + r.revision + ").");
      if (skipped.length) note.push("Not restored (not on this page, or its annotations use another schema): " + esc(skipped.join(", ")) + ".");
    } else if (r.status === "corrupt") note.push("The saved draft couldn't be read: " + esc(r.problems.join("; ")) + ". Started from the page's catalogue.");
    else if (r.status === "incompatible") note.push("The saved draft uses a schema this page can't read; it was left untouched. " + esc(r.problems.join("; ")));
    var others = drafts.others();
    others.forEach(function (o) {
      note.push("A draft from another source (" + esc(o.differs.join(", ") || "older page") + ", revision " + o.revision + "): " +
        "<button data-other=\"" + esc(o.key) + "\" data-act=\"review\">Review and restore</button> " +
        "<button data-other=\"" + esc(o.key) + "\" data-act=\"download\">Download</button>");
    });
    $("featDrafts").innerHTML = note.join("<br>");
  }

  // The page's legacy edits for maps nobody touched follow the canonical catalogue (after an import or a draft).
  function rebuildLegacy(force) {
    Object.keys(D.maps).forEach(function (n) {
      if (!force && TG.edits[n] && TG.edits[n].touched) return;
      var entry = (canonical.maps || {})[n];
      if (entry && entry.image_sha && entry.image_sha !== D.maps[n].image_sha) return;    // the minimap changed: kept as is
      TG.edits[n] = T.editsFrom(entry);
      TG.loaded[n] = T.editsFrom(entry);
    });
  }

  // ---------------------------------------------------------------- editing
  // A read-only map (annotations of another schema version) takes no edit: nothing replaces its entry.
  function readOnly() {
    if (!cur().incompatible) return false;
    ui.message = "This map's annotations use another schema version: they are read-only here and kept as loaded.";
    render();
    return true;
  }
  function commit(next, keepSel) {
    if (readOnly()) return;
    var c = cur();
    ui.issueCells = null;
    c.hist = F.historyPush(c.hist, next);
    c.mf = c.hist.present; c.dirty = true;
    highs[mapName()] = Math.max(highs[mapName()] || 1, c.hist.high);
    if (!keepSel && ui.sel && !F.find(c.mf, ui.sel)) ui.sel = null;
    autosave();
    ui.slideClosure = {};
    resimulate();
    render();
  }
  function edit(id, path, value) { commit(F.setField(mf(), id, path, value), true); }
  function step(move) {
    if (readOnly()) return;
    var c = cur();
    ui.issueCells = null;
    c.hist = move(Object.assign({}, c.hist, { high: Math.max(c.hist.high || 1, highs[mapName()] || 1) }));
    c.mf = c.hist.present; c.dirty = true; autosave(); ui.slideClosure = {}; resimulate(); render();
  }
  function doUndo() { step(F.undo); }
  function doRedo() { step(F.redo); }

  function createPreset(name) {
    var label = $("featNewName").value.trim();
    if (name === "trigger") {
      var t = F.addObject(mf(), "trigger", { name: label || "Switch", type: "switch", geometry: null, targets: [] });
      ui.sel = t.id; ui.placing = "trigger"; setTool("point");
      commit(t.mf, true);
      return;
    }
    var seed = (FD.seeds[mapName()] || []).filter(function (c) { return c.preset === name; })[0];
    var r = F.create(mf(), FD.presets[name], label || PRESET_LABELS[name], seed ? { category: seed.key } : {}, (FD.routes || {})[name] || null);
    ui.sel = r.route || r.id;
    if (r.route) { ui.placing = "endpoint:a"; setTool("point"); } else { setTool(name === "rotating_door" ? "point" : "polygon"); ui.placing = name === "rotating_door" ? "pivot" : null; }
    $("featNewName").value = "";
    commit(r.mf, true);
  }

  // The geometry the drawing tools write to for the selection: {path, kind}.
  function target(tool) {
    var o = ui.sel && F.find(mf(), ui.sel);
    if (!o) return null;
    var kind = o.id.split("-")[0], pl = ui.placing || "";
    if (kind === "trigger") return { path: ["geometry"] };
    if (kind === "route") {
      if (pl.indexOf("endpoint:") === 0) {
        var idx = (o.endpoints || []).findIndex(function (e) { return e.id === pl.slice(9); });
        return idx >= 0 ? { path: ["endpoints", idx, "uv"], raw: true } : null;
      }
      if (pl.indexOf("site:") === 0) {
        var sites = (o.access && o.access.sites) || [], j = sites.findIndex(function (s) { return s.id === pl.slice(5); });
        return j >= 0 ? { path: ["access", "sites", j, "uv"], raw: true } : null;
      }
      return { path: ["path"] };
    }
    if (pl === "pivot") return { path: ["rotation", "pivot"] };
    if (pl === "panel") return { path: ["rotation", "panel"] };
    if (pl === "slide-open") return { path: ["sliding", "open_center"] };
    if (pl === "noise") return { path: ["noise", "origin"] };
    if (pl === "potential_ground" || pl === "remove_sight") return { path: ["base_edits", pl] };
    var si = stateIndex(o);
    if (si < 0) return null;
    if (tool === "line") return { path: ["states", si, "sight"], append: true };
    return { path: ["states", si, "footprint"] };
  }

  function stateIndex(o) {
    var name = ui.editState[o.id] || o.initial_state, list = o.states || [];
    var i = list.findIndex(function (s) { return s.name === name; });
    if (i < 0) i = list.findIndex(function (s) { return s.blocks_movement || s.blocks_sight; });
    return i;
  }

  function placePoint(p) {
    var uv = uvOf(p);
    var t = target("point");
    if (!t) return;
    commit(F.setField(mf(), ui.sel, t.path, t.raw ? uv : { type: "point", uv: uv }), true);
    var o = F.find(mf(), ui.sel);
    if (o && o.id.indexOf("route-") === 0 && ui.placing === "endpoint:a") ui.placing = "endpoint:b";
    else if (o && o.id.indexOf("route-") === 0 && ui.placing === "endpoint:b") { ui.placing = null; setTool("select"); }
    render();
  }

  function finishDraft() {
    var d = ui.draft;
    ui.draft = null;
    if (!d) return;
    var need = d.kind === "polygon" ? 3 : 2;
    if (d.points.length < need) { render(); return; }
    var uv = d.points.map(uvOf), t = target(d.kind);
    if (!t) { render(); return; }
    var geom = { type: d.kind === "line" ? "polyline" : d.kind, uv: uv };
    if (t.append) {
      var o = F.find(mf(), ui.sel), si = t.path[1], list = ((o.states[si] || {}).sight || []).slice();
      list.push({ geometry: geom, bounds: { ref: "unresolved" } });
      commit(F.setField(mf(), ui.sel, t.path, list), true);
    } else commit(F.setField(mf(), ui.sel, t.path, geom), true);
  }

  function brushAt(p, erase) {
    var t = target("brush");
    if (!t || t.raw || t.append) return;
    var o = F.find(mf(), ui.sel), g = o, i;
    for (i = 0; i < t.path.length && g; i++) g = g[t.path[i]];
    var cells = g ? F.raster(g) : new Uint8Array(P * P);
    var rad = TG.state.brush / (PX / P), cx = p[0] / (PX / P), cy = p[1] / (PX / P);
    for (var gy = Math.max(0, Math.floor(cy - rad)); gy <= Math.min(P - 1, Math.ceil(cy + rad)); gy++)
      for (var gx = Math.max(0, Math.floor(cx - rad)); gx <= Math.min(P - 1, Math.ceil(cx + rad)); gx++)
        if ((gx + 0.5 - cx) * (gx + 0.5 - cx) + (gy + 0.5 - cy) * (gy + 0.5 - cy) <= rad * rad) cells[gy * P + gx] = erase ? 0 : 1;
    var packed = T.packPaint(cells);
    return { path: t.path, geom: packed ? { type: "paint", cells: packed } : null };
  }

  function linkTo(fid) {
    var t = ui.sel && F.find(mf(), ui.sel);
    if (!t || t.id.indexOf("trigger-") !== 0 || fid.indexOf("feature-") !== 0) return;
    var next = mf();
    (TRIGGER_EVENTS[t.type] || ["activate"]).forEach(function (ev) { next = F.link(next, t.id, fid, ev); });
    commit(next, true);
  }

  function removeSelected(opts) {
    if (!ui.sel) return;
    var r = F.remove(mf(), ui.sel, opts || {});
    if (r.refused && r.dangling.length) { ui.confirmDelete = r.dangling; renderProps(); return; }
    ui.confirmDelete = null;
    ui.sel = null;
    commit(r.mf);
  }

  function placements() {
    var walk = TG.state.masks && TG.state.masks.walk, fd = (FD.floors || {})[mapName()] || {flat:true};
    return F.previewPlacement(mf(), {walk:F.walkableCells(walk), walk_px:walk, flat:fd.flat === true,
                                   floor_counts:fd.floor_counts || [], unresolved:fd.unresolved || []});
  }

  // ---------------------------------------------------------------- drawing
  var ctx = $("feat").getContext("2d");
  function drawGeom(g, stroke, fill, lw) {
    if (!g || F.geometryProblems(g).length) return;
    var z = ui.zoom;
    ctx.lineWidth = (lw || 2) / z; ctx.strokeStyle = stroke; ctx.fillStyle = fill || stroke;
    if (g.type === "point") { var p = pxOf(g.uv); ctx.beginPath(); ctx.arc(p[0], p[1], 5 / z, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); return; }
    if (g.type === "polyline" || g.type === "polygon") {
      ctx.beginPath();
      g.uv.forEach(function (q, i) { var p = pxOf(q); if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); });
      if (g.type === "polygon") { ctx.closePath(); if (fill) ctx.fill(); }
      ctx.stroke(); return;
    }
    if (g.type === "paint") {
      var cells = F.raster(g), c = PX / P;
      ctx.fillStyle = fill || stroke;
      for (var i = 0; i < cells.length; i++) if (cells[i]) ctx.fillRect((i % P) * c, Math.floor(i / P) * c, c, c);
    }
  }
  function centroid(g) {
    if (!g || F.geometryProblems(g).length) return null;
    if (g.type === "point") return pxOf(g.uv);
    if (g.uv) { var sx = 0, sy = 0; g.uv.forEach(function (q) { sx += q[0]; sy += q[1]; }); return pxOf([sx / g.uv.length, sy / g.uv.length]); }
    var cells = F.raster(g), n = 0, x = 0, y = 0;
    for (var i = 0; i < cells.length; i++) if (cells[i]) { n++; x += (i % P) + 0.5; y += Math.floor(i / P) + 0.5; }
    return n ? [x / n * PX / P, y / n * PX / P] : null;
  }
  // Labels are drawn in screen space on their own canvas (outside the zoomed layer), so they stay sharp and the
  // same size at any zoom. `p` is in map px; `dy` a screen-pixel offset (stacked labels).
  var labels = [];
  function label(text, p, colour, dy) { if (p) labels.push([text, p, colour, dy || 0]); }
  function drawLabels() {
    var c = $("featLabels"), w = stage.clientWidth, h = stage.clientHeight, dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); c.style.width = w + "px"; c.style.height = h + "px";
      c.style.position = "absolute"; c.style.inset = "0";
    }
    var g = c.getContext("2d"), k = w / PX;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.font = "bold 12px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "bottom"; g.lineWidth = 3;
    labels.forEach(function (l) {
      var x = ui.pan[0] + l[1][0] * k * ui.zoom, y = ui.pan[1] + l[1][1] * k * ui.zoom - 6 - l[3];
      g.strokeStyle = "rgba(0,0,0,0.85)"; g.strokeText(l[0], x, y);
      g.fillStyle = l[2]; g.fillText(l[0], x, y);
    });
    labels = [];
  }
  function arrow(a, b, colour) {
    var z = ui.zoom, dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy);
    ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = 2 / z;
    if (len < 1) {           // same place (a rope between floors): a vertical marker
      ctx.beginPath(); ctx.arc(a[0], a[1], 9 / z, 0, Math.PI * 2); ctx.stroke(); return;
    }
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    var ux = dx / len, uy = dy / len, s = 10 / z, mx = a[0] + dx * 0.6, my = a[1] + dy * 0.6;
    ctx.beginPath(); ctx.moveTo(mx + ux * s, my + uy * s); ctx.lineTo(mx - uy * s * 0.6, my + ux * s * 0.6); ctx.lineTo(mx + uy * s * 0.6, my - ux * s * 0.6); ctx.closePath(); ctx.fill();
  }

  function layers() {
    var out = {};
    Array.prototype.forEach.call(document.querySelectorAll("[data-layer]"), function (b) { out[b.getAttribute("data-layer")] = b.checked; });
    return out;
  }

  // Landings and access sites the permanent walk mask doesn't cover: flagged (the minimap may omit the ground),
  // never snapped anywhere. Restored ground only counts once a bundle publishes it, so it isn't added here.
  function offGround(m) {
    var walk = TG.state.masks && TG.state.masks.walk, out = [];
    if (!walk) return out;
    (m.routes || []).forEach(function (r) {
      (r.endpoints || []).concat((r.access && r.access.sites) || []).forEach(function (e) {
        if (!e.uv) return;
        var p = pxOf(e.uv), i = Math.min(PX - 1, Math.floor(p[1])) * PX + Math.min(PX - 1, Math.floor(p[0]));
        if (!walk[i]) out.push(r.id + "." + e.id);
      });
    });
    return out;
  }

  function shownState(f) {
    var name = (ui.sim[mapName()] || {})[f.id];
    return name || ui.editState[f.id] || f.initial_state;
  }

  // ---------------------------------------------------------------- the sandbox (not replay evidence)
  // Simulated events on a clock; each feature's state is the shared reducer's answer (TaggerCore.Features.run,
  // the twin of map_feature_state.py). Reset round clears the events: every feature is back at its round start.
  function simOf() {
    var n = mapName();
    ui.simAll = ui.simAll || {};
    return ui.simAll[n] = ui.simAll[n] || { t: 0, events: [], traces: {} };
  }
  function resimulate() {
    var s = simOf(), states = {};
    (mf().features || []).forEach(function (f) {
      var mine = s.events.filter(function (e) { return e.feature === f.id; }).map(function (e) {
        var o = { t: e.t, kind: e.kind }; if (e.occupant) o.occupant = e.occupant; return o;
      });
      var trace = [];
      s.fullStates = s.fullStates || {};
      try { var evaluated = F.evaluate(f, mine, s.t); trace = evaluated.trace; s.fullStates[f.id] = evaluated.state; }
      catch (err) { delete s.fullStates[f.id]; trace = [{ t: s.t, kind: "error", result: "rejected", reason: String(err.message), state: f.initial_state }]; }
      s.traces[f.id] = trace;
      if (trace.length) states[f.id] = trace[trace.length - 1].state;
    });
    ui.sim[mapName()] = states;
  }
  function simulate(fid, kind, occupant) {
    ui.slideClosure = {};
    var s = simOf();
    s.events.push({ t: s.t, kind: kind, feature: fid, occupant: occupant });
    resimulate(); render();
  }
  function simTrigger(tid) {
    ui.slideClosure = {};
    var t = F.find(mf(), tid), s = simOf();
    (t && t.targets || []).forEach(function (x) { s.events.push({ t: s.t, kind: x.event, feature: x.feature, occupant: x.event.indexOf("proximity") === 0 ? "sim" : undefined }); });
    resimulate(); render();
  }
  function simAdvance(dt) { ui.slideClosure = {}; simOf().t = Math.round((simOf().t + dt) * 1000) / 1000; resimulate(); render(); }
  function simReset() { ui.slideClosure = {}; var s = simOf(); s.events = []; s.t = 0; s.traces = {}; s.fullStates = {}; ui.sim[mapName()] = {}; render(); }

  function enableSliding(id) {
    var f = F.find(mf(), id), names = f && (f.states || []).map(function(s) {return s.name;});
    if (!names || names.indexOf("open") < 0 || names.indexOf("closed") < 0 || f.sliding) return false;
    commit(F.setField(mf(), id, ["sliding"], {open_state: "open", closed_state: "closed", open_center: null}), true);
    return true;
  }
  function slidingPreview(f) {
    if (!f.sliding) return null;
    var sim = simOf(), manual = (ui.slideClosure || {})[f.id], full = (sim.fullStates || {})[f.id];
    if (full && full.state !== shownState(f)) full = null;
    var fraction = manual !== undefined ? manual : F.slidingClosureAt(f, full || {state: shownState(f)}, sim.t);
    if (fraction === null) return null;
    var cells = F.slidingCoverage(f, fraction);
    if (!cells) return null;
    return {fraction: fraction, cells: cells, pose: F.slidingPose(f, fraction)};
  }

  // The preview's masks with every feature in its shown state (TaggerCore composeFeatures = features.py
  // compose_masks): drawn as the difference from the permanent masks.
  function composedOverlay() {
    var base = TG.state.masks;
    if (!base || !ui.showComposed) return null;
    var states = {};
    (mf().features || []).forEach(function (f) { states[f.id] = shownState(f); });
    var m = F.composeFeatures(base.sight, base.walk, mf(), states, placements()), img = ctx.createImageData(PX, PX), d = img.data;
    for (var i = 0; i < PX * PX; i++) {
      if (m.walk[i] !== base.walk[i]) { d[i * 4] = 255; d[i * 4 + 1] = 209; d[i * 4 + 2] = 102; d[i * 4 + 3] = 120; }
      if (m.sight[i] !== base.sight[i]) { d[i * 4] = 229; d[i * 4 + 1] = 72; d[i * 4 + 2] = 77; d[i * 4 + 3] = 170; }
    }
    return img;
  }

  function draw() {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, PX, PX);
    labels = [];
    if (!cur()) { drawLabels(); return; }
    var m = mf(), overlay = composedOverlay(), L = layers();
    if (overlay) ctx.putImageData(overlay, 0, 0);
    if (L.grid) {
      ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.lineWidth = 1 / ui.zoom; ctx.beginPath();
      for (var g = 0; g <= PX; g += 8) { ctx.moveTo(g, 0); ctx.lineTo(g, PX); ctx.moveTo(0, g); ctx.lineTo(PX, g); }
      ctx.stroke();
    }
    if (ui.issueCells) {
      ctx.fillStyle = "rgba(255,165,0,0.55)";
      ui.issueCells.forEach(function (cell) { ctx.fillRect((cell % 128) * 8, Math.floor(cell / 128) * 8, 8, 8); });
    }
    (L.features ? m.features || [] : []).forEach(function (f) {
      var sel = f.id === ui.sel, name = shownState(f), st = (f.states || []).filter(function (s) { return s.name === name; })[0];
      var colour = sel ? "rgba(255,255,255,0.95)" : "rgba(76,201,240,0.9)";
      (f.states || []).forEach(function (s) {
        if (s !== st && s.footprint && sel) drawGeom(s.footprint, "rgba(76,201,240,0.35)", null, 1);
      });
      var slide = slidingPreview(f);
      if (st && !slide) {
        if (st.footprint) drawGeom(st.footprint, colour, st.blocks_movement ? "rgba(76,201,240,0.35)" : "rgba(76,201,240,0.08)");
        if (L.sight) (st.sight || []).forEach(function (o) { drawGeom(o.geometry, (o.bounds || {}).ref === "unresolved" ? "rgba(245,165,36,0.9)" : "rgba(229,72,77,0.95)", null, 3); });
      }
      if (slide) {
        var closed = F.slidingClosed(f);
        drawGeom(closed.footprint, "rgba(76,201,240,0.35)", null, 1);
        drawGeom(f.sliding.open_center, colour);
        drawGeom(slide.pose, "rgba(76,201,240,0.65)", null, 2);
        var packed = T.packPaint(slide.cells);
        if (packed) drawGeom({type: "paint", cells: packed}, colour, "rgba(229,72,77,0.65)");
        var centre = F.slidingCenter(f);
        drawGeom({type:"point",uv:centre}, "rgba(255,214,0,0.9)");
      }
      var edits = f.base_edits || {};
      if (L.base && edits.potential_ground) drawGeom(edits.potential_ground, "rgba(48,164,108,0.9)", "rgba(48,164,108,0.3)", 1);
      if (L.base && edits.remove_sight) drawGeom(edits.remove_sight, "rgba(190,110,255,0.9)", "rgba(190,110,255,0.25)", 1);
      if (f.rotation) {
        drawGeom(f.rotation.pivot, colour);
        var frac = (ui.rotFrac || {})[f.id], pose = frac ? F.rotationPose(f, frac) : null;
        drawGeom(f.rotation.panel, pose ? "rgba(76,201,240,0.35)" : colour, null, 4);
        if (pose) drawGeom(pose, colour, null, 4);
        (f.rotation.phases || []).forEach(function (ph) { if (ph && ph.panel) drawGeom(ph.panel, "rgba(76,201,240,0.25)", null, 2); });
      }
      if (f.noise && f.noise.origin) drawGeom(f.noise.origin, "rgba(255,214,0,0.9)");
      var at = centroid(st && st.footprint) || centroid(f.rotation && f.rotation.pivot);
      label((f.name || f.id) + (name ? " · " + name : ""), at, sel ? "#fff" : "#a5e9fb");
    });
    (L.triggers ? m.triggers || [] : []).forEach(function (t) {
      var sel = t.id === ui.sel, colour = sel ? "#fff" : "rgba(247,37,133,0.95)";
      drawGeom(t.geometry, colour, t.geometry && t.geometry.type !== "point" ? "rgba(247,37,133,0.2)" : colour);
      var from = centroid(t.geometry);
      (t.targets || []).forEach(function (x) {
        var f = F.find(m, x.feature);
        if (!f || !from) return;
        var st = (f.states || []).filter(function (s) { return s.footprint; })[0], to = centroid(st && st.footprint) || centroid(f.rotation && f.rotation.pivot);
        if (!to) return;
        ctx.setLineDash([6 / ui.zoom, 4 / ui.zoom]); arrow(from, to, "rgba(247,37,133,0.7)"); ctx.setLineDash([]);
      });
      label(t.name || t.id, from, colour);
    });
    (L.routes ? m.routes || [] : []).forEach(function (r) {
      var sel = r.id === ui.sel, colour = sel ? "#fff" : "rgba(181,228,140,0.95)";
      if (r.path) drawGeom(r.path, colour, null, 1);
      var pts = {}, stacked = {};
      (r.endpoints || []).concat((r.access && r.access.sites) || []).forEach(function (e) {
        if (!e.uv) return;
        pts[e.id] = pxOf(e.uv);
        drawGeom({ type: "point", uv: e.uv }, colour);
        var fl = "automatic placement";
        // landings at one spot (a rope between floors) stack their labels instead of overprinting
        var key = e.uv.join(","), n = stacked[key] = (stacked[key] || 0) + 1;
        label(e.id.toUpperCase() + " · " + fl, pxOf(e.uv), colour, (n - 1) * 15);
      });
      (r.directions || []).forEach(function (d) { if (pts[d.from] && pts[d.to]) arrow(pts[d.from], pts[d.to], colour); });
    });
    if (ui.draft && ui.draft.points.length) {
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 / ui.zoom; ctx.beginPath();
      ui.draft.points.forEach(function (p, i) { if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); });
      if (ui.draft.hover) ctx.lineTo(ui.draft.hover[0], ui.draft.hover[1]);
      ctx.stroke();
    }
    drawLabels();
  }

  // ---------------------------------------------------------------- hit testing
  function distSeg(p, a, b) {
    var dx = b[0] - a[0], dy = b[1] - a[1], dd = dx * dx + dy * dy, t = dd ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]);
  }
  function near(g, p, tol) {
    if (!g) return false;
    if (g.type === "point") { var q = pxOf(g.uv); return Math.hypot(q[0] - p[0], q[1] - p[1]) <= tol; }
    if (g.type === "polyline" || g.type === "polygon") {
      var pts = g.uv.map(pxOf), n = pts.length, i;
      for (i = 0; i + 1 < n; i++) if (distSeg(p, pts[i], pts[i + 1]) <= tol) return true;
      if (g.type === "polygon") {
        if (distSeg(p, pts[n - 1], pts[0]) <= tol) return true;
        var inside = false;
        for (i = 0, j = n - 1; i < n; j = i++) {
          var j;
          if ((pts[i][1] > p[1]) !== (pts[j][1] > p[1]) && p[0] < (pts[j][0] - pts[i][0]) * (p[1] - pts[i][1]) / (pts[j][1] - pts[i][1]) + pts[i][0]) inside = !inside;
        }
        return inside;
      }
      return false;
    }
    if (g.type === "paint") { var cells = F.raster(g), c = PX / P; return !!cells[Math.floor(p[1] / c) * P + Math.floor(p[0] / c)]; }
    return false;
  }
  function hit(p, onlyFeatures) {
    var m = mf(), tol = 8 / ui.zoom, out = null;
    if (!onlyFeatures) {
      (m.triggers || []).forEach(function (t) { if (!out && near(t.geometry, p, tol)) out = t.id; });
      (m.routes || []).forEach(function (r) {
        if (out) return;
        (r.endpoints || []).forEach(function (e) { if (!out && e.uv && near({ type: "point", uv: e.uv }, p, tol)) out = r.id; });
        if (!out && near(r.path, p, tol)) out = r.id;
      });
    }
    (m.features || []).forEach(function (f) {
      if (out) return;
      (f.states || []).forEach(function (s) {
        if (!out && (near(s.footprint, p, tol) || (s.sight || []).some(function (o) { return near(o.geometry, p, tol); }))) out = f.id;
      });
      if (!out && f.rotation && (near(f.rotation.pivot, p, tol) || near(f.rotation.panel, p, tol))) out = f.id;
    });
    return out;
  }

  // ---------------------------------------------------------------- pointer, zoom, keys
  var canvas = $("feat"), stage = $("stage");
  function toMap(e) { var r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * PX, (e.clientY - r.top) / r.height * PX]; }
  function applyZoom() {
    $("zoom").style.transform = "translate(" + ui.pan[0] + "px," + ui.pan[1] + "px) scale(" + ui.zoom + ")";
    draw();
  }
  function zoomAt(factor, cx, cy) {
    var z = Math.min(16, Math.max(1, ui.zoom * factor)), f = z / ui.zoom;
    ui.pan = [cx - (cx - ui.pan[0]) * f, cy - (cy - ui.pan[1]) * f];
    ui.zoom = z;
    if (z === 1) ui.pan = [0, 0];
    applyZoom();
  }
  stage.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = stage.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  stage.addEventListener("pointerdown", function (e) {
    if (e.button === 1 || (e.button === 0 && ui.space)) {
      e.preventDefault(); e.stopPropagation();
      ui.panning = [e.clientX, e.clientY, ui.pan[0], ui.pan[1]];
      stage.setPointerCapture(e.pointerId);
    }
  }, true);
  stage.addEventListener("pointermove", function (e) {
    if (!ui.panning) return;
    ui.pan = [ui.panning[2] + e.clientX - ui.panning[0], ui.panning[3] + e.clientY - ui.panning[1]];
    applyZoom();
  });
  stage.addEventListener("pointerup", function () { ui.panning = null; });

  canvas.addEventListener("pointerdown", function (e) {
    if (e.button !== 0 || ui.panning) return;
    var p = toMap(e);
    if (ui.tool === "select") { ui.sel = hit(p); ui.issueCells = null; render(); return; }
    if (ui.tool === "link") { var f = hit(p, true); if (f) linkTo(f); return; }
    if (ui.tool === "point") { placePoint(p); return; }
    if (ui.tool === "line" || ui.tool === "polygon") {
      ui.draft = ui.draft && ui.draft.kind === ui.tool ? ui.draft : { kind: ui.tool, points: [] };
      ui.draft.points.push(p); draw(); return;
    }
    if (ui.tool === "brush" || ui.tool === "erase") {
      var r = brushAt(p, ui.tool === "erase");
      if (!r) return;
      ui.painting = { mf: F.setField(mf(), ui.sel, r.path, r.geom), last: p };
      cur().mf = ui.painting.mf; draw();
      canvas.setPointerCapture(e.pointerId);
    }
  });
  canvas.addEventListener("pointermove", function (e) {
    var p = toMap(e);
    if (ui.draft) { ui.draft.hover = p; draw(); }
    if (!ui.painting) return;
    var dx = p[0] - ui.painting.last[0], dy = p[1] - ui.painting.last[1], n = Math.max(1, Math.ceil(Math.hypot(dx, dy) / 3));
    for (var k = 1; k <= n; k++) {
      var r = brushAt([ui.painting.last[0] + dx * k / n, ui.painting.last[1] + dy * k / n], ui.tool === "erase");
      if (r) { ui.painting.mf = F.setField(cur().mf, ui.sel, r.path, r.geom); cur().mf = ui.painting.mf; }
    }
    ui.painting.last = p; draw();
  });
  function endPaint() {
    if (!ui.painting) return;
    var next = ui.painting.mf;
    ui.painting = null;
    cur().mf = cur().hist.present;          // the stroke becomes one undo step
    commit(next, true);
  }
  canvas.addEventListener("pointerup", endPaint);
  canvas.addEventListener("pointercancel", endPaint);
  canvas.addEventListener("dblclick", function () { finishDraft(); });

  function setTool(tool) {
    ui.tool = tool;
    if (tool !== "line" && tool !== "polygon") ui.draft = null;
    Array.prototype.forEach.call(document.querySelectorAll("[data-ftool]"), function (b) {
      b.classList.toggle("active", b.getAttribute("data-ftool") === tool);
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === " ") ui.space = true;
    if (TG.state.mode !== "features") return;
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && e.target.type !== "checkbox") return;
    var k = e.key, lower = k.toLowerCase(), used = true;
    if ((e.ctrlKey || e.metaKey) && lower === "z" && !e.shiftKey) doUndo();
    else if ((e.ctrlKey || e.metaKey) && (lower === "y" || (lower === "z" && e.shiftKey))) doRedo();
    else if (k === "Enter") finishDraft();
    else if (k === "Escape" && (ui.draft || ui.placing)) { ui.draft = null; ui.placing = null; setTool("select"); render(); }
    else if (k === "Delete") removeSelected();
    else if (k === "+" || k === "=") zoomAt(1.25, stage.clientWidth / 2, stage.clientHeight / 2);
    else if (k === "-") zoomAt(0.8, stage.clientWidth / 2, stage.clientHeight / 2);
    else if ({ v: 1, ".": 1, l: 1, g: 1, r: 1, x: 1, k: 1 }[lower] && !e.ctrlKey && !e.metaKey)
      setTool({ v: "select", ".": "point", l: "line", g: "polygon", r: "brush", x: "erase", k: "link" }[lower]);
    else used = false;
    if (used) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  document.addEventListener("keyup", function (e) { if (e.key === " ") ui.space = false; });

  // ---------------------------------------------------------------- panels
  function valueEditor(v, unit, onChange) {
    var known = v && v.status === "known";
    var wrap = document.createElement("span");
    wrap.innerHTML = "<input type=\"number\" step=\"any\" min=\"0\" style=\"width:6em\"" + (known ? " value=\"" + esc(v.value) + "\"" : " disabled") + "> " +
      esc(unit) + " <label style=\"display:inline-flex\"><input type=\"checkbox\"" + (known ? "" : " checked") + "> unknown</label>";
    var num = wrap.querySelector("input[type=number]"), box = wrap.querySelector("input[type=checkbox]");
    function fire() {
      num.disabled = box.checked;
      if (box.checked) onChange({ status: "unresolved" });
      else if (num.value !== "" && isFinite(+num.value)) onChange({ status: "known", value: +num.value, unit: unit });
    }
    num.addEventListener("change", fire); box.addEventListener("change", fire);
    return wrap;
  }
  function row(labelText, el) {
    var l = document.createElement("label");
    l.appendChild(document.createTextNode(labelText));
    l.appendChild(el);
    return l;
  }
  function selectEl(options, value, onChange) {
    var s = document.createElement("select");
    options.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o[0]; opt.textContent = o[1];
      if (o[0] === value) opt.selected = true;
      s.appendChild(opt);
    });
    s.addEventListener("change", function () { onChange(s.value); });
    return s;
  }
  function textEl(value, onChange) {
    var i = document.createElement("input");
    i.type = "text"; i.value = value || "";
    i.addEventListener("change", function () { onChange(i.value); });
    return i;
  }
  function checkEl(value, onChange) {
    var i = document.createElement("input");
    i.type = "checkbox"; i.checked = !!value;
    i.addEventListener("change", function () { onChange(i.checked); });
    return i;
  }
  function button(text, onClick, title) {
    var b = document.createElement("button");
    b.textContent = text; if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }
  function fieldset(legend) {
    var f = document.createElement("fieldset"), l = document.createElement("legend");
    l.textContent = legend; f.appendChild(l);
    return f;
  }
  function boundsEditor(fid, path, bounds) {
    var b = bounds || { ref: "unresolved" }, box = document.createElement("div");
    box.id = "feat-field-" + fid + "-" + path.join("-"); box.tabIndex = -1;
    box.appendChild(row("Sight bounds", selectEl([["unresolved", "unresolved"], ["ground", "above a floor's ground"], ["world", "world height"], ["all_height", "every height (verified)"]], b.ref, function (ref) {
      var nb = { ref: ref };
      if (ref === "ground" || ref === "world") { nb.bottom = b.bottom || { status: "unresolved" }; nb.top = b.top || { status: "unresolved" }; }
      edit(fid, path, nb);
    })));
    if (b.ref === "ground" || b.ref === "world") {
      box.appendChild(row("Bottom", valueEditor(b.bottom, "m", function (v) { edit(fid, path.concat(["bottom"]), v); })));
      box.appendChild(row("Top", valueEditor(b.top, "m", function (v) { edit(fid, path.concat(["top"]), v); })));
    }
    return box;
  }

  function renderProps() {
    var box = $("featProps"), o = ui.sel && F.find(mf(), ui.sel);
    box.innerHTML = "";
    box.hidden = !o || TG.state.mode !== "features";
    if (!o) return;
    var kind = o.id.split("-")[0], h = document.createElement("h2");
    h.textContent = kind + " " + o.id + (o.preset ? " · " + (PRESET_LABELS[o.preset] || o.preset) : "");
    box.appendChild(h);
    if (ui.confirmDelete) {
      var warn = document.createElement("div");
      warn.className = "note issue-warn";
      warn.innerHTML = "Linked from: " + ui.confirmDelete.map(function (r) { return esc(r[0] + " (" + r[1] + ")"); }).join(", ");
      box.appendChild(warn);
      box.appendChild(button("Delete and unlink everything", function () { removeSelected({ cascade: true }); }));
      var others = (mf()[kind === "feature" ? "features" : kind + "s"] || []).filter(function (x) { return x.id !== o.id; });
      if (others.length) box.appendChild(row("Or relink to", selectEl([["", "choose…"]].concat(others.map(function (x) { return [x.id, x.name || x.id]; })), "", function (v) { if (v) removeSelected({ relink: v }); })));
      box.appendChild(button("Cancel", function () { ui.confirmDelete = null; renderProps(); }));
      return;
    }
    box.appendChild(row("Name", textEl(o.name, function (v) { commit(F.rename(mf(), o.id, v), true); })));
    if (kind === "feature") featureProps(box, o);
    else if (kind === "trigger") triggerProps(box, o);
    else if (kind === "route") routeProps(box, o);
    var actions = document.createElement("div");
    actions.className = "row";
    if (kind === "feature") {
      actions.appendChild(button("Duplicate", function () { var r = F.duplicate(mf(), o.id); ui.sel = r.id; commit(r.mf, true); }));
      actions.appendChild(button("Duplicate with its triggers", function () { var r = F.duplicate(mf(), o.id, { keepExternal: true }); ui.sel = r.id; commit(r.mf, true); }));
    }
    actions.appendChild(button("Delete", function () { removeSelected(); }, "Delete (Del)"));
    box.appendChild(actions);
  }

  function featureProps(box, f) {
    var seeds = FD.seeds[mapName()] || [];
    box.appendChild(row("Status", selectEl([["draft", "draft"], ["needs_verification", "needs verification"], ["user_reviewed", "user reviewed"]], f.review || "draft",
      function (v) { edit(f.id, ["review"], v); })));
    box.appendChild(row("Checklist", selectEl([["", "(none)"]].concat(seeds.map(function (c) { return [c.key, c.label]; })), f.category || "",
      function (v) { edit(f.id, ["category"], v || undefined); })));
    var names = (f.states || []).map(function (s) { return s.name; });
    box.appendChild(row("Round-start state", selectEl([["", "needs verification"]].concat(names.map(function (n) { return [n, n]; })), f.initial_state || "",
      function (v) { edit(f.id, ["initial_state"], v || null); })));
    box.appendChild(row("Drawing on state", selectEl(names.map(function (n) { return [n, n]; }), ui.editState[f.id] || f.initial_state || names[0],
      function (v) { ui.editState[f.id] = v; ui.issueCells = null; render(); })));
    box.appendChild(row('Placement', document.createTextNode('Automatic: every required cell must have one measured floor.')));
    (f.states || []).forEach(function (s, si) {
      var fs = fieldset("State: " + s.name + (s.terminal ? " (terminal for the round)" : ""));
      fs.id = "feat-state-" + f.id + "-" + si; fs.tabIndex = -1;
      fs.appendChild(row("Blocks movement", checkEl(s.blocks_movement, function (v) { edit(f.id, ["states", si, "blocks_movement"], v); })));
      fs.appendChild(row("Blocks vision", checkEl(s.blocks_sight, function (v) { edit(f.id, ["states", si, "blocks_sight"], v); })));
      fs.appendChild(row("Footprint", document.createTextNode(s.footprint ? s.footprint.type : "not drawn")));
      if (s.footprint && s.blocks_sight) fs.appendChild(boundsEditor(f.id, ["states", si, "sight_bounds"], s.sight_bounds));
      (s.sight || []).forEach(function (occ, j) {
        var sub = fieldset("Sight blocker " + (j + 1));
        if (!s.blocks_sight) sub.appendChild(document.createTextNode("This state does not block vision. Its sight edge has no vision effect, but still needs valid geometry and placement bounds. Remove it explicitly if it is unintended."));
        sub.appendChild(boundsEditor(f.id, ["states", si, "sight", j, "bounds"], occ.bounds));
        sub.appendChild(button("Remove", function () {
          var next = s.sight.slice(); next.splice(j, 1); edit(f.id, ["states", si, "sight"], next);
        }));
        fs.appendChild(sub);
      });
      var drawFootprint = button("Draw footprint", function () { ui.editState[f.id] = s.name; ui.placing = null; setTool("polygon"); render(); });
      drawFootprint.id = "feat-field-" + f.id + "-states-" + si + "-footprint";
      fs.appendChild(drawFootprint);
      var sources = (f.states || []).filter(function (other) { return other.name !== s.name && other.footprint && !F.geometryProblems(other.footprint).length; });
      if (sources.length) {
        var copyBounds = button("Copy footprint and vision bounds", function () { copyFootprint(f.id, fromState.value, s.name, true); });
        function updateCopyBounds() { var source = sources.find(function (other) { return other.name === fromState.value; }); copyBounds.disabled = !source || !source.sight_bounds; }
        var fromState = selectEl(sources.map(function (other) { return [other.name, other.name]; }), sources[0].name, updateCopyBounds);
        fs.appendChild(row("Copy footprint from", fromState));
        fs.appendChild(button("Copy footprint only", function () { copyFootprint(f.id, fromState.value, s.name); }));
        fs.appendChild(copyBounds); updateCopyBounds();
        fs.appendChild(document.createTextNode(" Copies the shape and optional bounds. Choose motion coverage explicitly; movement/vision flags and motion behavior stay as set."));
      }
      fs.appendChild(button("Draw sight edge", function () { ui.editState[f.id] = s.name; ui.placing = null; setTool("line"); render(); }));
      if (s.footprint) fs.appendChild(button("Clear footprint", function () { edit(f.id, ["states", si, "footprint"], undefined); }));
      box.appendChild(fs);
    });
    var tr = fieldset("Behaviour (transitions)");
    (f.transitions || []).forEach(function (r, ri) {
      var line = document.createElement("div");
      line.className = "note";
      line.textContent = (r.from === "*" ? "any state" : (r.from || []).join("/")) + " —" + r.event + (r.name ? " " + r.name : "") + "→ " + r.to;
      tr.appendChild(line);
      if (r.motion) {
        tr.appendChild(row("Motion time", valueEditor(r.motion.duration, "s", function (v) { edit(f.id, ["transitions", ri, "motion", "duration"], v); })));
        tr.appendChild(row("Pressed while moving", selectEl([["unresolved", "unknown"], ["ignore", "ignored"], ["queue", "runs after"], ["restart", "restarts"], ["reverse", "reverses"]],
          r.mid_motion || "unresolved", function (v) { edit(f.id, ["transitions", ri, "mid_motion"], v); })));
      }
      if (r.follow_up) tr.appendChild(row("Then after", valueEditor(r.follow_up.after, "s", function (v) { edit(f.id, ["transitions", ri, "follow_up", "after"], v); })));
    });
    box.appendChild(tr);
    if (f.sliding) {
      var sliding = fieldset("Sliding panel (preview)");
      sliding.appendChild(document.createTextNode("The closed footprint is the panel and doorway. Place the open centre where the yellow closed-centre marker moves when fully open. Red cells show the panel inside the doorway. This previews geometry; it does not verify placement or replay behavior."));
      ["open_state", "closed_state"].forEach(function(key) {
        sliding.appendChild(row(key === "open_state" ? "Open state" : "Closed state", selectEl(names.map(function(n) {return [n,n];}), f.sliding[key],
          function(v) {edit(f.id, ["sliding",key],v);}))); });
      sliding.appendChild(button("Place open panel centre", function() {ui.placing = "slide-open"; setTool("point");}));
      F.slidingProblems(f).forEach(function(message) {sliding.appendChild(row("Needs input",document.createTextNode(message)));});
      var openCoverage = F.slidingCoverage(f,0);
      if (openCoverage && openCoverage.some(function(cell) {return !!cell;}))
        sliding.appendChild(row("Needs input",document.createTextNode("The panel still covers part of the doorway at fully open. Move the open centre farther along its travel direction.")));
      var closeRow = (f.transitions || []).find(function(r) {return r.motion && r.to === f.sliding.closed_state;});
      var duration = closeRow && F.known(closeRow.motion.duration), validDuration = Number.isFinite(duration) && duration > 0;
      var current = slidingPreview(f), fraction = current ? current.fraction : 0;
      var slideLabel = document.createElement("span"); slideLabel.id = "slideLabel";
      function labelClosure(frac) {slideLabel.textContent = (validDuration ? (frac*duration).toFixed(2) + " / " + duration + " s — " : "") + Math.round(frac*100) + "% closed";}
      labelClosure(fraction);
      if (!current) slideLabel.textContent = "Position unknown. Scrub to preview a pose.";
      var closeSlider = document.createElement("input"); closeSlider.type = "range"; closeSlider.min = "0"; closeSlider.max = "100"; closeSlider.step = "1";
      closeSlider.id = "slideSlider"; closeSlider.value = String(Math.round(fraction*100)); closeSlider.disabled = F.slidingProblems(f).length > 0;
      closeSlider.addEventListener("input",function() {ui.slideClosure = ui.slideClosure || {}; ui.slideClosure[f.id] = +closeSlider.value/100; labelClosure(+closeSlider.value/100); draw();});
      sliding.appendChild(row("Closing preview",closeSlider)); sliding.appendChild(slideLabel);
      sliding.appendChild(button("Follow sandbox clock",function() {ui.slideClosure = ui.slideClosure || {}; delete ui.slideClosure[f.id]; resimulate(); render();}));
      sliding.appendChild(button("Remove sliding preview",function() {edit(f.id,["sliding"],undefined);}));
      box.appendChild(sliding);
    } else if (names.indexOf("open") >= 0 && names.indexOf("closed") >= 0 && !f.rotation) {
      box.appendChild(button("Enable linear sliding preview",function() {enableSliding(f.id);}));
    }
    var caps = fieldset("Capabilities");
    var breakable = (f.capabilities || []).indexOf("breakable") >= 0;
    if (breakable) caps.appendChild(row("Breakable", document.createTextNode("yes: destroyed until the next round")));
    else caps.appendChild(button("Also breakable", function () {
      var next = clone(mf());
      next.features = next.features.map(function (x) { return x.id === f.id ? F.makeBreakable(x) : x; });
      commit(next, true);
    }, "adds a terminal 'broken' state and a destroy transition from any state"));
    caps.appendChild(row("Makes noise", checkEl((f.noise || {}).makes_noise, function (v) { edit(f.id, ["noise", "makes_noise"], v); })));
    caps.appendChild(row("Noise notes", textEl((f.noise || {}).notes, function (v) { edit(f.id, ["noise", "notes"], v); })));
    caps.appendChild(button("Place sound origin", function () { ui.placing = "noise"; setTool("point"); }));
    box.appendChild(caps);
    if (f.rotation) {
      var rot = fieldset("Rotation");
      rot.appendChild(button("Place pivot", function () { ui.placing = "pivot"; setTool("point"); }));
      rot.appendChild(button("Draw panel", function () { ui.placing = "panel"; setTool("line"); }));
      rot.appendChild(row("Direction", selectEl([["", "unknown"], ["cw", "clockwise"], ["ccw", "anticlockwise"]],
        (f.rotation.direction || {}).status === "known" ? f.rotation.direction.value : "",
        function (v) { edit(f.id, ["rotation", "direction"], v ? { status: "known", value: v, unit: "dir" } : { status: "unresolved" }); })));
      rot.appendChild(row("Start angle", valueEditor(f.rotation.start_deg, "deg", function (v) { edit(f.id, ["rotation", "start_deg"], v); })));
      rot.appendChild(row("End angle", valueEditor(f.rotation.end_deg, "deg", function (v) { edit(f.id, ["rotation", "end_deg"], v); })));
      var slider = document.createElement("input");
      slider.type = "range"; slider.min = "0"; slider.max = "100"; slider.id = "rotSlider";
      slider.value = String(Math.round(((ui.rotFrac || {})[f.id] || 0) * 100));
      slider.addEventListener("input", function () { ui.rotFrac = ui.rotFrac || {}; ui.rotFrac[f.id] = +slider.value / 100; draw(); });
      rot.appendChild(row(F.rotationPose(f, 0.5) ? "Motion preview" : "Motion preview (describe pivot, panel, angles and direction first)", slider));
      rot.appendChild(button("Save this pose as a phase", function () {
        var frac = (ui.rotFrac || {})[f.id] || 0, pose = F.rotationPose(f, frac);
        if (!pose) return;
        var phases = (f.rotation.phases || []).filter(function (p) { return p.at !== frac; }).concat([{ at: frac, panel: pose }]);
        edit(f.id, ["rotation", "phases"], phases);
      }, "custom phase geometry overrides the rigid-panel approximation"));
      box.appendChild(rot);
    }
    var sim = fieldset("Sandbox (simulated, not replay evidence) · t = " + simOf().t + " s");
    sim.appendChild(row("Show state", selectEl([["", "as simulated"]].concat(names.map(function (n) { return [n, n]; })),
      (ui.sim[mapName()] || {})[f.id] || "", function (v) { ui.sim[mapName()] = ui.sim[mapName()] || {}; if (v) ui.sim[mapName()][f.id] = v; else resimulate(); render(); })));
    var evs = {};
    (f.transitions || []).forEach(function (r) { if (r.event !== "scheduled") evs[r.event] = true; });
    var evRow = document.createElement("div");
    evRow.className = "row";
    Object.keys(evs).forEach(function (ev) {
      if (ev === "proximity_enter" || ev === "proximity_leave") return;
      evRow.appendChild(button(ev, function () { simulate(f.id, ev); }));
    });
    if (evs.proximity_enter || evs.proximity_leave) {
      ["a", "b"].forEach(function (who) {
        evRow.appendChild(button("occupant " + who + " enters", function () { simulate(f.id, "proximity_enter", who); }));
        evRow.appendChild(button("occupant " + who + " leaves", function () { simulate(f.id, "proximity_leave", who); }));
      });
    }
    sim.appendChild(evRow);
    var tRow = document.createElement("div");
    tRow.className = "row";
    [0.5, 1, 5].forEach(function (dt) { tRow.appendChild(button("+" + dt + " s", function () { simAdvance(dt); })); });
    tRow.appendChild(button("Reset round", simReset));
    sim.appendChild(tRow);
    var trace = simOf().traces[f.id] || [];
    if (trace.length) {
      var log = document.createElement("div");
      log.className = "note";
      log.innerHTML = trace.slice(-6).map(function (e) {
        return esc(e.t + " s " + e.kind + ": " + e.result + (e.reason ? " (" + e.reason + ")" : "") + " → " + e.state);
      }).join("<br>");
      sim.appendChild(log);
    }
    box.appendChild(sim);
    var base = fieldset("Base map corrections (feature-owned, published only as a bundle)");
    base.appendChild(button("Paint potential ground", function () { ui.placing = "potential_ground"; setTool("brush"); }));
    base.appendChild(button("Paint sight to remove", function () { ui.placing = "remove_sight"; setTool("brush"); }));
    box.appendChild(base);
  }

  function triggerProps(box, t) {
    box.appendChild(row("Type", selectEl([["switch", "use switch"], ["shoot", "shoot target"], ["proximity", "proximity area"], ["other", "other / unknown"]], t.type,
      function (v) { edit(t.id, ["type"], v); })));
    if (t.type === "proximity" && !(t.geometry && t.geometry.type !== "point"))
      box.appendChild(row("Range (no area drawn)", valueEditor(t.range, "m", function (v) { edit(t.id, ["range"], v); })));
    var place = document.createElement("div");
    place.className = "row";
    place.appendChild(button("Place point", function () { ui.placing = "trigger"; setTool("point"); }));
    place.appendChild(button("Draw area", function () { ui.placing = "trigger"; setTool("polygon"); }));
    place.appendChild(button("Link to a feature…", function () { setTool("link"); }, "then click the feature it operates"));
    place.appendChild(button("Simulate it", function () { simTrigger(t.id); }, "the sandbox: sends its declared action to every linked feature"));
    box.appendChild(place);
    var targets = fieldset("Operates");
    (t.targets || []).forEach(function (x, i) {
      var f = F.find(mf(), x.feature);
      targets.appendChild(row((f ? f.name || f.id : x.feature + " (missing)") + " · " + x.event, button("Unlink", function () {
        var next = t.targets.slice(); next.splice(i, 1); edit(t.id, ["targets"], next);
      })));
    });
    if (!(t.targets || []).length) targets.appendChild(document.createTextNode("Not linked yet: nothing is paired by distance."));
    box.appendChild(targets);
  }

  function routeProps(box, r) {
    var owner = F.find(mf(), r.owner);
    box.appendChild(row("Kind", selectEl([["zipline", "zipline"], ["rope", "vertical rope"], ["teleporter", "teleporter"], ["drop", "drop"], ["custom", "custom"]], r.kind,
      function (v) { edit(r.id, ["kind"], v); })));
    (r.endpoints || []).forEach(function (e, i) {
      var fs = fieldset("Landing " + e.id.toUpperCase() + (e.uv ? "" : " (not placed)"));
      fs.appendChild(button("Place " + e.id.toUpperCase(), function () { ui.placing = "endpoint:" + e.id; setTool("point"); }));
      box.appendChild(fs);
    });
    box.appendChild(row("Boarding", selectEl([["endpoint_only", "at the ends only"], ["sites", "also at marked sites"]],
      r.access && typeof r.access === "object" ? "sites" : "endpoint_only",
      function (v) { edit(r.id, ["access"], v === "sites" ? { sites: [] } : "endpoint_only"); })));
    if (r.access && typeof r.access === "object") {
      box.appendChild(button("Add a boarding site", function () {
        var sites = (r.access.sites || []).slice(), id = "s" + (sites.length + 1);
        sites.push({ id: id, uv: null });
        edit(r.id, ["access", "sites"], sites);
        ui.placing = "site:" + id; setTool("point");
      }));
    }
    box.appendChild(button("Draw the route line (drawing only)", function () { ui.placing = null; setTool("line"); }));
    (r.directions || []).forEach(function (d, i) {
      var fs = fieldset(d.from.toUpperCase() + " → " + d.to.toUpperCase());
      fs.appendChild(row("Getting on", valueEditor(d.entry, "s", function (v) { edit(r.id, ["directions", i, "entry"], v); })));
      fs.appendChild(row("Travel", valueEditor(d.transit, "s", function (v) { edit(r.id, ["directions", i, "transit"], v); })));
      fs.appendChild(row("Length", valueEditor(d.length, "m", function (v) { edit(r.id, ["directions", i, "length"], v); })));
      fs.appendChild(button("Remove this direction", function () { var next = r.directions.slice(); next.splice(i, 1); edit(r.id, ["directions"], next); }));
      box.appendChild(fs);
    });
    box.appendChild(row("Closed while riding", selectEl([["unresolved", "unknown"], ["complete", "rider still arrives"], ["abort", "ride must fit"]], r.in_transit || "unresolved",
      function (v) { edit(r.id, ["in_transit"], v); })));
    if (owner && (owner.states || []).length > 1)
      box.appendChild(row("Runs only in state", selectEl([["", "always"]].concat(owner.states.map(function (s) { return [s.name, s.name]; })),
        (r.states || [])[0] || "", function (v) { edit(r.id, ["states"], v ? [v] : null); })));
  }

  function renderList() {
    var list = $("featList"), m = mf();
    list.innerHTML = "";
    var all = [].concat(m.features || [], m.triggers || [], m.routes || []);
    if (!all.length) { list.innerHTML = "<span class=\"note\">No features on this map yet. Pick a preset above.</span>"; return; }
    all.forEach(function (o) {
      var b = document.createElement("button");
      b.textContent = (o.name || o.label || o.id) + " · " + o.id;
      if (o.id === ui.sel) b.className = "sel";
      b.addEventListener("click", function () { ui.sel = o.id; ui.issueCells = null; render(); });
      list.appendChild(b);
    });
  }

  function renderIssues() {
    var m = mf(), rep = F.validate(m, FD.seeds[mapName()] || [], ((canonical.maps || {})[mapName()] || {}).specials);
    var out = [];
    if (cur().incompatible) out.push("<b class=\"issue-error\">This map's loaded annotations use another schema version: shown empty, never overwritten.</b>");
    rep.errors.forEach(function (e) { out.push("<button type=\"button\" class=\"issue-error\" data-where=\"" + esc(e.where) + "\">✖ " + esc(e.where) + ": " + esc(e.message) + "</button>"); });
    rep.warnings.forEach(function (w) { out.push("<button type=\"button\" class=\"issue-warn\" data-where=\"" + esc(w.where) + "\">⚠ " + esc(w.where) + ": " + esc(w.message) + "</button>"); });
    var placed = placements();
    placementIssues(placed).forEach(function(r) {
      var id = r.affected_features[0];
      var o = F.find(m, id), location = issueLocation(r.path), owner = location && F.find(m, location.id);
      var message = {invalid_geometry:"Draw or correct the required shape", invalid_bounds:"Choose verified vision bounds", off_map:"Move the shape inside the map",
        missing_floor:"Required cells have no measured floor", multi_floor:"Required cells have multiple floors", unresolved_height:"Required heights are unresolved",
        empty_geometry:"The shape covers no placement cells", off_ground:"Required cells are outside walkable ground"}[r.code] || r.code.replace(/_/g, " ");
      var label = (owner && owner.name || o && o.name || id) + " · " + (location && location.state ? location.state + " · " : "") +
        (location ? location.field : r.path);
      out.push('<button type="button" class="issue-warn" data-where="' + esc(r.path) + '">' + esc(label) + ': ' + esc(message) +
        (r.cells && r.cells.length ? ' (' + r.cell_count + ' cells)' : '') + '</button> <span class="muted">' + esc(r.code) + '</span>');
    });
    offGround(m).forEach(function (w) {
      out.push("<span class=\"issue-warn\" data-where=\"" + esc(w) + "\">⚠ " + esc(w) + ": landing not on walkable ground (the minimap may omit it: restore ground or move the landing; nothing is snapped)</span>");
    });
    if (ui.message) out.push("<b>" + esc(ui.message) + "</b>");
    $("featIssues").innerHTML = (out.length ? out.join("<br>") : "No errors or open questions on this map.") +
      "<br><span class=\"muted\">" + rep.errors.length + " errors, " + rep.warnings.length + " unresolved or warnings</span>";
    $("featExport").disabled = false;
  }

  // Validator and placement paths have different prefixes; resolve both to the actual authored object.
  function issueLocation(path) {
    var match = /^(?:(?:features|triggers|routes)\.)?((?:feature|trigger|route)-\d+)(?:\.|:|$)(.*)$/.exec(path || "");
    if (!match) return null;
    var rest = match[2], obj = F.find(mf(), match[1]);
    var state = (obj && obj.states || []).slice().sort(function (a,b) { return b.name.length - a.name.length; }).find(function (s) {
      return rest === "states." + s.name || rest.indexOf("states." + s.name + ".") === 0;
    });
    return {id:match[1], state:state ? state.name : null, field:state ? rest.slice(8 + state.name.length) : rest};
  }
  function focusIssue(path) {
    var location = issueLocation(path), obj = location && F.find(mf(), location.id);
    if (!obj) return false;
    ui.sel = obj.id; ui.draft = null; ui.placing = null; setTool("select");
    ui.issueCells = [];
    var placement = placements();
    Object.keys(placement).forEach(function (id) { placement[id].reasons.forEach(function (r) {
      if (r.path === path) ui.issueCells = ui.issueCells.concat(r.cells || []);
    }); });
    ui.issueCells = Array.from(new Set(ui.issueCells));
    var si = (obj.states || []).findIndex(function (s) { return s.name === location.state; });
    if (si >= 0) ui.editState[obj.id] = location.state;
    render();
    var field = location.field.replace(/\[(\d+)\]/g, ".$1").split(".");
    var targetId = si >= 0 ? "feat-field-" + obj.id + "-states-" + si + "-" + field.join("-") : null;
    var targetEl = targetId && document.getElementById(targetId);
    if (!targetEl && si >= 0) targetEl = document.getElementById("feat-state-" + obj.id + "-" + si);
    if (!targetEl) targetEl = $("featProps");
    targetEl.scrollIntoView({block:"nearest"}); targetEl.focus();
    return true;
  }

  function placementIssues(placed) {
    var grouped = {};
    Object.keys(placed).sort().forEach(function (id) { placed[id].reasons.forEach(function (reason) {
      var key = reason.code + ":" + reason.path;
      var group = grouped[key] || (grouped[key] = {code:reason.code,path:reason.path,cells:[],affected_features:[]});
      group.cells = group.cells.concat(reason.cells || []);
      if (group.affected_features.indexOf(id) < 0) group.affected_features.push(id);
    }); });
    return Object.keys(grouped).sort().map(function (key) {
      var group = grouped[key]; group.cells = Array.from(new Set(group.cells)).sort(function(a,b) {return a-b;});
      group.cell_count = group.cells.length; return group;
    });
  }

  function renderChecklist() {
    var seeds = FD.seeds[mapName()] || [], m = mf(), box = $("featChecklist");
    box.innerHTML = "";
    var sum = F.mapSummary(m, seeds), s = document.createElement("div"), placement = placements();
    var diagnostic = (FD.diagnostics || {})[mapName()], compilerNote = "Not attached. Use diagnose_map_features.py and rebuild this page with --diagnostics";
    if (diagnostic) {
      var currentEntry = (workingCatalogue().maps || {})[mapName()];
      compilerNote = F.digest(currentEntry) === F.digest(diagnostic.editor_entry) ?
        diagnostic.counts.pending + " of " + diagnostic.counts.total_tagged + " features pending in the exact attached " + diagnostic.height + " context; site active height unverified" :
        "STALE after annotation edits. Export and regenerate the diagnostic report";
    }
    s.className = "note";
    s.innerHTML = "<b>Your annotation:</b> " + sum.annotation.features + " features (" + sum.annotation.review.user_reviewed + " reviewed); checklist " +
      sum.annotation.checklist.user_reviewed + "/" + seeds.length + " reviewed.<br><b>Geometry:</b> " +
      (sum.geometry.ready ? "structurally described" : sum.geometry.errors + " errors, " + sum.geometry.unresolved + " open facts") +
      ".<br><b>Placement preview:</b> " + Object.keys(placement).filter(function (id) { return !placement[id].ok; }).length + " features pending; " +
      (((FD.floors || {})[mapName()] || {}).flat !== false ? "flat fallback, no measured height asset" : "measured height asset " + esc(FD.floors[mapName()].height_sha)) +
      ".<br><b>Compiler report:</b> " + esc(compilerNote) +
      ".<br><b>Replay signal:</b> " + esc(sum.replay.note) + ". Runtime activation requires reviewed replay evidence and a registered consumer.";
    box.appendChild(s);
    if (!seeds.length) {
      box.appendChild(document.createTextNode(FD.no_features_note || "No features reported."));
      return;
    }
    seeds.forEach(function (c) {
      var entry = (m.checklist || {})[c.key] || {}, n = (m.features || []).filter(function (f) { return f.category === c.key; }).length;
      var fs = fieldset(c.label + " · " + n + " annotated" + (c.expected ? " (you said " + c.expected + ")" : ""));
      fs.appendChild(row("Review", selectEl([["not_started", "not started"], ["in_progress", "in progress"], ["user_reviewed", "user reviewed"]],
        entry.status || "not_started", function (v) { setCheck(c.key, "status", v); })));
      fs.appendChild(row("No such feature", checkEl(entry.none, function (v) { setCheck(c.key, "none", v); })));
      fs.appendChild(row("Notes", textEl(entry.notes, function (v) { setCheck(c.key, "notes", v); })));
      box.appendChild(fs);
    });
  }
  function setCheck(key, field, value) {
    var next = clone(mf());
    next.checklist = next.checklist || {};
    next.checklist[key] = Object.assign({ status: "not_started", none: false, notes: "" }, next.checklist[key] || {});
    next.checklist[key][field] = value;
    commit(next, true);
  }

  function render() {
    var on = TG.state.mode === "features";
    $("featCard").hidden = $("featCheck").hidden = !on;
    stage.classList.toggle("features", on);
    if (!cur()) return;
    renderList(); renderProps(); renderIssues(); renderChecklist(); draw();
  }

  // ---------------------------------------------------------------- import / export
  function exportAll(maps, edits) {
    var bad = [];
    Object.keys(fe).forEach(function (n) {
      if (!fe[n].dirty) return;
      var rep = F.validate(fe[n].mf, FD.seeds[n] || [], ((canonical.maps || {})[n] || {}).specials);
      if (rep.errors.length) bad.push(n + " (" + rep.errors.length + ")");
    });
    if (bad.length) {
      ui.message = "Export refused: structural errors in " + bad.join(", ") + ". Fix them (listed above), or use Download draft.";
      if (TG.state.mode !== "features") TG.setMode("features");
      render();
      return null;
    }
    ui.message = null;
    return F.exportCatalogue(canonical, maps, edits, fe);
  }

  function download(name, text) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    a.download = name; a.click();
  }

  // The page's actual working catalogue: the canonical one with the legacy edits (exportTags) and each edited
  // map's current model in place. Imports are compared against this, so the review names every unsaved edit
  // a replace would lose; it adds no image checksum or runtime digest (those are export claims).
  function workingCatalogue() {
    Object.keys(TG.edits).forEach(TG.ensureMap);
    var out = T.exportTags(canonical, TG.maps, TG.edits);
    out.maps = out.maps || {};
    Object.keys(fe).forEach(function (n) {
      if (!fe[n].dirty || fe[n].incompatible) return;
      out.maps[n] = Object.assign({}, out.maps[n] || {}, { map_features: clone(fe[n].mf) });
    });
    return out;
  }

  var pendingImport = null;
  // A tags.json goes through importCatalogue; a downloaded draft (or an older draft's snapshot) through
  // importDraft. Both are transactional and reviewed here first; `provenance` is an older draft's source.
  function importText(text, filename, provenance) {
    var working = workingCatalogue();
    var r = F.importDraft(sourceOf(), text, working, provenance);
    var draft = !!r;
    if (!draft) r = F.importCatalogue(working, text, FD.seeds || {});
    pendingImport = { result: r, text: text, filename: filename || "imported.json", draft: draft };
    var html = [];
    html.push("<h2>" + (draft ? "Restore draft " : "Import ") + esc(filename || "") + "</h2>");
    if (!r.ok) html.push("<p class=\"issue-error\">Not " + (draft ? "restored" : "imported") + ": your current draft is unchanged.</p>");
    r.errors.forEach(function (e) { html.push("<div class=\"issue-error\">✖ " + esc((e.map || "file") + " · " + e.where + ": " + e.message) + "</div>"); });
    if (draft && r.differs.length) html.push("<p class=\"note\">Saved for a different page: " + esc(r.differs.join(", ")) + ". Check it before restoring.</p>");
    var unsaved = Object.keys(fe).filter(function (n) { return fe[n].dirty; });
    if (r.affected.length) {
      html.push("<p>What it changes" + (draft ? " (restoring replaces the whole draft)" : "") + ":</p><ul>" + r.affected.map(function (a) {
        var lost = a.change !== "added" && unsaved.indexOf(a.map) >= 0 ? " (replaces your unsaved edits to it)" : "";
        return "<li>" + esc(a.map + ": " + a.change + lost) + "</li>";
      }).join("") + "</ul>");
    } else if (r.ok) html.push("<p>It matches your current work.</p>");
    if (r.warnings.length) html.push("<p class=\"note\">" + r.warnings.length + (draft ? " structural errors, unresolved facts or warnings (an incomplete draft restores; fix them before export)." : " unresolved facts or warnings (fine to import).") + "</p>");
    $("featImportBody").innerHTML = html.join("");
    $("featImportReplace").textContent = draft ? "Restore draft" : "Replace";
    $("featImportReplace").disabled = !r.ok;
    var dlg = $("featImport");
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
  }
  function closeImport() { var dlg = $("featImport"); if (dlg.close) dlg.close(); else dlg.removeAttribute("open"); pendingImport = null; }
  // Every map starts from the new catalogue.
  function applyCatalogue(cat) {
    canonical = cat;
    initMaps();
    rebuildLegacy(true);
    Object.keys(TG.edits).forEach(function (n) { TG.edits[n].touched = false; });
    TG.save();
    autosave();
    ui.sel = null;
    TG.selectMap(mapName());
    render();
  }
  function restoreDraft(body) {
    canonical = body.canonical;
    initMaps();
    var skipped = restoreModels(body);
    var repaired = repairLegacySightLines();
    rebuildLegacy(false);
    TG.save();
    autosave();
    ui.sel = null;
    ui.message = skipped.length ? "Not restored (not on this page, or its annotations use another schema): " + skipped.join(", ") + "." : null;
    if (repaired) ui.message = (ui.message ? ui.message + " " : "") + "Repaired " + repaired + " older sight edge(s); coordinates and height facts kept.";
    TG.selectMap(mapName());
    render();
  }
  $("featImportReplace").addEventListener("click", function () {
    if (!pendingImport || !pendingImport.result.ok) return;            // a refused file applies nothing
    var p = pendingImport;
    closeImport();
    if (p.draft) restoreDraft(p.result.body); else applyCatalogue(p.result.catalogue);
  });
  $("featImportCopy").addEventListener("click", function () { download("copy-of-" + pendingImport.filename, pendingImport.text); });
  $("featImportCancel").addEventListener("click", closeImport);

  $("featExport").addEventListener("click", function () {
    Object.keys(TG.edits).forEach(TG.ensureMap);
    var out = exportAll(TG.maps, TG.edits);
    if (out) download("tags.json", JSON.stringify(out, null, 1) + "\n");
  });
  // A restorable copy of the draft (Import reads it back): what it was saved for, its checksum, the catalogue,
  // the edited models (valid or not) and the id high-water marks.
  $("featDownload").addEventListener("click", function () {
    download("tagger-draft.json", JSON.stringify(F.draftFile(sourceOf(), draftBody()), null, 1) + "\n");
  });
  $("featDrafts").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-other]") : null;
    if (!b) return;
    var snap = drafts.readOther(b.getAttribute("data-other"));
    if (!snap) return;
    if (b.getAttribute("data-act") === "download") {
      download("older-draft.json", JSON.stringify(F.draftFile(snap.provenance, snap.body), null, 1) + "\n");
      return;
    }
    // an older draft goes through the same review as a downloaded one: nothing is applied without seeing it
    importText(JSON.stringify({ canonical: snap.body.canonical || canonical, features: snap.body.features || {},
                                high: snap.body.high || null }), "older draft", snap.provenance);
  });

  // ---------------------------------------------------------------- wiring
  Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (b) {
    b.addEventListener("click", function () { createPreset(b.getAttribute("data-preset")); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-ftool]"), function (b) {
    b.addEventListener("click", function () { setTool(b.getAttribute("data-ftool")); });
  });
  $("featComposed").addEventListener("change", function () { ui.showComposed = this.checked; draw(); });
  window.addEventListener("resize", function () { draw(); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-layer]"), function (b) { b.addEventListener("change", draw); });
  $("featUndo").addEventListener("click", doUndo);
  $("featRedo").addEventListener("click", doRedo);
  $("zoomIn").addEventListener("click", function () { zoomAt(1.25, stage.clientWidth / 2, stage.clientHeight / 2); });
  $("zoomOut").addEventListener("click", function () { zoomAt(0.8, stage.clientWidth / 2, stage.clientHeight / 2); });
  $("zoomReset").addEventListener("click", function () { ui.zoom = 1; ui.pan = [0, 0]; applyZoom(); });
  $("featIssues").addEventListener("click", function (e) {
    var w = e.target.getAttribute && e.target.getAttribute("data-where");
    if (!w) return;
    focusIssue(w);
  });

  initMaps();
  loadDraft();
  var repairedEdges = repairLegacySightLines();
  if (repairedEdges) {
    ui.message = "Repaired " + repairedEdges + " older sight edge(s); coordinates and height facts kept.";
    autosave();
  }

  function copyFootprint(id, source, destination, includeBounds) {
    var f = F.find(mf(), id);
    if (!f || !Array.isArray(f.states)) return false;
    var src = f.states.find(function (s) { return s.name === source; }), di = f.states.findIndex(function (s) { return s.name === destination; });
    if (!src || di < 0 || source === destination || !src.footprint || F.geometryProblems(src.footprint).length) return false;
    if (includeBounds && !src.sight_bounds) return false;
    var next = F.setField(mf(), id, ["states", di, "footprint"], clone(src.footprint));
    if (includeBounds) next = F.setField(next, id, ["states", di, "sight_bounds"], clone(src.sight_bounds));
    commit(next, true);
    return true;
  }
  TG.recompute();
  setTool("select");
  window.taggerFeatures = {
    onMode: function () { ui.draft = null; ui.issueCells = null; render(); },
    onMap: function () { ui.sel = null; ui.draft = null; ui.message = null; ui.issueCells = null; ui.slideClosure = {}; render(); },
    simulate: simulate, simTrigger: simTrigger, simAdvance: simAdvance, simReset: simReset,
    exportAll: exportAll, importText: importText, ui: ui, fe: fe, canonical: function () { return canonical; },
    commit: commit, setTool: setTool, createPreset: createPreset, placePoint: placePoint, finishDraft: finishDraft,
    linkTo: linkTo, undo: doUndo, redo: doRedo, zoomAt: zoomAt, render: render, autosave: autosave, drafts: drafts,
    focusIssue: focusIssue, copyFootprint: copyFootprint, placementIssues: placementIssues,
    enableSliding: enableSliding, slidingPreview: slidingPreview
  };
  render();
})();

/* Offline utility gallery: data, edits and isolated replay drawing. */
(function (global) {
  "use strict";

  var PREFIX = "util-gallery:v1:", MAP_UV = 10000, VIEWER_PX = 1024;
  var ZONE_PATTERN = "^Grenadier_Q_SemtexBasic_Zone$";
  var FIELDS = ["color", "r", "badge", "look"], OWN_FIELDS = ["r", "badge", "look"];
  var CRITIQUE_INSTRUCTIONS = "Apply each after to the entry with that pattern in replay.js's " +
    "ABILITY_STYLES / PROJECTILE_STYLES, and apply colours to UTIL_ABILITY_COLORS. " +
    "Add a PROJECTILE_STYLES entry for an unstyled key when its notes ask for one. " +
    "Build a new UTIL_LOOKS function where a note asks for a new picture. Follow the repo's test rules.";

  function own(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
  function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function copy(value) {
    if (Array.isArray(value)) return value.map(copy);
    if (object(value)) return Object.fromEntries(Object.keys(value).map(function (k) { return [k, copy(value[k])]; }));
    return value;
  }
  function equal(a, b) {
    if (a === b) return true;
    if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
    var keys = Object.keys(a);
    return Array.isArray(a) === Array.isArray(b) && keys.length === Object.keys(b).length &&
      keys.every(function (k) { return own(b, k) && equal(a[k], b[k]); });
  }
  function keysOf(samples) { return (samples && samples.keys) || samples || {}; }
  function samplesOf(value) { return Array.isArray(value) ? value : (value && value.samples) || []; }
  function firstMatch(table, key) {
    return table.findIndex(function (pair) { return pair[0].test(key); });
  }

  function entrySamples(R, entry, samples) {
    var groups = keysOf(samples), out = [];
    if (entry.list === "unstyled") return samplesOf(groups[entry.key]).slice();
    Object.keys(groups).forEach(function (key) {
      var split = key.indexOf("|"), kind = key.slice(0, split), name = key.slice(split + 1);
      if (entry.list === "ability" && entry.pattern === ZONE_PATTERN &&
          key === "Projectile|Grenadier_Q_SemtexBasic") {
        out = out.concat(samplesOf(groups[key]).filter(function (s) {
          return s.row && s.row.flight && s.row.flight.length;
        }));
      } else if ((entry.list === "projectile" ? kind === "Projectile" : kind !== "Projectile" && kind !== "Bomb") &&
          firstMatch(entry.list === "projectile" ? R.PROJECTILE_STYLES : R.ABILITY_STYLES, name) === entry.index) {
        out = out.concat(samplesOf(groups[key]));
      }
    });
    return out;
  }

  function galleryEntries(R, samples, abilitiesJson) {
    var out = [], agents = {};
    Object.keys(abilitiesJson || {}).forEach(function (agent) {
      Object.keys(abilitiesJson[agent]).forEach(function (ability) {
        if (!own(agents, ability)) agents[ability] = agent;
      });
    });
    function add(list, table) {
      table.forEach(function (pair, index) {
        var style = pair[1];
        if (list === "ability" && style.shape === "hidden") return;
        var entry = {id: list + ":" + pair[0].source, list: list, index: index,
          pattern: pair[0].source, style: style, ability: style.ability,
          agent: "Other", shape: list === "projectile" ? "projectile" : style.shape};
        var found = entrySamples(R, entry, samples).find(function (s) { return s.row && s.row.agent; });
        entry.agent = found ? found.row.agent : agents[entry.ability] || "Other";
        out.push(entry);
      });
    }
    add("ability", R.ABILITY_STYLES);
    add("projectile", R.PROJECTILE_STYLES);
    var groups = keysOf(samples);
    Object.keys(groups).forEach(function (key) {
      if (key.indexOf("Projectile|") !== 0 || firstMatch(R.PROJECTILE_STYLES, key.slice(11)) !== -1) return;
      var s = samplesOf(groups[key])[0], row = s && s.row;
      var ability = row && typeof row.name === "string" && row.name ? row.name.replace(/_/g, " ") : key.slice(11);
      out.push({id: "unstyled:" + key, list: "unstyled", index: -1, key: key, style: null,
        ability: ability, agent: (row && row.agent) || agents[ability] || "Other", shape: "projectile"});
    });
    return out;
  }

  function sampleRound(R, sample, sideOverride) {
    var row = sample.row, end = sample.t_end, slot = row.by;
    var side = sideOverride === "A" || sideOverride === "B" ? sideOverride : sample.side === "B" ? "B" : "A";
    var extras = R.extrasFromUtil([row].concat(sample.companions || [])), all = extras.abilities;
    var allTraces = R.traceWalls(all), zones = R.fragmentZones(all), focus = all[0];
    extras.abilities = [focus].concat(zones.filter(function (z) { return z.zoneOf === 0; }));
    var traces = {};
    if (own(allTraces, 0)) traces[0] = allTraces[0];
    var tracks = {}, alive = {};
    tracks[slot] = [[{t: 0, u: row.u, v: row.v, yaw: row.yaw || 0},
                     {t: end, u: row.u, v: row.v, yaw: row.yaw || 0}]];
    alive[slot] = [[0, end]];
    return {blob: {t_end: end, util: [], players: [{slot: slot, side: side}], alive: alive},
      tracks: tracks, extras: extras, traces: traces};
  }

  function focusRow(R, round, entry) {
    var rows = round.extras.abilities;
    return entry.list === "ability" && entry.pattern === ZONE_PATTERN ?
      rows.find(function (a) { return a.code + "_" + a.name === "Grenadier_Q_SemtexBasic_Zone"; }) : rows[0];
  }

  function representativeKey(R, entry) {
    if (entry.list !== "ability") return null;
    var re = R.ABILITY_STYLES[entry.index][0], key = re.source.replace(/^\^|\$$/g, "");
    // Current tables use simple optional suffixes and alternations. Do not invent
    // values for other regex syntax: the final test and first-match check gate it.
    var previous;
    do {
      previous = key;
      key = key.replace(/\([^()]*\)\?/g, "").replace(/\(([^()|]*)\|[^()]*\)/g, "$1");
    } while (key !== previous);
    return re.test(key) && firstMatch(R.ABILITY_STYLES, key) === entry.index ? key : null;
  }

  function madeUpSample(R, entry) {
    if (entry.list !== "ability" || ["smoke", "area", "badge", "reveal"].indexOf(entry.shape) === -1) return null;
    var key = representativeKey(R, entry);
    if (key === null || key.indexOf("_") === -1) return null;
    var split = key.indexOf("_");
    return {map: null, t_end: 40, side: "A", companions: [], row: {
      k: "ability", by: 0, t: 5, t1: 25, kind: "Zone", agent: entry.agent,
      code: key.slice(0, split), name: key.slice(split + 1), u: R.UV / 2, v: R.UV / 2
    }};
  }

  function galleryPhases(R, row, style, tEnd) {
    var t0 = Number.isFinite(row.t0) ? row.t0 : 0, phases = [], close = R.closeOf(row);
    if (row.kind === "Projectile") {
      var points = row.flight && row.flight.length ? row.flight : row.path;
      phases.push({name: "in flight", t: points && points.length ? points[Math.floor(points.length / 2)][0] : t0});
    } else if (style.shape === "box" && Number.isFinite(style.sweep)) {
      phases.push({name: "windup", t: t0 + 0.5 * style.sweep}, {name: "active", t: t0 + style.sweep + 0.5});
    } else if (Number.isFinite(style.pop)) {
      var pops = R.popTimes(row).filter(Number.isFinite);
      if (pops.length) {
        if (pops[0] > t0) phases.push({name: "windup", t: t0 + 0.5 * (pops[0] - t0)});
        phases.push({name: "blast", t: pops[0] + 0.25 * style.pop});
        if (pops.length >= 2) phases.push({name: "between", t: (pops[0] + pops[1]) / 2});
      }
    }
    if (!phases.length) phases.push({name: "active", t: t0 + (Number.isFinite(close) ? Math.min(2, (close - t0) / 2) : 2)});
    // Timed pulses outlive their object close; untimed/recorded pops can end
    // before close. Follow both limits so every offered phase remains drawable.
    var ends = [tEnd, row.gone];
    if (Number.isFinite(style.pop)) {
      ends.push(R.popUntil(row, style.pop));
      if (!R.popTimesTimed(row)) ends.push(close);
    } else ends.push(close);
    var finiteEnds = ends.filter(Number.isFinite), end = finiteEnds.length ? Math.min.apply(null, finiteEnds) : Infinity;
    var hi = Math.max(t0, end - 0.05), out = [];
    phases.forEach(function (phase) {
      if (!Number.isFinite(phase.t)) return;
      var t = Math.max(t0, Math.min(hi, phase.t));
      if (!out.some(function (p) { return p.t === t; })) out.push({name: phase.name, t: t});
    });
    return out.length ? out : [{name: "active", t: t0}];
  }

  function frameOf(R, row, style, traces, uvPerUnit) {
    var points = [], unit = Number.isFinite(uvPerUnit) ? uvPerUnit : 1;
    function point(x, y) { if (Number.isFinite(x) && Number.isFinite(y)) points.push([x, y]); }
    function geometry(value, timed) {
      if (!value) return;
      if (Array.isArray(value)) {
        if (typeof value[0] === "number") point(value[timed ? 1 : 0], value[timed ? 2 : 1]);
        else value.forEach(function (v) { geometry(v, timed); });
      } else if (object(value)) {
        if (own(value, "u")) point(value.u, value.v);
        else Object.keys(value).forEach(function (k) { geometry(value[k], timed); });
      }
    }
    point(row.u, row.v);
    ["end", "points", "arms", "segments", "line"].forEach(function (key) { geometry(row[key], false); });
    ["path", "flight"].forEach(function (key) { geometry(row[key], true); });
    geometry(traces, true);
    if (style.shape === "line") geometry(R.lineEnds(row, style, unit), false);
    if (style.capsule) geometry(R.capsuleEnds(row, style, unit), false);
    if (style.shape === "box" || style.shape === "lanes") geometry(R.boxCorners(row, style, unit), false);
    if (Number.isFinite(style.coneLen)) {
      var facings = row.yaws && row.yaws.length ? row.yaws.map(function (p) { return p[1]; }) : [row.yaw];
      facings.forEach(function (yaw) {
        if (!Number.isFinite(yaw)) return;
        var angle = yaw * Math.PI / 180;
        point(row.u + Math.cos(angle) * style.coneLen * unit, row.v + Math.sin(angle) * style.coneLen * unit);
      });
    }
    if (!points.length) point(R.UV / 2, R.UV / 2);
    var xs = points.map(function (p) { return p[0]; }), ys = points.map(function (p) { return p[1]; });
    function clip(x) { return Math.max(0, Math.min(R.UV, x)); }
    var x0 = clip(Math.min.apply(null, xs)), x1 = clip(Math.max.apply(null, xs));
    var y0 = clip(Math.min.apply(null, ys)), y1 = clip(Math.max.apply(null, ys));
    var pad = Math.max(Number.isFinite(style.r) ? style.r : 0, Number.isFinite(style.trip) ? style.trip : 0) * unit;
    return {cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, half: Math.max(600, (x1 - x0) / 2 + pad, (y1 - y0) / 2 + pad)};
  }

  // k is the viewer's dimensionless map zoom; ox/oy are unzoomed canvas pixels.
  function viewOf(frame, canvasPx) {
    var span = Math.min(MAP_UV, 2 * frame.half / 0.6), k = MAP_UV / span;
    return {k: k, ox: Math.max(0, Math.min(MAP_UV - span, frame.cx - span / 2)) / MAP_UV * canvasPx,
      oy: Math.max(0, Math.min(MAP_UV - span, frame.cy - span / 2)) / MAP_UV * canvasPx};
  }

  function snapshotStyle(R, entry) {
    var style = entry.style || {}, snapshot = {color: R.UTIL_ABILITY_COLORS[entry.ability] || R.UTIL_AGENT_COLORS[entry.agent],
      r: copy(style.r), badge: copy(style.badge), look: copy(style.look)};
    // Presence is page-local reset bookkeeping, excluded from storage and export.
    Object.defineProperty(snapshot, "presence", {value: {
      color: own(R.UTIL_ABILITY_COLORS, entry.ability),
      r: own(style, "r"), badge: own(style, "badge"), look: own(style, "look")
    }});
    return snapshot;
  }

  function effectiveBadge(entry) {
    return entry.style && entry.style.badge !== undefined ? entry.style.badge : entry.shape !== "smoke";
  }
  function applyEdit(R, entry, edit) {
    if (entry.list === "unstyled") return;
    if (own(edit, "color")) R.UTIL_ABILITY_COLORS[entry.ability] = edit.color;
    if (entry.list === "ability") OWN_FIELDS.forEach(function (key) {
      if (own(edit, key)) entry.style[key] = copy(edit[key]);
    });
  }
  function resetEntry(R, entry, original) {
    if (entry.list === "unstyled") return;
    if (original.presence ? original.presence.color : original.color !== undefined)
      R.UTIL_ABILITY_COLORS[entry.ability] = original.color;
    else delete R.UTIL_ABILITY_COLORS[entry.ability];
    if (entry.list === "ability") OWN_FIELDS.forEach(function (key) {
      if (original.presence ? original.presence[key] : original[key] !== undefined) entry.style[key] = copy(original[key]);
      else delete entry.style[key];
    });
  }
  function parseSettings(text) {
    try {
      var value = JSON.parse(text);
      return object(value) ? {ok: true, value: value} : {ok: false, error: "Settings must be a JSON object"};
    } catch (error) { return {ok: false, error: error.message}; }
  }
  function lookFrom(kind, settings) {
    return Object.keys(settings).length ? Object.assign({}, copy(settings), {kind: kind}) : kind;
  }

  function entryKey(entry) { return PREFIX + entry.list + ":" + (entry.list === "unstyled" ? entry.key : entry.pattern); }
  function colorKey(ability) { return PREFIX + "color:" + ability; }
  function readEntry(storage, entry) {
    try {
      var value = JSON.parse(storage.getItem(entryKey(entry)));
      return object(value) ? value : {};
    } catch (_) { return {}; }
  }
  function ownEdit(value) {
    var out = {};
    if (Number.isFinite(value.r)) out.r = value.r;
    if (typeof value.badge === "boolean") out.badge = value.badge;
    if (typeof value.look === "string" || object(value.look)) out.look = copy(value.look);
    return out;
  }
  function loadEdits(storage, entries) {
    var result = {edits: {}, notes: {}, colors: {}};
    entries.forEach(function (entry) {
      var value = readEntry(storage, entry), edit = ownEdit(value);
      if (entry.list === "ability" && Object.keys(edit).length) result.edits[entry.id] = edit;
      if (typeof value.notes === "string" && value.notes) result.notes[entry.id] = value.notes;
      if (entry.list !== "unstyled") try {
        var color = storage.getItem(colorKey(entry.ability));
        if (/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(color)) result.colors[entry.ability] = color;
      } catch (_) { /* Storage can be unavailable even when the page is open. */ }
    });
    return result;
  }
  function saveEdit(storage, entry, edit) {
    if (entry.list !== "ability") return;
    try {
      var value = readEntry(storage, entry), next = ownEdit(edit);
      if (typeof value.notes === "string") next.notes = value.notes;
      storage.setItem(entryKey(entry), JSON.stringify(next));
    } catch (_) { /* Best effort persistence. */ }
  }
  function saveNote(storage, entry, note) {
    try {
      var next = ownEdit(readEntry(storage, entry));
      next.notes = note;
      storage.setItem(entryKey(entry), JSON.stringify(next));
    } catch (_) { /* Best effort persistence. */ }
  }
  function saveColor(storage, ability, color) { try { storage.setItem(colorKey(ability), color); } catch (_) {} }
  function clearColor(storage, ability) { try { storage.removeItem(colorKey(ability)); } catch (_) {} }
  function clearAll(storage) {
    try {
      for (var i = storage.length - 1; i >= 0; i--) {
        var key = storage.key(i);
        if (typeof key === "string" && key.indexOf(PREFIX) === 0) storage.removeItem(key);
      }
    } catch (_) { /* Best effort persistence. */ }
  }

  function exportCritique(R, entries, originals, current, notes, meta) {
    var changes = [];
    entries.forEach(function (entry) {
      var before = originals[entry.id] || {}, now = current[entry.id] || snapshotStyle(R, entry), after = {};
      FIELDS.forEach(function (key) { if (!equal(before[key], now[key])) after[key] = copy(now[key]); });
      var note = notes[entry.id] || "";
      if (!Object.keys(after).length && !note) return;
      changes.push({list: entry.list, pattern: entry.list === "unstyled" ? entry.key : entry.pattern,
        ability: entry.ability, agent: entry.agent, shape: entry.shape, before: copy(before), after: after, notes: note});
    });
    return {made: meta.made || new Date().toISOString(), replay_js_sha256: meta.replay_js_sha256,
      palette: meta.palette, entries: changes};
  }
  function critiquePrompt(json) { return CRITIQUE_INSTRUCTIONS + "\n\n" + JSON.stringify(json, null, 1); }

  function setTileSample(R, viewer, sample, side) {
    viewer.tile.sample = sample;
    viewer.current = sample ? sampleRound(R, sample, side) : null;
    var url = sample && viewer.payload.maps && viewer.payload.maps[sample.map];
    if (url === viewer.mapUrl) return;
    viewer.mapUrl = url;
    viewer.map = null;
    if (url) {
      viewer.map = new Image();
      viewer.map.onload = viewer.draw;
      viewer.map.src = url;
    }
  }

  function tileViewer(R, tile, payload) {
    var viewer = Object.create(R.ReplayViewer.prototype);
    Object.assign(viewer, {root: tile.root || {}, t: 0, controlPalette: "green-red", turns: 0,
      linked: null, areas: null, highlight: null, icons: {}, tile: tile, payload: payload,
      layers: {abilities: true, projectiles: true},
      options: {abilityIcons: payload.abilityIcons || {},
        assetUrl: function (url) { return (payload.assets || {})[url] || url; }},
      nameOf: function () { return "owner"; },
      abilityText: function (row, style) { return style.label || style.ability || row.name; }});
    // Image.onload must redraw only this tile, without the full viewer's ctx/canvas/UI.
    viewer.draw = function () {
      (tile.canvases || []).forEach(function (item) { drawTile(R, viewer, item.canvas, item.phase); });
    };
    setTileSample(R, viewer, tile.sample);
    return viewer;
  }

  function drawTile(R, viewer, canvas, phase) {
    // Draw in the viewer's 1024 px logical units (its strokes are fixed pixels), onto a smaller backing canvas.
    var ctx = canvas.getContext("2d"), size = VIEWER_PX, backing = canvas.width, round = viewer.current;
    var row = round && focusRow(R, round, viewer.tile.entry);
    var style = row && (row.kind === "Projectile" ? R.projectileStyle(row) : R.abilityStyle(row));
    var view = viewOf(row ? frameOf(R, row, style, round.traces,
      (viewer.linked && viewer.linked.uvPerUnit) || 0.75) : {cx: R.UV / 2, cy: R.UV / 2, half: R.UV / 2}, size);
    viewer.canvas = {width: VIEWER_PX, height: VIEWER_PX};
    viewer.view = view;
    viewer.t = phase.t;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, backing, backing);
    ctx.setTransform.apply(ctx, R.viewMatrix(view, 0, size).map(function (v) { return v * backing / VIEWER_PX; }));
    if (viewer.map && viewer.map.complete && viewer.map.naturalWidth) ctx.drawImage(viewer.map, 0, 0, size, size);
    else { ctx.fillStyle = "#1b1d24"; ctx.fillRect(0, 0, size, size); }
    if (row) {
      var s = size / R.UV, r = size / 48 / Math.sqrt(view.k);
      if (viewer.layers.abilities) {
        viewer.drawAbilities(ctx, s, r, []);
        if (style.carried) viewer.drawCarried(ctx, s, r, []);
      }
      if (viewer.layers.projectiles && viewer.tile.entry.list !== "ability") viewer.drawProjectiles(ctx, s, r, []);
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  function mount(R, document, payload, storage) {
    var root = document.querySelector("[data-gallery]");
    var entries = galleryEntries(R, payload.samples || {}, payload.abilityIcons || {});
    var originals = {}, tiles = [], sections = {}, notes, header = {};
    ["palette", "side", "agent", "filter", "edited", "export", "copy", "clear", "status"].forEach(function (key) {
      header[key] = document.querySelector('[data-header="' + key + '"]');
    });
    function node(tag, text, parent) {
      var element = document.createElement(tag);
      if (text !== undefined) element.textContent = text;
      if (parent) parent.appendChild(element);
      return element;
    }
    function option(select, value, label) {
      var element = node("option", label === undefined ? value : label, select);
      element.value = value;
    }
    function button(parent, text, action) {
      var element = node("button", text, parent);
      element.type = "button";
      element.addEventListener("click", action);
      return element;
    }
    function changed(entry) {
      var now = snapshotStyle(R, entry), before = originals[entry.id];
      return FIELDS.some(function (key) { return !equal(now[key], before[key]); });
    }
    function filter() {
      var text = header.filter.value.trim().toLowerCase();
      tiles.forEach(function (tile) {
        var entry = tile.entry;
        tile.root.hidden = !!((header.agent.value && header.agent.value !== entry.agent) ||
          (text && [entry.ability, entry.agent, entry.shape, entry.pattern, entry.style && entry.style.label]
            .join(" ").toLowerCase().indexOf(text) < 0) ||
          (header.edited.checked && !changed(entry) && !notes[entry.id]));
      });
      Object.keys(sections).forEach(function (agent) {
        sections[agent].root.hidden = !tiles.some(function (tile) { return tile.entry.agent === agent && !tile.root.hidden; });
      });
    }
    function redrawAbility(ability) {
      tiles.forEach(function (tile) {
        if (tile.entry.ability !== ability) return;
        if (tile.controls.color) tile.controls.color.value = snapshotStyle(R, tile.entry).color || "#77787f";
        tile.viewer.draw();
      });
      filter();
    }
    function persist(tile, edit) {
      applyEdit(R, tile.entry, edit);
      // saveEdit replaces the stored own fields, so always send the complete diff.
      var now = snapshotStyle(R, tile.entry), before = originals[tile.entry.id], complete = {};
      OWN_FIELDS.forEach(function (key) { if (!equal(now[key], before[key])) complete[key] = copy(now[key]); });
      saveEdit(storage, tile.entry, complete);
      tile.viewer.draw();
      filter();
    }
    function syncControls(tile) {
      var entry = tile.entry, controls = tile.controls, snapshot = snapshotStyle(R, entry);
      if (controls.color) controls.color.value = snapshot.color || "#77787f";
      if (controls.r) controls.r.value = entry.style.r;
      if (controls.badge) controls.badge.checked = effectiveBadge(entry);
      if (controls.look) {
        var look = R.utilLookOf(entry.style);
        controls.look.value = look.kind;
        controls.settings.value = JSON.stringify(look.settings, null, 2);
      }
      controls.notes.value = notes[entry.id] || "";
      tile.error.textContent = "";
    }
    function phases(tile) {
      tile.phases.replaceChildren();
      tile.canvases = [];
      var round = tile.viewer.current, row = round && focusRow(R, round, tile.entry);
      tile.sampleBadge.textContent = !row ? "no sample" : tile.madeUp ? "made up" : "";
      tile.sampleBadge.hidden = !!row && !tile.madeUp;
      tile.counter.textContent = tile.samples.length ? (tile.sampleIndex + 1) + " of " + tile.samples.length : "";
      if (!row) return;
      var style = row.kind === "Projectile" ? R.projectileStyle(row) : R.abilityStyle(row);
      galleryPhases(R, row, style, tile.sample.t_end).forEach(function (phase) {
        var figure = node("figure", undefined, tile.phases);
        node("figcaption", phase.name + " · t = " + phase.t.toFixed(2) + " s", figure);
        var canvas = node("canvas", undefined, figure);
        canvas.width = canvas.height = 480;   // backing store; CSS shows it at 240 px
        canvas.setAttribute("data-phase", phase.name);
        canvas.setAttribute("aria-label", tile.entry.ability + ", " + phase.name);
        tile.canvases.push({canvas: canvas, phase: phase});
      });
      tile.viewer.draw();
    }
    function buildTile(entry, parent) {
      var tile = {entry: entry, root: node("article", undefined, parent), controls: {}, sampleIndex: 0, canvases: []};
      tile.root.setAttribute("data-entry-id", entry.id);
      node("h3", entry.ability, tile.root);
      var meta = node("p", [entry.style && entry.style.label || entry.ability, entry.agent, entry.shape].join(" · "), tile.root);
      meta.className = "gallery-meta";
      tile.sampleBadge = node("span", "", meta);
      tile.sampleBadge.className = "gallery-badge";
      if (entry.list === "unstyled") node("span", "no style entry", meta).className = "gallery-badge";
      tile.samples = entrySamples(R, entry, payload.samples || {});
      tile.sample = tile.samples[0] || madeUpSample(R, entry);
      tile.madeUp = !tile.samples.length && !!tile.sample;
      tile.counter = node("span", "", meta);
      if (tile.samples.length > 1) ["prev", "next"].forEach(function (direction) {
        var pick = button(meta, direction === "prev" ? "Previous" : "Next", function () {
          tile.sampleIndex = (tile.sampleIndex + (direction === "prev" ? -1 : 1) + tile.samples.length) % tile.samples.length;
          setTileSample(R, tile.viewer, tile.samples[tile.sampleIndex], header.side.value);
          phases(tile);
        });
        pick.setAttribute("data-sample", direction);
      });
      tile.phases = node("div", undefined, tile.root);
      tile.phases.className = "gallery-phases";
      tile.viewer = tileViewer(R, tile, payload);
      tile.viewer.controlPalette = header.palette.value;
      setTileSample(R, tile.viewer, tile.sample, header.side.value);
      var controls = node("div", undefined, tile.root);
      controls.className = "gallery-controls";
      function control(key, title, tag, type) {
        var label = node("label", title, controls), input = node(tag || "input", undefined, label);
        if (type) input.type = type;
        if (type === "checkbox") label.className = "gallery-check";
        input.setAttribute("data-control", key);
        tile.controls[key] = input;
        return input;
      }
      if (entry.list !== "unstyled") {
        var color = control("color", "Colour (applies to every " + entry.ability + " piece)", "input", "color");
        color.addEventListener("input", function () {
          applyEdit(R, entry, {color: color.value});
          saveColor(storage, entry.ability, color.value);
          redrawAbility(entry.ability);
        });
      }
      if (entry.list === "ability") {
        if (own(entry.style, "r")) {
          var radius = control("r", "Size (world units)", "input", "number");
          radius.min = "0"; radius.step = "any";
          radius.addEventListener("change", function () {
            var value = Number(radius.value);
            if (radius.value.trim() !== "" && Number.isFinite(value) && value >= 0) persist(tile, {r: value});
          });
        }
        var badge = control("badge", "Icon", "input", "checkbox");
        badge.addEventListener("change", function () { persist(tile, {badge: badge.checked}); });
        var look = control("look", "Look", "select"), settings = control("settings", "Look settings (JSON object)", "textarea");
        Object.keys(R.UTIL_LOOKS).forEach(function (kind) { option(look, kind); });
        function updateLook() {
          var result = parseSettings(settings.value);
          tile.error.textContent = result.ok ? "" : result.error;
          if (result.ok) persist(tile, {look: lookFrom(look.value, result.value)});
        }
        look.addEventListener("change", updateLook);
        settings.addEventListener("change", updateLook);
      }
      var note = control("notes", "Notes", "textarea");
      note.addEventListener("input", function () {
        notes[entry.id] = note.value;
        saveNote(storage, entry, note.value);
        filter();
      });
      var reset = button(controls, "Reset", function () {
        resetEntry(R, entry, originals[entry.id]);
        saveEdit(storage, entry, {});
        clearColor(storage, entry.ability);
        syncControls(tile);
        redrawAbility(entry.ability);
      });
      reset.setAttribute("data-control", "reset");
      tile.error = node("p", "", tile.root);
      tile.error.setAttribute("data-settings-error", "");
      tile.error.setAttribute("role", "alert");
      syncControls(tile);
      phases(tile);
      return tile;
    }

    // Snapshot every entry before applying anything: colours are shared across entries.
    entries.forEach(function (entry) { originals[entry.id] = snapshotStyle(R, entry); });
    var loaded = loadEdits(storage, entries);
    notes = loaded.notes;
    entries.forEach(function (entry) { applyEdit(R, entry, loaded.edits[entry.id] || {}); });
    Object.keys(loaded.colors).forEach(function (ability) { R.UTIL_ABILITY_COLORS[ability] = loaded.colors[ability]; });
    Object.keys(R.CONTROL_PALETTES).forEach(function (palette) { option(header.palette, palette); });
    header.palette.value = "green-red";
    Array.from(new Set(entries.map(function (entry) { return entry.agent; }))).sort().forEach(function (agent) {
      option(header.agent, agent);
      var section = node("section", undefined, root), heading = node("h2", undefined, section);
      if (payload.icons && payload.icons[agent]) {
        var icon = node("img", undefined, heading);
        icon.src = payload.icons[agent]; icon.alt = ""; icon.className = "gallery-agent-icon";
      }
      node("span", agent, heading);
      var grid = node("div", undefined, section); grid.className = "gallery-grid";
      sections[agent] = {root: section, grid: grid};
    });
    entries.forEach(function (entry) { tiles.push(buildTile(entry, sections[entry.agent].grid)); });
    ["palette", "side"].forEach(function (key) {
      header[key].addEventListener("change", function () {
        tiles.forEach(function (tile) {
          tile.viewer.controlPalette = header.palette.value;
          if (key === "side") setTileSample(R, tile.viewer, tile.sample, header.side.value);
          tile.viewer.draw();
        });
      });
    });
    header.agent.addEventListener("change", filter);
    header.filter.addEventListener("input", filter);
    header.edited.addEventListener("change", filter);
    function critique() {
      return exportCritique(R, entries, originals, {}, notes,
        {palette: header.palette.value, replay_js_sha256: payload.replay_js_sha256});
    }
    header.export.addEventListener("click", function () {
      var json = critique(), blob = new Blob([JSON.stringify(json, null, 2)], {type: "application/json"});
      var url = global.URL.createObjectURL(blob), link = node("a", undefined, document.body);
      link.href = url;
      link.download = "util-critique-" + json.made.slice(0, 10) + ".json";
      link.click(); link.remove();
      global.setTimeout(function () { global.URL.revokeObjectURL(url); }, 0);
    });
    header.copy.addEventListener("click", async function () {
      var text = critiquePrompt(critique());
      function fallback() {
        var area = node("textarea", undefined, document.body);
        area.value = text; area.readOnly = true;
        area.style.position = "fixed"; area.style.left = "-10000px";
        try {
          area.focus(); area.select();
          if (!document.execCommand("copy")) throw new Error("Copy unavailable");
        } finally { area.remove(); }
      }
      try {
        try { await global.navigator.clipboard.writeText(text); } catch (_) { fallback(); }
        header.status.textContent = "Copied prompt and critique.";
      } catch (_) { header.status.textContent = "Could not copy. Use Export to save the critique."; }
    });
    header.clear.addEventListener("click", function () {
      clearAll(storage);
      entries.forEach(function (entry) { resetEntry(R, entry, originals[entry.id]); });
      notes = {};
      tiles.forEach(function (tile) { syncControls(tile); tile.viewer.draw(); });
      header.status.textContent = "Cleared all gallery edits and notes.";
      filter();
    });
    filter();
    return {entries: entries, originals: originals, tiles: tiles, critique: critique};
  }

  var api = {galleryEntries: galleryEntries, entrySamples: entrySamples, sampleRound: sampleRound,
    focusRow: focusRow, representativeKey: representativeKey, madeUpSample: madeUpSample,
    galleryPhases: galleryPhases, frameOf: frameOf, viewOf: viewOf, snapshotStyle: snapshotStyle,
    effectiveBadge: effectiveBadge, applyEdit: applyEdit, resetEntry: resetEntry, parseSettings: parseSettings,
    lookFrom: lookFrom, entryKey: entryKey, colorKey: colorKey, loadEdits: loadEdits, saveEdit: saveEdit,
    saveNote: saveNote, saveColor: saveColor, clearColor: clearColor, clearAll: clearAll,
    exportCritique: exportCritique, critiquePrompt: critiquePrompt,
    mount: mount, tileViewer: tileViewer, drawTile: drawTile};
  global.UtilGallery = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

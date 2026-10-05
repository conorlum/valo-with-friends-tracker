/*
 * The map-control drawing page's model (scripts/control_tagger.py; docs/map-control-stages-4-7-impl.md,
 * S6.2). Pure functions, inlined into the page and tested in node against app/control/geometry.py
 * (tests/replays/test_control_tagger.py):
 *
 * - `compose(map, edits)`: the sight and walk masks, exactly as geometry.masks builds them, from the
 *   Python-computed base masks the page embeds (`opaque`, the candidate `labels`), the tags and the
 *   paints. The page never reads colours from a canvas.
 * - `riskOne(sight, lines)`: the kill-line test (geometry.line_blocked: numpy's linspace and
 *   round-half-to-even, 3 px trimmed at each end) against the 2% bar.
 * - `exportTags(original, maps, edits)`: the whole tags.json, every map, with untouched maps and
 *   unknown fields exactly as loaded.
 * - chokes: `selectAt`, `rename`, `remove` (a tombstone), `move`, `add`, `exportAsset` (the
 *   `<Map>.chokes.json` body app/replays/choke_assets.py `save` writes) and `assetText` (its exact text).
 */
(function (global) {
  "use strict";

  var PX = 1024, P = 256, END_SKIP = 3, BAR = 0.02;
  var PAINTS = ["see_across_paint", "cover_paint", "cant_walk_paint", "uncertain_paint", "barrier_paint"];
  var TAG_KINDS = ["cover", "seeover", "walkable", "glyph", "seeacross"];

  function base64Bytes(text) {
    if (typeof atob !== "undefined") {
      var bin = atob(text), out = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    }
    return new Uint8Array(Buffer.from(text, "base64"));
  }

  function bytesBase64(bytes) {
    if (typeof btoa !== "undefined") {
      var s = "";
      for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
      return btoa(s);
    }
    return Buffer.from(bytes).toString("base64");
  }

  // [value, run, value, run, ...] over PX * PX pixels, row-major.
  function rleDecode(runs, ArrayType) {
    var out = new (ArrayType || Uint8Array)(PX * PX), pos = 0;
    for (var i = 0; i < runs.length; i += 2) {
      if (runs[i]) out.fill(runs[i], pos, pos + runs[i + 1]);
      pos += runs[i + 1];
    }
    return out;
  }

  // A paint string (P x P bits, little bit order, base64) <-> a Uint8Array of P * P cells.
  function unpackPaint(b64) {
    var bytes = base64Bytes(b64), out = new Uint8Array(P * P);
    for (var i = 0; i < out.length; i++) out[i] = (bytes[i >> 3] >> (i & 7)) & 1;
    return out;
  }

  function packPaint(cells) {
    var bytes = new Uint8Array(P * P / 8), any = false;
    for (var i = 0; i < cells.length; i++) if (cells[i]) { bytes[i >> 3] |= 1 << (i & 7); any = true; }
    return any ? bytesBase64(bytes) : null;
  }

  function paintAt(cells, i) {
    return cells ? cells[((i >> 10) >> 2) * P + ((i & 1023) >> 2)] : 0;
  }

  // The masks: sight (1 blocks) = cover | (not opaque and not see-across); walk (1 walkable) =
  // opaque and not cover and not can't-walk (app/control/geometry.py `masks`).
  function compose(map, edits) {
    var tags = edits.tags || {}, paints = edits.paints || {};
    var maxId = 0, id;
    for (var c = 0; c < map.candidates.length; c++) maxId = Math.max(maxId, map.candidates[c].id);
    var tagOf = new Uint8Array(maxId + 1);   // 0 none, 1 cover, 2 see-across, 3 other
    Object.keys(tags).forEach(function (key) {
      id = Number(key);
      if (id <= maxId) tagOf[id] = tags[key] === "cover" ? 1 : tags[key] === "seeacross" ? 2 : 3;
    });
    var sight = new Uint8Array(PX * PX), walk = new Uint8Array(PX * PX);
    var labels = map.labels, opaque = map.opaque;
    var cover = paints.cover_paint, across = paints.see_across_paint, cantWalk = paints.cant_walk_paint;
    for (var i = 0; i < PX * PX; i++) {
      var t = labels[i] ? tagOf[labels[i]] : 0;
      var isCover = t === 1 || paintAt(cover, i) === 1;
      var isAcross = t === 2 || paintAt(across, i) === 1;
      sight[i] = isCover || (!opaque[i] && !isAcross) ? 1 : 0;
      walk[i] = opaque[i] && !isCover && !paintAt(cantWalk, i) ? 1 : 0;
    }
    return { sight: sight, walk: walk };
  }

  function roundHalfEven(v) {
    var f = Math.floor(v), d = v - f;
    if (d > 0.5) return f + 1;
    if (d < 0.5) return f;
    return f % 2 === 0 ? f : f + 1;
  }

  // geometry.line_blocked: the pixels of np.round(np.linspace(a, b, n + 1)), ends trimmed.
  function linePixels(line) {
    var ax = line[0], ay = line[1], bx = line[2], by = line[3];
    var n = Math.max(Math.abs(bx - ax), Math.abs(by - ay)), out = [];
    if (n === 0) return out;
    var sx = (bx - ax) / n, sy = (by - ay) / n;
    for (var i = END_SKIP; i <= n - END_SKIP; i++) {
      var x = i === n ? bx : i * sx + ax, y = i === n ? by : i * sy + ay;
      out.push(roundHalfEven(y) * PX + roundHalfEven(x));
    }
    return out;
  }

  function riskOne(sight, lines) {
    var blocked = [];
    lines.forEach(function (line, k) {
      var px = linePixels(line);
      for (var j = 0; j < px.length; j++) if (sight[px[j]]) { blocked.push(k); return; }
    });
    var share = lines.length ? blocked.length / lines.length : null;
    return { qualifying: lines.length, blocked: blocked, share: share, passes: share === null ? null : share <= BAR };
  }

  function clone(value) { return JSON.parse(JSON.stringify(value)); }

  // The whole tags.json. `edits[name]`: {touched, tags: {id: tag}, paints: {key: cells | null},
  // cover_reviewed}. An untouched map keeps its loaded entry exactly; a touched one keeps its
  // unknown fields and the loaded rows of tags it didn't change.
  function exportTags(original, maps, edits) {
    var out = clone(original);
    out.maps = out.maps || {};
    Object.keys(edits).forEach(function (name) {
      var e = edits[name], map = maps[name];
      if (!e || !e.touched || !map) return;
      var entry = out.maps[name] || {};
      var before = {};
      (entry.tags || []).forEach(function (row) { before[row.id] = row; });
      var byId = {};
      map.candidates.forEach(function (c) { byId[c.id] = c; });
      var rows = Object.keys(e.tags).map(Number).sort(function (a, b) { return a - b; }).map(function (id) {
        var old = before[id], c = byId[id];
        if (old && old.tag === e.tags[id]) return old;
        return { id: id, tag: e.tags[id], kind: c.kind, bbox: c.bbox, px: c.px };
      });
      if (rows.length || entry.tags) entry.tags = rows;
      var packed = {};
      PAINTS.forEach(function (key) {
        packed[key] = e.paints[key] ? packPaint(e.paints[key]) : null;
        if (packed[key]) entry[key] = packed[key]; else delete entry[key];
      });
      if (rows.length && !entry.params) entry.params = clone(map.params);
      if ((rows.length || PAINTS.some(function (k) { return packed[k]; })) && !entry.image_sha) entry.image_sha = map.image_sha;
      entry.cover_reviewed = !!e.cover_reviewed;
      if (!entry.specials) entry.specials = [];
      out.maps[name] = entry;
    });
    return out;
  }

  // A map's editable state from its loaded entry.
  function editsFrom(entry) {
    var e = { touched: false, tags: {}, paints: {}, cover_reviewed: !!(entry && entry.cover_reviewed) };
    ((entry && entry.tags) || []).forEach(function (row) { e.tags[row.id] = row.tag; });
    PAINTS.forEach(function (key) { e.paints[key] = entry && entry[key] ? unpackPaint(entry[key]) : null; });
    return e;
  }

  // ---- Chokes (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2; app/replays/choke_assets.py).
  // A choke is {id, name, cells, source: "auto" | "hand", deleted}; cells index the 128 x 128 grid (8 px a
  // cell): cell = row * 128 + col. Every edit returns a new list and leaves its input alone. An edited choke
  // becomes source "hand", which choke_assets.merge keeps on re-detection; a deleted one stays as a tombstone
  // with its cells, so the merge's overlap rule stops detection from bringing it back.
  var G = 128;

  function chokeCopy(c) {
    return { id: c.id, name: c.name, cells: c.cells.slice(), source: c.source || "auto", deleted: !!c.deleted };
  }

  function maxId(chokes) {
    var m = 0;
    chokes.forEach(function (c) { m = Math.max(m, c.id); });
    return m;
  }

  function highWater(chokes, nextId) { return Math.max(nextId || 1, maxId(chokes) + 1); }

  function sortedCells(cells) {
    var seen = {}, out = [];
    cells.forEach(function (x) { x = Number(x); if (!seen[x]) { seen[x] = true; out.push(x); } });
    return out.sort(function (a, b) { return a - b; });
  }

  function edit(chokes, id, fn) {
    return chokes.map(function (c) { var d = chokeCopy(c); if (c.id === id) fn(d); return d; });
  }

  // The live choke whose cells include `cell` (null when none).
  function selectAt(chokes, cell) {
    for (var i = 0; i < chokes.length; i++) {
      var c = chokes[i];
      if (!c.deleted && c.cells.indexOf(cell) >= 0) return c;
    }
    return null;
  }

  function rename(chokes, id, name) {
    return edit(chokes, id, function (c) { c.name = String(name); c.source = "hand"; });
  }

  function remove(chokes, id) {
    return edit(chokes, id, function (c) { c.deleted = true; c.source = "hand"; });
  }

  // Shifts a choke's cells by whole cells; cells pushed off the grid are dropped (a move that would drop
  // every cell is refused). A choke's first move in a session also leaves a tombstone, with a new id, on the
  // cells it had as loaded, whatever its source: otherwise re-detection could find the passage there again
  // and recreate it (a choke renamed in an earlier session loads as "hand", yet may still sit on detected
  // cells). `original` is the list as loaded this session (default: `chokes`); a choke not in it (added
  // this session) gets no tombstone, and none is added while a tombstone with exactly those cells exists,
  // so later moves add nothing. Returns {chokes, nextId}.
  function move(chokes, id, dCol, dRow, nextId, original) {
    var next = highWater(chokes, nextId), target = null, loadedCells = null;
    chokes.forEach(function (c) { if (c.id === id) target = c; });
    if (!target || target.deleted || (!dCol && !dRow)) return { chokes: chokes.map(chokeCopy), nextId: next };
    (original || chokes).forEach(function (c) { if (c.id === id && !c.deleted) loadedCells = c.cells; });
    if (loadedCells) {
      var key = JSON.stringify(sortedCells(loadedCells));
      chokes.forEach(function (c) { if (c.deleted && JSON.stringify(sortedCells(c.cells)) === key) loadedCells = null; });
    }
    var detected = loadedCells;
    var cells = [];
    target.cells.forEach(function (x) {
      var col = (x % G) + dCol, row = Math.floor(x / G) + dRow;
      if (col >= 0 && col < G && row >= 0 && row < G) cells.push(row * G + col);
    });
    if (!cells.length) return { chokes: chokes.map(chokeCopy), nextId: next };
    var out = edit(chokes, id, function (c) { c.cells = sortedCells(cells); c.source = "hand"; });
    if (detected) {
      out.push({ id: next, name: String(next), cells: detected.slice(), source: "hand", deleted: true });
      next += 1;
    }
    return { chokes: out, nextId: next };
  }

  // A new hand choke on `cells`, named by its id. Returns {chokes, nextId}.
  function add(chokes, cells, nextId) {
    var next = highWater(chokes, nextId), out = chokes.map(chokeCopy);
    cells = sortedCells(cells).filter(function (x) { return x >= 0 && x < G * G; });
    if (!cells.length) return { chokes: out, nextId: next };
    out.push({ id: next, name: String(next), cells: cells, source: "hand", deleted: false });
    return { chokes: out, nextId: next + 1 };
  }

  // choke_assets.save's body: {"version": 1, "map", "next_id", "chokes"} sorted by id, keys in its order.
  function exportAsset(map, chokes, nextId) {
    return { version: 1, map: map, next_id: highWater(chokes, nextId),
             chokes: chokes.map(chokeCopy).sort(function (a, b) { return a.id - b.id; }) };
  }

  // The file text exactly as choke_assets.save writes it: json.dumps(body, indent=1) + "\n", whose
  // ensure_ascii escapes every character outside printable ASCII (and DEL) as \uXXXX. JS strings are
  // UTF-16, so escaping per code unit gives Python's surrogate pairs for astral characters.
  function assetText(body) {
    return JSON.stringify(body, null, 1).replace(/[\u007f-￿]/g, function (ch) {
      return "\\u" + ("000" + ch.charCodeAt(0).toString(16)).slice(-4);
    }) + "\n";
  }

  // ---- Map features (docs/superpowers/plans/2026-10-04-map-interaction-tagger.md). `Features` holds the
  // pure model the features panel edits; every function returns new values and leaves its inputs alone.
  var Features = {};

  // -- The state reducer: a line-for-line twin of app/replays/map_feature_state.py (its docstring is the
  // contract). tests/replays/test_map_feature_tagger.py runs both on the same event fixtures.
  var FS_EVENTS = ["switch", "shoot", "activate", "destroy", "proximity_enter", "proximity_leave", "reset", "observed",
                   "motion_complete", "scheduled"];
  var FS_PRIORITY = { reset: 0, destroy: 1, motion_complete: 2, "switch": 3, shoot: 3, observed: 3,
                      activate: 3, proximity_enter: 4, proximity_leave: 5, scheduled: 6 };
  var FS_MAX_STEPS = 10000;

  function fsError(message) { var e = new Error(message); e.name = "FeatureStateError"; return e; }
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function present(v) { return v !== undefined && v !== null; }

  function known(value) {
    if (value && typeof value === "object" && value.status === "known" && isNum(value.value)) return value.value;
    return null;
  }

  function fsStates(feature) {
    var out = {};
    (feature.states || []).forEach(function (s) { out[s.name] = s; });
    return out;
  }

  function fsFresh(feature, epoch, generation) {
    return { state: present(feature.initial_state) ? feature.initial_state : null, epoch: epoch,
             generation: generation, occupants: [], anonymous: 0, motion: null, pending: [], queue: [] };
  }

  function occupancy(st) { return st.occupants.length + st.anonymous; }

  function guardOk(guard, st) {
    if (!present(guard)) return true;
    var keys = typeof guard === "object" ? Object.keys(guard) : [];
    if (keys.length !== 1) throw fsError("a guard is one condition: " + JSON.stringify(guard));
    var key = keys[0], arg = guard[key], results;
    if (key === "all") {
      results = arg.map(function (g) { return guardOk(g, st); });
      return results.indexOf(false) >= 0 ? false : (results.indexOf(null) >= 0 ? null : true);
    }
    if (key === "any") {
      results = arg.map(function (g) { return guardOk(g, st); });
      return results.indexOf(true) >= 0 ? true : (results.indexOf(null) >= 0 ? null : false);
    }
    if (key === "not") { var r = guardOk(arg, st); return r === null ? null : !r; }
    if (key === "state_in") return arg.indexOf(st.state) >= 0;
    if (key === "occupants_eq") return occupancy(st) === arg;
    if (key === "occupants_gt") return occupancy(st) > arg;
    if (key === "motion_idle") return (st.motion === null) === !!arg;
    if (key === "unresolved") return null;
    throw fsError("unknown guard " + key);
  }

  function fsMatch(feature, st, event) {
    var unresolved = false, rows = feature.transitions || [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row.event !== event.kind) continue;
      if (event.kind === "scheduled" && (present(row.name) ? row.name : null) !== (present(event.name) ? event.name : null)) continue;
      if (row.from !== "*" && (row.from || []).indexOf(st.state) < 0) continue;
      var ok = guardOk(present(row.guard) ? row.guard : null, st);
      if (ok === null) { unresolved = true; continue; }
      if (ok) return [row, null];
    }
    return [null, unresolved ? "unresolved_guard" : "no_transition"];
  }

  function fsSchedule(st, t, kind, name) {
    st.pending.push({ t: t, kind: kind, name: present(name) ? name : null, generation: st.generation, epoch: st.epoch });
  }

  function fsFollowUp(st, row, t) {
    var follow = row.follow_up;
    if (!follow) return [];
    var after = known(follow.after);
    if (after === null) return ["unresolved_follow_up"];
    fsSchedule(st, t + after, "scheduled", follow.name);
    return [];
  }

  function fsEnter(feature, st, row, t) {
    var notes = [], states = fsStates(feature);
    st.generation += 1;
    st.pending = [];
    var motion = row.motion;
    if (motion) {
      st.motion = { row: present(row.id) ? row.id : null, from: st.state, to: present(row.to) ? row.to : null, start: t,
                    duration: known(motion.duration) };
      st.state = present(motion.state) ? motion.state : null;
      if (st.motion.duration === null) notes.push("unresolved_duration");
      else fsSchedule(st, t + st.motion.duration, "motion_complete");
    } else {
      st.state = present(row.to) ? row.to : null;
      st.motion = null;
      notes = notes.concat(fsFollowUp(st, row, t));
    }
    if ((states[st.state] || {}).terminal) { st.pending = []; st.queue = []; st.motion = null; }
    return notes;
  }

  function fsRow(feature, id) {
    var rows = feature.transitions || [];
    for (var i = 0; i < rows.length; i++) if (rows[i].id === id) return rows[i];
    return null;
  }

  function pendingKey(a, b) {
    if (a.t !== b.t) return a.t < b.t ? -1 : 1;
    if (FS_PRIORITY[a.kind] !== FS_PRIORITY[b.kind]) return FS_PRIORITY[a.kind] - FS_PRIORITY[b.kind];
    var an = a.name || "", bn = b.name || "";
    return an < bn ? -1 : an > bn ? 1 : 0;
  }

  function fsSnapshot(st) {
    return { state: st.state, epoch: st.epoch, generation: st.generation, occupancy: occupancy(st),
             moving: st.motion !== null,
             pending: st.pending.slice().sort(pendingKey).map(function (p) { return [p.t, p.kind, p.name]; }) };
  }

  function fsApply(feature, st, event) {
    st = clone(st);
    var kind = event.kind, t = Number(event.t);
    if (FS_EVENTS.indexOf(kind) < 0) throw fsError("unknown event " + kind);
    var entry = { t: t, kind: kind };
    if (present(event.name)) entry.name = event.name;
    var states = fsStates(feature);

    function done(result, reason, row, notes) {
      entry.result = result;
      if (reason) entry.reason = reason;
      if (row) entry.transition = present(row.id) ? row.id : null;
      if (notes && notes.length) entry.notes = notes;
      var snap = fsSnapshot(st);
      Object.keys(snap).forEach(function (k) { entry[k] = snap[k]; });
      return [st, entry];
    }

    if (kind === "reset") { st = fsFresh(feature, st.epoch + 1, st.generation + 1); return done("applied"); }
    if (kind === "motion_complete" || kind === "scheduled") {
      if (!("generation" in event) || !("epoch" in event)) return done("rejected", "no_token");
      if (event.epoch !== st.epoch || event.generation !== st.generation) return done("stale");
      st.pending = st.pending.filter(function (p) {
        return !(p.kind === event.kind && p.name === (present(event.name) ? event.name : null) && p.t === t);
      });
      if (kind === "motion_complete") {
        var motion = st.motion;
        if (motion === null) return done("stale");
        st.state = motion.to; st.motion = null;
        var notes = fsFollowUp(st, fsRow(feature, motion.row) || {}, t);
        var queued = st.queue; st.queue = [];
        queued.forEach(function (q) {
          var copy = Object.assign({}, q, { t: t }), out = fsApply(feature, st, copy);
          st = out[0];
          (entry.released = entry.released || []).push(out[1].result);
        });
        return done("applied", null, null, notes);
      }
    }
    if ((states[st.state] || {}).terminal) return done("rejected", "terminal");
    if (kind === "observed") {
      if (!Object.prototype.hasOwnProperty.call(states, event.state)) return done("rejected", "unknown_state");
      st.generation += 1;
      st.state = event.state; st.motion = null; st.pending = [];
      return done("applied");
    }
    var who = present(event.occupant) ? event.occupant : null;
    if (kind === "proximity_enter") {
      if (who === null) st.anonymous += 1;
      else if (st.occupants.indexOf(who) >= 0) return done("ignored", "duplicate_occupant");
      else st.occupants = st.occupants.concat([who]).sort();
    } else if (kind === "proximity_leave") {
      if (who === null) {
        if (!st.anonymous) return done("rejected", "unknown_occupant");
        st.anonymous -= 1;
      } else if (st.occupants.indexOf(who) < 0) return done("rejected", "unknown_occupant");
      else st.occupants = st.occupants.filter(function (o) { return o !== who; });
    }
    var m = fsMatch(feature, st, event);
    if (m[0]) { var n = fsEnter(feature, st, m[0], t); return done("applied", null, m[0], n); }
    if (kind === "proximity_enter" || kind === "proximity_leave") return done("applied", "occupancy_only");
    if (st.motion !== null) {
      var current = fsRow(feature, st.motion.row) || {}, policy = present(current.mid_motion) ? current.mid_motion : "unresolved";
      var mo = st.motion;
      if (policy === "ignore") return done("ignored", "in_motion");
      if (policy === "queue") {
        var q = {};
        Object.keys(event).forEach(function (k) { if (k !== "t") q[k] = event[k]; });
        st.queue.push(q);
        return done("queued");
      }
      if (policy === "restart") {
        st.generation += 1; st.pending = []; mo.start = t;
        if (mo.duration !== null) fsSchedule(st, t + mo.duration, "motion_complete");
        return done("applied", "restart", current);
      }
      if (policy === "reverse") {
        if (mo.duration === null) return done("rejected", "unresolved_duration");
        var elapsed = Math.min(Math.max(t - mo.start, 0.0), mo.duration);
        st.generation += 1; st.pending = [];
        var from = mo.from; mo.from = mo.to; mo.to = from;
        mo.start = t - (mo.duration - elapsed);
        fsSchedule(st, t + elapsed, "motion_complete");
        return done("applied", "reverse", current);
      }
      return done("rejected", "unresolved_policy");
    }
    return done("rejected", m[1]);
  }

  function fsRun(feature, events, until) {
    var st = fsFresh(feature, 0, 0);
    events.forEach(function (e) { if (FS_EVENTS.indexOf(e.kind) < 0) throw fsError("unknown event " + e.kind); });
    var inputs = events.map(function (e, i) { return [i, e]; }).sort(function (a, b) {
      var ta = Number(a[1].t), tb = Number(b[1].t);
      if (ta !== tb) return ta - tb;
      if (FS_PRIORITY[a[1].kind] !== FS_PRIORITY[b[1].kind]) return FS_PRIORITY[a[1].kind] - FS_PRIORITY[b[1].kind];
      return a[0] - b[0];
    });
    var trace = [], i = 0;
    while (true) {
      if (trace.length >= FS_MAX_STEPS) throw fsError("more than " + FS_MAX_STEPS + " events: a follow-up loop?");
      var nIn = i < inputs.length ? inputs[i][1] : null;
      var nP = st.pending.length ? st.pending.slice().sort(pendingKey)[0] : null;
      if (nIn === null && nP === null) break;
      var useInput = nP === null || (nIn !== null && (Number(nIn.t) < nP.t ||
        (Number(nIn.t) === nP.t && FS_PRIORITY[nIn.kind] <= FS_PRIORITY[nP.kind])));
      var ev = useInput ? nIn : nP;
      if (present(until) && Number(ev.t) > until) break;
      if (useInput) i += 1;
      var out = fsApply(feature, st, ev);
      st = out[0];
      if (!useInput) out[1].scheduled = true;
      trace.push(out[1]);
    }
    return trace;
  }

  Features.known = known;
  Features.initial = function (feature) { return fsFresh(feature, 0, 0); };
  Features.apply = fsApply;
  Features.run = fsRun;
  Features.guardOk = guardOk;
  Features.PRIORITY = FS_PRIORITY;

  // -- The schema's model operations (app/replays/map_feature_schema.py is the reference; its presets and
  // checklist seed are embedded in the page by scripts/control_tagger.py, not copied here).
  var SCHEMA_VERSION = 1, UV_MAX = 10000, PAINT_BYTES = 256 * 256 / 8;
  var ID_KINDS = ["feature", "trigger", "route", "floor", "bundle"];
  var LISTS = ["features", "triggers", "routes", "floors", "bundles"];
  var ID_RE = /^(feature|trigger|route|floor|bundle)-([1-9][0-9]*)$/;
  var EDITORIAL = ["name", "notes", "review", "ui", "parser_bindings", "category"];
  var TOP_EDITORIAL = ["checklist", "next_id", "image_sha", "runtime_digest", "ui", "notes"];

  function emptyMf() {
    return { version: SCHEMA_VERSION, next_id: 1, features: [], triggers: [], routes: [], floors: [], bundles: [],
             checklist: {} };
  }

  function eachObject(mf, fn) {
    LISTS.forEach(function (kind) {
      (Array.isArray(mf[kind]) ? mf[kind] : []).forEach(function (o) { if (o && typeof o === "object") fn(kind, o); });
    });
  }

  function nextNumber(mf) {
    var top = 0;
    eachObject(mf, function (_, o) { var m = ID_RE.exec(String(o.id)); if (m) top = Math.max(top, Number(m[2])); });
    var n = mf.next_id;
    return Math.max(typeof n === "number" && Math.floor(n) === n ? n : 1, top + 1);
  }

  function allocate(mf, kind) {
    if (ID_KINDS.indexOf(kind) < 0) throw new Error("unknown id kind " + kind);
    var out = clone(mf), n = nextNumber(out);
    out.next_id = n + 1;
    return { id: kind + "-" + n, mf: out };
  }

  function listOf(kind) { return kind === "feature" ? "features" : kind + "s"; }
  function kindOf(id) { var m = ID_RE.exec(String(id)); return m ? m[1] : null; }

  function find(mf, id) {
    var kind = kindOf(id);
    if (!kind) return null;
    var list = mf[listOf(kind)] || [];
    for (var i = 0; i < list.length; i++) if (list[i] && list[i].id === id) return list[i];
    return null;
  }

  // A new feature from a preset template (an object from the page's embedded presets). A traversal preset
  // (`route` given: the page's route template) also gets its route, owned by the feature.
  function create(mf, template, name, extra, route) {
    var a = allocate(mf, "feature"), out = a.mf;
    var f = Object.assign({ id: a.id, name: name || a.id }, clone(template), extra || {});
    f.id = a.id;
    out.features = (out.features || []).concat([f]);
    var routeId = null;
    if (route) {
      var r = allocate(out, "route");
      out = r.mf;
      routeId = r.id;
      out.routes = (out.routes || []).concat([Object.assign({ id: r.id, name: (name || a.id) + " route", owner: a.id }, clone(route))]);
    }
    return { mf: out, id: a.id, route: routeId };
  }

  function addObject(mf, kind, body) {
    var a = allocate(mf, kind), out = a.mf, list = listOf(kind);
    out[list] = (out[list] || []).concat([Object.assign({}, clone(body || {}), { id: a.id })]);
    return { mf: out, id: a.id };
  }

  // Edit one object's field (a path of keys/indexes). Returns a new catalogue.
  function setField(mf, id, path, value) {
    var out = clone(mf), obj = find(out, id);
    if (!obj) throw new Error("no object " + id);
    var cur = obj;
    for (var i = 0; i < path.length - 1; i++) {
      if (cur[path[i]] === undefined || cur[path[i]] === null) cur[path[i]] = typeof path[i + 1] === "number" ? [] : {};
      cur = cur[path[i]];
    }
    if (value === undefined) delete cur[path[path.length - 1]];
    else cur[path[path.length - 1]] = clone(value);
    return out;
  }

  function renameObject(mf, id, name) { return setField(mf, id, ["name"], String(name)); }

  function link(mf, triggerId, featureId, event) {
    var out = clone(mf), t = find(out, triggerId);
    if (!t || !find(out, featureId)) throw new Error("link needs an existing trigger and feature");
    t.targets = t.targets || [];
    if (!t.targets.some(function (x) { return x.feature === featureId && x.event === event; }))
      t.targets.push({ feature: featureId, event: event });
    return out;
  }

  function unlink(mf, triggerId, featureId, event) {
    var out = clone(mf), t = find(out, triggerId);
    if (t) t.targets = (t.targets || []).filter(function (x) { return !(x.feature === featureId && (!event || x.event === event)); });
    return out;
  }

  // Every typed reference as [from id, field, to id] (map_feature_schema.references).
  function references(mf) {
    var out = [];
    function floorRef(src, field, v) { if (typeof v === "string") out.push([src, field, v]); }
    (mf.features || []).forEach(function (f) {
      if (typeof f.parent === "string") out.push([f.id, "parent", f.parent]);
      if (typeof f.bundle === "string") out.push([f.id, "bundle", f.bundle]);
      if (Array.isArray(f.floors)) f.floors.forEach(function (fl) { floorRef(f.id, "floors", fl); });
      (f.states || []).forEach(function (s) {
        (s.sight || []).forEach(function (occ) { floorRef(f.id, "states." + s.name + ".sight.floor", (occ.bounds || {}).floor); });
      });
      floorRef(f.id, "base_edits.ground_binding", (f.base_edits || {}).ground_binding);
    });
    (mf.triggers || []).forEach(function (t) {
      (t.targets || []).forEach(function (x) { if (x && typeof x.feature === "string") out.push([t.id, "targets", x.feature]); });
      floorRef(t.id, "floor", t.floor);
    });
    (mf.routes || []).forEach(function (r) {
      if (typeof r.owner === "string") out.push([r.id, "owner", r.owner]);
      (r.endpoints || []).forEach(function (e) { floorRef(r.id, "endpoints." + e.id + ".floor", e.floor); });
      if (r.access && typeof r.access === "object")
        (r.access.sites || []).forEach(function (s) { floorRef(r.id, "access." + s.id + ".floor", s.floor); });
    });
    (mf.bundles || []).forEach(function (b) { (b.members || []).forEach(function (m) { out.push([b.id, "members", m]); }); });
    return out;
  }

  function referrers(mf, target) {
    return references(mf).filter(function (r) { return r[2] === target; }).map(function (r) { return [r[0], r[1]]; });
  }

  // Rewrites every reference to `from` as `to` (null: drop it, or mark a floor unresolved).
  function rewriteRefs(mf, from, to) {
    var unres = { status: "unresolved" };
    function fl(v) { return v === from ? (to === null ? clone(unres) : to) : v; }
    (mf.features || []).forEach(function (f) {
      if (f.parent === from) { if (to === null) delete f.parent; else f.parent = to; }
      if (f.bundle === from) { if (to === null) delete f.bundle; else f.bundle = to; }
      if (Array.isArray(f.floors)) {
        f.floors = f.floors.map(fl).filter(function (x) { return typeof x === "string"; });
      }
      (f.states || []).forEach(function (s) {
        (s.sight || []).forEach(function (occ) { if (occ.bounds && occ.bounds.floor === from) occ.bounds.floor = fl(from); });
      });
      if (f.base_edits && f.base_edits.ground_binding === from) f.base_edits.ground_binding = to;
    });
    (mf.triggers || []).forEach(function (t) {
      t.targets = (t.targets || []).map(function (x) { return x.feature === from ? (to === null ? null : Object.assign({}, x, { feature: to })) : x; })
        .filter(function (x) { return x !== null; });
      if (t.floor === from) t.floor = fl(from);
    });
    (mf.routes || []).forEach(function (r) {
      if (r.owner === from) r.owner = to;
      (r.endpoints || []).forEach(function (e) { if (e.floor === from) e.floor = fl(from); });
      if (r.access && typeof r.access === "object") (r.access.sites || []).forEach(function (s) { if (s.floor === from) s.floor = fl(from); });
    });
    (mf.bundles || []).forEach(function (b) {
      b.members = (b.members || []).map(function (m) { return m === from ? to : m; }).filter(function (m) { return m !== null; });
    });
  }

  // Deletes an object. With references to it and neither option, nothing changes and the references are
  // returned (`refused`): no hidden dangling link. {cascade: true} removes the links (and the routes a
  // deleted feature owns, and its child features' parent links); {relink: id} points them at another object.
  function removeObject(mf, id, opts) {
    opts = opts || {};
    var refs = referrers(mf, id);
    if (refs.length && !opts.cascade && !opts.relink) return { mf: clone(mf), dangling: refs, refused: true };
    var out = clone(mf), kind = kindOf(id);
    if (!kind || !find(out, id)) return { mf: out, dangling: [], refused: true };
    out[listOf(kind)] = out[listOf(kind)].filter(function (o) { return o.id !== id; });
    var removed = [id];
    if (opts.relink) {
      if (!find(out, opts.relink) || kindOf(opts.relink) !== kind) throw new Error("relink needs an existing " + kind);
      rewriteRefs(out, id, opts.relink);
    } else {
      if (kind === "feature") {
        (out.routes || []).filter(function (r) { return r.owner === id; }).forEach(function (r) { removed.push(r.id); });
        out.routes = (out.routes || []).filter(function (r) { return r.owner !== id; });
      }
      removed.forEach(function (gone) { rewriteRefs(out, gone, null); });
    }
    return { mf: out, dangling: [], refused: false, removed: removed };
  }

  // A copy of a feature with fresh ids for it and what it owns (its routes, its child features), internal
  // links remapped. Triggers aimed at the original aim at the copy too only with {keepExternal: true}.
  function duplicate(mf, id, opts) {
    opts = opts || {};
    var out = clone(mf), orig = find(out, id);
    if (!orig || kindOf(id) !== "feature") throw new Error("duplicate needs a feature");
    var map = {}, a;
    var family = [orig].concat((out.features || []).filter(function (f) { return f.parent === id; }));
    family.forEach(function (f) { a = allocate(out, "feature"); out = a.mf; map[f.id] = a.id; });
    var routes = (out.routes || []).filter(function (r) { return map[r.owner]; });
    routes.forEach(function (r) { a = allocate(out, "route"); out = a.mf; map[r.id] = a.id; });
    family.forEach(function (f) {
      var c = clone(f);
      c.id = map[f.id];
      if (map[c.parent]) c.parent = map[c.parent];
      delete c.bundle;
      if (f.id === id) c.name = (f.name || f.id) + " (copy)";
      out.features.push(c);
    });
    routes.forEach(function (r) {
      var c = clone(r);
      c.id = map[r.id]; c.owner = map[r.owner];
      out.routes.push(c);
    });
    if (opts.keepExternal) {
      (out.triggers || []).forEach(function (t) {
        var extra = [];
        (t.targets || []).forEach(function (x) { if (map[x.feature]) extra.push(Object.assign({}, x, { feature: map[x.feature] })); });
        t.targets = (t.targets || []).concat(extra);
      });
    }
    return { mf: out, id: map[id], ids: map };
  }

  // Undo/redo over whole immutable snapshots: links and properties undo with the strokes. `high` is the
  // allocation high-water mark: the largest next number any snapshot of this history has reached (or the caller
  // carried in from a save, a recovery or an import). It only grows, and every present is raised to it, so an
  // id allocated, undone and allocated again is never handed out twice; the content itself undoes as before
  // (next_id is editorial).
  function mfHighWater(mf) { return isObj(mf) ? nextNumber(mf) : 1; }
  function raiseNextId(mf, high) {
    if (!isObj(mf) || !high || nextNumber(mf) >= high) return mf;
    var out = clone(mf);
    out.next_id = high;
    return out;
  }
  function history(present, high) {
    var hw = Math.max(high || 1, mfHighWater(present));
    return { past: [], present: raiseNextId(present, hw), future: [], high: hw };
  }
  function historyPush(h, next) {
    var hw = Math.max(h.high || 1, mfHighWater(next));
    return { past: h.past.concat([h.present]), present: raiseNextId(next, hw), future: [], high: hw };
  }
  function undo(h) {
    if (!h.past.length) return h;
    return { past: h.past.slice(0, -1), present: raiseNextId(h.past[h.past.length - 1], h.high),
             future: [h.present].concat(h.future), high: h.high };
  }
  function redo(h) {
    if (!h.future.length) return h;
    return { past: h.past.concat([h.present]), present: raiseNextId(h.future[0], h.high), future: h.future.slice(1),
             high: h.high };
  }

  // -- Canonical JSON and SHA-256 (map_feature_schema.digest: sorted keys, no spaces, ASCII escapes).
  function canonicalJson(v) {
    if (v === null || typeof v !== "object") {
      if (typeof v === "number" && !isFinite(v)) throw new Error("non-finite number");
      return JSON.stringify(v).replace(/[\u007f-￿]/g, function (ch) {
        return "\\u" + ("000" + ch.charCodeAt(0).toString(16)).slice(-4);
      });
    }
    if (Array.isArray(v)) return "[" + v.map(canonicalJson).join(",") + "]";
    return "{" + Object.keys(v).filter(function (k) { return v[k] !== undefined; }).sort().map(function (k) {
      return canonicalJson(k) + ":" + canonicalJson(v[k]);
    }).join(",") + "}";
  }

  var SHA_K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb,
    0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f,
    0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];

  // SHA-256 of an ASCII string (canonicalJson's output is ASCII), as hex.
  function sha256Hex(text) {
    var n = text.length, words = new Array(((n + 9 + 63) >> 6) << 4).fill(0), i;
    for (i = 0; i < n; i++) words[i >> 2] |= (text.charCodeAt(i) & 0xff) << (24 - (i & 3) * 8);
    words[n >> 2] |= 0x80 << (24 - (n & 3) * 8);
    words[words.length - 1] = n * 8;
    var h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var w = new Array(64);
    function rotr(x, r) { return (x >>> r) | (x << (32 - r)); }
    for (var blk = 0; blk < words.length; blk += 16) {
      for (i = 0; i < 16; i++) w[i] = words[blk + i] | 0;
      for (i = 16; i < 64; i++) {
        var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (i = 0; i < 64; i++) {
        var t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[i] + w[i]) | 0;
        var t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h = [h[0] + a, h[1] + b, h[2] + c, h[3] + d, h[4] + e, h[5] + f, h[6] + g, h[7] + hh].map(function (x) { return x | 0; });
    }
    return h.map(function (x) { return ("00000000" + (x >>> 0).toString(16)).slice(-8); }).join("");
  }

  function digest(value) { return sha256Hex(canonicalJson(value)).slice(0, 16); }

  function runtimeProjection(mf) {
    var out = {};
    Object.keys(mf).forEach(function (k) { if (TOP_EDITORIAL.indexOf(k) < 0) out[k] = clone(mf[k]); });
    LISTS.forEach(function (kind) {
      if (!Array.isArray(mf[kind])) return;
      out[kind] = mf[kind].map(function (o) {
        if (!o || typeof o !== "object") return o;
        var c = {};
        Object.keys(o).forEach(function (k) { if (EDITORIAL.indexOf(k) < 0) c[k] = clone(o[k]); });
        return c;
      }).sort(function (x, y) {
        var a = x && typeof x === "object" ? String(x.id) : "", b = y && typeof y === "object" ? String(y.id) : "";
        return a < b ? -1 : a > b ? 1 : 0;
      });
    });
    return out;
  }

  function runtimeDigest(mf) { return digest(runtimeProjection(mf)); }

  // -- Validation: a twin of map_feature_schema.validate (same codes and places; test_map_feature_tagger.py
  // compares them). `seed` is the map's checklist seed from the page ([{key, label, preset, expected}]).
  var GEOMETRY_TYPES = ["point", "polyline", "polygon", "paint"], TRIGGER_TYPES = ["switch", "shoot", "proximity", "other"];
  var ROUTE_KINDS = ["zipline", "rope", "teleporter", "drop", "custom"], IN_TRANSIT = ["complete", "abort", "unresolved"];
  var BOUND_REFS = ["ground", "world", "all_height", "unresolved"];
  var GUARD_KEYS = ["all", "any", "not", "state_in", "occupants_eq", "occupants_gt", "motion_idle", "unresolved"];
  var MID_MOTION = ["ignore", "restart", "queue", "reverse", "unresolved"];

  function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
  function finite(x) { return typeof x === "number" && isFinite(x); }
  function uvOk(p) { return Array.isArray(p) && p.length === 2 && p.every(function (c) { return finite(c) && c >= 0 && c <= UV_MAX; }); }
  function pyRepr(v) { return v === null || v === undefined ? "None" : typeof v === "string" ? "'" + v + "'" : JSON.stringify(v); }

  function checkVersion(mf) {
    if (!isObj(mf)) return "map_features is not an object";
    if (mf.version !== SCHEMA_VERSION) return "map_features version " + pyRepr(mf.version) + " is not " + SCHEMA_VERSION +
      "; refusing to read or rewrite it";
    return null;
  }

  function validate(mf, seed, specials) {
    var errors = [], warnings = [];
    function err(where, code, message) { errors.push({ where: where, code: code, message: message }); }
    function warn(where, code, message) { warnings.push({ where: where, code: code, message: message }); }
    var problem = checkVersion(mf);
    if (problem) { err("map_features", "incompatible_version", problem); return { errors: errors, warnings: warnings }; }

    function value(where, v, unit, required) {
      if (required === undefined) required = true;
      if (v === undefined || v === null) {
        if (required) err(where, "missing_value", "missing; use {\"status\": \"unresolved\"} for an unknown value");
        return false;
      }
      if (!isObj(v) || (v.status !== "known" && v.status !== "unresolved")) { err(where, "bad_value", "not a value object"); return false; }
      if (v.status === "unresolved") return true;
      if (!finite(v.value) || v.value < 0) err(where, "bad_value", "known value must be a finite number >= 0");
      if (unit && v.unit !== unit) err(where, "bad_unit", "unit must be '" + unit + "'");
      return false;
    }
    function geometry(where, g, allowed) {
      allowed = allowed || GEOMETRY_TYPES;
      if (!isObj(g) || allowed.indexOf(g.type) < 0) { err(where, "bad_geometry", "geometry must be one of " + allowed.join(", ")); return; }
      if (g.type === "point" && !uvOk(g.uv)) err(where, "bad_coordinates", "a point needs uv [u, v] within 0..10000");
      else if (g.type === "polyline" || g.type === "polygon") {
        var need = g.type === "polyline" ? 2 : 3;
        if (!Array.isArray(g.uv) || g.uv.length < need || !g.uv.every(uvOk))
          err(where, "bad_coordinates", "a " + g.type + " needs at least " + need + " uv points within 0..10000");
        if (g.type === "polyline" && "width" in g && !(finite(g.width) && g.width > 0))
          err(where, "bad_dimensions", "a polyline's width must be a positive number");
      } else if (g.type === "paint") {
        var ok = typeof g.cells === "string" && /^[A-Za-z0-9+/]*={0,2}$/.test(g.cells) && g.cells.length % 4 === 0;
        var len = ok ? g.cells.length / 4 * 3 - (g.cells.match(/=*$/)[0].length) : -1;
        if (len !== PAINT_BYTES) err(where, "bad_paint", "paint must be " + PAINT_BYTES + " bytes of base64");
      }
    }
    function guard(where, g) {
      if (g === undefined || g === null) return;
      var keys = isObj(g) ? Object.keys(g) : [];
      if (keys.length !== 1 || GUARD_KEYS.indexOf(keys[0]) < 0) { err(where, "bad_guard", "unknown guard"); return; }
      var key = keys[0], arg = g[key];
      if (key === "all" || key === "any") (Array.isArray(arg) ? arg : []).forEach(function (sub, i) { guard(where + "." + key + "[" + i + "]", sub); });
      else if (key === "not") guard(where + ".not", arg);
      else if (key === "unresolved") warn(where, "unresolved_guard", "guard unresolved: the transition can't run until it is described");
    }

    var ids = {};
    eachObject(mf, function (kind, o) {
      var oid = o.id, singular = kind.slice(0, -1);
      if (typeof oid !== "string" || !ID_RE.test(oid) || oid.indexOf(singular + "-") !== 0)
        err(kind + ":" + (oid === undefined ? "None" : oid), "bad_id", "id must be '" + singular + "-<n>'");
      else if (ids[oid]) err(oid, "duplicate_id", "id used twice (" + ids[oid] + " and " + kind + ")");
      else ids[oid] = kind;
    });
    LISTS.forEach(function (kind) { if (kind in mf && !Array.isArray(mf[kind])) err(kind, "bad_shape", kind + " must be a list"); });
    function byId(list) { var out = {}; (Array.isArray(list) ? list : []).forEach(function (o) { if (isObj(o)) out[o.id] = o; }); return out; }
    var features = byId(mf.features), floors = byId(mf.floors), bundles = byId(mf.bundles);
    function has(map, k) { return Object.prototype.hasOwnProperty.call(map, k); }
    references(mf).forEach(function (r) {
      var field = r[1], expected = { parent: features, owner: features, targets: features, members: features, bundle: bundles }[field] || floors;
      if (!has(expected, r[2])) err(r[0], "dangling_reference", field + " -> " + pyRepr(r[2]) + ", which doesn't exist");
    });
    Object.keys(floors).forEach(function (k) {
      var fl = floors[k], band = fl.z_band;
      if (band === undefined || band === null) warn(fl.id, "unresolved_floor", "floor has no height band: a manual label only");
      else if (!(Array.isArray(band) && band.length === 2 && band.every(finite) && band[0] < band[1]))
        err(fl.id, "bad_dimensions", "z_band must be [low, high] metres with low < high");
      else if (!fl.height_sha) warn(fl.id, "unbound_floor", "a height band without the height asset it was read from");
    });
    Object.keys(features).forEach(function (k) { validateFeature(features[k]); });
    function validateFeature(f) {
      var fid = f.id, states = Array.isArray(f.states) ? f.states : [];
      var names = states.filter(isObj).map(function (s) { return s.name; });
      var uniq = names.filter(function (n, i) { return names.indexOf(n) === i; });
      if (uniq.length !== names.length || !names.every(function (n) { return typeof n === "string" && n; }))
        err(fid, "bad_states", "state names must be unique non-empty strings");
      var init = f.initial_state;
      if (init === undefined || init === null) warn(fid, "uncertain_initial_state", "round-start state not given");
      else if (names.indexOf(init) < 0) err(fid, "bad_state_reference", "initial_state is not one of its states");
      var rowIds = {}, rows = f.transitions || [];
      rows.forEach(function (row, i) {
        var where = fid + ".transitions[" + i + "]";
        if (!isObj(row)) { err(where, "bad_shape", "a transition is an object"); return; }
        if (typeof row.id !== "string" || rowIds[row.id]) err(where, "duplicate_id", "transition ids must be unique strings within the feature");
        rowIds[row.id] = true;
        if (FS_EVENTS.indexOf(row.event) < 0 || row.event === "motion_complete" || row.event === "reset")
          err(where, "bad_event", "unknown or reducer-owned event");
        if (row.event === "activate") warn(where, "unresolved_trigger", "what starts this transition is not classified yet");
        var src = row.from;
        if (src !== "*" && !(Array.isArray(src) && src.every(function (s) { return names.indexOf(s) >= 0; })))
          err(where, "bad_state_reference", "from names a state the feature doesn't have");
        if (names.indexOf(row.to) < 0) err(where, "bad_state_reference", "to is not one of its states");
        var motion = row.motion;
        if (motion !== undefined && motion !== null) {
          if (!isObj(motion) || names.indexOf(motion.state) < 0) err(where, "bad_state_reference", "motion.state must be one of its states");
          else if (value(where + ".motion.duration", motion.duration, "s")) warn(where, "unresolved_timing", "motion duration unresolved");
          var mm = row.mid_motion === undefined ? "unresolved" : row.mid_motion;
          if (MID_MOTION.indexOf(mm) < 0) err(where, "bad_policy", "mid_motion must be one of " + MID_MOTION.join(", "));
          else if (mm === "unresolved") warn(where, "unresolved_policy", "what a press during the motion does is unresolved");
        }
        var follow = row.follow_up;
        if (follow !== undefined && follow !== null) {
          if (!isObj(follow) || typeof follow.name !== "string") err(where, "bad_follow_up", "follow_up needs a name");
          else if (value(where + ".follow_up.after", follow.after, "s")) warn(where, "unresolved_timing", "follow-up delay unresolved");
          else if (!rows.some(function (r) { return isObj(r) && r.event === "scheduled" && r.name === follow.name; }))
            err(where, "dangling_reference", "no row handles the scheduled event");
        }
        guard(where + ".guard", row.guard);
      });
      states.forEach(function (s) {
        if (!isObj(s)) return;
        var where = fid + ".states." + (s.name === undefined ? "None" : s.name);
        ["blocks_movement", "blocks_sight"].forEach(function (key) {
          if (key in s && typeof s[key] !== "boolean") err(where, "bad_shape", key + " is true or false");
        });
        if (s.footprint !== undefined && s.footprint !== null) geometry(where + ".footprint", s.footprint);
        (s.sight || []).forEach(function (occ, j) { occluder(where + ".sight[" + j + "]", occ); });
      });
      var fv = f.floors;
      if (fv === undefined || fv === null || (isObj(fv) && fv.status === "unresolved")) {
        if (states.some(function (s) { return isObj(s) && (s.blocks_movement || s.blocks_sight); }))
          warn(fid, "unresolved_floor", "which floors this feature affects is unresolved");
      } else if (!Array.isArray(fv)) err(fid, "bad_shape", "floors is a list of floor ids or an unresolved value");
      var rot = f.rotation;
      if ((f.capabilities || []).indexOf("rotating") >= 0) {
        if (!isObj(rot) || rot.pivot === undefined || rot.pivot === null || rot.panel === undefined || rot.panel === null)
          warn(fid, "motion_incomplete", "rotating door without a pivot and panel: motion not fully described");
        else geometry(fid + ".rotation.pivot", rot.pivot, ["point"]);
      }
      if (isObj(f.noise) && f.noise.origin !== undefined && f.noise.origin !== null) geometry(fid + ".noise.origin", f.noise.origin, ["point"]);
      var edits = f.base_edits || {};
      ["potential_ground", "remove_sight"].forEach(function (key) {
        if (edits[key] !== undefined && edits[key] !== null) geometry(fid + ".base_edits." + key, edits[key]);
      });
      if (edits.potential_ground !== undefined && edits.potential_ground !== null && typeof edits.ground_binding !== "string")
        warn(fid, "unresolved_floor", "restored ground has no floor binding: it stays pending");
      (edits.reclassify || []).forEach(function (rc, k) {
        var where = fid + ".base_edits.reclassify[" + k + "]";
        if (!isObj(rc) || ["cover_paint", "cant_walk_paint", "tag", "base"].indexOf(rc.source) < 0 || rc.geometry === undefined || rc.geometry === null)
          err(where, "bad_reclassify", "reclassify names its source and the exact geometry");
        else geometry(where, rc.geometry);
      });
    }
    function occluder(where, occ) {
      if (!isObj(occ)) { err(where, "bad_shape", "an occluder is an object"); return; }
      geometry(where + ".geometry", occ.geometry, ["polyline", "polygon"]);
      var b = occ.bounds;
      if (!isObj(b) || BOUND_REFS.indexOf(b.ref) < 0) { err(where, "bad_bounds", "bounds.ref must be one of " + BOUND_REFS.join(", ")); return; }
      if (b.ref === "unresolved") { warn(where, "unresolved_height", "sight bounds unresolved: saved, pending, blocks nothing"); return; }
      if (b.ref === "all_height") return;
      var unres = [value(where + ".bounds.bottom", b.bottom, "m"), value(where + ".bounds.top", b.top, "m")];
      if (unres[0] || unres[1]) warn(where, "unresolved_height", "a sight bound is unresolved");
      else if (b.bottom.value >= b.top.value) err(where, "bad_dimensions", "bottom must be below top");
      if (b.ref === "ground" && typeof b.floor !== "string") err(where, "bad_bounds", "ground-relative bounds name the floor they stand on");
    }
    (Array.isArray(mf.triggers) ? mf.triggers : []).forEach(function (t) {
      if (!isObj(t)) return;
      var tid = t.id;
      if (TRIGGER_TYPES.indexOf(t.type) < 0) err(tid, "bad_trigger", "type must be one of " + TRIGGER_TYPES.join(", "));
      if (t.geometry !== undefined && t.geometry !== null) geometry(tid + ".geometry", t.geometry, ["point", "polygon", "paint"]);
      if (t.type === "proximity") {
        var area = t.geometry !== undefined && t.geometry !== null && (t.geometry.type === "polygon" || t.geometry.type === "paint");
        if (!area) {
          if (t.range === undefined || t.range === null) err(tid, "missing_range", "a proximity trigger needs an area or an explicitly unresolved range");
          else if (value(tid + ".range", t.range, "m")) warn(tid, "unresolved_range", "proximity range unresolved");
        }
      }
      var targets = t.targets || [];
      if (!targets.length) warn(tid, "no_target", "trigger linked to nothing");
      targets.forEach(function (x) { if (!isObj(x) || FS_EVENTS.indexOf(x.event) < 0) err(tid, "bad_target", "a target is {feature, event} with a known event"); });
      if (t.floor === undefined || t.floor === null || isObj(t.floor)) warn(tid, "unresolved_floor", "trigger floor unresolved");
    });
    (Array.isArray(mf.routes) ? mf.routes : []).forEach(function (r) { if (isObj(r)) validateRoute(r); });
    function validateRoute(r) {
      var rid = r.id;
      if (ROUTE_KINDS.indexOf(r.kind) < 0) err(rid, "bad_route", "kind must be one of " + ROUTE_KINDS.join(", "));
      var ends = r.endpoints;
      if (!Array.isArray(ends) || ends.length !== 2) { err(rid, "missing_endpoint", "a route has exactly two endpoints"); return; }
      var names = {};
      ends.forEach(function (e) {
        if (!isObj(e) || typeof e.id !== "string") { err(rid, "missing_endpoint", "an endpoint needs an id"); return; }
        names[e.id] = true;
        if (e.uv === undefined || e.uv === null) err(rid + "." + e.id, "missing_endpoint", "endpoint not placed");
        else if (!uvOk(e.uv)) err(rid + "." + e.id, "bad_coordinates", "endpoint uv must be within 0..10000");
        if (typeof e.floor !== "string") warn(rid + "." + e.id, "unresolved_floor", "landing floor unresolved");
      });
      var access = r.access === undefined ? "endpoint_only" : r.access;
      if (isObj(access)) {
        (access.sites || []).forEach(function (s) {
          if (!isObj(s) || !uvOk(s.uv) || typeof s.id !== "string") err(rid, "bad_coordinates", "an access site needs an id and uv");
          else names[s.id] = true;
        });
        warn(rid, "unsupported_access", "intermediate access is recorded but not yet consumable");
      } else if (access !== "endpoint_only") err(rid, "bad_route", "access is 'endpoint_only' or {sites: [...]}");
      if (r.path !== undefined && r.path !== null) geometry(rid + ".path", r.path, ["polyline"]);
      (r.directions || []).forEach(function (d, i) {
        var where = rid + ".directions[" + i + "]";
        if (!isObj(d) || !names[d.from] || !names[d.to] || d.from === d.to) { err(where, "bad_route", "a direction joins two of the route's endpoints or access sites"); return; }
        var u1 = value(where + ".entry", d.entry, "s"), u2 = value(where + ".transit", d.transit, "s");
        if (u1 || u2) warn(where, "unresolved_cost", "travel cost unresolved: the arc is saved but not consumable");
        if (d.length !== undefined && d.length !== null) value(where + ".length", d.length, "m", false);
      });
      if (!r.directions || !r.directions.length) err(rid, "bad_route", "a route needs at least one direction");
      var it = r.in_transit === undefined ? "unresolved" : r.in_transit;
      if (IN_TRANSIT.indexOf(it) < 0) err(rid, "bad_route", "in_transit must be one of " + IN_TRANSIT.join(", "));
      else if (r.states !== undefined && r.states !== null && it === "unresolved")
        warn(rid, "unresolved_policy", "conditional route with no policy for travel underway at closure");
      var owner = features[r.owner];
      if (r.states !== undefined && r.states !== null) {
        var owned = ((owner || {}).states || []).map(function (s) { return s.name; });
        if (!Array.isArray(r.states) || !r.states.every(function (s) { return owned.indexOf(s) >= 0; }))
          err(rid, "bad_state_reference", "a route's states are states of its owner");
      }
      var good = ends.filter(function (e) { return isObj(e) && uvOk(e.uv); });
      if (good.length === 2 && good[0].uv[0] === good[1].uv[0] && good[0].uv[1] === good[1].uv[1]) {
        var f0 = good[0].floor, f1 = good[1].floor;
        var verified = [f0, f1].every(function (x) { return typeof x === "string" && Array.isArray((floors[x] || {}).z_band); });
        if (!(verified && f0 !== f1)) warn(rid, "zero_length", "endpoints share a position without two distinct verified floors");
      }
      (specials || []).forEach(function (sp) {
        if (!sp || !Array.isArray(sp.a) || !Array.isArray(sp.b) || good.length !== 2) return;
        var k = function (p) { return p[0] + "," + p[1]; };
        var pair = [k(sp.a), k(sp.b)].sort().join("|"), mine = [k(good[0].uv), k(good[1].uv)].sort().join("|");
        if (pair === mine) warn(rid, "duplicates_special", "this route repeats a legacy special: it would run twice");
      });
    }
    Object.keys(bundles).forEach(function (k) { if (!(bundles[k].members || []).length) warn(bundles[k].id, "empty_bundle", "a bundle with no members"); });
    (seed || []).forEach(function (cat) {
      if (cat.expected === null || cat.expected === undefined) return;
      var n = (mf.features || []).filter(function (f) { return isObj(f) && f.category === cat.key; }).length;
      var entry = (mf.checklist || {})[cat.key] || {};
      if (n && n !== cat.expected) warn(cat.key, "count_mismatch", n + " annotated; the user's catalogue says " + cat.expected);
      else if (!n && entry.status === "user_reviewed" && !entry.none) warn(cat.key, "count_mismatch", "reviewed with none annotated");
    });
    return { errors: errors, warnings: warnings };
  }

  // -- The canonical catalogue: the whole tags.json the page edits, every map and unknown field kept.
  // `importCatalogue` is transactional: any problem leaves the current catalogue and returns the report by
  // map and feature. `seeds` = {map: checklist seed}. Returns {ok, catalogue, errors: [{map, where, code,
  // message}], warnings, affected: [{map, change}]}.
  function importCatalogue(current, text, seeds, specialsByMap) {
    var parsed, errors = [], warnings = [];
    try { parsed = typeof text === "string" ? JSON.parse(text) : clone(text); }
    catch (e) { return { ok: false, catalogue: current, errors: [{ map: null, where: "file", code: "bad_json", message: String(e.message) }], warnings: [], affected: [] }; }
    if (!isObj(parsed) || !isObj(parsed.maps))
      return { ok: false, catalogue: current, errors: [{ map: null, where: "file", code: "bad_shape", message: "not a tags.json: no maps object" }], warnings: [], affected: [] };
    Object.keys(parsed.maps).forEach(function (name) {
      var entry = parsed.maps[name];
      if (!isObj(entry) || !("map_features" in entry)) return;
      var rep = validate(entry.map_features, (seeds || {})[name], (specialsByMap || {})[name] || entry.specials);
      rep.errors.forEach(function (e) { errors.push(Object.assign({ map: name }, e)); });
      rep.warnings.forEach(function (w) { warnings.push(Object.assign({ map: name }, w)); });
    });
    var affected = diffCatalogues(current, parsed);
    if (errors.length) return { ok: false, catalogue: current, errors: errors, warnings: warnings, affected: affected };
    return { ok: true, catalogue: parsed, errors: [], warnings: warnings, affected: affected };
  }

  // What replacing `a` with `b` changes, per map: added / removed / features changed / other keys changed.
  function diffCatalogues(a, b) {
    var out = [], am = (a && a.maps) || {}, bm = (b && b.maps) || {};
    Object.keys(Object.assign({}, am, bm)).sort().forEach(function (name) {
      if (!(name in bm)) { out.push({ map: name, change: "removed" }); return; }
      if (!(name in am)) { out.push({ map: name, change: "added" }); return; }
      var fa = canonicalJson(am[name].map_features === undefined ? null : am[name].map_features);
      var fb = canonicalJson(bm[name].map_features === undefined ? null : bm[name].map_features);
      var ra = clone(am[name]), rb = clone(bm[name]);
      delete ra.map_features; delete rb.map_features;
      if (fa !== fb) out.push({ map: name, change: "features" });
      if (canonicalJson(ra) !== canonicalJson(rb)) out.push({ map: name, change: "tags" });
    });
    return out;
  }

  // An explicit merge: maps only one side has are kept; where both have map_features and they differ, the
  // policy picks ("incoming" or "current") and the conflict is listed. Other keys follow the same policy.
  function mergeCatalogue(current, incoming, policy) {
    var out = clone(current), conflicts = [];
    out.maps = out.maps || {};
    Object.keys(incoming.maps || {}).forEach(function (name) {
      var mine = out.maps[name], theirs = incoming.maps[name];
      if (mine === undefined) { out.maps[name] = clone(theirs); return; }
      if (canonicalJson(mine) === canonicalJson(theirs)) return;
      conflicts.push(name);
      if (policy === "incoming") out.maps[name] = clone(theirs);
    });
    return { catalogue: out, conflicts: conflicts };
  }

  // The whole tags.json for download: exactly exportTags (the legacy tags and paints) plus, for each map
  // whose features were edited (`featureEdits[name].dirty`), its map_features with the map image's checksum
  // and the runtime digest (a claim readers recompute, never trust). A map with no feature edits, or not on
  // the page, keeps its loaded map_features byte for byte. So does a map whose loaded map_features this page
  // can't read (another schema version): an edit never replaces it.
  function exportCatalogue(canonical, maps, edits, featureEdits) {
    var out = exportTags(canonical, maps, edits);
    Object.keys(featureEdits || {}).forEach(function (name) {
      var fe = featureEdits[name];
      if (!fe || !fe.dirty || fe.incompatible || !isObj(fe.mf)) return;
      var loaded = ((canonical && canonical.maps) || {})[name];
      if (isObj(loaded) && loaded.map_features !== undefined && checkVersion(loaded.map_features) !== null) return;
      var mf = clone(fe.mf);
      if (maps[name] && maps[name].image_sha) mf.image_sha = maps[name].image_sha;
      mf.runtime_digest = runtimeDigest(mf);
      out.maps = out.maps || {};
      out.maps[name] = Object.assign({}, out.maps[name] || {}, { map_features: mf });
    });
    return out;
  }

  // -- Rasterising: the twin of app/control/features.py `raster` (same float operations in the same order, so
  // the cells agree bit for bit): P x P cells, row-major, tested at cell centres in u/v.
  var CELL_UV = UV_MAX / P;

  function segmentCells(out, a, b, r2) {
    var ax = Number(a[0]), ay = Number(a[1]), dx = Number(b[0]) - ax, dy = Number(b[1]) - ay, dd = dx * dx + dy * dy;
    for (var row = 0; row < P; row++) {
      var v = (row + 0.5) * CELL_UV;
      for (var col = 0; col < P; col++) {
        var u = (col + 0.5) * CELL_UV, ex, ey;
        if (dd === 0) { ex = u - ax; ey = v - ay; }
        else {
          var t = ((u - ax) * dx + (v - ay) * dy) / dd;
          t = Math.min(Math.max(t, 0.0), 1.0);
          ex = ax + t * dx - u; ey = ay + t * dy - v;
        }
        if (ex * ex + ey * ey <= r2) out[row * P + col] = 1;
      }
    }
  }

  function raster(geometry) {
    var out = new Uint8Array(P * P);
    if (!geometry) return out;
    var kind = geometry.type, i, j, row, col;
    if (kind === "paint") return unpackPaint(geometry.cells);
    if (kind === "point") {
      col = Math.min(Math.floor(geometry.uv[0] / CELL_UV), P - 1); row = Math.min(Math.floor(geometry.uv[1] / CELL_UV), P - 1);
      out[row * P + col] = 1;
      return out;
    }
    var pts = geometry.uv;
    if (kind === "polyline") {
      var width = Number(geometry.width || 0.0), r = Math.max(width / 2.0, CELL_UV / 2.0);
      for (i = 0; i + 1 < pts.length; i++) segmentCells(out, pts[i], pts[i + 1], r * r);
      return out;
    }
    if (kind === "polygon") {
      var n = pts.length;
      j = n - 1;
      for (i = 0; i < n; i++) {
        var xi = Number(pts[i][0]), yi = Number(pts[i][1]), xj = Number(pts[j][0]), yj = Number(pts[j][1]);
        if (yi !== yj) {
          for (row = 0; row < P; row++) {
            var v = (row + 0.5) * CELL_UV;
            if ((yi > v) === (yj > v)) continue;
            var xAt = (xj - xi) * (v - yi) / (yj - yi) + xi;
            for (col = 0; col < P; col++) if ((col + 0.5) * CELL_UV < xAt) out[row * P + col] ^= 1;
          }
        }
        j = i;
      }
      return out;
    }
    throw new Error("unknown geometry type " + kind);
  }

  // The preview masks with each feature's state drawn in: app/control/features.py `compose_masks`, bit for bit.
  // `sight`/`walk` are PX * PX Uint8Arrays (1 blocks / 1 walkable); returns new arrays.
  function composeFeatures(sight, walk, mf, states) {
    var s = new Uint8Array(sight), w = new Uint8Array(walk);
    function apply(cells, fn) {
      for (var i = 0; i < PX * PX; i++) if (cells[((i >> 10) >> 2) * P + ((i & 1023) >> 2)]) fn(i);
    }
    (mf.features || []).forEach(function (f) {
      var st = stateOf(f, states);
      if (!st) return;
      if (st.footprint) {
        var cells = raster(st.footprint);
        if (st.blocks_movement) apply(cells, function (i) { w[i] = 0; });
        if (st.blocks_sight) apply(cells, function (i) { s[i] = 1; });
      }
      if (st.blocks_sight) (st.sight || []).forEach(function (occ) {
        if ((occ.bounds || {}).ref !== "unresolved") apply(raster(occ.geometry), function (i) { s[i] = 1; });
      });
    });
    return { sight: s, walk: w };
  }

  function stateOf(feature, states) {
    var name = states && Object.prototype.hasOwnProperty.call(states, feature.id) ? states[feature.id] : feature.initial_state;
    var list = feature.states || [];
    for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
    return null;
  }

  // The twin of features.py `rotation_pose` (coordinates agree to rounding: cos/sin may differ in the last bit).
  function rotationPose(feature, fraction) {
    var rot = feature.rotation || {};
    var phases = (rot.phases || []).filter(function (p) { return p && p.panel; }).sort(function (a, b) { return a.at - b.at; });
    if (phases.length) {
      var chosen = phases.filter(function (p) { return p.at <= fraction; });
      return (chosen.length ? chosen[chosen.length - 1] : phases[0]).panel;
    }
    var pivot = rot.pivot, panel = rot.panel, dir = rot.direction || {}, start = known(rot.start_deg), end = known(rot.end_deg);
    if (!pivot || !panel || start === null || end === null || dir.status !== "known" || (dir.value !== "cw" && dir.value !== "ccw")) return null;
    var theta = (dir.value === "cw" ? 1 : -1) * (end - start) * fraction * Math.PI / 180, c = Math.cos(theta), s = Math.sin(theta);
    var cx = pivot.uv[0], cy = pivot.uv[1];
    return Object.assign({}, panel, { uv: panel.uv.map(function (p) {
      return [cx + (p[0] - cx) * c - (p[1] - cy) * s, cy + (p[0] - cx) * s + (p[1] - cy) * c];
    }) });
  }

  // What a map's annotations amount to, three ways the plan keeps apart: the user's own completeness, whether
  // the geometry is fully described, and whether any replay signal exists for it (none yet: no decoder).
  function mapSummary(mf, seed) {
    var review = { draft: 0, needs_verification: 0, user_reviewed: 0 }, checklist = { not_started: 0, in_progress: 0, user_reviewed: 0 };
    (mf.features || []).forEach(function (f) { review[f.review || "draft"] = (review[f.review || "draft"] || 0) + 1; });
    (seed || []).forEach(function (c) { var e = (mf.checklist || {})[c.key] || {}; checklist[e.status || "not_started"]++; });
    var rep = validate(mf, seed || []), open = rep.warnings.filter(function (w) {
      return /unresolved|uncertain|motion_incomplete|zero_length|no_target/.test(w.code);
    }).length;
    return { annotation: { features: (mf.features || []).length, review: review, checklist: checklist },
             geometry: { errors: rep.errors.length, unresolved: open, ready: !rep.errors.length && !open },
             replay: { decoder: false, note: "no replay decoder yet: signals are unverified for every feature" } };
  }

  function makeBreakable(feature) {
    var out = clone(feature);
    out.capabilities = out.capabilities || [];
    if (out.capabilities.indexOf("breakable") < 0) out.capabilities.push("breakable");
    out.states = out.states || [];
    if (!out.states.some(function (s) { return s.name === "broken"; }))
      out.states.push({ name: "broken", blocks_movement: false, blocks_sight: false, terminal: true });
    out.transitions = out.transitions || [];
    if (!out.transitions.some(function (r) { return r.event === "destroy"; }))
      out.transitions.push({ id: "break", from: "*", event: "destroy", to: "broken" });
    return out;
  }

  // -- Drafts: autosaved snapshots in a key/value store (the page passes a localStorage wrapper; tests a fake).
  // A draft belongs to its source: the catalogue, map images and floor assets it was started from (their
  // digests) and the schema version. A save writes the whole new snapshot, reads it back, and only then
  // moves the active pointer, keeping the previous snapshot; a failed or interrupted save leaves the last good
  // one active, and a corrupt active snapshot falls back to the previous. A draft from another source is listed
  // for restore or download, never applied silently. Store errors (quota, permission) come back as
  // {ok: false}: the page shows "not saved" and offers the download.
  var DRAFT_PREFIX = "control-tagger-features-v1";

  function draftSourceKey(source) { return digest(source).slice(0, 12); }

  // {map: high-water} taking the larger number per map (either side may be absent): null when neither has any.
  function maxHigh(a, b) {
    if (!isObj(a) && !isObj(b)) return null;
    var out = {};
    [a, b].forEach(function (h) {
      if (!isObj(h)) return;
      Object.keys(h).forEach(function (m) {
        var n = h[m];
        if (typeof n === "number" && Math.floor(n) === n && n > 0) out[m] = Math.max(out[m] || 1, n);
      });
    });
    return out;
  }

  // Why a draft body's own feature models can't be restored by this page: a model that isn't an object or is of
  // another schema version, or an edit of a map whose catalogue entry is of another version (it would replace
  // annotations this page can't read).
  function draftModelProblems(body) {
    var out = [], maps = (isObj(body) && isObj(body.canonical) && isObj(body.canonical.maps)) ? body.canonical.maps : {};
    Object.keys((isObj(body) && isObj(body.features)) ? body.features : {}).sort().forEach(function (m) {
      var entry = body.features[m], why = isObj(entry) ? checkVersion(entry.mf) : "not a feature draft";
      if (why) out.push("the draft of " + m + ": " + why);
      var loaded = isObj(maps[m]) ? maps[m].map_features : undefined;
      if (loaded !== undefined && checkVersion(loaded) !== null)
        out.push("the draft edits " + m + ", whose map_features can't be read by this page: " + checkVersion(loaded));
    });
    return out;
  }

  // What differs between the source a draft was saved for and this page's source.
  function sourceDiffers(src, mine) {
    var differs = [];
    src = src || {}; mine = mine || {};
    if (src.catalogue_digest !== mine.catalogue_digest) differs.push("catalogue");
    if (src.schema_version !== mine.schema_version) differs.push("schema");
    ["image_digests", "floor_digests"].forEach(function (part) {
      var a = src[part] || {}, b = mine[part] || {};
      Object.keys(Object.assign({}, a, b)).sort().forEach(function (m) {
        if (a[m] !== b[m]) differs.push((part === "image_digests" ? "image of " : "floors of ") + m);
      });
    });
    return differs;
  }

  // A draft's whole catalogue: its canonical tags.json with each edited map's model in place.
  function draftCatalogue(body) {
    var out = clone(body.canonical);
    out.maps = out.maps || {};
    Object.keys(body.features || {}).forEach(function (m) {
      out.maps[m] = Object.assign({}, out.maps[m] || {}, { map_features: clone(body.features[m].mf) });
    });
    return out;
  }

  // The Download draft file: the draft body ({canonical, features, high}) with what it was saved for and a
  // checksum, so a restore can show what changed since and refuse a damaged file.
  var DRAFT_FORMAT = "control-tagger-draft";
  function draftFile(source, body) {
    var core = { canonical: body.canonical, features: body.features || {}, high: body.high || null };
    return Object.assign({ format: DRAFT_FORMAT, version: 1, provenance: source, checksum: digest(core) }, core);
  }

  // Reads a downloaded draft (or an older draft's snapshot body) for restore. Returns null when `text` isn't a
  // draft (a tags.json goes to importCatalogue). Transactional like importCatalogue: any problem restores
  // nothing. A draft may be incomplete: its models' structural errors are warnings, not refusals. A file from
  // before drafts carried a provenance ({canonical, features}) is read with that noted. Returns {draft: true,
  // ok, body, errors, warnings, affected (against `working`, the page's current catalogue), differs}.
  function importDraft(source, text, working, provenance) {
    var parsed;
    try { parsed = typeof text === "string" ? JSON.parse(text) : clone(text); }
    catch (e) { return null; }
    var envelope = isObj(parsed) && parsed.format === DRAFT_FORMAT;
    var bare = isObj(parsed) && !("maps" in parsed) && isObj(parsed.canonical) && isObj(parsed.features);
    if (!envelope && !bare) return null;
    var errors = [], warnings = [], differs = [];
    function fail(code, message) { errors.push({ map: null, where: "draft", code: code, message: message }); }
    var body = { canonical: parsed.canonical, features: parsed.features, high: parsed.high || null };
    if (envelope && parsed.version !== 1) fail("incompatible_draft", "draft file version " + pyRepr(parsed.version) + " is not 1");
    if (!isObj(body.canonical) || !isObj(body.canonical.maps)) fail("bad_shape", "the draft has no catalogue (canonical.maps)");
    if (!isObj(body.features) || !Object.keys(body.features).every(function (m) { return isObj(body.features[m]) && isObj(body.features[m].mf); }))
      fail("bad_shape", "the draft's feature models aren't {map: {mf}}");
    if (body.high !== null && !isObj(body.high)) fail("bad_shape", "the draft's id high-water marks aren't {map: number}");
    if (envelope && !errors.length) {
      var sum;
      try { sum = digest({ canonical: body.canonical, features: body.features, high: body.high }); } catch (e) { sum = null; }
      if (sum !== parsed.checksum) fail("corrupt", "the draft's checksum doesn't match its contents (damaged or edited)");
    }
    if (!errors.length) draftModelProblems(body).forEach(function (p) { fail("incompatible_version", p); });
    var prov = envelope ? parsed.provenance : provenance;
    if (isObj(prov)) differs = sourceDiffers(prov, source);
    else differs = ["no provenance recorded"];
    if (errors.length) return { draft: true, ok: false, body: null, errors: errors, warnings: [], affected: [], differs: differs };
    Object.keys(body.features).sort().forEach(function (m) {
      var rep = validate(body.features[m].mf, null, (body.canonical.maps[m] || {}).specials);
      rep.errors.concat(rep.warnings).forEach(function (w) { warnings.push(Object.assign({ map: m }, w)); });
    });
    return { draft: true, ok: true, body: clone(body), errors: [], warnings: warnings,
             affected: diffCatalogues(working, draftCatalogue(body)), differs: differs };
  }

  function Drafts(store, source) {
    this.store = store;
    this.source = source;
    this.key = draftSourceKey(source);
  }

  Drafts.prototype._k = function (sourceKey, part) { return DRAFT_PREFIX + ":" + sourceKey + ":" + part; };

  Drafts.prototype._get = function (k) {
    try { var v = this.store.getItem(k); return v === null || v === undefined ? null : JSON.parse(v); }
    catch (e) { return undefined; }           // unreadable: treated as corrupt by the caller
  };

  Drafts.prototype._readSnap = function (sourceKey, rev) {
    var s = this._get(this._k(sourceKey, "snap:" + rev));
    if (!s || typeof s !== "object" || s.revision !== rev || !s.body) return null;
    try { if (digest(s.body) !== s.checksum) return null; } catch (e) { return null; }
    return s;
  };

  Drafts.prototype.save = function (body) {
    var pointer = this._get(this._k(this.key, "active"));
    var rev = (pointer && typeof pointer.revision === "number" ? pointer.revision : 0) + 1;
    var snap = { provenance: this.source, revision: rev, checksum: digest(body), body: body };
    var stage = "snapshot";
    try {
      this.store.setItem(this._k(this.key, "snap:" + rev), JSON.stringify(snap));
      if (!this._readSnap(this.key, rev)) throw new Error("the snapshot did not read back intact");
      stage = "pointer";
      // the pointer also carries the id high-water marks (the larger of this body's and the last pointer's), so a
      // recovery to the previous snapshot never hands out an id the damaged newest one had already used
      this.store.setItem(this._k(this.key, "active"), JSON.stringify({ revision: rev, previous: pointer ? pointer.revision : null,
                                                                       high: maxHigh(pointer && pointer.high, body && body.high) }));
      stage = "cleanup";
      if (pointer && typeof pointer.previous === "number") this.store.removeItem(this._k(this.key, "snap:" + pointer.previous));
      stage = "index";
      var index = this._get(DRAFT_PREFIX + ":index") || {};
      index[this.key] = { source: this.source, revision: rev };
      this.store.setItem(DRAFT_PREFIX + ":index", JSON.stringify(index));
    } catch (e) {
      var advanced = stage === "cleanup" || stage === "index";
      if (!advanced) { try { this.store.removeItem(this._k(this.key, "snap:" + rev)); } catch (e2) { /* nothing to undo */ } }
      return { ok: advanced, revision: advanced ? rev : (pointer ? pointer.revision : null),
               error: (e && e.name ? e.name + ": " : "") + (e && e.message ? e.message : String(e)), stage: stage };
    }
    return { ok: true, revision: rev };
  };

  // {status: "ok" | "recovered" | "none" | "corrupt" | "incompatible", body, revision, problems}
  Drafts.prototype.load = function () {
    var pointer = this._get(this._k(this.key, "active"));
    if (pointer === null) return { status: "none", body: null, revision: null, problems: [] };
    if (!pointer || typeof pointer.revision !== "number") return { status: "corrupt", body: null, revision: null, problems: ["the active pointer is unreadable"] };
    var problems = [], tries = [[pointer.revision, "ok"], [pointer.previous, "recovered"]];
    for (var i = 0; i < tries.length; i++) {
      var rev = tries[i][0];
      if (typeof rev !== "number") continue;
      var s = this._readSnap(this.key, rev);
      if (!s) { problems.push("snapshot " + rev + " is missing or corrupt"); continue; }
      // the draft's own feature models must be readable; a catalogue entry of another schema is only carried
      // (read-only, kept byte for byte), unless the draft holds an edit of it
      var bad = draftModelProblems(s.body);
      if (bad.length) return { status: "incompatible", body: null, revision: rev, problems: bad };
      var body = s.body;
      if (isObj(pointer.high)) { body = clone(body); body.high = maxHigh(body.high, pointer.high); }
      return { status: tries[i][1], body: body, revision: rev, problems: problems };
    }
    return { status: "corrupt", body: null, revision: null, problems: problems };
  };

  // Drafts saved for other sources: {key, source, revision, differs: [what changed]}.
  Drafts.prototype.others = function () {
    var index = this._get(DRAFT_PREFIX + ":index") || {}, mine = this.source, out = [];
    Object.keys(index).sort().forEach(function (k) {
      if (k === this.key) return;
      var src = index[k].source || {};
      out.push({ key: k, source: src, revision: index[k].revision, differs: sourceDiffers(src, mine) });
    }, this);
    return out;
  };

  // Another source's draft, for an explicit restore (the page revalidates it against the current maps) or a
  // download. Never applied by itself.
  Drafts.prototype.readOther = function (key) {
    var pointer = this._get(this._k(key, "active"));
    if (!pointer || typeof pointer.revision !== "number") return null;
    return this._readSnap(key, pointer.revision) || (typeof pointer.previous === "number" ? this._readSnap(key, pointer.previous) : null);
  };

  Object.assign(Features, {
    Drafts: Drafts, draftSourceKey: draftSourceKey, DRAFT_PREFIX: DRAFT_PREFIX,
    DRAFT_FORMAT: DRAFT_FORMAT, draftFile: draftFile, importDraft: importDraft, draftCatalogue: draftCatalogue,
    draftModelProblems: draftModelProblems, sourceDiffers: sourceDiffers, maxHigh: maxHigh, raiseNextId: raiseNextId,
    raster: raster, composeFeatures: composeFeatures, stateOf: stateOf, makeBreakable: makeBreakable, CELL_UV: CELL_UV,
    rotationPose: rotationPose, mapSummary: mapSummary,
    importCatalogue: importCatalogue, diffCatalogues: diffCatalogues, mergeCatalogue: mergeCatalogue,
    exportCatalogue: exportCatalogue,
    SCHEMA_VERSION: SCHEMA_VERSION, UV_MAX: UV_MAX, emptyMf: emptyMf, checkVersion: checkVersion, validate: validate, nextNumber: nextNumber, allocate: allocate,
    find: find, create: create, addObject: addObject, setField: setField, rename: renameObject, link: link, unlink: unlink,
    references: references, referrers: referrers, remove: removeObject, duplicate: duplicate,
    history: history, historyPush: historyPush, undo: undo, redo: redo,
    canonicalJson: canonicalJson, sha256Hex: sha256Hex, digest: digest, runtimeProjection: runtimeProjection,
    runtimeDigest: runtimeDigest
  });

  var api = {
    Features: Features,
    PX: PX, P: P, BAR: BAR, PAINTS: PAINTS, TAG_KINDS: TAG_KINDS, rleDecode: rleDecode, unpackPaint: unpackPaint,
    packPaint: packPaint, compose: compose, linePixels: linePixels, riskOne: riskOne, exportTags: exportTags,
    editsFrom: editsFrom, base64Bytes: base64Bytes, roundHalfEven: roundHalfEven,
    CHOKE_GRID: G, selectAt: selectAt, rename: rename, remove: remove, move: move, add: add, exportAsset: exportAsset,
    assetText: assetText
  };
  global.TaggerCore = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

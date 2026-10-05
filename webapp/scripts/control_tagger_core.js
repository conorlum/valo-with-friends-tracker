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
  var FS_EVENTS = ["switch", "shoot", "destroy", "proximity_enter", "proximity_leave", "reset", "observed",
                   "motion_complete", "scheduled"];
  var FS_PRIORITY = { reset: 0, destroy: 1, motion_complete: 2, "switch": 3, shoot: 3, observed: 3,
                      proximity_enter: 4, proximity_leave: 5, scheduled: 6 };
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

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

  var api = {
    PX: PX, P: P, BAR: BAR, PAINTS: PAINTS, TAG_KINDS: TAG_KINDS, rleDecode: rleDecode, unpackPaint: unpackPaint,
    packPaint: packPaint, compose: compose, linePixels: linePixels, riskOne: riskOne, exportTags: exportTags,
    editsFrom: editsFrom, base64Bytes: base64Bytes, roundHalfEven: roundHalfEven,
    CHOKE_GRID: G, selectAt: selectAt, rename: rename, remove: remove, move: move, add: add, exportAsset: exportAsset,
    assetText: assetText
  };
  global.TaggerCore = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

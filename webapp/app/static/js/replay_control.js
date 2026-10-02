/*
 * Map control in the replay viewer (docs/replay-map-control-plan.md, "Cell states", "Delivery";
 * Stages 4-5). No build step and no dependencies, like replay.js.
 *
 * Decodes a round's stored control (`GET /replays/{uuid}/{n}/control.bin`; the format is
 * app/replays/control_format.py's, whose decode_states / decode_masks this must match), and paints
 * it: the layer's cell states, one player's highlight, and the match heatmap. Pure functions,
 * tested in node (tests/replays/test_control_viewer.py).
 */
(function (global) {
  "use strict";

  var MAGIC = [86, 67, 84, 76];    // "VCTL"
  var DATA_VERSION = 1;
  var GRID = 128;
  var SLOTS = 10;
  var STATE_NAMES = ["none", "a_passive", "a_safe", "a_active", "b_passive", "b_safe", "b_active",
                     "contested_active", "contested"];

  // ------------------------------------------------------------ decoding

  function readVarint(buf, pos) {
    var n = 0, mul = 1, b;
    do {
      b = buf[pos++];
      n += (b & 0x7f) * mul;
      mul *= 128;
    } while (b & 0x80);
    return [n, pos];
  }

  function utf8(bytes) {
    if (typeof TextDecoder !== "undefined") return new TextDecoder("utf-8").decode(bytes);
    return Buffer.from(bytes).toString("utf-8");
  }

  // The raw (already un-gzipped) bytes -> {header, states, coverage, control}, each stream a view.
  function parse(buffer) {
    var raw = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    for (var i = 0; i < 4; i++) {
      if (raw[i] !== MAGIC[i]) throw new Error("not a control blob");
    }
    if (raw[4] !== DATA_VERSION) throw new Error("control data version " + raw[4] + " is not " + DATA_VERSION);
    var length = raw[5] | (raw[6] << 8) | (raw[7] << 16) | (raw[8] * 16777216);
    var header = JSON.parse(utf8(raw.subarray(9, 9 + length)));
    var body = raw.subarray(9 + length);
    var out = { header: header };
    Object.keys(header.streams).forEach(function (name) {
      var at = header.streams[name];
      out[name] = body.subarray(at[0], at[0] + at[1]);
    });
    out.cells = header.cells;
    out.walk = walkCells(header);
    out.times = header.ticks.map(function (units) { return units / header.hz; });
    return out;
  }

  function base64Bytes(text) {
    if (typeof atob !== "undefined") {
      var bin = atob(text), out = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    }
    return new Uint8Array(Buffer.from(text, "base64"));
  }

  // The flat map cells (row-major over GRID x GRID) of the round's walkable cells, in order.
  function walkCells(header) {
    var bits = base64Bytes(header.walk), out = [];
    for (var i = 0; i < GRID * GRID; i++) {
      if (bits[i >> 3] >> (7 - (i & 7)) & 1) out.push(i);
    }
    return Int32Array.from(out);
  }

  // The last tick at or before t, or -1 before the first (the layer holds the last state).
  function tickAt(times, t) {
    var lo = 0, hi = times.length - 1;
    if (!times.length || t < times[0] - 1e-9) return -1;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (times[mid] <= t + 1e-9) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  // Seeks through one round's streams: each reader decodes forward from where it is, or from the
  // nearest checkpoint at or before the wanted tick.
  function Cursor(parsed) {
    this.p = parsed;
    this.cps = parsed.header.checkpoints;   // [[tick, states offset, coverage offset, control offset]]
    this.isCp = {};
    for (var i = 0; i < this.cps.length; i++) this.isCp[this.cps[i][0]] = i;
    this.st = null;
    this.mk = {};
  }

  Cursor.prototype.checkpointBefore = function (i) {
    var best = 0;
    for (var k = 0; k < this.cps.length && this.cps[k][0] <= i; k++) best = k;
    return best;
  };

  // Tick i's cell state codes (a Uint8Array over the walkable cells; don't keep it across calls).
  Cursor.prototype.states = function (i) {
    var s = this.st, stream = this.p.states, cells = this.p.cells, cp = this.cps[this.checkpointBefore(i)];
    if (!s || s.tick > i || cp[0] > s.tick + 1) {
      s = this.st = { tick: cp[0] - 1, pos: cp[1], cur: s ? s.cur : new Uint8Array(cells) };
    }
    while (s.tick < i) {
      var pos = s.pos, cur = s.cur, kind = stream[pos++];
      if (kind === 0) {
        for (var c = 0; c < cells; c += 2) {
          var b = stream[pos++];
          cur[c] = b >> 4;
          if (c + 1 < cells) cur[c + 1] = b & 15;
        }
      } else {
        var r = readVarint(stream, pos), count = r[0], cell = -1;
        pos = r[1];
        for (var k = 0; k < count; k++) {
          r = readVarint(stream, pos);
          cell += r[0] + 1;
          cur[cell] = stream[r[1]];
          pos = r[1] + 1;
        }
      }
      s.pos = pos;
      s.tick += 1;
    }
    return s.cur;
  };

  // What a side group knew at tick i (the optional `knew_a` / `knew_b` streams, R3.3): the true state
  // with that tick's stored cells replaced; null when the row has no such stream. Each tick stands
  // alone, so reading one only skips over the ticks since the nearest checkpoint.
  Cursor.prototype.knew = function (name, i) {
    var stream = this.p[name], cps = this.p.header.knew_checkpoints;
    if (!stream || !cps) return null;
    var col = name === "knew_a" ? 1 : 2;
    var base = this.states(i), out = this.knewBuf || (this.knewBuf = new Uint8Array(this.p.cells));
    out.set(base);
    this.kn = this.kn || {};
    var k = this.kn[name], best = 0;
    for (var c = 0; c < cps.length && cps[c][0] <= i; c++) best = c;
    if (!k || k.tick > i || cps[best][0] > k.tick) k = this.kn[name] = { tick: cps[best][0], pos: cps[best][col] };
    var r, count, j;
    while (k.tick < i) {
      r = readVarint(stream, k.pos);
      count = r[0];
      var pos = r[1];
      for (j = 0; j < count; j++) pos = readVarint(stream, pos)[1] + 1;
      k.pos = pos;
      k.tick += 1;
    }
    r = readVarint(stream, k.pos);
    count = r[0];
    var at = r[1], cell = -1;
    for (j = 0; j < count; j++) {
      r = readVarint(stream, at);
      cell += r[0] + 1;
      out[cell] = stream[r[1]];
      at = r[1] + 1;
    }
    return out;
  };

  // A side group's unknown at tick i (the optional `unknown_a` / `unknown_b` streams: 1 where an enemy of
  // that group could be; docs/map-control-unknown-plan.md); null when the row has no such stream. One
  // slot in the coverage/control mask format, with its own checkpoint offsets.
  Cursor.prototype.unknown = function (name, i) {
    var stream = this.p[name], cps = this.p.header.unknown_checkpoints;
    if (!stream || !cps) return null;
    var col = name === "unknown_a" ? 1 : 2, cells = this.p.cells, width = (cells + 7) >> 3, best = 0;
    for (var c = 0; c < cps.length && cps[c][0] <= i; c++) best = c;
    this.uk = this.uk || {};
    var m = this.uk[name];
    if (!m || m.tick > i || cps[best][0] > m.tick + 1) {
      m = this.uk[name] = { tick: cps[best][0] - 1, pos: cps[best][col], cur: m ? m.cur : new Uint8Array(cells) };
    }
    while (m.tick < i) {
      var tick = m.tick + 1, pos = m.pos;
      if (this.isCp[tick] !== undefined) {
        for (var k = 0; k < cells; k++) m.cur[k] = stream[pos + (k >> 3)] >> (7 - (k & 7)) & 1;
        pos += width;
      } else {
        var r = readVarint(stream, pos), count = r[0], cell = -1;
        pos = r[1];
        for (var j = 0; j < count; j++) {
          r = readVarint(stream, pos);
          pos = r[1];
          cell += r[0] + 1;
          m.cur[cell] ^= 1;
        }
      }
      m.pos = pos;
      m.tick = tick;
    }
    return m.cur;
  };

  // Each enemy slot's sightings by a group ({slot: [[t0, t1, u, v], ...]}): the ones lost within
  // `fade` seconds before t, as {slot, u, v, age} (a just-lost enemy's last-seen point).
  function lostAt(sightings, t, fade) {
    var out = [];
    Object.keys(sightings || {}).forEach(function (slot) {
      var runs = sightings[slot], now = null, last = null;
      for (var i = 0; i < runs.length; i++) {
        if (runs[i][0] <= t && t <= runs[i][1]) now = runs[i];
        if (runs[i][1] < t) last = runs[i];
      }
      if (!now && last && t - last[1] <= fade) out.push({ slot: Number(slot), u: last[2], v: last[3], age: t - last[1] });
    });
    return out;
  }

  // One slot's coverage or control mask at tick i (a Uint8Array of 0/1 over the walkable cells).
  Cursor.prototype.mask = function (name, slot, i) {
    var key = name + ":" + slot, m = this.mk[key], stream = this.p[name], cells = this.p.cells;
    var width = (cells + 7) >> 3, col = name === "coverage" ? 2 : 3, cp = this.cps[this.checkpointBefore(i)];
    if (!m || m.tick > i || cp[0] > m.tick + 1) {
      m = this.mk[key] = { tick: cp[0] - 1, pos: cp[col], cur: m ? m.cur : new Uint8Array(cells) };
    }
    while (m.tick < i) {
      var tick = m.tick + 1, pos = m.pos, full = this.isCp[tick] !== undefined;
      for (var q = 0; q < SLOTS; q++) {
        if (full) {
          if (q === slot) {
            for (var c = 0; c < cells; c++) m.cur[c] = stream[pos + (c >> 3)] >> (7 - (c & 7)) & 1;
          }
          pos += width;
        } else {
          var r = readVarint(stream, pos), count = r[0], cell = -1;
          pos = r[1];
          for (var k = 0; k < count; k++) {
            r = readVarint(stream, pos);
            pos = r[1];
            if (q === slot) { cell += r[0] + 1; m.cur[cell] ^= 1; }
          }
        }
      }
      m.pos = pos;
      m.tick = tick;
    }
    return m.cur;
  };

  // ------------------------------------------------------------ the cache

  // Decoded control per round, bounded: `load(n, signal)` answers {status: "ok", buffer, stale} or
  // {status: "not_ready" | "failed" | ...}. Only an ok answer is kept, so returning to a round that
  // wasn't ready asks again. `keep(rounds)` drops (and aborts) every other round; past `limit` the
  // least recently asked goes. `get(n)` is a promise of {status, parsed, cursor, stale}.
  function ControlCache(load, limit) {
    this.load = load;
    this.limit = limit || 3;
    this.entries = {};
    this.order = [];
  }

  ControlCache.prototype.drop = function (n) {
    var entry = this.entries[n];
    if (!entry) return;
    if (!entry.done && entry.controller) entry.controller.abort();
    delete this.entries[n];
    this.order = this.order.filter(function (m) { return m !== n; });
  };

  ControlCache.prototype.keep = function (rounds) {
    var self = this, wanted = {};
    rounds.forEach(function (n) { if (n !== undefined && n !== null) wanted[n] = true; });
    Object.keys(this.entries).forEach(function (key) { if (!wanted[key]) self.drop(Number(key)); });
  };

  ControlCache.prototype.ready = function (n) {
    var entry = this.entries[n];
    return entry && entry.value ? entry.value : null;
  };

  ControlCache.prototype.get = function (n) {
    var self = this, entry = this.entries[n];
    this.order = this.order.filter(function (m) { return m !== n; });
    this.order.push(n);
    if (entry) return entry.promise;
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    entry = this.entries[n] = { controller: controller, value: null };
    entry.promise = Promise.resolve()
      .then(function () { return self.load(n, controller ? controller.signal : undefined); })
      .then(function (answer) {
        entry.done = true;
        if (!(answer && answer.status === "ok")) return { status: (answer && answer.status) || "unavailable" };
        var parsed = parse(answer.buffer);
        return { status: "ok", stale: !!answer.stale, parsed: parsed, cursor: new Cursor(parsed) };
      })
      .then(null, function (err) {
        entry.done = true;
        return { status: err && err.name === "AbortError" ? "aborted" : "error" };
      })
      .then(function (value) {
        if (self.entries[n] === entry) {
          if (value.status === "ok") entry.value = value; else self.drop(n);
        }
        return value;
      });
    while (this.order.length > this.limit) this.drop(this.order[0]);
    return entry.promise;
  };

  // ------------------------------------------------------------ painting

  function hexRgb(color) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(color || "").trim());
    if (m) {
      var n = parseInt(m[1], 16);
      return [n >> 16 & 255, n >> 8 & 255, n & 255];
    }
    m = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i.exec(String(color || ""));
    return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [187, 187, 187];
  }

  // The plan's opacities: active 55%, safe 30%, passive 15%; contested stripes at the active
  // intensity when a cone is on the cell (`contested_active`), lighter otherwise.
  var LEVEL_ALPHA = { active: 0.55, safe: 0.30, passive: 0.15 };
  var CONTESTED_ALPHA = { contested_active: 0.55, contested: 0.30 };
  var STRIPE_PX = 4;   // stripe width in the painted image's pixels

  // What each state code paints: {team: "a" | "b", alpha} or {contested: true, alpha} or null.
  var STATE_PAINT = STATE_NAMES.map(function (name) {
    if (name === "none") return null;
    if (CONTESTED_ALPHA[name] !== undefined) return { contested: true, alpha: CONTESTED_ALPHA[name] };
    var parts = name.split("_");
    return { team: parts[0], alpha: LEVEL_ALPHA[parts[1]] };
  });

  // Paints tick states into an RGBA image of size x size (a multiple of GRID) over the whole
  // minimap: colours = {a: [r, g, b], b: [r, g, b]} (side group A's and B's team colours).
  function paintStates(rgba, size, walk, codes, colors) {
    var px = size / GRID;
    rgba.fill(0);
    for (var k = 0; k < walk.length; k++) {
      var paint = STATE_PAINT[codes[k]];
      if (!paint) continue;
      var cell = walk[k], cx = (cell % GRID) * px, cy = Math.floor(cell / GRID) * px;
      var a = Math.round(paint.alpha * 255);
      for (var y = cy; y < cy + px; y++) {
        for (var x = cx; x < cx + px; x++) {
          var rgb = paint.contested ? (Math.floor((x + y) / STRIPE_PX) % 2 ? colors.b : colors.a) : colors[paint.team];
          var o = (y * size + x) * 4;
          rgba[o] = rgb[0]; rgba[o + 1] = rgb[1]; rgba[o + 2] = rgb[2]; rgba[o + 3] = a;
        }
      }
    }
    return rgba;
  }

  // Each side group's unknown as a hatch in the enemy's colour (docs/map-control-unknown-plan.md): A's
  // unknown in B's colour along "/" lines, B's in A's colour along "\" lines, so a cell in both teams'
  // unknown is cross-hatched. `show` is "A", "B" or "both"; a or b may be null (no stream).
  var HATCH_PX = 4, HATCH_ALPHA = 0.55;

  function paintUnknown(rgba, size, walk, a, b, colors, show) {
    var px = size / GRID, alpha = Math.round(HATCH_ALPHA * 255);
    rgba.fill(0);
    for (var k = 0; k < walk.length; k++) {
      var inA = !!(a && a[k]) && show !== "B", inB = !!(b && b[k]) && show !== "A";
      if (!inA && !inB) continue;
      var cell = walk[k], cx = (cell % GRID) * px, cy = Math.floor(cell / GRID) * px;
      for (var y = cy; y < cy + px; y++) {
        for (var x = cx; x < cx + px; x++) {
          var rgb = null;
          if (inA && (x + y) % HATCH_PX === 0) rgb = colors.b;
          else if (inB && ((x - y) % HATCH_PX + HATCH_PX) % HATCH_PX === 0) rgb = colors.a;
          if (!rgb) continue;
          var o = (y * size + x) * 4;
          rgba[o] = rgb[0]; rgba[o + 1] = rgb[1]; rgba[o + 2] = rgb[2]; rgba[o + 3] = alpha;
        }
      }
    }
    return rgba;
  }

  // One player's highlight: their control cells filled (60%), and the edge of their coverage-only
  // cells outlined lighter (the pixels of a covered cell next to an uncovered one).
  var HIGHLIGHT_FILL = 0.6, HIGHLIGHT_EDGE = 0.9;

  function paintHighlight(rgba, size, walk, control, coverage, rgb) {
    var px = size / GRID, covered = new Uint8Array(GRID * GRID);
    rgba.fill(0);
    for (var k = 0; k < walk.length; k++) if (coverage[k] || control[k]) covered[walk[k]] = 1;
    for (k = 0; k < walk.length; k++) {
      var cell = walk[k], gx = cell % GRID, gy = Math.floor(cell / GRID), cx = gx * px, cy = gy * px;
      if (control[k]) {
        fillRect(rgba, size, cx, cy, px, px, rgb, Math.round(HIGHLIGHT_FILL * 255));
      } else if (coverage[k]) {
        var edge = [[0, -1], [0, 1], [-1, 0], [1, 0]];
        for (var e = 0; e < 4; e++) {
          var nx = gx + edge[e][0], ny = gy + edge[e][1];
          if (nx >= 0 && ny >= 0 && nx < GRID && ny < GRID && covered[ny * GRID + nx]) continue;
          var w = edge[e][0] ? 1 : px, h = edge[e][1] ? 1 : px;
          var x0 = edge[e][0] === 1 ? cx + px - 1 : cx, y0 = edge[e][1] === 1 ? cy + px - 1 : cy;
          fillRect(rgba, size, x0, y0, w, h, [255, 255, 255], Math.round(HIGHLIGHT_EDGE * 255));
        }
      }
    }
    return rgba;
  }

  function fillRect(rgba, size, x0, y0, w, h, rgb, a) {
    for (var y = y0; y < y0 + h; y++) {
      for (var x = x0; x < x0 + w; x++) {
        var o = (y * size + x) * 4;
        rgba[o] = rgb[0]; rgba[o + 1] = rgb[1]; rgba[o + 2] = rgb[2]; rgba[o + 3] = a;
      }
    }
  }

  // The match heatmap (Stage 5): shares are 0-255 per walkable cell (heatmap.json's x, y and
  // contested). Mode "lead" paints each cell in the colour of whichever holds it longest (contested:
  // stripes of both), opacity by that share; "x", "y" or "contested" paints that one share alone.
  var HEAT_MAX_ALPHA = 0.85;

  function paintHeatmap(rgba, size, walk, shares, mode, colors) {
    var px = size / GRID;
    rgba.fill(0);
    for (var k = 0; k < walk.length; k++) {
      var x = shares.x[k], y = shares.y[k], c = shares.contested[k], v, which;
      if (mode === "lead") {
        v = Math.max(x, y, c);
        which = v === 0 ? null : c === v ? "contested" : x >= y ? "x" : "y";
      } else {
        v = shares[mode][k];
        which = v ? mode : null;
      }
      if (!which) continue;
      var a = Math.round(HEAT_MAX_ALPHA * v), cell = walk[k];
      var cx = (cell % GRID) * px, cy = Math.floor(cell / GRID) * px;
      for (var yy = cy; yy < cy + px; yy++) {
        for (var xx = cx; xx < cx + px; xx++) {
          var rgb = which === "contested" ? (Math.floor((xx + yy) / STRIPE_PX) % 2 ? colors.y : colors.x) : colors[which];
          var o = (yy * size + xx) * 4;
          rgba[o] = rgb[0]; rgba[o + 1] = rgb[1]; rgba[o + 2] = rgb[2]; rgba[o + 3] = a;
        }
      }
    }
    return rgba;
  }

  // Map cell (row-major GRID x GRID) -> its index among the walkable cells, or -1.
  function cellIndex(walk) {
    var out = new Int32Array(GRID * GRID).fill(-1);
    for (var k = 0; k < walk.length; k++) out[walk[k]] = k;
    return out;
  }

  // Side group -> "team-1" / "team-2", from the linked players ({slot: {side, team}}).
  function groupTeams(players) {
    var out = {};
    Object.keys(players || {}).forEach(function (slot) {
      var p = players[slot];
      if (p && p.side && p.team && !out[p.side]) out[p.side] = p.team;
    });
    return out;
  }

  var api = {
    STATE_NAMES: STATE_NAMES, GRID: GRID, SLOTS: SLOTS, readVarint: readVarint, parse: parse, walkCells: walkCells,
    tickAt: tickAt, Cursor: Cursor, ControlCache: ControlCache, lostAt: lostAt, hexRgb: hexRgb, paintStates: paintStates, paintHighlight: paintHighlight,
    groupTeams: groupTeams, base64Bytes: base64Bytes, paintHeatmap: paintHeatmap, cellIndex: cellIndex,
    HEAT_MAX_ALPHA: HEAT_MAX_ALPHA, LEVEL_ALPHA: LEVEL_ALPHA, CONTESTED_ALPHA: CONTESTED_ALPHA,
    STRIPE_PX: STRIPE_PX, paintUnknown: paintUnknown, HATCH_PX: HATCH_PX, HATCH_ALPHA: HATCH_ALPHA
  };
  global.ReplayControl = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

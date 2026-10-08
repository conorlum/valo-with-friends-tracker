/*
 * The height viewer's model (scripts/height_viewer.py; docs/superpowers/plans/2026-10-05-height-viewer.md).
 * Pure functions, inlined into the page and tested in node (tests/replays/test_height_viewer.py) against the
 * height build's own rules (app/control/height_build.py: `picture` for colours and drops).
 * Heights in a payload are whole decimetres above the map's lowest floor, -1 for none, cell-major
 * (cell * max_floors + floor); everything returned here is in metres.
 */
(function (global) {
  "use strict";
  var GRID = 128, CELL = 8, PX = 1024;
  var SAME_M = 0.3;   // at most this far apart reads "same height" (a display choice)
  var BIG_M = 1.5;    // this far apart or more reads "much higher/lower" (a display choice)

  function rleDecode(runs, n) {
    var out = new Uint8Array(n), pos = 0;
    for (var i = 0; i < runs.length; i += 2) {
      if (runs[i]) out.fill(runs[i], pos, pos + runs[i + 1]);
      pos += runs[i + 1];
    }
    return out;
  }

  function prepare(p) {
    var n = GRID * GRID;
    return {maxFloors: p.max_floors, floors: Int16Array.from(p.floors), spread: Int16Array.from(p.spread),
            supported: rleDecode(p.supported, n), unresolved: rleDecode(p.unresolved, n),
            walk: rleDecode(p.walk, n), kind: p.kinds ? rleDecode(p.kinds, n) : null, raw: p};
  }

  function floorsOf(map, cell) {
    var out = [];
    for (var k = 0; k < map.maxFloors; k++) {
      var i = cell * map.maxFloors + k, dm = map.floors[i];
      if (dm >= 0) out.push({floor: k, z: dm / 10, spread: map.spread[i] / 10});
    }
    return out;
  }

  function ground(map, cell) { return map.floors[cell * map.maxFloors]; }

  // How a cell got its ground height (app/control/heights.py KIND_*). Without kinds (an older payload), what
  // `supported` says.
  var KIND_WORDS = ["no height", "from stands", "from walks alone", "filled from neighbours", "filled along a gradient"];

  function kindWord(map, cell) {
    if (!map.kind) return map.supported[cell] ? "supported" : "filled from neighbours";
    return KIND_WORDS[map.kind[cell]] || "kind " + map.kind[cell];
  }

  function compare(refZ, z) {
    var d = Math.round((z - refZ) * 10) / 10, a = Math.abs(d);
    var word = a <= SAME_M ? "same height" : (a < BIG_M ? "a little " : "much ") + (d > 0 ? "higher" : "lower");
    return {delta: d === 0 ? 0 : d, word: word};
  }

  function sameMask(map, refZ, tolM) {
    var out = new Uint8Array(GRID * GRID);
    for (var c = 0; c < GRID * GRID; c++) {
      for (var k = 0; k < map.maxFloors; k++) {
        var dm = map.floors[c * map.maxFloors + k];
        if (dm >= 0 && Math.round(Math.abs(dm / 10 - refZ) * 10) / 10 <= tolM) { out[c] = 1; break; }
      }
    }
    return out;
  }

  function drops(map, stepM) {
    var out = [], step = stepM * 10;
    for (var cy = 0; cy < GRID; cy++) {
      for (var cx = 0; cx < GRID; cx++) {
        var c = cy * GRID + cx, g = ground(map, c);
        if (g < 0) continue;
        if (cx + 1 < GRID && ground(map, c + 1) >= 0 && Math.abs(g - ground(map, c + 1)) > step) out.push([c, "e"]);
        if (cy + 1 < GRID && ground(map, c + GRID) >= 0 && Math.abs(g - ground(map, c + GRID)) > step) out.push([c, "s"]);
      }
    }
    return out;
  }

  function groundTop(map) {
    var top = -1;
    for (var c = 0; c < GRID * GRID; c++) top = Math.max(top, ground(map, c));
    return Math.max(top, 1);
  }

  function colour(dm, top) {
    var f = Math.min(Math.max(dm / top, 0), 1);
    return [Math.floor(40 + 215 * f), Math.floor(90 + 150 * f), Math.floor(200 - 170 * f)];
  }

  function cellAt(x, y) {
    var cx = Math.min(GRID - 1, Math.max(0, Math.floor(x / CELL)));
    var cy = Math.min(GRID - 1, Math.max(0, Math.floor(y / CELL)));
    return cy * GRID + cx;
  }

  var api = {GRID: GRID, CELL: CELL, PX: PX, SAME_M: SAME_M, BIG_M: BIG_M, rleDecode: rleDecode, prepare: prepare,
             floorsOf: floorsOf, ground: ground, compare: compare, sameMask: sameMask, drops: drops,
             groundTop: groundTop, colour: colour, cellAt: cellAt, kindWord: kindWord, KIND_WALKS: 2,
             KIND_GRADIENT: 4};
  global.HeightCore = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

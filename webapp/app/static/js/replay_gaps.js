/*
 * Timing gaps in the replay viewer (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 8;
 * docs/superpowers/plans/2026-10-04-timing-gaps-viewer.md, section 3). No build step and no dependencies,
 * like replay.js and replay_control.js.
 *
 * Reads the rows app/services/replay_gaps_view.py serves (a stored gap row plus spot_xy, victim_xy,
 * released_xy, used, merged) and the map's chokes ({"<id>": {name, x, y}}); every position is in pixels on
 * the 1024-px map. Pure functions, tested in node (tests/replays/test_gaps_viewer.py); the drawing hooks
 * live in replay.js.
 */
(function (global) {
  "use strict";

  var MAP_PX = 1024;
  var BACKSHOT_SHOW_S = 3;            // a back-shot stays on the map this long after its damage run starts
  var REAR_HALF = Math.PI / 3;        // the rear arc is 120 degrees: 60 either side of straight back
  var TWO_PI = 2 * Math.PI;

  // The predicted reasons a teammate stopped watching a cell (app/gaps/detect.py REASONS), in plain words.
  var REASON_WORDS = {
    died: "died",
    turned: "turned away",
    moved: "moved off it",
    blinded: "was blinded",
    smoked: "was smoked off it",
    utility_expired: "their utility ended",
    utility_left: "stopped using their camera or drone",
    other: "for another reason"
  };

  function isBackshot(row) { return row.kind === "backshot"; }

  // The rows to draw at time t: a predicted gap from t_open to t_close inclusive (flickers only when
  // asked for), a back-shot for BACKSHOT_SHOW_S after its damage run starts.
  function openAt(rows, t, showFlickers) {
    return (rows || []).filter(function (row) {
      if (isBackshot(row)) return row.t_open <= t && t <= row.t_open + BACKSHOT_SHOW_S;
      if (row.flicker && !showFlickers) return false;
      var close = typeof row.t_close === "number" ? row.t_close : row.t_open;
      return row.t_open <= t && t <= close;
    });
  }

  // The round's list: every row in opening order (seq breaks a tie), flickers only when asked for.
  function listRows(rows, showFlickers) {
    return (rows || []).filter(function (row) { return showFlickers || !row.flicker || isBackshot(row); })
      .slice().sort(function (a, b) { return a.t_open - b.t_open || a.seq - b.seq; });
  }

  // "used" (an enemy stood, shot or killed), "unused", or "backshot": how a row is drawn and labelled.
  function markOf(row) {
    if (isBackshot(row)) return "backshot";
    return row.used ? "used" : "unused";
  }

  function chokeName(id, chokes) {
    var c = chokes && chokes[String(id)];
    return c && c.name ? String(c.name) : "choke " + id;
  }

  function names(slots, nameOf) {
    return (slots || []).map(function (s) { return nameOf(s); }).join(", ");
  }

  function seconds(value) {
    return typeof value === "number" ? value.toFixed(1) + " s" : "?";
  }

  // The plain-words strings for one row: the list's cells and the map's tooltip.
  function describe(row, nameOf, chokes) {
    var ctx = row.context || {};
    var victim = nameOf(row.victim_slot);
    var when = seconds(typeof ctx.t_round === "number" ? ctx.t_round : row.t_open);
    var route;
    if (row.choke_seq === null || row.choke_seq === undefined) route = "unknown (the path has a break)";
    else if (!row.choke_seq.length) route = "no choke";
    else route = row.choke_seq.map(function (id) { return chokeName(id, chokes); }).join(" → ");
    var could = names(row.candidate_slots, nameOf) || "nobody known";
    var why, happened;
    if (isBackshot(row)) {
      var shooter = (row.candidate_slots || []).length ? nameOf(row.candidate_slots[0]) : "an unknown enemy";
      why = "the shooter had gone unseen by " + victim + "'s team for 5 s or more";
      happened = "shot from behind by " + shooter + (ctx.wall ? " (through a wall)" : "") +
        (typeof row.killed_at === "number" ? " · killed them" : "");
    } else {
      var detail = row.cause_detail || {};
      if (row.cause === "route_released") {
        var who = detail.player === null || detail.player === undefined ? "a teammate" : nameOf(detail.player);
        var reason = REASON_WORDS[detail.reason] || String(detail.reason || "for a reason not recorded");
        why = "a teammate stopped watching the route (" + who + (detail.by === "utility" ? "'s utility" : "") +
          ", " + reason + ")";
      } else if (row.cause === "victim_turned") why = victim + " turned away";
      else if (row.cause === "victim_moved") why = victim + " moved into the open";
      else if (row.cause === "open_timing") why = "enough time passed (no one ever watched the route)";
      else why = String(row.cause || "not recorded");
      if (row.used === "killed") happened = "killed from behind by " + nameOf(row.killed_by);
      else if (row.used === "shot") happened = "shot from behind by " + nameOf(row.shot_by);
      else if (row.used === "stood") happened = "an enemy stood there (" + nameOf(row.stood_by) + ")";
      else happened = "nothing (unused)";
      if (row.merged > 0) happened += " · merged " + row.merged;
    }
    return {
      kind: isBackshot(row) ? "back-shot" : row.flicker ? "predicted (flicker)" : "predicted",
      when: when, who: victim, could: could, route: route, why: why, happened: happened
    };
  }

  // The map tooltip: one line from describe().
  function summary(row, nameOf, chokes) {
    var d = describe(row, nameOf, chokes);
    if (isBackshot(row)) {
      return "Back-shot at " + d.when + " on " + d.who + ": " + d.happened + " · " + d.why + " · route: " + d.route;
    }
    return "Gap at " + d.when + ": " + d.who + "'s back was open · could have been " + d.could + " · route: " +
      d.route + " · why: " + d.why + " · what happened: " + d.happened;
  }

  // The rear 120 degrees as [start, end] canvas radians, start in [0, 2pi) and end = start + 2pi/3. Yaw is
  // in canvas orientation (degrees, 0 = +x, 90 = +y), as replay.js draws the view cones.
  function rearArc(yaw) {
    var back = yaw * Math.PI / 180 + Math.PI;
    var start = ((back - REAR_HALF) % TWO_PI + TWO_PI) % TWO_PI;
    return [start, start + 2 * REAR_HALF];
  }

  // A [x, y] (or a route point's [t, x, y]: pass point.slice(1)) on the 1024-px map -> canvas px.
  function toCanvas(xy, size) {
    return [xy[0] / MAP_PX * size, xy[1] / MAP_PX * size];
  }

  var api = {
    MAP_PX: MAP_PX, BACKSHOT_SHOW_S: BACKSHOT_SHOW_S, REASON_WORDS: REASON_WORDS, openAt: openAt,
    listRows: listRows, markOf: markOf, chokeName: chokeName, describe: describe, summary: summary,
    rearArc: rearArc, toCanvas: toCanvas
  };
  global.ReplayGaps = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

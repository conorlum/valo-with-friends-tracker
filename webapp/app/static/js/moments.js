/*
 * Critical Moments tab of the replay viewer: every fight one player was in, grouped by the tags
 * app/moments/fights.py measured. Reads /replays/<uuid>/moments.json (app/services/replay_moments.py). The tags
 * are measurements, not verdicts. No build step and no dependencies. Pure functions are tested in node
 * (tests/replays/test_moments_viewer.py); render() is the only part that touches the DOM.
 */
(function (global) {
  "use strict";

  var GROUPS = [
    { key: "moving", tag: "moving", label: "Moving while firing" },
    { key: "alone", tag: "alone", label: "Alone" },
    { key: "with_teammate", tag: "with_teammate", label: "With a teammate" },
    { key: "died_not_traded", tag: "died_not_traded", label: "Died not traded" },
    { key: "died_traded", tag: "died_traded", label: "Traded" },
    { key: "traded_teammate", tag: "traded_teammate", label: "Traded a teammate" },
    { key: "util_on_them", tag: "util_on_them", label: "Utility on them" },
    { key: "util_near_them", tag: "util_near_them", label: "Utility near them" },
    { key: "util_on_you", tag: "util_on_you", label: "Utility on you" },
    { key: "mollied", tag: "mollied", label: "Mollied off a spot" },
    { key: "saw_first", tag: "saw_first", label: "Saw them first" },
    { key: "seen_first", tag: "seen_first", label: "Seen first" },
    { key: "caught_off_angle", tag: "caught_off_angle", label: "Caught off-angle" },
    { key: "died_unseen", tag: "died_unseen", label: "Died without seeing the killer" },
    { key: "killed_unseen", tag: "killed_unseen", label: "Killed unseen" },
    { key: "through_smoke", tag: "through_smoke", label: "Through smoke" },
    { key: "heard", tag: null, label: "Heard" },
    { key: "found_by_utility", tag: null, label: "Found by utility" }
  ];

  var BANDS = ["all", "on", "near", "off"];
  var JUMP_LEAD_S = 5;

  function keysOf(obj) { return Object.keys(obj || {}); }

  function defaultSlot(payload, viewerSlot) {
    var players = payload.players || {};
    if (viewerSlot !== null && viewerSlot !== undefined && players[String(viewerSlot)]) return Number(viewerSlot);
    var attack = (payload.attack_round1 || []).slice().sort(function (a, b) { return a - b; });
    if (attack.length) return attack[0];
    var slots = keysOf(players).map(Number).sort(function (a, b) { return a - b; });
    return slots.length ? slots[0] : null;
  }

  function fightsFor(payload, slot) {
    var out = [];
    keysOf(payload.rounds).forEach(function (n) {
      ((payload.rounds[n] || {}).fights || []).forEach(function (f) {
        if (f.slot === slot) out.push(Object.assign({}, f, { round: Number(n) }));
      });
    });
    return out.sort(function (a, b) { return a.round - b.round || a.t0 - b.t0; });
  }

  function infoRows(payload, slot, key) {
    var s = (payload.summary || {})[String(slot)];
    return ((s && s.info && s.info[key]) || []).slice().sort(function (a, b) { return a.round - b.round || a.t - b.t; });
  }

  function groupsFor(payload, slot) {
    var fights = fightsFor(payload, slot);
    return GROUPS.map(function (g) {
      var rows;
      if (g.tag) rows = fights.filter(function (f) { return (f.tags || []).indexOf(g.tag) >= 0; });
      else rows = infoRows(payload, slot, g.key);
      return { key: g.key, label: g.label, rows: rows };
    });
  }

  // m:ss of a round time (never negative), as replay.js's lists show it.
  function clock(t) {
    var whole = Math.floor(Math.max(0, t));
    var sec = whole % 60;
    return Math.floor(whole / 60) + ":" + (sec < 10 ? "0" : "") + sec;
  }

  function nameOf(players, slot) {
    var p = (players || {})[String(slot)];
    return p && p.name ? p.name : "slot " + slot;
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  function outcome(row) {
    var kills = (row.kills || []).length;
    var parts = [];
    if (kills) parts.push(plural(kills, "kill"));
    if (row.died) parts.push("died");
    var text = parts.length ? parts.join(", ") : "no kill, no death";
    if (row.died && row.trade && row.trade.died === "traded") text += " (traded)";
    return text;
  }

  function angles(row) {
    var d = (row.duels || [])[0];
    if (!d) return "";
    var bits = [];
    if (typeof d.you_angle === "number") bits.push("you " + d.you_angle + "°");
    if (typeof d.they_angle === "number") bits.push("they " + d.they_angle + "°");
    return bits.join(", ");
  }

  function groupNumbers(row, key) {
    var mates = row.mates || {};
    switch (key) {
      case "moving":
        var m = row.moving || {};
        return m.measured ? m.above + " of " + m.measured + " shots moving" : "";
      case "alone":
        return typeof mates.nearest_m === "number" ? "nearest teammate " + mates.nearest_m + " m" : "no teammate alive";
      case "with_teammate":
        return typeof mates.with_m === "number" ? "teammate " + mates.with_m + " m" : "";
      case "died_traded":
        return row.trade && typeof row.trade.after_s === "number" ? "traded after " + row.trade.after_s + " s" : "";
      case "util_on_them": return "utility " + (row.util_on || []).length;
      case "util_near_them": return "utility " + (row.util_near || []).length;
      case "util_on_you": return "utility " + (row.util_on_you || []).length;
      case "mollied": return "molly " + (row.mollied || []).length;
      case "saw_first":
      case "seen_first":
      case "caught_off_angle":
        return angles(row);
      default: return "";
    }
  }

  function rowText(row, group, players) {
    var key = typeof group === "string" ? group : group.key;
    if (key === "heard" || key === "found_by_utility") {
      return "R" + row.round + " " + clock(row.t) + (key === "found_by_utility" && row.reason ? " " + row.reason : "");
    }
    var parts = ["R" + row.round + " " + clock(row.t0)];
    var names = (row.opponents || []).map(function (s) { return nameOf(players, s); });
    if (names.length) parts.push("vs " + names.join(", "));
    parts.push(outcome(row));
    var nums = groupNumbers(row, key);
    if (nums) parts.push(nums);
    if (row.died && typeof row.died.control_m2 === "number") {
      var text = "team lost " + Math.round(row.died.control_m2) + " m²";
      if (typeof row.died.share === "number") text += " (" + Math.round(row.died.share * 100) + "%)";
      parts.push(text);
    }
    return parts.join(" · ");
  }

  function sec(v) { return typeof v === "number" ? v.toFixed(2) + " s" : "–"; }

  function summaryLine(summary) {
    var medians = (summary && summary.medians) || {};
    var bands = BANDS.map(function (b) {
      var m = medians[b] || {};
      return b + " " + sec(m.ttk_shot) + " (n " + (m.n || 0) + ")";
    });
    var info = (summary && summary.info) || {};
    var count = function (k) { return (info[k] || []).length; };
    return "TTK from first shot: " + bands.join(", ") + ". heard " + count("heard") + ", gunfire outside fights " + count("gunfire_outside") +
      ", found by utility " + count("found_by_utility") + ", seen " + (info.seen || 0);
  }

  // [round, t] to show for a row: five seconds before a fight starts, the moment itself for deaths and info.
  function jumpFor(row) {
    if (typeof row.t0 === "number") return [row.round, Math.max(0, row.t0 - JUMP_LEAD_S)];
    return [row.round, Math.max(0, row.t)];
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function jumpButton(text, round, t, target, viewer) {
    var li = el("li", "replay-moment-row");
    var b = el("button", "replay-moment-button", text);
    b.type = "button";
    b.setAttribute("data-moment-round", String(round));
    b.setAttribute("data-moment-t", String(t));
    b.addEventListener("click", function () { viewer.showRound(target[0], target[1]); });
    li.appendChild(b);
    return li;
  }

  function render(panel, payload, slot, viewer) {
    panel.textContent = "";
    var players = payload.players || {};
    var select = el("select", "replay-moments-picker");
    select.setAttribute("aria-label", "Player");
    keysOf(players).sort(function (a, b) { return a - b; }).forEach(function (s) {
      var o = el("option", "", nameOf(players, s));
      o.value = s;
      if (Number(s) === slot) o.selected = true;
      select.appendChild(o);
    });
    select.addEventListener("change", function () { render(panel, payload, Number(select.value), viewer); });
    panel.appendChild(select);
    var summary = (payload.summary || {})[String(slot)] || {};
    if (payload.stale > 0) panel.appendChild(el("p", "replay-side-note", payload.stale + " rounds not computed yet"));
    panel.appendChild(el("p", "replay-side-note", summaryLine(summary)));
    panel.appendChild(el("h3", "replay-moments-heading", "Most expensive deaths"));
    var deaths = el("ol", "replay-feed");
    (summary.expensive_deaths || []).forEach(function (d) {
      var text = "R" + d.round + " " + clock(d.t) + " · team lost " + Math.round(d.control_m2) + " m²" +
        (typeof d.share === "number" ? " (" + Math.round(d.share * 100) + "%)" : "");
      deaths.appendChild(jumpButton(text, d.round, d.t, jumpFor({ round: d.round, t: d.t }), viewer));
    });
    panel.appendChild(deaths);
    groupsFor(payload, slot).forEach(function (g) {
      panel.appendChild(el("h3", "replay-moments-heading", g.label + " (" + g.rows.length + ")"));
      var list = el("ol", "replay-feed");
      g.rows.forEach(function (row) {
        var t = typeof row.t0 === "number" ? row.t0 : row.t;
        list.appendChild(jumpButton(rowText(row, g.key, players), row.round, t, jumpFor(row), viewer));
      });
      panel.appendChild(list);
    });
  }

  var api = {
    GROUPS: GROUPS, defaultSlot: defaultSlot, fightsFor: fightsFor, groupsFor: groupsFor, rowText: rowText,
    summaryLine: summaryLine, jumpFor: jumpFor, clock: clock, render: render
  };
  global.ReplayMoments = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

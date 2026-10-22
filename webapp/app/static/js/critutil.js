/*
 * Critical Util tab of the replay viewer: every cast one player made, with what it did and what it was worth
 * (the change in their side's chance to win the round, percentage points). Reads /replays/<uuid>/util.json
 * (app/services/replay_util.py). Measurements first, the value beside them. No build step and no dependencies.
 * Pure functions are tested in node (tests/critutil/test_util_viewer.py); render() alone touches the DOM.
 */
(function (global) {
  "use strict";

  var KINDS = [
    { key: "ult", label: "Ults" }, { key: "blind", label: "Flashes" }, { key: "smoke", label: "Smokes and walls" },
    { key: "damaging", label: "Damage (mollies, nades)" }, { key: "status", label: "Slows, stuns, nearsight" },
    { key: "info", label: "Recon" }, { key: "device", label: "Traps and devices" }, { key: "heal", label: "Heals" },
    { key: "other", label: "Other" }
  ];
  var JUMP_LEAD_S = 2;
  var HOW = { blind: "blinded", nearsight: "nearsighted", reveal: "revealed", damage: "damaged" };

  function keysOf(obj) { return Object.keys(obj || {}); }

  function defaultSlot(payload, viewerSlot) {
    var players = payload.players || {};
    if (viewerSlot !== null && viewerSlot !== undefined && players[String(viewerSlot)]) return Number(viewerSlot);
    var slots = keysOf(players).map(Number).sort(function (a, b) { return a - b; });
    return slots.length ? slots[0] : null;
  }

  function castsFor(payload, slot) {
    var out = [];
    keysOf(payload.rounds).forEach(function (n) {
      ((payload.rounds[n] || {}).casts || []).forEach(function (c) {
        if (c.slot === slot) out.push(Object.assign({}, c, { round: Number(n) }));
      });
    });
    return out.sort(function (a, b) { return a.round - b.round || a.t - b.t; });
  }

  function unusedFor(payload, slot) {
    var out = [];
    keysOf(payload.rounds).forEach(function (n) {
      ((payload.rounds[n] || {}).unused || []).forEach(function (u) {
        if (u.slot === slot) out.push(Object.assign({}, u, { round: Number(n) }));
      });
    });
    return out.sort(function (a, b) { return a.round - b.round || a.t - b.t; });
  }

  function groupsFor(payload, slot) {
    var casts = castsFor(payload, slot);
    return KINDS.map(function (k) {
      return { key: k.key, label: k.label, rows: casts.filter(function (c) { return c.kind === k.key; }) };
    });
  }

  function clock(t) {
    var whole = Math.floor(Math.max(0, t));
    var sec = whole % 60;
    return Math.floor(whole / 60) + ":" + (sec < 10 ? "0" : "") + sec;
  }

  // Signed with an explicit glyph: a value is never told apart by colour alone.
  function signed(v) {
    if (typeof v !== "number") return "–";
    if (v > 0) return "+" + v.toFixed(2);
    if (v < 0) return "−" + (-v).toFixed(2);
    return "0.00";
  }

  function nameOf(players, slot) {
    var p = (players || {})[String(slot)];
    return p && p.name ? p.name : "slot " + slot;
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // What the cast did, in plain measurements.
  function facts(row, players) {
    var bits = [];
    var by = {};
    (row.effects || []).forEach(function (e) { (by[e.how] = by[e.how] || {})[e.target] = true; });
    keysOf(by).forEach(function (how) {
      var names = keysOf(by[how]).map(function (s) { return nameOf(players, s); });
      bits.push((HOW[how] || how) + " " + names.join(", "));
    });
    var ours = (row.kills || []).filter(function (k) { return k.ours; });
    var theirs = (row.kills || []).filter(function (k) { return !k.ours; });
    if (ours.length) bits.push(plural(ours.length, "kill") + " on affected enemies");
    if (theirs.length) bits.push(plural(theirs.length, "teammate") + " killed by an enemy it affected");
    if (row.followed === true) bits.push("followed up");
    else if (row.followed === false) bits.push("not followed up");
    var cover = row.cover || {};
    if (cover.pairs) bits.push("cut " + plural(cover.pairs, "sight line") + " (" + cover.blocked_s + " s)" +
      (cover.covered_lived && cover.covered_lived.length ? ", covered " + plural(cover.covered_lived.length, "teammate") + " who lived" : ""));
    var p = row.presence || {};
    if (p.held_back && p.held_back.length) {
      bits.push(p.held_back.length + (p.held_back.length === 1 ? " enemy" : " enemies") + " held back at its edge");
    }
    if (p.waiting_s) bits.push("enemies waited " + p.waiting_s + " s");
    var hold = row.hold || {};
    if (hold.front_s) bits.push("held the unknown back for " + Math.round(hold.front_s) + " s");
    if (hold.held_m2s) bits.push("held " + Math.round(hold.held_m2s / Math.max(hold.up_s || 1, 1)) + " m² no teammate watched");
    if (hold.crossed_s) bits.push("the unknown got past it for " + Math.round(hold.crossed_s) + " s");
    if (typeof row.trigger === "number") bits.push("triggered at " + clock(row.trigger));
    (row.harm || []).forEach(function (h) {
      bits.push((HOW[h.how] || h.how) + " teammate " + nameOf(players, h.mate) + (h.died ? " (died while affected)" : ""));
    });
    if (row.heal) bits.push("healed " + nameOf(players, row.heal.target) + (row.heal.saved ? ", kept them alive" : row.heal.fought ? ", before a fight" : ""));
    if (row.found_nobody) bits.push("found nobody (the area was empty)");
    if (row.wasted) bits.push("wasted: nothing came of it");
    return bits;
  }

  function rowText(row, players) {
    var parts = ["R" + row.round + " " + clock(row.t), row.ability];
    var f = facts(row, players);
    if (f.length) parts.push(f.join("; "));
    parts.push(signed(row.value) + " pp");
    return parts.join(" · ");
  }

  function unusedText(u) {
    if (u.kind === "ult_unused") return "R" + u.round + " " + clock(u.t) + " · full ult never used (" + u.points + " points)";
    return "R" + u.round + " " + clock(u.t) + " · died holding " + (u.abilities || u.kinds || []).join(", ") + " after engaging first";
  }

  function notes(payload) {
    var out = [];
    if (payload.stale > 0) out.push(payload.stale + " rounds not computed yet");
    if ((payload.failed || []).length) out.push("Util could not be computed for rounds " + payload.failed.join(", "));
    if (!payload.estimator) out.push("The win-odds estimator isn't built yet: kill values read 0 until it is");
    return out;
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function jumpButton(text, round, t, viewer, tone) {
    var li = el("li", "replay-moment-row");
    var b = el("button", "replay-moment-button" + (tone ? " " + tone : ""), text);
    b.type = "button";
    b.addEventListener("click", function () { viewer.showRound(round, Math.max(0, t - JUMP_LEAD_S)); });
    li.appendChild(b);
    return li;
  }

  function tone(v) { return typeof v !== "number" || v === 0 ? "" : v > 0 ? "util-good" : "util-bad"; }

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
    notes(payload).forEach(function (text) { panel.appendChild(el("p", "replay-side-note", text)); });
    var s = (payload.summary || {})[String(slot)] || {};
    panel.appendChild(el("p", "replay-side-note", plural(s.casts || 0, "cast") + ", " + signed(s.value) + " pp in all"));
    var casts = castsFor(payload, slot);
    var byKey = {};
    casts.forEach(function (c) { byKey[c.round + ":" + c.i] = c; });
    [["best", "Best util"], ["worst", "Worst util"]].forEach(function (pair) {
      panel.appendChild(el("h3", "replay-moments-heading", pair[1]));
      var list = el("ol", "replay-feed");
      (s[pair[0]] || []).forEach(function (b) {
        var row = byKey[b.round + ":" + b.i] || Object.assign({}, b);
        list.appendChild(jumpButton(rowText(row, players), b.round, b.t, viewer, tone(b.value)));
      });
      if (!list.children.length) list.appendChild(el("li", "replay-side-note", "none"));
      panel.appendChild(list);
    });
    var unused = unusedFor(payload, slot);
    panel.appendChild(el("h3", "replay-moments-heading", "Unused util (" + unused.length + ")"));
    var ul = el("ol", "replay-feed");
    unused.forEach(function (u) { ul.appendChild(jumpButton(unusedText(u), u.round, u.t, viewer)); });
    panel.appendChild(ul);
    groupsFor(payload, slot).forEach(function (g) {
      if (!g.rows.length) return;
      panel.appendChild(el("h3", "replay-moments-heading", g.label + " (" + g.rows.length + ")"));
      var list = el("ol", "replay-feed");
      g.rows.forEach(function (row) { list.appendChild(jumpButton(rowText(row, players), row.round, row.t, viewer, tone(row.value))); });
      panel.appendChild(list);
    });
  }

  var api = { KINDS: KINDS, defaultSlot: defaultSlot, castsFor: castsFor, unusedFor: unusedFor, groupsFor: groupsFor,
              facts: facts, rowText: rowText, unusedText: unusedText, notes: notes, signed: signed, clock: clock,
              render: render };
  global.ReplayCritUtil = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

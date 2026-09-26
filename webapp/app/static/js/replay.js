/*
 * The 2D replay viewer (docs/replay-viewer-plan.md, "The viewer").
 *
 * Plays one round of a condensed replay (JSON format v1) on a canvas over the minimap. No build
 * step and no dependencies. Unlinked mode shows agents and side groups only. Linked mode
 * (`options.linked`, the local preview's dev route for now) adds names and team colours, the
 * kill feed with each kill's weapon and Impact, a round scoreboard, ability objects, shot
 * tracers and the spike, all read from optional fields a round may carry beside format v1:
 * kills[].weapon/gain/loss/state, db, stats and extras {abilities, shots}.
 *
 * A round blob: {v, round, map, hz, t_start, t_decided, t_end, players:[{slot, agent, side}],
 * tracks:{slot:[{t0, u, v, yaw}]}, alive:{slot:[[from, to, cause, flags?]]}, kills:[{i, t, killer,
 * victim, u, v}], plant, defuse, util}. Track arrays are delta-encoded; u/v are 0..10000 of the
 * square minimap; yaw is map-space degrees (0 = +u, 90 = +v).
 */
(function (global) {
  "use strict";

  var SUPPORTED_VERSIONS = [1];
  var LAST_KNOWN_S = 2;           // a hollow last-known marker shows this long after a segment ends
  var SPEEDS = [0.5, 1, 2, 4];
  var STEP_S = 5;
  var UV = 10000;

  // ------------------------------------------------------------ decoding (pure; tested in node)

  function decodeSegment(segment, hz) {
    var out = [];
    var u = 0, v = 0, yaw = 0;
    for (var i = 0; i < segment.u.length; i++) {
      u = i ? u + segment.u[i] : segment.u[0];
      v = i ? v + segment.v[i] : segment.v[0];
      yaw = i ? yaw + segment.yaw[i] : segment.yaw[0];
      out.push({ t: Math.round((segment.t0 + i / hz) * 1e6) / 1e6, u: u, v: v, yaw: ((yaw % 360) + 360) % 360 });
    }
    return out;
  }

  function decodeRound(blob) {
    if (SUPPORTED_VERSIONS.indexOf(blob.v) < 0) throw new Error("unsupported replay format v" + blob.v);
    var tracks = {};
    Object.keys(blob.tracks || {}).forEach(function (slot) {
      tracks[slot] = blob.tracks[slot].map(function (segment) { return decodeSegment(segment, blob.hz); });
    });
    return tracks;
  }

  function lerpYaw(a, b, f) {
    var turn = ((b - a + 540) % 360) - 180;
    return ((a + turn * f) % 360 + 360) % 360;
  }

  // Where a slot is at time t: live (inside a segment, interpolated between grid samples),
  // last-known (up to LAST_KNOWN_S after a segment ends), or null.
  function trackAt(segments, t) {
    var last = null;
    for (var s = 0; s < (segments || []).length; s++) {
      var seg = segments[s];
      var a = seg[0], b = seg[seg.length - 1];
      if (t >= a.t && t <= b.t) {
        var lo = 0, hi = seg.length - 1;
        while (hi - lo > 1) {
          var mid = (lo + hi) >> 1;
          if (seg[mid].t <= t) lo = mid; else hi = mid;
        }
        var p = seg[lo], q = seg[hi];
        var f = q.t > p.t ? Math.min(1, Math.max(0, (t - p.t) / (q.t - p.t))) : 0;
        return { u: p.u + (q.u - p.u) * f, v: p.v + (q.v - p.v) * f, yaw: lerpYaw(p.yaw, q.yaw, f), live: true };
      }
      if (b.t < t) last = b;
    }
    if (last && t - last.t <= LAST_KNOWN_S) return { u: last.u, v: last.v, yaw: last.yaw, live: false };
    return null;
  }

  // The alive interval holding t, with its cause and flags ("unobserved", "uncertain"), or null.
  function aliveAt(intervals, t, tEnd) {
    for (var i = 0; i < (intervals || []).length; i++) {
      var iv = intervals[i];
      var to = iv[1] === null ? tEnd : iv[1];
      if (t >= iv[0] && t <= to) return { from: iv[0], to: iv[1], cause: iv[2], flags: iv[3] || [] };
    }
    return null;
  }

  // ------------------------------------------------------------ linked-mode helpers (pure; tested in node)

  // Ability archetypes -> how to draw them. `r` is a radius in world units; `shape` is
  // area (a filled circle), smoke (a grey disc), wire (a line to its paired end), device
  // (a small square), point (a small dot) or hidden. First match wins; unknown ones are points.
  var ABILITY_STYLES = [
    [/^Wraith_4_Smoke$/, { label: "Dark Cover", shape: "smoke", r: 410 }],
    [/^Smonk_NewSmoke$/, { label: "Ruse", shape: "smoke", r: 410 }],
    [/^Gumshoe_Q_Cage$/, { label: "Cyber Cage", shape: "smoke", r: 330 }],
    [/^Gumshoe_Q_CageTrap$/, { label: "Cyber Cage (placed)", shape: "device" }],
    [/^Gumshoe_4_TripWire$/, { label: "Trapwire", shape: "wire" }],
    [/^Gumshoe_4_TripWire_SecondWire$/, { label: "Trapwire", shape: "hidden" }],
    [/^Gumshoe_E_PossessableCamera$/, { label: "Spycam", shape: "device" }],
    [/^RemovableObject_GumshoeTrackingDart$/, { label: "Spycam dart", shape: "point" }],
    [/^Gumshoe_X_InterrogateHat$/, { label: "Neural Theft", shape: "point" }],
    [/^Hunter_Q_SonarPing$/, { label: "Recon Bolt", shape: "reveal", r: 1500 }],
    [/^Hunter_Q_SonarBolt$/, { label: "Recon Bolt", shape: "hidden" }],
    [/^Hunter_4_ExplosiveBolt_Explosion$/, { label: "Shock Bolt", shape: "area", r: 350 }],
    [/^Hunter_E_Drone$/, { label: "Owl Drone", shape: "device" }],
    [/^Hunter_E_Drone_RevealDart$/, { label: "Owl Drone dart", shape: "point" }],
    [/^Terra_E_RewindTime_RewindTarget$/, { label: "Rewind point", shape: "point" }],
    [/^Terra_C_TimeSlowGrenade_Explosion$/, { label: "Slow field", shape: "area", r: 500 }],
    [/^Sequoia_E_Shield$/, { label: "Double Tap shield", shape: "hidden" }],
    [/^Sequoia_E_Orb$/, { label: "Double Tap orb", shape: "point" }],
    [/^Sequoia_4_MovingCover$/, { label: "Contingency", shape: "device" }],
    [/^Sequoia_Q_FragileMissile_TrajectoryWarning$/, { label: "Undercut", shape: "point" }],
    [/^Sequoia_X_LineCapture$/, { label: "Kill Contract", shape: "point" }],
    [/^Phoenix_MolotovFire$/, { label: "Hot Hands", shape: "area", r: 450 }],
    [/^Phoenix_Q_FlameWallManager_Production$/, { label: "Blaze", shape: "point" }],
    [/^Phoenix_X_ResTarget_Production$/, { label: "Run It Back marker", shape: "device" }],
    [/^Guide_4_Heal_AOE$/, { label: "Regrowth", shape: "area", r: 900 }],
    [/^Guide_E_HawkFlash_FlashSource$/, { label: "Guiding Light", shape: "point" }],
    [/^Guide_Q_PossessableScout$/, { label: "Trailblazer", shape: "device" }],
    [/^Smonk_Q_DecayExplosion$/, { label: "Meddle", shape: "area", r: 450 }],
    [/^Wraith_Q_NearsightMissile_TrajectoryWarning$/, { label: "Paranoia", shape: "point" }]
  ];

  function abilityStyle(ability) {
    if (ability.kind === "Bomb") return { label: "Spike", shape: "spike" };
    if (ability.kind === "Projectile") return { label: ability.name, shape: "hidden" };
    var key = ability.code + "_" + ability.name;
    for (var i = 0; i < ABILITY_STYLES.length; i++) {
      if (ABILITY_STYLES[i][0].test(key)) return ABILITY_STYLES[i][1];
    }
    return { label: ability.name.replace(/_/g, " "), shape: "point" };
  }

  // Abilities showing at time t: spawned by t and not yet closed (an unclosed one lasts the round).
  function abilitiesAt(abilities, t, tEnd) {
    return (abilities || []).filter(function (a) {
      var until = a.t1 === null || a.t1 === undefined ? tEnd : a.t1;
      return a.t0 <= t && t <= until;
    });
  }

  // Trapwire: each first anchor pairs with the unpaired second anchor of the same owner
  // spawned closest in time (within 1 s). Returns {index of first: second ability}.
  function pairWires(abilities) {
    var pairs = {}, used = {};
    (abilities || []).forEach(function (a, i) {
      if (a.name !== "4_TripWire") return;
      var best = -1, bestDt = 1.0;
      abilities.forEach(function (b, j) {
        if (used[j] || b.name !== "4_TripWire_SecondWire" || b.slot !== a.slot) return;
        var dt = Math.abs(b.t0 - a.t0);
        if (dt <= bestDt) { best = j; bestDt = dt; }
      });
      if (best >= 0) { used[best] = true; pairs[i] = abilities[best]; }
    });
    return pairs;
  }

  // A signed number with an explicit sign (U+2212 for minus), never colour alone.
  function signed(value) {
    if (value === null || value === undefined || isNaN(value)) return "";
    var n = Math.round(value);
    return n > 0 ? "+" + n : n < 0 ? "−" + Math.abs(n) : "0";
  }

  // Kills, deaths and assists-free tallies up to t, per slot, from the blob's kills.
  function tallyAt(kills, t) {
    var out = {};
    (kills || []).forEach(function (k) {
      if (k.t > t) return;
      if (k.killer !== k.victim) (out[k.killer] = out[k.killer] || { k: 0, d: 0 }).k += 1;
      (out[k.victim] = out[k.victim] || { k: 0, d: 0 }).d += 1;
    });
    return out;
  }

  // ------------------------------------------------------------ the viewer

  var LAYERS = ["names", "abilities", "tracers", "cones"];
  var TRACER_S = 0.25;     // a shot's tracer fades over this long
  var KILL_LINE_S = 1.5;   // a kill's killer-to-victim line fades over this long

  function ReplayViewer(root, options) {
    this.root = root;
    this.options = options;
    this.linked = options.linked || null;          // {players: {slot: {name, team}}, uvPerUnit}
    this.rounds = options.rounds;                  // [1, 2, ...]
    this.cache = {};                               // round -> Promise of {blob, tracks}
    this.speed = 1;
    this.t = 0;
    this.playing = false;
    this.current = null;
    this.icons = {};
    this.layers = { names: true, abilities: true, tracers: true, cones: true };
    this.canvas = root.querySelector("[data-replay-canvas]");
    this.ctx = this.canvas.getContext("2d");
    this.map = new Image();
    this.map.onload = this.draw.bind(this);
    this.map.src = options.mapImage;
    this.hover = null;
    this.bindControls();
    this.lastFrame = null;
    this.tick = this.tick.bind(this);
    requestAnimationFrame(this.tick);
  }

  ReplayViewer.prototype.css = function (name, fallback) {
    return getComputedStyle(this.root).getPropertyValue(name).trim() || fallback;
  };

  // Linked: the DB team's colour (team-1 / team-2, like the match page). Unlinked: the side group.
  ReplayViewer.prototype.slotColor = function (slot, side) {
    var p = this.linked && this.linked.players[String(slot)];
    if (p) return this.css(p.team === "team-1" ? "--replay-team-1" : "--replay-team-2", "#bbbbbb");
    return this.color(side);
  };

  ReplayViewer.prototype.color = function (side) {
    var name = side === "A" ? "--replay-side-a" : side === "B" ? "--replay-side-b" : "--replay-neutral";
    return this.css(name, "#bbbbbb");
  };

  ReplayViewer.prototype.nameOf = function (slot) {
    var p = this.linked && this.linked.players[String(slot)];
    if (p) return p.name;
    var row = this.current && this.current.blob.players.filter(function (q) { return q.slot === slot; })[0];
    return row ? row.agent + " (slot " + slot + ")" : "slot " + slot;
  };

  ReplayViewer.prototype.icon = function (agent) {
    if (!this.options.agentIcon) return null;
    if (!(agent in this.icons)) {
      var url = this.options.agentIcon(agent);
      var img = null;
      if (url) {
        img = new Image();
        img.onload = this.draw.bind(this);
        img.src = url;
      }
      this.icons[agent] = img;
    }
    var found = this.icons[agent];
    return found && found.complete && found.naturalWidth ? found : null;
  };

  ReplayViewer.prototype.fetchRound = function (n) {
    if (!this.cache[n]) {
      this.cache[n] = Promise.resolve(this.options.loadRound(n)).then(function (blob) {
        var extras = blob.extras || { abilities: [], shots: [] };
        return { blob: blob, tracks: decodeRound(blob), extras: extras, wires: pairWires(extras.abilities) };
      });
    }
    return this.cache[n];
  };

  ReplayViewer.prototype.showRound = function (n, t) {
    var self = this;
    return this.fetchRound(n).then(function (round) {
      self.current = round;
      self.number = n;
      self.t = t || 0;
      self.playing = false;
      self.renderStrip();
      self.renderTicks();
      self.renderBanner();
      self.renderFeed();
      self.updateControls();
      self.draw();
      var next = self.rounds[self.rounds.indexOf(n) + 1];
      if (next !== undefined) self.fetchRound(next);
      return round;
    });
  };

  ReplayViewer.prototype.seek = function (t) {
    if (!this.current) return;
    this.t = Math.max(0, Math.min(this.current.blob.t_end, t));
    this.updateControls();
    this.draw();
  };

  ReplayViewer.prototype.toggle = function () {
    if (!this.current) return;
    if (!this.playing && this.t >= this.current.blob.t_end) this.t = 0;
    this.playing = !this.playing;
    this.updateControls();
  };

  ReplayViewer.prototype.tick = function (now) {
    if (this.playing && this.current && this.lastFrame !== null) {
      this.t += (now - this.lastFrame) / 1000 * this.speed;
      if (this.t >= this.current.blob.t_end) {
        this.t = this.current.blob.t_end;
        this.playing = false;
      }
      this.updateControls();
      this.draw();
    }
    this.lastFrame = now;
    requestAnimationFrame(this.tick);
  };

  ReplayViewer.prototype.bindControls = function () {
    var self = this;
    var q = function (sel) { return self.root.querySelector(sel); };
    this.ui = {
      play: q("[data-replay-play]"), speed: q("[data-replay-speed]"), scrub: q("[data-replay-scrub]"),
      clock: q("[data-replay-clock]"), strip: q("[data-replay-strip]"), ticks: q("[data-replay-ticks]"),
      prev: q("[data-replay-prev]"), next: q("[data-replay-next]"), state: q("[data-replay-state]"),
      feed: q("[data-replay-feed]"), board: q("[data-replay-board]"), banner: q("[data-replay-banner]"),
      tip: q("[data-replay-tip]")
    };
    SPEEDS.forEach(function (s) {
      var option = document.createElement("option");
      option.value = String(s);
      option.textContent = s + "×";
      if (s === 1) option.selected = true;
      self.ui.speed.appendChild(option);
    });
    this.ui.play.addEventListener("click", function () { self.toggle(); });
    this.ui.speed.addEventListener("change", function () { self.speed = Number(self.ui.speed.value); });
    this.ui.scrub.addEventListener("input", function () {
      if (self.current) self.seek(self.current.blob.t_end * Number(self.ui.scrub.value) / 1000);
    });
    this.ui.prev.addEventListener("click", function () { self.step(-1); });
    this.ui.next.addEventListener("click", function () { self.step(1); });
    this.root.addEventListener("keydown", function (e) {
      if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && e.key !== " ") return;
      if (e.key === " ") { e.preventDefault(); self.toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); self.seek(self.t - STEP_S); }
      else if (e.key === "ArrowRight") { e.preventDefault(); self.seek(self.t + STEP_S); }
    });
    LAYERS.forEach(function (name) {
      var box = q('[data-replay-layer="' + name + '"]');
      if (!box) return;
      try {
        var saved = global.localStorage && global.localStorage.getItem("replay-layer-" + name);
        if (saved !== null && saved !== undefined) self.layers[name] = saved === "1";
      } catch (err) { /* storage unavailable: keep the default */ }
      box.checked = self.layers[name];
      box.addEventListener("change", function () {
        self.layers[name] = box.checked;
        try { global.localStorage.setItem("replay-layer-" + name, box.checked ? "1" : "0"); } catch (err) { /* ignore */ }
        self.draw();
      });
    });
    if (this.ui.feed) {
      this.ui.feed.addEventListener("click", function (e) {
        var row = e.target.closest("[data-kill-t]");
        if (row) self.seek(Number(row.getAttribute("data-kill-t")) - 3);
      });
    }
    this.canvas.addEventListener("mousemove", function (e) { self.onHover(e); });
    this.canvas.addEventListener("mouseleave", function () { self.hover = null; self.hideTip(); });
  };

  ReplayViewer.prototype.step = function (delta) {
    var i = this.rounds.indexOf(this.number) + delta;
    if (i >= 0 && i < this.rounds.length) this.showRound(this.rounds[i]);
  };

  ReplayViewer.prototype.renderStrip = function () {
    var self = this;
    this.ui.strip.innerHTML = "";
    this.rounds.forEach(function (n) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = String(n);
      var winner = self.options.roundWinner ? self.options.roundWinner(n) : null;
      button.className = "replay-round" + (n === self.number ? " is-current" : "") +
        (winner ? " won-" + winner : "");
      button.setAttribute("aria-label", "Round " + n + (winner ? ", won by " + winner.replace("-", " ") : ""));
      if (n === self.number) button.setAttribute("aria-current", "true");
      button.addEventListener("click", function () { self.showRound(n); });
      self.ui.strip.appendChild(button);
    });
    this.ui.prev.disabled = this.rounds.indexOf(this.number) <= 0;
    this.ui.next.disabled = this.rounds.indexOf(this.number) >= this.rounds.length - 1;
  };

  ReplayViewer.prototype.renderTicks = function () {
    var blob = this.current.blob, self = this;
    var ticks = this.ui.ticks;
    ticks.innerHTML = "";
    var add = function (t, cls, title, color) {
      var mark = document.createElement("span");
      mark.className = cls;
      mark.style.left = (100 * t / blob.t_end) + "%";
      if (color) mark.style.background = color;
      mark.title = title;
      ticks.appendChild(mark);
    };
    blob.kills.forEach(function (k) {
      add(k.t, "replay-tick", self.nameOf(k.killer) + " killed " + self.nameOf(k.victim) + " at " + k.t.toFixed(1) + " s",
        self.linked ? self.slotColor(k.killer) : null);
    });
    if (blob.db && typeof blob.db.plant === "number") {
      add(blob.db.plant, "replay-tick replay-tick-plant", "spike planted at " + blob.db.plant.toFixed(1) + " s");
    }
    if (blob.db && typeof blob.db.defuse === "number") {
      add(blob.db.defuse, "replay-tick replay-tick-plant", "spike defused at " + blob.db.defuse.toFixed(1) + " s");
    }
    if (typeof blob.t_decided === "number" && blob.t_decided < blob.t_end) {
      add(blob.t_decided, "replay-tick replay-tick-decided", "round decided at " + blob.t_decided.toFixed(1) + " s");
    }
  };

  ReplayViewer.prototype.renderBanner = function () {
    var banner = this.ui.banner, blob = this.current.blob;
    if (!banner) return;
    var db = blob.db;
    if (!db) { banner.textContent = "Round " + blob.round; return; }
    var winner = db.winner === "team-1" ? "Team 1" : db.winner === "team-2" ? "Team 2" : "?";
    var how = (db.outcome || "").replace(/^Team [AB] /, "").replace(/ Win$/, "");
    var parts = ["Round " + blob.round + ": " + winner + " won" + (how ? " (" + how.toLowerCase() + ")" : "")];
    if (typeof db.plant === "number") parts.push("spike planted at " + db.plant.toFixed(1) + " s");
    if (db.defused) parts.push("defused" + (typeof db.defuse === "number" ? " at " + db.defuse.toFixed(1) + " s" : ""));
    if (db.exploded) parts.push("detonated");
    banner.textContent = parts.join(" · ");
    banner.className = "replay-banner" + (db.winner ? " won-" + db.winner : "");
  };

  ReplayViewer.prototype.personHtml = function (slot) {
    var p = this.linked && this.linked.players[String(slot)];
    var team = p ? p.team : "";
    return '<span class="replay-person team-' + escapeHtml(team.replace("team-", "")) + '">' +
      escapeHtml(this.nameOf(slot)) + "</span>";
  };

  ReplayViewer.prototype.renderFeed = function () {
    var feed = this.ui.feed, blob = this.current.blob, self = this;
    if (!feed) return;
    if (!blob.kills.length) { feed.innerHTML = '<li class="replay-feed-empty">No kills this round.</li>'; return; }
    feed.innerHTML = blob.kills.map(function (k) {
      var impact = "";
      if (typeof k.gain === "number") {
        impact = '<span class="replay-impact"><span class="pos" title="Killer\'s Impact from this kill">' +
          signed(k.gain) + '</span> <span class="neg" title="Victim\'s Impact from this death">' +
          signed(-k.loss) + "</span></span>";
      }
      var state = k.state ? '<span class="replay-feed-state" title="Players alive before the kill (killer\'s side v victim\'s side)">' +
        k.state[0] + "v" + k.state[1] + "</span>" : "";
      return '<li class="replay-feed-row" data-kill-t="' + k.t + '" data-kill-i="' + k.i + '" tabindex="0">' +
        '<span class="replay-feed-t">' + k.t.toFixed(1) + "s</span>" +
        '<span class="replay-feed-who">' + self.personHtml(k.killer) +
        ' <span class="replay-feed-gun">' + escapeHtml(k.weapon || (k.killer === k.victim ? "self" : "?")) + "</span> " +
        self.personHtml(k.victim) + "</span>" + state + impact + "</li>";
    }).join("");
  };

  ReplayViewer.prototype.renderBoard = function () {
    var board = this.ui.board, blob = this.current.blob, self = this, t = this.t;
    if (!board || !this.linked) return;
    var tally = tallyAt(blob.kills, t);
    var stats = blob.stats || {};
    var rows = { "team-1": [], "team-2": [] };
    blob.players.forEach(function (p) {
      var info = self.linked.players[String(p.slot)] || {};
      var life = aliveAt(blob.alive[String(p.slot)], t, blob.t_end);
      var s = stats[String(p.slot)] || {};
      var mine = tally[p.slot] || { k: 0, d: 0 };
      var impact = typeof s.impact === "number"
        ? '<td class="num ' + (s.impact > 0 ? "pos" : s.impact < 0 ? "neg" : "") + '">' + signed(s.impact) + "</td>"
        : "<td></td>";
      (rows[info.team] || rows["team-1"]).push(
        '<tr class="' + (life ? "" : "is-dead") + '"><td class="replay-board-name">' +
        '<span class="replay-dot" style="background:' + self.slotColor(p.slot) + '"></span>' +
        escapeHtml(info.name || p.agent) + ' <span class="replay-board-agent">' + escapeHtml(p.agent) + "</span>" +
        (life ? "" : ' <span class="replay-board-dead">dead</span>') + "</td>" +
        '<td class="num">' + mine.k + "/" + mine.d + "</td>" +
        '<td class="num">' + (typeof s.loadout === "number" ? s.loadout : "") + "</td>" + impact + "</tr>");
    });
    var head = '<thead><tr><th>Player</th><th class="num" title="Kills / deaths so far this round">K/D</th>' +
      '<th class="num" title="Loadout value this round (credits)">Loadout</th>' +
      '<th class="num" title="Stored Impact for the whole round">Round Impact</th></tr></thead>';
    board.innerHTML = ["team-1", "team-2"].map(function (team) {
      return '<table class="replay-board-team team-' + team.slice(-1) + '">' + head + "<tbody>" + rows[team].join("") +
        "</tbody></table>";
    }).join("");
  };

  ReplayViewer.prototype.updateControls = function () {
    if (!this.current) return;
    var blob = this.current.blob, t = this.t;
    this.ui.scrub.value = String(Math.round(1000 * t / blob.t_end));
    this.ui.play.textContent = this.playing ? "Pause" : "Play";
    this.ui.play.setAttribute("aria-pressed", this.playing ? "true" : "false");
    var phase = t > blob.t_decided ? " · round decided" : "";
    this.ui.clock.textContent = "Round " + blob.round + " · " + t.toFixed(1) + " s" + phase;
    if (this.ui.feed) {
      Array.prototype.forEach.call(this.ui.feed.querySelectorAll("[data-kill-t]"), function (row) {
        var kt = Number(row.getAttribute("data-kill-t"));
        row.classList.toggle("is-future", kt > t);
        row.classList.toggle("is-recent", kt <= t && t - kt < 3);
      });
    }
    this.renderBoard();
  };

  // How many players are drawn live at time t (and last-known), for tests and the page's summary.
  ReplayViewer.prototype.snapshot = function (t) {
    var blob = this.current.blob, tracks = this.current.tracks;
    var live = 0, lastKnown = 0;
    blob.players.forEach(function (p) {
      var life = aliveAt(blob.alive[String(p.slot)], t, blob.t_end);
      var at = trackAt(tracks[String(p.slot)], t);
      if (at && at.live && life) live += 1;
      else if (at) lastKnown += 1;
    });
    return { round: blob.round, t: t, live: live, lastKnown: lastKnown };
  };

  ReplayViewer.prototype.uvRadius = function (worldUnits) {
    return worldUnits * ((this.linked && this.linked.uvPerUnit) || 0.75);
  };

  ReplayViewer.prototype.drawAbilities = function (ctx, s, r, hits) {
    var blob = this.current.blob, t = this.t, self = this;
    var showing = abilitiesAt(this.current.extras.abilities, t, blob.t_end);
    var all = this.current.extras.abilities;
    var smoke = this.css("--replay-smoke", "rgba(210, 214, 220, 0.55)");
    // Areas first, so devices and points sit on top of them.
    ["smoke", "area", "reveal", "wire", "device", "point", "spike"].forEach(function (pass) {
      showing.forEach(function (a) {
        var style = abilityStyle(a);
        if (style.shape !== pass) return;
        var x = a.u * s, y = a.v * s, color = a.slot === null ? self.css("--ink-secondary", "#ccc") : self.slotColor(a.slot);
        ctx.save();
        if (pass === "smoke" || pass === "area" || pass === "reveal") {
          var rad = self.uvRadius(style.r) * s;
          ctx.beginPath(); ctx.arc(x, y, rad, 0, 2 * Math.PI);
          if (pass === "smoke") { ctx.fillStyle = smoke; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke(); }
          else if (pass === "area") { ctx.globalAlpha = 0.28; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 0.9; ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke(); }
          else {
            var age = t - a.t0, pulse = (age % 1.2) / 1.2;
            ctx.globalAlpha = 0.8 * (1 - pulse); ctx.lineWidth = 2; ctx.strokeStyle = color;
            ctx.beginPath(); ctx.arc(x, y, rad * (0.3 + 0.7 * pulse), 0, 2 * Math.PI); ctx.stroke();
            ctx.globalAlpha = 1; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, r * 0.3, 0, 2 * Math.PI); ctx.fill();
          }
          hits.push({ x: x, y: y, r: Math.max(rad, r * 0.6), text: self.abilityText(a, style), area: true });
        } else if (pass === "wire") {
          var other = self.current.wires[all.indexOf(a)];
          ctx.strokeStyle = color; ctx.lineWidth = Math.max(2, r / 5); ctx.setLineDash([r / 2, r / 4]);
          if (other) { ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(other.u * s, other.v * s); ctx.stroke(); }
          ctx.setLineDash([]); ctx.fillStyle = color;
          [[x, y]].concat(other ? [[other.u * s, other.v * s]] : []).forEach(function (pt) {
            ctx.beginPath(); ctx.arc(pt[0], pt[1], r * 0.28, 0, 2 * Math.PI); ctx.fill();
          });
          hits.push({ x: x, y: y, r: r * 0.6, text: self.abilityText(a, style) });
        } else if (pass === "device") {
          var d = r * 0.45;
          ctx.fillStyle = color; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2;
          ctx.fillRect(x - d, y - d, 2 * d, 2 * d); ctx.strokeRect(x - d, y - d, 2 * d, 2 * d);
          hits.push({ x: x, y: y, r: r * 0.7, text: self.abilityText(a, style) });
        } else if (pass === "point") {
          ctx.fillStyle = color; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(x, y, r * 0.3, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
          hits.push({ x: x, y: y, r: r * 0.6, text: self.abilityText(a, style) });
        } else if (pass === "spike") {
          var k = r * 0.75, beat = 0.5 + 0.5 * Math.sin((t - a.t0) * Math.PI * 2);
          ctx.fillStyle = self.css("--brand", "#ff4655"); ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2;
          ctx.globalAlpha = 0.35 * beat; ctx.beginPath(); ctx.arc(x, y, k * 2.2, 0, 2 * Math.PI); ctx.fill();
          ctx.globalAlpha = 1;
          ctx.beginPath(); ctx.moveTo(x, y - k); ctx.lineTo(x + k * 0.7, y); ctx.lineTo(x, y + k); ctx.lineTo(x - k * 0.7, y);
          ctx.closePath(); ctx.fill(); ctx.stroke();
          hits.push({ x: x, y: y, r: k * 1.4, text: "Spike planted" + (a.slot !== null ? " by " + self.nameOf(a.slot) : "") +
            " at " + a.t0.toFixed(1) + " s" });
        }
        ctx.restore();
      });
    });
  };

  ReplayViewer.prototype.abilityText = function (a, style) {
    var owner = a.slot === null ? "owner unknown" : this.nameOf(a.slot) + (a.owner_by === "nearest" ? " (nearest " + a.agent + ", a guess)" : "");
    return (a.agent ? a.agent + " · " : "") + style.label + " · " + owner + " · " + a.t0.toFixed(1) + "–" +
      (a.t1 === null || a.t1 === undefined ? "end" : a.t1.toFixed(1)) + " s";
  };

  ReplayViewer.prototype.drawTracers = function (ctx, s, r) {
    var t = this.t, self = this;
    (this.current.extras.shots || []).forEach(function (shot) {
      var age = t - shot.t;
      if (age < 0 || age > TRACER_S || shot.u1 === undefined) return;
      ctx.save();
      ctx.globalAlpha = 0.9 * (1 - age / TRACER_S);
      ctx.strokeStyle = self.slotColor(shot.slot);
      ctx.lineWidth = Math.max(1.5, r / 8);
      ctx.beginPath(); ctx.moveTo(shot.u * s, shot.v * s); ctx.lineTo(shot.u1 * s, shot.v1 * s); ctx.stroke();
      ctx.restore();
    });
  };

  ReplayViewer.prototype.drawUtil = function (ctx, s, r) {
    // W-e flash and nearsight casts: a burst for 1 s at the cast point, lines to whoever it hit.
    var blob = this.current.blob, t = this.t, tracks = this.current.tracks, self = this;
    (blob.util || []).forEach(function (u) {
      var age = t - u.t;
      if (age < 0 || age > 1 || u.u === undefined) return;
      var x = u.u * s, y = u.v * s;
      ctx.save();
      ctx.globalAlpha = 1 - age;
      ctx.strokeStyle = u.k === "flash" ? "#ffffff" : self.css("--replay-smoke", "#cccccc");
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, r * (0.6 + 1.4 * age), 0, 2 * Math.PI); ctx.stroke();
      (u.targets || []).forEach(function (slot) {
        var at = trackAt(tracks[String(slot)], u.t);
        if (!at) return;
        ctx.setLineDash([r / 3, r / 4]);
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(at.u * s, at.v * s); ctx.stroke();
      });
      ctx.restore();
    });
  };

  ReplayViewer.prototype.draw = function () {
    var ctx = this.ctx, size = this.canvas.width, s = size / UV;
    ctx.clearRect(0, 0, size, size);
    if (this.map.complete && this.map.naturalWidth) ctx.drawImage(this.map, 0, 0, size, size);
    if (!this.current) return;
    var blob = this.current.blob, tracks = this.current.tracks, t = this.t, self = this;
    var r = size / 48;
    var hits = [];

    if (this.layers.abilities) this.drawAbilities(ctx, s, r, hits);
    this.drawUtil(ctx, s, r);
    if (this.layers.tracers) this.drawTracers(ctx, s, r);

    blob.kills.forEach(function (k) {
      if (k.t > t || k.u === null) return;
      var x = k.u * s, y = k.v * s, age = t - k.t;
      if (age <= KILL_LINE_S && k.killer !== k.victim) {
        var from = trackAt(tracks[String(k.killer)], k.t);
        if (from) {
          ctx.save();
          ctx.globalAlpha = 1 - age / KILL_LINE_S;
          ctx.strokeStyle = self.slotColor(k.killer);
          ctx.lineWidth = Math.max(2, r / 4);
          ctx.beginPath(); ctx.moveTo(from.u * s, from.v * s); ctx.lineTo(x, y); ctx.stroke();
          ctx.restore();
        }
      }
      ctx.strokeStyle = "rgba(230, 230, 230, 0.85)";
      ctx.lineWidth = Math.max(2, r / 5);
      ctx.beginPath();
      ctx.moveTo(x - r * 0.6, y - r * 0.6); ctx.lineTo(x + r * 0.6, y + r * 0.6);
      ctx.moveTo(x + r * 0.6, y - r * 0.6); ctx.lineTo(x - r * 0.6, y + r * 0.6);
      ctx.stroke();
      var gain = typeof k.gain === "number" ? " (" + signed(k.gain) + " / " + signed(-k.loss) + " Impact)" : "";
      hits.push({ x: x, y: y, r: r, text: self.nameOf(k.killer) + " killed " + self.nameOf(k.victim) +
        (k.weapon ? " with " + k.weapon : "") + " at " + k.t.toFixed(1) + " s" + gain });
    });

    var labels = [];
    blob.players.forEach(function (p) {
      var slot = String(p.slot);
      var at = trackAt(tracks[slot], t);
      if (!at) return;
      var life = aliveAt(blob.alive[slot], t, blob.t_end);
      var x = at.u * s, y = at.v * s, color = self.slotColor(p.slot, p.side);
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(2, r / 5);
      if (!(at.live && life)) {
        ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.stroke();
        return;
      }
      var rad = at.yaw * Math.PI / 180;
      if (self.layers.cones) {
        ctx.globalAlpha = 0.35;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.arc(x, y, r * 2.4, rad - 0.4, rad + 0.4); ctx.closePath(); ctx.fill();
        ctx.globalAlpha = 1;
      }
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill();
      var img = self.icon(p.agent);
      if (img) {
        ctx.save();
        ctx.beginPath(); ctx.arc(x, y, r * 0.86, 0, 2 * Math.PI); ctx.clip();
        ctx.drawImage(img, x - r * 0.86, y - r * 0.86, r * 1.72, r * 1.72);
        ctx.restore();
      } else {
        ctx.fillStyle = "#ffffff";
        ctx.font = "600 " + Math.round(r * 0.95) + "px system-ui, sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(p.agent.slice(0, 2), x, y);
      }
      if (life.flags.length) {
        // An unobserved or uncertain life: a dashed ring says the evidence is incomplete.
        ctx.save();
        ctx.setLineDash([r / 3, r / 4]);
        ctx.strokeStyle = "#ffffff";
        ctx.beginPath(); ctx.arc(x, y, r * 1.35, 0, 2 * Math.PI); ctx.stroke();
        ctx.restore();
      }
      if (self.linked && self.layers.names) labels.push({ x: x, y: y + r * 1.25, text: self.nameOf(p.slot) });
      hits.push({ x: x, y: y, r: r * 1.2, text: self.nameOf(p.slot) + " · " + p.agent + (life.flags.length ? " · " + life.flags.join(", ") : "") });
    });

    // Names last, on a dark plate so they read over any part of the map.
    ctx.save();
    ctx.font = "600 " + Math.round(r * 0.8) + "px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    labels.forEach(function (l) {
      var text = l.text.length > 14 ? l.text.slice(0, 13) + "…" : l.text;
      var w = ctx.measureText(text).width + r * 0.5;
      ctx.fillStyle = "rgba(10, 10, 12, 0.72)";
      ctx.fillRect(l.x - w / 2, l.y, w, r * 1.05);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(text, l.x, l.y + r * 0.1);
    });
    ctx.restore();

    this.hits = hits;
    var snap = this.snapshot(t);
    this.ui.state.textContent = snap.live + " players on the map";
    this.canvas.setAttribute("aria-label", "Minimap, round " + blob.round + " at " + t.toFixed(1) + " s: " +
      snap.live + " players shown");
    if (this.hover) this.showTip(this.hover);
  };

  ReplayViewer.prototype.onHover = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    var scale = this.canvas.width / rect.width;
    this.hover = { x: (e.clientX - rect.left) * scale, y: (e.clientY - rect.top) * scale, px: e.clientX - rect.left, py: e.clientY - rect.top };
    this.showTip(this.hover);
  };

  // The tooltip names the smallest mark under the pointer (a player over a smoke wins).
  ReplayViewer.prototype.showTip = function (hover) {
    var tip = this.ui.tip;
    if (!tip) return;
    var best = null;
    (this.hits || []).forEach(function (h) {
      var d = Math.hypot(h.x - hover.x, h.y - hover.y);
      if (d <= h.r && (!best || (best.area && !h.area) || (best.area === h.area && h.r < best.r))) best = h;
    });
    if (!best) { this.hideTip(); return; }
    tip.textContent = best.text;
    tip.hidden = false;
    var stage = tip.parentNode.getBoundingClientRect();
    var left = Math.min(hover.px + 14, stage.width - tip.offsetWidth - 4);
    tip.style.left = Math.max(4, left) + "px";
    tip.style.top = Math.max(4, hover.py - tip.offsetHeight - 10) + "px";
  };

  ReplayViewer.prototype.hideTip = function () {
    if (this.ui.tip) this.ui.tip.hidden = true;
  };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var api = {
    decodeSegment: decodeSegment, decodeRound: decodeRound, trackAt: trackAt, aliveAt: aliveAt,
    lerpYaw: lerpYaw, ReplayViewer: ReplayViewer, SUPPORTED_VERSIONS: SUPPORTED_VERSIONS,
    abilityStyle: abilityStyle, abilitiesAt: abilitiesAt, pairWires: pairWires, signed: signed, tallyAt: tallyAt
  };
  global.Replay = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

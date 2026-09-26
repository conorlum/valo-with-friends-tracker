/*
 * The 2D replay viewer (docs/replay-viewer-plan.md, "The viewer"; W-d prototype, unlinked mode).
 *
 * Plays one round of a condensed replay (JSON format v1) on a canvas over the minimap. No build
 * step and no dependencies. Unlinked mode shows agents and side groups only; linked mode (names,
 * the kill feed from kill_events, DB clock seeking) arrives with Stage 2's routes.
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

  // ------------------------------------------------------------ the viewer

  function ReplayViewer(root, options) {
    this.root = root;
    this.options = options;
    this.rounds = options.rounds;                  // [1, 2, ...]
    this.cache = {};                               // round -> Promise of {blob, tracks}
    this.speed = 1;
    this.t = 0;
    this.playing = false;
    this.current = null;
    this.icons = {};
    this.canvas = root.querySelector("[data-replay-canvas]");
    this.ctx = this.canvas.getContext("2d");
    this.map = new Image();
    this.map.onload = this.draw.bind(this);
    this.map.src = options.mapImage;
    this.bindControls();
    this.lastFrame = null;
    this.tick = this.tick.bind(this);
    requestAnimationFrame(this.tick);
  }

  ReplayViewer.prototype.color = function (side) {
    var css = getComputedStyle(this.root);
    var name = side === "A" ? "--replay-side-a" : side === "B" ? "--replay-side-b" : "--replay-neutral";
    return css.getPropertyValue(name).trim() || "#bbbbbb";
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
        return { blob: blob, tracks: decodeRound(blob) };
      });
    }
    return this.cache[n];
  };

  ReplayViewer.prototype.showRound = function (n) {
    var self = this;
    return this.fetchRound(n).then(function (round) {
      self.current = round;
      self.number = n;
      self.t = 0;
      self.playing = false;
      self.renderStrip();
      self.renderTicks();
      self.updateControls();
      self.draw();
      // Prefetch the next round while this one plays.
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
      prev: q("[data-replay-prev]"), next: q("[data-replay-next]"), state: q("[data-replay-state]")
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
      button.className = "replay-round" + (n === self.number ? " is-current" : "");
      button.setAttribute("aria-label", "Round " + n);
      if (n === self.number) button.setAttribute("aria-current", "true");
      button.addEventListener("click", function () { self.showRound(n); });
      self.ui.strip.appendChild(button);
    });
    this.ui.prev.disabled = this.rounds.indexOf(this.number) <= 0;
    this.ui.next.disabled = this.rounds.indexOf(this.number) >= this.rounds.length - 1;
  };

  ReplayViewer.prototype.renderTicks = function () {
    var blob = this.current.blob;
    var ticks = this.ui.ticks;
    ticks.innerHTML = "";
    var add = function (t, cls, title) {
      var mark = document.createElement("span");
      mark.className = cls;
      mark.style.left = (100 * t / blob.t_end) + "%";
      mark.title = title;
      ticks.appendChild(mark);
    };
    blob.kills.forEach(function (k) { add(k.t, "replay-tick", "kill at " + k.t.toFixed(1) + " s"); });
    if (typeof blob.t_decided === "number" && blob.t_decided < blob.t_end) {
      add(blob.t_decided, "replay-tick replay-tick-decided", "round decided at " + blob.t_decided.toFixed(1) + " s");
    }
  };

  ReplayViewer.prototype.updateControls = function () {
    if (!this.current) return;
    var blob = this.current.blob;
    this.ui.scrub.value = String(Math.round(1000 * this.t / blob.t_end));
    this.ui.play.textContent = this.playing ? "Pause" : "Play";
    this.ui.play.setAttribute("aria-pressed", this.playing ? "true" : "false");
    var phase = this.t > blob.t_decided ? " · round decided" : "";
    this.ui.clock.textContent = "Round " + blob.round + " · " + this.t.toFixed(1) + " s" + phase;
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

  ReplayViewer.prototype.draw = function () {
    var ctx = this.ctx, size = this.canvas.width, s = size / UV;
    ctx.clearRect(0, 0, size, size);
    if (this.map.complete && this.map.naturalWidth) ctx.drawImage(this.map, 0, 0, size, size);
    if (!this.current) return;
    var blob = this.current.blob, tracks = this.current.tracks, t = this.t, self = this;
    var r = size / 48;

    blob.kills.forEach(function (k) {
      if (k.t > t || k.u === null) return;
      var x = k.u * s, y = k.v * s;
      ctx.strokeStyle = "rgba(220, 220, 220, 0.8)";
      ctx.lineWidth = Math.max(2, r / 5);
      ctx.beginPath();
      ctx.moveTo(x - r * 0.6, y - r * 0.6); ctx.lineTo(x + r * 0.6, y + r * 0.6);
      ctx.moveTo(x + r * 0.6, y - r * 0.6); ctx.lineTo(x - r * 0.6, y + r * 0.6);
      ctx.stroke();
    });

    blob.players.forEach(function (p) {
      var slot = String(p.slot);
      var at = trackAt(tracks[slot], t);
      if (!at) return;
      var life = aliveAt(blob.alive[slot], t, blob.t_end);
      var x = at.u * s, y = at.v * s, color = self.color(p.side);
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(2, r / 5);
      if (!(at.live && life)) {
        ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.stroke();
        return;
      }
      var rad = at.yaw * Math.PI / 180;
      ctx.globalAlpha = 0.35;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.arc(x, y, r * 2.4, rad - 0.4, rad + 0.4); ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
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
    });

    var snap = this.snapshot(t);
    this.ui.state.textContent = snap.live + " players on the map";
    this.canvas.setAttribute("aria-label", "Minimap, round " + blob.round + " at " + t.toFixed(1) + " s: " +
      snap.live + " players shown");
  };

  var api = {
    decodeSegment: decodeSegment, decodeRound: decodeRound, trackAt: trackAt, aliveAt: aliveAt,
    lerpYaw: lerpYaw, ReplayViewer: ReplayViewer, SUPPORTED_VERSIONS: SUPPORTED_VERSIONS
  };
  global.Replay = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

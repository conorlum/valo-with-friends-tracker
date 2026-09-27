/*
 * The 2D replay viewer (docs/replay-viewer-plan.md, "The viewer").
 *
 * Plays one round of a condensed replay (JSON format v1) on a canvas over the minimap. No build
 * step and no dependencies. Unlinked mode shows agents and side groups only. Linked mode
 * (`options.linked`, the replay page for a linked replay) adds names and team colours, the
 * kill feed with each kill's weapon and Impact, a round scoreboard, ability objects, shot
 * tracers and the spike. Abilities and shots come from the blob's `util` (kinds "ability" and
 * "shot", Stage 2); the page merges the site's fields onto each round: kills[].weapon/gain/
 * loss/state, db and stats.
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

  // Ability archetypes -> the ability they are and how to draw them. The key is the extras row's
  // `<code>_<name>` (extras.py normalises slot-first archetypes). `ability` is its display name
  // (the key in static/data/abilities.json, which gives its icon); `shape` is smoke (a grey
  // disc), area (a team-tinted disc), reveal (a pulsing ring), wire (a line to its paired end),
  // line (a wall or an aim, from the object's yaw: see lineEnds), badge (a team-coloured disc
  // with the ability's glyph) or hidden. `r` and `len` are world units. First match wins; an
  // unknown archetype is a badge with no glyph. Projectiles are never drawn (their object is).
  // PROVISIONAL(D7): radii are approximate in-game sizes, tuned by eye on the map.
  var ABILITY_STYLES = [
    // Omen
    [/^Wraith_4_Smoke$/, { ability: "Dark Cover", shape: "smoke", r: 410 }],
    [/^Wraith_Q_NearsightMissile_TrajectoryWarning$/, { ability: "Paranoia", shape: "hidden" }],
    // Clove (the _PDS smoke is cast after death)
    [/^Smonk_NewSmoke(_PDS)?$/, { ability: "Ruse", shape: "smoke", r: 410 }],
    [/^Smonk_Q_DecayExplosion$/, { ability: "Meddle", shape: "area", r: 450 }],
    // Cypher
    [/^Gumshoe_Q_Cage$/, { ability: "Cyber Cage", shape: "smoke", r: 330 }],
    [/^Gumshoe_Q_CageTrap$/, { ability: "Cyber Cage", shape: "badge" }],
    [/^Gumshoe_4_TripWire$/, { ability: "Trapwire", shape: "wire" }],
    [/^Gumshoe_4_TripWire_SecondWire$/, { ability: "Trapwire", shape: "hidden" }],
    [/^Gumshoe_E_PossessableCamera$/, { ability: "Spycam", shape: "badge" }],
    [/^Gumshoe_RemovableObject_GumshoeTrackingDart$/, { ability: "Spycam", shape: "badge", small: true, label: "Spycam dart" }],
    [/^Gumshoe_X_InterrogateHat$/, { ability: "Neural Theft", shape: "badge" }],
    // Sova
    [/^Hunter_Q_SonarPing$/, { ability: "Recon Bolt", shape: "reveal", r: 1500 }],
    [/^Hunter_Q_SonarBolt$/, { ability: "Recon Bolt", shape: "hidden" }],
    [/^Hunter_4_ExplosiveBolt_Explosion$/, { ability: "Shock Bolt", shape: "area", r: 350 }],
    [/^Hunter_E_Drone$/, { ability: "Owl Drone", shape: "badge" }],
    [/^Hunter_E_Drone_RevealDart$/, { ability: "Owl Drone", shape: "badge", small: true, label: "Owl Drone dart" }],
    // Waylay
    [/^Terra_E_RewindTime_RewindTarget$/, { ability: "Refract", shape: "badge", label: "Refract (return point)" }],
    [/^Terra_C_TimeSlowGrenade_Explosion$/, { ability: "Saturate", shape: "area", r: 500 }],
    [/^Terra_X_DelayedBeam/, { ability: "Convergent Paths", shape: "badge" }],
    // Iso
    [/^Sequoia_E_Shield$/, { ability: "Double Tap", shape: "hidden" }],
    [/^Sequoia_E_Orb$/, { ability: "Double Tap", shape: "badge", label: "Double Tap orb" }],
    [/^Sequoia_4_MovingCover$/, { ability: "Contingency", shape: "badge" }],
    [/^Sequoia_Q_FragileMissile_TrajectoryWarning$/, { ability: "Undercut", shape: "badge" }],
    [/^Sequoia_X_LineCapture$/, { ability: "Kill Contract", shape: "badge" }],
    // Phoenix
    [/^Phoenix_MolotovFire$/, { ability: "Hot Hands", shape: "area", r: 450 }],
    [/^Phoenix_Q_FlameWallManager/, { ability: "Blaze", shape: "badge" }],
    [/^Phoenix_X_ResTarget/, { ability: "Run it Back", shape: "badge", label: "Run it Back (return point)" }],
    // Skye
    [/^Guide_4_Heal_AOE$/, { ability: "Regrowth", shape: "area", r: 900 }],
    [/^Guide_E_HawkFlash_FlashSource$/, { ability: "Guiding Light", shape: "badge", unlisted: true }],
    [/^Guide_Q_PossessableScout$/, { ability: "Trailblazer", shape: "badge" }],
    // Astra: stars are placed markers; a fake nebula is a dissipate (a thin, fading smoke).
    [/^Rift_E_SmokeZone$/, { ability: "Nebula  / Dissipate", shape: "smoke", r: 475, label: "Nebula" }],
    [/^Rift_E_SmokeZone_Fake$/, { ability: "Nebula  / Dissipate", shape: "smoke", r: 475, faint: true, label: "Dissipate" }],
    [/^Rift_Q_FlashBurst$/, { ability: "Nova Pulse", shape: "area", r: 475 }],
    [/^Rift_4_BlackHole$/, { ability: "Gravity Well", shape: "area", r: 475 }],
    [/^Rift_X_GlobalWall$/, { ability: "Astral Form / Cosmic Divide", shape: "line", dir: "along", full: true, label: "Cosmic Divide" }],
    [/^Rift_X_Markers$/, { ability: "Astral Form / Cosmic Divide", shape: "badge", small: true, unlisted: true, label: "Astra star" }],
    // Gekko: globules are what's left to reclaim.
    [/^Aggrobot_C_ExplodeyPatch$/, { ability: "Mosh Pit", shape: "area", r: 450 }],
    [/^Aggrobot_SeekerNade$/, { ability: "Wingman", shape: "badge" }],
    [/^Aggrobot_RollyPolly$/, { ability: "Thrash", shape: "badge" }],
    [/^Aggrobot_(X_)?Reclaim_Orb/, { ability: "Wingman", shape: "badge", small: true, unlisted: true, label: "Gekko globule" }],
    // Raze
    [/^Clay_Q_Explosion$/, { ability: "Blast Pack", shape: "area", r: 450 }],
    [/^Clay_E_Boomba$/, { ability: "Boom Bot", shape: "badge" }],
    // Chamber
    [/^Deadeye_E_Trap$/, { ability: "Trademark", shape: "badge" }],
    [/^Deadeye_E_Slow_Large$/, { ability: "Trademark", shape: "area", r: 550, label: "Trademark slow" }],
    [/^Deadeye_E_Teleporter_Tether$/, { ability: "Rendezvous", shape: "badge" }],
    // Sage: the wall runs across its yaw (every Summit wall's four segments did); segments hidden.
    [/^Thorne_E_Wall_Fortifying$/, { ability: "Barrier Orb", shape: "line", dir: "across", len: 1040 }],
    [/^Thorne_E_Wall_Segment_Fortifying$/, { ability: "Barrier Orb", shape: "hidden" }],
    [/^Thorne_4_SlowField_Production$/, { ability: "Slow Orb", shape: "area", r: 600 }],
    // Reyna
    [/^Vampire_4_NearsightAOE_Source$/, { ability: "Leer", shape: "badge" }],
    [/^Vampire_Q_Heal_HealPool/, { ability: "Devour", shape: "badge", small: true, unlisted: true, label: "Soul orb" }],
    // Fade
    [/^BountyHunter_E_LoSReveal_Source_Reactivate$/, { ability: "Haunt", shape: "reveal", r: 1500 }],
    [/^BountyHunter_Q_Tether_SphereExpansion$/, { ability: "Seize", shape: "area", r: 600 }],
    [/^BountyHunter_X_WaveForm$/, { ability: "Nightfall", shape: "badge" }],
    // Veto
    [/^Pine_4_UsableTeleport$/, { ability: "Crosscut", shape: "badge" }],
    [/^Pine_Q_SeizeTrap$/, { ability: "Chokehold", shape: "badge" }],
    [/^Pine_Q_Tether_SphereExpansion$/, { ability: "Chokehold", shape: "area", r: 500 }],
    [/^Pine_E_RadEater$/, { ability: "Interceptor", shape: "badge" }],
    // Viper (the screen's direction isn't checked yet: a badge until a replay shows it)
    [/^Pandemic_E_SmokeScreenManager$/, { ability: "Toxic Screen", shape: "badge" }],
    [/^Pandemic_AcidMolotov_NewMolotov$/, { ability: "Snake Bite", shape: "area", r: 450 }],
    // Jett
    [/^Wushu_4_SmokeZone$/, { ability: "Cloudburst", shape: "smoke", r: 335 }],
    // Breach: both objects spawn 800 units ahead of Breach along its aim (every cast on Sunset);
    // the reach isn't decoded, so they are drawn as an aim from Breach, not a blast zone.
    [/^Breach_E_SweetSpotFissure$/, { ability: "Fault Line", shape: "line", dir: "along", from: -800, len: 800, aim: true }],
    [/^Breach_X_Shockwave$/, { ability: "Rolling Thunder", shape: "line", dir: "along", from: -800, len: 800, aim: true }],
    [/^Breach_4_FusionBlast$/, { ability: "Aftershock", shape: "area", r: 300 }],
    // Tejo
    [/^Cashew_4_SonarPing$/, { ability: "Stealth Drone", shape: "reveal", r: 1000 }],
    [/^Cashew_E_Explosion$/, { ability: "Guided Salvo", shape: "area", r: 400 }],
    [/^Cashew_E_MapMissileMarker/, { ability: "Guided Salvo", shape: "badge", small: true, unlisted: true, label: "Guided Salvo target" }],
    [/^Cashew_E_AirStrikeMortar$/, { ability: "Guided Salvo", shape: "hidden" }],
    [/^Cashew_Q_ShellShockGrenade/, { ability: "Special Delivery", shape: "area", r: 500 }],
    [/^Cashew_X_Segment$/, { ability: "Armageddon", shape: "area", r: 350 }],
    [/^Cashew_X_SegmentManager$/, { ability: "Armageddon", shape: "hidden" }],
    // Brimstone
    [/^Sarge_4_Smoke_Production/, { ability: "Sky Smoke", shape: "smoke", r: 415 }],
    [/^Sarge_4_SmokeManager/, { ability: "Sky Smoke", shape: "hidden" }],
    [/^Sarge_Q_Molotov_Production$/, { ability: "Incendiary", shape: "area", r: 450 }],
    [/^Sarge_E_SpeedStim$/, { ability: "Stim Beacon", shape: "area", r: 500 }],
    [/^Sarge_X_OrbitalStrike/, { ability: "Orbital Strike", shape: "area", r: 900 }],
    // Yoru
    [/^Stealth_4_Decoy_V2$/, { ability: "FAKEOUT", shape: "badge" }],
    [/^Stealth_DecoySpawner$/, { ability: "FAKEOUT", shape: "hidden" }],
    // Miks (`Thumper` objects are read as Miks's: see extras.CODE_ALIASES)
    [/^Iris_E_Smoke$/, { ability: "Waveform", shape: "smoke", r: 400 }],
    [/^Iris_X_SonicWave$/, { ability: "Bassquake", shape: "badge" }],
    [/^Iris_Concuss$/, { ability: "M-pulse", shape: "area", r: 500 }],
    [/^Iris_ConcussPulse$/, { ability: "M-pulse", shape: "hidden" }],
    [/^Iris_Heal$/, { ability: "Harmonize", shape: "area", r: 500 }]
  ];

  // A line ability's two ends in minimap uv, from its yaw (degrees on the map: 0 = +u, 90 = +v):
  // `along` the yaw from `from` to `from + len` world units, or `across` it, centred; `full` runs
  // the length of the map both ways (Astra's divide). Null without a yaw.
  function lineEnds(a, style, uvPerUnit) {
    if (a.yaw === null || a.yaw === undefined) return null;
    var rad = a.yaw * Math.PI / 180, du = Math.cos(rad), dv = Math.sin(rad);
    if (style.dir === "across") { var tmp = du; du = -dv; dv = tmp; }
    var lo, hi;
    if (style.full) { lo = -20000; hi = 20000; }
    else if (style.dir === "across") { lo = -style.len * uvPerUnit / 2; hi = style.len * uvPerUnit / 2; }
    else { lo = (style.from || 0) * uvPerUnit; hi = lo + style.len * uvPerUnit; }
    return [[a.u + du * lo, a.v + dv * lo], [a.u + du * hi, a.v + dv * hi]];
  }

  function abilityStyle(ability) {
    if (ability.kind === "Bomb") return { label: "Spike", shape: "spike" };
    if (ability.kind === "Projectile") return { label: ability.name, shape: "hidden" };
    var key = ability.code + "_" + ability.name;
    for (var i = 0; i < ABILITY_STYLES.length; i++) {
      if (ABILITY_STYLES[i][0].test(key)) {
        var style = ABILITY_STYLES[i][1];
        return {
          ability: style.ability, agent: style.agent || ability.agent, shape: style.shape, r: style.r,
          small: !!style.small, unlisted: !!style.unlisted, label: style.label || style.ability,
          faint: !!style.faint, dir: style.dir, len: style.len, from: style.from, full: !!style.full, aim: !!style.aim
        };
      }
    }
    return { label: ability.name.replace(/_/g, " "), agent: ability.agent, shape: "badge" };
  }

  // A W-e utility cast's ability id ("omen_paranoia", "phoenix_curveball_left") -> {agent, ability}.
  var UTIL_ABILITIES = {
    omen_paranoia: ["Omen", "Paranoia"], skye_guiding_light: ["Skye", "Guiding Light"],
    phoenix_curveball_left: ["Phoenix", "Curveball"], phoenix_curveball_right: ["Phoenix", "Curveball"]
  };

  function utilAbility(id) {
    var known = UTIL_ABILITIES[id];
    if (known) return { agent: known[0], ability: known[1] };
    var parts = String(id || "").split("_").filter(function (w) { return w && w !== "left" && w !== "right"; });
    var title = function (w) { return w.charAt(0).toUpperCase() + w.slice(1); };
    return parts.length > 1 ? { agent: title(parts[0]), ability: parts.slice(1).map(title).join(" ") } : { agent: null, ability: id };
  }

  // Where a moving pawn (a drone) is at t, from its [[t, u, v], ...] path, or null outside it.
  function pathAt(path, t) {
    if (!path || !path.length || t < path[0][0] || t > path[path.length - 1][0]) return null;
    for (var i = 1; i < path.length; i++) {
      if (path[i][0] >= t) {
        var p = path[i - 1], q = path[i], f = q[0] > p[0] ? (t - p[0]) / (q[0] - p[0]) : 0;
        return { u: p[1] + (q[1] - p[1]) * f, v: p[2] + (q[2] - p[2]) * f };
      }
    }
    return { u: path[0][1], v: path[0][2] };
  }

  // Abilities showing at time t: spawned by t and not yet closed (an unclosed one lasts the round).
  // A brief one (Regrowth's heal pulse lives 0.3 s) stays for ABILITY_MIN_S, fading after its close.
  var ABILITY_MIN_S = 1.0;

  function abilitiesAt(abilities, t, tEnd) {
    return (abilities || []).filter(function (a) {
      var until = a.t1 === null || a.t1 === undefined ? tEnd : Math.max(a.t1, a.t0 + ABILITY_MIN_S);
      return a.t0 <= t && t <= until;
    });
  }

  // How opaque an ability is at t: fading in over its first 0.25 s, and out after its close
  // while it lingers (see abilitiesAt).
  function abilityAlpha(a, t) {
    var alpha = Math.min(1, Math.max(0, (t - a.t0) / 0.25));
    if (a.t1 !== null && a.t1 !== undefined && t > a.t1) {
      alpha *= Math.max(0, 1 - (t - a.t1) / Math.max(0.2, a.t0 + ABILITY_MIN_S - a.t1));
    }
    return alpha;
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
    this.view = { k: 1, ox: 0, oy: 0 };          // canvas px = (map px - o) * k
    this.map.onload = (function () { this.fitView(); this.draw(); }).bind(this);
    this.map.src = options.mapImage;
    this.hover = null;
    this.bindControls();
    this.lastFrame = null;
    this.tick = this.tick.bind(this);
    requestAnimationFrame(this.tick);
  }

  // The smallest square around the minimap's opaque pixels, with a margin: the view the
  // canvas shows, so the map fills it instead of floating in the image's empty border.
  ReplayViewer.prototype.fitView = function () {
    var size = this.canvas.width, n = 256;
    try {
      var off = document.createElement("canvas");
      off.width = off.height = n;
      var octx = off.getContext("2d");
      octx.drawImage(this.map, 0, 0, n, n);
      var data = octx.getImageData(0, 0, n, n).data;
      var x0 = n, y0 = n, x1 = -1, y1 = -1;
      for (var y = 0; y < n; y++) {
        for (var x = 0; x < n; x++) {
          if (data[(y * n + x) * 4 + 3] > 16) {
            if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
          }
        }
      }
      if (x1 < 0) return;
      var scale = size / n, pad = 0.04 * n;
      var side = Math.max(x1 - x0, y1 - y0) + 2 * pad;
      var cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      var k = Math.min(3, n / side);
      this.view = { k: k, ox: Math.max(0, (cx - side / 2)) * scale, oy: Math.max(0, (cy - side / 2)) * scale };
    } catch (err) {
      this.view = { k: 1, ox: 0, oy: 0 };   // a tainted or unreadable image: show it whole
    }
  };

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

  // Stored rounds (Stage 2) carry abilities and shots as `util` kinds "ability" and "shot"
  // (app/replays/extras.py util_entries); the envelope's t/by were the row's t0|t / slot.
  var CAST_KINDS = { flash: true, nearsight: true };

  function extrasFromUtil(util) {
    var abilities = [], shots = [];
    (util || []).forEach(function (u) {
      if (u.k !== "ability" && u.k !== "shot") return;
      var row = {};
      Object.keys(u).forEach(function (key) { if (key !== "k" && key !== "t" && key !== "by") row[key] = u[key]; });
      row.slot = u.by;
      if (u.k === "ability") { row.t0 = u.t; abilities.push(row); } else { row.t = u.t; shots.push(row); }
    });
    return { abilities: abilities, shots: shots };
  }

  function castUtil(util) {
    return (util || []).filter(function (u) { return CAST_KINDS[u.k]; });
  }

  ReplayViewer.prototype.fetchRound = function (n) {
    if (!this.cache[n]) {
      this.cache[n] = Promise.resolve(this.options.loadRound(n)).then(function (blob) {
        var extras = blob.extras || extrasFromUtil(blob.util);
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
      self.renderUtilList();
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
      tip: q("[data-replay-tip]"), util: q("[data-replay-util]")
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
    [this.ui.feed, this.ui.util].forEach(function (list) {
      if (!list) return;
      var go = function (e) {
        var row = e.target.closest("[data-seek-t]");
        if (!row || (e.type === "keydown" && e.key !== "Enter")) return;
        if (e.type === "keydown") e.preventDefault();
        self.seek(Number(row.getAttribute("data-seek-t")));
      };
      list.addEventListener("click", go);
      list.addEventListener("keydown", go);
    });
    Array.prototype.forEach.call(this.root.querySelectorAll("[data-replay-tab]"), function (tab) {
      tab.addEventListener("click", function () { self.showTab(tab.getAttribute("data-replay-tab")); });
    });
    this.canvas.addEventListener("mousemove", function (e) { self.onHover(e); });
    this.canvas.addEventListener("mouseleave", function () { self.hover = null; self.hideTip(); });
  };

  ReplayViewer.prototype.showTab = function (name) {
    Array.prototype.forEach.call(this.root.querySelectorAll("[data-replay-tab]"), function (tab) {
      var on = tab.getAttribute("data-replay-tab") === name;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.classList.toggle("is-active", on);
    });
    Array.prototype.forEach.call(this.root.querySelectorAll("[data-replay-panel]"), function (panel) {
      panel.hidden = panel.getAttribute("data-replay-panel") !== name;
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
      return '<li class="replay-feed-row" data-kill-t="' + k.t + '" data-seek-t="' + (k.t - 3) + '" data-kill-i="' + k.i + '" tabindex="0">' +
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

  // The round's utility, in time order: placed objects (one row per trapwire, none for hidden
  // parts) and flash / nearsight casts. Each row: time, the ability's glyph on its owner's
  // colour, who, and what. A click seeks to 1 s before it.
  ReplayViewer.prototype.utilItems = function () {
    var self = this, blob = this.current.blob, items = [];
    (this.current.extras.abilities || []).forEach(function (a) {
      var style = abilityStyle(a);
      if (style.shape === "hidden" || style.shape === "spike" || style.small || style.unlisted) return;
      items.push({ t: a.thrown ? a.thrown.t0 : a.t0, slot: a.slot, agent: style.agent, ability: style.ability,
        label: style.label, guess: a.owner_by === "nearest" });
    });
    castUtil(blob.util).forEach(function (u) {
      var which = utilAbility(u.ability);
      var hit = (u.targets || []).length;
      items.push({ t: u.t, slot: u.by, agent: which.agent, ability: which.ability, label: which.ability,
        note: u.k === "flash" ? (hit ? "flashed " + hit : "flashed nobody") : (hit ? "nearsighted " + hit : "hit nobody") });
    });
    return items.sort(function (a, b) { return a.t - b.t; });
  };

  ReplayViewer.prototype.renderUtilList = function () {
    var list = this.ui.util, self = this;
    if (!list) return;
    var items = this.utilItems();
    if (!items.length) { list.innerHTML = '<li class="replay-feed-empty">No utility decoded this round.</li>'; return; }
    var table = this.options.abilityIcons || {};
    list.innerHTML = items.map(function (it) {
      var url = it.agent && it.ability && table[it.agent] && table[it.agent][it.ability];
      var icon = '<span class="replay-util-icon" style="background:' + self.ownerColor(it.slot) + '">' +
        (url ? '<img src="' + escapeHtml(url) + '" alt="">' : "") + "</span>";
      var who = it.slot === null || it.slot === undefined ? '<span class="replay-person">owner unknown</span>'
        : self.personHtml(it.slot);
      return '<li class="replay-feed-row replay-util-row" data-kill-t="' + it.t + '" data-seek-t="' + (it.t - 1) + '" tabindex="0">' +
        '<span class="replay-feed-t">' + Math.max(0, it.t).toFixed(1) + "s</span>" + icon +
        '<span class="replay-feed-who">' + who + ' <span class="replay-feed-gun">' + escapeHtml(it.label) +
        (it.note ? " · " + escapeHtml(it.note) : "") + (it.guess ? " · owner a guess" : "") + "</span></span></li>";
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
      Array.prototype.forEach.call(this.root.querySelectorAll("[data-kill-t]"), function (row) {
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

  // An ability's glyph (white on transparency, static/img/abilities/), or null until it loads.
  ReplayViewer.prototype.abilityIcon = function (agent, ability) {
    var table = this.options.abilityIcons || {};
    var url = agent && ability && table[agent] && table[agent][ability];
    if (!url) return null;
    if (!(url in this.icons)) {
      var img = new Image();
      img.onload = this.draw.bind(this);
      img.src = url;
      this.icons[url] = img;
    }
    var found = this.icons[url];
    return found.complete && found.naturalWidth ? found : null;
  };

  ReplayViewer.prototype.ownerColor = function (slot) {
    return slot === null || slot === undefined ? this.css("--replay-unknown", "#77787f") : this.slotColor(slot);
  };

  // A team-coloured disc with the ability's white glyph and a dark surface ring, valoplant-style.
  ReplayViewer.prototype.drawBadge = function (ctx, x, y, radius, color, glyph, alpha) {
    ctx.save();
    ctx.globalAlpha = alpha === undefined ? 1 : alpha;
    ctx.beginPath(); ctx.arc(x, y, radius + 2, 0, 2 * Math.PI);
    ctx.fillStyle = "rgba(12, 12, 16, 0.85)"; ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, radius, 0, 2 * Math.PI);
    ctx.fillStyle = color; ctx.fill();
    if (glyph) {
      var g = radius * 1.35;
      ctx.drawImage(glyph, x - g / 2, y - g / 2, g, g);
    } else {
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(x, y, radius * 0.3, 0, 2 * Math.PI); ctx.fill();
    }
    ctx.restore();
  };

  // Where an ability is drawn at t: a moving pawn follows its path, anything else stays put.
  ReplayViewer.prototype.abilityPoint = function (a, t, s) {
    var at = a.path ? pathAt(a.path, t) : null;
    return at ? { x: at.u * s, y: at.v * s } : { x: a.u * s, y: a.v * s };
  };

  ReplayViewer.prototype.drawAbilities = function (ctx, s, r, hits) {
    var blob = this.current.blob, t = this.t, self = this;
    var showing = abilitiesAt(this.current.extras.abilities, t, blob.t_end);
    var all = this.current.extras.abilities;
    var smoke = this.css("--replay-smoke", "rgba(214, 218, 226, 0.5)");
    // Areas first, so badges sit on top of them; the spike last.
    ["smoke", "area", "reveal", "line", "wire", "badge", "spike"].forEach(function (pass) {
      showing.forEach(function (a) {
        var style = abilityStyle(a);
        if (style.shape !== pass) return;
        var at = self.abilityPoint(a, t, s), x = at.x, y = at.y;
        var color = self.ownerColor(a.slot);
        var glyph = self.abilityIcon(style.agent, style.ability);
        var text = self.abilityText(a, style);
        var age = t - a.t0, fadeIn = abilityAlpha(a, t);
        ctx.save();
        if (pass === "smoke" || pass === "area") {
          var rad = self.uvRadius(style.r) * s;
          ctx.globalAlpha = fadeIn;
          ctx.beginPath(); ctx.arc(x, y, rad, 0, 2 * Math.PI);
          if (pass === "smoke") {
            if (style.faint) ctx.globalAlpha = 0.45 * fadeIn;
            var grad = ctx.createRadialGradient(x, y, rad * 0.2, x, y, rad);
            grad.addColorStop(0, "rgba(232, 234, 240, 0.72)");
            grad.addColorStop(1, smoke);
            ctx.fillStyle = grad; ctx.fill();
          } else {
            ctx.globalAlpha = 0.3 * fadeIn; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = fadeIn;
          }
          ctx.lineWidth = 2.5; ctx.strokeStyle = color;
          if (style.faint) ctx.setLineDash([r / 3, r / 4]);
          ctx.stroke();
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.52, color, glyph, 0.9 * fadeIn);
          hits.push({ x: x, y: y, r: Math.max(rad, r * 0.6), text: text, area: true });
        } else if (pass === "reveal") {
          var reach = self.uvRadius(style.r) * s, pulse = (age % 1.2) / 1.2;
          ctx.globalAlpha = 0.8 * (1 - pulse); ctx.lineWidth = 2.5; ctx.strokeStyle = color;
          ctx.beginPath(); ctx.arc(x, y, reach * (0.15 + 0.85 * pulse), 0, 2 * Math.PI); ctx.stroke();
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.6, color, glyph);
          hits.push({ x: x, y: y, r: r * 0.8, text: text });
        } else if (pass === "line") {
          var ends = lineEnds(a, style, (self.linked && self.linked.uvPerUnit) || 0.75);
          if (ends) {
            var x0 = ends[0][0] * s, y0 = ends[0][1] * s, x1 = ends[1][0] * s, y1 = ends[1][1] * s;
            ctx.globalAlpha = fadeIn; ctx.strokeStyle = color; ctx.lineCap = "round";
            if (style.aim) {
              // An aim: dashed from the caster, with an arrowhead where the object spawned.
              ctx.lineWidth = Math.max(2, r / 5); ctx.setLineDash([r / 3, r / 4]);
              ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); ctx.setLineDash([]);
              var ang = Math.atan2(y1 - y0, x1 - x0), head = r * 0.7;
              var tail = Math.hypot(x1 - x0, y1 - y0) * 2.5;
              var tx = x1 + tail * Math.cos(ang), ty = y1 + tail * Math.sin(ang);
              var fadeTail = ctx.createLinearGradient(x1, y1, tx, ty);
              fadeTail.addColorStop(0, color); fadeTail.addColorStop(1, "rgba(0, 0, 0, 0)");
              ctx.save(); ctx.strokeStyle = fadeTail; ctx.setLineDash([r / 3, r / 4]);
              ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(tx, ty); ctx.stroke(); ctx.restore();
              ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(x1, y1);
              ctx.lineTo(x1 - head * Math.cos(ang - 0.45), y1 - head * Math.sin(ang - 0.45));
              ctx.lineTo(x1 - head * Math.cos(ang + 0.45), y1 - head * Math.sin(ang + 0.45));
              ctx.closePath(); ctx.fill();
            } else {
              ctx.lineWidth = Math.max(4, r / 2.2); ctx.globalAlpha = 0.85 * fadeIn;
              ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
            }
          }
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.5, color, glyph, fadeIn);
          hits.push({ x: x, y: y, r: r * 0.7, text: text });
        } else if (pass === "wire") {
          var other = self.current.wires[all.indexOf(a)];
          ctx.strokeStyle = color; ctx.lineWidth = Math.max(2.5, r / 4.5);
          if (other) {
            ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(other.u * s, other.v * s); ctx.stroke();
            ctx.fillStyle = color; ctx.beginPath(); ctx.arc(other.u * s, other.v * s, r * 0.22, 0, 2 * Math.PI); ctx.fill();
          }
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.56, color, glyph);
          hits.push({ x: x, y: y, r: r * 0.75, text: text });
        } else if (pass === "badge") {
          var radius = r * (style.small ? 0.42 : 0.62);
          if (a.path) {
            // The trail of the last 3 s behind a drone or Trailblazer.
            ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.globalAlpha = 0.55; ctx.setLineDash([r / 4, r / 5]);
            ctx.beginPath();
            var started = false;
            a.path.forEach(function (pt) {
              if (pt[0] < t - 3 || pt[0] > t) return;
              if (!started) { ctx.moveTo(pt[1] * s, pt[2] * s); started = true; } else ctx.lineTo(pt[1] * s, pt[2] * s);
            });
            if (started) { ctx.lineTo(x, y); ctx.stroke(); }
            ctx.setLineDash([]); ctx.globalAlpha = 1;
          }
          self.drawBadge(ctx, x, y, radius, color, glyph, fadeIn);
          hits.push({ x: x, y: y, r: radius * 1.4, text: text });
        } else if (pass === "spike") {
          var k = r * 0.7, beat = 0.5 + 0.5 * Math.sin((t - a.t0) * Math.PI * 2);
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

  // A throw in flight: a dashed line from the thrower to where it lands, with the glyph riding
  // it, then a short fade after it lands.
  ReplayViewer.prototype.drawThrows = function (ctx, s, r) {
    var t = this.t, self = this;
    (this.current.extras.abilities || []).forEach(function (a) {
      var th = a.thrown;
      if (!th || t < th.t0 || t > th.t1 + 0.6) return;
      var style = abilityStyle(a);
      if (style.shape === "hidden") return;
      var x0 = th.u * s, y0 = th.v * s, x1 = a.u * s, y1 = a.v * s;
      var f = th.t1 > th.t0 ? Math.min(1, (t - th.t0) / (th.t1 - th.t0)) : 1;
      var color = self.ownerColor(a.slot);
      ctx.save();
      ctx.globalAlpha = t > th.t1 ? 1 - (t - th.t1) / 0.6 : 0.9;
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash([r / 3, r / 4]);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f); ctx.stroke();
      ctx.restore();
      if (t <= th.t1) {
        self.drawBadge(ctx, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, r * 0.34, color,
          self.abilityIcon(style.agent, style.ability));
      }
    });
  };

  ReplayViewer.prototype.abilityText = function (a, style) {
    var owner = a.slot === null ? "owner unknown" : this.nameOf(a.slot) +
      (a.owner_by === "nearest" ? " (nearest " + a.agent + ", a guess)" : "");
    return (style.agent ? style.agent + " · " : "") + style.label + " · " + owner + " · " + a.t0.toFixed(1) + "–" +
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

  ReplayViewer.prototype.drawUtil = function (ctx, s, r, hits) {
    // W-e flash and nearsight casts: the ability's badge with an expanding ring for 1.2 s at the
    // cast point, and a dashed line to each player it hit.
    var blob = this.current.blob, t = this.t, tracks = this.current.tracks, self = this;
    castUtil(blob.util).forEach(function (u) {
      var age = t - u.t;
      if (age < 0 || age > 1.2 || u.u === undefined) return;
      var x = u.u * s, y = u.v * s, color = self.ownerColor(u.by), fade = 1 - age / 1.2;
      var which = utilAbility(u.ability);
      ctx.save();
      ctx.globalAlpha = fade;
      ctx.strokeStyle = u.k === "flash" ? "#ffffff" : color;
      ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(x, y, r * (0.7 + 2.2 * (age / 1.2)), 0, 2 * Math.PI); ctx.stroke();
      (u.targets || []).forEach(function (slot) {
        var at = trackAt(tracks[String(slot)], u.t);
        if (!at) return;
        ctx.setLineDash([r / 3, r / 4]);
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(at.u * s, at.v * s); ctx.stroke();
      });
      ctx.restore();
      self.drawBadge(ctx, x, y, r * 0.5, color, self.abilityIcon(which.agent, which.ability), fade);
      var hitNames = (u.targets || []).map(function (slot) { return self.nameOf(slot); });
      hits.push({ x: x, y: y, r: r * 0.8, text: (which.agent ? which.agent + " · " : "") + which.ability + " · " +
        self.nameOf(u.by) + " at " + u.t.toFixed(1) + " s" + (hitNames.length ? " · hit " + hitNames.join(", ") : " · hit nobody") });
    });
  };

  ReplayViewer.prototype.draw = function () {
    var ctx = this.ctx, size = this.canvas.width, s = size / UV, view = this.view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.setTransform(view.k, 0, 0, view.k, -view.ox * view.k, -view.oy * view.k);
    if (this.map.complete && this.map.naturalWidth) ctx.drawImage(this.map, 0, 0, size, size);
    if (!this.current) { ctx.setTransform(1, 0, 0, 1, 0, 0); return; }
    var blob = this.current.blob, tracks = this.current.tracks, t = this.t, self = this;
    var r = size / 48 / Math.sqrt(view.k);   // zoomed marks grow, but only by the square root
    var hits = [];

    if (this.layers.abilities) {
      this.drawAbilities(ctx, s, r, hits);
      this.drawThrows(ctx, s, r);
      this.drawUtil(ctx, s, r, hits);
    }
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
    // Stacked players: a label that would cover an earlier one moves below it, with a thin
    // leader back to its player.
    var placed = [];
    labels.forEach(function (l) {
      var text = l.text.split("#")[0];
      text = text.length > 14 ? text.slice(0, 13) + "…" : text;
      var w = ctx.measureText(text).width + r * 0.5, h = r * 1.05, y = l.y, moved = false;
      for (var guard = 0; guard < 10; guard++) {
        var clash = placed.filter(function (b) {
          return Math.abs(b.x - l.x) < (b.w + w) / 2 && y < b.y + b.h && y + h > b.y;
        })[0];
        if (!clash) break;
        y = clash.y + clash.h + 1;
        moved = true;
      }
      if (moved) {
        ctx.strokeStyle = "rgba(255, 255, 255, 0.35)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(l.x, l.y - r * 0.2); ctx.lineTo(l.x, y); ctx.stroke();
      }
      ctx.fillStyle = "rgba(10, 10, 12, 0.78)";
      ctx.fillRect(l.x - w / 2, y, w, h);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(text, l.x, y + r * 0.1);
      placed.push({ x: l.x, y: y, w: w, h: h });
    });
    ctx.restore();

    ctx.setTransform(1, 0, 0, 1, 0, 0);
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
    var v = this.view;
    this.hover = { x: (e.clientX - rect.left) * scale / v.k + v.ox, y: (e.clientY - rect.top) * scale / v.k + v.oy,
                   px: e.clientX - rect.left, py: e.clientY - rect.top };
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
    abilityStyle: abilityStyle, lineEnds: lineEnds, abilitiesAt: abilitiesAt, abilityAlpha: abilityAlpha, pairWires: pairWires, signed: signed, tallyAt: tallyAt,
    utilAbility: utilAbility, pathAt: pathAt, extrasFromUtil: extrasFromUtil, castUtil: castUtil
  };
  global.Replay = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

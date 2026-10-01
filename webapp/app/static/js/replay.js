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
  // (the key in static/data/abilities.json, which gives its icon); `shape` is smoke (a dark
  // disc), area (a team-tinted disc), reveal (a pulsing ring), wire (a line to its paired end),
  // line (a wall or an aim, from the object's yaw: see lineEnds), wall (Viper's screen, along its
  // laid points), badge (a team-coloured disc with the ability's glyph) or hidden. `pop` (seconds):
  // the ability goes off in an instant though its object lives on, so it shows only until that long
  // after its last pop (see popTimes), each pop drawn as a burst. `r` and `len` are world units. First match wins; an
  // unknown archetype is a badge with no glyph. Projectiles are never drawn (their object is).
  // Radii are approximate in-game sizes, tuned by eye on the map.
  var ABILITY_STYLES = [
    // Omen
    [/^Wraith_4_Smoke$/, { ability: "Dark Cover", shape: "smoke", r: 410 }],
    [/^Wraith_Q_NearsightMissile_TrajectoryWarning$/, { ability: "Paranoia", shape: "hidden" }],
    // Clove (the _PDS smoke is cast after death)
    [/^Smonk_NewSmoke(_PDS)?$/, { ability: "Ruse", shape: "smoke", r: 410 }],
    [/^Smonk_Q_DecayExplosion$/, { ability: "Meddle", shape: "area", r: 450, pop: 1.0 }],
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
    [/^Terra_C_TimeSlowGrenade_Explosion$/, { ability: "Saturate", shape: "area", r: 500, pop: 1.0 }],
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
    [/^Rift_Q_FlashBurst$/, { ability: "Nova Pulse", shape: "area", r: 475, pop: 1.0 }],
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
    // Viper: the screen is the line it was laid along (`points`), solid while up (`on`).
    [/^Pandemic_E_SmokeScreenManager$/, { ability: "Toxic Screen", shape: "wall" }],
    [/^Pandemic_4_SmokeZone$/, { ability: "Poison Cloud", shape: "smoke", r: 450 }],
    [/^Pandemic_X_Circular$/, { ability: "Viper's Pit", shape: "smoke", r: 900 }],
    [/^Pandemic_AcidMolotov_NewMolotov$/, { ability: "Snake Bite", shape: "area", r: 450 }],
    // Jett
    [/^Wushu_4_SmokeZone$/, { ability: "Cloudburst", shape: "smoke", r: 335 }],
    // Breach: both objects spawn 800 units ahead of Breach along its aim (every cast on Sunset);
    // the reach isn't decoded, so they are drawn as an aim from Breach, not a blast zone.
    // Fault Line fires 1.1 s after the cast (its own effect); Rolling Thunder records none, so its
    // wave shows 1.5 s. Aftershock's blasts hit 2.2 and 2.8 s after it sticks: 3 s in all.
    [/^Breach_E_SweetSpotFissure$/, { ability: "Fault Line", shape: "line", dir: "along", from: -800, len: 800, aim: true, pop: 0.8 }],
    [/^Breach_X_Shockwave$/, { ability: "Rolling Thunder", shape: "line", dir: "along", from: -800, len: 800, aim: true, pop: 1.5 }],
    [/^Breach_4_FusionBlast$/, { ability: "Aftershock", shape: "area", r: 300, pop: 3.0 }],
    // KAY/O: ZERO/point's knife pulses 1 s after it sticks; its object lingers 15 s in the replay.
    [/^Grenadier_E_SuppressionPulse$/, { ability: "ZERO/point", shape: "area", r: 700, pop: 1.0 }],
    // Tejo
    [/^Cashew_4_SonarPing$/, { ability: "Stealth Drone", shape: "reveal", r: 1000 }],
    [/^Cashew_E_Explosion$/, { ability: "Guided Salvo", shape: "area", r: 400 }],
    [/^Cashew_E_MapMissileMarker/, { ability: "Guided Salvo", shape: "badge", small: true, unlisted: true, label: "Guided Salvo target" }],
    [/^Cashew_E_AirStrikeMortar$/, { ability: "Guided Salvo", shape: "hidden" }],
    [/^Cashew_Q_ShellShockGrenade/, { ability: "Special Delivery", shape: "area", r: 500, pop: 0.8 }],
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
    // M-pulse pulses at +0, +2 and +4 s (its own effects): a ring each, not a 5 s disc.
    [/^Iris_Concuss$/, { ability: "M-pulse", shape: "area", r: 500, pop: 0.8 }],
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
          faint: !!style.faint, dir: style.dir, len: style.len, from: style.from, full: !!style.full, aim: !!style.aim,
          pop: style.pop
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
      var pop = a.kind === "Projectile" || a.kind === "Bomb" || !a.name ? undefined : abilityStyle(a).pop;
      if (pop !== undefined) until = Math.min(until, popUntil(a, pop));
      // Shot and destroyed before its object closed (a camera, a trip): going off never counts.
      if (typeof a.gone === "number") until = Math.min(until, a.gone + GONE_S);
      return a.t0 <= t && t <= until;
    });
  }

  var GONE_S = 0.6;

  // When a pop ability went off: the effects it played on itself (`fx`), or its spawn.
  function popTimes(a) {
    return a.fx && a.fx.length ? a.fx : [a.t0];
  }

  function popUntil(a, pop) {
    var pops = popTimes(a);
    return pops[pops.length - 1] + pop;
  }

  // The statuses on players at t (concussed, hindered, suppressed, fragile, tethered, decayed, slowed).
  function statusesAt(statuses, t) {
    return (statuses || []).filter(function (st) {
      return st.t0 <= t && t <= (typeof st.t1 === "number" ? st.t1 : st.t0 + 1);
    });
  }

  // Each status's colour and its label (drawn beside the colour, never colour alone).
  var STATUS_STYLES = {
    concussed: { color: "#f2c230", label: "CONCUSSED" }, hindered: { color: "#4da3ff", label: "HINDERED" },
    suppressed: { color: "#b06cff", label: "SUPPRESSED" }, fragile: { color: "#ff5ea8", label: "FRAGILE" },
    tethered: { color: "#2cd5c4", label: "TETHERED" }, decayed: { color: "#c0463f", label: "DECAYED" },
    slowed: { color: "#7cc9ff", label: "SLOWED" }, hit: { color: "#e8e8e8", label: "HIT" }
  };

  function statusStyle(name) {
    return STATUS_STYLES[name] || { color: "#e8e8e8", label: String(name || "status").toUpperCase() };
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

  // Stage 4: the players alive per team at t, from the page's steps [[t, team-1, team-2], ...].
  function aliveCountAt(steps, t) {
    var now = null;
    (steps || []).forEach(function (step) { if (step[0] <= t) now = step; });
    return now ? [now[1], now[2]] : null;
  }

  // The latest analysis state at or before t (state_replay's man-advantage states).
  function stateAt(annotations, t) {
    var now = null;
    ((annotations && annotations.states) || []).forEach(function (s) { if (s.t <= t) now = s; });
    return now;
  }

  // Kills, deaths and assists up to t, per slot, from the blob's kills (assists: the page's
  // `assists` slots on each kill, from the match's own kill rows).
  function tallyAt(kills, t) {
    var out = {};
    var row = function (slot) { return (out[slot] = out[slot] || { k: 0, d: 0, a: 0 }); };
    (kills || []).forEach(function (k) {
      if (k.t > t) return;
      if (k.killer !== k.victim) row(k.killer).k += 1;
      row(k.victim).d += 1;
      (k.assists || []).forEach(function (slot) { if (slot !== k.killer && slot !== k.victim) row(slot).a += 1; });
    });
    return out;
  }

  // Each slot's Impact from kills and deaths up to t (the per-kill split: the killer gains `gain`,
  // the victim loses `loss`), or null when the round's kills carry no split.
  function impactAt(kills, t) {
    var out = {}, any = false;
    (kills || []).forEach(function (k) {
      if (typeof k.gain !== "number") return;
      any = true;
      if (k.t > t) return;
      if (k.killer !== k.victim) out[k.killer] = (out[k.killer] || 0) + k.gain;
      out[k.victim] = (out[k.victim] || 0) - k.loss;
    });
    return any ? out : null;
  }

  // "Next kill": 1 s before the first kill that starts after t (so pressing it again moves on), or null.
  var NEXT_KILL_LEAD_S = 1;

  function nextKillTime(kills, t) {
    var next = null;
    (kills || []).forEach(function (k) {
      var at = Math.max(0, k.t - NEXT_KILL_LEAD_S);
      if (at > t + 0.05 && (next === null || at < next)) next = at;
    });
    return next;
  }

  // "Previous kill": 1 s before the last kill whose lead-in starts before t (so pressing it again
  // keeps going back), or null before the round's first kill.
  function prevKillTime(kills, t) {
    var prev = null;
    (kills || []).forEach(function (k) {
      var at = Math.max(0, k.t - NEXT_KILL_LEAD_S);
      if (at < t - 0.05 && (prev === null || at > prev)) prev = at;
    });
    return prev;
  }

  // The spike at t, from its ability row (kind "Bomb": spawned at the plant, with `defuses`
  // [[from, to | null, slot, finished]]): {plantedAt, slot, left (s to detonation), exploded,
  // halved, defusing: {slot, elapsed, needed, frac} | null, defused: {slot, t} | null}, or null
  // before the plant. A defuse takes DEFUSE_S; once one is held HALF_DEFUSE_S the next needs half.
  var SPIKE_S = 45, DEFUSE_S = 7, HALF_DEFUSE_S = 3.5;

  function spikeAt(abilities, t) {
    var spike = (abilities || []).filter(function (a) { return a.kind === "Bomb" && a.t0 <= t; })[0];
    if (!spike) return null;
    var out = { plantedAt: spike.t0, slot: spike.slot, left: Math.max(0, SPIKE_S - (t - spike.t0)),
                exploded: false, halved: false, defusing: null, defused: null };
    (spike.defuses || []).forEach(function (d) {
      var from = d[0], to = d[1] === null || d[1] === undefined ? Infinity : d[1];
      if (from > t || out.defused) return;
      if (d[3] && to <= t) { out.defused = { slot: d[2], t: to }; return; }
      var held = Math.min(t, to) - from;
      if (t <= to) {
        var needed = out.halved ? HALF_DEFUSE_S : DEFUSE_S;
        out.defusing = { slot: d[2], elapsed: held, needed: needed, frac: Math.min(1, held / needed) };
      }
      if (held >= HALF_DEFUSE_S) out.halved = true;
    });
    out.exploded = !out.defused && t - spike.t0 >= SPIKE_S;
    if (out.defused || out.exploded) out.defusing = null;
    return out;
  }

  // Whether Viper's wall is up at t, from its `on` spans [[from, to | null], ...].
  function wallUp(ability, t) {
    return (ability.on || []).some(function (span) { return span[0] <= t && (span[1] === null || t <= span[1]); });
  }

  // The reveals showing at t (a reveal without an end shows for 2 s).
  function revealsAt(reveals, t) {
    return (reveals || []).filter(function (r) {
      return r.t0 <= t && t <= (r.t1 === null || r.t1 === undefined ? r.t0 + 2 : r.t1);
    });
  }

  // The page's site data for one round (its `rounds[n]`: DB outcome, stats, alive steps,
  // annotations, and per-kill fields by the kill's index) merged onto the stored blob.
  function withSiteData(site, blob) {
    if (!site) return blob;
    blob.db = site.db;
    blob.stats = site.stats;
    blob.alive_steps = site.alive_steps;
    blob.annotations = site.annotations;
    (blob.kills || []).forEach(function (k) {
      var extra = site.kills && site.kills[String(k.i)];
      if (extra) Object.keys(extra).forEach(function (key) { k[key] = extra[key]; });
    });
    return blob;
  }

  // Map control's table (Stage 4), from /replays/{uuid}/control/players.json: one list of rows per
  // team, and each team's redundant control. `scope` is "round" (round `n`) or "match";
  // groupTeam maps the summaries' side groups (A/B) to "team-1" / "team-2". Null when the round has
  // no stored control.
  function controlRows(tables, scope, n, groupTeam) {
    var src = scope === "match" ? tables && tables.match : tables && tables.rounds && tables.rounds[String(n)];
    if (!src || (scope !== "match" && src.status !== "ok")) return null;
    var out = { "team-1": [], "team-2": [], redundant: {}, stale: !!src.stale };
    Object.keys(src.players || {}).sort(function (a, b) { return a - b; }).forEach(function (slot) {
      var p = src.players[slot], team = groupTeam[p.team] || (p.team === "B" ? "team-2" : "team-1");
      var lost = null;
      if (scope === "match") {
        // Per death on average, and the summed loss over the summed area held (a ratio of sums).
        lost = p.deaths ? { m2: p.lost_mean_m2, share: p.lost_share, deaths: p.deaths } : null;
      } else if ((p.lost || []).length) {
        var shared = 0, held = 0;
        lost = { m2: 0, share: null, deaths: p.lost.length };
        p.lost.forEach(function (d) {
          lost.m2 += d.control_m2 || 0;
          if (d.share_of_team) { shared += d.control_m2 || 0; held += (d.control_m2 || 0) / d.share_of_team; }
        });
        if (held > 0) lost.share = shared / held;
      }
      out[team].push({ slot: Number(slot), control: p.control_m2, active: p.active_m2, passive: p.passive_m2,
                       ratio: p.active_ratio, lost: lost, alive: p.alive_s,
                       taken: scope === "match" ? p.taken_per_round_m2 : p.taken_m2 });
    });
    Object.keys(src.redundant_m2 || {}).forEach(function (group) {
      out.redundant[groupTeam[group] || (group === "B" ? "team-2" : "team-1")] = src.redundant_m2[group];
    });
    return out;
  }

  // ------------------------------------------------------------ the viewer

  var LAYERS = ["names", "abilities", "tracers", "cones", "control"];
  var CONTROL_KEEP = 3;    // rounds of decoded control kept: the current one and its neighbours
  var CONTROL_PX = 512;    // the layer's offscreen image (4 px a cell)
  var CONTROL_STATUS = {
    not_ready: "Map control isn't ready for this round yet.",
    failed: "Map control couldn't be computed for this round.",
    unavailable: "Map control isn't available for this round.",
    error: "Map control couldn't be loaded."
  };

  function controlApi() {
    if (global.ReplayControl) return global.ReplayControl;
    return typeof require === "function" ? require("./replay_control.js") : null;
  }
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
    this.layers = { names: true, abilities: true, tracers: true, cones: true, control: false };
    // Map control (Stage 4): off by default; the page's match.control says the map has it.
    this.control = options.control && options.loadControl && controlApi() ? options.control : null;
    this.controlCache = this.control ? new (controlApi().ControlCache)(options.loadControl, CONTROL_KEEP) : null;
    this.controlPaint = null;                      // what the offscreen images show
    this.controlScope = "round";
    this.controlTables = null;                     // the players.json answer, once loaded
    this.highlight = null;                         // the slot whose control is highlighted
    this.canvas = root.querySelector("[data-replay-canvas]");
    this.ctx = this.canvas.getContext("2d");
    this.map = new Image();
    this.view = { k: 1, ox: 0, oy: 0 };          // canvas px = (map px - o) * k
    this.map.onload = (function () { this.fitView(); this.draw(); if (this.heat) this.paintHeatmap(); }).bind(this);
    this.map.src = options.mapImage;
    this.hover = null;
    this.bindControls();
    this.bindHeatmap();
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
    var abilities = [], shots = [], reveals = [], statuses = [];
    var into = { ability: abilities, reveal: reveals, status: statuses };
    (util || []).forEach(function (u) {
      if (u.k !== "shot" && !into[u.k]) return;
      var row = {};
      Object.keys(u).forEach(function (key) { if (key !== "k" && key !== "t" && key !== "by") row[key] = u[key]; });
      row.slot = u.by;
      if (u.k === "shot") { row.t = u.t; shots.push(row); }
      else { row.t0 = u.t; into[u.k].push(row); }
    });
    var out = { abilities: abilities, shots: shots };
    if (reveals.length) out.reveals = reveals;
    if (statuses.length) out.statuses = statuses;
    return out;
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
      self.renderAnalysis();
      self.renderUtilList();
      self.controlPaint = null;
      self.refreshControl();
      self.renderControlTable();
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

  // Jumps to 1 s before the next kill in this round, keeping play or pause as it was.
  ReplayViewer.prototype.nextKill = function () {
    if (!this.current) return;
    var at = nextKillTime(this.current.blob.kills, this.t);
    if (at !== null) this.seek(at);
  };

  // Jumps to 1 s before the previous kill in this round.
  ReplayViewer.prototype.prevKill = function () {
    if (!this.current) return;
    var at = prevKillTime(this.current.blob.kills, this.t);
    if (at !== null) this.seek(at);
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
      badge: q("[data-replay-badge]"), analysis: q("[data-replay-analysis]"),
      tip: q("[data-replay-tip]"), util: q("[data-replay-util]"), hud: q("[data-replay-hud]"),
      nextKill: q("[data-replay-nextkill]"), prevKill: q("[data-replay-prevkill]"),
      controlStatus: q("[data-replay-control-status]"), controlLegend: q("[data-replay-control-legend]"),
      controlTable: q("[data-replay-control-table]")
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
    if (this.ui.nextKill) this.ui.nextKill.addEventListener("click", function () { self.nextKill(); });
    if (this.ui.prevKill) this.ui.prevKill.addEventListener("click", function () { self.prevKill(); });
    this.ui.next.addEventListener("click", function () { self.step(1); });
    this.root.addEventListener("keydown", function (e) {
      if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && e.key !== " ") return;
      if (e.key === " ") { e.preventDefault(); self.toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); self.seek(self.t - STEP_S); }
      else if (e.key === "ArrowRight") { e.preventDefault(); self.seek(self.t + STEP_S); }
      else if (e.key === "n" || e.key === "N") { e.preventDefault(); self.nextKill(); }
      else if (e.key === "b" || e.key === "B") { e.preventDefault(); self.prevKill(); }
      else if (e.key === "Escape" && self.highlight !== null) { e.preventDefault(); self.setHighlight(null); }
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
        if (name === "control") self.refreshControl();
        self.draw();
      });
    });
    if (!this.control) this.layers.control = false;
    this.controlView = "true";
    this.ui.controlView = q("[data-replay-control-view]");
    this.ui.controlViewWrap = q("[data-replay-control-view-wrap]");
    if (this.ui.controlView) {
      this.ui.controlView.addEventListener("change", function () {
        self.controlView = self.ui.controlView.value;
        self.controlPaint = null;
        self.refreshControl();
        self.draw();
      });
    }
    this.unknownView = "both";
    this.ui.controlUnknown = q("[data-replay-control-unknown]");
    this.ui.controlUnknownWrap = q("[data-replay-control-unknown-wrap]");
    if (this.ui.controlUnknown) {
      this.ui.controlUnknown.addEventListener("change", function () {
        self.unknownView = self.ui.controlUnknown.value;
        self.controlPaint = null;
        self.draw();
      });
    }
    Array.prototype.forEach.call(this.root.querySelectorAll("[data-replay-control-scope]"), function (button) {
      button.addEventListener("click", function () {
        self.controlScope = button.getAttribute("data-replay-control-scope");
        Array.prototype.forEach.call(self.root.querySelectorAll("[data-replay-control-scope]"), function (b) {
          var on = b === button;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
        });
        self.renderControlTable();
      });
    });
    if (this.ui.controlTable) {
      var pick = function (e) {
        var row = e.target.closest("[data-control-slot]");
        if (!row || (e.type === "keydown" && e.key !== "Enter")) return;
        if (e.type === "keydown") e.preventDefault();
        self.setHighlight(Number(row.getAttribute("data-control-slot")));
      };
      this.ui.controlTable.addEventListener("click", pick);
      this.ui.controlTable.addEventListener("keydown", pick);
    }
    this.canvas.addEventListener("click", function (e) { self.onCanvasClick(e); });
    [this.ui.feed, this.ui.util, this.ui.analysis].forEach(function (list) {
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
    this.tab = name;
    if (name === "control") this.loadControlTables();
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

  // Stage 4: the round's analysis panel: state_replay's man-advantage states on the replay clock,
  // the ending at its own DB time, or why the round is excluded from the analysis.
  ReplayViewer.prototype.renderAnalysis = function () {
    var panel = this.ui.analysis, blob = this.current.blob;
    if (!panel) return;
    var a = blob.annotations;
    if (!a) { panel.innerHTML = '<li class="replay-feed-empty">No analysis for this round.</li>'; return; }
    if (a.excluded) {
      panel.innerHTML = '<li class="replay-feed-empty">Not analysed: ' + escapeHtml(a.excluded.replace(/_/g, " ")) + ".</li>";
      return;
    }
    var rows = a.states.map(function (s) {
      var note = s.post_plant ? " · post-plant" : "";
      if (s.decided_by) note += " · decided (" + s.decided_by + ")";
      return '<li class="replay-feed-row" data-kill-t="' + s.t + '" data-seek-t="' + Math.max(0, s.t - 1) + '" tabindex="0">' +
        '<span class="replay-feed-t">' + s.t.toFixed(1) + 's</span><span class="replay-feed-state">' +
        '<span class="team-name-team-1">' + s.alive[0] + '</span>v<span class="team-name-team-2">' + s.alive[1] +
        "</span></span>" + escapeHtml(note) + "</li>";
    });
    if (a.ending && a.ending.cause !== "elimination") {
      rows.push('<li class="replay-feed-row"' + (typeof a.ending.t === "number" ? ' data-kill-t="' + a.ending.t +
        '" data-seek-t="' + Math.max(0, a.ending.t - 1) + '" tabindex="0"' : "") + '><span class="replay-feed-t">' +
        (typeof a.ending.t === "number" ? a.ending.t.toFixed(1) + "s" : "") + "</span>Round ends: " +
        escapeHtml(a.ending.cause) + "</li>");
    }
    panel.innerHTML = rows.join("");
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
      if (k.post_decision) state += ' <span class="replay-feed-post" title="After the round was decided">after the round</span>';
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
    var live = impactAt(blob.kills, t), over = t >= blob.t_end;
    var stats = blob.stats || {};
    var rows = { "team-1": [], "team-2": [] };
    blob.players.forEach(function (p) {
      var info = self.linked.players[String(p.slot)] || {};
      var life = aliveAt(blob.alive[String(p.slot)], t, blob.t_end);
      var s = stats[String(p.slot)] || {};
      var mine = tally[p.slot] || { k: 0, d: 0, a: 0 };
      // Live: the kills' and deaths' Impact so far; at the round's end, the stored round Impact
      // (which adds damage, assists and trade credit). Without a per-kill split: the stored value.
      var value = live && !over ? (live[p.slot] || 0) : s.impact;
      var impact = typeof value === "number"
        ? '<td class="num ' + (value > 0 ? "pos" : value < 0 ? "neg" : "") + '">' + signed(value) + "</td>"
        : "<td></td>";
      (rows[info.team] || rows["team-1"]).push(
        '<tr class="' + (life ? "" : "is-dead") + '"><td class="replay-board-name">' +
        '<span class="replay-dot" style="background:' + self.slotColor(p.slot) + '"></span>' +
        escapeHtml(info.name || p.agent) + ' <span class="replay-board-agent">' + escapeHtml(p.agent) + "</span>" +
        (life ? "" : ' <span class="replay-board-dead">dead</span>') + "</td>" +
        '<td class="num">' + mine.k + "/" + mine.d + "/" + mine.a + "</td>" +
        '<td class="num">' + (typeof s.loadout === "number" ? s.loadout : "") + "</td>" + impact + "</tr>");
    });
    var impactTitle = live && !over
      ? "Impact from kills and deaths so far this round; at the round's end, the stored round Impact (adding damage, assists and trade credit)"
      : "Stored Impact for the whole round";
    var head = '<thead><tr><th>Player</th><th class="num" title="Kills / deaths / assists so far this round">K/D/A</th>' +
      '<th class="num" title="Loadout value at the round&#39;s start (credits): the replay has no live value">Loadout</th>' +
      '<th class="num" title="' + impactTitle.replace(/'/g, "&#39;") + '">' + (live && !over ? "Impact so far" : "Round Impact") +
      "</th></tr></thead>";
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
        label: style.label, guess: a.owner_by === "nearest", reveals: style.shape === "reveal" ? [] : null });
    });
    // Reveals: named on the revealing ability's row (the same owner and agent, cast up to 5 s
    // before), or a row of their own (a dart, Neural Theft).
    (this.current.extras.reveals || []).forEach(function (rv) {
      var agent = self.agentOf(rv.slot);
      var row = items.filter(function (it) {
        return it.reveals && it.slot === rv.slot && it.agent === agent && it.t <= rv.t0 && rv.t0 - it.t <= 5;
      }).pop();
      if (!row) {
        var style = abilityStyle({ kind: "GameObject", code: rv.code, name: rv.name, agent: agent });
        row = { t: rv.t0, slot: rv.slot, agent: style.agent, ability: style.ability, label: style.label, reveals: [] };
        items.push(row);
      }
      if (row.reveals.indexOf(rv.target) < 0) row.reveals.push(rv.target);
    });
    // Statuses: named on the ability's row (the same owner and ability, up to 6 s before), or a row
    // of their own.
    (this.current.extras.statuses || []).forEach(function (st) {
      var agent = self.agentOf(st.slot);
      var style = abilityStyle({ kind: "GameObject", code: st.code, name: st.name, agent: agent });
      var row = items.filter(function (it) {
        return it.slot === st.slot && it.ability === style.ability && it.t <= st.t0 + 0.05 && st.t0 - it.t <= 6;
      }).pop();
      if (!row) {
        row = { t: st.t0, slot: st.slot, agent: style.agent, ability: style.ability, label: style.label };
        items.push(row);
      }
      row.statuses = row.statuses || {};
      var hit = row.statuses[st.status] = row.statuses[st.status] || [];
      if (hit.indexOf(st.target) < 0) hit.push(st.target);
    });
    items.forEach(function (it) {
      var notes = [];
      if (it.reveals && it.reveals.length) {
        notes.push("revealed " + it.reveals.map(function (slot) { return self.nameOf(slot).split("#")[0]; }).join(", "));
      }
      Object.keys(it.statuses || {}).forEach(function (status) {
        notes.push(status + " " + it.statuses[status].map(function (slot) { return self.nameOf(slot).split("#")[0]; }).join(", "));
      });
      if (notes.length) it.note = notes.join(" · ");
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
    this.renderHud();
    if (this.tab === "control" && this.controlScope === "round" && this.controlTables) {
      var value = this.controlCache && this.controlCache.ready(this.number);
      var nowTick = value ? controlApi().tickAt(value.parsed.times, t) : null;
      if (nowTick !== this.controlNowTick) { this.controlNowTick = nowTick; this.renderControlTable(); }
    }
    if (this.ui.nextKill) this.ui.nextKill.disabled = nextKillTime(blob.kills, t) === null;
    if (this.ui.prevKill) this.ui.prevKill.disabled = prevKillTime(blob.kills, t) === null;
    if (this.ui.badge) {
      var alive = aliveCountAt(blob.alive_steps, t);
      this.ui.badge.hidden = !alive;
      if (alive) {
        this.ui.badge.innerHTML = '<span class="team-name-team-1">' + alive[0] + '</span> v <span class="team-name-team-2">' +
          alive[1] + "</span>" + (t > blob.t_decided ? ' <span class="replay-badge-note">decided</span>' : "");
      }
    }
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
    var smoke = this.css("--replay-smoke", "rgba(16, 18, 24, 0.6)");
    var smokeCore = this.css("--replay-smoke-core", "rgba(16, 18, 24, 0.84)");
    // Areas first, so badges sit on top of them; the spike last.
    ["wall", "smoke", "area", "reveal", "line", "wire", "badge", "spike"].forEach(function (pass) {
      showing.forEach(function (a) {
        var style = abilityStyle(a);
        if (style.shape !== pass) return;
        var at = self.abilityPoint(a, t, s), x = at.x, y = at.y;
        var color = self.ownerColor(a.slot);
        var glyph = self.abilityIcon(style.agent, style.ability);
        var text = self.abilityText(a, style);
        var age = t - a.t0, fadeIn = abilityAlpha(a, t);
        ctx.save();
        if ((pass === "smoke" || pass === "area") && style.pop !== undefined) {
          // A pop: a dashed outline while it is set (before its first pop), then a burst at each pop.
          var prad = self.uvRadius(style.r) * s, pops = popTimes(a);
          ctx.strokeStyle = color; ctx.fillStyle = color;
          if (t < pops[0]) {
            ctx.globalAlpha = 0.7 * fadeIn; ctx.lineWidth = 2; ctx.setLineDash([r / 3, r / 4]);
            ctx.beginPath(); ctx.arc(x, y, prad, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
          }
          pops.forEach(function (p) {
            if (t < p || t > p + style.pop) return;
            var f = (t - p) / style.pop;
            ctx.globalAlpha = 0.35 * (1 - f); ctx.beginPath(); ctx.arc(x, y, prad * (0.35 + 0.65 * f), 0, 2 * Math.PI); ctx.fill();
            ctx.globalAlpha = 1 - f; ctx.lineWidth = Math.max(2.5, r / 4);
            ctx.beginPath(); ctx.arc(x, y, prad * (0.35 + 0.65 * f), 0, 2 * Math.PI); ctx.stroke();
          });
          ctx.restore(); ctx.save();
          var popFade = Math.min(1, Math.max(0, (popUntil(a, style.pop) - t) / 0.3));
          self.drawBadge(ctx, x, y, r * 0.52, color, glyph, 0.9 * fadeIn * popFade);
          hits.push({ x: x, y: y, r: Math.max(prad, r * 0.6), text: text + " · went off at " +
            pops.map(function (p) { return p.toFixed(1); }).join(", ") + " s", area: true });
        } else if (pass === "smoke" || pass === "area") {
          var rad = self.uvRadius(style.r) * s;
          ctx.globalAlpha = fadeIn;
          ctx.beginPath(); ctx.arc(x, y, rad, 0, 2 * Math.PI);
          if (pass === "smoke") {
            if (style.faint) ctx.globalAlpha = 0.45 * fadeIn;
            var grad = ctx.createRadialGradient(x, y, rad * 0.2, x, y, rad);
            grad.addColorStop(0, smokeCore);   // a dark tint: a smoke reads apart from other util
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
        } else if (pass === "wall") {
          var pts = a.points || [], up = wallUp(a, t);
          if (pts.length > 1) {
            ctx.globalAlpha = fadeIn; ctx.lineCap = "round"; ctx.lineJoin = "round";
            ctx.beginPath(); ctx.moveTo(pts[0][0] * s, pts[0][1] * s);
            pts.slice(1).forEach(function (pt) { ctx.lineTo(pt[0] * s, pt[1] * s); });
            if (up) {
              // Up: a wide smoke band with the owner's colour down its middle.
              ctx.strokeStyle = smoke; ctx.lineWidth = Math.max(8, r * 1.1); ctx.stroke();
              ctx.strokeStyle = color; ctx.lineWidth = Math.max(2.5, r / 4); ctx.stroke();
            } else {
              // Down: where it would rise, a thin dashed line.
              ctx.strokeStyle = color; ctx.globalAlpha = 0.7 * fadeIn; ctx.lineWidth = Math.max(2, r / 6);
              ctx.setLineDash([r / 3, r / 4]); ctx.stroke(); ctx.setLineDash([]);
            }
          }
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.5, color, glyph, fadeIn);
          hits.push({ x: x, y: y, r: r * 0.7, text: text + (up ? " · up now" : " · down now") });
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
            if (style.pop !== undefined) {
              // Where it goes off (Fault Line's fissure, Rolling Thunder's wave): a burst at each pop.
              popTimes(a).forEach(function (p) {
                if (t < p || t > p + style.pop) return;
                var f = (t - p) / style.pop;
                ctx.save(); ctx.globalAlpha = 1 - f; ctx.lineWidth = Math.max(2.5, r / 4); ctx.setLineDash([]);
                ctx.beginPath(); ctx.arc(x1, y1, r * (0.8 + 2.4 * f), 0, 2 * Math.PI); ctx.stroke(); ctx.restore();
              });
            }
          }
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.5, color, glyph, fadeIn);
          hits.push({ x: x, y: y, r: r * 0.7, text: text });
        } else if (pass === "wire") {
          // The far end: the second anchor its placement listed (`end`), else a same-owner pairing.
          var other = a.end ? { u: a.end[0], v: a.end[1] } : self.current.wires[all.indexOf(a)];
          ctx.strokeStyle = color; ctx.lineWidth = Math.max(2.5, r / 4.5);
          if (other) {
            ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(other.u * s, other.v * s); ctx.stroke();
            ctx.fillStyle = color; ctx.beginPath(); ctx.arc(other.u * s, other.v * s, r * 0.22, 0, 2 * Math.PI); ctx.fill();
          }
          if (typeof a.gone === "number" && t >= a.gone && other) {
            // It was shot and destroyed: a burst across the wire as it goes.
            var f = Math.min(1, (t - a.gone) / GONE_S), mx = (x + other.u * s) / 2, my = (y + other.v * s) / 2;
            ctx.globalAlpha = 1 - f; ctx.lineWidth = Math.max(2.5, r / 4);
            ctx.beginPath(); ctx.arc(mx, my, r * (0.8 + 2.2 * f), 0, 2 * Math.PI); ctx.stroke();
          }
          ctx.restore(); ctx.save();
          self.drawBadge(ctx, x, y, r * 0.56, color, glyph);
          hits.push({ x: x, y: y, r: r * 0.75, text: text + (typeof a.gone === "number" ? " · gone at " + a.gone.toFixed(1) + " s" : "") });
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
          var state = spikeAt([a], t);
          if (state && !state.defused && !state.exploded) {
            // The time left to detonation, as a draining ring.
            ctx.lineWidth = Math.max(2, r / 5); ctx.strokeStyle = self.css("--brand", "#ff4655"); ctx.globalAlpha = 0.9;
            ctx.beginPath(); ctx.arc(x, y, k * 1.9, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * state.left / SPIKE_S); ctx.stroke();
          }
          if (state && state.defusing) {
            // A defuse: a thick ring filling in the defuser's colour, and a line from the defuser.
            var dcolor = self.ownerColor(state.defusing.slot);
            var from = trackAt(self.current.tracks[String(state.defusing.slot)], t);
            if (from) {
              ctx.globalAlpha = 0.9; ctx.strokeStyle = dcolor; ctx.lineWidth = Math.max(2, r / 5);
              ctx.beginPath(); ctx.moveTo(from.u * s, from.v * s); ctx.lineTo(x, y); ctx.stroke();
            }
            ctx.globalAlpha = 0.35; ctx.lineWidth = Math.max(5, r / 2); ctx.strokeStyle = "#ffffff";
            ctx.beginPath(); ctx.arc(x, y, k * 2.7, 0, 2 * Math.PI); ctx.stroke();
            ctx.globalAlpha = 1; ctx.strokeStyle = dcolor;
            ctx.beginPath(); ctx.arc(x, y, k * 2.7, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * state.defusing.frac); ctx.stroke();
          }
          hits.push({ x: x, y: y, r: k * 1.4, text: "Spike planted" + (a.slot !== null ? " by " + self.nameOf(a.slot) : "") +
            " at " + a.t0.toFixed(1) + " s" + (state && state.halved ? " · half defused" : "") });
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

  ReplayViewer.prototype.agentOf = function (slot) {
    var row = this.current && this.current.blob.players.filter(function (q) { return q.slot === slot; })[0];
    return row ? row.agent : null;
  };

  // A revealed player: a ring in the revealer's colour pulsing out from them, with the revealing
  // ability's badge at their shoulder, while the reveal lasts.
  ReplayViewer.prototype.drawReveals = function (ctx, s, r, hits) {
    var t = this.t, tracks = this.current.tracks, self = this;
    revealsAt(this.current.extras.reveals, t).forEach(function (rv) {
      var at = trackAt(tracks[String(rv.target)], t);
      if (!at) return;
      var x = at.u * s, y = at.v * s, color = self.ownerColor(rv.slot);
      var style = abilityStyle({ kind: "GameObject", code: rv.code, name: rv.name, agent: self.agentOf(rv.slot) });
      var pulse = ((t - rv.t0) % 0.8) / 0.8;
      ctx.save();
      ctx.strokeStyle = color; ctx.lineWidth = Math.max(3, r / 3.5);
      ctx.beginPath(); ctx.arc(x, y, r * 1.45, 0, 2 * Math.PI); ctx.stroke();
      ctx.globalAlpha = 0.8 * (1 - pulse); ctx.lineWidth = Math.max(2, r / 5);
      ctx.beginPath(); ctx.arc(x, y, r * (1.45 + 1.3 * pulse), 0, 2 * Math.PI); ctx.stroke();
      ctx.restore();
      self.drawBadge(ctx, x + r * 1.15, y - r * 1.15, r * 0.42, color, self.abilityIcon(style.agent, style.ability));
      hits.push({ x: x, y: y, r: r * 1.5, text: self.nameOf(rv.target) + " revealed by " + self.nameOf(rv.slot) +
        "'s " + style.label + " · " + rv.t0.toFixed(1) + "–" + (typeof rv.t1 === "number" ? rv.t1.toFixed(1) : "?") + " s" });
    });
  };

  // A player under an enemy's status: a dashed ring in the status's colour turning around them, its
  // name in a chip above them (several stack), a burst when it lands, and the ability's badge.
  ReplayViewer.prototype.drawStatuses = function (ctx, s, r, hits) {
    var t = this.t, tracks = this.current.tracks, self = this, stacked = {};
    statusesAt(this.current.extras.statuses, t).forEach(function (st) {
      var at = trackAt(tracks[String(st.target)], t);
      if (!at) return;
      var x = at.u * s, y = at.v * s, look = statusStyle(st.status);
      var style = abilityStyle({ kind: "GameObject", code: st.code, name: st.name, agent: self.agentOf(st.slot) });
      var row = stacked[st.target] = (stacked[st.target] || 0) + 1;
      ctx.save();
      ctx.strokeStyle = look.color; ctx.lineWidth = Math.max(2.5, r / 4.5);
      ctx.setLineDash([r / 2.5, r / 4]); ctx.lineDashOffset = -(t * r * 2);
      ctx.beginPath(); ctx.arc(x, y, r * (1.25 + 0.3 * row), 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
      var age = t - st.t0;
      if (age < 0.5) {
        ctx.globalAlpha = 1 - age / 0.5; ctx.lineWidth = Math.max(3, r / 3);
        ctx.beginPath(); ctx.arc(x, y, r * (1.2 + 2 * age / 0.5), 0, 2 * Math.PI); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.font = "700 " + Math.round(r * 0.62) + "px system-ui, sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      var w = ctx.measureText(look.label).width + r * 0.5, h = r * 0.85, cy = y - r * (1.35 + 0.95 * row);
      ctx.fillStyle = "rgba(10, 10, 12, 0.85)"; ctx.fillRect(x - w / 2, cy - h / 2, w, h);
      ctx.fillStyle = look.color; ctx.fillRect(x - w / 2, cy - h / 2, r * 0.18, h);
      ctx.fillText(look.label, x + r * 0.05, cy + r * 0.03);
      ctx.restore();
      self.drawBadge(ctx, x - r * 1.15, y - r * 1.15, r * 0.4, self.ownerColor(st.slot),
        self.abilityIcon(style.agent, style.ability));
      hits.push({ x: x, y: y, r: r * 1.5, text: self.nameOf(st.target) + " " + st.status + " by " +
        (st.slot === null || st.slot === undefined ? "an unknown owner" : self.nameOf(st.slot)) + "'s " + style.label +
        " · " + st.t0.toFixed(1) + "–" + (typeof st.t1 === "number" ? st.t1.toFixed(1) : "?") + " s" });
    });
  };

  // The spike panel over the map: time to detonation, "half defused", and a defuse in progress
  // with its defuser, time held of time needed and a bar; then "Defused" or "Detonated".
  ReplayViewer.prototype.renderHud = function () {
    var hud = this.ui.hud;
    if (!hud || !this.current) return;
    var state = spikeAt(this.current.extras.abilities, this.t);
    if (!state) { hud.hidden = true; return; }
    hud.hidden = false;
    var parts = [];
    if (state.defused) {
      parts.push('<span class="replay-hud-row"><strong class="replay-hud-done">Defused</strong> by ' +
        this.personHtml(state.defused.slot) + " at " + state.defused.t.toFixed(1) + " s</span>");
    } else if (state.exploded) {
      parts.push('<span class="replay-hud-row"><strong class="replay-hud-boom">Detonated</strong></span>');
    } else {
      parts.push('<span class="replay-hud-row"><span class="replay-hud-label">Spike</span> <strong class="replay-hud-time' +
        (state.left <= 10 ? " is-low" : "") + '">' + state.left.toFixed(1) + " s</strong> to detonation" +
        (state.halved ? ' <span class="replay-hud-half">half defused</span>' : "") + "</span>");
    }
    if (state.defusing) {
      var d = state.defusing, late = state.left < d.needed - d.elapsed;
      parts.push('<span class="replay-hud-row replay-hud-defuse"><span class="replay-hud-label">Defusing</span> ' +
        this.personHtml(d.slot) + ' <strong class="replay-hud-time">' + d.elapsed.toFixed(1) + " / " + d.needed.toFixed(1) +
        " s</strong>" + (late ? ' <span class="replay-hud-late">too late: it detonates first</span>' : "") + "</span>" +
        '<span class="replay-hud-bar"><span style="width:' + (100 * d.frac).toFixed(1) + "%;background:" +
        this.ownerColor(d.slot) + '"></span></span>');
    }
    hud.innerHTML = parts.join("");
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

  // ------------------------------------------------------------ map control (Stage 4)

  ReplayViewer.prototype.setControlStatus = function (text) {
    if (this.ui.controlStatus) this.ui.controlStatus.textContent = text || "";
  };

  // Loads the current round's control when the layer or a highlight needs it, keeps only this
  // round and its neighbours, and prefetches the next round while the layer is on.
  ReplayViewer.prototype.refreshControl = function () {
    if (!this.control || !this.current) return;
    var self = this, n = this.number, i = this.rounds.indexOf(n);
    var prev = this.rounds[i - 1], next = this.rounds[i + 1];
    if (this.ui.controlLegend) this.ui.controlLegend.hidden = !this.layers.control;
    if (this.ui.controlViewWrap && !this.layers.control) this.ui.controlViewWrap.hidden = true;
    if (this.ui.controlUnknownWrap && !this.layers.control) this.ui.controlUnknownWrap.hidden = true;
    this.controlCache.keep([prev, n, next]);
    if (!this.layers.control && this.highlight === null) { this.setControlStatus(""); return; }
    if (!this.controlCache.ready(n)) this.setControlStatus("Loading map control…");
    this.controlCache.get(n).then(function (value) {
      if (self.number !== n || value.status === "aborted") return;
      var text = value.status === "ok"
        ? (value.stale ? "Computed from older inputs; it will be refreshed." : "")
        : CONTROL_STATUS[value.status] || CONTROL_STATUS.unavailable;
      self.showControlViews(value);
      var knowing = value.status === "ok" ? self.knowingGroup(value) : null;
      if (knowing) {
        text = (text ? text + " " : "") + "Showing what " + self.groupName(knowing) + " knew: enemies it saw are " +
          "exact; others could be anywhere they could have run to unseen. The table and highlight stay true.";
      }
      if (value.status === "ok" && self.highlight !== null) {
        text = (text ? text + " " : "") + "Showing " + self.nameOf(self.highlight).split("#")[0] +
          "'s control (filled) and coverage (outlined): click them again or press Esc to clear.";
      }
      self.setControlStatus(text);
      self.draw();
    });
    if (next !== undefined && this.layers.control) this.controlCache.get(next);
  };

  // "Team 1" / "Team 2" for a side group, from the linked players; else "side A" / "side B".
  ReplayViewer.prototype.groupName = function (group) {
    var team = controlApi().groupTeams(this.linked && this.linked.players)[group];
    return team ? team.replace("team-", "Team ") : "side " + group;
  };

  // The "as ... knew it" picker: shown only for a round stored with the knowledge streams.
  ReplayViewer.prototype.showControlViews = function (value) {
    var wrap = this.ui.controlViewWrap, select = this.ui.controlView, self = this;
    if (!wrap || !select) return;
    var has = !!(value && value.status === "ok" && value.parsed.knew_a && value.parsed.knew_b);
    wrap.hidden = !has || !this.layers.control;
    Array.prototype.forEach.call(select.querySelectorAll("[data-knew-group]"), function (option) {
      option.textContent = self.groupName(option.getAttribute("data-knew-group")) + " knew it";
    });
    var uwrap = this.ui.controlUnknownWrap, uselect = this.ui.controlUnknown;
    if (uwrap && uselect) {
      var hasUnknown = !!(value && value.status === "ok" && value.parsed.unknown_a && value.parsed.unknown_b);
      uwrap.hidden = !hasUnknown || !this.layers.control;
      Array.prototype.forEach.call(uselect.querySelectorAll("[data-unknown-group]"), function (option) {
        option.textContent = self.groupName(option.getAttribute("data-unknown-group")) + "'s";
      });
    }
  };

  // Group (A/B) -> the RGB its players are drawn in: their team's colour, else the side's.
  ReplayViewer.prototype.controlColors = function () {
    var C = controlApi(), teams = C.groupTeams(this.linked && this.linked.players), self = this, out = {};
    "AB".split("").forEach(function (group) {
      var css = teams[group] ? self.css(teams[group] === "team-1" ? "--replay-team-1" : "--replay-team-2", "")
        : self.color(group);
      out[group.toLowerCase()] = C.hexRgb(css || self.color(group));
    });
    return out;
  };

  ReplayViewer.prototype.controlCanvas = function (key) {
    this.controlImages = this.controlImages || {};
    if (!this.controlImages[key]) {
      var c = document.createElement("canvas");
      c.width = c.height = CONTROL_PX;
      this.controlImages[key] = { canvas: c, ctx: c.getContext("2d"), data: null };
    }
    return this.controlImages[key];
  };

  // Paints the layer (and the highlight) for the tick at t into offscreen images, only when the
  // round, the tick, the highlight or the switches change, then draws them over the map.
  ReplayViewer.prototype.drawControl = function (ctx, size, hits) {
    if (!this.control || (!this.layers.control && this.highlight === null)) return;
    var value = this.controlCache.ready(this.number);
    if (!value) return;
    var C = controlApi(), parsed = value.parsed, tick = C.tickAt(parsed.times, this.t);
    if (tick < 0) return;
    var knowing = this.knowingGroup(value);
    var key = [this.number, tick, this.layers.control, this.highlight, knowing, this.unknownView].join(":");
    var showUnknown = !!(this.layers.control && this.unknownView !== "off" && parsed.unknown_a && parsed.unknown_b);
    if (this.controlPaint !== key) {
      this.controlPaint = key;
      var colors = this.controlColors();
      if (this.layers.control) {
        var img = this.controlCanvas("states");
        img.data = img.data || img.ctx.createImageData(CONTROL_PX, CONTROL_PX);
        var codes = knowing ? value.cursor.knew("knew_" + knowing.toLowerCase(), tick) : value.cursor.states(tick);
        C.paintStates(img.data.data, CONTROL_PX, parsed.walk, codes, colors);
        img.ctx.putImageData(img.data, 0, 0);
      }
      if (showUnknown) {
        var uk = this.controlCanvas("unknown");
        uk.data = uk.data || uk.ctx.createImageData(CONTROL_PX, CONTROL_PX);
        C.paintUnknown(uk.data.data, CONTROL_PX, parsed.walk, value.cursor.unknown("unknown_a", tick),
          value.cursor.unknown("unknown_b", tick), colors, this.unknownView);
        uk.ctx.putImageData(uk.data, 0, 0);
      }
      if (this.highlight !== null) {
        var hl = this.controlCanvas("highlight"), slot = this.highlight;
        var group = (this.current.blob.players.filter(function (p) { return p.slot === slot; })[0] || {}).side;
        hl.data = hl.data || hl.ctx.createImageData(CONTROL_PX, CONTROL_PX);
        C.paintHighlight(hl.data.data, CONTROL_PX, parsed.walk, value.cursor.mask("control", slot, tick),
          value.cursor.mask("coverage", slot, tick), colors[String(group || "A").toLowerCase()] || colors.a);
        hl.ctx.putImageData(hl.data, 0, 0);
      }
    }
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    if (this.layers.control) ctx.drawImage(this.controlCanvas("states").canvas, 0, 0, size, size);
    if (showUnknown) ctx.drawImage(this.controlCanvas("unknown").canvas, 0, 0, size, size);
    if (this.highlight !== null) ctx.drawImage(this.controlCanvas("highlight").canvas, 0, 0, size, size);
    ctx.restore();
    if (knowing && this.layers.control) this.drawLostEnemies(ctx, size, value, knowing, hits || []);
  };

  // The side group whose picture the layer shows ("A" / "B"), or null for the true positions (also
  // for a row stored without the knowledge streams).
  ReplayViewer.prototype.knowingGroup = function (value) {
    if (!this.controlView || this.controlView === "true") return null;
    var parsed = value && value.parsed;
    return parsed && parsed["knew_" + this.controlView.toLowerCase()] ? this.controlView : null;
  };

  // In a team's picture: each enemy it just lost sight of, as a dashed diamond at the last-seen point
  // fading over the header's knew_fade_s.
  ReplayViewer.prototype.drawLostEnemies = function (ctx, size, value, group, hits) {
    var header = value.parsed.header, fade = header.knew_fade_s || 3, s = size / UV, self = this;
    var r = size / 48 / Math.sqrt(this.view.k);
    controlApi().lostAt((header.knew || {})[group], this.t, fade).forEach(function (lost) {
      var x = lost.u * s, y = lost.v * s, k = r * 1.1;
      ctx.save();
      ctx.globalAlpha = Math.max(0.15, 1 - lost.age / fade);
      ctx.strokeStyle = self.slotColor(lost.slot);
      ctx.lineWidth = Math.max(2, r / 4);
      ctx.setLineDash([r / 3, r / 4]);
      ctx.beginPath(); ctx.moveTo(x, y - k); ctx.lineTo(x + k, y); ctx.lineTo(x, y + k); ctx.lineTo(x - k, y); ctx.closePath();
      ctx.stroke();
      ctx.restore();
      hits.push({ x: x, y: y, r: k, text: self.nameOf(lost.slot) + " last seen here " +
        lost.age.toFixed(1) + " s ago" });
    });
  };

  // A click on a player (their dot on the map, or their row in the Control tab) highlights them;
  // the same click again clears it.
  ReplayViewer.prototype.setHighlight = function (slot) {
    if (!this.control) return;
    this.highlight = slot === null || slot === this.highlight ? null : slot;
    this.controlPaint = null;
    this.refreshControl();
    this.renderControlTable();
    this.draw();
  };

  ReplayViewer.prototype.onCanvasClick = function (e) {
    if (!this.control || !this.hits) return;
    var rect = this.canvas.getBoundingClientRect(), scale = this.canvas.width / rect.width, v = this.view;
    var x = (e.clientX - rect.left) * scale / v.k + v.ox, y = (e.clientY - rect.top) * scale / v.k + v.oy;
    var best = null;
    this.hits.forEach(function (h) {
      if (h.slot === undefined) return;
      var d = Math.hypot(h.x - x, h.y - y);
      if (d <= h.r && (!best || d < best.d)) best = { slot: h.slot, d: d };
    });
    if (best) this.setHighlight(best.slot);
  };

  ReplayViewer.prototype.loadControlTables = function () {
    if (!this.control || !this.options.loadControlPlayers || this.controlTablesAsked) return;
    var self = this;
    this.controlTablesAsked = true;
    if (this.ui.controlTable) this.ui.controlTable.innerHTML = '<p class="replay-feed-empty">Loading…</p>';
    Promise.resolve(this.options.loadControlPlayers()).then(function (tables) {
      self.controlTables = tables;
      self.renderControlTable();
    }, function () {
      self.controlTablesAsked = false;   // ask again next time the tab opens
      if (self.ui.controlTable) self.ui.controlTable.innerHTML = '<p class="replay-feed-empty">Control numbers couldn\'t be loaded.</p>';
    });
  };

  function num(value) {
    return typeof value === "number" ? String(Math.round(value)) : "—";
  }

  function signedCell(value) {
    if (typeof value !== "number") return '<td class="num">—</td>';
    var cls = value > 0.5 ? "pos" : value < -0.5 ? "neg" : "";
    return '<td class="num ' + cls + '">' + (signed(value) || "0") + "</td>";
  }

  ReplayViewer.prototype.renderControlTable = function () {
    var box = this.ui.controlTable, self = this;
    if (!box || !this.controlTables || !this.current) return;
    var scope = this.controlScope, groupTeam = controlApi().groupTeams(this.linked && this.linked.players);
    var rows = controlRows(this.controlTables, scope, this.number, groupTeam);
    if (!rows) {
      box.innerHTML = '<p class="replay-feed-empty">No map control stored for this round yet.</p>';
      return;
    }
    var now = scope === "round" ? this.controlNow() : null;
    var head = "<thead><tr><th>Player</th>" +
      '<th class="num" title="Average m² the team would lose if this player died, while alive (signed)">Control</th>' +
      (now ? '<th class="num" title="Control at this moment (m²)">Now</th>' : "") +
      '<th class="num" title="What the team lost at this player&#39;s death' + (scope === "match"
        ? "s: m² per death on average, and all of it as a share of what the team held at those deaths"
        : ": m², and as a share of what the team held then") +
      '. The share can pass 100%: ground that flips to the enemy counts twice, and ground the enemy gains that the team never held counts too.">' +
      (scope === "match" ? "Lost/death" : "Lost") + "</th>" +
      '<th class="num" title="Average m² in their held cone / of passive vision and their own live utility">Cover a/p</th>' +
      '<th class="num" title="Active coverage ÷ (active + passive)">Act %</th>' +
      '<th class="num" title="Space taken: ground that was the enemy&#39;s or nobody&#39;s and became the team&#39;s while this player saw it (m²' +
      (scope === "match" ? ", per round" : "") + ')">Taken</th></tr></thead>';
    box.innerHTML = (rows.stale ? '<p class="replay-side-note">Computed from older inputs; it will be refreshed.</p>' : "") +
      ["team-1", "team-2"].map(function (team) {
        var body = rows[team].map(function (r) {
          // The stored share, shown even past 100% (the tooltip says why).
          var lost = r.lost ? Math.round(r.lost.m2) + (typeof r.lost.share === "number"
            ? ' <span class="replay-control-share">' + Math.round(100 * r.lost.share) + "%</span>" : "") : "—";
          var nowValue = now ? now[r.slot] : undefined;
          return '<tr data-control-slot="' + r.slot + '" tabindex="0" class="replay-control-row' +
            (self.highlight === r.slot ? " is-highlighted" : "") + '" aria-pressed="' + (self.highlight === r.slot) + '">' +
            '<td class="replay-board-name" title="' + escapeHtml(self.nameOf(r.slot)) + (scope === "match" && r.lost
              ? " · " + r.lost.deaths + " deaths" : "") + '"><span class="replay-dot" style="background:' + self.slotColor(r.slot) +
            '"></span>' + escapeHtml(self.nameOf(r.slot).split("#")[0]) + "</td>" + signedCell(r.control) +
            (now ? (typeof nowValue === "number" ? signedCell(nowValue) : '<td class="num">—</td>') : "") +
            '<td class="num">' + lost + "</td>" + '<td class="num">' + num(r.active) + " / " + num(r.passive) + "</td>" +
            '<td class="num">' + (typeof r.ratio === "number" ? Math.round(100 * r.ratio) + "%" : "—") + "</td>" +
            '<td class="num">' + num(r.taken) + "</td></tr>";
        }).join("");
        var redundant = rows.redundant[team];
        var foot = '<tr class="replay-control-redundant"><td title="The team&#39;s own area minus its players&#39; control: space two or more of them hold at once, or none alone">Redundant</td>' +
          signedCell(redundant) + '<td colspan="' + (now ? 5 : 4) + '"></td></tr>';
        return '<table class="replay-board-team replay-control-team team-' + team.slice(-1) + '">' + head +
          "<tbody>" + body + foot + "</tbody></table>";
      }).join("");
  };

  // Each alive slot's control (m²) at the current tick, from the round's stored header, or null.
  ReplayViewer.prototype.controlNow = function () {
    var value = this.controlCache && this.controlCache.ready(this.number);
    if (!value) return null;
    var tick = controlApi().tickAt(value.parsed.times, this.t), out = {};
    if (tick < 0) return null;
    (value.parsed.header.control_m2 || []).forEach(function (series, slot) {
      if (series && typeof series[tick] === "number") out[slot] = series[tick];
    });
    return out;
  };

  // ------------------------------------------------------------ the match heatmap (Stage 5)

  var HEAT_LABELS = { attack: "Attack", defense: "Defense", "team-1": "Team 1", "team-2": "Team 2" };

  ReplayViewer.prototype.bindHeatmap = function () {
    var box = this.root.querySelector("[data-replay-heatmap]");
    if (!box || !this.control || !this.options.loadHeatmap) return;
    var self = this, q = function (sel) { return box.querySelector(sel); };
    var canvas = q("[data-replay-heatmap-canvas]");
    this.heat = { box: box, view: q("[data-replay-heatmap-view]"), mode: q("[data-replay-heatmap-mode]"),
                  section: q("[data-replay-heatmap-section]"), canvas: canvas, ctx: canvas.getContext("2d"),
                  tip: q("[data-replay-heatmap-tip]"), legend: q("[data-replay-heatmap-legend]"), data: {}, shown: null };
    box.addEventListener("toggle", function () { if (box.open) self.loadHeatmap(); });
    this.heat.view.addEventListener("change", function () { self.loadHeatmap(); });
    this.heat.mode.addEventListener("change", function () { self.paintHeatmap(); });
    this.heat.section.addEventListener("change", function () { self.paintHeatmap(); });
    canvas.addEventListener("mousemove", function (e) { self.heatTip(e); });
    canvas.addEventListener("mouseleave", function () { self.heat.tip.hidden = true; });
    if (box.open) this.loadHeatmap();
  };

  ReplayViewer.prototype.loadHeatmap = function () {
    var heat = this.heat, self = this, view = heat.view.value, C = controlApi();
    if (heat.data[view]) { this.showHeatmap(view); return; }
    heat.legend.textContent = "Loading…";
    Promise.resolve(this.options.loadHeatmap(view)).then(function (body) {
      if (!body || body.status !== "ok" || !body.sections.length) {
        heat.legend.textContent = "No map control is stored for this match yet.";
        return;
      }
      var walk = C.walkCells({ walk: body.walk });
      heat.data[view] = {
        labels: body.labels, walk: walk, index: C.cellIndex(walk), used: body.rounds_used, skipped: body.rounds_skipped,
        sections: body.sections.map(function (s) {
          return { key: s.key, label: s.label, seconds: s.seconds, rounds: s.rounds,
                   x: C.base64Bytes(s.x), y: C.base64Bytes(s.y), contested: C.base64Bytes(s.contested) };
        })
      };
      if (heat.view.value === view) self.showHeatmap(view);
    }, function () { heat.legend.textContent = "The heatmap couldn't be loaded."; });
  };

  ReplayViewer.prototype.showHeatmap = function (view) {
    var heat = this.heat, data = heat.data[view], was = heat.section.value;
    heat.shown = view;
    heat.section.innerHTML = data.sections.map(function (s) {
      return '<option value="' + escapeHtml(s.key) + '">' + escapeHtml(s.label) + " (" + s.rounds + " round" +
        (s.rounds === 1 ? "" : "s") + ")</option>";
    }).join("");
    if (was && data.sections.some(function (s) { return s.key === was; })) heat.section.value = was;
    heat.mode.querySelector("[data-label-x]").textContent = HEAT_LABELS[data.labels.x] || data.labels.x;
    heat.mode.querySelector("[data-label-y]").textContent = HEAT_LABELS[data.labels.y] || data.labels.y;
    this.paintHeatmap();
  };

  ReplayViewer.prototype.heatColors = function (labels) {
    var C = controlApi(), self = this, pick = function (label) {
      var name = label === "attack" ? "--replay-attack" : label === "defense" ? "--replay-defense"
        : label === "team-1" ? "--replay-team-1" : "--replay-team-2";
      return C.hexRgb(self.css(name, "#bbbbbb"));
    };
    return { x: pick(labels.x), y: pick(labels.y) };
  };

  ReplayViewer.prototype.heatSection = function () {
    var data = this.heat && this.heat.shown && this.heat.data[this.heat.shown];
    if (!data) return null;
    var key = this.heat.section.value;
    return { data: data, section: data.sections.filter(function (s) { return s.key === key; })[0] || data.sections[0] };
  };

  ReplayViewer.prototype.paintHeatmap = function () {
    var heat = this.heat, found = this.heatSection();
    if (!found) return;
    var C = controlApi(), data = found.data, sec = found.section, mode = heat.mode.value;
    var colors = this.heatColors(data.labels);
    var img = this.controlCanvas("heat");
    img.data = img.data || img.ctx.createImageData(CONTROL_PX, CONTROL_PX);
    C.paintHeatmap(img.data.data, CONTROL_PX, data.walk, sec, mode, colors);
    img.ctx.putImageData(img.data, 0, 0);
    var ctx = heat.ctx, size = heat.canvas.width, v = this.view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.setTransform(v.k, 0, 0, v.k, -v.ox * v.k, -v.oy * v.k);
    if (this.map.complete && this.map.naturalWidth) ctx.drawImage(this.map, 0, 0, size, size);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(img.canvas, 0, 0, size, size);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    var name = function (label) { return HEAT_LABELS[label] || label; };
    var keys = mode === "lead" ? ["x", "y", "contested"] : [mode];
    heat.legend.innerHTML = keys.map(function (k) {
      var cls = k === "contested" ? "replay-key replay-key-heat-contested" : "replay-key";
      var style = k === "contested"
        ? ' style="--kx:rgb(' + colors.x.join(",") + ");--ky:rgb(" + colors.y.join(",") + ')"'
        : ' style="--key:rgb(' + colors[k].join(",") + ')"';
      return '<span class="' + cls + ' replay-key-heat"' + style + ">" + (k === "contested" ? "Contested" : name(data.labels[k])) + "</span>";
    }).join("") + '<span class="replay-heatmap-sample">' + (mode === "lead" ? "Colour: who held each cell longest; stronger = longer. "
      : "Stronger = a larger share of the time. ") + sec.seconds.toFixed(0) + " s over " + sec.rounds + " round" +
      (sec.rounds === 1 ? "" : "s") + (data.skipped.length ? "; " + data.skipped.length + " round(s) left out (older map geometry)" : "") + ".</span>";
    heat.canvas.setAttribute("aria-label", "Match heatmap, " + sec.label + ", " + (mode === "lead" ? "who held each part longest" : mode));
  };

  ReplayViewer.prototype.heatTip = function (e) {
    var heat = this.heat, found = this.heatSection();
    if (!found) return;
    var rect = heat.canvas.getBoundingClientRect(), scale = heat.canvas.width / rect.width, v = this.view;
    var x = ((e.clientX - rect.left) * scale / v.k + v.ox) / heat.canvas.width * controlApi().GRID;
    var y = ((e.clientY - rect.top) * scale / v.k + v.oy) / heat.canvas.height * controlApi().GRID;
    var cell = Math.floor(y) * controlApi().GRID + Math.floor(x), k = found.data.index[cell];
    if (!(x >= 0 && y >= 0 && x < controlApi().GRID && y < controlApi().GRID) || k < 0) { heat.tip.hidden = true; return; }
    var sec = found.section, labels = found.data.labels;
    var pct = function (b) { return Math.round(100 * b / 255) + "%"; };
    var nobody = Math.max(0, 255 - sec.x[k] - sec.y[k] - sec.contested[k]);
    heat.tip.textContent = (HEAT_LABELS[labels.x] || labels.x) + " " + pct(sec.x[k]) + " · " + (HEAT_LABELS[labels.y] || labels.y) +
      " " + pct(sec.y[k]) + " · contested " + pct(sec.contested[k]) + " · nobody " + pct(nobody);
    heat.tip.hidden = false;
    var stage = heat.tip.parentNode.getBoundingClientRect();
    heat.tip.style.left = Math.max(4, Math.min(e.clientX - rect.left + 14, stage.width - heat.tip.offsetWidth - 4)) + "px";
    heat.tip.style.top = Math.max(4, e.clientY - rect.top - heat.tip.offsetHeight - 10) + "px";
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

    this.drawControl(ctx, size, hits);   // map control: under everything else

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
    // In a team's picture (R3.3) the true enemy dots are dimmed: that team didn't see them all.
    var knowing = this.layers.control && this.controlCache ? this.knowingGroup(this.controlCache.ready(this.number)) : null;
    blob.players.forEach(function (p) {
      var slot = String(p.slot);
      var at = trackAt(tracks[slot], t);
      if (!at) return;
      var life = aliveAt(blob.alive[slot], t, blob.t_end);
      var x = at.u * s, y = at.v * s, color = self.slotColor(p.slot, p.side);
      var dim = knowing && p.side !== knowing ? 0.35 : 1;
      ctx.globalAlpha = dim;
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(2, r / 5);
      if (!(at.live && life)) {
        ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.stroke();
        return;
      }
      var rad = at.yaw * Math.PI / 180;
      if (self.layers.cones) {
        ctx.globalAlpha = 0.35 * dim;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.arc(x, y, r * 2.4, rad - 0.4, rad + 0.4); ctx.closePath(); ctx.fill();
        ctx.globalAlpha = dim;
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
      if (self.highlight === p.slot) {
        ctx.save();
        ctx.strokeStyle = "#ffffff"; ctx.lineWidth = Math.max(2.5, r / 4);
        ctx.beginPath(); ctx.arc(x, y, r * 1.6, 0, 2 * Math.PI); ctx.stroke();
        ctx.restore();
      }
      hits.push({ x: x, y: y, r: r * 1.2, slot: p.slot, text: self.nameOf(p.slot) + " · " + p.agent +
        (life.flags.length ? " · " + life.flags.join(", ") : "") + (self.control ? " · click to show their control" : "") });
    });
    ctx.globalAlpha = 1;

    if (this.layers.abilities) {
      this.drawReveals(ctx, s, r, hits);
      this.drawStatuses(ctx, s, r, hits);
    }

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
    abilityStyle: abilityStyle, lineEnds: lineEnds, abilitiesAt: abilitiesAt, abilityAlpha: abilityAlpha, pairWires: pairWires, signed: signed, tallyAt: tallyAt, aliveCountAt: aliveCountAt, stateAt: stateAt,
    utilAbility: utilAbility, pathAt: pathAt, extrasFromUtil: extrasFromUtil, castUtil: castUtil,
    impactAt: impactAt, nextKillTime: nextKillTime, prevKillTime: prevKillTime, spikeAt: spikeAt, wallUp: wallUp, revealsAt: revealsAt,
    popTimes: popTimes, popUntil: popUntil, statusesAt: statusesAt, statusStyle: statusStyle,
    controlRows: controlRows, withSiteData: withSiteData
  };
  global.Replay = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

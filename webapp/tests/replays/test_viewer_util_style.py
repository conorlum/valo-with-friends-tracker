"""Utility styles, pulse timing and derived zones work without map control."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, payload):
    completed = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_util_colour_uses_ability_then_agent():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const rows = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(JSON.stringify(rows.map(([a, style]) => R.utilColorOf(a, style))));
    """, [
        [{"agent": "Jett"}, {"ability": "Cloudburst"}],
        [{"agent": "Brimstone"}, {"ability": "Sky Smoke"}],
        [{"agent": "Nobody"}, {"ability": "X"}],
        [{}, {"agent": "Omen", "ability": "Paranoia"}],
        [{"agent": "Veto"}, {"ability": "X"}],
        [{"agent": "Jett"}, {"agent": "Omen", "ability": "X"}],
        [{"agent": "Astra"}, {"ability": "Nebula  / Dissipate"}],
        [{"agent": "Omen"}, {"ability": "Dark Cover"}],
        [{"agent": "Astra"}, {"ability": "Gravity Well"}],
        [{"agent": "Cypher"}, {"ability": "Cyber Cage"}],
        [{"agent": "Sage"}, {"ability": "Barrier Orb"}],
        [{"agent": "Brimstone"}, {"ability": "Stim Beacon"}],
    ])
    assert actual == ["#f4f7fb", "#c9b08a", None, "#7a5ad6", "#7fc8b8", "#f4f7fb",
                      "#c98bff", "#5c46a8", "#9b6bff", "#d9dee6", "#8ff0e4", "#ff9b54"]


@needs_node
def test_side_colour_uses_team_then_unlinked_group():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      console.log(JSON.stringify([
        R.sideColorOf("green-red", "team-1", "B"),
        R.sideColorOf("green-red", "team-2", "A"),
        R.sideColorOf("green-red", undefined, "A"),
        R.sideColorOf("green-red", undefined, "B"),
        R.sideColorOf("teal-coral", undefined, "A"),
        R.sideColorOf("bogus", undefined, "A"),
        R.sideColorOf("green-red", undefined, undefined),
        R.sideColorOf("green-red", "unknown-team", "A"),
        R.sideColorOf("green-red", null, "unknown-group")
      ]));
    """, None)
    assert actual == ["#5ccf8f", "#d9534f", "#5ccf8f", "#d9534f", "#1fb8c9", "#5ccf8f",
                      None, None, None]


@needs_node
def test_viewer_side_colour_without_control_module():
    actual = run_node(r"""
      Object.defineProperty(global, "ReplayControl", {get() { throw Error("control module accessed"); }});
      const R = require(process.argv[1]);
      const v = Object.create(R.ReplayViewer.prototype);
      v.linked = null;
      v.controlPalette = "purple-yellow";
      v.current = {blob: {players: [{slot: 3, side: "B"}]}};
      v.controlColors = () => { throw Error("controlColors called"); };
      const cssCalls = [];
      v.css = (name, fallback) => { cssCalls.push([name, fallback]); return fallback; };
      const colours = [v.sideColor(3), v.sideColor(9)];
      v.linked = {players: {"3": {team: "team-1"}}};
      colours.push(v.sideColor(3));
      v.controlPalette = "teal-coral";
      colours.push(v.sideColor(3));
      console.log(JSON.stringify({colours, cssCalls}));
    """, None)
    assert actual == {"colours": ["#f2c230", "#77787f", "#9b6bff", "#1fb8c9"],
                      "cssCalls": [["--replay-unknown", "#77787f"]]}


@needs_node
def test_viewer_util_colour_falls_back_to_owner_only_when_unknown():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const v = Object.create(R.ReplayViewer.prototype);
      const slots = [];
      v.ownerColor = slot => { slots.push(slot); return "#123456"; };
      const colours = [v.utilColor({slot: 3, agent: "Jett"}, {ability: "Cloudburst"}),
                       v.utilColor({slot: 9, agent: "Nobody"}, {ability: "X"})];
      console.log(JSON.stringify({colours, slots, veto: R.UTIL_AGENT_COLORS.Veto}));
    """, None)
    assert actual == {"colours": ["#f4f7fb", "#123456"], "slots": [9], "veto": "#7fc8b8"}


SEMTEX = {"kind": "Projectile", "code": "Grenadier", "name": "Q_SemtexBasic", "agent": "KAY/O", "slot": 4,
          "t0": 17.689, "t1": 22.21, "flight": [[17.689, 1, 2, 0], [18.704, 3, 4, 0]]}
FRAGMENT_ZONE = {"kind": "GameObject", "code": "Grenadier", "name": "Q_SemtexBasic_Zone", "agent": "KAY/O",
                 "slot": 4, "u": 3, "v": 4, "t0": 18.704, "t1": 22.21, "zoneOf": 0}


@needs_node
def test_aftershock_pulses_and_empty_schedule():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = {kind: "GameObject", code: "Breach", name: "4_FusionBlast", t0: 35.693, t1: 39.996};
      const early = {...a, t0: 10, t1: 11};
      const open = {...early, t1: null};
      console.log(JSON.stringify({times: R.popTimes(a), until: R.popUntil(a, 0.6),
        visible: [39.0, 39.2].map(t => R.abilitiesAt([a], t, 90).length),
        empty: R.popTimes(early), emptyUntil: R.popUntil(early, 0.6),
        earlyVisible: [10.5, 11.5].map(t => R.abilitiesAt([early], t, 90).length),
        open: R.popTimes(open), fx: R.popTimes({...a, fx: [36, 38]}),
        fxTimed: R.popTimesTimed({...a, fx: [36, 38]}), emptyFx: R.popTimes({...a, fx: []})}));
    """, None)
    assert actual["times"] == pytest.approx([37.893, 38.493], rel=0, abs=1e-9)
    assert actual["until"] == pytest.approx(39.093, rel=0, abs=1e-9)
    assert actual["visible"] == [1, 0]
    assert actual["empty"] == [] and actual["emptyUntil"] == 11
    assert actual["earlyVisible"] == [1, 0]
    assert actual["open"] == pytest.approx([12.2, 12.8], rel=0, abs=1e-9)
    assert actual["fx"] == [36, 38] and actual["fxTimed"] is False
    assert actual["emptyFx"] == pytest.approx(actual["times"], rel=0, abs=1e-9)


@needs_node
def test_mpulse_heal_clips_pulses_but_concuss_keeps_recorded_fx():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const rows = [[24.395, 26.586], [24.395, 25.0], [64.947, 69.947]].map(([t0, t1]) =>
        ({kind: "GameObject", code: "Iris", name: "Heal", t0, t1}));
      rows.push({kind: "GameObject", code: "Iris", name: "Concuss", t0: 32.208, t1: 37,
                 fx: [32.208, 34.209, 36.213]});
      console.log(JSON.stringify(rows.map(a => ({times: R.popTimes(a), timed: R.popTimesTimed(a)}))));
    """, None)
    for row, times in zip(actual, [[24.395, 26.395], [24.395], [64.947, 66.947, 68.947],
                                   [32.208, 34.209, 36.213]], strict=True):
        assert row["times"] == pytest.approx(times, rel=0, abs=1e-9)
    assert [row["timed"] for row in actual] == [True, True, True, False]


@needs_node
def test_timed_pulses_stay_opaque_past_close_with_gone_and_round_end_limits():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const smoke = {kind: "Zone", code: "Wraith", name: "4_Smoke", t0: 10, t1: 25};
      console.log(JSON.stringify({
        visible: [22.4, 22.7, 22.9].map(t => R.abilitiesAt([a], t, 90).length),
        alpha: [22.4, 22.7].map(t => R.abilityAlpha(a, t)),
        gone: [21.09, 21.1, 21.11].map(t => R.abilitiesAt([{...a, gone: 20.5}], t, 90).length),
        end: [21, 21.1].map(t => R.abilitiesAt([a], t, 21).length),
        smoke: [24.9, 25.1].map(t => R.abilitiesAt([smoke], t, 90).length),
        smokeAlpha: R.abilityAlpha(smoke, 25.1)}));
    """, FRAGMENT_ZONE)
    assert actual == {"visible": [1, 1, 0], "alpha": [1, 1], "gone": [1, 1, 0], "end": [1, 0],
                      "smoke": [1, 0], "smokeAlpha": pytest.approx(0.5)}


@needs_node
def test_pulse_styles_and_semtex_projectile_name():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const style = (code, name, kind = "GameObject") => R.abilityStyle({kind, code, name});
      console.log(JSON.stringify({heal: style("Iris", "Heal"), aftershock: style("Breach", "4_FusionBlast"),
        zone: style("Grenadier", "Q_SemtexBasic_Zone"),
        projectile: style("Grenadier", "Q_SemtexBasic", "Projectile")}));
    """, None)
    for name, fields in {
        "heal": {"ability": "M-pulse", "label": "M-pulse (heal)", "shape": "area", "r": 500,
                 "pop": 0.8, "pulses": [0, 2, 4]},
        "aftershock": {"ability": "Aftershock", "shape": "area", "r": 300, "capsule": 1000,
                       "pop": 0.6, "pulses": [2.2, 2.8]},
        "zone": {"ability": "FRAG/ment", "shape": "area", "r": 400, "pop": 0.6,
                 "pulses": [0.5, 1.5, 2.5, 3.5], "part": True, "unlisted": True},
        "projectile": {"ability": "FRAG/ment"},
    }.items():
        assert {key: actual[name].get(key) for key in fields} == fields


@needs_node
def test_fragment_zones_use_landing_and_preserve_inputs():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const rows = [a, {...a, t1: undefined}, {...a, flight: [a.flight[0]]}, {...a, flight: undefined},
                    {...a, flight: []}, {...a, name: "C_Flash"}, {...a, kind: "GameObject"}];
      const before = JSON.stringify(rows);
      const zones = R.fragmentZones(rows);
      console.log(JSON.stringify({zones, pops: R.popTimes(zones[0]), unchanged: before === JSON.stringify(rows)}));
    """, SEMTEX)
    assert actual["zones"][0] == FRAGMENT_ZONE
    assert actual["zones"][1] == {**FRAGMENT_ZONE, "t1": 22.204, "zoneOf": 1}
    assert actual["zones"][2] == {**FRAGMENT_ZONE, "u": 1, "v": 2, "t0": 17.689, "zoneOf": 2}
    assert len(actual["zones"]) == 3
    assert actual["pops"] == pytest.approx([19.204, 20.204, 21.204, 22.204], rel=0, abs=1e-9)
    assert actual["unchanged"] is True


@needs_node
def test_fragment_zone_does_not_add_a_utility_item_or_cast():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const v = Object.create(R.ReplayViewer.prototype);
      v.current = {blob: {util: []}, extras: {abilities: [a,
        {kind: "GameObject", code: "Iris", name: "Heal", agent: "Miks", slot: 3, t0: 10, t1: 15}]}};
      const before = v.utilItems();
      v.current.extras.abilities.push(...R.fragmentZones([a]));
      console.log(JSON.stringify({before, after: v.utilItems()}));
    """, SEMTEX)
    assert len(actual["before"]) == 1 and actual["before"][0]["cast"] is True
    assert actual["after"] == actual["before"]


@needs_node
@pytest.mark.parametrize("own_extras", [False, True])
def test_fetch_round_appends_zones_after_indexes_without_mutating_extras(own_extras):
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const {a, ownExtras} = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const {t0, slot, ...rest} = a;
      const blob = {v: 1, round: 1, hz: 16, t_end: 90, tracks: {}, players: [], kills: [],
                    util: [{...rest, k: "ability", t: t0, by: slot}]};
      // Include an actual wall trace and bomb so both reference indexes contain data.
      const abilities = [a,
        {kind: "Projectile", code: "Mage", name: "Q_Wall", slot: 2, t0: 5, t1: 10,
         flight: [[5, 0, 0, 0], [6, 1, 1, 0]]},
        {kind: "GameObject", code: "Mage", name: "Q_WallManager", slot: 2, t0: 5, t1: 10},
        {kind: "Bomb", t0: 30, t1: 60}];
      if (ownExtras) blob.extras = {abilities, shots: [], custom: "preserved"};
      const original = blob.extras || R.extrasFromUtil(blob.util);
      const before = JSON.stringify(original);
      const traces = R.traceWalls(original.abilities), state = R.indexRoundState(blob, original.abilities);
      const v = Object.create(R.ReplayViewer.prototype);
      let loads = 0;
      v.cache = {}; v.options = {loadRound: () => {loads++; return Promise.resolve(blob);}};
      (async () => {
        const round = await v.fetchRound(1), again = await v.fetchRound(1);
        console.log(JSON.stringify({zone: round.extras.abilities.at(-1), length: round.extras.abilities.length,
          originalLength: original.abilities.length, unchanged: before === JSON.stringify(original),
          copied: round.extras !== original, custom: round.extras.custom, traceCount: Object.keys(traces).length,
          tracesSame: JSON.stringify(round.traces) === JSON.stringify(traces),
          stateSame: JSON.stringify(round.state) === JSON.stringify(state), cached: round === again, loads}));
      })().catch(e => {console.error(e); process.exitCode = 1;});
    """, {"a": SEMTEX, "ownExtras": own_extras})
    assert actual["zone"] == FRAGMENT_ZONE
    assert actual["length"] == actual["originalLength"] + 1
    for key in ("unchanged", "copied", "tracesSame", "stateSame", "cached"):
        assert actual[key] is True, key
    assert actual["loads"] == 1
    if own_extras:
        assert actual["custom"] == "preserved" and actual["originalLength"] == 4
        assert actual["traceCount"] == 1


# Recording canvas based on test_viewer_annotations, including gradients and stroke widths.
UTIL_DRAW = r"""
  global.getComputedStyle = () => ({getPropertyValue: () => ""});
  global.Image = class { constructor() { this.complete = false; } };
  const R = require(process.argv[1]);
  function mockCtx(log) {
    function gradient(kind, args) {
      const g = {kind, args, stops: [], addColorStop(at, color) { this.stops.push([at, color]); }};
      log.push({op: "gradient", gradient: g}); return g;
    }
    const keys = ["fillStyle", "strokeStyle", "globalAlpha", "font", "lineWidth", "textAlign", "textBaseline",
                  "lineDashOffset", "globalCompositeOperation", "lineCap", "lineJoin", "dash"];
    let path = [], stack = [], tf = [1, 0, 0, 1, 0, 0];
    const c = {fillStyle: "#000", strokeStyle: "#000", globalAlpha: 1, font: "10px sans-serif", lineWidth: 1,
               textAlign: "start", textBaseline: "alphabetic", lineDashOffset: 0, dash: []};
    const snap = () => ({fill: c.fillStyle, stroke: c.strokeStyle, alpha: c.globalAlpha, font: c.font,
                        tf: tf.slice(), dash: c.dash.slice(), offset: c.lineDashOffset, lineWidth: c.lineWidth});
    const rec = (op) => {
      const pts = path.filter(p => p[0] === "M" || p[0] === "L");
      const arcs = path.filter(p => p[0] === "A");
      const row = Object.assign({op: op, path: path.slice()}, snap());
      if (op === "fill" && pts.length === 4 && arcs.length === 0 && c.fillStyle === "#ff4655") {
        row.spike = true;
        row.cx = (pts[0][1] + pts[2][1]) / 2; row.cy = (pts[0][2] + pts[2][2]) / 2;
      }
      log.push(row);
    };
    Object.assign(c, {
      save() { const o = {tf: tf.slice()}; keys.forEach(k => { o[k] = c[k]; }); stack.push(o); },
      restore() { const o = stack.pop(); if (!o) return; tf = o.tf; keys.forEach(k => { c[k] = o[k]; }); },
      setTransform(a, b, cc, d, e, f) { tf = [a, b, cc, d, e, f]; },
      beginPath() { path = []; }, closePath() { path.push(["Z"]); },
      moveTo(x, y) { path.push(["M", x, y]); }, lineTo(x, y) { path.push(["L", x, y]); },
      arc(x, y, r, a0, a1) { path.push(["A", x, y, r, a0, a1]); }, rect(x, y, w, h) { path.push(["R", x, y, w, h]); },
      fill() { rec("fill"); }, stroke() { rec("stroke"); },
      fillRect(x, y, w, h) { log.push(Object.assign({op: "fillRect", x, y, w, h}, snap())); },
      strokeRect(x, y, w, h) { log.push(Object.assign({op: "strokeRect", x, y, w, h}, snap())); },
      fillText(text, x, y) { log.push(Object.assign({op: "fillText", text: String(text), x, y}, snap())); },
      measureText(text) {
        const m = /(\d+(?:\.\d+)?)px/.exec(c.font); const px = m ? parseFloat(m[1]) : 10;
        return {width: String(text).length * px * 0.6};
      },
      setLineDash(dash) { c.dash = dash.slice(); }, getLineDash() { return c.dash.slice(); },
      clip() { rec("clip"); }, clearRect() {},
      createRadialGradient(...args) { return gradient("radial", args); },
      createLinearGradient(...args) { return gradient("linear", args); },
      createPattern() { log.push({op: "createPattern"}); return "noise-pattern"; },
      drawImage(img, x, y, w, h) { log.push(Object.assign({op: "drawImage", src: img.src, x, y, w, h}, snap())); }
    });
    return new Proxy(c, {get: (o, k) => (k in o ? o[k] : () => {})});
  }
  const p = JSON.parse(require("fs").readFileSync(0, "utf8"));
  const log = [], hits = [], v = Object.create(R.ReplayViewer.prototype);
  const row = p.row;
  Object.assign(v, {root: {}, t: p.t === undefined ? 12 : p.t, controlPalette: p.palette || "green-red", linked: p.linked || null,
    current: {blob: {t_end: 40, util: [], players: [{slot: 1, side: "A"}], alive: p.alive || {"1": [[0, 40]]}},
      tracks: p.tracks || {}, extras: {abilities: p.rows || [row], statuses: p.statuses || [], reveals: []}}});
  v.abilityIcon = () => p.glyph ? {src: "glyph"} : null;
  v.abilityText = () => "utility";
  v.nameOf = () => "owner";
  if (p.ownerColor) v.ownerColor = () => p.ownerColor;
  const ctx = mockCtx(log);
  if (p.projectile && !p.both) v.drawProjectiles(ctx, 1000, 20, hits);
  else v.drawAbilities(ctx, 1000, 20, hits);
  if (p.both) v.drawProjectiles(ctx, 1000, 20, hits);
  let tips = [];
  if (p.probes) {
    v.hits = p.hit ? [p.hit] : hits;
    v.ui = {tip: {hidden: true, style: {}, offsetWidth: 10, offsetHeight: 10,
      parentNode: {getBoundingClientRect: () => ({width: 1000, height: 1000})}}};
    tips = p.probes.map(([x, y]) => {
      v.showTip({x, y, px: 0, py: 0}); return !v.ui.tip.hidden;
    });
  }
  console.log(JSON.stringify({log, hits, tips, radius: v.uvRadius(410) * 1000}));
"""


def ability(code, name, agent, **kw):
    return dict(kind="Zone", code=code, name=name, agent=agent, slot=1,
                u=0.4, v=0.5, t0=10, t1=25, **kw)


def util_draw(row, **kw):
    return run_node(UTIL_DRAW, dict(row=row, **kw))


def arc_calls(log, op, radius):
    return [c for c in log if c["op"] == op and any(
        p[0] == "A" and p[3] == pytest.approx(radius) for p in c.get("path", []))]


@needs_node
def test_colour_helpers():
    assert run_node('''const R = require(process.argv[1]); console.log(JSON.stringify([
      R.withAlpha("#5c46a8", 0.75), R.lighten("#4fe07a", 0.45),
      R.lighten("#123456", 0), R.lighten("#123456", 1)]));''', None) == [
        "rgba(92, 70, 168, 0.75)", "#9eeeb6", "#123456", "#ffffff"]


@needs_node
@pytest.mark.parametrize("color,alpha,light", [
    ("#abc", "rgba(170, 187, 204, 0.5)", "#d5dde6"),
    ("#AbC", "rgba(170, 187, 204, 0.5)", "#d5dde6"),
    ("#AAbbCC", "rgba(170, 187, 204, 0.5)", "#d5dde6"),
    ("#000", "rgba(0, 0, 0, 0.5)", "#808080"),
    ("rgb(1, 2, 3)", "rgba(1, 2, 3, 0.5)", "#808181"),
    ("red", "red", "red"),
    ("#abcd", "#abcd", "#abcd"),
    ("rgb(no, 2, 3)", "rgb(no, 2, 3)", "rgb(no, 2, 3)"),
    ("var(--custom-util)", "var(--custom-util)", "var(--custom-util)"),
])
def test_colour_helpers_accept_css_fallbacks(color, alpha, light):
    actual = run_node('''const R = require(process.argv[1]);
      const color = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(JSON.stringify([R.withAlpha(color, 0.5), R.lighten(color, 0.5)]));''', color)
    assert actual == [alpha, light]


@needs_node
def test_smoke_accepts_rgb_owner_colour_fallback():
    row = ability("Wushu", "4_SmokeZone", "Unlisted")
    log = util_draw(row, ownerColor="rgb(57, 135, 229)")["log"]
    assert "NaN" not in json.dumps(log)
    patch = next(c for c in log if c["op"] == "fillRect")
    assert patch["fill"]["stops"][0] == [0, "rgba(57, 135, 229, 0.75)"]


@needs_node
@pytest.mark.parametrize("kw,side", [({}, "#5ccf8f"), ({"palette": "teal-coral"}, "#1fb8c9"),
    ({"linked": {"players": {"1": {"team": "team-2"}}}}, "#d9534f")])
def test_smoke_glass_body_and_side_badge(kw, side):
    out = util_draw(ability("Wraith", "4_Smoke", "Omen"), **kw)
    log, rad = out["log"], out["radius"]
    assert any(c["op"] == "clip" for c in log)
    patches = [c for c in log if c["op"] == "fillRect" and isinstance(c["fill"], dict)]
    assert patches[0]["fill"]["stops"] == [
        [0, "rgba(92, 70, 168, 0.75)"], [0.55, "rgba(92, 70, 168, 0.45)"],
        [1, "rgba(92, 70, 168, 0.3)"]]
    glow = patches[1]["fill"]
    assert glow["args"][0] < 400 and glow["args"][1] < 500
    assert glow["stops"][0][1] == "rgba(255, 255, 255, 0.28)"
    assert any(c["stroke"] == "#a599cf" and c["lineWidth"] == 1.5
               for c in arc_calls(log, "stroke", rad))
    assert any(c["stroke"] == side and c["lineWidth"] == 1.5 and c["alpha"] == 0.75
               for c in arc_calls(log, "stroke", rad + 1.5))
    assert any(c["fill"] == "#151922" for c in arc_calls(log, "fill", 8.8))
    assert any(c["stroke"] == side for c in arc_calls(log, "stroke", 8.8))
    assert "rgba(16, 18, 24" not in json.dumps(log)
    assert any(c["stroke"] == "#ffffff" and c["lineWidth"] == 2.5 and
               c["path"][0][4:] == [-1.2, 0.35] for c in log if c["op"] == "stroke")


@needs_node
def test_area_soft_gradient_without_outline_or_node_noise():
    log = util_draw(ability("Phoenix", "MolotovFire", "Phoenix"))["log"]
    fills = [c for c in log if c["op"] == "fillRect" and isinstance(c["fill"], dict)]
    assert fills[0]["fill"]["stops"] == [[0, "rgba(255, 123, 46, 0.7)"],
        [0.6, "rgba(255, 123, 46, 0.5)"], [1, "rgba(255, 123, 46, 0.12)"]]
    assert not any(c["stroke"] == "#ff7b2e" for c in arc_calls(log, "stroke", 450 * 0.75 * 1000))
    assert any(c["stroke"] == "#5ccf8f" for c in arc_calls(log, "stroke", 450 * 0.75 * 1000 + 1.5))
    assert not any(c["op"] == "createPattern" for c in log)


@needs_node
@pytest.mark.parametrize("state", ["dead", "suppressed", "arena", "off"])
def test_disabled_device_uses_down_fill_and_ring(state):
    row = ability("Gumshoe", "E_PossessableCamera", "Cypher")
    kw = {}
    if state == "dead":
        kw["alive"] = {"1": [[0, 11]]}
    elif state == "suppressed":
        kw["statuses"] = [{"target": 1, "status": "suppressed", "t0": 11, "t1": 13}]
    elif state == "arena":
        kw["tracks"] = {"1": [[{"t": 11, "z": -4990}, {"t": 13, "z": -4990}]]}
    else:
        row.update(code="Killjoy", name="E_Turret", agent="Killjoy", off=[[11, 13]])
    log = util_draw(row, **kw)["log"]
    assert any(c["fill"] == "#7d828c" for c in log if c["op"] == "fill")
    assert any(c["stroke"] == "#7d828c" and c["lineWidth"] == 2 for c in log if c["op"] == "stroke")
    assert "#5ccf8f" not in json.dumps(log)


BAR_ROWS = [
    (ability("Pandemic", "E_SmokeScreenManager", "Viper", points=[[0.2, 0.3], [0.7, 0.8]], on=[[10, 25]]),
     "#4fe07a", "rgba(79, 224, 122, 0.92)", "#9eeeb6", 22, 1),
    (ability("Thorne", "E_Wall_Fortifying", "Sage", segments=[[0.3, 0.4, 10, None], [0.4, 0.4, 10, None]]),
     "#8ff0e4", "rgba(143, 240, 228, 0.92)", "#c1f7f0", 20 / 2.2, 2),
    (ability("Rift", "X_GlobalWall", "Astra", yaw=0),
     "#b07cff", "rgba(176, 124, 255, 0.92)", "#d4b7ff", 20 / 2.2, 1),
    (ability("Sprinter", "4_Tunnel", "Neon", yaw=0),
     "#46d2ff", "rgba(70, 210, 255, 0.92)", "#99e6ff", 14, 2),
    (ability("Cable", "E_CableJam_Root", "Deadlock", arms=[[0.7, 0.8, 10, None]], on=[[10, 25]]),
     "#dbe7f5", "rgba(219, 231, 245, 0.92)", "#ebf2fa", 20 / 3, 1),
    (ability("Nox", "WallTrap", "Vyse", line=[[0.2, 0.3], [0.7, 0.8]], raised=[11, 20]),
     "#b18cff", "rgba(177, 140, 255, 0.92)", "#d4c0ff", 20 / 3, 1),
]


@needs_node
@pytest.mark.parametrize("row,color,body,core,width,paths", BAR_ROWS)
def test_solid_bars_have_three_strokes_without_side_edge(row, color, body, core, width, paths):
    log = util_draw(row)["log"]
    strokes = [c for c in log if c["op"] == "stroke" and any(p[0] == "L" for p in c["path"])]
    assert len(strokes) == paths * 3
    for i in range(paths):
        under, middle, light = strokes[i * 3:i * 3 + 3]
        assert under["path"] == middle["path"] == light["path"]
        assert [c["stroke"] for c in (under, middle, light)] == ["rgba(10, 12, 18, 0.55)", body, core]
        assert [c["lineWidth"] for c in (under, middle, light)] == pytest.approx([width + 2, width, width / 4])
        assert light["alpha"] == pytest.approx(0.6)
        assert not any(c["dash"] for c in (under, middle, light))


@needs_node
@pytest.mark.parametrize("row,color", [
    (ability("Cable", "E_CableJam_Root", "Deadlock", arms=[[0.7, 0.8, 10, None]], on=[[15, 25]]), "#dbe7f5"),
    (ability("Nox", "WallTrap", "Vyse", line=[[0.2, 0.3], [0.7, 0.8]], raised=[15, 20]), "#b18cff"),
    (ability("Pandemic", "E_SmokeScreenManager", "Viper", points=[[0.2, 0.3], [0.7, 0.8]], on=[[15, 25]]), "#4fe07a"),
])
def test_unraised_bars_stay_single_dashed_util_strokes(row, color):
    strokes = [c for c in util_draw(row)["log"] if c["op"] == "stroke" and any(p[0] == "L" for p in c["path"])]
    assert len(strokes) == 1
    assert strokes[0]["stroke"] == color
    assert strokes[0]["dash"]


@needs_node
def test_trip_wire_and_end_dot_keep_util_colour():
    log = util_draw(ability("Gumshoe", "4_TripWire", "Cypher", end=[0.7, 0.8]))["log"]
    strokes = [c for c in log if c["op"] == "stroke" and any(p[0] == "L" for p in c["path"])]
    assert len(strokes) == 1 and strokes[0]["stroke"] == "#e9edf2"
    assert any(c["fill"] == "#e9edf2" for c in arc_calls(log, "fill", 4.4))


@needs_node
@pytest.mark.parametrize("glyph", [True, False])
def test_util_badge_dark_disc_side_ring_and_glyph_or_dot(glyph):
    log = util_draw(ability("Gumshoe", "E_PossessableCamera", "Cypher"), glyph=glyph)["log"]
    rad = 20 * 0.62
    assert any(c["fill"] == "#151922" for c in arc_calls(log, "fill", rad))
    assert any(c["stroke"] == "#5ccf8f" and c["lineWidth"] == 2 for c in arc_calls(log, "stroke", rad))
    if glyph:
        img = next(c for c in log if c["op"] == "drawImage")
        assert img["w"] == img["h"] == pytest.approx(rad * 1.3)
    else:
        assert any(c["fill"] == "#e9edf2" for c in arc_calls(log, "fill", rad * 0.38))


@needs_node
def test_projectile_agent_dot_and_side_ring():
    row = ability("Grenadier", "C_Flash", "KAY/O", flight=[[10, 0.2, 0.3, 0], [15, 0.7, 0.8, 0]])
    row["kind"] = "Projectile"
    log = util_draw(row, projectile=True)["log"]
    assert any(c["fill"] == "#4a95ff" for c in arc_calls(log, "fill", 4.4))
    assert any(c["stroke"] == "#5ccf8f" and c["lineWidth"] == 1.5 for c in arc_calls(log, "stroke", 4.4))


@needs_node
def test_dissipate_keeps_faint_body_and_dashed_ring():
    log = util_draw(ability("Rift", "E_SmokeZone_Fake", "Astra"))["log"]
    patch = next(c for c in log if c["op"] == "fillRect")
    assert patch["alpha"] == 0.45
    assert any(c["dash"] for c in arc_calls(log, "stroke", 475 * 0.75 * 1000))


AFTERSHOCK = {"kind": "GameObject", "code": "Breach", "name": "4_FusionBlast", "slot": 1,
              "u": 0.4, "v": 0.5, "t0": 35.693, "t1": 39.996, "yaw": 77}
UTIL_SCALE = {"uvPerUnit": 0.0001, "players": {}}


def assert_dim_area(log):
    patches = [c for c in log if c["op"] == "fillRect" and isinstance(c["fill"], dict)]
    assert len(patches) == 1
    assert patches[0]["alpha"] == pytest.approx(0.4)
    assert [stop[0] for stop in patches[0]["fill"]["stops"]] == [0, 0.6, 1]
    assert [stop[1].rsplit(", ", 1)[1] for stop in patches[0]["fill"]["stops"]] == ["0.7)", "0.5)", "0.12)"]


@needs_node
def test_capsule_ends_axes_and_missing_yaw():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      console.log(JSON.stringify([0, 90, undefined, null].map(yaw =>
        R.capsuleEnds({u: 0.5, v: 0.5, yaw}, {capsule: 1000}, 0.0001))));
    """, None)
    assert actual == [[[0.5, 0.5], [0.6, 0.5]], [[0.5, 0.5], [0.5, 0.6]], None, None]


@needs_node
def test_aftershock_windup_capsule_and_side_ring():
    out = util_draw(AFTERSHOCK, t=36, linked=UTIL_SCALE)
    dashed = [c for c in arc_calls(out["log"], "stroke", 30) if c["dash"]]
    assert len(dashed) == 1
    arcs = [p for p in dashed[0]["path"] if p[0] == "A"]
    ends = [[400, 500], [400 + 100 * math.cos(math.radians(77)), 500 + 100 * math.sin(math.radians(77))]]
    assert len(arcs) == 2
    for arc, end in zip(arcs, ends, strict=True):
        assert arc[1:3] == pytest.approx(end, abs=1e-6)
        assert abs(arc[5] - arc[4]) == pytest.approx(math.pi)
    assert sum(p[0] == "L" for p in dashed[0]["path"]) == 2
    assert out["hits"][0]["r"] == pytest.approx(30)
    for end, expected in zip(out["hits"][0]["seg"], ends, strict=True):
        assert end == pytest.approx(expected, abs=1e-6)
    assert any(c["stroke"] == "#5ccf8f" and c["alpha"] == 0.75 and c["lineWidth"] == 1.5
               for c in arc_calls(out["log"], "stroke", 31.5))
    assert not arc_calls(out["log"], "fill", 30)
    assert not any(c["op"] == "fillRect" for c in out["log"])


@needs_node
@pytest.mark.parametrize("t,pop", [(37.893 + 0.3, 37.893), (38.6, 38.493)])
def test_aftershock_pulse_flash_dim_area_and_opening_capsule(t, pop):
    out = util_draw(AFTERSHOCK, t=t, linked=UTIL_SCALE)
    log = out["log"]
    flashes = arc_calls(log, "fill", 30)
    assert len(flashes) == 1
    f = (t - pop) / 0.6
    assert flashes[0]["alpha"] == pytest.approx(0.55 * (1 - f))
    assert_dim_area(log)
    clips = [c for c in log if c["op"] == "clip"]
    assert len(clips) == 1 and len([p for p in clips[0]["path"] if p[0] == "A"]) == 2
    rings = arc_calls(log, "stroke", 30 * (0.35 + 0.65 * f))
    assert len(rings) == 1
    assert rings[0]["stroke"] == flashes[0]["fill"]
    arcs = [p for p in rings[0]["path"] if p[0] == "A"]
    ends = out["hits"][0]["seg"]
    mid = [(ends[0][i] + ends[1][i]) / 2 for i in range(2)]
    for arc, end in zip(arcs, ends, strict=True):
        assert arc[1:3] == pytest.approx([mid[i] + (end[i] - mid[i]) * (0.35 + 0.65 * f) for i in range(2)])
    assert not any(c["dash"] for c in log if c["op"] == "stroke")
    suffix = "went off at 37.9" + (", 38.5" if t > 38.493 else "") + " s (timed from the ability, not recorded)"
    assert out["hits"][0]["text"].endswith(suffix)


@needs_node
def test_pop_dim_between_pulses_and_aftershock_disappears_after_last():
    row = {"kind": "GameObject", "code": "Iris", "name": "Concuss", "slot": 1,
           "u": 0.4, "v": 0.5, "t0": 30, "t1": 35, "fx": [30, 32, 34]}
    between = util_draw(row, t=31, linked=UTIL_SCALE)
    assert_dim_area(between["log"])
    assert not arc_calls(between["log"], "fill", 50)
    assert not any(c["dash"] for c in between["log"] if c["op"] == "stroke")
    out = util_draw(AFTERSHOCK, t=39.2, linked=UTIL_SCALE)
    assert out["log"] == [] and out["hits"] == []


@needs_node
def test_aftershock_missing_yaw_circle_and_empty_schedule():
    row = {k: v for k, v in AFTERSHOCK.items() if k != "yaw"}
    circle = util_draw(row, t=36, linked=UTIL_SCALE)
    outline = next(c for c in arc_calls(circle["log"], "stroke", 30) if c["dash"])
    assert len([p for p in outline["path"] if p[0] == "A"]) == 1
    assert not circle["hits"][0].get("seg")
    empty = util_draw({**AFTERSHOCK, "t0": 10, "t1": 11}, t=10.5, linked=UTIL_SCALE)
    assert any(c["dash"] for c in arc_calls(empty["log"], "stroke", 30))
    assert not arc_calls(empty["log"], "fill", 30)
    assert not any(c["op"] == "fillRect" for c in empty["log"])
    # JSON turns non-finite JS numbers into null, so inspect all numeric draw fields.
    for c in empty["log"]:
        if "alpha" in c:
            assert c["alpha"] is not None and math.isfinite(c["alpha"])
        for point in c.get("path", []):
            assert all(n is not None and math.isfinite(n) for n in point[1:])


@needs_node
def test_mpulse_recorded_second_flash_and_dim_area():
    row = {"kind": "GameObject", "code": "Iris", "name": "Concuss", "slot": 1,
           "u": 0.4, "v": 0.5, "t0": 30, "t1": 35, "fx": [30, 32, 34]}
    out = util_draw(row, t=32.2, linked=UTIL_SCALE)
    flashes = arc_calls(out["log"], "fill", 50)
    assert len(flashes) == 1 and flashes[0]["alpha"] == pytest.approx(0.55 * 0.75)
    assert_dim_area(out["log"])
    assert "(timed" not in out["hits"][0]["text"]
    assert out["hits"][0]["text"].endswith("went off at 30.0, 32.0 s")


@needs_node
@pytest.mark.parametrize("t,dot,zone", [(18, True, False), (19.3, False, True),
                                      (22.5, False, True), (22.9, False, False)])
def test_fragment_landing_hands_projectile_over_to_zone(t, dot, zone):
    out = util_draw(SEMTEX, rows=[SEMTEX, FRAGMENT_ZONE], both=True, t=t, linked=UTIL_SCALE)
    assert bool(arc_calls(out["log"], "fill", 4.4)) is dot
    flashes = arc_calls(out["log"], "fill", 40)
    assert bool(flashes) is zone
    if zone:
        assert len(flashes) == 1
        assert flashes[0]["path"][0][1:3] == [3000, 4000]
    else:
        assert not any(h.get("area") for h in out["hits"])


@needs_node
@pytest.mark.parametrize("flight,zone,t", [(True, False, 19.3), (False, True, 19.3), (True, True, 18.704)])
def test_semtex_keeps_dot_without_zone_or_flight_and_at_landing(flight, zone, t):
    row = SEMTEX if flight else {k: v for k, v in SEMTEX.items() if k != "flight"}
    row = {**row, "u": 0.4, "v": 0.5}
    out = util_draw(row, rows=[row] + ([FRAGMENT_ZONE] if zone else []), projectile=True, t=t)
    assert len(arc_calls(out["log"], "fill", 4.4)) == 1


@needs_node
def test_capsule_tooltip_covers_segment_and_round_caps():
    row = {**AFTERSHOCK, "yaw": 0}
    out = util_draw(row, t=36, linked=UTIL_SCALE,
                    probes=[[500, 500], [450, 527], [450, 533], [530.01, 500]])
    assert out["tips"] == [True, True, False, False]
    circle = util_draw(row, t=36, linked=UTIL_SCALE, hit={"x": 10, "y": 20, "r": 5, "text": "circle"},
                       probes=[[10, 20], [15, 20], [15.01, 20]])
    assert circle["tips"] == [True, True, False]

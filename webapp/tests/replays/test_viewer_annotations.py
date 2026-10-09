"""P05: one annotation layout per player on the minimap, drawn on a recording mock canvas. Each player's
condition chips sit above the circle, the health/name block below it and the spike carrier badge at its upper
right; one spike pass outside the abilities toggle owns the carried, dropped and planted markers. The checks
read the canvas calls in order (paint order, one spike glyph at every time, the plant transition with abilities
on and off), the viewer's `layout` record (bounds, collisions with stacked players, map edges, zoom) and its
tooltip hits. Synthetic fixtures only. The JS checks need Node; they skip without it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

SPIKE = "#ff4655"
PLANT_T = 40.0


def run_node(script: str, payload):
    completed = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# A real viewer prototype drawing onto a mock 2d context that records every fill, stroke, fillRect and fillText
# with the state it was drawn in. Control and gaps are stubbed: control leaves one marker call, so its place in
# the paint order is known. A filled four-point polygon in the spike colour is a spike glyph.
DRAW = r"""
  global.getComputedStyle = () => ({getPropertyValue: () => ""});
  const R = require(process.argv[1]);
  function mockCtx(log) {
    const keys = ["fillStyle", "strokeStyle", "globalAlpha", "font", "lineWidth", "textAlign", "textBaseline",
                  "lineDashOffset", "globalCompositeOperation", "lineCap", "lineJoin"];
    let path = [], stack = [], tf = [1, 0, 0, 1, 0, 0];
    const c = {fillStyle: "#000", strokeStyle: "#000", globalAlpha: 1, font: "10px sans-serif", lineWidth: 1,
               textAlign: "start", textBaseline: "alphabetic", lineDashOffset: 0};
    const snap = () => ({fill: c.fillStyle, stroke: c.strokeStyle, alpha: c.globalAlpha, font: c.font, tf: tf.slice()});
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
      setLineDash() {}, getLineDash() { return []; }, clip() {}, clearRect() {},
      drawImage() { log.push({op: "drawImage"}); }
    });
    return new Proxy(c, {get: (o, k) => (k in o ? o[k] : () => {})});
  }
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input);
    const log = [];
    const v = Object.create(R.ReplayViewer.prototype);
    const blob = p.blob;
    const extras = R.extrasFromUtil(blob.util);
    Object.assign(v, {
      options: {}, icons: {}, linked: p.linked || null, t: p.t, hover: null, highlight: null, number: blob.round,
      layers: Object.assign({names: true, abilities: true, projectiles: true, tracers: true, cones: false,
                             control: false, gaps: false}, p.layers || {}),
      view: p.view || {k: 1, ox: 0, oy: 0}, canvas: {width: 1024, setAttribute() {}}, map: {complete: false},
      ui: {state: {}, hud: {hidden: true, innerHTML: ""}}, controlCache: null, control: null,
      current: {blob: blob, tracks: R.decodeRound(blob), extras: extras, wires: R.pairWires(extras.abilities),
                state: R.indexRoundState(blob, extras.abilities)}
    });
    v.ctx = mockCtx(log);
    v.drawControl = function (ctx) { ctx.fillText("CONTROL-MARK", 0, 0); };
    v.drawGaps = function () {};
    v.personHtml = function (slot) { return "P" + slot; };
    if (p.knowing) { v.layers.control = true; v.controlCache = {ready: () => ({})}; v.knowingGroup = () => p.knowing; }
    v.draw();
    v.renderHud();
    const out = {log: log, layout: v.layout || null, hits: v.hits.map(h => ({x: h.x, y: h.y, r: h.r, slot: h.slot,
                 text: h.text, area: !!h.area})), hud: {hidden: v.ui.hud.hidden, html: v.ui.hud.innerHTML},
                 aria: v.ui.state.textContent, r: 1024 / 48 / Math.sqrt(v.view.k), s: 1024 / 10000};
    if (p.tip) {
      v.ui.tip = {textContent: "", hidden: true, style: {}, offsetWidth: 10, offsetHeight: 10,
                  parentNode: {getBoundingClientRect: () => ({width: 1000, height: 1000})}};
      out.tips = p.tip.map(pt => { v.showTip({x: pt[0], y: pt[1], px: 0, py: 0});
                                   return v.ui.tip.hidden ? null : v.ui.tip.textContent; });
    }
    if (p.click) {
      v.control = {}; let picked = "no selection"; v.setHighlight = slot => { picked = slot; };
      v.canvas.getBoundingClientRect = () => ({left: 0, top: 0, width: 1024});
      out.clicks = p.click.map(pt => { picked = "no selection";
        v.onCanvasClick({clientX: (pt[0] - v.view.ox) * v.view.k, clientY: (pt[1] - v.view.oy) * v.view.k});
        return picked; });
    }
    process.stdout.write(JSON.stringify(out));
  });"""


def still(u, v, t0=-10.0, t1=100.0, hz=2):
    n = int((t1 - t0) * hz) + 1
    return [{"t0": t0, "u": [u] + [0] * (n - 1), "v": [v] + [0] * (n - 1), "yaw": [0] + [0] * (n - 1)}]


def vit(t, life, hp, sh, mhp=100, msh=50):
    return {"t": t, "life": life, "hp": hp, "sh": sh, "mhp": mhp, "msh": msh}


def round_blob(players, util=(), player_state=None, alive=None, t_end=100.0):
    """players: [(slot, side, agent, u, v)]."""
    blob = {"v": 1, "round": 5, "hz": 2, "t_end": t_end, "kills": [], "util": list(util),
            "players": [{"slot": s, "side": side, "agent": agent} for s, side, agent, _, _ in players],
            "tracks": {str(s): still(u, v) for s, _, _, u, v in players},
            "alive": alive or {str(s): [[-10.0, None, "spawn", []]] for s, _, _, _, _ in players}}
    if player_state is not None:
        blob["player_state"] = dict({"version": 1}, **player_state)
    return blob


LINKED = {"players": {str(s): {"name": f"Name{s}#tag", "team": "team-1" if s < 5 else "team-2"} for s in range(10)},
          "uvPerUnit": 0.75}

BOMB = {"k": "ability", "kind": "Bomb", "t": PLANT_T, "by": 1, "u": 6000, "v": 6000, "t1": None, "defuses": []}
FLASH = {"k": "flash", "t": 9.5, "by": 6, "ability": "phoenix_curveball_left", "hits": [[1, 10.0, 2.0]]}
NEAR = {"k": "nearsight", "t": 10.0, "by": 7, "ability": "omen_paranoia", "hits": [[1, 10.2, 2.0]]}
CONC = {"k": "status", "t": 10.1, "by": 8, "target": 1, "status": "concussed", "t1": 12.0, "code": "Iris",
        "name": "Concuss"}


def spike_round(extra_util=(), **kw):
    """Slot 1 carries from 0 s and plants at PLANT_T (its Bomb row); slot 6 defends far away."""
    ps = {"vitals": {"1": [vit(5.0, 0, 70, 20)]}, "damage_taken": {"1": [5.0]},
          "spike": [{"t": 0.0, "s": "carried", "slot": 1}, {"t": PLANT_T - 4, "s": "planting", "slot": 1},
                    {"t": PLANT_T + 0.01, "s": "planted", "u": 6000, "v": 6000}]}
    return round_blob([(1, "A", "Phoenix", 5000, 5000), (6, "B", "Sova", 2000, 2000)],
                      util=[BOMB, *extra_util], player_state=ps, **kw)


def draw(blob, t, **kw):
    return run_node(DRAW, dict({"blob": blob, "t": t, "linked": LINKED}, **kw))


def spikes(out):
    return [row for row in out["log"] if row.get("spike")]


def first(out, pred):
    return next(i for i, row in enumerate(out["log"]) if pred(row))


def boxes(out, kind=None, slot=None):
    return [b for b in out["layout"] if (kind is None or b["kind"] == kind) and (slot is None or b["slot"] == slot)]


def overlap(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def texts(out):
    return [row["text"] for row in out["log"] if row["op"] == "fillText"]


HEALTH_COLOURS = {"#4ade80": "healthy", "#facc15": "hurt", "#f87171": "low"}


def bars(out):
    """The health bars' fills as (log index, band, percent of the bar's length); D10: the bar has no number."""
    r = out["r"]
    return [(i, HEALTH_COLOURS[row["fill"]], round(100 * row["w"] / (r * 1.6)))
            for i, row in enumerate(out["log"]) if row["op"] == "fillRect" and row["fill"] in HEALTH_COLOURS]


def no_percent(out):
    return not any(t.endswith("%") for t in texts(out))


@needs_node
@pytest.mark.parametrize("abilities", [True, False])
def test_paint_order_control_then_ground_spike_then_players_then_annotations(abilities):
    out = draw(spike_round(), PLANT_T + 5, layers={"abilities": abilities})
    s, r = out["s"], out["r"]
    control = first(out, lambda row: row.get("text") == "CONTROL-MARK")
    ground = [i for i, row in enumerate(out["log"]) if row.get("spike")]
    assert len(ground) == 1
    circles = [i for i, row in enumerate(out["log"]) if row["op"] == "fill" and len(row["path"]) == 1
               and row["path"][0][0] == "A" and abs(row["path"][0][3] - r) < 1e-6]
    assert len(circles) == 2, "both players' circles"
    health = bars(out)[0][0]
    assert control < ground[0] < min(circles) < max(circles) < health and no_percent(out)
    row = out["log"][ground[0]]
    assert (row["cx"], row["cy"]) == pytest.approx((6000 * s, 6000 * s))


@needs_node
def test_paint_order_carrier_badge_over_the_circles():
    out = draw(spike_round(), 20.0)
    r = out["r"]
    circles = [i for i, row in enumerate(out["log"]) if row["op"] == "fill" and len(row["path"]) == 1
               and row["path"][0][0] == "A" and abs(row["path"][0][3] - r) < 1e-6]
    badge = [i for i, row in enumerate(out["log"]) if row.get("spike")]
    assert len(badge) == 1 and badge[0] > max(circles)


@needs_node
@pytest.mark.parametrize("abilities", [True, False])
def test_the_plant_transition_shows_exactly_one_spike_glyph_and_todays_hud(abilities):
    blob = spike_round()
    seen = {}
    for name, t in [("long_before", 20.0), ("planting", PLANT_T - 1), ("just_before", PLANT_T - 0.01),
                    ("at", PLANT_T), ("just_after", PLANT_T + 0.02), ("after", PLANT_T + 10)]:
        out = draw(blob, t, layers={"abilities": abilities})
        glyphs = spikes(out)
        assert len(glyphs) == 1, (name, len(glyphs))
        seen[name] = (out, glyphs[0])
    s, r = seen["at"][0]["s"], seen["at"][0]["r"]
    for name in ("long_before", "planting", "just_before"):
        out, g = seen[name]
        assert g["cx"] > 5000 * s and g["cy"] < 5000 * s, "the carrier badge at the circle's upper right"
        assert abs(g["cx"] - 5000 * s) < 2 * r and abs(g["cy"] - 5000 * s) < 2 * r
        assert out["hud"]["hidden"] is True
        assert boxes(out, "badge", 1)
    for name in ("at", "just_after", "after"):
        out, g = seen[name]
        assert (g["cx"], g["cy"]) == pytest.approx((6000 * s, 6000 * s)), "one planted marker at the plant"
        assert not boxes(out, "badge")
        assert out["hud"]["hidden"] is False and "to detonation" in out["hud"]["html"]
    assert "45.0 s" in seen["at"][0]["hud"]["html"] and "35.0 s" in seen["after"][0]["hud"]["html"]


@needs_node
def test_the_hud_and_glyph_are_the_same_with_abilities_on_and_off():
    blob = spike_round()
    for t in (PLANT_T - 0.01, PLANT_T, PLANT_T + 3):
        on, off = draw(blob, t), draw(blob, t, layers={"abilities": False})
        assert on["hud"] == off["hud"]
        assert [(g["cx"], g["cy"]) for g in spikes(on)] == [(g["cx"], g["cy"]) for g in spikes(off)]


@needs_node
def test_an_old_round_without_player_state_keeps_the_planted_marker_and_hud():
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000)], util=[BOMB])
    before, after = draw(blob, PLANT_T - 1), draw(blob, PLANT_T + 1, layers={"abilities": False})
    assert spikes(before) == [] and before["hud"]["hidden"] is True
    assert len(spikes(after)) == 1 and after["hud"]["hidden"] is False
    assert any(h["text"].startswith("Spike planted") for h in after["hits"])


@needs_node
def test_a_dropped_spike_with_a_position_is_one_ground_marker_and_without_one_none():
    ps = {"spike": [{"t": 0.0, "s": "carried", "slot": 1}, {"t": 10.0, "s": "dropped", "u": 3000, "v": 3000},
                    {"t": 20.0, "s": "dropped"}]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000)], player_state=ps)
    placed, bare = draw(blob, 15.0), draw(blob, 25.0)
    s = placed["s"]
    assert [(g["cx"], g["cy"]) for g in spikes(placed)] == [pytest.approx((3000 * s, 3000 * s))]
    assert any("dropped" in h["text"].lower() for h in placed["hits"])
    assert spikes(bare) == []
    assert "dropped" in bare["aria"].lower()


@needs_node
def test_an_isolated_player_has_chips_above_health_and_name_below_and_the_badge_upper_right():
    out = draw(spike_round([FLASH]), 10.5)
    s, r = out["s"], out["r"]
    x, y = 5000 * s, 5000 * s
    [block] = boxes(out, "block", 1)
    assert block["moved"] is False
    assert block["y"] == pytest.approx(y + r * 1.25) and block["x"] < x < block["x"] + block["w"]
    [chip] = boxes(out, "chip", 1)
    assert chip["y"] + chip["h"] <= y - r and chip["x"] < x < chip["x"] + chip["w"]
    [badge] = boxes(out, "badge", 1)
    assert badge["x"] > x and badge["y"] + badge["h"] / 2 < y
    assert "BLINDED" in texts(out) and "Name1" in texts(out) and no_percent(out)
    [(bar, band, pct)] = bars(out)
    assert (band, pct) == ("healthy", 60)
    name = first(out, lambda row: row["op"] == "fillText" and row["text"] == "Name1")
    bar_y = out["log"][bar]["y"]
    assert bar_y < out["log"][name]["y"], "the health bar sits between the circle and the name"


@needs_node
def test_health_is_a_coloured_bar_with_no_number_and_no_raw_hp_or_shield():
    ps = {"vitals": {"1": [vit(5.0, 0, 61, 12, 100, 50)]}, "damage_taken": {"1": [5.0]}}
    out = draw(round_blob([(1, "A", "Phoenix", 5000, 5000)], player_state=ps), 6.0)
    assert [b[1:] for b in bars(out)] == [("hurt", 49)] and no_percent(out)   # 100 * 73 / 150 = 48.7
    assert not any(t in ("61", "12", "73", "150") or "HP" in t for t in texts(out))


@needs_node
def test_names_toggle_keeps_the_health_attached():
    blob = spike_round()
    on, off = draw(blob, 20.0), draw(blob, 20.0, layers={"names": False})
    [b_on], [b_off] = boxes(on, "block", 1), boxes(off, "block", 1)
    assert "Name1" in texts(on) and "Name1" not in texts(off)
    assert len(bars(off)) == 1 and b_off["y"] == pytest.approx(b_on["y"]) and b_off["h"] < b_on["h"]
    # an undamaged player with names off has no block at all; with names on just the name
    assert boxes(off, "block", 6) == [] and len(boxes(on, "block", 6)) == 1


@needs_node
def test_the_unlinked_standalone_preview_has_no_names_but_keeps_health():
    out = draw(spike_round([FLASH]), 10.5, linked=None)
    assert not any(t.startswith("Name") for t in texts(out))
    assert len(bars(out)) == 1 and "BLINDED" in texts(out)
    [block] = boxes(out, "block", 1)
    assert block["moved"] is False


@needs_node
def test_conditions_and_health_do_not_depend_on_the_abilities_toggle():
    blob = spike_round([FLASH, NEAR, CONC])
    on, off = draw(blob, 10.5), draw(blob, 10.5, layers={"abilities": False})
    for out in (on, off):
        assert {"BLINDED", "NEARSIGHTED", "CONCUSSED"} <= set(texts(out))
        assert len(bars(out)) == 1 and no_percent(out)
    assert [(b["kind"], b["slot"]) for b in on["layout"]] == [(b["kind"], b["slot"]) for b in off["layout"]]


@needs_node
def test_multiple_conditions_stack_as_separate_chips_without_overlap():
    out = draw(spike_round([FLASH, NEAR, CONC]), 10.5)
    chips = boxes(out, "chip", 1)
    assert len(chips) == 3
    labels = [t for t in texts(out) if t in ("BLINDED", "NEARSIGHTED", "CONCUSSED")]
    assert labels[:2] == ["BLINDED", "NEARSIGHTED"], "blinded first, then nearsighted, then the rest"
    for i, a in enumerate(chips):
        for b in chips[i + 1:]:
            assert not overlap(a, b)
    s = out["s"]
    # the conditions end on their own: [t0, t1)
    later = draw(spike_round([FLASH, NEAR, CONC]), 12.0)
    assert {"BLINDED", "NEARSIGHTED", "CONCUSSED"} & set(texts(later)) == {"NEARSIGHTED"}
    assert s > 0


@needs_node
def test_stacked_players_blocks_avoid_each_other_and_reserved_chips_with_leaders():
    ps = {"vitals": {str(k): [vit(5.0, 0, 75, 0)] for k in (1, 2, 3)}, "damage_taken": {str(k): [5.0] for k in (1, 2, 3)},
          "spike": [{"t": 0.0, "s": "carried", "slot": 2}]}
    flash = {"k": "flash", "t": 9.5, "by": 6, "ability": "phoenix_curveball_left", "hits": [[3, 10.0, 2.0]]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000), (2, "A", "Sova", 5030, 5020), (3, "A", "Jett", 4990, 5300)],
                      util=[flash], player_state=ps)
    out = draw(blob, 10.5)
    blocks = boxes(out, "block")
    assert len(blocks) == 3
    reserved = boxes(out, "chip") + boxes(out, "badge")
    assert reserved
    for i, a in enumerate(blocks):
        for b in blocks[i + 1:]:
            assert not overlap(a, b), (a, b)
        for c in reserved:
            assert not overlap(a, c), (a, c)
    moved = [b for b in blocks if b["moved"]]
    assert moved, "a stacked player's block moves"
    leaders = [row for row in out["log"] if row["op"] == "stroke" and len(row["path"]) == 2
               and row["path"][0][0] == "M" and row["path"][1][0] == "L"]
    assert len(leaders) >= len(moved)
    for b in blocks:
        assert [b[1:] for b in bars(out)] == [("hurt", 50)] * 3
        assert b["x"] <= b["px"] <= b["x"] + b["w"], "the block stays under its player"


@needs_node
def test_a_block_that_would_cover_the_next_players_chips_moves_below_them():
    # slot 3 stands 700 units (about 3.4 marker radii) below slot 1: slot 1's block, in its own place, would
    # cover slot 3's BLINDED chip
    ps = {"vitals": {"1": [vit(5.0, 0, 75, 0)]}, "damage_taken": {"1": [5.0]}}
    flash = {"k": "flash", "t": 9.5, "by": 6, "ability": "phoenix_curveball_left", "hits": [[3, 10.0, 2.0]]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000), (3, "A", "Jett", 5000, 5700)], util=[flash], player_state=ps)
    out = draw(blob, 10.5)
    s, r = out["s"], out["r"]
    [chip] = boxes(out, "chip", 3)
    [block] = boxes(out, "block", 1)
    own = {"x": block["x"], "y": 5000 * s + r * 1.25, "w": block["w"], "h": block["h"]}
    assert overlap(own, chip), "the fixture puts the chip in the block's own place"
    assert not overlap(block, chip) and block["moved"] is True and block["y"] >= chip["y"] + chip["h"]


@needs_node
@pytest.mark.parametrize("view", [{"k": 1, "ox": 0, "oy": 0}, {"k": 2, "ox": 256, "oy": 256}])
def test_annotations_clamp_at_the_map_edges(view):
    ps = {"vitals": {"1": [vit(5.0, 0, 75, 0)], "2": [vit(5.0, 0, 75, 0)]},
          "damage_taken": {"1": [5.0], "2": [5.0]}, "spike": [{"t": 0.0, "s": "carried", "slot": 2}]}
    flash = {"k": "flash", "t": 9.5, "by": 6, "ability": "phoenix_curveball_left", "hits": [[2, 10.0, 2.0]]}
    s = 1024 / 10000
    lo = view["ox"] / s + 30
    hi = (view["ox"] + 1024 / view["k"]) / s - 30
    blob = round_blob([(1, "A", "Phoenix", hi, hi), (2, "A", "Sova", lo, lo)], util=[flash], player_state=ps)
    out = draw(blob, 10.5, view=view)
    x0, y0 = view["ox"], view["oy"]
    x1, y1 = x0 + 1024 / view["k"], y0 + 1024 / view["k"]
    assert {b["kind"] for b in out["layout"]} >= {"block", "chip", "badge"}
    for b in out["layout"]:
        assert x0 - 1e-6 <= b["x"] and b["x"] + b["w"] <= x1 + 1e-6, b
        assert y0 - 1e-6 <= b["y"] and b["y"] + b["h"] <= y1 + 1e-6, b


@needs_node
def test_zoomed_annotations_scale_with_the_marks_and_hit_in_map_coordinates():
    blob = spike_round([FLASH])
    normal, zoomed = draw(blob, 10.5), draw(blob, 10.5, view={"k": 4, "ox": 300, "oy": 300})
    [bn], [bz] = boxes(normal, "block", 1), boxes(zoomed, "block", 1)
    assert bz["h"] / bn["h"] == pytest.approx(zoomed["r"] / normal["r"], rel=0.05)
    centre = [bz["x"] + bz["w"] / 2, bz["y"] + bz["h"] / 2]
    out = draw(blob, 10.5, view={"k": 4, "ox": 300, "oy": 300}, tip=[centre])
    assert "as of the last hit at 0:05" in out["tips"][0]


@needs_node
def test_tooltips_name_health_condition_and_carrier_and_the_click_still_selects():
    blob = spike_round([FLASH])
    probe = draw(blob, 10.5)
    s = probe["s"]
    [block], [chip], [badge] = boxes(probe, "block", 1), boxes(probe, "chip", 1), boxes(probe, "badge", 1)
    mid = lambda b: [b["x"] + b["w"] / 2, b["y"] + b["h"] / 2]  # noqa: E731
    player = [5000 * s, 5000 * s]
    # the block's hit area is its rectangle: a corner inside it hits, a point just below it (still within half
    # its width of its centre) doesn't
    corner = [block["x"] + block["w"] * 0.02, block["y"] + block["h"] * 0.05]
    below = [block["x"] + block["w"] / 2, block["y"] + block["h"] + block["h"] * 0.2]
    out = draw(blob, 10.5, tip=[player, mid(block), mid(chip), mid(badge), corner, below],
               click=[player, mid(block), mid(chip)])
    tip_player, tip_block, tip_chip, tip_badge, tip_corner, tip_below = out["tips"]
    assert "healthy" in tip_player and "%" not in tip_player and "blinded" in tip_player and "carrying the spike" in tip_player
    assert "as of the last hit at 0:05" in tip_block
    assert "Blinded" in tip_chip and "Name6" in tip_chip and "0:10" in tip_chip
    assert "spike" in tip_badge.lower()
    assert "as of the last hit at 0:05" in tip_corner
    assert tip_below is None or "as of the last hit" not in tip_below
    assert out["clicks"][0] == 1, "clicking the player still selects them"
    assert out["clicks"][1:] == ["no selection", "no selection"], "annotations never select or clear a selection"
    assert all(h["slot"] is None for h in out["hits"] if "as of the last hit" in h["text"])


@needs_node
def test_unavailable_health_is_a_neutral_marker_with_a_tooltip():
    ps = {"vitals": {"1": [vit(5.0, 0, 60, None, 100, None)]}, "damage_taken": {"1": [5.0]}}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000)], player_state=ps)
    out = draw(blob, 6.0)
    assert "?" in texts(out) and not any(t.endswith("%") for t in texts(out))
    [block] = boxes(out, "block", 1)
    centre = [block["x"] + block["w"] / 2, block["y"] + block["h"] / 2]
    tipped = draw(blob, 6.0, tip=[centre])
    assert "unknown" in tipped["tips"][0].lower()


@needs_node
def test_death_hides_the_annotations_and_a_revive_resumes_them():
    alive = {"1": [[-10.0, 20.0, "spawn", []], [30.0, None, "revive", []]], "6": [[-10.0, None, "spawn", []]]}
    ps = {"vitals": {"1": [vit(5.0, 0, 60, 20), vit(30.0, 1, 100, 0, 100, 0), vit(32.0, 1, 40, 0, 100, 0)]},
          "damage_taken": {"1": [5.0, 32.0]}, "spike": [{"t": 0.0, "s": "carried", "slot": 1}]}
    flash = {"k": "flash", "t": 18.0, "by": 6, "ability": "phoenix_curveball_left", "hits": [[1, 18.5, 5.0]]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000), (6, "B", "Sova", 2000, 2000)], util=[flash],
                      player_state=ps, alive=alive)
    dead = draw(blob, 21.0)
    assert boxes(dead, "block", 1) == [] and boxes(dead, "chip", 1) == [] and boxes(dead, "badge") == []
    assert not any(t.endswith("%") or t == "BLINDED" for t in texts(dead))
    assert spikes(dead) == [], "a dead carrier's spike is dropped with no position"
    back = draw(blob, 31.0)
    # D10: back at full after the revive is not worth a bar; the name block alone stays
    assert bars(back) == [] and boxes(back, "block", 1)
    hurt = draw(blob, 33.0)
    assert [b[1:] for b in bars(hurt)] == [("hurt", 40)] and no_percent(hurt)


@needs_node
def test_team_knowledge_dimming_applies_to_the_enemy_annotations():
    ps = {"vitals": {"6": [vit(5.0, 0, 70, 20)]}, "damage_taken": {"6": [5.0]}}
    flash = {"k": "flash", "t": 9.5, "by": 1, "ability": "phoenix_curveball_left", "hits": [[6, 10.0, 2.0]]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000), (6, "B", "Sova", 2000, 2000)], util=[flash], player_state=ps)
    out = draw(blob, 10.5, knowing="A")
    rows = [row for row in out["log"] if row["op"] == "fillText" and row["text"] == "BLINDED"]
    rows += [out["log"][b[0]] for b in bars(out)]
    assert len(rows) == 2 and all(row["alpha"] == pytest.approx(0.35) for row in rows)
    own = draw(blob, 10.5, knowing="B")
    rows = [row for row in own["log"] if row["op"] == "fillText" and row["text"] == "BLINDED"]
    rows += [own["log"][b[0]] for b in bars(own)]
    assert len(rows) == 2 and all(row["alpha"] == pytest.approx(1.0) for row in rows), "their own team: not dimmed"


@needs_node
@pytest.mark.parametrize("abilities", [True, False])
def test_a_placed_dropped_spike_paints_over_control_and_under_the_players(abilities):
    ps = {"vitals": {"6": [vit(5.0, 0, 70, 20)]}, "damage_taken": {"6": [5.0]},
          "spike": [{"t": 0.0, "s": "carried", "slot": 1}, {"t": 10.0, "s": "dropped", "u": 3000, "v": 3000}]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000), (6, "B", "Sova", 2000, 2000)], player_state=ps)
    out = draw(blob, 15.0, layers={"abilities": abilities})
    r = out["r"]
    control = first(out, lambda row: row.get("text") == "CONTROL-MARK")
    [drop] = [i for i, row in enumerate(out["log"]) if row.get("spike")]
    circles = [i for i, row in enumerate(out["log"]) if row["op"] == "fill" and len(row["path"]) == 1
               and row["path"][0][0] == "A" and abs(row["path"][0][3] - r) < 1e-6]
    health = bars(out)[0][0]
    assert len(circles) == 2 and control < drop < min(circles) and max(circles) < health
    assert boxes(out, "badge") == [], "a dropped spike has no carrier"


@needs_node
def test_a_revived_player_not_hit_since_shows_the_unavailable_marker_and_no_spike():
    # D9 (2): no vitals in the new life -> the bar stays (the round's damage) as unavailable, never full health;
    # the spike the player carried before dying stays dropped after the revive.
    alive = {"1": [[-10.0, 20.0, "spawn", []], [30.0, None, "revive", []]]}
    ps = {"vitals": {"1": [vit(5.0, 0, 60, 20)]}, "damage_taken": {"1": [5.0]},
          "spike": [{"t": 0.0, "s": "carried", "slot": 1}]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000)], player_state=ps, alive=alive)
    out = draw(blob, 31.0)
    assert "?" in texts(out) and not any(t.endswith("%") for t in texts(out))
    assert boxes(out, "block", 1) and boxes(out, "badge") == [] and spikes(out) == []
    assert "dropped" in out["aria"].lower()
    [block] = boxes(out, "block", 1)
    tipped = draw(blob, 31.0, tip=[[block["x"] + block["w"] / 2, block["y"] + block["h"] / 2]])
    assert "unknown" in tipped["tips"][0].lower() and "not hit in this life" in tipped["tips"][0]


@needs_node
@pytest.mark.parametrize("abilities", [True, False])
def test_a_cancelled_plant_keeps_the_one_carrier_badge_and_no_hud(abilities):
    ps = {"spike": [{"t": 0.0, "s": "carried", "slot": 1}, {"t": 30.0, "s": "planting", "slot": 1},
                    {"t": 32.0, "s": "carried", "slot": 1}]}
    blob = round_blob([(1, "A", "Phoenix", 5000, 5000)], player_state=ps)
    for t in (29.0, 31.0, 33.0):
        out = draw(blob, t, layers={"abilities": abilities})
        assert len(spikes(out)) == 1 and len(boxes(out, "badge", 1)) == 1, t
        assert out["hud"]["hidden"] is True
    planting = draw(blob, 31.0, tip=[[5000 * 1024 / 10000, 5000 * 1024 / 10000]])
    assert "planting the spike" in planting["tips"][0]

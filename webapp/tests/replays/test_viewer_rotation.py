"""Display-only quarter turns, pointer inverses, opaque fitting and a pinned zero-turn call log."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
REPLAY_JS = WEBAPP / "app/static/js/replay.js"
RECORDER = Path(__file__).with_name("viewer_recorder.js")
GOLDEN = WEBAPP / "tests/fixtures/replay/rotation0_calls.json"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(body, payload=None):
    script = """
      const H = require(process.argv[2]), R = H.environment(process.argv[1]);
      let input = ''; process.stdin.on('data', d => input += d).on('end', () => {
        const p = JSON.parse(input);
        const result = (() => { BODY })();
        process.stdout.write(JSON.stringify(result));
      });
    """.replace("BODY", body)
    done = subprocess.run([NODE, "-e", script, str(REPLAY_JS), str(RECORDER)], input=json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_quarter_turn_values_and_inverse_including_negative_turns():
    got = node("""
      const points = [[0, 0], [1024, 0], [10, 700], [512, 512], [1, 0]];
      return Array.from({length: 13}, (_, i) => i - 6).map(k => points.map(([x, y]) => {
        const d = R.rotatePoint(x, y, 1024, k);
        return {d, back: R.unrotatePoint(d.x, d.y, 1024, k)};
      }));
    """)
    for k, rows in zip(range(-6, 7), got):
        for (x, y), row in zip([(0, 0), (1024, 0), (10, 700), (512, 512), (1, 0)], rows):
            expected = [(x, y), (1024 - y, x), (1024 - x, 1024 - y), (y, 1024 - x)][k % 4]
            assert row == {"d": dict(zip(("x", "y"), expected)), "back": {"x": x, "y": y}}


def test_yaw_is_not_wrapped():
    assert node("return [R.rotateYaw(0, 1), R.rotateYaw(350, 1), R.rotateYaw(20, 3)];") == [90, 440, 290]


@pytest.mark.parametrize("view", [{"k": 1, "ox": 0, "oy": 0}, {"k": 2.5, "ox": 23, "oy": 81},
                                      {"k": 0.75, "ox": -10, "oy": 300}])
def test_view_matrix_zero_is_exact_and_other_turns_map_to_display(view):
    got = node("""
      return [0, 1, 2, 3, -1, 5].map(k => {
        const m = R.viewMatrix(p, k, 1024), q = R.rotatePoint(10, 700, 1024, k);
        return {m, actual: [m[0]*10 + m[2]*700 + m[4], m[1]*10 + m[3]*700 + m[5]],
          expected: [(q.x - p.ox)*p.k, (q.y - p.oy)*p.k]};
      });
    """, view)
    k, ox, oy = view["k"], view["ox"], view["oy"]
    assert got[0]["m"] == [k, 0, 0, k, -ox * k, -oy * k]
    for row in got:
        assert row["actual"] == row["expected"]


@pytest.mark.parametrize("rotation,expected", [(None, 0), (0, 0), (1, 1), (7, 3), (-1, 3), (-4, 0)])
def test_constructor_normalizes_turns(rotation, expected):
    assert node("""
      global.Image = class {};
      global.requestAnimationFrame = () => {};
      const proto = R.ReplayViewer.prototype;
      proto.bindControls = proto.bindHeatmap = proto.loadBulletMask = function () {};
      const canvas = {getContext: () => H.mockCtx([])};
      const root = {classList: {contains: () => false},
        querySelector: key => key === '[data-replay-canvas]' ? canvas : null};
      return new R.ReplayViewer(root, {rotation: p, rounds: []}).turns;
    """, rotation) == expected


@pytest.mark.parametrize("rotation", [None, 0])
def test_zero_rotation_canvas_calls_match_pinned_base(rotation):
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = node("return H.recordRounds(process.argv[1], p === null ? undefined : p);", rotation)
    assert expected["base"] == "b9c958f6"
    assert got == expected["rounds"]
    # Keep the safety net broad: these paths must remain represented when fixtures are maintained.
    calls = [call for row in got for call in row["calls"]]
    assert {"setTransform", "save", "restore", "clip", "drawImage", "measureText", "setLineDash",
            "fillText", "strokeText", "createRadialGradient", "addColorStop"} <= {c["op"] for c in calls}
    texts = [c["args"][0] for c in calls if c["op"] == "fillText"]
    assert {"So", "Test area", "Test choke", "approximate"} <= set(texts)
    assert {"chip", "block", "badge"} <= {box["kind"] for row in got for box in row["layout"]}
    images = [c["args"][0]["image"] for c in calls if c["op"] == "drawImage"]
    assert {"minimap", "agent-omen", "areas", "control-states", "control-unknown"} <= set(images)
    assert any("Dark Cover" in h["text"] for row in got for h in row["hits"])
    assert any("Spycam" in h["text"] for row in got for h in row["hits"])
    assert any("killed" in h["text"] for row in got for h in row["hits"])
    assert any(c["op"] == "lineTo" and c["args"] == [4200 * (1024 / 10000), 3600 * (1024 / 10000)]
               for c in calls), "the tracer has a clear path with no low box"


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_map_layers_use_the_display_matrix_and_approximate_text_stays_on_screen(turns):
    got = node("""
      return H.recordRounds(process.argv[1], p, 1).map((round, i) => {
        const view = i % 2 ? {k: 1.6, ox: 80, oy: 120} : {k: 1, ox: 0, oy: 0};
        return {m: R.viewMatrix(view, p, 1024),
          images: round.calls.filter(c => c.op === 'drawImage' && c.args[3] === 1024 && c.args[4] === 1024).map(c => c.tf),
          approximate: round.calls.filter(c => c.op === 'fillText' && c.args[0] === 'approximate').map(c => c.tf)};
      });
    """, turns)
    for row in got:
        assert len(row["images"]) >= 5
        assert all(matrix == row["m"] for matrix in row["images"])
        assert row["approximate"] == [[1, 0, 0, 1, 0, 0]]


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_fit_opaque_top_left_block_in_display_frame(turns):
    got = node("""
      const pixels = new Uint8ClampedArray(256*256*4);
      for (let y = 16; y <= 80; y++) for (let x = 8; x <= 56; x++) pixels[(y*256+x)*4+3] = 255;
      global.document = {createElement: () => ({getContext: () => ({drawImage() {},
        getImageData: () => ({data: pixels})})})};
      const {v} = H.makeViewer(R, undefined, p);
      v.fitView(); return v.view;
    """, turns)
    corners = [(8, 16), (56, 80)]
    rotated = [[(x, y), (256 - y, x), (256 - x, 256 - y), (y, 256 - x)][turns] for x, y in corners]
    side = 64 + 2 * 0.04 * 256
    cx, cy = [sum(point[axis] for point in rotated) / 2 for axis in (0, 1)]
    assert got == {"k": min(3, 256 / side), "ox": max(0, cx - side / 2) * 4,
                   "oy": max(0, cy - side / 2) * 4}
    if turns == 1:
        assert got["ox"] > 512 and got["oy"] == 0


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_hover_legacy_tooltip_and_click_find_map_player(turns):
    got = node("""
      const {v} = H.makeViewer(R, undefined, p, {k: 1.3, ox: 30, oy: 40});
      v.control = {}; v.drawControl = () => {}; v.draw();
      const h = v.hits.find(h => h.slot === 1), q = R.rotatePoint(h.x, h.y, 1024, p);
      const m = R.viewMatrix(v.view, p, 1024);
      const px = m[0]*h.x + m[2]*h.y + m[4], py = m[1]*h.x + m[3]*h.y + m[5];
      const event = {clientX: 12 + px/2, clientY: 20 + py/2};
      v.onHover(event);
      const hover = Object.assign({}, v.hover), text = v.ui.tip.textContent, hidden = v.ui.tip.hidden;
      v.showTip({x: q.x, y: q.y, px: px/2, py: py/2});
      const legacy = v.ui.tip.hidden ? null : v.ui.tip.textContent;
      let picked = null; v.setHighlight = slot => { picked = slot; }; v.onCanvasClick(event);
      return {hover, text, hidden, legacy, picked, h, q};
    """, turns)
    assert got["hidden"] is False and "Omen" in got["text"]
    assert got["legacy"] == got["text"]
    assert got["picked"] == 1
    for key, expected in [("x", got["q"]["x"]), ("y", got["q"]["y"]),
                          ("mx", got["h"]["x"]), ("my", got["h"]["y"])]:
        assert got["hover"][key] == pytest.approx(expected)


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_display_and_map_frame_box_and_circle_hits(turns):
    assert node("""
      const {v} = H.makeViewer(R, undefined, p), q = R.rotatePoint(100, 220, 1024, p);
      const hover = {x: q.x, y: q.y, px: 1, py: 2};
      return [false, true].flatMap(disp => [false, true].map(box => {
        const x = disp ? q.x : 100, y = disp ? q.y : 220;
        v.hits = [Object.assign({x, y, r: 5, disp, text: 'hit'}, box ? {box: {x:x-3,y:y-3,w:6,h:6}} : {})];
        v.showTip(hover); return !v.ui.tip.hidden && v.ui.tip.textContent === 'hit';
      }));
    """, turns) == [True] * 4


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_heatmap_paint_and_tooltip_use_the_same_cell(turns):
    got = node("""
      const {v} = H.makeViewer(R, undefined, p, {k: 1.4, ox: 50, oy: 60}), log = [];
      const C = global.ReplayControl, cell = 35*C.GRID+19, index = new Int32Array(C.GRID*C.GRID).fill(-1);
      index[cell] = 0;
      const section = {key: 'all', label: 'All rounds', seconds: 10, rounds: 1,
        x: [255], y: [0], contested: [0]};
      const data = {index, walk: [cell], labels: {x: 'Attack', y: 'Defence'}, sections: [section], skipped: []};
      v.heat = {shown: 'all', data: {all: data}, section: {value: 'all'}, mode: {value: 'lead'},
        canvas: v.canvas, ctx: H.mockCtx(log), tip: H.tip(), legend: {}};
      v.controlCanvas = () => ({canvas: {recordingId: 'heat'}, ctx: {
        createImageData: (w,h) => ({data: new Uint8ClampedArray(w*h*4)}), putImageData() {}}});
      v.paintHeatmap();
      const q = R.rotatePoint(19*8+4, 35*8+4, 1024, p), m = R.viewMatrix(v.view, p, 1024);
      v.heatTip({clientX: 12+(q.x-v.view.ox)*v.view.k/2, clientY: 20+(q.y-v.view.oy)*v.view.k/2});
      const text = v.heat.tip.textContent, hidden = v.heat.tip.hidden;
      v.heatTip({clientX: -5000, clientY: -5000});
      return {text, hidden, outside: v.heat.tip.hidden, m,
        images: log.filter(c => c.op === 'drawImage').map(c => c.tf)};
    """, turns)
    assert got["hidden"] is False and "Attack 100%" in got["text"] and "Defence 0%" in got["text"]
    assert got["outside"] is True
    assert got["images"] == [got["m"], got["m"]]


@pytest.mark.parametrize("turns", [1, 3])
def test_text_and_icons_are_upright_and_agent_icons_follow_the_player(turns):
    rounds = node("return H.recordRounds(process.argv[1], p);", turns)
    for i, row in enumerate(rounds):
        labels = [c for c in row["calls"] if c["op"] in ("fillText", "strokeText")]
        images = [c for c in row["calls"] if c["op"] == "drawImage"]
        icons = [c for c in images if c["args"][3:] != [1024, 1024]]
        assert labels and icons
        for call in labels + icons:
            assert call["tf"][1:3] == [0, 0], call
        assert next(c for c in images if c["args"][0] == {"image": "minimap"})["tf"][1] != 0
        [icon] = [c for c in icons if c["args"][0] == {"image": "agent-omen"}]
        _, x, y, w, h = icon["args"]
        mx, my = (2400 + i * 300) * 1024 / 10000, (3600 + i * 200) * 1024 / 10000
        expected = [(1024 - my, mx), (my, 1024 - mx)][turns == 3]
        assert (x + w / 2, y + h / 2) == pytest.approx(expected)


@pytest.mark.parametrize("turns", [0, 1, 3])
def test_upright_restores_the_callers_transform_even_on_error(turns):
    got = node("""
      const {v, log} = H.makeViewer(R, undefined, p);
      v.ctx.setTransform(2, 0, 0, 2, 9, 17);
      try { v.upright(v.ctx, 100, 220, (x,y) => {
        v.ctx.fillText('label', x, y); throw new Error('test');
      }); } catch (e) { if (e.message !== 'test') throw e; }
      v.ctx.fillRect(1,2,3,4); return log;
    """, turns)
    assert got[-1]["tf"] == [2, 0, 0, 2, 9, 17]
    [text] = [c for c in got if c["op"] == "fillText"]
    expected = [(100, 220), (804, 100), (220, 924)][[0, 1, 3].index(turns)]
    assert text["args"][1:] == list(expected)
    if turns == 0:
        assert [c["op"] for c in got] == ["setTransform", "fillText", "fillRect"]
    else:
        assert text["tf"] == [1, 0, 0, 1, 0, 0]


def test_ability_badge_glyph_and_timer_are_upright_at_their_map_anchor():
    got = node("""
      const {v, log} = H.makeViewer(R, undefined, 1);
      v.ctx.setTransform(...R.viewMatrix(v.view, 1, 1024));
      const glyph = {recordingId: 'ability'};
      v.drawBadge(v.ctx, 100, 220, 12, 'red', glyph, 1);
      v.drawGlyph(v.ctx, 100, 220, 20, glyph, 1);
      v.drawTimer(v.ctx, 100, 220, 18, 0.5, 3, 1);
      v.ctx.fillRect(1,2,3,4); return log;
    """)
    images = [c for c in got if c["op"] == "drawImage"]
    arcs = [c for c in got if c["op"] == "arc"]
    assert len(images) == 2 and len(arcs) == 3
    for call in images + arcs:
        assert call["tf"] == [1, 0, 0, 1, 0, 0]
        if call["op"] == "arc":
            assert call["args"][:2] == [804, 100]
        else:
            _, x, y, w, h = call["args"]
            assert (x + w / 2, y + h / 2) == pytest.approx((804, 100))
    assert arcs[-1]["args"][3] == pytest.approx(-3.141592653589793 / 2)
    assert got[-1]["tf"][1] != 0


ANNOTATIONS = """
  const blob = H.simpleBlob(), slots = p.stacked || p.edge ? [1, 2] : [1];
  blob.players = slots.map(slot => ({slot, side: 'A', agent: 'Omen'}));
  blob.tracks = {}; blob.alive = {};
  slots.forEach((slot, i) => {
    let point = {x: 240 + i*2, y: 360 + i*2};
    if (p.edge) {
      const lo = p.view.ox + 3, hi = p.view.ox + 1024/p.view.k - 3;
      point = R.unrotatePoint(i ? lo : hi, i ? lo : hi, 1024, p.turns);
    }
    blob.tracks[slot] = [{t0: 0, u: [point.x*10000/1024, 0], v: [point.y*10000/1024, 0], yaw: [0, 0]}];
    blob.alive[slot] = [[0, null, 'spawn', []]];
  });
  blob.player_state = {version: 1,
    vitals: Object.fromEntries(slots.map(s => [s, [{t: 5, life: 0, hp: 70, sh: 20, mhp: 100, msh: 50}]])),
    damage_taken: Object.fromEntries(slots.map(s => [s, [5]])), spike: [{t: 0, s: 'carried', slot: 1}]};
  blob.util = [{k: 'status', by: 6, target: 1, status: 'concussed', t: 9, t1: 12, code: 'Iris', name: 'Concuss'}];
  if (p.spike === 'dropped') blob.player_state.spike.push({t: 8, s: 'dropped', u: 3500, v: 5000});
  if (p.spike === 'planted') blob.util.push({k: 'ability', kind: 'Bomb', t: 8, t1: null, by: 1,
    u: 6000, v: 6000, defuses: [[9, 11, 1, false]]});
  const {v, log} = H.makeViewer(R, blob, p.turns, p.view);
  v.linked = {players: Object.fromEntries(slots.map(s => [s, {name: 'Player' + s, team: 'team-1'}]))};
  v.options.agentIcon = () => null; v.draw();
  const tips = v.layout.map(b => {
    const x = b.x+b.w/2, y = b.y+b.h/2;
    v.onHover({clientX: 12+(x-v.view.ox)*v.view.k/2, clientY: 20+(y-v.view.oy)*v.view.k/2});
    return {kind: b.kind, hidden: v.ui.tip.hidden, text: v.ui.tip.textContent};
  });
  let path = [], spikes = [], arcs = [], lines = [];
  log.forEach(c => {
    if (c.op === 'beginPath') path = [];
    if (c.op === 'moveTo' || c.op === 'lineTo') path.push(c.args);
    if (c.op === 'fill' && path.length === 4 && c.style.fillStyle === '#ff4655')
      spikes.push({tf: c.tf, x: (path[0][0]+path[2][0])/2, y: (path[0][1]+path[2][1])/2});
    if (c.op === 'arc') arcs.push(c);
    if (c.op === 'stroke' && path.length === 2) lines.push({tf: c.tf, path});
  });
  return {layout: v.layout, hits: v.hits, tips, spikes, arcs, lines, bounds: v.viewBounds(), r: 1024/48/Math.sqrt(v.view.k)};
"""


def annotation_scene(turns, **kw):
    return node(ANNOTATIONS, dict({"turns": turns, "view": {"k": 1, "ox": 0, "oy": 0}}, **kw))


@pytest.mark.parametrize("turns", [1, 3])
def test_annotation_chips_blocks_and_badge_follow_display_directions(turns):
    got = annotation_scene(turns)
    x, y = (664, 240) if turns == 1 else (360, 784)
    boxes = {b["kind"]: b for b in got["layout"]}
    assert set(boxes) == {"chip", "block", "badge"}
    block, chip, badge = [boxes[k] for k in ("block", "chip", "badge")]
    assert block["moved"] is False
    assert block["y"] == pytest.approx(y + got["r"] * 1.25)
    assert block["x"] < x < block["x"] + block["w"]
    assert chip["y"] + chip["h"] <= y - got["r"] and chip["x"] < x < chip["x"] + chip["w"]
    assert badge["x"] > x and badge["y"] + badge["h"] / 2 < y
    for box in boxes.values():
        assert (box["px"], box["py"]) == pytest.approx((x, y))
    assert all(h.get("disp") is True for h in got["hits"] if "box" in h)


@pytest.mark.parametrize("view", [{"k": 1, "ox": 0, "oy": 0}, {"k": 2, "ox": 256, "oy": 256}])
def test_rotated_annotations_clamp_to_display_view(view):
    got = annotation_scene(1, edge=True, view=view)
    bounds = got["bounds"]
    assert {b["kind"] for b in got["layout"]} == {"chip", "block", "badge"}
    for b in got["layout"]:
        assert bounds["x0"] - 1e-6 <= b["x"] and b["x"] + b["w"] <= bounds["x1"] + 1e-6
        assert bounds["y0"] - 1e-6 <= b["y"] and b["y"] + b["h"] <= bounds["y1"] + 1e-6


def test_half_turn_stacked_blocks_avoid_each_other_and_reserved_chips():
    got = annotation_scene(2, stacked=True)
    boxes = got["layout"]
    blocks = [b for b in boxes if b["kind"] == "block"]
    assert len(blocks) == 2 and any(b["moved"] for b in blocks)
    for a in blocks:
        assert a["y"] > a["py"]
        for b in boxes:
            if a is b:
                continue
            assert not (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
                        and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])
    assert len(got["lines"]) >= sum(b["moved"] for b in blocks)


def test_rotated_annotation_hover_finds_health_condition_and_carrier():
    got = annotation_scene(1, view={"k": 1.3, "ox": 30, "oy": 40})
    tips = {t["kind"]: t for t in got["tips"]}
    assert all(not t["hidden"] for t in tips.values())
    assert "as of the last hit at 0:05" in tips["block"]["text"]
    assert "Concussed" in tips["chip"]["text"]
    assert "carrying the spike" in tips["badge"]["text"]


@pytest.mark.parametrize("turns", [1, 3])
def test_gap_label_plate_and_text_share_upright_display_offsets(turns):
    rounds = node("return H.recordRounds(process.argv[1], p);", turns)
    for row in rounds:
        calls = row["calls"]
        [index] = [i for i, c in enumerate(calls) if c["op"] == "fillText" and c["args"][0] == "Test choke"]
        text = calls[index]
        plate = next(c for c in reversed(calls[:index]) if c["op"] == "fillRect")
        assert text["tf"] == plate["tf"] and text["tf"][1:3] == [0, 0]
        x, y, w, h = plate["args"]
        assert x <= text["args"][1] <= x + w and y <= text["args"][2] <= y + h


@pytest.mark.parametrize("turns", [1, 3])
@pytest.mark.parametrize("state", ["carried", "dropped", "planted"])
def test_spike_glyphs_and_planted_timers_are_upright_once(turns, state):
    got = annotation_scene(turns, spike=state)
    [spike] = got["spikes"]
    assert spike["tf"][1:3] == [0, 0]
    if state == "carried":
        [badge] = [b for b in got["layout"] if b["kind"] == "badge"]
        expected = (badge["x"] + badge["w"] / 2, badge["y"] + badge["h"] / 2)
    else:
        mx, my = (3500 * 1024 / 10000, 512) if state == "dropped" else (614.4, 614.4)
        expected = (1024 - my, mx) if turns == 1 else (my, 1024 - mx)
    assert (spike["x"], spike["y"]) == pytest.approx(expected)
    if state == "planted":
        # Detonation plus defuse under-ring and progress ring, in their original paint order.
        timers = [c for c in got["arcs"] if c["args"][2] > got["r"] * 0.7 * 1.8
                  and c["args"][2] < got["r"] * 0.7 * 2.8
                  and c["args"][:2] == pytest.approx(expected)]
        assert len(timers) == 4  # also includes the planted pulse
        assert all(c["tf"][1:3] == [0, 0] for c in timers)
        assert len([c for c in timers if c["args"][3] == -3.141592653589793 / 2]) == 2
        assert any(line["tf"][1] != 0 for line in got["lines"]), "defuser line stays in map frame"

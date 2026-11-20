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
    assert expected["base"] == "54e44c74"
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
      return H.recordRounds(process.argv[1], p).map((round, i) => {
        const view = i % 2 ? {k: 1.6, ox: 80, oy: 120} : {k: 1, ox: 0, oy: 0};
        return {m: R.viewMatrix(view, p, 1024),
          images: round.calls.filter(c => c.op === 'drawImage').map(c => c.tf),
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

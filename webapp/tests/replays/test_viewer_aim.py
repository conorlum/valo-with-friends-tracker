"""Display-only aim lines and tracers share sight walls, passing over low boxes."""

import json

import pytest
from jinja2 import Environment, FileSystemLoader

from test_viewer_rotation import GOLDEN, NODE, WEBAPP, node

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

DRAW = """
  const blob = H.simpleBlob();
  blob.players = [{slot: 1, side: 'B', agent: 'Omen'}];
  blob.tracks = {'1': [{t0: 0, u: [2000, 0], v: [2000, 0], yaw: [p.yaw || 0, 0]}]};
  blob.alive = {'1': [[0, p.dead ? 5 : null, 'spawn', []]]};
  if (p.stale) blob.tracks['1'] = [{t0: 0, u: [2000], v: [2000], yaw: [0]}];
  const {v, log} = H.makeViewer(R, blob, p.turns || 0, {k: p.zoom || 1, ox: 0, oy: 0});
  v.t = p.stale ? 0.1 : 10.1;
  Object.keys(v.layers).forEach(k => {v.layers[k] = false;});
  v.layers.aim = p.aim !== false;
  if (p.mask) {
    const n = 1024, mask = new Uint8Array(n*n), bullet = new Uint8Array(n*n);
    for (let y = 0; y < n; y++) mask[y*n + 400] = bullet[y*n + 400] = 1;
    if (p.box) for (let y = 190; y < 220; y++) for (let x = 280; x < 300; x++) bullet[y*n+x] = 1;
    v.sight = {mask, size: n}; v.bullet = {mask: bullet, size: n};
  }
  if (p.dim) {
    v.layers.control = true; v.controlView = 'A';
    v.controlCache = {ready: () => ({parsed: {knew_a: true}})};
  }
  if (p.tracer) {
    v.layers.aim = false; v.layers.tracers = true;
    v.current.extras.shots = [{slot: 1, t: 10, u: 2000, v: 2000, u1: 3500, v1: 2000}];
  }
  v.draw();
  const strokes = log.flatMap((c, i) => c.op === 'stroke' && log[i-1].op === 'lineTo' &&
    log[i-2].op === 'moveTo' ? [{from: log[i-2].args, to: log[i-1].args,
      style: c.style, tf: c.tf, i}] : []);
  return {strokes, log, color: v.slotColor(1, 'B'), r: 1024 / 48 / Math.sqrt(v.view.k)};
"""


@needs_node
@pytest.mark.parametrize("box", [False, True])
@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_aim_reaches_sight_wall_in_map_frame_and_passes_low_boxes(box, turns):
    got = node(DRAW, {"mask": True, "box": box, "turns": turns})
    assert len(got["strokes"]) == 1
    stroke = got["strokes"][0]
    assert stroke["from"] == [204.8, 204.8]
    assert stroke["to"] == pytest.approx([400, 204.8], abs=1)
    assert stroke["style"]["globalAlpha"] == 0.8
    assert stroke["style"]["strokeStyle"] == got["color"]
    assert stroke["style"]["lineWidth"] == pytest.approx(got["r"] / 10)
    expected_tf = [[1, 0, 0, 1, 0, 0], [0, 1, -1, 0, 1024, 0],
                   [-1, 0, 0, -1, 1024, 1024], [0, -1, 1, 0, 0, 1024]][turns]
    assert stroke["tf"] == expected_tf
    # The aim stroke precedes the player's filled circle.
    circle = next(i for i, c in enumerate(got["log"]) if c["op"] == "arc" and c["args"][2] == got["r"])
    assert stroke["i"] < circle


@needs_node
@pytest.mark.parametrize("yaw,zoom,direction", [(0, 1, (1, 0)), (90, 1, (0, 1)), (180, 9, (-1, 0))])
def test_without_mask_aim_is_three_cone_radii_along_yaw(yaw, zoom, direction):
    got = node(DRAW, {"yaw": yaw, "zoom": zoom})
    assert len(got["strokes"]) == 1
    stroke = got["strokes"][0]
    length = got["r"] * 2.4 * 3
    assert stroke["to"] == pytest.approx([204.8 + length * direction[0], 204.8 + length * direction[1]])
    assert stroke["style"]["lineWidth"] == pytest.approx(max(1, got["r"] / 10))


@needs_node
@pytest.mark.parametrize("options", [{"dead": True}, {"stale": True}, {"aim": False}])
def test_aim_is_hidden_without_a_living_live_track_or_when_disabled(options):
    assert node(DRAW, options)["strokes"] == []


@needs_node
def test_enemy_aim_uses_the_knowledge_dim():
    got = node(DRAW, {"dim": True})
    assert len(got["strokes"]) == 1
    assert got["strokes"][0]["style"]["globalAlpha"] == pytest.approx(0.8 * 0.35)


@needs_node
def test_tracer_passes_low_box_and_stops_at_sight_wall():
    got = node(DRAW, {"mask": True, "box": True, "tracer": True})
    assert len(got["strokes"]) == 1
    assert got["strokes"][0]["to"] == pytest.approx([400, 204.8], abs=1)


@needs_node
def test_sight_mask_loader_decodes_red_and_clears_failed_loads():
    got = node("""
      const pixels = new Uint8ClampedArray(1024*1024*4);
      pixels[0] = 127; pixels[4] = 128; pixels[8] = 255;
      let image, redraws = 0;
      global.Image = class {constructor() {image = this;}};
      global.document = {createElement: () => ({getContext: () => ({drawImage() {},
        getImageData: () => ({data: pixels})})})};
      const v = Object.create(R.ReplayViewer.prototype);
      v.options = {sightMask: 'synthetic.sight.png'}; v.draw = () => {redraws++;};
      v.loadSightMask(); const loading = v.sight, src = image.src;
      image.onload(); const decoded = {size: v.sight.size, mask: Array.from(v.sight.mask.slice(0, 3))};
      image.onerror(); const error = v.sight;
      global.document.createElement = () => {throw Error('unreadable');};
      image.onload(); const unreadable = v.sight;
      v.options = {}; v.sight = {}; v.loadSightMask();
      return {loading, src, decoded, redraws, error, unreadable, absent: v.sight};
    """)
    assert got == {"loading": None, "src": "synthetic.sight.png", "decoded": {"size": 1024, "mask": [0, 1, 1]},
                   "redraws": 1, "error": None, "unreadable": None, "absent": None}


@needs_node
def test_aim_defaults_on_and_follows_cones_in_layer_binding():
    got = node("""
      global.Image = class {}; global.requestAnimationFrame = () => {};
      const proto = R.ReplayViewer.prototype;
      proto.bindControls = proto.bindHeatmap = function () {};
      const canvas = {getContext: () => H.mockCtx([])};
      const root = {classList: {contains: () => false},
        querySelector: key => key === '[data-replay-canvas]' ? canvas : null};
      const v = new R.ReplayViewer(root, {rounds: []});
      return {aim: v.layers.aim, next: R.LAYERS[R.LAYERS.indexOf('cones') + 1]};
    """)
    assert got == {"aim": True, "next": "aim"}


@needs_node
def test_aim_off_preserves_the_pinned_zero_rotation_calls():
    assert node("return H.recordRounds(process.argv[1], 0);") == json.loads(GOLDEN.read_text(encoding="utf-8"))["rounds"]


def test_linked_page_offers_aim_and_loads_only_sight_mask():
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app/templates")))
    html = env.get_template("replays/replay.html").render(
        replay={"map_name": "Split", "asset": "Split"}, replay_data={"match": {"layout": {"state": "ok"}}},
        linked=True, control=None, gaps=False, demo_mode=False,
        start_round=1, start_t=None, static_version=lambda: "test")
    assert 'data-replay-layer="aim" checked> Aim lines' in html
    assert html.index('data-replay-layer="cones"') < html.index('data-replay-layer="aim"')
    assert 'sightMask:' in html and '.sight.png?v=test' in html
    assert 'bullet.png' not in html and 'bulletMask' not in html

"""Map control in the viewer (docs/map-control-stages-4-7-impl.md, S4.2-S5.2): the JS decoder in
app/static/js/replay_control.js matches the reference decoder in app/replays/control_format.py,
and its painters draw what the plan says. The JS checks need Node; they skip without it."""

import base64
import gzip
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))

from test_control_format import round_control  # noqa: E402,F401  (fixture)

from app.control.encode import encode_data  # noqa: E402
from app.replays import control_format as cf  # noqa: E402

CONTROL_JS = WEBAPP / "app" / "static" / "js" / "replay_control.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

READ_STDIN = """
  const C = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const payload = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(payload)));
  });
"""


def run_node(body: str, payload, js: Path = CONTROL_JS):
    completed = subprocess.run([NODE, "-e", READ_STDIN + body, str(js)], input=json.dumps(payload),
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def stored(round_control):
    rc, blob = round_control
    data = encode_data(rc, blob)
    header, streams = cf.unpack_data(data)
    ticks, cells = len(header["ticks"]), header["cells"]
    checkpoints = [c[0] for c in header["checkpoints"]]
    return {"raw": base64.b64encode(gzip.decompress(data)).decode("ascii"), "header": header, "ticks": ticks,
            "states": cf.decode_states(streams["states"], ticks, cells),
            "coverage": cf.decode_masks(streams["coverage"], ticks, cells, checkpoints),
            "control": cf.decode_masks(streams["control"], ticks, cells, checkpoints)}


DECODE = """
  function run(p) {
    const parsed = C.parse(C.base64Bytes(p.raw));
    const cur = new C.Cursor(parsed);
    const out = {walk: Array.from(parsed.walk), times: parsed.times, states: {}, coverage: {}, control: {}};
    for (const i of p.order) {
      out.states[i] = Array.from(cur.states(i));
      for (const name of ["coverage", "control"]) {
        out[name][i] = {};
        for (const slot of p.slots) out[name][i][slot] = Array.from(cur.mask(name, slot, i));
      }
    }
    return out;
  }
"""


def _check(stored, got, order, slots):
    for i in order:
        assert got["states"][str(i)] == stored["states"][i], f"states at tick {i}"
        for name in ("coverage", "control"):
            for slot in slots:
                assert got[name][str(i)][str(slot)] == stored[name][i][slot], f"{name} slot {slot} at tick {i}"


def test_the_js_decoder_matches_the_reference_in_order(stored):
    order, slots = list(range(stored["ticks"])), [0, 1, 5, 6]
    got = run_node(DECODE, {"raw": stored["raw"], "order": order, "slots": slots})
    assert got["walk"] == cf.walk_bitmap(stored["header"])
    assert got["times"] == [u / cf.GRID_HZ for u in stored["header"]["ticks"]]
    _check(stored, got, order, slots)


def test_the_js_decoder_seeks_in_any_order(stored):
    order = list(range(stored["ticks"]))
    random.Random(7).shuffle(order)
    order += [0, stored["ticks"] - 1, 0]
    got = run_node(DECODE, {"raw": stored["raw"], "order": order, "slots": [1, 6]})
    _check(stored, got, order, [1, 6])


def test_the_js_decoder_reads_what_each_team_knew(stored):
    _, streams = cf.unpack_data(gzip.compress(base64.b64decode(stored["raw"])))
    expected = {name: cf.decode_knew(streams[name], stored["states"]) for name in ("knew_a", "knew_b")}
    order = list(range(stored["ticks"]))
    shuffled = order[:]
    random.Random(3).shuffle(shuffled)
    body = """
      function run(p) {
        const parsed = C.parse(C.base64Bytes(p.raw)), cur = new C.Cursor(parsed), out = {knew_a: {}, knew_b: {}};
        for (const i of p.order) for (const name of ["knew_a", "knew_b"]) out[name][i] = Array.from(cur.knew(name, i));
        out.none = cur.knew("knew_c", 0);
        return out;
      }"""
    for seq in (order, shuffled):
        got = run_node(body, {"raw": stored["raw"], "order": seq})
        for name in ("knew_a", "knew_b"):
            for i in seq:
                assert got[name][str(i)] == expected[name][i], f"{name} tick {i}"
        assert got["none"] is None


def test_the_js_decoder_reads_each_teams_unknown(stored):
    _, streams = cf.unpack_data(gzip.compress(base64.b64decode(stored["raw"])))
    checkpoints = [c[0] for c in stored["header"]["checkpoints"]]
    expected = {name: cf.decode_unknown(streams[name], stored["ticks"], stored["header"]["cells"], checkpoints)
                for name in ("unknown_a", "unknown_b")}
    order = list(range(stored["ticks"]))
    shuffled = order[:]
    random.Random(5).shuffle(shuffled)
    body = """
      function run(p) {
        const parsed = C.parse(C.base64Bytes(p.raw)), cur = new C.Cursor(parsed), out = {unknown_a: {}, unknown_b: {}};
        for (const i of p.order) for (const name of ["unknown_a", "unknown_b"]) out[name][i] = Array.from(cur.unknown(name, i));
        out.none = cur.unknown("unknown_c", 0);
        return out;
      }"""
    for seq in (order, shuffled):
        got = run_node(body, {"raw": stored["raw"], "order": seq})
        for name in ("unknown_a", "unknown_b"):
            for i in seq:
                assert got[name][str(i)] == expected[name][i], f"{name} tick {i}"
        assert got["none"] is None


def test_unknown_is_hatched_in_the_enemys_colour_and_crosshatched_where_both():
    body = """
      function run(p) {
        const size = 512, rgba = new Uint8Array(size * size * 4), colors = {a: [200, 10, 10], b: [10, 10, 200]};
        const walk = Int32Array.from([0, 1, 2]);                 // three cells along the top row, 4 px each
        const a = Uint8Array.from([1, 0, 1]), b = Uint8Array.from([0, 1, 1]);
        const px = (x, y) => Array.from(rgba.subarray((y * size + x) * 4, (y * size + x) * 4 + 4));
        const out = {};
        for (const show of ["both", "A", "B"]) {
          C.paintUnknown(rgba, size, walk, a, b, colors, show);
          out[show] = [];
          for (let y = 0; y < 4; y++) for (let x = 0; x < 12; x++) out[show].push(px(x, y));
        }
        C.paintUnknown(rgba, size, walk, null, null, colors, "both");
        out.blank = !Array.from(rgba).some(v => v);
        out.hatch = C.HATCH_PX;
        return out;
      }"""
    got = run_node(body, {})
    n = got["hatch"]

    def cell(show, k):
        return [got[show][y * 12 + x] for y in range(4) for x in range(4 * k, 4 * k + 4)]

    def colours(pixels):
        return {tuple(p[:3]) for p in pixels if p[3]}

    assert colours(cell("both", 0)) == {(10, 10, 200)}, "A's unknown in B's colour"
    assert colours(cell("both", 1)) == {(200, 10, 10)}, "B's unknown in A's colour"
    assert colours(cell("both", 2)) == {(10, 10, 200), (200, 10, 10)}, "both: cross-hatched"
    lit = [p for p in cell("both", 0) if p[3]]
    assert 0 < len(lit) < 16 and len({p[3] for p in lit}) == 1, "a hatch, not a fill, at one opacity"
    assert colours(cell("A", 1)) == set() and colours(cell("B", 0)) == set(), "the toggle shows one team"
    assert got["blank"] and n >= 3


def test_a_just_lost_enemy_is_placed_at_its_last_sighting_while_it_fades():
    body = "function run(p) { return p.ts.map(t => C.lostAt(p.s, t, 3)); }"
    sightings = {"5": [[1.0, 2.0, 100, 200], [6.0, 7.0, 300, 400]]}
    got = run_node(body, {"s": sightings, "ts": [0.5, 1.5, 3.0, 5.5, 6.5, 9.0, 11.0]})
    assert got[0] == [] and got[1] == []                          # before any sighting; seen now
    assert got[2] == [{"slot": 5, "u": 100, "v": 200, "age": 1.0}]
    assert got[3] == []                                           # faded (3.5 s after)
    assert got[4] == [] and got[5] == [{"slot": 5, "u": 300, "v": 400, "age": 2.0}] and got[6] == []


def test_a_wrong_magic_or_version_throws(stored):
    raw = bytearray(base64.b64decode(stored["raw"]))
    bad_magic, bad_version = bytearray(raw), bytearray(raw)
    bad_magic[0] = ord("X")
    bad_version[4] = cf.DATA_VERSION + 1
    body = """
      function run(p) {
        return p.cases.map(b => { try { C.parse(C.base64Bytes(b)); return "ok"; } catch (e) { return e.message; } });
      }"""
    got = run_node(body, {"cases": [base64.b64encode(bytes(b)).decode() for b in (bad_magic, bad_version, raw)]})
    assert got[0] == "not a control blob"
    assert "version" in got[1]
    assert got[2] == "ok"


def test_tick_at_holds_the_last_tick():
    body = "function run(p) { return p.ts.map(t => C.tickAt(p.times, t)); }"
    got = run_node(body, {"times": [0.5, 1.0, 1.0625, 3.0], "ts": [0.0, 0.5, 0.99, 1.0, 1.07, 2.9, 3.0, 99]})
    assert got == [-1, 0, 0, 1, 2, 2, 3, 3]


PAINT = """
  function run(p) {
    const size = 256, rgba = new Uint8Array(size * size * 4);
    C.paintStates(rgba, size, Int32Array.from(p.walk), Uint8Array.from(p.codes), {a: [200, 10, 10], b: [10, 10, 200]});
    return p.at.map(([x, y]) => Array.from(rgba.subarray((y * size + x) * 4, (y * size + x) * 4 + 4)));
  }
"""


def test_the_layer_paints_each_state_at_the_plans_opacity():
    names = cf.STATE_NAMES
    walk = list(range(len(names)))          # cells 0..8 along the top row, one per state code
    codes = list(range(len(names)))
    at = [[c * 2, 0] for c in walk] + [[300 % 256, 200]]   # 2 px a cell at size 256; then an unwalkable pixel
    got = run_node(PAINT, {"walk": walk, "codes": codes, "at": at})
    alpha = {"passive": 38, "safe": 77, "active": 140}
    for code, name in enumerate(names):
        pixel = got[code]
        if name == "none":
            assert pixel == [0, 0, 0, 0]
        elif name.startswith(("a_", "b_")):
            assert pixel[:3] == ([200, 10, 10] if name[0] == "a" else [10, 10, 200])
            assert pixel[3] == alpha[name.split("_")[1]]
        else:
            assert pixel[3] == (140 if name == "contested_active" else 77)
            assert pixel[:3] in ([200, 10, 10], [10, 10, 200])
    assert got[-1] == [0, 0, 0, 0]


def test_contested_cells_are_striped_in_both_colours():
    body = """
      function run(p) {
        const size = 1024, rgba = new Uint8Array(size * size * 4);
        C.paintStates(rgba, size, Int32Array.from([0]), Uint8Array.from([7]), {a: [200, 10, 10], b: [10, 10, 200]});
        const row = [];
        for (let x = 0; x < 8; x++) row.push(rgba[x * 4]);
        return row;
      }"""
    row = run_node(body, {})
    assert set(row) == {200, 10}                 # both colours along one pixel row of the 8 px cell
    assert row[:cf_stripe()] == [200] * cf_stripe()


def cf_stripe() -> int:
    return run_node("function run(p) { return C.STRIPE_PX; }", {})


def test_borders_off_preserves_every_legacy_paint_byte():
    got = run_node("""
      function run() {
        const size = 512, colors = {a: [200, 10, 10], b: [10, 10, 200]};
        const walk = Int32Array.from(C.STATE_NAMES.map((_, i) => i));
        const codes = Uint8Array.from(walk), a = Uint8Array.from(walk, i => i % 2);
        const b = Uint8Array.from(walk, i => i % 3 ? 1 : 0);
        const expected = new Uint8Array(size * size * 4), actual = expected.slice();
        const put = (x, y, rgb, alpha) => expected.set([...rgb, alpha], (y * size + x) * 4);
        let states = true, unknown = true;
        for (const borders of [undefined, false]) {
          expected.fill(0);
          for (let k = 0; k < walk.length; k++) {
            const name = C.STATE_NAMES[k];
            if (name === 'none') continue;
            const contested = name.startsWith('contested');
            const alpha = name.endsWith('active') ? 140 : name.endsWith('passive') ? 38 : 77;
            for (let y = 0; y < 4; y++) for (let x = k * 4; x < k * 4 + 4; x++)
              put(x, y, contested ? (Math.floor((x + y) / 4) % 2 ? colors.b : colors.a)
                : colors[name[0]], alpha);
          }
          C.paintStates(actual, size, walk, codes, colors, borders);
          states = states && Buffer.from(actual).equals(Buffer.from(expected));
          for (const show of ['A', 'B', 'both']) {
            expected.fill(0);
            for (let k = 0; k < walk.length; k++)
              for (let y = 0; y < 4; y++) for (let x = k * 4; x < k * 4 + 4; x++) {
                if (a[k] && show !== 'B' && (x + y) % 4 === 0) put(x, y, colors.b, 140);
                else if (b[k] && show !== 'A' && ((x - y) % 4 + 4) % 4 === 0) put(x, y, colors.a, 140);
              }
            C.paintUnknown(actual, size, walk, a, b, colors, show, borders);
            unknown = unknown && Buffer.from(actual).equals(Buffer.from(expected));
          }
        }
        return {states, unknown};
      }""", {})
    assert got == {"states": True, "unknown": True}


@pytest.mark.parametrize("other", ["b_active", "contested", "none", "a_safe"])
def test_control_borders_only_shared_sides_between_different_owners(other):
    got = run_node("""
      function run(p) {
        const size = 512, rgba = new Uint8Array(size * size * 4);
        C.paintStates(rgba, size, Int32Array.from([0, 1]),
          Uint8Array.from(['a_active', p.other].map(n => C.STATE_NAMES.indexOf(n))),
          {a: [200, 10, 10], b: [10, 10, 200]}, true);
        const px = (x, y) => Array.from(rgba.slice((y * size + x) * 4, (y * size + x) * 4 + 4));
        return {a: px(3, 1), a2: px(2, 1), b: px(4, 1), b2: px(5, 1), b3: px(6, 1),
          outer: px(0, 1), top: px(1, 0), inside: px(1, 1)};
      }""", {"other": other})
    # Edges are two pixels deep, inside the cell that owns them, opaque, in the team colour at full brightness.
    assert got["a"] == got["a2"] == ([200, 10, 10, 140] if other == "a_safe" else [255, 13, 13, 255])
    assert got["b"] == got["b2"] == ([13, 13, 255, 255] if other == "b_active" else
                                     [0, 0, 0, 0] if other == "none" else
                                     [10, 10, 200, 77] if other == "contested" else [200, 10, 10, 77])
    assert got["b3"] == ([10, 10, 200, 140] if other == "b_active" else [0, 0, 0, 0] if other == "none" else
                         [10, 10, 200, 77] if other == "contested" else [200, 10, 10, 77])
    assert got["outer"] == got["top"] == got["inside"] == [200, 10, 10, 140]


@pytest.mark.parametrize("show", ["A", "B", "both"])
def test_unknown_borders_dash_at_region_edges_in_the_hatch_colour(show):
    got = run_node("""
      function run(p) {
        const size = 1024, rgba = new Uint8Array(size * size * 4);
        // Two unknown cells vertically: the right edge must continue through their join.
        const walk = Int32Array.from([0, 1, 128, 129, 256]);
        const mask = Uint8Array.from([1, 0, 1, 0, 0]), blank = new Uint8Array(5);
        C.paintUnknown(rgba, size, walk, p.show === 'B' ? blank : mask,
          p.show === 'B' ? mask : blank, {a: [200, 10, 10], b: [10, 10, 200]}, p.show, true);
        const px = (x, y) => Array.from(rgba.slice((y * size + x) * 4, (y * size + x) * 4 + 4));
        const col = x => Array.from({length: 16}, (_, y) => px(x, y));
        const row = y => Array.from({length: 8}, (_, x) => px(x, y));
        return {edge7: col(7), edge6: col(6), bottom15: row(15), bottom14: row(14),
          inner: col(5), join: px(2, 7), outside: px(8, 1)};
      }""", {"show": show})
    colour = [200, 10, 10] if show == "B" else [10, 10, 200]

    def hatch(x, y):
        return (x - y) % 4 == 0 if show == "B" else (x + y) % 4 == 0

    # Dashes are two pixels deep: on for two pixels, off for two, along the edge, in the hatch colour at full
    # brightness; the hatch shows in the gaps.
    on, hatched, blank = [13 if c == 10 else 255 for c in colour] + [217], colour + [140], [0, 0, 0, 0]
    for x in (7, 6):
        assert got[f"edge{x}"] == [on if y % 4 < 2 else hatched if hatch(x, y) else blank for y in range(16)]
    for y in (15, 14):
        assert got[f"bottom{y}"] == [on if x % 4 < 2 else hatched if hatch(x, y) else blank for x in range(8)]
    assert got["inner"][:14] == [hatched if hatch(5, y) else blank for y in range(14)]  # 14-15: bottom edge
    assert got["join"] == got["outside"] == [0, 0, 0, 0]


@pytest.mark.parametrize("show", ["A", "B"])
def test_unknown_border_off_pixels_preserve_underlying_hatch(show):
    got = run_node("""
      function run(p) {
        // Six pixels per cell puts a hatch on the left edge at an off pixel of the dash.
        const size = 768, rgba = new Uint8Array(size * size * 4);
        const walk = Int32Array.from([0, 1]), mask = Uint8Array.from([0, 1]), blank = new Uint8Array(2);
        const a = p.show === 'A' ? mask : blank, b = p.show === 'B' ? mask : blank;
        const colors = {a: [200, 10, 10], b: [10, 10, 200]};
        const px = (x, y) => Array.from(rgba.slice((y * size + x) * 4, (y * size + x) * 4 + 4));
        C.paintUnknown(rgba, size, walk, a, b, colors, p.show);
        const hatch = px(6, 2);
        C.paintUnknown(rgba, size, walk, a, b, colors, p.show, true);
        return {hatch, offWithHatch: px(6, 2), offWithoutHatch: px(6, 3), on: px(6, 0)};
      }""", {"show": show})
    colour = [10, 10, 200] if show == "A" else [200, 10, 10]
    assert got["hatch"] == got["offWithHatch"] == colour + [140]
    assert got["on"] == [13 if c == 10 else 255 for c in colour] + [217]
    assert got["offWithoutHatch"] == [0, 0, 0, 0]


def test_unknown_border_off_pixels_preserve_the_other_teams_dash():
    got = run_node("""
      function run() {
        const size = 768, rgba = new Uint8Array(size * size * 4);
        // In cell 1, A's left edge crosses B's bottom edge at (6, 5): A on, B off.
        const walk = Int32Array.from([0, 1, 129]);
        const a = Uint8Array.from([0, 1, 1]), b = Uint8Array.from([1, 1, 0]);
        const colors = {a: [200, 10, 10], b: [10, 10, 200]};
        const px = (x, y) => Array.from(rgba.slice((y * size + x) * 4, (y * size + x) * 4 + 4));
        C.paintUnknown(rgba, size, walk, a, b, colors, 'both');
        const noHatch = px(6, 5);
        C.paintUnknown(rgba, size, walk, a, b, colors, 'A', true);
        const aOnly = px(6, 5);
        C.paintUnknown(rgba, size, walk, a, b, colors, 'both', true);
        return {noHatch, aOnly, both: px(6, 5), bOn: px(8, 5)};
      }""", {})
    assert got["noHatch"] == [0, 0, 0, 0]
    assert got["aOnly"] == got["both"] == [13, 13, 255, 217]
    assert got["bOn"] == [255, 13, 13, 217]


def test_the_highlight_fills_control_and_outlines_coverage():
    body = """
      function run(p) {
        const size = 1024, rgba = new Uint8Array(size * size * 4);
        // cells 0 (control), 1 and 2 (coverage only) on the top row; 8 px a cell
        C.paintHighlight(rgba, size, Int32Array.from([0, 1, 2]), Uint8Array.from([1, 0, 0]), Uint8Array.from([1, 1, 1]),
                         [0, 200, 0]);
        const px = (x, y) => Array.from(rgba.subarray((y * size + x) * 4, (y * size + x) * 4 + 4));
        return {control: px(3, 3), coverTop: px(12, 0), coverInside: px(12, 4), coverRightEdge: px(23, 4),
                between: px(16, 4)};
      }"""
    got = run_node(body, {})
    assert got["control"] == [0, 200, 0, 153]
    assert got["coverTop"][3] == 230 and got["coverRightEdge"][3] == 230
    assert got["coverInside"] == [0, 0, 0, 0]
    assert got["between"] == [0, 0, 0, 0]       # cell 1 and cell 2 are both covered: no edge between them


CACHE = """
  async function go(p) {
    const asked = [], aborted = [];
    const load = (n, signal) => {
      asked.push(n);
      if (signal) signal.addEventListener("abort", () => aborted.push(n));
      if (n === p.notReady) return {status: "not_ready"};
      if (n === p.hang) return new Promise(() => {});
      return {status: "ok", buffer: C.base64Bytes(p.raw), stale: n === p.stale};
    };
    const cache = new C.ControlCache(load, 3);
    const out = {};
    for (const n of [1, 2, 3]) out["r" + n] = (await cache.get(n)).status;
    out.kept = Object.keys(cache.entries).map(Number);
    await cache.get(4);                                   // past the limit: round 1 goes
    out.afterFour = Object.keys(cache.entries).map(Number);
    out.nr = (await cache.get(p.notReady)).status;        // not kept, so asked again
    await cache.get(p.notReady);
    out.stale = (await cache.get(p.stale)).stale;
    cache.get(p.hang);                                    // in flight, then evicted by keep()
    await new Promise(r => setTimeout(r, 0));
    cache.keep([p.stale]);
    out.afterKeep = Object.keys(cache.entries).map(Number);
    out.asked = asked; out.aborted = aborted;
    out.tick0 = Array.from(cache.ready(p.stale).cursor.states(0)).length;
    return out;
  }
"""


def test_the_control_cache_is_bounded_and_keeps_only_ok_answers(stored):
    script = """
      const C = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", async () => {
        process.stdout.write(JSON.stringify(await go(JSON.parse(input))));
      });
    """ + CACHE
    completed = subprocess.run([NODE, "-e", script, str(CONTROL_JS)],
                               input=json.dumps({"raw": stored["raw"], "notReady": 7, "hang": 9, "stale": 5}),
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    got = json.loads(completed.stdout)
    assert (got["r1"], got["r2"], got["r3"]) == ("ok", "ok", "ok") and sorted(got["kept"]) == [1, 2, 3]
    assert sorted(got["afterFour"]) == [2, 3, 4]
    assert got["nr"] == "not_ready" and got["asked"].count(7) == 2
    assert got["stale"] is True
    assert got["afterKeep"] == [5] and got["aborted"] == [9]
    assert got["tick0"] == stored["header"]["cells"]


@pytest.mark.parametrize("value, expected", [
    ({"stale": False, "revision": 5, "currentRevision": "5"}, "Map control r5."),
    ({"stale": False, "revision": None, "currentRevision": "5"}, "Map control (revision not recorded)."),
    ({"stale": True, "revision": 4, "currentRevision": "5"},
     "Out of date: computed under r4, current r5. Queued for recompute."),
    ({"stale": True, "revision": 5, "currentRevision": "5"},
     "Out of date: computed under r5, but its inputs changed (a new link, the replay, or the map's geometry). "
     "Queued for recompute."),
    ({"stale": True, "revision": None, "currentRevision": "5"},
     "Out of date: revision not recorded. Queued for recompute."),
    ({"stale": True, "revision": 5, "currentRevision": None},
     "Out of date: computed under r5. Queued for recompute."),
])
def test_the_revision_note(value, expected):
    # docs/superpowers/plans/2026-10-05-control-idle-queue.md, D6
    script = f"const C = require({json.dumps(str(CONTROL_JS))}); process.stdout.write(C.revisionNote({json.dumps(value)}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert out == expected


def test_the_cache_reads_the_revision_from_the_data_header(stored):
    script = """
      const C = require(process.argv[1]);
      let input = ""; process.stdin.on("data", d => input += d).on("end", async () => {
        const raw = C.base64Bytes(JSON.parse(input).raw);
        const cache = new C.ControlCache(() => ({ status: "ok", buffer: raw, stale: false, currentRevision: "9" }));
        const value = await cache.get(1);
        process.stdout.write(JSON.stringify({ revision: value.revision, current: value.currentRevision }));
      });
    """
    completed = subprocess.run([NODE, "-e", script, str(CONTROL_JS)], input=json.dumps({"raw": stored["raw"]}),
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"revision": stored["header"]["revision"], "current": "9"}


REPLAY_JS =WEBAPP / "app" / "static" / "js" / "replay.js"


def test_the_control_table_groups_players_by_team_and_sums_lost_control():
    tables = {
        "rounds": {"3": {"status": "ok", "stale": True, "redundant_m2": {"A": -12.5, "B": 4.0}, "players": {
            "5": {"team": "B", "control_m2": 80.0, "active_m2": 10.0, "passive_m2": 30.0, "active_ratio": 0.25,
                  "alive_s": 40.0, "lost": []},
            "0": {"team": "A", "control_m2": -3.0, "active_m2": 5.0, "passive_m2": 15.0, "active_ratio": None,
                  "alive_s": 12.0, "lost": [{"control_m2": 30.0, "share_of_team": 0.1},
                                            {"control_m2": 2.0, "share_of_team": None}]}}},
                   "4": {"status": "missing"}},
        "match": {"redundant_m2": {"A": 1.0}, "players": {
            "0": {"team": "A", "control_m2": 20.0, "deaths": 2, "lost_m2": 32.0, "lost_mean_m2": 16.0, "lost_share": 0.1},
            "5": {"team": "B", "control_m2": 50.0, "deaths": 0, "lost_m2": 0.0, "lost_mean_m2": None, "lost_share": None}}}}
    body = """
      function run(p) {
        return {round: C.controlRows(p.tables, "round", 3, p.groupTeam), missing: C.controlRows(p.tables, "round", 4, p.groupTeam),
                match: C.controlRows(p.tables, "match", 3, p.groupTeam)};
      }"""
    got = run_node(body, {"tables": tables, "groupTeam": {"A": "team-2", "B": "team-1"}}, js=REPLAY_JS)
    rnd = got["round"]
    assert [r["slot"] for r in rnd["team-1"]] == [5] and [r["slot"] for r in rnd["team-2"]] == [0]
    assert rnd["team-2"][0]["lost"] == {"m2": 32.0, "share": 0.1, "deaths": 2}
    assert rnd["team-1"][0]["lost"] is None
    assert rnd["redundant"] == {"team-2": -12.5, "team-1": 4.0} and rnd["stale"] is True
    assert got["missing"] is None
    assert got["match"]["team-2"][0]["lost"] == {"m2": 16.0, "share": 0.1, "deaths": 2}
    assert got["match"]["team-1"][0]["lost"] is None
    # space taken (CONTROL_REVISION 2): per round, or per round on average for the match; absent before
    assert rnd["team-1"][0].get("taken") is None


def test_the_control_table_carries_space_taken():
    tables = {"rounds": {"1": {"status": "ok", "redundant_m2": {}, "players": {
        "0": {"team": "A", "control_m2": 1.0, "taken_m2": 42.0, "lost": []}}}},
              "match": {"redundant_m2": {}, "players": {"0": {"team": "A", "taken_m2": 84.0, "taken_per_round_m2": 42.0,
                                                               "deaths": 0}}}}
    body = """function run(p) { return [C.controlRows(p.t, "round", 1, {}), C.controlRows(p.t, "match", 1, {})]; }"""
    rnd, match = run_node(body, {"t": tables}, js=REPLAY_JS)
    assert rnd["team-1"][0]["taken"] == 42.0 and match["team-1"][0]["taken"] == 42.0


def test_site_data_merges_onto_a_blob_by_kill_index():
    body = "function run(p) { return C.withSiteData(p.site, p.blob); }"
    got = run_node(body, {"site": {"db": {"winner": "team-1"}, "stats": {}, "alive_steps": [], "annotations": None,
                                   "kills": {"1": {"weapon": "Vandal"}}},
                          "blob": {"kills": [{"i": 0}, {"i": 1}]}}, js=REPLAY_JS)
    assert got["db"] == {"winner": "team-1"} and got["kills"] == [{"i": 0}, {"i": 1, "weapon": "Vandal"}]


def test_the_preview_page_inlines_control_from_an_export_folder(tmp_path, round_control):
    sys.path.insert(0, str(WEBAPP / "scripts"))
    import render_replay_standalone as standalone

    rc, blob = round_control
    from app.replays import format as fmt

    (tmp_path / "1.json.gz").write_bytes(fmt.encode_blob({**blob, "v": 1, "round": 1, "map": "Ascent", "hz": 16,
                                                         "tracks": {}, "alive": {}, "kills": []}))
    (tmp_path / "1.control.bin").write_bytes(encode_data(rc, blob))
    context = {"match": {"linked": True, "control": {"cover_reviewed": False}, "uv_per_unit": 0.75, "rounds": [1]},
               "players": {}, "rounds": {"1": {"db": None, "stats": {}, "kills": {}}}}
    (tmp_path / "context.json").write_text(json.dumps(context), encoding="utf-8")
    (tmp_path / "control_players.json").write_text(json.dumps({"rounds": {}, "match": {}}), encoding="utf-8")
    site = standalone.load_site(tmp_path)
    assert site["control"]["1"] == base64.b64encode(gzip.decompress(encode_data(rc, blob))).decode("ascii")
    page = standalone.render(standalone.load_blobs(tmp_path), site)
    assert 'data-replay-layer="control"' in page and "cover not reviewed" in page
    assert "global.ReplayControl = api" in page and site["control"]["1"] in page
    assert 'data-replay-layer="control"' not in standalone.render(standalone.load_blobs(tmp_path))


HEAT = """
  function run(p) {
    const size = 256, rgba = new Uint8Array(size * size * 4), out = {};
    const shares = {x: Uint8Array.from(p.x), y: Uint8Array.from(p.y), contested: Uint8Array.from(p.c)};
    for (const mode of ["lead", "x", "y", "contested"]) {
      C.paintHeatmap(rgba, size, Int32Array.from(p.walk), shares, mode, {x: [200, 0, 0], y: [0, 0, 200]});
      out[mode] = p.walk.map(cell => Array.from(rgba.subarray((cell * 2) * 4, (cell * 2) * 4 + 4)));
    }
    out.index = Array.from(C.cellIndex(Int32Array.from(p.walk)).subarray(0, 6));
    return out;
  }
"""


def test_the_heatmap_paints_the_leader_or_one_share():
    # cells 0-3 on the top row, 2 px each at size 256: x leads, y leads, contested leads, nothing
    got = run_node(HEAT, {"walk": [0, 1, 2, 3], "x": [200, 10, 50, 0], "y": [20, 100, 60, 0], "c": [0, 0, 90, 0]})
    alpha = lambda share: int(0.85 * share + 0.5)  # noqa: E731  (JS Math.round)
    assert got["lead"][0] == [200, 0, 0, alpha(200)]
    assert got["lead"][1] == [0, 0, 200, alpha(100)]
    assert got["lead"][2][3] == alpha(90) and got["lead"][2][:3] in ([200, 0, 0], [0, 0, 200])
    assert got["lead"][3] == [0, 0, 0, 0]
    assert got["x"][1] == [200, 0, 0, alpha(10)] and got["y"][0] == [0, 0, 200, alpha(20)]
    assert got["contested"][0] == [0, 0, 0, 0] and got["contested"][2][3] == alpha(90)
    assert got["index"] == [0, 1, 2, 3, -1, -1]


def test_side_groups_map_to_teams():
    got = run_node("function run(p) { return C.groupTeams(p.players); }",
                   {"players": {"0": {"side": "A", "team": "team-2"}, "5": {"side": "B", "team": "team-1"}}})
    assert got == {"A": "team-2", "B": "team-1"}


def test_the_control_bar_counts_each_groups_cells_at_any_level():
    names = cf.STATE_NAMES
    codes = [names.index(n) for n in ("a_active", "a_safe", "a_passive", "b_active", "b_passive",
                                      "contested", "contested_active", "none", "none", "none")]
    got = run_node("function run(p) { return C.controlShares(Uint8Array.from(p.codes)); }", {"codes": codes})
    assert got == {"a": 3, "b": 2, "contested": 2, "none": 3, "total": 10}
    assert run_node("function run(p) { return C.controlShares(null); }", {})["total"] == 0


def test_the_darker_map_dims_the_floor_keeps_the_sites_hue_and_the_empty_border():
    body = "function run(p) { return Array.from(C.darkenMap(Uint8ClampedArray.from(p.px))); }"
    floor, site, empty = [150, 150, 150, 255], [170, 160, 100, 255], [90, 90, 90, 0]
    got = run_node(body, {"px": floor + site + empty}, js=REPLAY_JS)
    f, s, e = got[0:4], got[4:8], got[8:12]
    assert f == [57, 60, 71, 255]                                         # mean x 0.42, then (0.9, 0.95, 1.12)
    assert s == [94, 88, 45, 255]                                         # site: (0.55, 0.55, 0.45), still yellow
    assert e == empty                                                     # off the map: untouched


def test_outlines_trace_blocking_pixels_next_to_open_floor():
    # 12 x 12, everything blocked but one open pixel: painted within squared distance 10 of it, not beyond
    size = 12
    body = """
      function run(p) {
        const mask = new Uint8Array(p.size * p.size).fill(1);
        mask[p.open[1] * p.size + p.open[0]] = 0;
        const rgba = new Uint8ClampedArray(p.size * p.size * 4);
        C.outlinePixels(mask, p.size, rgba);
        return Array.from({length: p.size * p.size}, (_, k) => Array.from(rgba.subarray(k * 4, k * 4 + 4)));
      }"""
    got = run_node(body, {"size": size, "open": [6, 6]}, js=REPLAY_JS)
    at = lambda x, y: got[y * size + x]  # noqa: E731
    ink = [222, 232, 244, 255]                                            # the literal colour, not the constant
    for x, y in [(7, 6), (9, 6), (9, 7), (8, 8), (6, 5)]:                 # squared distance 1, 9, 10, 8, 1
        assert at(x, y) == ink, (x, y)
    for x, y in [(9, 8), (10, 6), (10, 8), (0, 0), (11, 11)]:             # squared distance 13, 16, 20, far
        assert at(x, y) == [0, 0, 0, 0], (x, y)
    assert at(6, 6) == [0, 0, 0, 0]                                       # the open floor is never painted
    got = run_node(body, {"size": size, "open": [1, 0]}, js=REPLAY_JS)    # a blocked pixel on the border
    assert at(0, 0) == ink and at(11, 11) == [0, 0, 0, 0]                 # no read past the mask's edge


def _viewer_script(body: str) -> str:
    return ("const rec = require(" + json.dumps(_RECORDER) + "); const R = rec.environment(process.argv[1]);\n"
            "function stubBar() {\n"
            "  const spans = {}, attrs = {};\n"
            "  const span = k => spans[k] || (spans[k] = {style: {}, textContent: ''});\n"
            "  return {hidden: true, spans, attrs, setAttribute: (k, v) => { attrs[k] = v; },\n"
            "          getAttribute: k => (k in attrs ? attrs[k] : null), querySelector: sel => span(sel)};\n"
            "}\n" + body)


_RECORDER = str(WEBAPP / "tests" / "replays" / "viewer_recorder.js")
_PLAYERS = {"0": {"side": "A", "team": "team-2"}, "5": {"side": "B", "team": "team-1"}}


def test_the_control_bar_puts_team_1_at_the_bottom_and_hides_without_data():
    body = _viewer_script("""
      function run(p) {
        const {v} = rec.makeViewer(R);
        v.linked = {players: p.players};
        const out = {};
        for (const [name, layers, shares] of p.cases) {
          v.ui.ctlBar = stubBar();
          Object.assign(v.layers, layers);
          v.renderCtlBar(shares);
          const s = v.ui.ctlBar.spans;
          out[name] = {hidden: v.ui.ctlBar.hidden, top: s["[data-ctlbar-n2]"] && s["[data-ctlbar-n2]"].textContent,
                       bottom: s["[data-ctlbar-n1]"] && s["[data-ctlbar-n1]"].textContent,
                       t2: s["[data-ctlbar-t2]"] && s["[data-ctlbar-t2]"].style.flexGrow,
                       t1: s["[data-ctlbar-t1]"] && s["[data-ctlbar-t1]"].style.flexGrow,
                       mid: s["[data-ctlbar-mid]"] && s["[data-ctlbar-mid]"].style.flexGrow};
        }
        return out;
      }""")
    shares = {"a": 60, "b": 20, "contested": 10, "none": 10, "total": 100}
    on = {"control": True, "bar": True}
    cases = [["shown", on, shares], ["no_control", {"control": False, "bar": True}, shares],
             ["no_bar", {"control": True, "bar": False}, shares], ["null", on, None],
             ["empty", on, dict(shares, total=0)]]
    got = run_node(body, {"recorder": _RECORDER, "players": _PLAYERS, "cases": cases}, js=REPLAY_JS)
    # A is Team 2 (top), B is Team 1 (bottom)
    assert got["shown"] == {"hidden": False, "top": 60, "bottom": 20, "t2": 60, "t1": 20, "mid": 20}
    for name in ("no_control", "no_bar", "null", "empty"):
        assert got[name]["hidden"] is True, name


def test_the_control_bar_counts_the_picture_the_map_shows():
    body = _viewer_script("""
      function run(p) {
        const names = ReplayControl.STATE_NAMES;
        const code = n => names.indexOf(n);
        const truth = Uint8Array.from([code("a_active"), code("a_active"), code("b_active"), code("none")]);
        const knew = Uint8Array.from([code("b_safe"), code("b_safe"), code("b_passive"), code("contested")]);
        const out = {};
        for (const view of ["true", "A"]) {
          const {v} = rec.makeViewer(R);
          v.linked = {players: p.players};
          v.ui.ctlBar = stubBar();
          v.control = {}; v.layers.control = true; v.layers.bar = true; v.unknownView = "off";
          v.controlView = view; v.controlPaint = null; v.t = 0;
          v.controlCache = {ready: () => ({parsed: {times: [0], walk: [], knew_a: true, header: {}},
            cursor: {states: () => truth, knew: () => knew}})};
          v.controlColors = () => ({});
          v.controlCanvas = () => ({canvas: {}, ctx: {putImageData() {}, createImageData: () => ({data: new Uint8ClampedArray(4)})},
                                    data: {data: new Uint8ClampedArray(4)}});
          ReplayControl.paintStates = () => {};
          v.drawControl(rec.mockCtx([]), 512, []);
          out[view] = {n1: v.ui.ctlBar.spans["[data-ctlbar-n1]"].textContent,
                       n2: v.ui.ctlBar.spans["[data-ctlbar-n2]"].textContent};
        }
        return out;
      }""")
    got = run_node(body, {"recorder": _RECORDER, "players": _PLAYERS}, js=REPLAY_JS)
    # true picture: A 2 of 4 (Team 2 at the top: 50), B 1 of 4 (Team 1, 25); as A knew it: B holds 3 of 4 (75), A none
    assert got["true"] == {"n1": 25, "n2": 50}
    assert got["A"] == {"n1": 75, "n2": 0}


def test_a_layer_set_from_code_switches_the_view_picker_to_custom():
    body = _viewer_script("""
      function run(p) {
        const out = {};
        for (const [name, layer, on, applying, picker] of p.cases) {
          const store = {"replay-preset": picker};
          globalThis.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, x) => { store[k] = String(x); }};
          const {v} = rec.makeViewer(R);
          v.root = {querySelector: () => ({checked: false})};
          v.refreshGaps = () => {}; v.refreshAreas = () => {}; v.draw = () => {};
          v.ui.preset = {value: picker};
          v.applyingPreset = applying;
          v.setLayer(layer, on);
          out[name] = {picker: v.ui.preset.value, saved: store["replay-preset"], layer: v.layers[layer],
                       stored: store["replay-layer-" + layer]};
        }
        return out;
      }""")
    cases = [["change", "gaps", True, False, "team-1"],            # gaps starts off: this really changes it
             ["unchanged", "gaps", False, False, "team-1"],        # already off: nothing changed
             ["preset", "gaps", True, True, "team-1"],             # a preset is applying: never Custom
             ["custom", "gaps", True, False, "custom"]]
    got = run_node(body, {"recorder": _RECORDER, "cases": cases}, js=REPLAY_JS)
    assert got["change"] == {"picker": "custom", "saved": "custom", "layer": True, "stored": "1"}
    assert got["unchanged"]["picker"] == "team-1" and got["unchanged"]["saved"] == "team-1"
    assert got["preset"]["picker"] == "team-1" and got["preset"]["saved"] == "team-1"
    assert got["preset"]["layer"] is True and got["preset"]["stored"] == "1"
    assert got["custom"]["picker"] == "custom" and got["custom"]["saved"] == "custom"


def test_the_view_presets_are_the_owners_picks():
    body = "function run(p) { return p.names.map(n => C.viewPreset(n, p.teams)); }"
    teams = {"A": "team-2", "B": "team-1"}
    blue, red, every, custom, odd = run_node(
        body, {"names": ["team-1", "team-2", "all", "custom", "nope"], "teams": teams}, js=REPLAY_JS)
    on = {"abilities", "projectiles", "tracers", "cones", "aim", "features", "control", "bar"}
    for preset in (blue, red, every):
        assert {k for k, v in preset["layers"].items() if v} - {"names"} == on
        assert preset["layers"]["areas"] is False and preset["layers"]["gaps"] is False
        assert preset["view"] == "true"                                   # the true positions, every preset
    assert blue["layers"]["names"] is False and red["layers"]["names"] is False and every["layers"]["names"] is True
    assert blue["unknown"] == "A" and red["unknown"] == "B"               # the enemy team's unknown, by group
    assert every["unknown"] == "both"
    assert custom is None and odd is None
    fallback = run_node(body, {"names": ["team-1", "team-2"], "teams": {}}, js=REPLAY_JS)
    assert [p["unknown"] for p in fallback] == ["B", "A"]

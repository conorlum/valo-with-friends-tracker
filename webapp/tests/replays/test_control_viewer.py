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


REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"


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

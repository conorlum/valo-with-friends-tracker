"""Timing gaps in the viewer (docs/superpowers/plans/2026-10-04-timing-gaps-viewer.md, S3): the pure functions in
app/static/js/replay_gaps.js, replay.js's drawing hook on a stub canvas, and the player partial's Gaps parts.
The JS checks need Node; they skip without it."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
GAPS_JS = WEBAPP / "app" / "static" / "js" / "replay_gaps.js"
REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

READ_STDIN = """
  const G = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const payload = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(payload)));
  });
"""

NAMES = {0: "Alpha#1", 1: "Bravo#2", 5: "Echo#5", 6: "Foxtrot#6", 7: "Golf#7"}
CHOKES = {"3": {"name": "Mid doors", "x": 500.0, "y": 300.0}, "8": {"name": "8", "x": 420.0, "y": 260.0}}


def row(seq, **kw):
    base = {"seq": seq, "kind": "predicted", "victim_slot": 0, "t_open": 10.0, "t_close": 15.0, "flicker": False,
            "cause": "open_timing", "cause_detail": {}, "choke_seq": [], "route": [[[8.0, 400, 200], [10.0, 500, 300]]],
            "candidate_slots": [5], "spot_xy": [500, 300], "victim_xy": [520, 320], "released_xy": None,
            "used": None, "merged": 0, "context": {"t_round": 10.0}, "stood_by": None, "shot_by": None,
            "killed_by": None, "killed_at": None}
    base.update(kw)
    return base


def run_node(body: str, payload, js: Path = GAPS_JS):
    completed = subprocess.run([NODE, "-e", READ_STDIN + body, str(js)], input=json.dumps(payload),
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_open_at_windows_and_the_flicker_filter():
    rows = [row(0), row(1, t_open=12.0, t_close=12.5, flicker=True),
            row(2, kind="backshot", t_open=20.0, t_close=None, cause=None)]
    got = run_node("""
      function run(p) {
        const at = (t, f) => G.openAt(p.rows, t, f).map(r => r.seq);
        return {before: at(9.99, false), open: at(10.0, false), close: at(15.0, false), after: at(15.01, false),
                hidden: at(12.2, false), shown: at(12.2, true), bs0: at(20.0, false), bs3: at(23.0, false),
                bs_after: at(23.01, false), bs_before: at(19.9, true), limit: G.BACKSHOT_SHOW_S};
      }""", {"rows": rows})
    assert got["before"] == [] and got["open"] == [0] and got["close"] == [0] and got["after"] == []
    assert got["hidden"] == [0], "a flicker is hidden unless switched on"
    assert got["shown"] == [0, 1]
    assert got["limit"] == 3 and got["bs0"] == [2] and got["bs3"] == [2] and got["bs_after"] == []
    assert got["bs_before"] == []


@needs_node
def test_list_rows_in_opening_order_without_flickers():
    rows = [row(4, t_open=30.0), row(1, t_open=5.0, flicker=True), row(2, t_open=12.0),
            row(3, t_open=12.0, kind="backshot"), row(0, t_open=12.0)]
    got = run_node("""
      function run(p) {
        const before = p.rows.map(r => r.seq);
        return {plain: G.listRows(p.rows, false).map(r => r.seq), all: G.listRows(p.rows, true).map(r => r.seq),
                untouched: JSON.stringify(p.rows.map(r => r.seq)) === JSON.stringify(before)};
      }""", {"rows": rows})
    assert got["plain"] == [0, 2, 3, 4]
    assert got["all"] == [1, 0, 2, 3, 4]
    assert got["untouched"] is True, "the input is not sorted in place"


DESCRIBE = """
  function run(p) {
    const names = p.names;
    const nameOf = s => names[String(s)] || ("slot " + s);
    return p.rows.map(r => Object.assign(G.describe(r, nameOf, p.chokes), {mark: G.markOf(r),
      tip: G.summary(r, nameOf, p.chokes)}));
  }
"""


@needs_node
def test_describe_names_players_chokes_and_causes_in_plain_words():
    rows = [
        row(0, cause="route_released", cause_detail={"cell": 100, "t": 9.0, "player": 1, "by": "view", "reason": "turned"},
            choke_seq=[3, 8], candidate_slots=[5, 6], released_xy=[100, 100], context={"t_round": 7.5}),
        row(1, cause="route_released", cause_detail={"cell": 100, "t": 9.0, "player": 7, "by": "utility",
                                                      "reason": "utility_expired"}, choke_seq=[9]),
        row(2, cause="victim_turned", used="stood", stood_by=6, merged=2),
        row(3, cause="victim_moved", used="shot", shot_by=5),
        row(4, cause="open_timing", used="killed", killed_by=6, choke_seq=None),
    ]
    got = run_node(DESCRIBE, {"rows": rows, "names": {str(k): v for k, v in NAMES.items()}, "chokes": CHOKES})
    first = got[0]
    assert first["when"] == "7.5 s" and first["who"] == "Alpha#1" and first["could"] == "Echo#5, Foxtrot#6"
    assert first["route"] == "Mid doors → 8"
    assert first["why"] == "a teammate stopped watching the route (Bravo#2, turned away)"
    assert first["happened"] == "nothing (unused)" and first["mark"] == "unused" and first["kind"] == "predicted"
    assert got[1]["route"] == "choke 9", "a choke missing from the asset still has a name"
    assert got[1]["why"] == "a teammate stopped watching the route (Golf#7's utility, their utility ended)"
    assert got[2]["why"] == "Alpha#1 turned away"
    assert got[2]["happened"] == "an enemy stood there (Foxtrot#6) · merged 2" and got[2]["mark"] == "used"
    assert got[3]["why"] == "Alpha#1 moved into the open" and got[3]["happened"] == "shot from behind by Echo#5"
    assert got[4]["why"] == "enough time passed (no one ever watched the route)"
    assert got[4]["happened"] == "killed from behind by Foxtrot#6"
    assert got[4]["route"].startswith("unknown")
    assert "Alpha#1" in first["tip"] and "Mid doors" in first["tip"] and "turned away" in first["tip"]


@needs_node
def test_describe_backshots_and_the_no_choke_route():
    rows = [
        row(0, kind="backshot", candidate_slots=[6], cause=None, context={"t_round": 31.2, "wall": True},
            killed_at=31.9, killed_by=6),
        row(1, kind="backshot", candidate_slots=[5], cause=None, context={"t_round": 40.0, "wall": False}),
        row(2, flicker=True),
    ]
    got = run_node(DESCRIBE, {"rows": rows, "names": {str(k): v for k, v in NAMES.items()}, "chokes": CHOKES})
    assert got[0]["kind"] == "back-shot" and got[0]["mark"] == "backshot" and got[0]["when"] == "31.2 s"
    assert got[0]["happened"] == "shot from behind by Foxtrot#6 (through a wall) · killed them"
    assert got[1]["happened"] == "shot from behind by Echo#5"
    assert got[1]["tip"].startswith("Back-shot at 40.0 s on Alpha#1")
    assert got[2]["route"] == "no choke" and got[2]["kind"] == "predicted (flicker)"


@needs_node
def test_rear_arc_and_to_canvas():
    got = run_node("""
      function run(p) {
        return {east: G.rearArc(0), south: G.rearArc(90), wrap: G.rearArc(-170), px: G.toCanvas([512, 256], 800)};
      }""", {})
    third = 2 * math.pi / 3
    # Facing +x (yaw 0): the back is centred on pi, from 2pi/3 to 4pi/3.
    assert got["east"][0] == pytest.approx(math.pi - math.pi / 3) and got["east"][1] == pytest.approx(math.pi + math.pi / 3)
    # Facing +y (yaw 90, down the canvas): the back is centred on 3pi/2.
    assert got["south"][0] == pytest.approx(1.5 * math.pi - math.pi / 3)
    for start, end in (got["east"], got["south"], got["wrap"]):
        assert 0 <= start < 2 * math.pi and end - start == pytest.approx(third)
    # Facing -170 degrees: back centred on 10 degrees, starting 50 degrees below it, wrapped into [0, 2pi).
    assert got["wrap"][0] == pytest.approx(2 * math.pi - math.radians(50))
    assert got["px"] == [400, 200]


@needs_node
def test_replay_js_loads_and_draws_open_gaps_on_a_stub_canvas():
    rows = [row(0, cause="route_released", cause_detail={"cell": 1, "t": 9.0, "player": 1, "by": "view",
                                                          "reason": "died"}, choke_seq=[3], released_xy=[300, 300]),
            row(1, kind="backshot", t_open=11.0, cause=None, candidate_slots=[6], context={"t_round": 11.0}),
            row(2, t_open=40.0, t_close=45.0)]
    got = run_node("""
      function run(p) {
        const calls = {arc: 0, stroke: 0, fill: 0, text: []};
        const ctx = new Proxy({}, {get: (o, k) => {
          if (k in o) return o[k];
          if (k === "measureText") return () => ({width: 10});
          if (k === "fillText") return (t) => calls.text.push(t);
          if (k === "arc") return () => { calls.arc += 1; };
          if (k === "stroke") return () => { calls.stroke += 1; };
          if (k === "fill") return () => { calls.fill += 1; };
          return () => {};
        }, set: (o, k, v) => { o[k] = v; return true; }});
        const v = Object.create(G.ReplayViewer.prototype);
        Object.assign(v, {gaps: true, layers: {gaps: true}, gapsFlickers: false, gapsHover: null, number: 2,
          t: 11.5, view: {k: 1}, current: {blob: {players: []}, tracks: {"0": [[{t: 0, u: 5000, v: 3000, yaw: 90},
          {t: 60, u: 5000, v: 3000, yaw: 90}]]}}, gapsReady: {2: {status: "ok", rows: p.rows, chokes: p.chokes}},
          css: (name, fallback) => fallback, nameOf: s => "slot " + s});
        const hits = [];
        v.drawGaps(ctx, 1024, hits);
        const open = hits.length;
        v.layers.gaps = false; v.gapsHover = 2;
        const hovered = [];
        v.drawGaps(ctx, 1024, hovered);
        v.gapsHover = null;
        const off = [];
        v.drawGaps(ctx, 1024, off);
        return {open: open, texts: hits.map(h => h.text), hovered: hovered.map(h => h.x), off: off.length,
                labels: calls.text, arcs: calls.arc};
      }""", {"rows": rows, "chokes": CHOKES}, js=REPLAY_JS)
    assert got["open"] == 2, "the predicted gap and the back-shot are open at 11.5 s; the third is not"
    assert got["texts"][0].startswith("Gap at 10.0 s") and "Mid doors" in got["texts"][0]
    assert got["texts"][1].startswith("Back-shot at 11.0 s")
    assert "Mid doors" in got["labels"], "a choke is drawn with its name"
    assert got["hovered"] == [500], "a hovered list row is drawn even when it is not open and the layer is off"
    assert got["off"] == 0
    assert got["arcs"] >= 4, "rings and the rear wedge"


def render_player(**context):
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    return env.get_template("replays/_player.html").render(replay={"map_name": "Ascent", "round_numbers": [1, 2]},
                                                           **context)


def test_the_partial_has_no_gaps_parts_unless_asked():
    for context in ({"linked": False}, {"linked": True}, {"linked": True, "control": {"cover_reviewed": True}},
                    {"linked": False, "gaps": True}):
        html = render_player(**context)
        assert 'data-replay-tab="gaps"' not in html and 'data-replay-layer="gaps"' not in html
        assert "data-replay-gaps-legend" not in html


def test_the_partial_with_gaps_has_the_tab_checkbox_legend_and_headings():
    html = render_player(linked=True, gaps=True)
    for hook in ('data-replay-tab="gaps"', 'data-replay-panel="gaps"', 'data-replay-layer="gaps"',
                 "data-replay-gaps-legend", "data-replay-gaps-list", "data-replay-gaps-flickers",
                 "data-replay-gaps-status"):
        assert hook in html, hook
    for words in ("the spot behind the player an enemy could have reached unseen", "the 120° they weren't facing",
                  "renamed in the tagger's choke mode", "where a teammate stopped watching", "Solid red",
                  "Dashed amber: nobody used it", "a real shot from behind", "Show flickers (open under 1 s)"):
        assert words in html, words
    box = html.split('data-replay-layer="gaps"', 1)[0].rsplit("<input", 1)[1]
    assert "checked" not in box, "the layer is off by default"


def test_the_list_headings_are_plain_words():
    js = (WEBAPP / "app" / "static" / "js" / "replay.js").read_text(encoding="utf-8")
    for heading in ("When (s after the barriers dropped)", "Exposed player", "Could have been",
                    "Route (chokes crossed)", "Why it opened", "What happened"):
        assert "<th>" + heading + "</th>" in js, heading

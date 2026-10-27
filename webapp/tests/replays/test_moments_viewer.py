"""Critical Moments tab (app/static/js/moments.js, the player partial's tab and panel, replay.html's wiring and
viewer_context's viewer_slot). The JS checks need Node; they skip without it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WEBAPP = HERE.parents[1]
from test_gaps_web import page_db, page_linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

MOMENTS_JS = WEBAPP / "app" / "static" / "js" / "moments.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

READ_STDIN = """
  const M = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const payload = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(payload)));
  });
"""

PLAYERS = {"0": {"name": "Alpha#1", "team": "Red"}, "1": {"name": "Bravo#2", "team": "Red"},
           "5": {"name": "Echo#5", "team": "Blue"}, "6": {"name": "Foxtrot#6", "team": "Blue"}}


def fight(slot=0, i=0, t0=20.0, **kw):
    base = {"slot": slot, "i": i, "t0": t0, "t1": t0 + 4, "opponents": [5], "first": "you", "man": "5v5",
            "kills": [], "died": None, "trade": {"died": None, "by": None, "after_s": None, "avenged": []},
            "moving": {"measured": 0, "above": 0, "first_mps": None},
            "mates": {"state": "alone", "nearest_m": None, "nearest_slot": None, "with_slot": None, "with_m": None},
            "util_on": [], "util_near": [], "smoke_near": [], "util_on_you": [], "mollied": [], "duels": [],
            "kill_detail": [], "death_detail": None, "tags": []}
    base.update(kw)
    return base


def payload(rounds=None, **kw):
    base = {"players": PLAYERS, "attack_round1": [1, 0], "rounds": rounds or {}, "stale": 0,
            "summary": {"0": {"expensive_deaths": [],
                              "medians": {b: {"ttk_shot": None, "ttk_spot": None, "spot_to_shot": None, "n": 0}
                                          for b in ("all", "on", "near", "off")},
                              "info": {"heard": [], "gunfire_outside": [], "found_by_utility": [], "seen": 0}}}}
    base.update(kw)
    return base


def run_node(body: str, data):
    completed = subprocess.run([NODE, "-e", READ_STDIN + body, str(MOMENTS_JS)], input=json.dumps(data),
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_default_slot():
    p = payload()
    got = run_node("""function run(p) { return [M.defaultSlot(p, 5), M.defaultSlot(p, 9), M.defaultSlot(p, null),
      M.defaultSlot(Object.assign({}, p, {attack_round1: []}), null)]; }""", p)
    assert got == [5, 0, 0, 0]
    got = run_node("""function run(p) { return M.defaultSlot(Object.assign({}, p, {attack_round1: [6, 5]}), 3); }""", p)
    assert got == 5


@needs_node
def test_every_fight_is_in_some_group_and_smoke_near_has_its_own():
    # B2, B5: a fight with no shown tag goes to Other fights; a clutch has its own group, and so does smoke near
    rounds = {"1": {"status": "ok", "fights": [
        fight(t0=10, tags=["no_teammate"], man=[1, 3]), fight(t0=20, tags=[]),
        fight(t0=30, tags=["smoke_near"], smoke_near=[{"by": 1, "name": "4_Smoke", "util_kind": "smoke", "on": 5, "m": 6.5},
                                                     {"by": 1, "name": "E_Smoke", "util_kind": "smoke", "on": 5, "m": 3.2}]),
        fight(t0=40, tags=["alone"])]}}
    got = run_node("""function run(p) {
      const g = M.groupsFor(p, 0);
      const rows = k => g.find(x => x.key === k).rows.map(r => r.t0);
      const seen = new Set(); g.forEach(x => x.rows.forEach(r => { if (r.t0 !== undefined) seen.add(r.t0); }));
      return {other: rows("other"), smoke: rows("smoke_near"), clutch: rows("no_teammate"),
              clutchText: M.rowText(g.find(x => x.key === "no_teammate").rows[0], "no_teammate", p.players),
              seen: Array.from(seen).sort((a, b) => a - b),
              text: M.rowText(g.find(x => x.key === "smoke_near").rows[0], "smoke_near", p.players)};
    }""", payload(rounds))
    assert got["other"] == [20] and got["smoke"] == [30] and got["clutch"] == [10]
    assert "1v3 clutch" in got["clutchText"]
    assert got["seen"] == [10, 20, 30, 40]
    assert "2 smokes, nearest 3.2 m" in got["text"]


@needs_node
def test_groups_for_puts_a_two_tag_fight_in_both_groups_in_order():
    rounds = {"2": {"status": "ok", "fights": [fight(t0=40, tags=["died_not_traded", "util_on_them"]),
                                               fight(slot=1, tags=["alone"]),
                                               fight(t0=10, tags=["alone"])]},
              "1": {"status": "ok", "fights": [fight(t0=30, tags=["alone"])]}}
    got = run_node("""function run(p) {
      const g = M.groupsFor(p, 0);
      return {keys: g.map(x => x.key), counts: g.map(x => x.rows.length),
              alone: g.find(x => x.key === "alone").rows.map(r => [r.round, r.t0]),
              labels: g.map(x => x.label), tags: M.GROUPS.map(x => x.tag)};
    }""", payload(rounds))
    assert got["labels"][:3] == ["Moving while firing", "Alone", "With a teammate"]
    assert got["labels"][-2:] == ["Heard", "Found by utility"]
    assert len(got["keys"]) == 22 and got["counts"][0] == 0
    assert got["alone"] == [[1, 30], [2, 10]]
    by_key = dict(zip(got["keys"], got["counts"]))
    assert by_key["died_not_traded"] == 1 and by_key["util_on_them"] == 1 and by_key["alone"] == 3 - 1 + 0
    assert got["keys"][1] == "alone" and got["keys"][2] == "with_teammate"


@needs_node
def test_row_text_for_each_kind_of_group():
    players = PLAYERS
    f = fight(opponents=[5, 6, 9], kills=[{"victim": 5, "t": 21}, {"victim": 6, "t": 22}],
              moving={"measured": 8, "above": 3, "first_mps": 3.1},
              mates={"state": "alone", "nearest_m": 18.5, "nearest_slot": 1, "with_slot": None, "with_m": None})
    died = fight(opponents=[5], died={"t": 25.0, "killer": 5, "control_m2": 1234.4, "share": 0.123},
                 trade={"died": "traded", "by": 1, "after_s": 2.5, "avenged": []},
                 mates={"state": "with", "nearest_m": 6, "nearest_slot": 1, "with_slot": 1, "with_m": 6.2},
                 util_on=[{"util_kind": "blind"}, {"util_kind": "status"}, {}, {"util_kind": "status"}], duels=[{"opponent": 5, "you_saw_t": 20, "they_saw_t": 20.1, "you_angle": 12.0,
                                           "they_angle": 40.5}])
    for r, n in ((f, 7), (died, 3)):
        r["round"] = n
    lonely = fight(mates={"state": "alone", "nearest_m": None, "nearest_slot": None, "with_slot": None,
                          "with_m": None})
    lonely["round"] = 1
    got = run_node("""function run(p) {
      const g = k => M.GROUPS.find(x => x.key === k);
      return {moving: M.rowText(p.f, g("moving"), p.players), alone: M.rowText(p.f, g("alone"), p.players),
        nomate: M.rowText(p.lonely, g("alone"), p.players),
        mate: M.rowText(p.died, g("with_teammate"), p.players), traded: M.rowText(p.died, g("died_traded"), p.players),
        util: M.rowText(p.died, g("util_on_them"), p.players), saw: M.rowText(p.died, g("saw_first"), p.players),
        unseen: M.rowText(p.died, g("died_unseen"), p.players),
        info: M.rowText({round: 4, t: 65, reason: "flash"}, g("found_by_utility"), p.players),
        heard: M.rowText({round: 4, t: 5}, g("heard"), p.players)};
    }""", {"f": f, "died": died, "lonely": lonely, "players": players})
    assert got["moving"].startswith("R7 0:20") and "Echo#5" in got["moving"] and "slot 9" in got["moving"]
    assert "2 kills" in got["moving"] and "3 of 8 shots moving" in got["moving"]
    assert "nearest teammate 18.5 m" in got["alone"]
    assert "no teammate alive" in got["nomate"]
    assert "teammate 6.2 m" in got["mate"]
    assert "died" in got["traded"] and "traded after 2.5 s" in got["traded"]
    assert "team lost 1234 m² (12%)" in got["traded"]
    assert "· utility: 2 status, 1 blind, 1 other ·" in got["util"]
    assert "you 12°, they 40.5°" in got["saw"]
    assert "team lost" in got["unseen"]
    assert got["info"] == "R4 1:05 flash" and got["heard"] == "R4 0:05"


@needs_node
def test_expensive_fight_text_carries_its_sign():
    tf = {"t0": 30.0, "t1": 33.0, "ours": 4, "theirs": 1, "swing_m2": -1551.4, "wipe": False}
    row = fight(died={"t": 30.0, "killer": 5}, tags=["expensive_fight"], team_fight=tf)
    row["round"] = 18
    got = run_node("""function run(p) {
      const g = M.GROUPS.find(x => x.key === "expensive_fight");
      return [M.rowText(p.row, g, p.players), M.swingText(p.up), M.swingText(p.small), M.swingText(p.tiny_loss),
              M.swingText(p.zero), M.swingText(null),
              M.groupsFor(p.payload, 0).find(x => x.key === "expensive_fight").rows.length];
    }""", {"row": row, "players": PLAYERS, "up": dict(tf, swing_m2=812.0), "small": dict(tf, swing_m2=0.2),
           "tiny_loss": dict(tf, swing_m2=-0.2), "zero": dict(tf, swing_m2=0.0),
           "payload": payload({"18": {"status": "ok", "fights": [row]}})})
    assert "fight swing −1,551 m² (4 for 1)" in got[0]
    assert got[1:6] == ["fight swing +812 m² (4 for 1)", "fight swing +0.2 m² (4 for 1)",
                        "fight swing −0.2 m² (4 for 1)", "fight swing 0 m² (4 for 1)", ""]
    assert got[6] == 1


@needs_node
def test_summary_line():
    s = payload()["summary"]["0"]
    s["medians"]["all"] = {"ttk_shot": 0.42, "ttk_spot": None, "spot_to_shot": 0.3, "n": 12}
    s["info"] = {"heard": [{}, {}], "gunfire_outside": [{}], "found_by_utility": [], "seen": 4}
    got = run_node("function run(p) { return M.summaryLine(p); }", s)
    s["medians"]["all"]["ttk_spot"] = 0.8
    got = run_node("function run(p) { return M.summaryLine(p); }", s)
    assert "TTK from first shot: all 0.42 s (n 12)" in got and "on – (n 0)" in got
    assert "TTK from spot: all 0.80 s (n 12), on – (n 0)" in got
    assert got.index("TTK from first shot") < got.index("TTK from spot")
    assert got.endswith("heard 2, gunfire outside fights 1, found by utility 0, seen 4")


@needs_node
def test_notes_name_failed_rounds_beside_stale_ones():
    # C5: a failed round's fights are missing, which the tab must not show as "no fights"
    got = run_node("""function run(p) { return [M.notes(p.a), M.notes(p.b), M.notes(p.c)]; }""",
                   {"a": payload(stale=2, failed=[3, 7]), "b": payload(failed=[5]), "c": payload()})
    assert got[0] == ["2 rounds not computed yet",
                      "Moments could not be computed for rounds 3, 7: their fights are missing below"]
    assert got[1] == ["Moments could not be computed for round 5: their fights are missing below"]
    assert got[2] == []


@needs_node
def test_jump_target():
    got = run_node("""function run(p) { return [M.jumpFor({round: 3, t0: 42}), M.jumpFor({round: 3, t0: 2}),
      M.jumpFor({round: 2, t: 9}), M.jumpFor({round: 2, t: 9, t0: undefined})]; }""", {})
    assert got == [[3, 39], [3, 0], [2, 9], [2, 9]]


def render_player(**context):
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    return env.get_template("replays/_player.html").render(replay={"map_name": "Ascent", "round_numbers": [1, 2]},
                                                           **context)


def test_the_tab_and_panel_render_only_for_linked_with_control():
    html = render_player(linked=True, control={"cover_reviewed": True})
    for hook in ('data-replay-tab="moments"', 'data-replay-panel="moments"', "data-replay-moments", "Critical Moments",
                 "Measurements, not verdicts."):
        assert hook in html, hook
    assert html.index('data-replay-tab="analysis"') < html.index('data-replay-tab="moments"')
    for context in ({"linked": False, "control": {"cover_reviewed": True}}, {"linked": True}, {"linked": False}):
        html = render_player(**context)
        assert 'data-replay-tab="moments"' not in html and 'data-replay-panel="moments"' not in html


def render_replay(**context):
    from app.templates import templates
    from test_replay_routes import request

    base = {"replay": {"map_name": "Ascent", "round_numbers": [1], "match_url": None}, "replay_data": {"match": {}},
            "start_round": 1, "start_t": None, "linked": True, "gaps": False, "viewer_slot": 3}
    base.update(context)
    return templates.env.get_template("replays/replay.html").render(**base, request=request())


def test_replay_html_wires_moments_only_with_control():
    html = render_replay(control={"cover_reviewed": True})
    assert "/static/js/moments.js" in html and "loadMoments" in html and "/moments.json" in html
    assert "momentsViewerSlot: 3" in html
    html = render_replay(control=None)
    assert "moments.js" not in html and "loadMoments" not in html and "momentsViewerSlot" not in html


def test_viewer_context_has_the_logged_in_players_slot(page_db, page_linked):
    from starlette.requests import Request

    from app.models import Player
    from app.routers import replays as routes
    from app.services import replays as service

    players = service.page_context(page_db, page_linked)["players"]
    entry = next(iter(players.values()))

    def ctx(session):
        req = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [],
                       "session": session})
        return routes.viewer_context(req, page_db, page_linked)

    assert ctx({"player_id": entry["player_id"]})["viewer_slot"] == entry["slot"]
    assert ctx({})["viewer_slot"] is None
    assert ctx({"player_id": 987654})["viewer_slot"] is None
    assert page_db.get(Player, entry["player_id"]) is not None

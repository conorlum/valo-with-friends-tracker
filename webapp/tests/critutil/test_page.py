"""Critical Util's page shaping (app/services/replay_util.py page_for), names, areas, and the viewer JS in node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import replay_util

JS = Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "critutil.js"


def row(pid, value, i, match=1, rnd=1, ability="Flashpoint", area="A", **kw):
    base = {"replay": 1, "uuid": "u", "map": "Ascent", "match": match, "round": rnd, "i": i, "slot": 0,
            "player_id": pid, "agent": "Breach", "ability": ability, "kind": "blind", "kinds": ["blind"],
            "side": "attack", "area": area, "phase": "contact", "t": 20.0, "buy": "full buy", "won": True,
            "value": value, "ledger": value, "jobs": 0.0, "wasted": False, "harm": 0, "harm_died": 0,
            "followed": True, "ult_points": None, "enemies": 1, "features": {}, "combo": [], "scored": True,
            "swing": value, "base": 0.0, "base_n": 40, "base_level": "state and phase", "lift": value}
    base.update(kw)
    return base


def corpus(rows, personal=None):
    return {"rows": rows, "ability_prior": {"Flashpoint": 0.0}, "spread": 0.01, "fit": {"fitted": False},
            "estimator": {}, "personal": personal or {}}


def test_page_is_friends_only_and_lineups_need_five_casts():
    rows = [row(1, 0.03, i, match=1 + i % 3, rnd=i) for i in range(6)] + [row(2, 0.05, i, rnd=i) for i in range(6)]
    page = replay_util.page_for(corpus(rows), {1})
    assert {c["player_id"] for c in page["lineups"]} == {1}
    (c,) = page["lineups"]
    assert c["n"] == 6 and c["matches"] == 3 and c["call"] == "this wins rounds, keep throwing it"
    assert set(page["players"]) == {1}
    few = replay_util.page_for(corpus([row(1, 0.03, i, rnd=i) for i in range(4)]), {1})
    assert few["lineups"] == []


def test_a_lineup_is_scored_against_the_no_util_baseline_not_the_abilitys_average():
    # credited value is high, but what followed these casts was no better than moments without util: no clear value
    rows = [row(1, 0.05, i, rnd=i, swing=0.01, lift=0.0) for i in range(6)]
    c = replay_util.page_for({**corpus(rows), "ability_prior": {"Flashpoint": 0.05}}, {1})["lineups"][0]
    assert c["value"] == 5.0 and c["prior"] == 5.0
    assert c["score"] == pytest.approx(100 * 5 * 0.05 / 11, abs=0.01), "shrunk toward the prior, not minus it"
    # without five compared casts there is no score and no call
    rows = [row(1, 0.05, i, rnd=i, lift=None) for i in range(6)]
    c = replay_util.page_for(corpus(rows), {1})["lineups"][0]
    assert c["score"] is None and c["call"] == "no comparison yet" and c["compared"] == 0
    # the build no longer carries personal tables (W18; W21 rebuilds Yours on the page): the column reads None
    c = replay_util.page_for(corpus([row(1, 0.03, i, rnd=i) for i in range(6)]), {1})["lineups"][0]
    assert c["personal"] is None


def test_casts_after_the_round_was_decided_stay_out_of_the_page():
    rows = [row(1, 0.03, i, rnd=i) for i in range(5)] + [row(1, 0.0, 9, scored=False, lift=None)]
    page = replay_util.page_for(corpus(rows), {1})
    assert page["n"] == 5 and page["lineups"][0]["n"] == 5


def test_player_block_best_worst_and_patterns():
    rows = [row(1, v, i, rnd=i, wasted=(v == 0)) for i, v in enumerate([0.04, -0.02, 0.0, 0.01, 0.0])]
    block = replay_util.page_for(corpus(rows), {1})["players"][1]
    assert block["best"][0]["value"] == 4.0 and block["worst"][0]["value"] == -2.0
    assert block["patterns"]["waste_rate"] == 40.0 and block["patterns"]["support_rate"] == 100.0


def test_ult_value_per_point():
    rows = [row(1, 0.10, i, rnd=i, kinds=["ult"], kind="ult", ability="Rolling Thunder", ult_points=8)
            for i in range(3)]
    (u,) = replay_util.page_for(corpus(rows), {1})["ults"]
    assert u["value"] == 10.0 and u["per_point"] == 1.25


def test_ability_names_come_from_the_viewer_table():
    assert replay_util.ability_label({"agent": "Phoenix", "codes": ["Phoenix"], "names": ["MolotovFire"]}) == "Hot Hands"
    assert replay_util.ability_label({"codes": [], "names": ["Unknown_Thing"]}) == "Unknown_Thing"
    assert replay_util.ability_label({"agent": "Phoenix", "names": ["phoenix_curveball_left"]}) == "Curveball"
    assert replay_util.ability_label({"agent": "Breach", "names": [None]}) == "utility"


def test_movement_is_never_wasted():
    from app.services import util_values

    dash = {"kinds": ["teleport"], "enemies": [], "kills": []}
    assert util_values.is_wasted(dash, 0.0, util_values.features(dash)) is False


def test_economy_conventions_match_their_sources():
    from app.scoring.impact import FULL_BUY_THRESHOLD, econ_tier_name
    from app.services.economy_graphs import PISTOL_ROUNDS

    assert replay_util.FULL_BUY_THRESHOLD == FULL_BUY_THRESHOLD
    assert econ_tier_name(FULL_BUY_THRESHOLD) == "FULL_BUY" and econ_tier_name(FULL_BUY_THRESHOLD - 1) != "FULL_BUY"
    assert set(replay_util.PISTOL_ROUNDS) == set(PISTOL_ROUNDS)


def test_buy_type_and_winner_side():
    assert replay_util.buy_type(1, 5000) == "pistol" and replay_util.buy_type(13, None) == "pistol"
    assert replay_util.buy_type(5, 4300) == "full buy" and replay_util.buy_type(5, 2000) == "other"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_viewer_text_is_measured_and_signed():
    script = f"""
const u = require({json.dumps(str(JS))});
const payload = {{players: {{"0": {{name: "Me"}}, "5": {{name: "the enemy Jett"}}}},
  rounds: {{"3": {{casts: [{{i: 0, slot: 0, t: 61.2, ability: "Flashpoint", kind: "blind", value: 2.5,
    effects: [{{target: 5, how: "blind"}}], kills: [{{ours: true}}], followed: true, harm: []}}],
    unused: [{{slot: 0, t: 70, kind: "held_at_death", kinds: ["blind"], abilities: ["Flashpoint"]}}]}}}},
  summary: {{}}, stale: 0, failed: []}};
const rows = u.castsFor(payload, 0);
console.log(JSON.stringify([u.rowText(rows[0], payload.players), u.signed(-1.5), u.unusedText(u.unusedFor(payload, 0)[0]),
  u.groupsFor(payload, 0).filter(g => g.rows.length).map(g => g.key)]));
"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert out[0] == "R3 1:01 · Flashpoint · blinded the enemy Jett; 1 kill on affected enemies; followed up · +2.50 pp"
    assert out[1] == "−1.50"
    assert out[2] == "R3 1:10 · died holding Flashpoint after engaging first"
    assert out[3] == ["blind"]


def _node(expr, rows):
    script = f"""
const u = require({json.dumps(str(JS))});
const players = {{"0": {{name: "Me"}}}};
const base = {{round: 3, t: 61.2, ability: "Flashpoint", kind: "blind", value: 2.5}};
const rows = {json.dumps(rows)}.map(r => Object.assign({{}}, base, r));
console.log(JSON.stringify({expr}));
"""
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                                     check=True).stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_viewer_lift_text_by_mode():
    both = {"lift_yours": 1.5, "yours_level": "your state", "lift_all": 0.5, "base_level": "state", "gate_all": True,
            "gate_yours": True}
    only_all = {"lift_all": 0.5, "base_level": "state", "gate_all": True}
    ungated = {"lift_all": 0.5, "base_level": "state", "gate_all": False}
    old = {"lift": 0.5, "base_level": "state"}
    out = _node("[u.rowText(rows[0], players), u.rowText(rows[0], players, 'all'), u.rowText(rows[2], players),"
                " u.rowText(rows[3], players, 'all'), u.rowText(rows[4], players), u.rowText(rows[5], players)]",
                [both, both, only_all, only_all, ungated, old])
    assert out[0].endswith("+2.50 pp · +1.50 pp vs your rounds without it (your state)")
    assert out[1].endswith("+2.50 pp · +0.50 pp vs all players' rounds without it (state)")
    assert out[2].endswith("+0.50 pp vs all players' rounds without it (state) · no personal comparison yet: all players shown")
    assert out[3].endswith("vs all players' rounds without it (state)") and "no personal" not in out[3]
    assert out[4].endswith("+2.50 pp · baseline not validated for this window")
    assert out[5].endswith("+2.50 pp · +0.50 pp vs no util (state)")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_viewer_label_other_group_heal_and_omen_text():
    out = _node("[u.rowText(rows[0], players), u.rowText(rows[1], players), u.rowText(rows[2], players),"
                " u.rowText(rows[3], players), u.rowText(rows[4], players),"
                " u.groupsFor({players, rounds: {'1': {casts: rows.map((r, i) => Object.assign({slot: 0, i: i}, r))}}}, 0)"
                ".filter(g => g.key === 'other')[0].rows.length]",
                [{"label": "Dart"}, {"heal": {"target": 0, "kept_alive": True, "hits_after": 2}, "kind": "heal"},
                 {"heal": {"target": 0, "kept_alive": False, "fought": True}, "kind": "heal"},
                 {"signals": {"got_spike": True, "got_spike_planted": True}},
                 {"signals": {"got_spike": True}, "kind": "wall"}])
    assert "Flashpoint (Dart)" in out[0]
    assert "healed Me, kept them alive; survived 2 more hits (to within one damage run)" in out[1]
    assert "healed Me, before a fight" in out[2]
    assert "got the spike, then planted" in out[3]
    assert out[4].count("got the spike") == 1 and "planted" not in out[4]
    assert out[5] == 1

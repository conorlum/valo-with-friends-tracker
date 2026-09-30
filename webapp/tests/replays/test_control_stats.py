"""Stage 2 per-player control, coverage, stats and heatmap sections (app/control/engine.py;
docs/replay-map-control-plan.md, "Per-player control" and "Heatmaps") on toy maps."""

import numpy as np
import pytest

from app.control import engine as ce
from tests.replays.control_toys import blob, door_hall, midwall_hall, open_hall

A_OWN = (ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE)
B_OWN = (ce.B_PASSIVE, ce.B_SAFE, ce.B_ACTIVE)


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


def test_signed_scoring():
    state = np.array([ce.NONE, ce.A_PASSIVE, ce.A_ACTIVE, ce.B_SAFE, ce.CONTESTED, ce.CONTESTED_ACTIVE], np.uint8)
    assert ce.score(state, "A").tolist() == [0, 1, 1, -1, 0, 0]
    assert ce.score(state, "B").tolist() == [0, -1, -1, 1, 0, 0]
    before = np.array([ce.A_SAFE, ce.A_SAFE, ce.CONTESTED], np.uint8)
    after = np.array([ce.B_SAFE, ce.CONTESTED, ce.B_ACTIVE], np.uint8)
    assert (ce.score(before, "A") - ce.score(after, "A")).tolist() == [2, 1, 1], "ours->theirs costs 2"


def test_coverage_is_split_evenly_and_redundant_holders_control_nothing():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 1: still("A", 150, 200, 0), 5: still("B", 400, 110, 0)}
    tk = ce.Tick(ce.RoundInputs(blob(players), geo), 1.0)
    cov = tk.coverage()
    union_act = (tk.holders[0].active | tk.holders[1].active).sum()
    assert cov[0][0] == pytest.approx(union_act / 2) and cov[1][0] == pytest.approx(union_act / 2)
    assert cov[0][0] + cov[1][0] == pytest.approx(union_act)
    rc = ce.compute_round(blob(players, t_end=1.0), geo)
    assert np.nan_to_num(rc.control[:, [0, 1]]).max() == 0, "either one's removal loses nothing"
    owned = sum(float(np.isin(rc.states[i], A_OWN).sum()) * rc.cell_m2 * w for i, w in enumerate(rc.weights))
    assert rc.redundant_m2s["A"] == pytest.approx(owned), "all of it is redundant"


def test_the_last_player_controls_only_what_their_team_loses():
    geo = open_hall()
    rc = ce.compute_round(blob({0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}, t_end=1.0), geo)
    owned = np.isin(rc.states[0], A_OWN).sum() * rc.cell_m2
    assert rc.control[0, 0] == pytest.approx(owned), "no terminal flip: the enemy's gain doesn't count"


def rotator_and_lurker():
    """A0 holds the door; A1 turns circles in the safe west room behind him; A2 lurks in the far
    corner of B's side, behind B, watching B's ground. B looks north-west, away from the door."""
    return {0: still("A", 204, 292, 0), 1: still("A", 120, 150, 180), 2: still("A", 400, 280, 240),
            5: still("B", 300, 200, 225)}


def test_the_spawn_rotator_controls_nothing_and_the_lurker_a_lot():
    geo = door_hall()
    rc = ce.compute_round(blob(rotator_and_lurker(), t_end=1.0, deaths={1: 0.8, 2: 0.9}), geo, full_every=1)
    assert rc.control[0, 1] == 0, "a player rotating through safe space costs the team nothing"
    assert rc.control[0, 2] > 0
    assert rc.players[1].deaths[0]["control_m2"] == 0
    # most of the lurker's value is denial: without them, B's own space grows
    tk = ce.Tick(ce.RoundInputs(blob(rotator_and_lurker()), geo), 0.0)
    base, gone = tk.compose()["state"], tk.compose(removed=2)["state"]
    assert np.isin(gone, B_OWN).sum() > np.isin(base, B_OWN).sum(), "both teams are recomputed"
    denial = ((ce.score(base, "A") - ce.score(gone, "A")) > 0) & ~np.isin(base, A_OWN)
    assert denial.sum() > 0
    assert rc.cf_check["identical"] == rc.cf_check["player_ticks"] > 0, "incremental equals full"


def test_a_removed_entry_who_bridged_their_fill_is_recomputed():
    geo = midwall_hall()
    players = {0: still("A", 150, 170, 0), 1: still("A", 120, 260, 0), 5: still("B", 220, 170, 180),
               6: still("B", 380, 130, 90)}
    rc = ce.compute_round(blob(players, t_end=1.0), geo, full_every=1)
    assert rc.timings["branches"].get("own_fill_bridge_recomputed", 0) > 0
    assert rc.cf_check["identical"] == rc.cf_check["player_ticks"]


def test_sections_split_at_the_plant_and_cover_the_live_round_only():
    geo = open_hall()
    bomb = {"k": "ability", "t": 12.3, "t1": None, "by": 0, "kind": "Bomb", "code": "", "name": "Spike",
            "u": 3000, "v": 3000}
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}
    rc = ce.compute_round(blob(players, t_end=30.0, t_decided=25.0, util=[bomb]), geo, ce.ControlLink(
        sides={0: "attack", 5: "defense"}))
    got = [(s.key, s.t0, s.t1) for s in rc.sections]
    assert got == [("r0", 0.0, 10.0), ("r1", 10.0, 12.3125), ("p0", 12.3125, 22.3125), ("p1", 22.3125, 25.0)]
    n_walk = len(rc.walk_cells)
    for s in rc.sections:
        assert s.seconds == pytest.approx(s.t1 - s.t0)
        assert float(s.totals.sum()) == pytest.approx(s.seconds * n_walk, rel=1e-5)
    assert sum(s.seconds for s in rc.sections) == pytest.approx(25.0)
    assert rc.players[0].alive_s == pytest.approx(25.0), "nothing after the round is decided"
    assert rc.group_side == {"A": "attack", "B": "defense"}


def test_lost_control_at_a_death_and_the_redundant_total():
    geo = door_hall()
    rc = ce.compute_round(blob(rotator_and_lurker(), t_end=2.0, deaths={2: 1.2}), geo)
    [death] = rc.players[2].deaths
    i = rc.ticks.tolist().index(1.1875)
    assert death["control_m2"] == pytest.approx(float(rc.control[i, 2]), abs=0.1)
    area = death["control_m2"]
    assert 0 < death["share_of_team"]
    lost_area = sum(death["went_to_m2"].values())
    assert sum(death["by_level_m2"].values()) == pytest.approx(lost_area, abs=0.5)
    assert lost_area <= area + 0.5, "ours->theirs counts twice in control, once in area"
    # redundant = the team's own area minus its players' control, over the live ticks
    expect = 0.0
    for n, w in enumerate(rc.weights):
        alive = [s for s in (0, 1, 2) if not np.isnan(rc.control[n, s])]
        owned = np.isin(rc.states[n], A_OWN).sum() * rc.cell_m2
        expect += (owned - sum(float(rc.control[n, s]) for s in alive)) * w
    assert rc.redundant_m2s["A"] == pytest.approx(expect, rel=1e-4)
    assert rc.players[2].alive_s == pytest.approx(1.2)
    stats = rc.players[2].as_dict()
    assert stats["control_m2"] == pytest.approx(rc.players[2].control_m2s / 1.2, abs=0.1)
    assert stats["active_ratio"] is not None and 0 <= stats["active_ratio"] <= 1

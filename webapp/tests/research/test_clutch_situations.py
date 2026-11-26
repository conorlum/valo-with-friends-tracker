"""Offline contracts for the pure clutch research measurements."""

import copy
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import clutch_measures as measures

from app.coach.roundview import RoundView


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def context(steps, *, winner="team-2", decided=100):
    """Page JSON shape, using only slots and synthetic match metadata."""
    players = {str(s): {"side": "A" if s < 5 else "B",
                        "team": "team-1" if s < 5 else "team-2"} for s in range(10)}
    ctx = {
        "match": {"uuid": "00000000-0000-0000-0000-000000000001",
                  "side_to_team": {"A": "team-1", "B": "team-2"}, "clock_offset": 0},
        "players": players,
        "rounds": {"1": {"alive_steps": steps, "db": {"winner": winner},
                          "db_deaths": [], "stats": {}}},
    }
    alive = {}
    for s in range(10):
        index, rank = (1 if s < 5 else 2), s % 5
        death = next((t for t, *counts in steps if counts[index - 1] <= rank), None)
        alive[str(s)] = [[0, death, "kill" if death is not None else None]]
    blob = {"round": 1, "hz": 16, "t_start": 0, "t_end": 110,
            "t_decided": decided, "players": [{"slot": int(s), **p} for s, p in players.items()],
            "alive": alive, "tracks": {}, "util": [], "kills": [], "player_state": {}}
    return ctx, blob


def views(blob):
    view = RoundView(blob, {p["slot"]: str(p["slot"]) for p in blob["players"]}, 1, None)
    rnd = SimpleNamespace(tracks={}, geo=SimpleNamespace(m_per_px=0.14))
    return view, rnd


def window(t=40, end=100, slot=0):
    return {"slot": slot, "t_s": t, "t_end_window": end, "side": "attack", "n_enemies": 3}


def test_sides_are_db_free_and_follow_regulation_and_overtime():
    code = """
import sys
sys.path.insert(0, 'scripts')
import clutch_measures as m
ctx = {'match': {'side_to_team': {'A': 'team-1', 'B': 'team-2'}},
       'players': {'0': {'side': 'A'}, '5': {'side': 'B'}}}
for n, role in [(1, 'attack'), (13, 'defense'), (25, 'attack'), (26, 'defense')]:
    result = m.sides(ctx, n)
    assert result['0'] == role
    assert result['5'] == ('defense' if role == 'attack' else 'attack')
assert m.sides(ctx, 0) == {}
assert not {'app.db', 'app.models', 'app.scoring.plant_window', 'preview_control_live'} & sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_first_clutch_retains_start_n_and_ends_at_death():
    ctx, blob = context([[0, 5, 5], [10, 1, 3], [20, 1, 2], [30, 0, 2]])
    before = copy.deepcopy((ctx, blob))
    rows, cutoffs = measures.situations(ctx, 1, blob)
    assert len(rows) == 1
    assert rows[0] == {
        "situation_id": f"{ctx['match']['uuid']}:1:team-1:1vN", "kind": "1vN",
        "team": "team-1", "side": "attack", "slot": 0, "n_enemies": 3,
        "own_alive": 1, "t_s": 10, "t_end_window": 30, "won": False,
        "alive_at_end": False, "group": "1v3+", "n_at_start": 3,
    }
    assert cutoffs == []
    assert (ctx, blob) == before


def test_both_teams_enter_1v1_with_round_outcome():
    ctx, blob = context([[0, 5, 5], [20, 1, 1]])
    rows, _ = measures.situations(ctx, 1, blob)
    assert [(r["group"], r["won"], r["alive_at_end"]) for r in rows] == [
        ("1v1", False, True), ("1v1", True, True)]


@pytest.mark.parametrize("entry", [40, 41])
@pytest.mark.parametrize("n_enemies", [3, 4, 5])
def test_first_entry_at_or_after_decision_only_reconciles(entry, n_enemies):
    ctx, blob = context([[0, 5, 5], [entry, 1, n_enemies], [50, 1, 2]], decided=40)
    assert measures.situations(ctx, 1, blob) == ([], [{
        "situation_id": f"{ctx['match']['uuid']}:1:team-1:1vN",
        "team": "team-1", "kind": "1vN", "n_enemies": n_enemies,
        "group": "1v3+", "won": False, "t": entry,
    }])


def test_two_player_cutoff_counts_one_situation_and_does_not_repeat():
    ctx, blob = context([[0, 5, 5], [40, 2, 3], [50, 2, 2]], decided=40)
    assert measures.situations(ctx, 1, blob) == ([], [
        {"situation_id": f"{ctx['match']['uuid']}:1:team-1:2vN",
         "team": "team-1", "kind": "2vN", "n_enemies": 3,
         "group": "2v3", "won": False, "t": 40},
        {"situation_id": f"{ctx['match']['uuid']}:1:team-2:2vN",
         "team": "team-2", "kind": "2vN", "n_enemies": 2,
         "group": "2v2", "won": True, "t": 50},
    ])


def test_four_enemy_pair_waits_for_three_and_five_enemy_clutch_keeps_exact_n():
    ctx, blob = context([[0, 5, 5], [5, 2, 4], [10, 2, 3], [15, 1, 3]])
    rows, _ = measures.situations(ctx, 1, blob)
    assert [(r["kind"], r["t_s"]) for r in rows if r["team"] == "team-1"] == [
        ("2vN", 10), ("2vN", 10), ("1vN", 15)]
    ctx, blob = context([[0, 5, 5], [5, 1, 5]])
    rows, _ = measures.situations(ctx, 1, blob)
    assert (rows[0]["n_enemies"], rows[0]["n_at_start"], rows[0]["group"]) == (5, 5, "1v3+")


def test_two_player_entries_share_id_and_can_precede_a_clutch():
    ctx, blob = context([[0, 5, 5], [5, 2, 3], [9, 2, 2], [12, 1, 2]])
    rows, _ = measures.situations(ctx, 1, blob)
    own = [r for r in rows if r["team"] == "team-1"]
    enemy = [r for r in rows if r["team"] == "team-2"]
    pair = [r for r in own if r["kind"] == "2vN"]
    assert [(r["slot"], r["group"], r["t_s"], r["t_end_window"]) for r in pair] == [
        (0, "2v3", 5, 100), (1, "2v3", 5, 12)]
    assert pair[0]["situation_id"] == pair[1]["situation_id"]
    assert [(r["t_s"], r["n_enemies"]) for r in own if r["kind"] == "1vN"] == [(12, 2)]
    assert [(r["group"], r["t_s"]) for r in enemy] == [("2v2", 9), ("2v2", 9)]
    assert enemy[0]["situation_id"] == enemy[1]["situation_id"]


def test_death_does_not_change_spike_win_or_extend_window_through_revive():
    ctx, blob = context([[0, 5, 5], [10, 1, 3], [30, 0, 3]], winner="team-1")
    blob["alive"]["0"] = [[0, 30, "kill"], [50, None, None]]
    rows, _ = measures.situations(ctx, 1, blob)
    assert rows[0]["won"] is True
    assert rows[0]["alive_at_end"] is True
    assert rows[0]["t_end_window"] == 30
    blob["alive"]["0"] = [[0, 30, "kill"]]
    rows, _ = measures.situations(ctx, 1, blob)
    assert rows[0]["won"] is True
    assert rows[0]["alive_at_end"] is False


def test_db_only_death_is_clock_adjusted_and_excluded_from_clutch_slots():
    ctx, blob = context([[0, 5, 5], [10, 1, 3]])
    ctx["match"]["clock_offset"] = 2.125
    ctx["rounds"]["1"]["db_deaths"] = [
        {"slot": 0, "t_db": 12.1254}, {"slot": None, "t_db": 1}]
    blob["alive"]["0"] = [[0, None, None]]
    blob["alive"]["1"] = [[0, None, None]]
    assert measures.db_deaths(ctx, 1) == [[0, 10.0]]
    rows, _ = measures.situations(ctx, 1, blob)
    assert [r["slot"] for r in rows] == [1]


def test_side_to_team_mapping_is_authoritative():
    ctx, blob = context([[0, 5, 5], [10, 1, 3]])
    ctx["match"]["side_to_team"] = {"A": "team-2", "B": "team-1"}
    # Replay slots 5..9 now belong to team-1; page counts remain team-indexed.
    for slot in range(10):
        source = (slot + 5) % 10
        blob["alive"][str(slot)] = copy.deepcopy(context([[0, 5, 5], [10, 1, 3]])[1]["alive"][str(source)])
    rows, _ = measures.situations(ctx, 1, blob)
    assert rows[0]["slot"] == 5
    assert rows[0]["side"] == "attack"


def test_clock_before_and_after_plant_and_missing_engagement():
    _, blob = context([[0, 5, 5]])
    blob["player_state"]["spike"] = [{"t": 0, "s": "carried"}, {"t": 50, "s": "planted"}]
    view, rnd = views(blob)
    assert measures.clock(view, rnd, blob, window())["time_left_start"] == 60
    result = measures.clock(view, rnd, blob, window(60))
    assert result["time_left_start"] == 35
    assert result["time_left_first_engagement"] is None
    assert result["still_share"] is None


def test_plant_attempts_use_start_clock_and_require_confirmed_completion():
    _, blob = context([[0, 5, 5]])
    blob["player_state"] = {
        "plants": [[39, 43, 0], [40, 41, 1], [90, 94, 0], [96, 97, 0], [97, None, 0], [100, 104, 0]],
        "spike": [{"t": 104, "s": "planted", "slot": 0}],
    }
    view, rnd = views(blob)
    attempts = measures.clock(view, rnd, blob, window())["plant_attempts"]
    assert [(r["t"], r["enough_time"], r["finished"]) for r in attempts] == [
        (90, True, False), (96, True, False), (97, False, False)]
    blob["player_state"]["spike"] = [{"t": 94, "s": "planted", "slot": 0}]
    assert measures.clock(view, rnd, blob, window())["plant_attempts"][0]["finished"] is True


def test_defuse_attempts_use_coach_results_and_filter_player_and_window():
    _, blob = context([[0, 5, 5]])
    blob["player_state"]["spike"] = [{"t": 50, "s": "planted"}]
    blob["util"] = [{"k": "ability", "kind": "Bomb", "t": 50, "t1": 100,
                     "defuses": [[59, 60, 0, False], [85, 86, 1, False],
                                 [87.5, 91.5, 0, False], [89, 90, 0, False],
                                 [90, 91, 0, False], [92, 95, 0, True], [100, 101, 0, False]]}]
    view, rnd = views(blob)
    attempts = measures.clock(view, rnd, blob, window(60))["defuse_attempts"]
    assert [(r["t"], r["enough_time"], r["reached_half"], r["result"]) for r in attempts] == [
        (87.5, True, True, "half"), (89, False, False, "tap"),
        (90, False, False, "tap"), (92, False, True, "defused")]
    assert all(r["by"] == 0 for r in attempts)
    # Exactly seven seconds is enough, even after a previous half.
    blob["util"][0]["defuses"] = [[88, 89, 0, False]]
    assert measures.clock(view, rnd, blob, window(60))["defuse_attempts"][0]["enough_time"] is True


def test_unfinished_defuse_uses_death_cap_and_is_never_defused():
    _, blob = context([[0, 5, 5]])
    blob["alive"]["0"] = [[0, 91, "kill"]]
    blob["util"] = [{"k": "ability", "kind": "Bomb", "t": 50, "t1": 100,
                     "defuses": [[87, None, 0, True]]}]
    view, rnd = views(blob)
    attempt = measures.clock(view, rnd, blob, window(60, 91))["defuse_attempts"][0]
    assert (attempt["t1"], attempt["seconds"], attempt["result"], attempt["until"]) == (91, 4, "half", "death")
    assert attempt["reached_half"] is True
    assert attempt["enough_time"] is True


def test_still_share_on_toy_track_and_only_alive_window_samples():
    _, blob = context([[0, 5, 5]])
    blob["alive"]["0"] = [[0, 4, "kill"]]
    view, rnd = views(blob)
    ts = np.arange(0, 6 + 1 / 16, 1 / 16)
    # Two seconds still, then 5 m/s; coordinates are the engine's 0..10000 units.
    xs = np.maximum(ts - 2, 0) * 5 / rnd.geo.m_per_px / 1024 * 10000
    rnd.tracks[0] = (ts, xs, np.zeros_like(ts), np.zeros_like(ts))
    result = measures.clock(view, rnd, blob, window(0, 6))
    assert result["still_share"] == pytest.approx(0.5, abs=1 / 64)


def hold(t, slot, gun="Vandal", cat="rifle"):
    return {"k": "hold", "t": t, "by": slot, "gun": gun, "cat": cat}


@pytest.mark.parametrize("mode,expected", [("null", None), ("revive", None), ("shot", 2)])
def test_state_guns_follow_coach_life_and_null_hold_rule(mode, expected):
    ctx, blob = context([[0, 5, 5]])
    blob["util"] = [hold(0, 0), {"k": "shot", "t": 39, "by": 0, "gun": "Vandal"}]
    if mode == "null":
        blob["util"].append(hold(30, 0, None, "ability"))
    elif mode == "revive":
        blob["alive"]["0"] = [[0, 20, "kill"], [30, None, None]]
    else:
        blob["util"] = blob["util"][1:]
    view, rnd = views(blob)
    result = measures.state(view, rnd, blob, ctx, 1, window())
    assert result["gun_tier"] == expected


@pytest.mark.parametrize("stats_shape", ["dict", "string", "row_strings"])
def test_state_enemies_vitals_loadouts_and_spike_at_start(stats_shape):
    ctx, blob = context([[0, 5, 5]])
    blob["alive"]["9"] = [[0, 39, "kill"]]
    blob["util"] = [hold(0, 0), hold(0, 5, "Ghost", "sidearm"), hold(0, 6, "Spectre", "smg"),
                    hold(0, 7, None, "ability"), hold(0, 8), hold(0, 9)]
    blob["player_state"] = {
        "vitals": {"0": [{"t": 0, "life": 0, "hp": 100, "sh": 50, "mhp": 100, "msh": 50},
                          {"t": 40, "life": 0, "hp": 60, "sh": 20, "mhp": 100, "msh": 50},
                          {"t": 41, "life": 0, "hp": 10, "sh": 0, "mhp": 100, "msh": 50}]},
        "spike": [{"t": 0, "s": "carried"}, {"t": 50, "s": "planted"}],
    }
    stats = {"0": {"loadout": 3000, "remaining": 0}, "1": {"loadout": 2000},
             "5": {"loadout": 1000}, "9": {"loadout": 5000}}
    ctx["rounds"]["1"]["stats"] = (str(stats) if stats_shape == "string" else
                                         {s: str(r) for s, r in stats.items()} if stats_shape == "row_strings" else stats)
    view, rnd = views(blob)
    result = measures.state(view, rnd, blob, ctx, 1, window())
    assert result == {"gun_tier": 2, "enemy_gun_tiers": {5: 0, 6: 1, 7: None, 8: 2},
                      "hp_shield": 80, "team_loadouts": {"team-1": 5000, "team-2": 6000},
                      "spike_state": "carried", "n_enemies": 3, "side": "attack"}
    # Missing observations stay unknown, and missing stats are not zero-valued loadouts.
    blob["player_state"] = {}
    ctx["rounds"]["1"]["stats"] = {}
    result = measures.state(view, rnd, blob, ctx, 1, window())
    assert result["hp_shield"] is None
    assert result["spike_state"] is None
    assert result["team_loadouts"] == {}


@pytest.mark.parametrize("mode,expected", [
    ("untouched", 150), ("unknown_shield", None), ("damaged", None),
    ("no_vitals", None), ("empty_slot", None), ("before_entry", 80),
])
def test_state_hp_start_before_first_vitals_event(mode, expected):
    ctx, blob = context([[0, 5, 5]])
    events = [{"t": 45, "life": 0, "hp": 60, "sh": 0, "mhp": 100, "msh": 50},
              {"t": 55, "life": 0, "hp": 40, "sh": 0, "mhp": 100, "msh": 0}]
    ps = blob["player_state"] = {"vitals": {"0": events}, "damage_taken": {"0": [45, 55]}}
    if mode == "unknown_shield":
        events[0]["msh"] = None
    elif mode == "damaged":
        ps["damage_taken"]["0"].insert(0, 35)
    elif mode == "no_vitals":
        ps.pop("vitals")
    elif mode == "empty_slot":
        ps["vitals"] = {}
    elif mode == "before_entry":
        events.insert(0, {"t": 35, "life": 0, "hp": 60, "sh": 20, "mhp": 100, "msh": 50})
    view, rnd = views(blob)
    assert measures.state(view, rnd, blob, ctx, 1, window())["hp_shield"] == expected


@pytest.mark.parametrize("current_life_later,expected", [(False, None), (True, 100)])
def test_state_hp_start_uses_only_entry_life_maxima(current_life_later, expected):
    ctx, blob = context([[0, 5, 5]])
    blob["alive"]["0"] = [[0, 20, "kill"], [30, 60, "kill"], [70, None, None]]
    events = [{"t": 10, "life": 0, "hp": 60, "sh": 20, "mhp": 100, "msh": 50}]
    if current_life_later:
        events.append({"t": 45, "life": 1, "hp": 60, "sh": 0, "mhp": 100, "msh": 0})
    events.append({"t": 75, "life": 2, "hp": 60, "sh": 0, "mhp": 100, "msh": 25})
    blob["player_state"] = {"vitals": {"0": events}}
    view, rnd = views(blob)
    assert measures.state(view, rnd, blob, ctx, 1, window())["hp_shield"] == expected


def test_vitals_do_not_carry_across_revive_or_guess_unknown_shield():
    ctx, blob = context([[0, 5, 5]])
    blob["alive"]["0"] = [[0, 20, "kill"], [30, None, None]]
    blob["player_state"] = {"vitals": {"0": [
        {"t": 10, "life": 0, "hp": 60, "sh": 20, "mhp": 100, "msh": 50}]}}
    view, rnd = views(blob)
    assert measures.state(view, rnd, blob, ctx, 1, window())["hp_shield"] is None
    blob["player_state"]["vitals"]["0"].append(
        {"t": 35, "life": 1, "hp": 100, "sh": None, "mhp": 100, "msh": None})
    assert measures.state(view, rnd, blob, ctx, 1, window())["hp_shield"] is None
    blob["player_state"]["vitals"]["0"][-1].update(sh=0, msh=0)
    assert measures.state(view, rnd, blob, ctx, 1, window())["hp_shield"] == 100

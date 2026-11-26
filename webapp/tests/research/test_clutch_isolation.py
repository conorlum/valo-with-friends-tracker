"""Offline toy geometry contracts for clutch engagements and isolation."""

from pathlib import Path
import socket
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import clutch_measures as measures

from app.coach.roundview import RoundView
from app.control.utility import _sees_point
from app.moments.constants import SIGHT_STEP_S
from tests.moments.helpers import damage, flash, kill, round_of, toy_ability
from tests.replays.control_toys import midwall_hall


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def hall(*, yaw=180, util=(), geo=None, kills=(), db_deaths=()):
    return round_of({
        0: ("A", [(0, 160, 200, 0)]),
        5: ("B", [(0, 160, 270, 270)]),
        6: ("B", [(0, 352, 200, yaw)]),
    }, util=util, geo=geo, kills=kills, db_deaths=db_deaths)


@pytest.mark.parametrize("condition, expected", [
    ("open", (2, 2)), ("away", (1, 2)), ("wall", (1, 1)),
    ("flash", (1, 1)), ("nearsight", (1, 1)), ("smoke", (1, 1)),
])
def test_isolation_respects_facing_walls_and_blinds(condition, expected):
    util = []
    if condition in ("flash", "nearsight"):
        util = [flash(4, 0, [[6, 4, 3]], k=condition)]
    elif condition == "smoke":
        util = [toy_ability("Wraith", "4_Smoke", 260, 200, 0, t=4, t1=7)]
    rnd, _ = hall(yaw=0 if condition == "away" else 180, util=util,
                  geo=midwall_hall() if condition == "wall" else None)
    assert measures.isolation(rnd, 0, 5, [5, 6]) == dict(zip(("in_view", "clear_line"), expected))


def test_distinct_enemies_across_window_not_largest_simultaneous_count():
    rnd, _ = round_of({
        0: ("A", [(0, 160, 200, 0)]),
        5: ("B", [(0, 160, 270, 270), (5, 160, 270, 90)]),
        6: ("B", [(0, 352, 200, 0), (5, 352, 200, 180)]),
    })
    p = rnd.pos(0, 5)
    simultaneous = [sum(_sees_point(rnd, s, 4.5 + k * SIGHT_STEP_S, *p[:2], target=0)
                        for s in (5, 6)) for k in range(17)]
    assert max(simultaneous) == 1
    assert measures.isolation(rnd, 0, 5, [5, 6, 6]) == {"in_view": 2, "clear_line": 2}


def test_window_includes_both_endpoints():
    rnd, _ = hall(util=[flash(0, 0, [[6, 0, 5.5]]), flash(4.5625, 0, [[5, 4.5625, 3]])])
    assert measures.isolation(rnd, 0, 5, [5, 6]) == {"in_view": 2, "clear_line": 2}


@pytest.mark.parametrize("death", ["replay", "db"])
def test_dead_enemies_are_excluded_despite_remaining_tracks(death):
    rnd, _ = hall(kills=[kill(4, 0, 6)] if death == "replay" else (),
                  db_deaths=[(6, 4)] if death == "db" else ())
    assert measures.isolation(rnd, 0, 5, [5, 6]) == {"in_view": 1, "clear_line": 1}


def test_dead_clutcher_is_not_a_visible_target():
    rnd, _ = hall(kills=[kill(4, 5, 0)])
    assert measures.isolation(rnd, 0, 5, [5, 6]) == {"in_view": 0, "clear_line": 0}


def encounters(*, sight=12, direction="enemy", util=(), kills=(), end=30):
    """Both enemies initially look away; only one direction turns toward the pair."""
    return round_of({
        0: ("A", [(0, 160, 200, 180), (sight, 160, 200, 0 if direction == "clutcher" else 180)]),
        5: ("B", [(0, 160, 270, 90)]),
        6: ("B", [(0, 352, 200, 0), (sight, 352, 200, 180 if direction == "enemy" else 0)]),
    }, util=util, kills=kills, t_end=end)


@pytest.mark.parametrize("direction", ["enemy", "clutcher"])
def test_damage_and_later_first_sight_are_separate_engagements(direction):
    rnd, blob = encounters(direction=direction, util=[damage(5, 0, 5)])
    assert measures.engagements(rnd, blob, 0, 0, 20) == [
        {"t": 5, "enemies": [5], "kind": "fight"},
        {"t": 12, "enemies": [6], "kind": "sight"},
    ]


def test_first_sight_either_way_is_one_encounter_per_enemy():
    rnd, blob = hall()
    assert measures.engagements(rnd, blob, 0, 0, 20) == [
        {"t": 0, "enemies": [5], "kind": "sight"},
        {"t": 0, "enemies": [6], "kind": "sight"},
    ]


@pytest.mark.parametrize("sight", [2, 7, 8])
def test_sight_within_fight_gap_of_same_enemy_is_suppressed(sight):
    rnd, blob = encounters(sight=sight, util=[damage(5, 0, 6)])
    assert measures.engagements(rnd, blob, 0, 0, 20) == [
        {"t": 5, "enemies": [6], "kind": "fight"},
    ]


def test_fights_reuse_damage_run_ends_and_cross_team_kills():
    rnd, blob = encounters(sight=25, util=[damage(5, 0, 5, t1=8), damage(11, 6, 0),
                                         damage(4, 0, 5), damage(21, 0, 6)],
                           kills=[kill(14, 0, 5), kill(18, 0, 6)])
    assert measures.engagements(rnd, blob, 0, 5, 20) == [
        {"t": 5, "enemies": [5, 6], "kind": "fight"},
        {"t": 18, "enemies": [6], "kind": "fight"},
    ]


def test_sight_does_not_merge_with_fight_against_different_enemy():
    rnd, blob = encounters(sight=7, util=[damage(5, 0, 5)])
    assert [e["kind"] for e in measures.engagements(rnd, blob, 0, 0, 20)] == ["fight", "sight"]


def test_engagement_window_includes_endpoints_and_excludes_outside_events():
    rnd, blob = encounters(sight=25, util=[damage(4, 0, 5), damage(5, 0, 5),
                                         damage(20, 6, 0), damage(21, 0, 6)])
    assert measures.engagements(rnd, blob, 0, 5, 20) == [
        {"t": 5, "enemies": [5], "kind": "fight"},
        {"t": 20, "enemies": [6], "kind": "fight"},
    ]


def test_dead_enemy_cannot_generate_a_later_first_sight():
    rnd, blob = encounters(sight=12, kills=[kill(5, 0, 6)])
    assert measures.engagements(rnd, blob, 0, 6, 20) == []


@pytest.mark.parametrize("counts, isolated, unseen, share", [
    ([1, 2, 1, 0], 2, 1, 2 / 3), ([0, 0], 0, 2, None), ([], 0, 0, None),
])
def test_isolation_summary(counts, isolated, unseen, share):
    assert measures.isolation_summary([{"in_view": k, "clear_line": 3} for k in counts]) == {
        "engagements": len(counts), "isolated": isolated, "unseen": unseen, "one_at_a_time": share,
    }


@pytest.mark.parametrize("sight, expected", [(52, 48), (100, None)])
def test_caller_fills_first_engagement_clock(sight, expected):
    rnd, blob = encounters(sight=sight, end=100)
    view = RoundView(blob, {s: str(s) for s in rnd.team}, 1, None)
    row = {"slot": 0, "t_s": 40, "t_end_window": 90}
    result = measures.clock(view, rnd, blob, row)
    engagements = measures.engagements(rnd, blob, row["slot"], row["t_s"], row["t_end_window"])
    result["time_left_first_engagement"] = measures.time_left(blob, engagements[0]["t"]) if engagements else None
    assert result["time_left_first_engagement"] == expected


def test_import_remains_database_free_in_fresh_process():
    code = """
import sys
sys.path.insert(0, 'scripts')
import clutch_measures
assert not {'app.db', 'app.models', 'app.scoring.plant_window', 'preview_control_live'} & sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

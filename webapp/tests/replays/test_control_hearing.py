"""R1/R2 (2026-10-14): a heard footstep or shot names no enemy unless only one enemy could have made it
(the only one alive, every other living enemy in the team's live sight, or, for gunfire, the only living
enemy carrying that gun). Kills, the plant and gun damage still name the enemy. Toy rounds, real engine."""

import numpy as np
import pytest

from app.control import engine as ce
from tests.replays.control_toys import blob, open_hall, uv
from tests.replays.test_gaps_locating import _runner, _step

RUN_PX_PER_S = 6.0 / open_hall().m_per_px      # 6 m/s: over AUDIBLE_MPS


def _players(other_at=(400, 260), **kw):
    """A at x 120 facing west; B 5 (the actor) and B 6 both behind A's back, unseen."""
    return {0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)]),
            6: ("B", [(0.0, *other_at, 180)])}


def _shot(t, by, gun=None):
    s = {"k": "shot", "t": t, "by": by, "u": 0, "v": 0}
    if gun is not None:
        s["gun"] = gun
    return s


def _run(data, until, monkeypatch):
    monkeypatch.setattr(ce, "FOOTSTEP_RANGE_M", 100.0)
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Vandal": 100.0, "Phantom": 100.0})
    rnd, runner = _runner(open_hall(), data)
    for t in np.arange(0.0, until, 0.5):
        _step(rnd, runner, float(t))
    return runner.unknown.located["A"]


def _footsteps(other, deaths=None):
    players = {0: ("A", [(0.0, 120, 200, 180)]),
               5: ("B", [(0.0, 400, 120, 180), (4.0, 400, 120, 180), (5.0, 400, 120 + RUN_PX_PER_S, 180)]),
               6: ("B", [(0.0, *other, 180)])}
    return blob(players, t_end=9.0, deaths=deaths)


def test_footsteps_with_another_enemy_unseen_name_nobody(monkeypatch):
    assert 5 not in _run(_footsteps((400, 260)), 5.5, monkeypatch)


def test_footsteps_with_the_other_enemy_spotted_name_the_runner(monkeypatch):
    # A faces east with the other enemy ahead in its view; the runner is north of A, out of its cone
    data = _footsteps((300, 200))
    data["tracks"]["0"] = blob({0: ("A", [(0.0, 120, 200, 0)])}, t_end=9.0)["tracks"]["0"]
    data["tracks"]["5"] = blob({5: ("B", [(0.0, 120, 100, 0), (4.0, 120, 100, 0),
                                          (5.0, 120, 100 + RUN_PX_PER_S, 0)])}, t_end=9.0)["tracks"]["5"]
    located = _run(data, 5.5, monkeypatch)
    assert 6 in located, "the toy puts the other enemy in A's sight"
    assert located[5] == pytest.approx(5.0)


def test_footsteps_with_the_other_enemy_dead_name_the_runner(monkeypatch):
    assert _run(_footsteps((400, 260), deaths={6: 3.0}), 5.5, monkeypatch)[5] == pytest.approx(5.0)


def _gunfire(shots, deaths=None):
    return blob(_players(), t_end=10.0, util=shots, deaths=deaths)


def test_gunfire_only_one_enemy_carried_by_then_names_the_shooter(monkeypatch):
    assert _run(_gunfire([_shot(6.0, 5, "Vandal")]), 6.5, monkeypatch)[5] == pytest.approx(6.0)


def test_gunfire_both_carrying_the_gun_names_nobody(monkeypatch):
    located = _run(_gunfire([_shot(5.0, 6, "Vandal"), _shot(6.0, 5, "Vandal")]), 6.5, monkeypatch)
    assert 5 not in located


def test_gunfire_the_other_firing_it_only_after_names_the_shooter(monkeypatch):
    located = _run(_gunfire([_shot(6.0, 5, "Vandal"), _shot(7.0, 6, "Vandal")]), 7.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_gunfire_the_other_carrying_another_gun_names_the_shooter(monkeypatch):
    located = _run(_gunfire([_shot(5.0, 6, "Phantom"), _shot(6.0, 5, "Vandal")]), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_gunfire_a_dead_carrier_does_not_count(monkeypatch):
    players = {**_players(), 7: ("B", [(0.0, 400, 320, 180)])}
    data = blob(players, t_end=10.0, util=[_shot(2.0, 7, "Vandal"), _shot(6.0, 5, "Vandal")], deaths={7: 3.0})
    assert _run(data, 6.5, monkeypatch)[5] == pytest.approx(6.0)


def test_gunfire_a_carrier_on_the_listening_team_does_not_count(monkeypatch):
    data = _gunfire([_shot(2.0, 0, "Vandal"), _shot(6.0, 5, "Vandal")])
    assert _run(data, 6.5, monkeypatch)[5] == pytest.approx(6.0)


def test_gunfire_with_no_gun_and_the_other_unseen_names_nobody(monkeypatch):
    assert 5 not in _run(_gunfire([_shot(6.0, 5)]), 6.5, monkeypatch)


def test_gunfire_with_no_gun_and_the_other_dead_names_the_shooter(monkeypatch):
    located = _run(_gunfire([_shot(6.0, 5)], deaths={6: 3.0}), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_a_kill_still_names_the_killer_with_the_other_unseen(monkeypatch):
    data = blob({**_players(), 1: ("A", [(0.0, 200, 120, 180)])}, t_end=12.0, deaths={1: 8.0})
    data["kills"] = [{"i": 0, "t": 8.0, "killer": 5, "victim": 1, "u": uv(200, 120)[0], "v": uv(200, 120)[1]}]
    assert _run(data, 8.5, monkeypatch)[5] == pytest.approx(8.0)


def test_carriers_are_slots_with_a_shot_of_the_gun_at_or_before_te():
    rnd = ce.RoundInputs(_gunfire([_shot(5.0, 6, "Vandal"), _shot(6.0, 5, "Vandal"), _shot(6.0, 5, "Phantom")]),
                         open_hall())
    assert rnd.carriers("Vandal", 5.0) == {6}
    assert rnd.carriers("Vandal", 6.0) == {5, 6}
    assert rnd.carriers("Phantom", 5.5) == set()
    assert rnd.carriers(None, 9.0) == set()

"""R1/R2 (2026-10-14): a heard footstep or shot names no enemy unless only one enemy could have made it
(the only one alive, or every other living enemy in the team's live sight). The gun heard is no clue (D2,
2026-10-14 review). Kills, the plant and gun damage still name the enemy. Toy rounds, real engine."""

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


def test_gunfire_the_only_one_to_fire_that_gun_still_names_nobody(monkeypatch):
    # D2 (2026-10-14 review): the gun is no clue; the other enemy may carry a Vandal he hasn't fired
    assert 5 not in _run(_gunfire([_shot(6.0, 5, "Vandal")]), 6.5, monkeypatch)


def test_gunfire_the_other_carrying_another_gun_still_names_nobody(monkeypatch):
    located = _run(_gunfire([_shot(5.0, 6, "Phantom"), _shot(6.0, 5, "Vandal")]), 6.5, monkeypatch)
    assert 5 not in located


def test_gunfire_with_the_other_dead_names_the_shooter_whatever_the_gun(monkeypatch):
    located = _run(_gunfire([_shot(2.0, 6, "Vandal"), _shot(6.0, 5, "Vandal")], deaths={6: 3.0}), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_gunfire_with_no_gun_and_the_other_unseen_names_nobody(monkeypatch):
    assert 5 not in _run(_gunfire([_shot(6.0, 5)]), 6.5, monkeypatch)


def test_gunfire_with_no_gun_and_the_other_dead_names_the_shooter(monkeypatch):
    located = _run(_gunfire([_shot(6.0, 5)], deaths={6: 3.0}), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


# Control revision 18 (2026-10-18): a spike tap and an ult orb taken say someone is there, not who (the owner).

def _spike_tap(t, by):
    """A planted spike (planted by nobody known, so the plant itself names no one) with one defuse attempt."""
    x, y = uv(400, 200)
    return {"k": "ability", "kind": "Bomb", "code": "", "name": "Spike", "t": 1.0, "t1": 40.0, "u": x, "v": y,
            "defuses": [[t, t + 0.3, by, False]]}


def _orb_taken(data, t, slot):
    x, y = uv(400, 200)
    data["player_state"] = {"version": 1, "orbs": [{"u": x, "v": y, "t0": 0.0, "t1": t, "end": "taken", "slot": slot}]}
    return data


def _sounds(monkeypatch, heard_m=100.0):
    monkeypatch.setattr(ce, "SOUND_RANGE_M", {"tap": heard_m, "orb": heard_m})


def test_a_spike_tap_with_another_enemy_unseen_names_nobody(monkeypatch):
    _sounds(monkeypatch)
    assert 5 not in _run(blob(_players(), t_end=10.0, util=[_spike_tap(6.0, 5)]), 6.5, monkeypatch)


def test_a_spike_tap_with_the_other_enemy_dead_names_the_tapper(monkeypatch):
    _sounds(monkeypatch)
    located = _run(blob(_players(), t_end=10.0, util=[_spike_tap(6.0, 5)], deaths={6: 3.0}), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_a_spike_tap_out_of_hearing_names_nobody(monkeypatch):
    _sounds(monkeypatch, heard_m=1.0)
    assert 5 not in _run(blob(_players(), t_end=10.0, util=[_spike_tap(6.0, 5)], deaths={6: 3.0}), 6.5, monkeypatch)


def test_an_orb_taken_names_the_taker_only_by_elimination(monkeypatch):
    _sounds(monkeypatch)
    assert 5 not in _run(_orb_taken(blob(_players(), t_end=10.0), 6.0, 5), 6.5, monkeypatch)
    located = _run(_orb_taken(blob(_players(), t_end=10.0, deaths={6: 3.0}), 6.0, 5), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_an_orb_capture_is_heard_as_its_channel_starts_too(monkeypatch):
    # captured at 8 s: its 2 s channel began at 6 s, heard then (the other enemy dead by 3 s)
    _sounds(monkeypatch)
    located = _run(_orb_taken(blob(_players(), t_end=10.0, deaths={6: 3.0}), 8.0, 5), 6.5, monkeypatch)
    assert located[5] == pytest.approx(6.0)


def test_a_buy_phase_capture_is_never_heard_at_the_round_start(monkeypatch):
    # captured at -10 s: both its sounds are before the round, none is moved to 0 s
    _sounds(monkeypatch)
    assert 5 not in _run(_orb_taken(blob(_players(), t_end=10.0, deaths={6: 0.0}), -10.0, 5), 1.0, monkeypatch)
    # one completing at 0.3 s is heard then, though its channel began before the round
    located = _run(_orb_taken(blob(_players(), t_end=10.0, deaths={6: 0.0}), 0.3, 5), 1.0, monkeypatch)
    assert located[5] == pytest.approx(0.3)


def test_a_capture_let_go_is_heard_as_it_starts(monkeypatch):
    _sounds(monkeypatch)
    data = blob(_players(), t_end=10.0, deaths={6: 3.0})
    x, y = uv(400, 200)
    data["player_state"] = {"version": 1, "orbs": [{"u": x, "v": y, "t0": 0.0, "t1": None, "end": "round",
                                                    "tries": [[6.0, 6.4, 5]]}]}
    assert _run(data, 6.5, monkeypatch)[5] == pytest.approx(6.0)


def test_a_kill_still_names_the_killer_with_the_other_unseen(monkeypatch):
    data = blob({**_players(), 1: ("A", [(0.0, 200, 120, 180)])}, t_end=12.0, deaths={1: 8.0})
    data["kills"] = [{"i": 0, "t": 8.0, "killer": 5, "victim": 1, "u": uv(200, 120)[0], "v": uv(200, 120)[1]}]
    assert _run(data, 8.5, monkeypatch)[5] == pytest.approx(8.0)

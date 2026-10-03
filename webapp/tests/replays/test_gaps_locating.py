"""The unknown's locating events (timing-gaps spec, section 4): a kill, the plant, audible movement,
gunfire and gun damage collapse an enemy's unknown to an area around them; a revived enemy restarts where
they stand. Toy rounds through the real engine."""

import numpy as np
import pytest

from app.control import engine as ce
from tests.replays.control_toys import blob, open_hall, toy_ability, track, uv
from tests.replays.test_control_unknown import _Tk, _at


def _runner(geo, data):
    rnd = ce.RoundInputs(data, geo)
    runner = ce.TickRunner(geo)
    return rnd, runner


def _step(rnd, runner, t):
    return runner.step(ce.Tick(rnd, t))


def _unknown_of(runner, side, slot):
    return np.isfinite(runner.unknown.reached[side][slot])


def _hidden_pair(**extra):
    """A faces west at x 120; B stands east at x 400 behind A's back: B is never seen by A."""
    return {0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}


def test_a_kill_collapses_the_killers_unknown_to_an_area():
    geo = open_hall()
    data = blob({**_hidden_pair(), 1: ("A", [(0.0, 200, 120, 180)])}, t_end=12.0, deaths={1: 8.0})
    data["kills"] = [{"i": 0, "t": 8.0, "killer": 5, "victim": 1, "u": uv(200, 120)[0], "v": uv(200, 120)[1]}]
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 8.0, 0.5):
        _step(rnd, runner, float(t))
    wide = _unknown_of(runner, "A", 5).sum()
    _step(rnd, runner, 8.0)
    after = _unknown_of(runner, "A", 5)
    assert after.sum() < wide
    r = ce.KILL_AREA_M / geo.m_per_px
    x, y = 400, 200
    far = np.hypot(geo.centres[:, 0] - x, geo.centres[:, 1] - y) > r + 8
    assert not (after & far).any(), "only within KILL_AREA_M of the killer (plus spread since)"
    assert runner.unknown.located["A"][5] == pytest.approx(8.0)
    assert ("kill" in {k for _, _, k in runner.unknown.events["A"]})


def test_gun_damage_locates_the_shooter_heard_or_not():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0,
                util=[{"k": "damage", "t": 6.0, "t1": 6.1, "by": 5, "target": 0, "src": "gun", "wall": True, "n": 1}])
    rnd, runner = _runner(geo, data)
    assert rnd.gun_runs == [(6.0, 6.1, 5, 0, True)]
    for t in sorted(set(np.arange(0.0, 7.0, 0.5).tolist()) | {6.0}):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(6.0)


def test_a_gun_damage_run_gets_its_own_tick():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0,
                util=[{"k": "damage", "t": 6.2, "t1": 6.3, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 1}])
    rnd, runner = _runner(geo, data)
    times = rnd.tick_times().tolist()
    assert 6.25 in times and 6.1875 not in times     # the first grid tick at or after 6.2, not the nearest
    for t in times:
        _step(rnd, runner, float(t))
        if t == 6.25:
            break
    assert runner.unknown.events["A"] == [(5, 6.2, "damage")], "applied at its own tick"
    assert runner.unknown.located["A"][5] == pytest.approx(6.2)


def test_a_shot_with_no_gun_uses_the_longest_range_in_the_table(monkeypatch):
    """Spec: a gun-less shot uses the table's longest range; a named gun missing from the table, the default."""
    geo = open_hall()
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Quiet": 1.0, "Loud": 100.0})
    monkeypatch.setattr(ce, "GUN_HEARING_DEFAULT_M", 1.0)
    for gun, heard in ((None, True), ("Unlisted", False)):
        shot = {"k": "shot", "t": 6.0, "by": 5, "u": 0, "v": 0}
        if gun is not None:
            shot["gun"] = gun
        rnd, runner = _runner(geo, blob(_hidden_pair(), t_end=10.0, util=[shot]))
        for t in np.arange(0.0, 6.5, 0.5):
            _step(rnd, runner, float(t))
        assert (5 in runner.unknown.located["A"]) is heard, gun


def test_ability_damage_locates_nobody():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0,
                util=[{"k": "damage", "t": 6.0, "t1": 6.1, "by": 5, "target": 0, "src": "ability", "wall": False, "n": 1}])
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert 5 not in runner.unknown.located["A"]


def test_gunfire_out_of_hearing_range_locates_nobody(monkeypatch):
    geo = open_hall()
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Quiet": 1.0})
    data = blob(_hidden_pair(), t_end=10.0, util=[{"k": "shot", "t": 6.0, "by": 5, "u": 0, "v": 0, "gun": "Quiet"}])
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert 5 not in runner.unknown.located["A"]
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Quiet": 100.0})
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(6.0)


def test_running_near_a_player_is_heard_and_walking_is_not(monkeypatch):
    geo = open_hall()
    monkeypatch.setattr(ce, "FOOTSTEP_RANGE_M", 100.0)
    walk_px = 3.0 / geo.m_per_px      # 3 m/s: under AUDIBLE_MPS
    run_px = 6.0 / geo.m_per_px       # 6 m/s: over it
    for px_per_s, heard in ((walk_px, False), (run_px, True)):
        data = blob({0: ("A", [(0.0, 120, 200, 180)]),
                     5: ("B", [(0.0, 400, 120, 180), (4.0, 400, 120, 180), (5.0, 400, 120 + px_per_s, 180)])},
                    t_end=8.0)
        rnd, runner = _runner(geo, data)
        for t in np.arange(0.0, 5.5, 0.5):
            _step(rnd, runner, float(t))
        assert (5 in runner.unknown.located["A"]) is heard


def test_the_plant_locates_the_planter():
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0, util=[toy_ability("", "Spike", 400, 200, 5, t=5.0, t1=None, kind="Bomb")])
    rnd, runner = _runner(geo, data)
    assert rnd.planter == (5.0, 5)
    for t in np.arange(0.0, 6.0, 0.5):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(5.0)


def test_a_revived_enemy_restarts_where_they_stand():
    from tests.replays.control_toys import barrier_hall
    geo = barrier_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180), (6.0, 300, 120, 180)])},
                t_end=10.0)
    data["alive"]["5"] = [[0.0, 3.0, "kill"], [6.0, None, None]]
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 6.5, 0.5):
        _step(rnd, runner, float(t))
    back = _unknown_of(runner, "A", 5)
    start = runner.unknown._start["A"]
    assert back.sum() <= 2, "not the barrier ground again: just where they stand"
    assert not (back & start & (np.hypot(geo.centres[:, 0] - 300, geo.centres[:, 1] - 120) > 16)).any()
    assert runner.unknown.located["A"][5] == pytest.approx(6.0)
    assert (5, 6.0, "revived") in runner.unknown.events["A"]


# ---------------------------------------------------------------- the plan review's revisions


def test_damage_and_gunfire_between_two_ticks_are_both_recorded():
    """R8: every event in the interval is recorded in time order; the region collapses round the latest."""
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0,
                util=[{"k": "shot", "t": 6.0, "by": 5, "u": 0, "v": 0, "gun": "Vandal"},
                      {"k": "damage", "t": 6.2, "t1": 6.3, "by": 5, "target": 0, "src": "gun", "wall": True, "n": 1}])
    rnd, runner = _runner(geo, data)
    for t in [*np.arange(0.0, 6.0, 0.5).tolist(), 6.5]:      # no tick at 6.0 or 6.2
        _step(rnd, runner, float(t))
    assert runner.unknown.events["A"] == [(5, 6.0, "gunfire"), (5, 6.2, "damage")]
    assert runner.unknown.located["A"][5] == pytest.approx(6.2)
    # the damage's area (10 m), spread on for 0.3 s at UNKNOWN_MPS: nothing beyond it
    r = (ce.DAMAGE_AREA_M + 0.3 * ce.UNKNOWN_MPS) / geo.m_per_px
    far = np.hypot(geo.centres[:, 0] - 400, geo.centres[:, 1] - 200) > r + 8
    assert not (_unknown_of(runner, "A", 5) & far).any()


def test_an_event_at_time_zero_is_recorded():
    """R8: the first tick takes every event up to and including it."""
    geo = open_hall()
    data = blob(_hidden_pair(), t_end=10.0, util=[{"k": "shot", "t": 0.0, "by": 5, "u": 0, "v": 0, "gun": "Vandal"}])
    rnd, runner = _runner(geo, data)
    _step(rnd, runner, 0.0)
    assert runner.unknown.events["A"] == [(5, 0.0, "gunfire")]
    assert runner.unknown.located["A"][5] == 0.0
    _step(rnd, runner, 0.5)
    assert runner.unknown.events["A"] == []          # this tick's only


def test_a_sighting_is_recorded_after_the_ticks_other_events():
    """R8: B, in A's view, fires at the tick: the gunfire, then the sighting; the region is B's node."""
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 300, 200, 180)])}, t_end=10.0,
                util=[{"k": "shot", "t": 2.0, "by": 5, "u": 0, "v": 0, "gun": "Vandal"}])
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 2.5, 0.5):
        _step(rnd, runner, float(t))
    assert runner.unknown.events["A"] == [(5, 2.0, "gunfire"), (5, 2.0, "seen")]
    assert runner.unknown.located["A"][5] == 2.0


def test_a_revived_enemy_without_a_sample_restarts_at_their_first_one():
    """R14: B comes back at 6.0 but has no position until 7.0: nothing until then, and the restart (and its
    5 s wait) counts from the first sample."""
    from tests.replays.control_toys import barrier_hall
    geo = barrier_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=10.0)
    data["tracks"]["5"] = track([(0.0, 400, 200, 180)], 0.0, 3.0) + track([(7.0, 300, 120, 180)], 7.0, 10.0)
    data["alive"]["5"] = [[0.0, 3.0, "kill"], [6.0, None, None]]
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
        if t >= 6.0:
            assert not _unknown_of(runner, "A", 5).any(), "not the barrier ground while they have no position"
    assert 5 not in runner.unknown.located["A"]
    _step(rnd, runner, 7.0)
    assert runner.unknown.located["A"][5] == 7.0
    assert runner.unknown.events["A"] == [(5, 7.0, "revived")]
    back = _unknown_of(runner, "A", 5)
    assert 1 <= back.sum() <= 2
    assert not (back & (np.hypot(geo.centres[:, 0] - 300, geo.centres[:, 1] - 120) > 16)).any()


def test_a_revival_between_ticks_is_located_at_the_lifes_start():
    """Spec: a revived enemy counts as located at that moment: the life's start when there is a sample then
    (not the tick that notices it), else the first sample of the new life (R14), even between ticks."""
    from tests.replays.control_toys import barrier_hall
    geo = barrier_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180), (6.0, 300, 120, 180)])},
                t_end=10.0)
    data["alive"]["5"] = [[0.0, 3.0, "kill"], [6.1, None, None]]
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.0, 0.5):
        _step(rnd, runner, float(t))
    assert runner.unknown.located["A"][5] == pytest.approx(6.1)
    # no sample at the start (6.1): the first one, at 6.75, between the ticks 6.5 and 7.0
    data["tracks"]["5"] = track([(0.0, 400, 200, 180)], 0.0, 3.0) + track([(6.75, 300, 120, 180)], 6.75, 10.0)
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 7.5, 0.5):
        _step(rnd, runner, float(t))
        if t == 6.5:
            assert 5 not in runner.unknown.located["A"]
    assert runner.unknown.located["A"][5] == pytest.approx(6.75)


class _RoundTick(_Tk):
    """A hand-made tick (holders with exact views) over a real round's inputs, for its events."""

    def __init__(self, rnd, t, *holders):
        super().__init__(t, *holders)
        self.rnd = rnd


def test_a_watched_area_centre_roots_its_areas_routes():
    """R15: B kills from a node the team observes (passive, not a sighting): the centre gets an entry, every
    node of the area traces back to it, and clearing and re-entry keep the old trace intact."""
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 180)]), 1: ("A", [(0.0, 150, 280, 180)]),
                 5: ("B", [(0.0, 404, 204, 180)])}, t_end=10.0, deaths={1: 2.0})
    data["kills"] = [{"i": 0, "t": 2.0, "killer": 5, "victim": 1, "u": 0, "v": 0}]
    rnd = ce.RoundInputs(data, geo)
    unk = ce.Unknown(geo)
    enemy = geo.cell_of_px(404, 204)        # a cell's middle: the track's quantised position stays in it

    def a(cells=()):
        h = _at(0, "A", geo, 120, 200, cells=cells)
        h.passive[enemy] = True                      # observed, but not a sighting
        return h

    for t in (0.0, 1.0, 2.0):
        unk.apply(_RoundTick(rnd, t, a(), _at(5, "B", geo, 404, 204)))
    assert unk.located["A"][5] == 2.0 and unk.events["A"] == [(5, 2.0, "kill")]
    log, ent = unk.log["A"], unk.entry["A"][5]
    assert ent[enemy] == -1                          # observed: not unknown
    root = [e for e in range(len(log)) if log.node[e] == enemy and log.parent[e] == -1 and log.t[e] == 2.0]
    assert len(root) == 1
    members = np.flatnonzero(ent >= 0)
    assert len(members) > 1
    for node in members.tolist():
        route = log.trace(int(ent[node]))
        assert route[0] == root[0] and log.t[route[-1]] == 2.0
    # clear one member, free it, let it come back from its neighbours
    target = int(members[0])
    old = log.trace(int(ent[target]))
    unk.apply(_RoundTick(rnd, 2.5, a([target]), _at(5, "B", geo, 404, 204)))
    assert unk.entry["A"][5][target] == -1
    unk.apply(_RoundTick(rnd, 3.0, a(), _at(5, "B", geo, 404, 204)))
    unk.apply(_RoundTick(rnd, 4.0, a(), _at(5, "B", geo, 404, 204)))
    new = int(unk.entry["A"][5][target])
    assert new >= 0 and new != old[-1]
    assert log.trace(old[-1]) == old
    assert log.trace(new)[0] == root[0]


def test_missing_data_is_counted_once_per_case_and_slot():
    """R20: two shots without a gun from one player count once; each locating event without a position and
    each speed across a track break counts once per slot and round, however many ticks meet it."""
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)]),
                 6: ("B", [(0.0, 400, 260, 180)])}, t_end=10.0,
                util=[{"k": "shot", "t": 4.0, "by": 5, "u": 0, "v": 0},
                      {"k": "shot", "t": 5.0, "by": 5, "u": 0, "v": 0},
                      {"k": "shot", "t": 4.0, "by": 6, "u": 0, "v": 0, "gun": "Vandal"},
                      {"k": "shot", "t": 5.0, "by": 6, "u": 0, "v": 0, "gun": "Vandal"},
                      toy_ability("", "Spike", 300, 200, None, t=8.0, t1=None, kind="Bomb")])
    # slot 6 has a hole in their track (3-6 s) while alive: both its shots fall in it
    data["tracks"]["6"] = track([(0.0, 400, 260, 180)], 0.0, 3.0) + track([(6.0, 400, 260, 180)], 6.0, 10.0)
    rnd, runner = _runner(geo, data)
    for t in np.arange(0.0, 9.0, 0.5):
        _step(rnd, runner, float(t))
    assert rnd.missing["shot without a gun (longest hearing range used)"] == 1
    assert rnd.missing["plant without a planter (locates nobody)"] == 1
    assert rnd.missing["locating event without a position"] == 1
    assert rnd.missing["speed across a track break or missing sample"] == 1
    assert 6 not in runner.unknown.located["A"]

"""Predicted gaps (timing-gaps spec, section 5) on toy rounds through the real engine, and on hand-made tick
records where a rule needs exposure, releases or events to come and go on cue."""

import json

import numpy as np
import pytest

from app.control import engine as ce
from app.control.geometry import cast
from app.control.observe import PlayerView, TickRecord
from app.control.routes import RouteLog
from app.gaps import detect as gd
from app.gaps.rows import to_rows
from tests.replays.control_toys import blob, door_hall, open_hall, toy_ability


def run(geo, data, link=None, chokes=None, detector=None):
    rnd = ce.RoundInputs(data, geo, link)
    det = gd.GapDetector(geo, rnd)
    ce.compute_round(data, geo, link, observer=lambda rec, unk: det.step(rec, unk.log), knowledge=False)
    if detector is not None:
        detector.append(det)
    return det.finish()


def predicted(gaps):
    return [g for g in gaps if g.kind == "predicted"]


def test_off_facing():
    assert gd.off_facing(0, 0, 0, 10, 0) == pytest.approx(0)
    assert gd.off_facing(0, 0, 0, -10, 0) == pytest.approx(180)
    assert gd.off_facing(90, 0, 0, 10, 0) == pytest.approx(90)


def test_an_enemy_behind_a_player_opens_one_gap_with_open_timing():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=8.0)
    gaps = predicted(run(geo, data))
    mine = [g for g in gaps if g.victim == 0]
    assert len(mine) == 1, "one route behind one victim is one gap, however many cells"
    g = mine[0]
    assert g.cause == "open_timing" and g.candidates.keys() == {5}
    assert g.angle_deg > gd.BEHIND_DEG and g.choke_seq == ()


def test_an_enemy_in_front_opens_nothing():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=8.0)
    assert [g for g in predicted(run(geo, data)) if g.victim == 0] == []


def test_the_5s_wait_after_a_sighting():
    geo = open_hall()
    # A faces east (sees B) until 3 s, then turns west: B was located at 3 s, so no gap before 8 s.
    data = blob({0: ("A", [(0.0, 150, 200, 0), (3.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])},
                t_end=12.0)
    mine = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert mine and mine[0].t_open >= 3.0 + gd.MIN_UNSEEN_S - 0.5


def test_turning_to_face_the_enemy_and_away_again_keeps_one_gap():
    geo = open_hall()
    # A faces west (B behind) 0-3 s, faces east and sees B 3-4 s, faces west again from 4 s. While B is seen
    # their unknown is empty, but the gap is judged against the unknown before each sighting (R7) and the
    # 5 s close timer bridges the rest: one gap, closed only by the round's end.
    data = blob({0: ("A", [(0.0, 150, 200, 180), (3.0, 150, 200, 0), (4.0, 150, 200, 180)]),
                 5: ("B", [(0.0, 400, 200, 180)])}, t_end=14.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.t_open == pytest.approx(0.0) and g.t_close == pytest.approx(14.0)


# Hand-made records: the close and glance rules need exposure to come and go on cue, which the engine's
# unknown (pushed out from the enemy's own cell every tick) never does in an open hall.

def _manual(geo, frames, t_decided=60.0):
    """frames: [(t, nodes of enemy 5's unknown)] or [(t, {enemy: nodes})]; victim 0 (team A) stands at
    (150, 200) facing west. Enemies 5 and 6 are team B."""
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)]),
                 6: ("B", [(0.0, 400, 120, 180)])}, t_end=t_decided + 1.0, t_decided=t_decided)
    rnd = ce.RoundInputs(data, geo)
    logs = {"A": RouteLog(), "B": RouteLog()}
    det = gd.GapDetector(geo, rnd)
    z = np.zeros(geo.n, bool)
    for t, nodes in frames:
        per_enemy = nodes if isinstance(nodes, dict) else {5: nodes}
        unknown = {}
        for enemy, ns in per_enemy.items():
            ent = np.full(geo.n, -1, np.int64)
            for node in ns:
                ent[node] = logs["A"].add(node, t, -1, -1)
            unknown[enemy] = ent
        p = PlayerView(0, "A", geo.cell_of_px(150, 200), 150.0, 200.0, 180.0, z.copy(), z.copy())
        det.step(TickRecord(t, {0: p}, {"A": unknown, "B": {}}, {"A": [], "B": []}, []), logs)
    return [g for g in det.finish() if g.kind == "predicted"]


def test_closes_5s_after_last_exposure_and_a_return_is_a_new_gap():
    geo = open_hall()
    behind = [geo.cell_of_px(300, 200)]
    frames = [(t, behind) for t in (0.0, 0.5, 1.0, 1.5, 2.0)] + [(t, []) for t in (2.5, 4.0, 6.0, 7.0)] + \
             [(8.0, behind), (8.5, behind)]
    first, second = _manual(geo, frames)
    assert first.t_last_exposed == pytest.approx(2.0) and first.t_close == pytest.approx(7.0)
    assert second.t_open == pytest.approx(8.0)


def test_a_glance_back_within_5s_is_the_same_gap():
    geo = open_hall()
    behind = [geo.cell_of_px(300, 200)]
    frames = [(t, behind) for t in (0.0, 0.5, 1.0)] + [(t, []) for t in (1.5, 2.0, 2.5)] + \
             [(t, behind) for t in (3.0, 3.5, 4.0)]
    [g] = _manual(geo, frames)
    assert g.t_open == pytest.approx(0.0) and g.t_close == pytest.approx(60.0)
    assert g.qualified_s == pytest.approx(1.5 + 1.0 + (60.0 - 4.0)), "time qualifying, clipped at the close"


def test_nothing_is_detected_after_the_round_is_decided():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=10.0, t_decided=3.0)
    gaps = predicted(run(geo, data))
    assert gaps, "the round has a gap before it is decided"
    assert all(g.t_open <= 3.0 for g in gaps)
    assert all(g.t_close is not None and g.t_close <= 3.0 for g in gaps)


def test_the_victims_death_closes_their_gap():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=10.0, deaths={0: 4.0})
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.t_close == pytest.approx(4.0)


def test_flicker_flag():
    geo = open_hall()
    # A faces west (B behind) for 0.5 s, then faces east (sees B) for the rest.
    data = blob({0: ("A", [(0.0, 150, 200, 180), (0.5, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])},
                t_end=8.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.qualified_s < gd.FLICKER_S and g.flicker


def test_stood_is_recorded_when_the_enemy_stands_in_the_gap():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=6.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.stood_by == 5 and g.stood_at is not None


# ---------------------------------------------------------------- R7: judged before the tick's own events


def test_a_same_tick_damage_event_opens_a_gap():
    """A faces north, so B (east) is beside them, never seen; at 4 s A turns west, putting B's unknown behind
    them, and B's gun damage on A starts at that same tick. The opening is judged against the unknown and the
    locating record before the damage (R7): it opens at 4 s, not 5 s later."""
    geo = open_hall()
    damage = {"k": "damage", "t": 4.0, "t1": 4.1, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 1}
    data = blob({0: ("A", [(0.0, 150, 200, 270), (4.0, 150, 200, 180)]), 5: ("B", [(0.0, 400, 200, 180)])},
                t_end=8.0, util=[damage])
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.t_open == pytest.approx(4.0)
    assert g.cause == "victim_turned"
    assert 5 in g.candidates and g.stood_at == pytest.approx(4.0)


def test_a_kill_at_the_moment_of_stood_still_counts_as_killed():
    """B stands in A's gap; at 6 s B kills A's teammate (a locating event for A's team), and at 9 s kills A.
    The stand at 6 s is judged before the kill locates B (R7), so the kill of A 3 s later is in the window;
    without it the last stand would be at 5.9375 s and the kill would fall outside."""
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 1: ("A", [(0.0, 120, 120, 180)]),
                 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, deaths={1: 6.0, 0: 9.0})
    data["kills"] = [{"i": 0, "t": 6.0, "killer": 5, "victim": 1}, {"i": 1, "t": 9.0, "killer": 5, "victim": 0}]
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert 6.0 in g.stood_times[5] and max(g.stood_times[5]) == pytest.approx(6.0)
    assert g.killed_at == pytest.approx(9.0) and g.killed_by == 5
    assert g.t_close == pytest.approx(9.0)


# ---------------------------------------------------------------- R9 and R10: the close timer


def test_a_non_candidate_with_the_same_sequence_does_not_keep_the_gap_open():
    geo = open_hall()
    behind, front = [geo.cell_of_px(300, 200)], [geo.cell_of_px(110, 200)]
    frames = [(t, {5: behind}) for t in (0.0, 0.5, 1.0, 1.5, 2.0)] + \
             [(t, {6: front}) for t in (2.5, 4.0, 6.0, 7.0, 8.0)]
    [g] = _manual(geo, frames)
    assert g.candidates.keys() == {5}
    assert g.t_last_exposed == pytest.approx(2.0) and g.t_close == pytest.approx(7.0)
    # a candidate's own cell in front of the victim does keep it open: exposure ignores facing
    frames = [(t, {5: behind}) for t in (0.0, 0.5, 1.0, 1.5, 2.0)] + \
             [(t, {5: front}) for t in (2.5, 4.0, 6.0, 7.0, 8.0)]
    [g] = _manual(geo, frames)
    assert g.t_last_exposed == pytest.approx(8.0) and g.t_close == pytest.approx(60.0)


def test_a_sparse_timeline_expires_before_the_tick_is_consumed():
    geo = open_hall()
    behind = [geo.cell_of_px(300, 200)]
    first, second = _manual(geo, [(0.0, behind), (1.0, []), (6.0, behind)])
    assert first.t_close == pytest.approx(5.0) and first.qualified_s == pytest.approx(1.0)
    assert second.t_open == pytest.approx(6.0)


def test_a_return_exactly_at_expiry_is_a_new_gap():
    geo = open_hall()
    behind = [geo.cell_of_px(300, 200)]
    first, second = _manual(geo, [(0.0, behind), (1.0, []), (5.0, behind)])
    assert first.t_close == pytest.approx(5.0)
    assert second.t_open == pytest.approx(5.0) and second.t_close == pytest.approx(60.0)


# ---------------------------------------------------------------- hand-made records with teammates and routes


def _pv(geo, slot, team, x, y, yaw, view=(), util=(), eye_z=None, node=None):
    node = geo.cell_of_px(x, y) if node is None else node
    v = np.zeros(geo.n, bool)
    v[list(view)] = True
    u = np.zeros(geo.n, bool)
    u[list(util)] = True
    live = v | u
    live[node] = True
    return PlayerView(slot, team, node, float(x), float(y), float(yaw), live, u, v, eye_z)


class _Drive:
    """Hand-made ticks for victim team A against enemy 5 (and 6), with one route log whose entries the test
    makes (each node keeps its entry while it stays unknown)."""

    def __init__(self, geo, slots=None, t_decided=60.0, deaths=None, util=(), merge=True):
        slots = slots or {0: "A", 1: "A", 2: "A", 5: "B", 6: "B"}
        data = blob({s: (side, [(0.0, 150, 200, 0)]) for s, side in slots.items()},
                    t_end=t_decided + 1.0, t_decided=t_decided, deaths=deaths, util=util)
        self.geo, self.rnd = geo, ce.RoundInputs(data, geo)
        self.logs = {"A": RouteLog(), "B": RouteLog()}
        self.det = gd.GapDetector(geo, self.rnd, merge=merge)

    def entry(self, node, t, parent=-1, choke=-1):
        return self.logs["A"].add(node, t, parent, choke)

    def tick(self, t, players, unknown, events=(), smokes=()):
        """unknown: {enemy: {node: entry}}; events: [(enemy, t, kind)] for team A."""
        arrays = {}
        for enemy, nodes in unknown.items():
            ent = np.full(self.geo.n, -1, np.int64)
            for node, e in nodes.items():
                ent[node] = e
            arrays[enemy] = ent
        rec = TickRecord(float(t), {p.slot: p for p in players}, {"A": arrays, "B": {}},
                         {"A": list(events), "B": []}, list(smokes))
        self.det.step(rec, self.logs)

    def gaps(self):
        return [g for g in self.det.finish() if g.kind == "predicted"]


def _released(teammates_before, teammates_after, smokes_after=(), deaths=None, util=(), edit=None):
    """The door hall. Victim 0 stands in the west room facing west. Enemy 5's unknown sits east of the wall
    (no line to the victim) until the door X is released at 3 s; it enters X at 3 s and a cell Y behind the
    victim at 3.5 s. `teammates_*` give the other A players before and at 3 s (callables of the geometry).
    `edit`, when given, is called with the round's RoundInputs before the first tick."""
    geo = door_hall()
    d = _Drive(geo, deaths=deaths, util=util)
    if edit is not None:
        edit(d.rnd)
    x_node, y_node, e_node = geo.cell_of_px(208, 292), geo.cell_of_px(190, 240), geo.cell_of_px(300, 250)
    victim = lambda: _pv(geo, 0, "A", 120, 200, 180)                    # noqa: E731
    e0 = d.entry(e_node, 0.0)
    for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5):
        d.tick(t, [victim(), *teammates_before(geo, x_node)], {5: {e_node: e0}})
    ex = d.entry(x_node, 3.0, e0)
    d.tick(3.0, [victim(), *teammates_after(geo, x_node)], {5: {e_node: e0, x_node: ex}}, smokes=smokes_after)
    ey = d.entry(y_node, 3.5, ex)
    d.tick(3.5, [victim(), *teammates_after(geo, x_node)], {5: {e_node: e0, x_node: ex, y_node: ey}},
           smokes=smokes_after)
    [g] = [g for g in d.gaps() if g.victim == 0]
    assert g.t_open == pytest.approx(3.5)
    return g, x_node


def test_a_release_delayed_arrival_is_route_released():
    g, x_node = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0, view=[x])],
                          lambda geo, x: [_pv(geo, 1, "A", 150, 292, 90)])
    assert g.cause == "route_released"
    assert g.cause_detail == {"cell": x_node, "t": 3.0, "player": 1, "by": "view", "reason": "turned"}


def test_a_teammate_turning_away_from_the_door_is_route_released_through_the_engine():
    """The door hall through the real engine: teammate 1 watches the door from the west room until 3 s, then
    turns away; the enemy's unknown, held at the door until then, reaches the ground behind player 0."""
    geo = door_hall()
    data = blob({0: ("A", [(0.0, 180, 150, 270)]), 1: ("A", [(0.0, 150, 280, 0), (3.0, 150, 280, 180)]),
                 5: ("B", [(0.0, 240, 270, 180)])}, t_end=10.0)
    [g] = [g for g in predicted(run(geo, data)) if g.victim == 0]
    assert g.t_open > 3.0 and g.cause == "route_released"
    detail = g.cause_detail
    assert (detail["t"], detail["player"], detail["by"], detail["reason"]) == (3.0, 1, "view", "turned")


def test_a_node_in_view_and_utility_is_credited_to_view():
    g, x_node = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 120, 0, util=[x]),
                                          _pv(geo, 2, "A", 150, 292, 0, view=[x], util=[x])],
                          lambda geo, x: [_pv(geo, 1, "A", 150, 120, 0), _pv(geo, 2, "A", 150, 292, 0)])
    assert g.cause == "route_released"
    assert g.cause_detail["player"] == 2 and g.cause_detail["by"] == "view"


def test_an_observer_without_a_position_has_not_died():
    g, _ = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0, view=[x])], lambda geo, x: [])
    assert g.cause_detail["player"] == 1 and g.cause_detail["reason"] == "other"
    g, _ = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0, view=[x])], lambda geo, x: [], deaths={1: 3.0})
    assert g.cause_detail["reason"] == "died"


def test_only_a_new_smoke_that_blocks_the_node_is_smoked():
    before = lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0, view=[x])]      # noqa: E731
    after = lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0)]                 # same place, same facing  # noqa: E731
    g, _ = _released(before, after, smokes_after=[(120.0, 120.0, 6.0, False)])
    assert g.cause_detail["reason"] == "other", "a new smoke elsewhere is not why"
    g, _ = _released(before, after, smokes_after=[(180.0, 292.0, 6.0, False)])
    assert g.cause_detail["reason"] == "smoked"


def test_a_release_that_did_not_hold_the_route_back_is_not_route_released():
    """Review fix 1 (ruling D5): the route reached X at 1.0 and Y (a child of X's entry) at 1.5. A teammate
    looks at X from 2.0 to 3.0, but Y keeps its old entry, so the route never waited for that release. The gap
    opens when the enemy's 5 s wait runs out: `open_timing`, not `route_released` at 3.0."""
    geo = door_hall()
    d = _Drive(geo)
    e_node, x_node, y_node = geo.cell_of_px(300, 250), geo.cell_of_px(208, 292), geo.cell_of_px(190, 240)
    e0 = d.entry(e_node, 0.0)
    ex = d.entry(x_node, 1.0, e0)
    ey = d.entry(y_node, 1.5, ex)
    victim = lambda: _pv(geo, 0, "A", 120, 200, 180)                    # noqa: E731
    for t in np.arange(0.0, 5.01, 0.5).tolist():
        mate = _pv(geo, 1, "A", 150, 292, 0, view=[x_node]) if 2.0 <= t < 3.0 else _pv(geo, 1, "A", 150, 292, 90)
        unknown = {e_node: e0, y_node: ey} if 2.0 <= t < 3.0 else {e_node: e0, x_node: ex, y_node: ey}
        d.tick(t, [victim(), mate], {5: unknown}, events=[(5, 0.0, "gunfire")] if t == 0.0 else ())
    assert d.det.rel_t["A"][x_node] == pytest.approx(3.0), "X was released at 3.0"
    [g] = [g for g in d.gaps() if g.victim == 0]
    assert g.t_open == pytest.approx(5.0)
    assert g.cause == "open_timing" and g.cause != "route_released"


def test_utility_expired_needs_a_watcher_that_covered_the_node():
    """Review fix 3 (minor 2): teammate 1's utility watched the door until 3 s. An alarmbot of theirs that
    ends at 3 s elsewhere is not why; one beside the door is."""
    before = lambda geo, x: [_pv(geo, 1, "A", 150, 120, 0, util=[x])]     # noqa: E731
    after = lambda geo, x: [_pv(geo, 1, "A", 150, 120, 0)]                # noqa: E731
    far = toy_ability("Killjoy", "Q_StealthAlarmbot", 120, 120, 1, t=0.0, t1=3.0, kind="GameObject")
    g, _ = _released(before, after, util=[far])
    assert g.cause_detail["by"] == "utility" and g.cause_detail["reason"] == "other"
    near = toy_ability("Killjoy", "Q_StealthAlarmbot", 200, 292, 1, t=0.0, t1=3.0, kind="GameObject")
    g, x_node = _released(before, after, util=[near])
    assert g.cause_detail["reason"] == "utility_expired"


def test_moving_into_a_new_line_of_sight_is_victim_moved():
    geo = door_hall()
    d = _Drive(geo)
    s_node = geo.cell_of_px(244, 292)                     # east of the door, in enemy 5's unknown from 0 s
    e = d.entry(s_node, 0.0)
    for t in (0.0, 0.5, 1.0, 1.5, 2.0):
        d.tick(t, [_pv(geo, 0, "A", 190, 200, 180)], {5: {s_node: e}})
    d.tick(2.5, [_pv(geo, 0, "A", 196, 290, 180)], {5: {s_node: e}})
    assert not cast(geo, 190, 200, gd.FULL_CIRCLE, [])[s_node] and cast(geo, 196, 290, gd.FULL_CIRCLE, [])[s_node]
    [g] = d.gaps()
    assert g.t_open == pytest.approx(2.5) and g.cause == "victim_moved"


def test_turning_the_back_on_exposed_cells_is_victim_turned():
    geo = open_hall()
    d = _Drive(geo)
    node = geo.cell_of_px(300, 200)
    e = d.entry(node, 0.0)
    for t in (0.0, 0.5, 1.0):
        d.tick(t, [_pv(geo, 0, "A", 150, 200, 0)], {5: {node: e}})       # facing it: exposed, in front
    d.tick(1.5, [_pv(geo, 0, "A", 150, 200, 180)], {5: {node: e}})
    [g] = d.gaps()
    assert g.t_open == pytest.approx(1.5) and g.cause == "victim_turned"


def test_aim_noise_while_the_wait_runs_out_is_not_a_turn():
    """The cells were already behind the victim while the 5 s wait ran; the gap opens because the wait ended,
    not because the victim's facing twitched."""
    geo = open_hall()
    d = _Drive(geo)
    node = geo.cell_of_px(300, 200)
    e = d.entry(node, 0.0)
    d.tick(0.0, [_pv(geo, 0, "A", 150, 200, 180)], {5: {node: e}}, events=[(5, 0.0, "gunfire")])
    for i, t in enumerate(np.arange(0.5, 5.01, 0.5).tolist()):
        d.tick(t, [_pv(geo, 0, "A", 150, 200, 180 + 2 * (i % 2))], {5: {node: e}})
    [g] = d.gaps()
    assert g.t_open == pytest.approx(5.0) and g.cause == "open_timing"


def test_spread_from_beside_to_behind_with_aim_jitter_is_not_victim_turned():
    """Final review I1: the turn test is per cell. The enemy's region sits beside the victim (exposed, in front)
    and spreads, on the same choke sequence, to a cell behind them that was never exposed in front; the aim
    jitters by 2 degrees every tick. The victim turned their back on nothing: not `victim_turned`."""
    geo = open_hall()
    d = _Drive(geo)
    beside, behind = geo.cell_of_px(300, 200), geo.cell_of_px(150, 120)
    eb = d.entry(beside, 0.0)
    victim = lambda i: _pv(geo, 0, "A", 150, 200, 90 + 2 * (i % 2))     # noqa: E731
    for i, t in enumerate((0.0, 0.5, 1.0, 1.5, 2.0)):
        d.tick(t, [victim(i)], {5: {beside: eb}})
    eh = d.entry(behind, 2.5, eb)
    d.tick(2.5, [victim(5)], {5: {beside: eb, behind: eh}})
    [g] = d.gaps()
    assert g.t_open == pytest.approx(2.5) and g.spot == behind
    assert g.cause in ("open_timing", "route_released") and g.cause != "victim_turned"


# ---------------------------------------------------------------- I3: gap identity and the cause tie order


def test_neighbouring_cells_with_different_choke_sequences_are_two_gaps():
    geo = open_hall()
    d = _Drive(geo)
    n1, n2 = geo.cell_of_px(300, 200), geo.cell_of_px(308, 200)
    e1, e2 = d.entry(n1, 0.0, choke=1), d.entry(n2, 0.0, choke=2)
    for t in (0.0, 0.5, 1.0):
        d.tick(t, [_pv(geo, 0, "A", 150, 200, 180)], {5: {n1: e1, n2: e2}})
    gaps = d.gaps()
    assert sorted(g.choke_seq for g in gaps) == [(1,), (2,)]
    assert {g.spot: g.choke_seq for g in gaps} == {n1: (1,), n2: (2,)}
    assert all(g.t_open == pytest.approx(0.0) and g.candidates.keys() == {5} for g in gaps)


def test_two_enemies_on_one_sequence_merge_into_one_gap_with_two_candidates():
    """One victim life and one choke sequence is one gap, whichever enemies' unknown it is. Its spot is the
    earliest arrival across the enemies (enemy 6's), and either candidate can stand in it."""
    geo = open_hall()
    d = _Drive(geo)
    n5, n6 = geo.cell_of_px(300, 200), geo.cell_of_px(300, 240)
    e5, e6 = d.entry(n5, 1.0, choke=1), d.entry(n6, 0.5, choke=1)
    enemy6 = _pv(geo, 6, "B", 300, 240, 180)
    for t in (1.0, 1.5, 2.0):
        d.tick(t, [_pv(geo, 0, "A", 150, 200, 180), enemy6], {5: {n5: e5}, 6: {n6: e6}})
    [g] = d.gaps()
    assert g.candidates.keys() == {5, 6} and g.choke_seq == (1,)
    assert g.spot == n6, "the earliest arrival across enemies"
    assert g.stood_by == 6 and g.stood_at == pytest.approx(1.0)


def test_a_second_sequence_opens_a_second_gap_while_the_first_is_latched():
    geo = open_hall()
    d = _Drive(geo)
    n1, n2 = geo.cell_of_px(300, 200), geo.cell_of_px(300, 240)
    e1, e2 = d.entry(n1, 0.0, choke=1), d.entry(n2, 1.5, choke=2)
    victim = lambda: _pv(geo, 0, "A", 150, 200, 180)                    # noqa: E731
    for t in (0.0, 0.5, 1.0):
        d.tick(t, [victim()], {5: {n1: e1}})
    for t in (1.5, 2.0):
        d.tick(t, [victim()], {5: {n1: e1, n2: e2}})
    first, second = sorted(d.gaps(), key=lambda g: g.t_open)
    assert (first.t_open, first.choke_seq) == (pytest.approx(0.0), (1,))
    assert (second.t_open, second.choke_seq) == (pytest.approx(1.5), (2,))
    assert first.t_close == pytest.approx(60.0) and second.t_close == pytest.approx(60.0), "both latched"


def test_a_teammate_moving_away_is_released_as_moved():
    g, _ = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0, view=[x])],
                     lambda geo, x: [_pv(geo, 1, "A", 150, 250, 0)])
    assert g.cause == "route_released" and g.cause_detail["reason"] == "moved"


def test_a_flashed_teammate_is_released_as_blinded():
    def flash(rnd):
        rnd.flashed[1].append((2.9, 4.0))

    g, _ = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0, view=[x])],
                     lambda geo, x: [_pv(geo, 1, "A", 150, 292, 0)], edit=flash)
    assert g.cause == "route_released" and g.cause_detail["reason"] == "blinded"


def test_a_camera_left_is_released_as_utility_left():
    def camera(rnd):
        rnd.watchers.append(ce.Watcher("camera", 1, 0.0, 60.0, in_use=[(0.0, 3.0)]))

    g, _ = _released(lambda geo, x: [_pv(geo, 1, "A", 150, 120, 0, util=[x])],
                     lambda geo, x: [_pv(geo, 1, "A", 150, 120, 0)], edit=camera)
    assert g.cause_detail["by"] == "utility" and g.cause_detail["reason"] == "utility_left"


def test_the_cause_tie_order_route_released_before_victim_turned():
    """A teammate stops watching S at 1.5 s, the route reaches S (anew) at 1.5 s, and the victim turns their
    back on S (exposed in front at 1.0 s) at 1.5 s: two causes at one time; CAUSE_ORDER puts route_released
    first. Without the teammate the same turn is `victim_turned`."""
    geo = open_hall()
    s_node, p_node = geo.cell_of_px(300, 200), geo.cell_of_px(150, 120)    # P stays beside the victim
    causes = []
    for watched in (False, True):
        d = _Drive(geo)
        ep = d.entry(p_node, 0.0)
        old = d.entry(s_node, 0.0, ep)
        mate = lambda view: [_pv(geo, 1, "A", 120, 250, 0, view=view)] if watched else []   # noqa: E731
        for t in (0.0, 0.5, 1.0):
            d.tick(t, [_pv(geo, 0, "A", 150, 200, 0), *mate([s_node])], {5: {p_node: ep, s_node: old}})
        new = d.entry(s_node, 1.5, ep)
        d.tick(1.5, [_pv(geo, 0, "A", 150, 200, 180), *mate([])], {5: {p_node: ep, s_node: new}})
        [g] = d.gaps()
        assert g.t_open == pytest.approx(1.5) and g.spot == s_node
        causes.append(g.cause)
    assert causes == ["victim_turned", "route_released"]


def test_the_cause_tie_order_victim_turned_before_victim_moved():
    """The victim steps to the door and turns at one tick: F (exposed in front from the old spot) is now behind
    them, and S (no line from the old spot) is newly exposed behind them. Both causes are at 2.5 s;
    CAUSE_ORDER puts victim_turned first."""
    geo = door_hall()
    s_node, f_node = geo.cell_of_px(244, 292), geo.cell_of_px(196, 260)
    old_sight = cast(geo, 190, 200, gd.FULL_CIRCLE, [])
    assert not old_sight[s_node] and old_sight[f_node] and cast(geo, 196, 290, gd.FULL_CIRCLE, [])[f_node]
    causes = []
    for yaw0 in (140, 90):          # without a turn the same step is `victim_moved`; with one, the tie
        d = _Drive(geo)
        es, ef = d.entry(s_node, 0.0), d.entry(f_node, 0.0)
        for t in (0.0, 0.5, 1.0, 1.5, 2.0):
            d.tick(t, [_pv(geo, 0, "A", 190, 200, yaw0)], {5: {s_node: es, f_node: ef}})
        d.tick(2.5, [_pv(geo, 0, "A", 196, 290, 140)], {5: {s_node: es, f_node: ef}})
        [g] = d.gaps()
        assert g.t_open == pytest.approx(2.5)
        causes.append(g.cause)
    assert causes == ["victim_moved", "victim_turned"]


# ---------------------------------------------------------------- R13: a clear line from the real eye


def test_a_within_cell_corner_position_changes_what_is_exposed():
    geo = door_hall()
    s_node = geo.cell_of_px(244, 292)
    assert geo.cell_of_px(192.5, 280.5) == geo.cell_of_px(199.5, 287.5), "one cell"
    found = {}
    for x, y in ((192.5, 280.5), (199.5, 287.5)):
        d = _Drive(geo)
        e = d.entry(s_node, 0.0)
        d.tick(0.0, [_pv(geo, 0, "A", x, y, 180)], {5: {s_node: e}})
        found[(x, y)] = d.gaps()
    assert found[(192.5, 280.5)] == [], "round the corner from the cell's north-west"
    assert len(found[(199.5, 287.5)]) == 1, "a clear line through the door from its south-east"


def test_a_raised_player_sees_over_the_ledge():
    from tests.replays.test_control_floors import ledge, node

    geo = ledge()
    x = 256 - 2 / 0.14
    target, own = node(geo, 268), node(geo, x)
    found = []
    for eye in (4.0 + 0.7, 4.0 + 2.4 + 0.7):          # standing on the ledge; boosted 2.4 m above it
        d = _Drive(geo)
        e = d.entry(target, 0.0)
        d.tick(0.0, [_pv(geo, 0, "A", x, 204, 180, eye_z=eye, node=own)], {5: {target: e}})
        found.append(d.gaps())
    assert found[0] == [] and len(found[1]) == 1


# ---------------------------------------------------------------- storage


def test_rows_are_json_safe():
    geo = open_hall()
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 1: ("A", [(0.0, 120, 120, 180)]),
                 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, deaths={1: 6.0, 0: 9.0})
    data["kills"] = [{"i": 0, "t": 6.0, "killer": 5, "victim": 1}, {"i": 1, "t": 9.0, "killer": 5, "victim": 0}]
    gaps = run(geo, data)
    assert gaps
    rnd = ce.RoundInputs(data, geo)
    rows = to_rows(gaps, rnd, geo)
    json.dumps(rows)
    for g in gaps:
        for value in (g.t_open, g.t_close, g.distance_m, g.angle_deg, g.qualified_s, g.spot, g.victim_node):
            assert type(value) in (int, float), (value, type(value))


# ---------------------------------------------------------------- stack merge (R1), on hand-built gaps


def _mg(seq, t_open, t_close, victim=0, life=0, spans=None, cand=None, stood=None, kind="predicted", spot=0):
    """A closed gap. cand: {enemy: (joined, distance)}; stood: {enemy: [times]} (stood_at/by the earliest)."""
    g = gd.Gap(kind, victim, "A", len(seq), tuple(seq), t_open, spot, 0, 1.0, 150.0, [[[t_open, 0.0, 0.0]]],
               life=life, t_last_exposed=t_close - gd.CLOSE_AFTER_S, t_close=t_close)
    for a, b in (spans if spans is not None else [[t_open, t_close]]):
        g._qualified(a, b)
    for e, (tj, dist) in (cand if cand is not None else {5: (t_open, 1.0)}).items():
        g.candidates[e], g.joined[e] = dist, tj
    for e, ts in (stood or {}).items():
        g.stood_times[e] = list(ts)
    if stood:
        g.stood_at, g.stood_by = min((min(ts), e) for e, ts in stood.items())
    g.context = {"t_round": t_open, "alive": {"A": 5, "B": 5}, "seq": list(seq)}
    return g


def _seqs(gaps):
    return sorted(g.choke_seq for g in gaps)


def test_a_prefix_gap_folds_into_its_extension():
    short, long_ = _mg((1,), 0.0, 10.0), _mg((1, 2), 5.0, 15.0)
    [g] = gd.merge_stacks([short, long_], 0.0)
    assert g is long_ and g.choke_seq == (1, 2), "the longest member survives with its sequence"
    assert (g.t_open, g.t_close, g.t_last_exposed) == (0.0, 15.0, 10.0)
    [g] = gd.merge_stacks([_mg((), 0.0, 10.0), _mg((3,), 2.0, 12.0)], 0.0)
    assert g.choke_seq == (3,), "the empty sequence is a prefix of every sequence (literal R1)"


def test_a_non_prefix_or_equal_sequence_stays():
    assert _seqs(gd.merge_stacks([_mg((1,), 0.0, 10.0), _mg((2, 1), 0.0, 10.0)], 0.0)) == [(1,), (2, 1)]
    assert _seqs(gd.merge_stacks([_mg((1, 3), 0.0, 10.0), _mg((1, 2), 0.0, 10.0)], 0.0)) == [(1, 2), (1, 3)]
    assert len(gd.merge_stacks([_mg((1,), 0.0, 5.0), _mg((1,), 5.0, 10.0)], 0.0)) == 2, "equal is no prefix"


def test_no_overlap_stays_and_touching_ends_overlap():
    assert len(gd.merge_stacks([_mg((1,), 0.0, 4.0), _mg((1, 2), 5.0, 10.0)], 0.0)) == 2
    assert len(gd.merge_stacks([_mg((1,), 0.0, 5.0), _mg((1, 2), 5.0, 10.0)], 0.0)) == 1, "closed intervals"


def test_a_chain_folds_transitively_into_its_longest_member():
    a, b, c = _mg((1,), 0.0, 4.0), _mg((1, 2), 3.0, 8.0), _mg((1, 2, 3), 7.0, 12.0)
    [g] = gd.merge_stacks([a, b, c], 0.0)
    assert g is c and (g.t_open, g.t_close) == (0.0, 12.0), "a reaches c through b, though a and c never overlap"
    assert g.context["merged"] == [{"choke_seq": [1], "t_open": 0.0}, {"choke_seq": [1, 2], "t_open": 3.0}]


def test_two_extensions_the_longest_then_the_earliest():
    short, mid, longest = _mg((1,), 0.0, 10.0), _mg((1, 2), 1.0, 5.0), _mg((1, 3, 4), 8.0, 12.0)
    out = gd.merge_stacks([short, mid, longest], 0.0)
    assert _seqs(out) == [(1, 2), (1, 3, 4)]
    assert [m["choke_seq"] for m in longest.context["merged"]] == [[1]] and "merged" not in mid.context
    short, late, early = _mg((1,), 0.0, 10.0), _mg((1, 3), 6.0, 9.0), _mg((1, 2), 2.0, 9.0)
    out = gd.merge_stacks([short, late, early], 0.0)
    assert _seqs(out) == [(1, 2), (1, 3)] and early.context["merged"] == [{"choke_seq": [1], "t_open": 0.0}]
    assert "merged" not in late.context
    # same length and t_open: the smaller sequence
    short, b, a = _mg((1,), 0.0, 10.0), _mg((1, 3), 2.0, 9.0), _mg((1, 2), 2.0, 9.0)
    gd.merge_stacks([short, b, a], 0.0)
    assert "merged" in a.context and "merged" not in b.context


def test_qualified_time_is_the_union_counted_once():
    [g] = gd.merge_stacks([_mg((1,), 0.0, 10.0, spans=[[0.0, 4.0]]), _mg((1, 2), 2.0, 10.0, spans=[[2.0, 6.0]])],
                          0.0)
    assert g.qualified_s == pytest.approx(6.0) and g.qual_spans == [[0.0, 6.0]]
    [g] = gd.merge_stacks([_mg((1,), 0.0, 6.0, spans=[[0.0, 0.3]]), _mg((1, 2), 0.2, 6.0, spans=[[0.2, 0.6]])],
                          0.0)
    assert g.qualified_s == pytest.approx(0.6) and g.flicker, "flicker follows the union"


def test_candidates_joined_and_stood_union_with_the_earliest():
    a = _mg((1,), 0.0, 10.0, cand={5: (0.0, 3.0), 6: (2.0, 4.0)}, stood={5: [1.0]})
    b = _mg((1, 2), 1.0, 12.0, cand={5: (1.0, 9.0), 7: (1.5, 2.0)}, stood={5: [3.0], 7: [2.5]})
    b.checked_at = [4.0]
    a.checked_at = [0.5]
    [g] = gd.merge_stacks([a, b], 0.0)
    assert g.candidates == {5: 3.0, 6: 4.0, 7: 2.0}, "each enemy's distance from the member they joined first"
    assert g.joined == {5: 0.0, 6: 2.0, 7: 1.5}
    assert dict(g.stood_times) == {5: [1.0, 3.0], 7: [2.5]}
    assert (g.stood_at, g.stood_by) == (1.0, 5)
    assert g.checked_at == [4.0], "checked_at from the longest member only"


def test_context_is_the_longest_members_with_t_round_and_merged():
    a, b = _mg((1,), 2.0, 10.0), _mg((1, 2), 4.0, 12.0)
    [g] = gd.merge_stacks([a, b], -10.0)
    assert g.context["t_round"] == pytest.approx(12.0), "recomputed from the merged t_open"
    assert g.context["seq"] == [1, 2] and g.context["alive"] == {"A": 5, "B": 5}
    assert g.context["merged"] == [{"choke_seq": [1], "t_open": 2.0}]


def test_a_different_victim_or_life_never_folds():
    assert len(gd.merge_stacks([_mg((1,), 0.0, 10.0), _mg((1, 2), 0.0, 10.0, victim=1)], 0.0)) == 2
    assert len(gd.merge_stacks([_mg((1,), 0.0, 10.0), _mg((1, 2), 0.0, 10.0, life=1)], 0.0)) == 2


def test_back_shots_never_merge():
    shot = _mg((1, 2), 0.0, 10.0, kind="backshot")
    pred = _mg((1,), 0.0, 10.0)
    out = gd.merge_stacks([pred, shot], 0.0)
    assert out == [pred, shot] and "merged" not in shot.context and pred.t_close == 10.0
    shot = _mg((), 0.0, 10.0, kind="backshot")
    out = gd.merge_stacks([shot, _mg((1,), 0.0, 10.0)], 0.0)
    assert len(out) == 2


def test_a_back_shot_links_to_the_merged_gap():
    from app.gaps import backshots as bs
    from types import SimpleNamespace

    geo = SimpleNamespace(centres=np.array([[0.0, 0.0], [100.0, 0.0]]), m_per_px=0.1)
    a, b = _mg((1,), 0.0, 10.0, spot=0), _mg((1, 2), 8.0, 15.0, spot=1, cand={6: (8.0, 1.0)})
    merged = gd.merge_stacks([a, b], 0.0)
    assert bs.link(geo, merged, 0, 0, 5, 3.0, (1,), 0.0, 0.0) is b, "enemy 5 joined through the folded member"


def test_merge_off_leaves_the_pre_merge_gaps():
    geo = open_hall()
    found = {}
    for merge in (True, False):
        d = _Drive(geo, merge=merge)
        n1, n2 = geo.cell_of_px(300, 200), geo.cell_of_px(308, 200)
        e1 = d.entry(n1, 0.0, choke=1)
        e2 = d.entry(n2, 0.0, parent=e1, choke=2)
        for t in (0.0, 0.5, 1.0):
            d.tick(t, [_pv(geo, 0, "A", 150, 200, 180)], {5: {n1: e1, n2: e2}})
        found[merge] = d.gaps()
    assert _seqs(found[False]) == [(1,), (1, 2)]
    assert all("merged" not in g.context for g in found[False])
    [g] = found[True]
    assert g.choke_seq == (1, 2) and g.context["merged"] == [{"choke_seq": [1], "t_open": 0.0}]

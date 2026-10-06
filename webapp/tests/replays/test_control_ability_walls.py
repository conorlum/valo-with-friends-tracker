"""Ability walls in time (W14; the 2026-10-05 review items 6, 26-28): what stops walking and what stops sight,
section by section and at exact times, across the unknown, the counterfactual, the knowledge picture and the gap
detector's sight; and a trip's end joined to its wall on a flat map."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control.geometry import Wall
from tests.replays.control_toys import blob, door_hall, open_hall, toy_ability, uv

# The open hall split by a wall ability across it at x 250 (y 96-296): A west of it, B east.
LINE = [uv(250, 96), uv(250, 296)]


def a_and_b(t_end=20.0):
    return {0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}


def regions(b, geo, ticks=None):
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            out[round(tick.t, 6)] = np.isfinite(unknown.reached["A"][5])

    rc = ce.compute_round(b, geo, ticks=ticks, knowledge=False, observer=Watch())
    return rc, out


def west(geo, region):
    return int((region & (geo.centres[:, 0] < 240)).sum())


def sage(segments, t=1.0, t1=40.0):
    return toy_ability("Thorne", "E_Wall_Fortifying", 250, 196, 5, t=t, t1=t1, kind="GameObject", yaw=0,
                       segments=segments)


def full_sage(t0=1.0, gone=None):
    """Four segments along x 250, y 96-296 (each 50 px, ~7 m: wider than the game's, for a toy hall that tall)."""
    gone = gone or {}
    return [[*uv(250, y), t0, gone.get(i)] for i, y in enumerate((121, 171, 221, 271))]


@pytest.fixture(autouse=True)
def long_segments(monkeypatch):
    monkeypatch.setattr(ce, "SAGE_SEGMENT_UNITS", 50 / (open_hall().uv_per_unit * ce.PX / 10000))


def test_an_intact_sage_wall_holds_the_unknown_and_blocks_sight():
    geo = open_hall()
    b = blob(a_and_b(), t_end=20.0, util=[sage(full_sage())])
    rnd = ce.RoundInputs(b, geo)
    assert len(rnd.blockers) == 4 and sum(isinstance(w, Wall) for _, _, w in rnd.walls) == 4
    _, out = regions(b, geo)
    assert west(geo, out[15.0]) == 0, "nothing crossed the wall"
    _, free = regions(blob(a_and_b(), t_end=20.0), geo)
    assert west(geo, free[15.0]) > 50


def test_a_broken_section_reopens_only_there():
    geo = open_hall()
    b = blob(a_and_b(), t_end=20.0, util=[sage(full_sage(gone={2: 8.0}))])
    _, out = regions(b, geo)
    assert west(geo, out[8.0]) == 0 and west(geo, out[15.0]) > 0
    gap = geo.node_at(geo.cell_of_px(250, 221), None)
    held = geo.node_at(geo.cell_of_px(250, 121), None)
    assert out[15.0][gap] and not out[15.0][held]


def test_expiry_reopens_the_whole_wall():
    geo = open_hall()
    _, out = regions(blob(a_and_b(), t_end=20.0, util=[sage(full_sage(), t1=6.0)]), geo)
    assert west(geo, out[5.5]) == 0 and west(geo, out[15.0]) > 50


def test_ground_already_crossed_stays_crossed_when_a_wall_goes_up():
    geo = open_hall()
    _, out = regions(blob(a_and_b(), t_end=20.0, util=[sage(full_sage(t0=12.0))]), geo)
    before, after = west(geo, out[11.5]), west(geo, out[12.0])
    assert before > 20 and after >= before - 30, "only the wall's own line is cleared"
    line = (np.abs(geo.centres[:, 0] - 252) < 6) & geo.walk_n
    assert not (out[12.0] & line).any()


def test_a_wall_up_entirely_between_frames_is_evaluated_and_integrated():
    geo = open_hall()
    b = blob(a_and_b(), t_end=20.0, util=[sage(full_sage(t0=6.2), t1=6.21)])
    rc, _ = regions(b, geo)
    assert {6.2, 6.21} <= {round(float(t), 6) for t in rc.analytic}
    assert 6.2 not in {round(float(t), 6) for t in rc.ticks} and 6.25 in {round(float(t), 6) for t in rc.ticks}
    assert rc.timings.get("analytic_only_ticks") == 2
    ref, _ = regions(b, geo, ticks=sorted({*np.round(rc.ticks, 6).tolist(), 6.2, 6.21}))
    for s in rc.players:
        assert rc.players[s].as_dict() == ref.players[s].as_dict()


def test_the_counterfactual_and_the_knowledge_picture_respect_the_wall():
    geo = open_hall()
    b = blob(a_and_b(), t_end=20.0, util=[sage(full_sage())])
    rnd = ce.RoundInputs(b, geo)
    runner = ce.TickRunner(geo)
    tick = None
    for t in np.arange(0.0, 15.01, 0.5):
        tick = runner.step(ce.Tick(rnd, float(t)))
    assert tick.sealed[rnd.blockers[0][2]].all(), "a wall up now is shut in the counterfactual's unknown"
    without = tick.unknown_without("A", 0)
    assert west(geo, without) == 0
    kn = ce.Knowledge(rnd, "A")
    kt = kn.tick_for(tick, 15.0)
    assert west(geo, kt.seeds.get("B", np.zeros(geo.n, bool))) == 0


def test_a_mesh_blocks_walking_but_not_sight():
    geo = open_hall()
    mesh = toy_ability("Cable", "E_CableJam_Root", 250, 196, 5, t=1.0, t1=40.0, kind="GameObject",
                       on=[[2.0, 40.0]], arms=[[*uv(250, 96), 10, None], [*uv(250, 296), 10, None]])
    b = blob(a_and_b(), t_end=20.0, util=[mesh])
    rnd = ce.RoundInputs(b, geo)
    assert len(rnd.blockers) == 2 and rnd.walls == []
    _, out = regions(b, geo)
    assert west(geo, out[15.0]) == 0


def test_a_mesh_arm_shot_out_reopens_its_side():
    geo = open_hall()
    mesh = toy_ability("Cable", "E_CableJam_Root", 250, 196, 5, t=1.0, t1=40.0, kind="GameObject",
                       on=[[2.0, 40.0]], arms=[[*uv(250, 96), 10, 6.0], [*uv(250, 296), 10, None]])
    _, out = regions(blob(a_and_b(), t_end=20.0, util=[mesh]), geo)
    assert west(geo, out[15.0]) > 0
    north = geo.centres[:, 1] < 190
    assert (out[15.0] & north & (geo.centres[:, 0] < 240)).any()


def test_a_sonic_sensor_holds_nothing():
    geo = open_hall()
    sensors = [toy_ability("Cable", "Q_SoundSensor", 250, y, 5, t=1.0, t1=40.0, kind="GameObject") for y in (110, 200, 290)]
    _, out = regions(blob(a_and_b(), t_end=20.0, util=sensors), geo)
    _, free = regions(blob(a_and_b(), t_end=20.0), geo)
    assert ce.RoundInputs(blob(a_and_b(), util=sensors), geo).blockers == []
    assert west(geo, out[15.0]) == west(geo, free[15.0])


def shear(raised):
    return toy_ability("Nox", "WallTrap", 250, 96, 5, t=1.0, t1=40.0, kind="GameObject", line=LINE, raised=raised)


def test_a_shear_lets_the_crossing_through_then_blocks_until_it_ends():
    geo = open_hall()
    _, untriggered = regions(blob(a_and_b(), t_end=20.0, util=[shear(None)]), geo)
    _, free = regions(blob(a_and_b(), t_end=20.0), geo)
    assert west(geo, untriggered[15.0]) == west(geo, free[15.0]), "not raised: nothing"
    _, out = regions(blob(a_and_b(), t_end=20.0, util=[shear([12.0, 16.0])]), geo)
    crossed = west(geo, out[11.5])
    assert crossed > 0
    assert west(geo, out[12.0]) >= crossed - 30, "what was already across stays"
    line = (np.abs(geo.centres[:, 0] - 252) < 6) & geo.walk_n
    assert not (out[14.0] & line).any(), "nothing is on the raised wall's line"
    assert (out[17.0] & line).any(), "open again after it ends"
    rnd = ce.RoundInputs(blob(a_and_b(), t_end=20.0, util=[shear([12.0, 16.0])]), geo)
    assert len(rnd.walls) == 1 and rnd.walls[0][:2] == (12.0, 16.0)


def test_a_blaze_blocks_sight_only():
    geo = open_hall()
    blaze = toy_ability("Phoenix", "Q_FlameWallManager_Production", 250, 96, 5, t=2.0, t1=12.0, kind="GameObject",
                        points=LINE, on=[[2.0, 12.0]])
    rnd = ce.RoundInputs(blob(a_and_b(), util=[blaze]), geo)
    assert rnd.blockers == [] and len(rnd.walls) == 1 and rnd.smokes_at(5.0)


def test_a_flat_trip_joins_the_wall_beside_its_end():
    geo = door_hall()                # the wall x 200-216 ends at y 288; a wire from beside its foot to the south edge
    wire = toy_ability("Gumshoe", "4_TripWire", 220, 290, 0, t=0.0, t1=20.0, kind="GameObject", end=list(uv(220, 295)))
    rnd = ce.RoundInputs(blob({0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}, util=[wire]), geo)
    [w] = [w for w in rnd.watchers if w.kind == "trip"]
    joined = rnd._join_wall(np.array([220.0, 290.0]))
    assert joined is not None and joined[0] < 217
    plain = rnd._trip_nodes(np.array([220.0, 290.0]), np.array([220.0, 295.0]), None, None, joined=True)
    assert set(plain) < set(w.cells.tolist())


def test_a_wire_with_no_wall_near_keeps_its_ends():
    geo = open_hall()
    rnd = ce.RoundInputs(blob({0: ("A", [(0.0, 104, 200, 180)])}, util=[]), geo)
    assert rnd._join_wall(np.array([250.0, 200.0])) is None

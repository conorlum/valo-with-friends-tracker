"""How players move, for the height build (app/control/height_motion.py;
docs/superpowers/specs/2026-10-05-height-slopes-design.md, parts 1 and 1b): walks and blackouts, on toy rounds
stored at the rate of real ones (125 Hz). Positions are minimap pixels; cells are 8 px (1.12 m)."""

import pytest

from app.control import height_build as hb
from app.control import height_motion as hm
from tests.replays.control_toys import FAST_HZ, TOY_Z0, fast_blob, open_hall, stair_run, z_dm

GEO = open_hall()
GRID = 128
Y = 204
ROW = Y // 8


def cell(c: int, r: int = ROW) -> int:
    return r * GRID + c


def test_a_run_up_stairs_is_a_walk_with_each_cells_low_end():
    b = fast_blob({0: stair_run()})
    found = hb.stands(b, GEO)
    [climb] = [w for w in hm.walks(b, GEO, found=found) if cell(22) in w.cells]
    lows = dict(zip(climb.cells, climb.low))
    for c in range(21, 27):        # 4 m over 64 px: half a metre a cell, lowest at each cell's west edge
        assert abs((lows[cell(c)] - TOY_Z0) / 10 - (c * 8 - 160) / 64 * 4.0) <= 0.11, c
    assert not any(cell(c) in s.cells for s in found for c in range(22, 26)), "nobody stood on the stairs"


def test_a_running_jump_and_a_fall_are_not_walks():
    n = int(0.35 * FAST_HZ)
    arc = [(1.0 + i / FAST_HZ, 192 + 32 * i / FAST_HZ, Y, 0, 0.6 * 4 * (i / n) * (1 - i / n)) for i in range(1, n + 1)]
    hop = ("A", [(0.0, 160, Y, 0, 0.0), (1.0, 192, Y, 0, 0.0), *arc, (3.0, 260, Y, 0, 0.0), (10.0, 300, Y, 0, 0.0)])
    b = fast_blob({0: hop})
    assert not [w for w in hm.walks(b, GEO, found=hb.stands(b, GEO)) if w.t0 < 1.25 and w.t1 > 1.1], \
        "past its first and last tenth of a second, the jump is nobody's ground"
    n = int(0.63 * FAST_HZ)        # off a 4 m ledge at a run: free fall at 20 m/s2
    drop = [(1.0 + i / FAST_HZ, 200 + 40 * i / FAST_HZ, Y, 0, 4.0 - 10.0 * (i / FAST_HZ) ** 2) for i in range(1, n + 1)]
    fall = ("A", [(0.0, 160, Y, 0, 4.0), (1.0, 200, Y, 0, 4.0), *drop, (1.64, 225.6, Y, 0, 0.0), (4.0, 300, Y, 0, 0.0),
                  (10.0, 300, Y, 0, 0.0)])
    b = fast_blob({0: fall})
    found = hm.walks(b, GEO, found=hb.stands(b, GEO))
    assert all(w.t1 <= 1.1 or w.t0 >= 1.5 for w in found), "no walk spans the fall"
    before = [z for w in found if w.t1 <= 1.1 for z in w.low]
    assert min(before, default=z_dm(4.0)) >= z_dm(3.9), \
        "what a walk keeps of the fall's start is within a decimetre of the ledge"
    below = GEO.cell_of_px(212, Y)
    assert all(dict(zip(w.cells, w.low)).get(below, 0) <= z_dm(0.2) for w in found), "the cell fallen past: ground only"


def test_a_walk_needs_z_and_leaves_a_stands_samples_to_the_stand():
    level = ("A", [(0.0, 160, Y, 0, 1.0), (10.0, 300, Y, 0, 1.0)])
    b = fast_blob({0: level})
    found = hb.stands(b, GEO)
    assert len(found) == 1 and hm.walks(b, GEO, found=found) == [], "a level walk is already a stand"
    assert len(hm.walks(b, GEO)) == 1, "and a walk when no stand claims it"
    no_z = fast_blob({0: stair_run()})
    for seg in no_z["tracks"]["0"]:
        del seg["z"]
    assert hm.walks(no_z, GEO) == []


def test_a_dash_an_updraft_and_a_listed_cast_start_a_blackout():
    dash = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 0.0), (10.0, 216, Y, 0, 0.0)])
    up = ("A", [(0.0, 300, Y, 0, 0.0), (4.0, 300, Y, 0, 0.0), (4.4, 300, Y, 0, 5.0), (5.2, 300, Y, 0, 0.0),
                (10.0, 300, Y, 0, 0.0)])
    runner = ("B", [(0.0, 160, 252, 0, 0.0), (10.0, 400, 252, 0, 0.0)])          # 3.4 m/s: no burst
    cast = {"k": "ability", "t": 6.0, "t1": 6.5, "by": 2, "kind": "GameObject", "code": "Clay", "name": "Q_Explosion",
            "u": 0, "v": 0}
    b = fast_blob({0: dash, 1: up, 2: runner}, util=[cast])
    out = hm.blackouts(b, GEO)
    [(d0, d1)], [(u0, u1)] = out[0], out[1]
    assert 1.9 <= d0 <= 2.01 and 5.2 <= d1 <= 5.45 and 3.9 <= u0 <= 4.01 and 7.3 <= u1 <= 7.55
    assert out[2] == [(6.0, 9.0)], "a listed cast, from its time"
    kept = hb.stands(b, GEO, skip=out)
    assert not any(s.slot == 0 and s.t0 < d1 and s.t1 > d0 for s in kept), "nothing of the dasher inside it"
    assert any(s.slot == 0 and s.t1 <= d0 for s in kept) and any(s.slot == 0 and s.t0 >= d1 for s in kept), \
        "what they did before it and what they do after it count as normal"


def test_a_box_someone_dashed_onto_counts_from_three_seconds_on():
    # Lands on a 1.5 m box at 2.3 s and stays: the stand there starts when the blackout ends.
    onto = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 1.5), (10.0, 216, Y, 0, 1.5)])
    b = fast_blob({0: onto})
    skip = hm.blackouts(b, GEO)
    [(_, t1)] = skip[0]
    [after] = [s for s in hb.stands(b, GEO, skip=skip) if s.z == z_dm(1.5)]
    assert after.t0 > t1 - 1e-6 and after.t1 == 10.0


def test_tracks_cut_a_segment_at_a_blackout_and_overlapping_blackouts_merge():
    b = fast_blob({0: ("A", [(0.0, 160, Y, 0, 0.0), (10.0, 300, Y, 0, 0.0)])})
    whole = list(hm.tracks(b))
    pieces = list(hm.tracks(b, {0: [(2.0, 4.0)]}))
    assert len(whole) == 1 and len(pieces) == 2
    assert pieces[0][1][-1] < 2.0 and pieces[1][1][0] > 4.0
    assert len(pieces[0][1]) + len(pieces[1][1]) == len(whole[0][1]) - (2 * FAST_HZ + 1)
    assert hm._merge([(5.0, 8.0), (1.0, 4.0), (3.0, 6.0)]) == [(1.0, 8.0)]
    assert list(hm.tracks(b, {1: [(0.0, 10.0)]}))[0][1].shape == whole[0][1].shape, "another player's blackout"


# ---------------------------------------------------------------- flights (one parabola over a window can't tell)


def flown(z_of, v: float = 5.0, seconds: float = 3.0):
    """A player moving east at `v` m/s whose height is z_of(t), a point per stored sample."""
    px_per_s = v / GEO.m_per_px
    return "A", [(i / FAST_HZ, 100 + px_per_s * i / FAST_HZ, Y, 0, z_of(i / FAST_HZ))
                 for i in range(int(seconds * FAST_HZ) + 1)]


def spans(z_of, v: float = 5.0):
    """(the walks' (t0, t1), the times of the samples in the air, the times of the ones on the ground) for that
    player. Level running is a stand, not a walk, so "the ground" is `on_ground`'s own answer."""
    b = fast_blob({0: flown(z_of, v)}, t_end=3.0)
    [(_, t, x, y, z)] = list(hm.tracks(b))
    air = hm.airborne(x, y, z.astype(float), FAST_HZ, GEO.m_per_px)
    ground = hm.on_ground(x, y, z.astype(float), FAST_HZ, GEO.m_per_px)
    return [(w.t0, w.t1) for w in hm.walks(b, GEO, found=hb.stands(b, GEO))], t[air], t[ground]


def inside(times, t0, t1):
    return bool(((times > t0) & (times < t1)).any())


def drop(height: float, before=lambda t: 0.0, after=lambda t: 0.0):
    """z(t): `before` (ending at `height` at t = 1), a fall of `height` from rest, then `after` from the landing."""
    air = (height / 10) ** 0.5
    return (lambda t: height + before(t - 1) if t < 1 else height - 10 * (t - 1) ** 2 if t < 1 + air
            else after(t - 1 - air)), air


@pytest.mark.parametrize("height", [0.5, 0.9, 1.1, 2.0])
def test_a_fall_off_a_ledge_is_no_walk_whatever_its_height(height):
    z_of, air = drop(height)
    walks, in_air, ground = spans(z_of)
    assert not any(t0 < 1 + air - 0.03 and t1 > 1.03 for t0, t1 in walks), "no walk holds the inside of the fall"
    assert not inside(ground, 1.03, 1 + air - 0.03), "nor is any sample of it ground"
    assert in_air.min() <= 1.02 and in_air.max() >= 1 + air - 0.02, "the whole flight is found"
    assert inside(ground, 0.3, 0.85) and inside(ground, 1 + air + 0.05, 2.7), "the ground before and after it stays"


def test_a_fall_between_two_ramps_is_no_walk():
    # Down a ramp at 2 m/s, off its end, 1.1 m through the air, and up another: the takeoff and the landing bend
    # z the other way from the fall, and one parabola over a window sees almost nothing.
    z_of, air = drop(1.1, before=lambda t: -2 * t, after=lambda t: 2 * t)
    walks, in_air, ground = spans(z_of)
    assert not any(t0 < 1 + air - 0.03 and t1 > 1.03 for t0, t1 in walks)
    assert not inside(ground, 1.03, 1 + air - 0.03)
    assert len(walks) == 2 and walks[0][1] <= 1.0 and walks[1][0] >= 1 + air - 0.01, "a walk down, and a walk up"


@pytest.mark.parametrize("v, name", [(5.0, "running"), (2.9, "walking"), (0.0, "standing")])
def test_a_jump_is_no_walk(v, name):
    walks, in_air, ground = spans(lambda t: 4.6 * (t - 1) - 10 * (t - 1) ** 2 if 1 <= t < 1.46 else 0.0, v)
    assert not any(t0 < 1.43 and t1 > 1.03 for t0, t1 in walks) and not inside(ground, 1.03, 1.43), name
    assert in_air.min() <= 1.02 and in_air.max() >= 1.44, name
    down, _, ground = spans(lambda t: 0.8 if t < 1 else 0.8 + 4.6 * (t - 1) - 10 * (t - 1) ** 2 if t < 1.594 else 0.0, v)
    assert not any(t0 < 1.56 and t1 > 1.03 for t0, t1 in down) and not inside(ground, 1.03, 1.56), \
        "nor a jump down off a 0.8 m ledge"


@pytest.mark.parametrize("name, z_of", [
    ("stairs up", lambda t: 0.0 if t < 1 else 2.5 * (t - 1) if t < 2 else 2.5),
    ("steep stairs up", lambda t: 0.0 if t < 1 else 4.5 * (t - 1) if t < 2 else 4.5),
    ("steep stairs down", lambda t: 4.5 if t < 1 else 4.5 - 4.5 * (t - 1) if t < 2 else 0.0),
    ("over a crest", lambda t: 2.5 * t if t < 1 else 2.5 - 2.5 * (t - 1)),
])
def test_where_a_slope_begins_or_ends_nobody_is_in_the_air(name, z_of):
    walks, in_air, ground = spans(z_of)
    assert len(in_air) == 0, name
    assert len(ground) == 3 * FAST_HZ + 1, "every sample is ground, straight through both bends"
    assert len(walks) == 1, "and the slope between them is one walk"


def test_a_round_with_recorded_casts_starts_blackouts_from_them_and_not_from_speed():
    dash = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 216, Y, 0, 0.0), (10.0, 216, Y, 0, 0.0)])
    sprint = ("B", [(0.0, 160, 252, 0, 0.0), (4.0, 160, 252, 0, 0.0), (4.3, 216, 252, 0, 0.0), (10.0, 216, 252, 0, 0.0)])
    cast = {"k": "cast", "t": 2.0, "by": 0, "code": "Wushu", "name": "E_Dash"}
    other = {"k": "cast", "t": 6.0, "by": 0, "code": "Wushu", "name": "4_Smoke"}
    b = fast_blob({0: dash, 1: sprint}, util=[cast, other])
    old = hm.blackouts(b, GEO)
    assert 1.9 <= old[0][0][0] <= 2.01 and 1 in old, "condensed before casts were recorded: both are told by speed"
    b["movement_casts"] = 1
    new = hm.blackouts(b, GEO)
    assert new == {0: [(2.0, 5.0)]}, "the cast, from its time; a smoke is no movement ability; speed alone is nothing"
    b["util"] = []
    assert hm.blackouts(b, GEO) == {}, "a round known to have no cast has no blackout, however fast anyone moved"


def test_an_updraft_still_starts_a_blackout_in_a_round_with_casts():
    # PROVISIONAL(D3): the updraft has no recorded cast, so rising fast still counts where dashes are casts.
    up = ("A", [(0.0, 300, Y, 0, 0.0), (4.0, 300, Y, 0, 0.0), (4.4, 300, Y, 0, 5.0), (5.2, 300, Y, 0, 0.0),
                (10.0, 300, Y, 0, 0.0)])
    b = fast_blob({0: up})
    b["movement_casts"] = 1
    [(u0, u1)] = hm.blackouts(b, GEO)[0]
    assert 3.9 <= u0 <= 4.01 and 7.3 <= u1 <= 7.55

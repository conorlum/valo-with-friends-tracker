"""The height build on toy maps (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 3):
stands, floors, fill, connections, unresolved areas, readiness, the asset and the command."""

import numpy as np
import pytest

from app.control import height_build as hb
from app.control import heights as hc
from tests.replays.control_toys import (HZ, TOY_Z0, height_blob, height_rounds, open_hall, standing, toy_ability,
                                        z_dm)

GEO = open_hall()
# A spot in the hall and its cell: (200, 200) px is the middle of cell column 25, row 25.
X, Y = 204, 204
CELL = GEO.cell_of_px(X, Y)


def floors_at(rounds, cell=CELL, geo=GEO):
    found, round_match, _ = hb.all_stands(rounds, geo)
    floors, reasons = hb.cell_floors(found, round_match, geo)
    return floors.get(cell), reasons.get(cell)


def heights_m(floors):
    return [round((z - TOY_Z0) / 10, 1) for z, _ in floors]


# ---------------------------------------------------------------- stands


def test_a_player_standing_still_is_one_stand_at_their_height():
    [stand] = hb.stands(height_blob({0: standing(X, Y, 2.5)}, t_end=5.0), GEO)
    assert (stand.slot, stand.z, stand.cells) == (0, z_dm(2.5), (CELL,))
    assert (stand.t0, stand.t1) == (0.0, 5.0)


def test_a_stand_belongs_to_every_cell_it_passes_through():
    walk = ("A", [(0.0, 204, 204, 0, 1.0), (4.0, 228, 204, 0, 1.0)])     # three cells east, level
    [stand] = hb.stands(height_blob({0: walk}, t_end=4.0), GEO)
    assert stand.cells == (CELL, CELL + 1, CELL + 2, CELL + 3)


def test_a_round_without_heights_has_no_stands():
    assert hb.stands(height_blob({0: ("A", [(0.0, X, Y, 0)])}, t_end=5.0), GEO) == []


def jump(t: float, top: float = 0.6, air: float = 0.7):
    """z of a jump starting at t: a parabola `air` seconds long reaching `top` metres."""
    steps = int(air * HZ)
    return [(t + i / HZ, top * 4 * (i / steps) * (1 - i / steps)) for i in range(steps + 1)]


def test_jumps_a_rope_a_fall_and_a_boost_make_no_floor():
    # Spec, Height build tests: "repeated jumps, a rope climb, a boost and a fall make no floor".
    def players(m, n):
        hops = [(0.0, 0.0)] + [p for k in range(6) for p in jump(1.0 + k * 1.0)] + [(8.0, 0.0), (10.0, 0.0)]
        jumper = ("A", [(t, X, Y, 0, z) for t, z in hops])
        rope = ("A", [(0.0, X, Y, 0, 0.0), (2.0, X, Y, 0, 0.0), (5.0, X, Y, 0, 9.0), (5.1, X + 40, Y, 0, 9.0)])
        fall = ("B", [(0.0, X + 40, Y, 0, 6.0), (3.0, X + 8, Y, 0, 6.0), (3.2, X, Y, 0, 5.0), (4.0, X, Y, 0, 0.0),
                      (10.0, X, Y, 0, 0.0)])
        out = {0: jumper, 1: rope, 5: fall}
        if (m, n) == (0, 1):
            out[6] = standing(X, Y, 1.8, "B")      # on a teammate's head, once
        return out

    floors, why = floors_at(height_rounds(players))
    assert why is None and heights_m(floors) == [0.0]


def test_the_top_of_a_jump_is_not_a_stand_but_a_box_someone_stays_on_is():
    hop = ("A", [(t, X, Y, 0, z) for t, z in [(0.0, 0.0), *jump(1.0), (3.0, 0.0)]])
    assert {s.z for s in hb.stands(height_blob({0: hop}, t_end=3.0), GEO)} == {z_dm(0.0)}
    box = ("A", [(0.0, X, Y, 0, 0.0), (1.0, X, Y, 0, 0.0), (1.2, X, Y, 0, 1.2), (3.0, X, Y, 0, 1.2),
                 (3.2, X, Y, 0, 0.0), (5.0, X, Y, 0, 0.0)])
    assert z_dm(1.2) in {s.z for s in hb.stands(height_blob({0: box}, t_end=5.0), GEO)}


# ---------------------------------------------------------------- floors


def test_two_floors_stay_two_with_airborne_samples_between_them():
    def players(m, n):
        drop = ("B", [(0.0, X, Y, 0, 5.0), (2.0, X, Y, 0, 5.0), (2.6, X, Y, 0, 0.0), (10.0, X, Y, 0, 0.0)])
        return {0: standing(X, Y, 0.0), 1: standing(X, Y, 5.0), 5: drop}

    floors, why = floors_at(height_rounds(players))
    assert why is None and heights_m(floors) == [0.0, 5.0]
    assert all(spread <= 1 for _, spread in floors)


def test_a_platform_used_in_one_match_is_not_a_floor():
    def players(m, n):
        out = {0: standing(X, Y, 0.0)}
        if m == 0:
            out[1] = standing(X, Y, 3.0)
            out[2] = standing(X, Y, 3.0)
        return out

    floors, why = floors_at(height_rounds(players))
    assert why is None and heights_m(floors) == [0.0], "six stands in three rounds, but of one match"


def test_stands_by_a_sage_wall_are_dropped():
    wall = toy_ability("Thorne", "E_Wall_Fortifying", X + 8, Y, 1, t=1.0, t1=9.0, kind="GameObject")

    def blobs(m, n):
        return height_blob({0: standing(X, Y, 0.0), 1: standing(X, Y, 3.0), 2: standing(X + 240, Y, 0.0, "B")},
                           t_end=10.0, util=[wall], n=n)

    rounds = height_rounds(blobs)
    found, _, counts = hb.all_stands(rounds, GEO)
    assert counts["on_platforms"] == 12 and {s.slot for s in found} == {2}, "far from the wall stays"
    assert floors_at(rounds) == (None, None)
    without = height_rounds(lambda m, n: {0: standing(X, Y, 0.0), 1: standing(X, Y, 3.0)})
    assert heights_m(floors_at(without)[0]) == [0.0, 3.0], "the same stands with no wall are two floors"


def test_crouching_stays_on_its_floor():
    floors, why = floors_at(height_rounds(lambda m, n: {0: standing(X, Y, 0.0), 1: standing(X, Y, -0.3)}))
    assert why is None and len(floors) == 1 and heights_m(floors)[0] in (-0.3, -0.2, -0.1, 0.0)


def ramp_walk(rise_m: float, side="A"):
    """Walks slowly east across the cell (1.12 m) while climbing `rise_m`, and back down."""
    x0, x1 = X - 4, X + 4
    return side, [(0.0, x0, Y, 0, 0.0), (5.0, x1, Y, 0, rise_m), (10.0, x0, Y, 0, 0.0)]


def test_a_gentle_ramp_is_one_floor_and_a_steep_one_is_unresolved():
    gentle, why = floors_at(height_rounds(lambda m, n: {0: ramp_walk(0.22), 1: ramp_walk(0.22)}))
    assert why is None and len(gentle) == 1 and gentle[0][1] <= hc.FLOOR_SPREAD_MAX_M * 10
    steep, why = floors_at(height_rounds(lambda m, n: {0: ramp_walk(2.4), 1: ramp_walk(2.4)}))
    assert steep is None and why in (hb.SPREAD, hb.TOO_CLOSE)


def test_four_floors_are_refused_not_cut_down():
    stack = lambda m, n: {s: standing(X, Y, 3.0 * s) for s in range(4)}    # noqa: E731
    floors, why = floors_at(height_rounds(stack))
    assert floors is None and why == hb.TOO_MANY


def test_floors_need_five_stands_three_rounds_and_two_matches():
    one = lambda m, n: {0: standing(X, Y, 0.0)}    # noqa: E731
    assert floors_at(height_rounds(one, matches=2, rounds=2))[0] is None, "four stands"
    assert floors_at(height_rounds(lambda m, n: {0: standing(X, Y, 0.0), 1: standing(X, Y, 0.0)}, 2, 1))[0] is None, \
        "four stands in two rounds"
    assert heights_m(floors_at(height_rounds(one, matches=2, rounds=3))[0]) == [0.0]


def test_group_cell_takes_the_densest_group_first():
    z = np.array([0, 0, 1, 0, 0, 1, 50, 50, 51, 50, 50, 49, 30.0])
    rounds = np.array([0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 0])
    floors, why = hb.group_cell(z, rounds, rounds // 3)
    assert why is None and [h for h, _ in floors] == [0, 50], "the single stand at 3 m is nobody's floor"

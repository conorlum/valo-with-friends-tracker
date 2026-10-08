"""The height build on toy maps (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 3):
stands, floors, fill, connections, unresolved areas, readiness, the asset and the command."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import geometry as cg
from app.control import height_build as hb
from app.control import height_motion as hm
from app.control import heights as hc
from app.replays import format as fmt
from app.replays import height_inputs
from tests.replays.control_toys import (HZ, TOY_Z0, fast_rounds, height_blob, height_rounds, open_hall, stair_run,
                                        standing, toy_ability, z_dm)

GEO = open_hall()
# A spot in the hall and its cell: (200, 200) px is the middle of cell column 25, row 25.
X, Y = 204, 204
CELL = GEO.cell_of_px(X, Y)


def floors_at(rounds, cell=CELL, geo=GEO):
    found, found_walks, round_match, _ = hb.all_ground(rounds, geo)
    floors, reasons, _ = hb.cell_floors(found, round_match, geo, found_walks)
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


def test_a_level_walk_that_starts_on_a_sage_wall_keeps_only_its_cells_away_from_it():
    # The stand's mean position is far from the wall, but its first cells are on it.
    wall = toy_ability("Thorne", "E_Wall_Fortifying", X, Y, 1, t=0.0, t1=10.0, kind="GameObject")
    walk = ("A", [(0.0, X, Y, 0, 3.0), (10.0, 404, Y, 0, 3.0)])
    rounds = height_rounds(lambda m, n: height_blob({k: walk for k in range(3)}, t_end=10.0, util=[wall], n=n))
    found, _, counts = hb.all_stands(rounds, GEO)
    far = GEO.cell_of_px(396, Y)
    assert len(found) == 18 and counts["on_platforms"] == 0, "the stands stay"
    assert all(CELL not in s.cells and CELL + 4 not in s.cells and far in s.cells for s in found)
    assert floors_at(rounds) == (None, None), "no floor at the wall"
    assert heights_m(floors_at(rounds, cell=far)[0]) == [3.0], "the ground far from it is still learnt"


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


def test_a_stray_stand_between_two_floors_is_nobodys_floor():
    z = np.array([0, 0, 1, 0, 0, 1, 50, 50, 51, 50, 50, 49, 25.0])
    rounds = np.array([0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 0])
    floors, why, kind = hb.group_cell(z, rounds, rounds // 3)
    assert why is None and [h for h, _ in floors] == [0, 50] and kind == hc.KIND_STANDS, \
        "the single stand at 2.5 m is a band of its own, too thin to be a floor; a floor is its low end"


@pytest.mark.parametrize("loose_z, reason", [(15, hb.TOO_CLOSE), (20, None)])
def test_final_floor_heights_keep_the_minimum_separation(loose_z, reason):
    # Supported bands at 0 and 20..30 dm start 2 m apart. Three unsupported low samples
    # join the upper band and can pull its 10th percentile down to 15 dm.
    z = np.array([0] * 6 + [25] * 6 + [20] * 6 + [30] * 6 + [loose_z] * 3, float)
    rounds = np.tile(np.arange(6), 5)[:len(z)]
    stand = np.array([True] * 12 + [False] * 15)
    floors, why, kind = hb.group_cell(z, rounds, rounds // 3, stand)
    assert why == reason
    if reason:
        assert floors == [] and kind == hc.KIND_NONE
    else:
        assert [h for h, _ in floors] == [0, 20], "exactly 2 m apart remains valid"


# ---------------------------------------------------------------- fill, connections, areas, readiness

GRID = 128
ROW = Y // 8                  # cell row 25
col = lambda x: x // 8        # noqa: E731


def cell(c: int, r: int = ROW) -> int:
    return r * GRID + c


def level_walk(x0: int, x1: int, z_m: float, y: int = Y, side: str = "A"):
    """Walks from x0 to x1 px along one row at one height: a single stand over every cell on the way."""
    return side, [(0.0, x0 + 4, y, 0, z_m), (9.0, x1 + 4, y, 0, z_m), (10.0, x1 + 4, y, 0, z_m)]


def built(make, geo=GEO, **kw):
    return hb.build(height_rounds(make, **kw), geo)


def floor_m(b, c, r=ROW):
    return [round(int(h) / 10, 1) for h in b.asset.floors[r, c] if h >= 0]


def test_an_unsampled_cell_between_a_drop_is_unresolved_and_between_agreeing_neighbours_it_fills():
    # Row 25: ground at 0 m west of column 25 and at 4 m east of it. Row 31: 0 m on both sides.
    b = built(lambda m, n: {0: level_walk(100, 192, 0.0), 1: level_walk(208, 300, 4.0),
                            2: level_walk(100, 192, 0.0, y=252), 3: level_walk(208, 300, 0.0, y=252)})
    assert floor_m(b, 24) == [0.0] and floor_m(b, 26) == [4.0]
    assert floor_m(b, 25) == [] and b.asset.unresolved[ROW, 25], "never 2 m: filling doesn't average a drop"
    assert b.reasons[cell(25)] == hb.NEIGHBOURS_DISAGREE
    assert floor_m(b, 25, 31) == [0.0] and not b.asset.unresolved[31, 25], "agreeing neighbours fill it"
    assert not b.asset.supported[31, 25] and b.asset.supported[31, 24], "filled is not supported"
    # two rows from a sampled row still fills (FILL_R = 2); three rows away has no samples
    assert floor_m(b, 15, 33) == [0.0] and b.reasons[cell(15, 35)] == hb.NO_SAMPLES
    assert b.asset.origin_z == z_dm(0.0), "the lowest floor is the origin, in world decimetres"


def step_then(x0: int, x1: int, z0: float, z1: float, t_move: float = 5.0, side: str = "A"):
    """Stands at x0 (height z0), then is at x1 (height z1) a quarter of a second later: a jump up or a drop,
    too abrupt to be a walk."""
    return side, [(0.0, x0 + 4, Y, 0, z0), (t_move, x0 + 4, Y, 0, z0), (t_move + 0.25, x1 + 4, Y, 0, z1),
                  (10.0, x1 + 4, Y, 0, z1)]


def edges_of(b):
    return {tuple(e[:4]) for e in b.asset.edges.tolist()}


def test_a_walked_step_connects_both_ways_and_a_drop_seen_only_downward_is_one_way():
    # Column 20 at 0 m and 21 at 1.2 m, climbed. Column 30 at 3 m and 31 at 0 m, only ever dropped from.
    # Column 40 at 0 m beside 41 at 4 m, never walked between.
    def players(m, n):
        return {0: step_then(160, 168, 0.0, 1.2), 1: step_then(240, 248, 3.0, 0.0),
                2: standing(324, Y, 0.0), 3: standing(332, Y, 4.0)}

    b = built(players)
    e = edges_of(b)
    assert floor_m(b, 20) == [0.0] and floor_m(b, 21) == [1.2]
    up, down = (cell(20), 0, cell(21), 0), (cell(21), 0, cell(20), 0)
    assert up in e and down in e, "a climb that was walked connects both ways"
    assert (cell(30), 0, cell(31), 0) in e and (cell(31), 0, cell(30), 0) not in e, "a drop is one-way"
    assert (cell(40), 0, cell(41), 0) not in e and (cell(41), 0, cell(40), 0) not in e, "an unwalked cliff: neither"
    assert b.report["one_way_edges"] >= 1


def test_a_big_step_walked_in_one_round_only_does_not_connect():
    def players(m, n):
        climb = step_then(160, 168, 0.0, 1.2) if (m, n) == (0, 1) else standing(164, Y, 0.0)
        return {0: climb, 1: standing(172, Y, 1.2)}

    e = edges_of(built(players))
    assert (cell(20), 0, cell(21), 0) not in e and (cell(21), 0, cell(20), 0) not in e


def test_neighbouring_floors_within_a_step_connect_both_ways_diagonals_too():
    b = built(lambda m, n: {0: level_walk(160, 200, 0.0), 1: level_walk(160, 200, 0.5, y=Y + 8)})
    e = edges_of(b)
    a, east, south, south_east = cell(21), cell(22), cell(21, ROW + 1), cell(22, ROW + 1)
    for other in (east, south, south_east):
        assert (a, 0, other, 0) in e and (other, 0, a, 0) in e
    assert all(len(row) == 5 and row[4] == hc.EDGE_STEP for row in b.asset.edges.tolist())
    assert b.report["edges"] == len(e)


def test_a_floor_reached_only_through_the_air_is_listed():
    # A platform 6 m up over columns 40-41 that nobody walks onto (a rope), in a hall whose ground is all
    # resolved (an unresolved cell beside it would join it to the ground, as the engine walks it).
    def players(m, n):
        return {**{k: sweep(k) for k in range(9)}, 9: level_walk(320, 328, 6.0, side="B")}

    b = built(players, t_end=SWEEP_S)
    assert b.report["unresolved_cells"] == 0
    assert floor_m(b, 40) == [0.0, 6.0] and floor_m(b, 41) == [0.0, 6.0]
    [island] = b.report["air_only"]
    assert island["cells"] == 2 and island["bbox"] == [320, 200, 335, 207]
    assert island["z"] == [z_dm(6.0), z_dm(6.0)]
    assert (cell(40), 1, cell(41), 1) in edges_of(b), "the platform's own cells connect to each other"
    assert b.report["cells_2_floors"] == 2 and b.report["cells_3_floors"] == 0


def test_unresolved_areas_carry_their_size_box_and_reason():
    b = built(lambda m, n: {0: level_walk(100, 192, 0.0), 1: level_walk(208, 300, 4.0)})
    areas = b.report["unresolved_areas"]
    assert sum(a["cells"] for a in areas) == b.report["unresolved_cells"] == int(b.asset.unresolved.sum())
    assert areas == sorted(areas, key=lambda a: -a["cells"])
    # the seam at the drop touches the unsampled rest of the hall, so they are one area with both reasons
    [area] = areas
    assert area["bbox"] == [96, 96, 415, 295], "minimap px, inclusive"
    assert set(area["why"]) == {hb.NO_SAMPLES, hb.NEIGHBOURS_DISAGREE}
    assert area["why"][hb.NO_SAMPLES] > area["why"][hb.NEIGHBOURS_DISAGREE] > 0
    assert b.report["unresolved_why"] == area["why"]
    seam = [c for c, why in b.reasons.items() if why == hb.NEIGHBOURS_DISAGREE]
    assert all(23 <= c % GRID <= 27 for c in seam), "only cells within FILL_R of both heights disagree"


def test_separate_unresolved_areas_are_listed_largest_first():
    # Rows 12-29 are walked at 0 m; rows 32-36 are never sampled; one cell is a stack of five floors.
    def players(m, n):
        return {**{k: sweep(k) for k in range(6)},
                **{6 + s: standing(164, 204, 3.0 * (s + 1), "B") for s in range(4)}}

    b = built(players, t_end=SWEEP_S)
    big, stack = b.report["unresolved_areas"]
    assert big["why"] == {hb.NO_SAMPLES: big["cells"]} and big["cells"] == 5 * 40 and big["bbox"][1] == 256
    assert stack == {"cells": 1, "bbox": [160, 200, 167, 207], "why": {hb.TOO_MANY: 1}}
    assert b.report["refused_cells"] == 1


SWEEP_S = 30.0     # a sweep's round: 44 m a leg at 5 m/s (a faster one reads as a dash)


def sweep(k: int, z_m: float = 0.0):
    """Player k walks three rows of the hall, end to end, at one height (rows 12 + 3k .. 14 + 3k)."""
    y = 100 + 24 * k
    pts = [(0.0, 100, y), (8.4, 412, y), (9.0, 412, y + 8), (17.4, 100, y + 8), (18.0, 100, y + 16), (26.4, 412, y + 16)]
    return "A" if k < 5 else "B", [(t, x, yy, 0, z_m) for t, x, yy in pts] + [(SWEEP_S, 412, y + 16, 0, z_m)]


def test_the_bar_refuses_a_thin_map_and_passes_a_covered_one():
    thin = built(lambda m, n: {0: level_walk(100, 192, 0.0)})
    assert not thin.ready and thin.report["ready"] is False
    assert thin.report["supported"] < hc.HEIGHT_SUPPORTED_MIN and "supported" in thin.report["not_ready"][0]
    covered = built(lambda m, n: {k: sweep(k) for k in range(9)}, t_end=SWEEP_S)
    assert covered.ready and covered.report["not_ready"] == []
    assert covered.report["supported"] >= 0.95 and covered.report["unresolved_cells"] == 0


def test_the_report_gives_visited_and_supported_separately():
    # Everyone sweeps the hall in one round of one match only: visited everywhere, supported nowhere.
    def players(m, n):
        return {k: sweep(k) for k in range(9)} if (m, n) == (0, 1) else {0: level_walk(100, 192, 0.0)}

    r = built(players, t_end=SWEEP_S).report
    assert r["visited"] >= 0.95 and r["supported"] < 0.1
    assert r["visited_cells"] > r["supported_cells"] > 0 and r["walkable_cells"] == int(GEO.walk.sum())
    assert (r["rounds"], r["matches"], r["rounds_without_z"]) == (6, 2, 0) and r["stands"] > 0


def test_a_large_unresolved_area_beside_two_floors_keeps_a_map_below_the_bar():
    n = GRID * GRID
    supported = np.ones(n, bool)
    count = np.ones(n, int)
    unresolved = np.zeros(n, bool)
    unresolved[[cell(c, r) for c in range(20, 25) for r in range(20, 23)]] = True      # 15 cells
    supported[unresolved] = False
    assert hb.readiness(supported, unresolved, count, n)[1] == [], "no two-floor cell near it: fine"
    count[cell(25, 21)] = 2
    share, why = hb.readiness(supported, unresolved, count, n)
    assert len(why) == 1 and "15 cells" in why[0] and share > 0.99
    unresolved[[cell(c, 22) for c in range(20, 25)]] = False                             # 10 cells left
    assert hb.readiness(supported, unresolved, count, n)[1] == [], "at or under UNRESOLVED_MAX"


# ---------------------------------------------------------------- the asset and the command

WEBAPP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WEBAPP / "scripts"))

import build_control_heights as command  # noqa: E402


def covered_rounds(extra=None):
    """A hall walked end to end at 0 m in six rounds of two matches (ready), plus `extra` players. Each
    round has one kill across the open hall (it qualifies for the kill-line check and isn't blocked)."""
    rounds = height_rounds(lambda m, n: {**{k: sweep(k) for k in range(9)}, **(extra or {})}, t_end=SWEEP_S)
    for _, _, blob in rounds:
        blob["kills"] = [{"i": 0, "t": 5.0, "killer": 0, "victim": 5}]
    return rounds


def test_the_asset_round_trips_and_its_digest_is_stable(tmp_path):
    b = hb.build(covered_rounds({9: level_walk(320, 328, 6.0, side="B")}), GEO)
    hc.save_asset(tmp_path / "Toy.height.npz", b.asset)
    back = hc.load_asset(tmp_path / "Toy.height.npz")
    for name in ("floors", "spread", "supported", "unresolved", "edges"):
        assert (getattr(back, name) == getattr(b.asset, name)).all(), name
    assert back.floors.dtype == np.int16 and back.floors.shape == (GRID, GRID, hc.MAX_FLOORS)
    assert back.meta["origin_z"] == z_dm(0.0) and back.meta["units"] == "dm" and back.meta["stand_m"] == hc.STAND_M
    assert back.meta["rounds"] == 6 and back.meta["matches"] == 2 and len(back.meta["walk_sha"]) == 12
    assert back.digest == b.asset.digest and len(back.digest) == 12
    again = hb.build(covered_rounds({9: level_walk(320, 328, 6.0, side="B")}), GEO)
    assert again.asset.digest == b.asset.digest, "the same rounds give the same asset"
    other = hb.build(covered_rounds(), GEO)
    assert other.asset.digest != b.asset.digest, "a different floor is a different digest"


def test_an_asset_of_another_version_is_refused(tmp_path):
    b = hb.build(covered_rounds(), GEO)
    b.asset.meta["version"] = hc.HEIGHT_VERSION + 1
    hc.save_asset(tmp_path / "Toy.height.npz", b.asset)
    with pytest.raises(hc.HeightError):
        hc.load_asset(tmp_path / "Toy.height.npz")


def toy_assets(tmp_path, name="Ascent"):
    """An asset folder holding the open hall's masks under a real map's name (the scale comes from
    maps.json, and the toys use Ascent's), with an index and no tags."""
    assets = tmp_path / "assets"
    assets.mkdir()
    cg.write_mask_png(assets / f"{name}.sight.png", GEO.sight)
    cg.write_mask_png(assets / f"{name}.walk.png", GEO.walk_px)
    (assets / "tags.json").write_text(json.dumps({"maps": {}}), encoding="utf-8")
    (assets / "index.json").write_text(json.dumps({"maps": {name: {"sight_sha": "s", "walk_sha": "w"}}}),
                                       encoding="utf-8")
    return assets


def write_blobs(directory, rounds, name="Ascent"):
    for match, n, blob in rounds:
        (directory / match).mkdir(parents=True, exist_ok=True)
        (directory / match / f"{n}.json.gz").write_bytes(fmt.encode_blob({**blob, "map": name}))
    return directory


@pytest.fixture
def no_picture(monkeypatch, tmp_path):
    monkeypatch.setattr(command, "picture_path", lambda name: tmp_path / f"{name}.height.png")
    # the toy map is called Ascent: the real Ascent's must-block lines aren't its own
    empty = tmp_path / "no-lines.json"
    empty.write_text('{"lines": []}', encoding="utf-8")
    monkeypatch.setattr(command, "MUST_BLOCK", empty)


def test_the_command_refuses_a_map_whose_must_block_lines_cant_be_checked_yet(tmp_path, capsys, no_picture, monkeypatch):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    lines = tmp_path / "must-block.json"
    lines.write_text(json.dumps({"lines": [{"map": "Ascent", "viewer": [X, Y, None], "target": [X + 40, Y, None],
                                            "source": "heights not known yet"}]}), encoding="utf-8")
    monkeypatch.setattr(command, "MUST_BLOCK", lines)
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 2
    captured = capsys.readouterr()
    assert "0/0 blocked, 1 not checked" in captured.out and "FAIL" in captured.out and "must_block" in captured.err
    assert not (assets / "Ascent.height.npz").exists()
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--accept-failures"], asset_dir=assets) == 0


def test_the_command_refuses_a_map_when_the_check_set_cant_be_read(tmp_path, capsys, no_picture, monkeypatch):
    monkeypatch.setattr(command, "MUST_BLOCK", tmp_path / "gone.json")
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 2
    captured = capsys.readouterr()
    assert "missing or unreadable" in captured.out and "must_block" in captured.err
    assert not (assets / "Ascent.height.npz").exists()


def test_the_command_writes_a_ready_maps_asset_and_index_entry(tmp_path, capsys, no_picture):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    write_blobs(blobs, [("other", 1, height_blob({0: standing(X, Y, 9.0)}, t_end=5.0))], name="Bind")
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 0
    out = capsys.readouterr().out
    assert "6 rounds of 2 matches" in out and "1 round(s) of other maps skipped" in out and "READY" in out \
        and "WROTE" in out
    asset = hc.load_asset(assets / "Ascent.height.npz")
    entry = json.loads((assets / "index.json").read_text(encoding="utf-8"))["maps"]["Ascent"]
    assert entry["height_sha"] == asset.digest and entry["sight_sha"] == "s", "the entry's other fields stay"
    assert entry["height"]["ready"] is True and entry["height"]["supported"] >= 0.95
    assert entry["height"]["walk_sha"] == asset.meta["walk_sha"]
    assert (tmp_path / "Ascent.height.png").stat().st_size > 0, "the review picture"


def test_the_command_refuses_a_map_below_the_bar_and_writes_nothing(tmp_path, capsys, no_picture):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", height_rounds(lambda m, n: {0: level_walk(100, 192, 0.0)}))
    before = (assets / "index.json").read_text(encoding="utf-8")
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 2
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and "below the bar" in captured.err and "NOT READY" in captured.out
    assert "WARNING Ascent: unresolved area of" in captured.out, "unresolved areas are printed loudly"
    assert not (assets / "Ascent.height.npz").exists()
    assert (assets / "index.json").read_text(encoding="utf-8") == before


def test_a_preview_builds_below_the_bar_and_writes_only_under_out(tmp_path, capsys, no_picture):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", height_rounds(lambda m, n: {0: level_walk(100, 192, 0.0)}))
    before = (assets / "index.json").read_text(encoding="utf-8")
    out_dir = tmp_path / "preview"
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview", "--out", str(out_dir)],
                        asset_dir=assets) == 0
    assert "PREVIEW written" in capsys.readouterr().out
    assert hc.load_asset(out_dir / "Ascent.height.npz").unresolved.any()
    assert json.loads((out_dir / "Ascent.height.json").read_text(encoding="utf-8"))["height"]["ready"] is False
    assert not (assets / "Ascent.height.npz").exists()
    assert (assets / "index.json").read_text(encoding="utf-8") == before


def test_a_preview_never_writes_inside_the_repository(tmp_path, capsys, no_picture):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    inside = WEBAPP / "app" / "static" / "data" / "control" / "preview-should-not-exist"
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview", "--out", str(inside)],
                        asset_dir=assets) == 2
    assert "inside the repository" in capsys.readouterr().err and not inside.exists()
    (tmp_path / "other-checkout" / ".git").mkdir(parents=True)      # the main checkout, or another worktree
    elsewhere = tmp_path / "other-checkout" / "webapp" / "app" / "static" / "data" / "control"
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview", "--out", str(elsewhere)],
                        asset_dir=assets) == 2
    assert "inside the repository" in capsys.readouterr().err and not elsewhere.exists()
    with pytest.raises(SystemExit):
        command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview"], asset_dir=assets)
    with pytest.raises(SystemExit):
        command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--out", str(tmp_path / "x")], asset_dir=assets)


def test_only_a_preview_may_make_floors_from_one_match(tmp_path, capsys, no_picture):
    # One match can't make a floor (FLOOR_MIN_MATCHES), so a map with a single match shows nothing; a
    # preview may relax that to look at it, and says so in what it writes.
    assets = toy_assets(tmp_path)
    one_match = height_rounds(lambda m, n: {k: sweep(k) for k in range(9)}, matches=1, rounds=6, t_end=SWEEP_S)
    blobs = write_blobs(tmp_path / "blobs", one_match)
    out_dir = tmp_path / "preview"
    base = ["--map", "Ascent", "--blobs-dir", str(blobs)]
    assert command.main([*base, "--preview", "--out", str(out_dir)], asset_dir=assets) == 0
    assert not hc.load_asset(out_dir / "Ascent.height.npz").supported.any(), "the real rule: no floors"
    assert command.main([*base, "--preview", "--out", str(out_dir), "--preview-min-matches", "1"],
                        asset_dir=assets) == 0
    assert "PREVIEW RULE" in capsys.readouterr().out
    relaxed = hc.load_asset(out_dir / "Ascent.height.npz")
    assert relaxed.supported.sum() > 900 and relaxed.meta["preview_min_matches"] == 1
    assert hc.FLOOR_MIN_MATCHES == 2, "the rule is put back"
    with pytest.raises(SystemExit):
        command.main([*base, "--preview-min-matches", "1"], asset_dir=assets)
    assert not (assets / "Ascent.height.npz").exists()


def test_the_report_prints_air_only_floors_and_the_counts():
    b = hb.build(covered_rounds({9: level_walk(320, 328, 6.0, side="B")}), GEO)
    lines = hb.report_lines("Toy", b.report)
    assert lines[0].startswith("Toy: ") and "6 rounds of 2 matches" in lines[0]
    assert any("reached only through the air: 2 cells" in line for line in lines)
    assert any("cells with 2 floors: 2" in line for line in lines) and lines[-1].strip() == "READY"


def test_no_real_maps_heights_are_committed():
    # A committed asset turns a map's heights on (spec: "built by a command and committed"). None is
    # committed by the build's own tests or by a preview; this fails loudly if one slips in unreviewed.
    index = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]
    with_heights = sorted(name for name, entry in index.items() if "height_sha" in entry)
    files = sorted(p.name for p in cg.ASSET_DIR.glob("*.height.npz"))
    assert [f"{name}.height.npz" for name in with_heights] == files, "every asset has its index entry and back"



# ---------------------------------------------------------------- the kill-line check and the must-block set

M_PX = 1 / 0.14


def ledge_geo():
    from tests.replays.control_toys import toy_heights

    return toy_heights("Ledge", [(96, 96, 416, 296)], ground=[((96, 96, 256, 296), 4.0)])


def kill_round(kills, players, n=1, t_end=10.0):
    blob = height_blob(players, t_end=t_end, n=n)
    blob["kills"] = [{"i": i, "t": t, "killer": k, "victim": v} for i, (t, k, v) in enumerate(kills)]
    return ("m", n, blob)


def test_kill_lines_count_qualifying_and_excluded_kills_and_block_only_when_body_and_head_are_hidden():
    geo = ledge_geo()
    x = 256 - 2 * M_PX
    players = {0: standing(x, 204, 4.0),                         # on the ledge, 2 m back from its edge
               5: standing(x + 3 * M_PX, 204, 0.0, "B"),          # just below it: body and head hidden
               6: standing(x + 10 * M_PX, 204, 0.0, "B"),         # far out: seen
               7: ("B", [(0.0, 300, 150, 0)]),                    # no z
               8: standing(x + 10 * M_PX, 120, 0.0, "B")}
    rounds = [kill_round([(2.0, 0, 5), (3.0, 0, 6), (4.0, 0, 7), (5.0, 0, 0)], players)]
    result = hb.kill_line_check(rounds, geo)
    assert (result["qualifying"], result["blocked"]) == (2, 1) and result["share"] == 0.5 and not result["passes"]
    assert result["excluded"] == {"no position with z near the kill": 1, "no killer": 1}
    assert result["examples"][0]["victim_px"] == [round(x + 3 * M_PX), 204]
    # the head is enough: somewhere out from the ledge the body is hidden but the head isn't, and a kill there
    # is not blocked
    from app.control import geometry as cg

    eye = 4.0 + hc.EYE_M
    hidden_body_seen_head = [d for d in np.arange(3.0, 9.0, 0.05)
                             if not cg.los(geo, (x, 204, eye), (x + d * M_PX, 204, hc.BODY_M))
                             and cg.los(geo, (x, 204, eye), (x + d * M_PX, 204, hc.EYE_M))]
    assert hidden_body_seen_head, "the band where only the head shows"
    near = standing(x + hidden_body_seen_head[len(hidden_body_seen_head) // 2] * M_PX, 204, 0.0, "B")
    assert hb.kill_line_check([kill_round([(2.0, 0, 5)], {0: players[0], 5: near})], geo)["blocked"] == 0


def test_a_kill_through_a_wall_or_on_unresolved_ground_does_not_qualify():
    from tests.replays.control_toys import toy_heights

    walled = toy_heights("WallLedge", [(96, 96, 416, 296)], walls=[(300, 96, 316, 296)],
                         ground=[((0, 0, 1024, 1024), 0.0)])
    result = hb.kill_line_check([kill_round([(2.0, 0, 5)], {0: standing(200, 204, 0.0), 5: standing(380, 204, 0.0, "B")})],
                                walled)
    assert result["qualifying"] == 0 and result["excluded"] == {"blocked in 2D (wall or smoke)": 1}
    rough = toy_heights("RoughLedge", [(96, 96, 416, 296)], ground=[((96, 96, 256, 296), 4.0)],
                        unresolved=[(368, 96, 416, 296)])
    result = hb.kill_line_check([kill_round([(2.0, 0, 5)], {0: standing(200, 204, 4.0), 5: standing(380, 204, 0.0, "B")})],
                                rough)
    assert result["excluded"] == {"on an unresolved cell": 1}


def test_a_kill_counts_with_a_sample_a_little_before_it():
    # KILL_SAMPLE_S is 0.25 s: positions two samples (0.125 s) before the kill still count, though the
    # engine's own lookup takes only a sample and a half.
    geo = ledge_geo()
    x = 256 - 2 * M_PX
    _, n, blob = kill_round([(2.0, 0, 6)], {0: standing(x, 204, 4.0), 6: standing(x + 10 * M_PX, 204, 0.0, "B")})
    for segs in blob["tracks"].values():
        [seg] = segs
        for key in ("u", "v", "yaw", "z"):
            seg[key] = seg[key][: int(1.875 * HZ) + 1]
    result = hb.kill_line_check([("m", n, blob)], geo)
    assert (result["qualifying"], result["excluded"]) == (1, {})


def test_the_checks_run_on_a_round_whose_alarmbot_has_no_height(tmp_path, capsys):
    # The build's checks use a geometry that just had heights attached: it has no visibility rows yet.
    import copy

    geo = copy.copy(ledge_geo())
    geo.rows = geo.row_of = None
    bot = toy_ability("Killjoy", "Q_StealthAlarmbot", 300, 204, 0, kind="GameObject")
    blob = height_blob({0: standing(120, 204, 4.0), 6: standing(380, 204, 0.0, "B")}, t_end=10.0, util=[bot])
    blob["kills"] = [{"i": 0, "t": 2.0, "killer": 6, "victim": 0}]
    assert hb.kill_line_check([("m", 1, blob)], geo)["qualifying"] == 1, "it runs: no rows are needed"


def test_a_must_block_line_that_is_not_blocked_fails_and_so_does_an_unknown_height():
    geo = ledge_geo()
    x = 256 - 2 * M_PX
    lines = [{"map": "Ledge", "viewer": [x, 204, z_dm(4.0)], "target": [x + 3 * M_PX, 204, z_dm(0.0)], "source": "hidden"},
             {"map": "Other", "viewer": [1, 1, 1], "target": [2, 2, 2], "source": "another map"}]
    result = hb.must_block_check(lines, geo, "Ledge")
    assert (result["lines"], result["checked"], result["blocked"], result["unchecked"]) == (1, 1, 1, 0) and result["passes"]
    unknown = [{"map": "Ledge", "viewer": [x, 204, None], "target": [x + 3 * M_PX, 204, z_dm(0.0)], "source": "no z"}]
    result = hb.must_block_check(lines + unknown, geo, "Ledge")
    assert (result["checked"], result["blocked"], result["unchecked"]) == (1, 1, 1) and not result["passes"], \
        "a required line that can't be checked yet is not a pass"
    assert not hb.must_block_check(unknown, geo, "Ledge")["passes"], "nor is a map with nothing checked"
    assert hb.must_block_check([], geo, "Ledge")["passes"], "a map with no required lines has nothing to fail"
    lines.append({"map": "Ledge", "viewer": [x, 204, z_dm(4.0)], "target": [x + 10 * M_PX, 204, z_dm(0.0)],
                  "source": "seen"})
    result = hb.must_block_check(lines, geo, "Ledge")
    assert not result["passes"] and [r["source"] for r in result["results"] if r["checked"] and not r["blocked"]] == ["seen"]


def test_the_committed_must_block_set_is_well_formed():
    data = json.loads(height_inputs.MUST_BLOCK.read_text(encoding="utf-8"))
    assert data["lines"] and all(line["map"] and len(line["viewer"]) == 3 and len(line["target"]) == 3
                                 and line["source"] for line in data["lines"])
    assert any(line["map"] == "Ascent" and "case 1" in line["source"] for line in data["lines"])


def test_the_command_refuses_a_map_that_fails_the_kill_lines_unless_accepted(tmp_path, capsys, no_picture, monkeypatch):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    failing = {"qualifying": 10, "blocked": 5, "share": 0.5, "passes": False, "excluded": {}, "examples": []}
    monkeypatch.setattr(hb, "kill_line_check", lambda rounds, geo: failing)
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 2
    captured = capsys.readouterr()
    assert "kill lines: 5/10 blocked" in captured.out and "FAIL" in captured.out and "kill_lines" in captured.err
    assert not (assets / "Ascent.height.npz").exists()
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--accept-failures"], asset_dir=assets) == 0
    entry = json.loads((assets / "index.json").read_text(encoding="utf-8"))["maps"]["Ascent"]
    assert entry["height"]["kill_lines"]["share"] == 0.5 and "must_block" in entry["height"]


# ---------------------------------------------------------------- slopes: bands, walks, gradient fill, slides
# docs/superpowers/specs/2026-10-05-height-slopes-design.md, parts 2 to 5. These rounds are stored at 125 Hz
# (control_toys.fast_blob), the rate the walk rules are tuned on.


def test_band_rules():
    r6 = np.array([0, 1, 2, 3, 4, 5])
    m6, r12, m12 = r6 // 3, np.r_[r6, r6], np.r_[r6 // 3, r6 // 3]
    no, yes = np.zeros(6, bool), np.ones(6, bool)
    # walks alone make the ground floor, at the low end (never the single lowest of several)
    assert hb.group_cell(np.array([20, 22, 24, 26, 28, 30.0]), r6, m6, no) == ([(22, 6)], None, hc.KIND_WALKS)
    two = np.r_[np.zeros(6), np.full(6, 40.0)]
    # an upper band of walks only (a boost passing over) is nobody's floor
    assert hb.group_cell(two, r12, m12, np.r_[yes, no]) == ([(0, 0)], None, hc.KIND_STANDS)
    # with stands it is a second floor
    assert hb.group_cell(two, r12, m12, np.r_[yes, yes]) == ([(0, 0), (40, 0)], None, hc.KIND_STANDS)
    # a tunnel only ever run through, under a bridge people stand on
    assert hb.group_cell(two, r12, m12, np.r_[no, yes]) == ([(0, 0), (40, 0)], None, hc.KIND_WALKS)
    close = np.r_[np.zeros(6), np.full(6, 12.0)]
    # two levels of stands 1.2 m apart: too close to tell apart
    assert hb.group_cell(close, r12, m12, np.r_[yes, yes]) == ([], hb.TOO_CLOSE, hc.KIND_NONE)
    # a box people only run over: one floor, and the lower wins
    assert hb.group_cell(close, r12, m12, np.r_[yes, no]) == ([(0, 12)], None, hc.KIND_STANDS)
    # one match is never a floor
    assert hb.group_cell(np.zeros(6), r6, np.zeros(6, int), no) == ([], None, hc.KIND_NONE)
    # one stray stand 1.9 m under a floor joins its band and moves nothing: not the height, not the spread
    z = np.array([0, 0, 1, 0, 0, 1, 50, 50, 51, 50, 50, 49, 31.0])
    rr = np.array([0, 1, 2, 3, 4, 5, 0, 1, 2, 3, 4, 5, 0])
    floors, why, _ = hb.group_cell(z, rr, rr // 3)
    assert why is None and floors == [(0, 1), (50, 0)], "the stray is nobody's: it isn't in the upper band at all"
    # a level of stands alone that spreads too wide is still refused (a steep ramp people stop on)
    assert hb.group_cell(np.r_[np.zeros(6), np.full(6, 9.0)], r12, m12) == ([], hb.SPREAD, hc.KIND_NONE)
    # stands scattered over 1.8 m with no level among them, and no walk: nothing is a floor, as before
    assert hb.group_cell(np.array([0, 4, 8, 12, 16, 19.0]), r6, m6) == ([], None, hc.KIND_NONE)
    # four bands of stands are refused, never cut down
    stack = np.repeat([0, 30, 60, 90.0], 6)
    assert hb.group_cell(stack, np.tile(r6, 4), np.tile(m6, 4))[1] == hb.TOO_MANY


def test_stairs_nobody_stops_on_get_floors_from_walks():
    # Two players a round run the stairs (rows 25 and 26); a player stands at each end of row 25.
    def players(m, n):
        return {0: stair_run(), 1: stair_run(y=Y + 8), 2: standing(132, Y, 0.0), 3: standing(252, Y, 4.0, "B")}

    b = hb.build(fast_rounds(players), GEO)
    kind = b.asset.kind
    for c in range(21, 27):
        assert kind[ROW, c] == hc.KIND_WALKS and b.asset.supported[ROW, c], c
        assert abs(int(b.asset.floors[ROW, c, 0]) / 10 - (c * 8 - 160) / 64 * 4.0) <= 0.11, c
    assert kind[ROW, 16] == hc.KIND_STANDS and kind[ROW, 31] == hc.KIND_STANDS
    assert b.report["cells_by_kind"]["walks"] >= 12 and b.report["walks"] > 0 and len(b.walks) == b.report["walks"]
    e = {tuple(row[:4]): row[4] for row in b.asset.edges.tolist()}
    for c in range(20, 27):       # half a metre a cell: steps, both ways, all the way up
        assert e[(cell(c), 0, cell(c + 1), 0)] == e[(cell(c + 1), 0, cell(c), 0)] == hc.EDGE_STEP


def test_a_player_after_a_dash_gives_no_floor_for_three_seconds():
    # Six rounds: a dash onto a spot 5 m up (a rooftop nobody can stand on), left again 2 s later.
    def players(m, n):
        dasher = ("A", [(0.0, 160, Y, 0, 0.0), (2.0, 160, Y, 0, 0.0), (2.3, 220, Y, 0, 5.0), (4.3, 220, Y, 0, 5.0),
                        (4.5, 240, Y, 0, 0.0), (10.0, 240, Y, 0, 0.0)])
        return {0: dasher, 1: standing(220, Y, 0.0), 2: standing(220, Y, 0.0, "B")}

    rounds = fast_rounds(players)
    floors, why = floors_at(rounds, cell=GEO.cell_of_px(220, Y))
    assert why is None and heights_m(floors) == [0.0], "the two seconds up there are inside the blackout"
    assert hb.all_ground(rounds, GEO)[3]["blackouts"] >= 6


def crosser(x0, x1, z0, z1, t=2.0, side="A"):
    """Stands at x0, runs to x1 in a second (z0 to z1), stands there."""
    return side, [(0.0, x0, Y, 0, z0), (t, x0, Y, 0, z0), (t + 1.0, x1, Y, 0, z1), (10.0, x1, Y, 0, z1)]


def gradient_rounds(rise, back=False):
    """Columns 23-24 stood on at 0 m and 26-27 at `rise`; column 25 crossed by one player, once in the whole
    build (too few passes for a floor of its own)."""
    def players(m, n):
        out = {0: standing(188, Y, 0.0), 1: standing(196, Y, 0.0), 2: standing(212, Y, rise), 3: standing(220, Y, rise)}
        if (m, n) == (0, 1):
            out[5] = crosser(220, 188, rise, 0.0, side="B") if back else crosser(188, 220, 0.0, rise, side="B")
        return out
    return fast_rounds(players)


def test_a_crossed_slope_is_filled_along_the_gradient_and_a_ledge_is_not():
    b = hb.build(gradient_rounds(1.2), GEO)
    assert b.asset.kind[ROW, 25] == hc.KIND_GRADIENT and int(b.asset.floors[ROW, 25, 0]) == 6
    assert not b.asset.supported[ROW, 25] and not b.asset.unresolved[ROW, 25]
    assert b.report["cells_by_kind"]["gradient"] >= 1
    # the same ground with nobody crossing it: a ledge for all the build knows
    ledge = hb.build(fast_rounds(lambda m, n: {0: standing(188, Y, 0.0), 1: standing(196, Y, 0.0),
                                               2: standing(212, Y, 1.2), 3: standing(220, Y, 1.2)}), GEO)
    assert ledge.asset.unresolved[ROW, 25] and ledge.reasons[cell(25)] == hb.NEIGHBOURS_DISAGREE
    assert ledge.asset.kind[ROW, 25] == hc.KIND_NONE


def test_a_gradient_cell_connects_the_way_it_was_crossed():
    up = hb.build(gradient_rounds(1.8), GEO)
    e = {tuple(row[:4]): row[4] for row in up.asset.edges.tolist()}
    assert int(up.asset.floors[ROW, 25, 0]) == 9, "0.9 m: more than a step from either side"
    for a, b in ((24, 25), (25, 26)):
        assert e[(cell(a), 0, cell(b), 0)] == e[(cell(b), 0, cell(a), 0)] == hc.EDGE_STEP, "climbed: both ways"
    down = hb.build(gradient_rounds(1.8, back=True), GEO)
    e = {tuple(row[:4]): row[4] for row in down.asset.edges.tolist()}
    for a, b in ((26, 25), (25, 24)):
        assert e[(cell(a), 0, cell(b), 0)] == hc.EDGE_SLIDE and (cell(b), 0, cell(a), 0) not in e, "only ever down"
    assert down.report["slides"] == 2 and down.report["falls"] == 0


def test_fill_gradient_rules():
    floors = {cell(24): [(z_dm(0.0), 0)], cell(26): [(z_dm(1.2), 0)]}
    why = {cell(25): hb.NEIGHBOURS_DISAGREE}
    up = hm.Walk(0, 0, 0.0, 1.0, (cell(23), cell(24), cell(25), cell(26)),
                 (z_dm(0.0), z_dm(0.0), z_dm(0.6), z_dm(1.2)))
    assert hb.fill_gradient(floors, why, hb.crossings([up]), GEO) == \
        ({cell(25): z_dm(0.6)}, {(cell(24), cell(25)), (cell(25), cell(26))})
    assert hb.fill_gradient(floors, why, {}, GEO) == ({}, set()), "nobody crossed"
    steep = {cell(24): [(z_dm(0.0), 0)], cell(26): [(z_dm(3.0), 0)]}
    assert hb.fill_gradient(steep, why, hb.crossings([up]), GEO) == ({}, set()), "3 m over 2.24 m"
    two = {cell(24): [(z_dm(0.0), 0), (z_dm(4.0), 0)], cell(26): [(z_dm(1.2), 0)]}
    assert hb.fill_gradient(two, why, hb.crossings([up]), GEO) == ({}, set()), "two floors never anchor a fill"
    cross = {**floors, cell(25, ROW - 1): [(z_dm(3.0), 0)], cell(25, ROW + 1): [(z_dm(4.0), 0)]}
    ns = hm.Walk(0, 1, 0.0, 1.0, (cell(25, ROW - 1), cell(25), cell(25, ROW + 1)),
                 (z_dm(3.0), z_dm(3.5), z_dm(4.0)))
    assert hb.fill_gradient(cross, why, hb.crossings([up, ns]), GEO) == ({}, set()), "the pairs disagree"
    assert hb.fill_gradient(floors, {cell(25): hb.NO_SAMPLES}, hb.crossings([up]), GEO) == ({}, set())
    edge = {0: [(z_dm(0.0), 0)], 2: [(z_dm(1.0), 0)]}        # cell 1 is on the map's top row: no pair runs off it
    over = hm.Walk(0, 0, 0.0, 1.0, (0, 1, 2), (z_dm(0.0), z_dm(0.5), z_dm(1.0)))
    assert hb.fill_gradient(edge, {1: hb.NEIGHBOURS_DISAGREE}, hb.crossings([over]), GEO)[0] == {1: z_dm(0.5)}


def test_a_drop_walked_down_is_a_slide_and_one_fallen_down_is_a_fall():
    # Columns 30-32: a ramp of 3 m over three cells (0.9 of rise per metre), only ever run down.
    # Column 40 at 3 m beside 41 at 0 m: only ever fallen from.
    def players(m, n):
        run_down = ("B", [(0.0, 240, Y, 0, 3.0), (2.0, 240, Y, 0, 3.0), (3.0, 264, Y, 0, 0.0), (10.0, 264, Y, 0, 0.0)])
        fall = ("B", [(0.0, 324, Y, 0, 3.0), (5.0, 324, Y, 0, 3.0), (5.25, 332, Y, 0, 0.0), (10.0, 332, Y, 0, 0.0)])
        return {0: standing(236, Y, 3.0), 1: standing(268, Y, 0.0), 5: run_down, 6: fall}

    b = hb.build(fast_rounds(players), GEO)
    assert [int(b.asset.floors[ROW, c, 0]) for c in (30, 31, 32)] == [20, 10, 0], "each cell's low end"
    e = {tuple(row[:4]): row[4] for row in b.asset.edges.tolist()}
    for a in (30, 31):
        assert e[(cell(a), 0, cell(a + 1), 0)] == hc.EDGE_SLIDE and (cell(a + 1), 0, cell(a), 0) not in e
    assert e[(cell(40), 0, cell(41), 0)] == hc.EDGE_FALL and (cell(41), 0, cell(40), 0) not in e
    assert (b.report["slides"], b.report["falls"], b.report["falls_closed"]) == (2, 1, 1)


def test_the_asset_keeps_kind_and_edge_kinds_and_only_the_edges_move_the_digest(tmp_path):
    b = hb.build(gradient_rounds(1.8, back=True), GEO)
    hc.save_asset(tmp_path / "Toy.height.npz", b.asset)
    back = hc.load_asset(tmp_path / "Toy.height.npz")
    assert back.kind.dtype == np.int8 and (back.kind == b.asset.kind).all() and back.edges.shape[1] == 5
    assert (back.edges == b.asset.edges).all() and back.digest == b.asset.digest
    assert set(np.unique(back.kind).tolist()) >= {hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_GRADIENT}
    other = hc.HeightAsset(back.floors, back.spread, back.supported, back.unresolved, back.edges.copy(), back.meta,
                           back.kind)
    slide = np.flatnonzero(other.edges[:, 4] == hc.EDGE_SLIDE)[0]
    other.edges[slide, 4] = hc.EDGE_FALL
    assert other.digest != back.digest, "the engine reads a connection's kind"
    relabelled = hc.HeightAsset(back.floors, back.spread, back.supported, back.unresolved, back.edges, back.meta,
                                np.where(back.kind == hc.KIND_GRADIENT, hc.KIND_FILLED, back.kind).astype(np.int8))
    assert relabelled.digest == back.digest, "it never reads where a height came from"
    plain = hc.HeightAsset(back.floors, back.spread, back.supported, back.unresolved, back.edges, back.meta)
    assert set(np.unique(plain.kind).tolist()) <= {hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_FILLED}, \
        "without a kind it is told from `supported`"


def test_a_stray_sample_between_two_floors_bridges_nothing():
    r6 = np.array([0, 1, 2, 3, 4, 5])
    two = np.r_[np.zeros(6), np.full(6, 30.0)]
    rr, mm, yes = np.r_[r6, r6, 0], np.r_[r6 // 3, r6 // 3, 0], np.ones(13, bool)
    assert hb.group_cell(two, rr[:12], mm[:12]) == ([(0, 0), (30, 0)], None, hc.KIND_STANDS)
    # one stand half way up (someone on a teammate's head), or one walk (a boost's way up): still two floors
    assert hb.group_cell(np.r_[two, 15.0], rr, mm) == ([(0, 0), (30, 0)], None, hc.KIND_STANDS)
    assert hb.group_cell(np.r_[two, 15.0], rr, mm, np.r_[yes[:12], False]) == ([(0, 0), (30, 0)], None, hc.KIND_STANDS)
    # four strays, still short of a level of their own
    strays = np.r_[two, [12.0, 14, 16, 18]]
    assert hb.group_cell(strays, np.r_[rr[:12], 0, 1, 2, 3], np.r_[mm[:12], 0, 0, 0, 1])[0] == [(0, 0), (30, 0)]
    # a supported level between them is evidence, not noise: the three can't be told apart
    level = np.r_[two, np.full(6, 15.0)]
    assert hb.group_cell(level, np.r_[r6, r6, r6], np.r_[r6 // 3, r6 // 3, r6 // 3])[1] == hb.TOO_CLOSE
    # so are stairs people walk from the ground to within 2 m of the upper floor (the spec's "what stays
    # unresolved, on purpose"): with stands on both levels the cell is refused, never guessed
    ramp = np.r_[two, np.full(6, 8.0), np.full(6, 14.0)]
    walked_up = np.r_[np.ones(12, bool), np.zeros(12, bool)]
    assert hb.group_cell(ramp, np.tile(r6, 4), np.tile(r6 // 3, 4), walked_up)[1] == hb.TOO_CLOSE
    only_run_over = np.r_[np.ones(6, bool), np.zeros(18, bool)]         # nobody stands on the upper level
    floors, why, _ = hb.group_cell(ramp, np.tile(r6, 4), np.tile(r6 // 3, 4), only_run_over)
    assert why is None and floors == [(0, 30)], "one band: its low end, spread over all of it"


LEDGE_MPS = 8.0        # fast enough that a 1 m fall clears a whole cell, slow enough not to be a dash


def ledge_run(height):
    """Six rounds: a player runs at 8 m/s off a ledge `height` up (it ends at x 200) and on along the low ground;
    one stands on top, one below."""
    air = (height / 10) ** 0.5
    px = LEDGE_MPS / GEO.m_per_px

    def z_of(t):
        return height if t < 1 else height - 10 * (t - 1) ** 2 if t < 1 + air else 0.0

    runner = ("B", [(i / 125, 200 - px + px * i / 125, Y, 0, z_of(i / 125)) for i in range(3 * 125 + 1)])
    return fast_rounds(lambda m, n: {0: standing(196, Y, height), 1: standing(228, Y, 0.0), 5: runner}, t_end=3.0)


@pytest.mark.parametrize("height", [0.9, 1.1])
def test_a_ledge_run_off_is_never_a_slope_or_a_slide(height):
    # Near SILENT_DROP_M the difference matters most: a fall read as a walk would fill the ledge in as a slope
    # and let the unknown down it.
    b = hb.build(ledge_run(height), GEO)
    landing = GEO.cell_of_px(200 + (height / 10) ** 0.5 * LEDGE_MPS / GEO.m_per_px, Y) % GRID
    # The level run on top is a stand, and a stand lasts until z has left it by STAND_TOL_M: a tenth of a second
    # past the edge, in cell 25. The cells after that one and before the landing are flown over.
    over = list(range(26, landing))
    assert over, "the fall crosses at least one whole cell"
    assert int(b.asset.floors[ROW, 24, 0]) == round(height * 10) and int(b.asset.floors[ROW, 28, 0]) == 0
    for c in over:
        assert b.asset.kind[ROW, c] == hc.KIND_NONE and b.asset.unresolved[ROW, c], (c, b.asset.kind[ROW, c])
        assert b.reasons[cell(c)] == hb.NEIGHBOURS_DISAGREE, "a ledge for all the build knows: never a height part way down"
    assert b.report["slides"] == 0 and b.report["cells_by_kind"]["gradient"] == 0
    e = {tuple(row[:4]): row[4] for row in b.asset.edges.tolist()}
    assert not any(kind == hc.EDGE_SLIDE for kind in e.values())


def test_a_fall_between_two_ramps_gives_no_floor_under_it_and_no_slide():
    air = (1.1 / 10) ** 0.5
    px = LEDGE_MPS / GEO.m_per_px

    def z_of(t):
        return 3.1 - 2 * t if t < 1 else 1.1 - 10 * (t - 1) ** 2 if t < 1 + air else 2 * (t - 1 - air)

    runner = ("B", [(i / 125, 160 + px * i / 125, Y, 0, z_of(i / 125)) for i in range(3 * 125 + 1)])
    b = hb.build(fast_rounds(lambda m, n: {5: runner}, t_end=3.0), GEO)
    takeoff, landing = GEO.cell_of_px(160 + px, Y) % GRID, GEO.cell_of_px(160 + px * (1 + air), Y) % GRID
    assert landing - takeoff >= 2
    for c in range(takeoff + 1, landing):
        assert b.asset.kind[ROW, c] == hc.KIND_NONE and b.asset.unresolved[ROW, c], c
    assert b.asset.kind[ROW, takeoff - 1] == hc.KIND_WALKS and b.asset.kind[ROW, landing + 1] == hc.KIND_WALKS
    assert b.report["slides"] == 0 and b.report["cells_by_kind"]["gradient"] == 0


def test_a_crossing_counts_only_on_the_floors_it_fills_between():
    floors = {cell(24): [(z_dm(0.0), 0)], cell(26): [(z_dm(1.2), 0)]}
    why = {cell(25): hb.NEIGHBOURS_DISAGREE}

    def crossed(za, zc, zb):
        run = hm.Walk(0, 0, 0.0, 1.0, (cell(24), cell(25), cell(26)), (z_dm(za), z_dm(zc), z_dm(zb)))
        return hb.fill_gradient(floors, why, hb.crossings([run]), GEO)[0]

    assert crossed(0.0, 0.6, 1.2) == {cell(25): z_dm(0.6)}, "up the slope itself"
    assert crossed(4.0, 4.0, 4.0) == {}, "along a bridge 4 m over the two anchors"
    assert crossed(2.0, 2.6, 3.2) == {}, "on something 2 m above them"
    assert crossed(-3.0, -3.0, -3.0) == {}, "through a tunnel under them"
    assert crossed(1.2, 0.6, 0.0) == {}, "going a to b downhill where the floors go uphill"
    assert crossed(0.0, 2.5, 1.2) == {}, "over a hump between them (a jump's arc)"
    level = hb.Stand(0, 0, 0.0, 1.0, z_dm(0.6), (cell(24), cell(25), cell(26)), 0.0, 0.0)
    assert hb.fill_gradient(floors, why, hb.crossings([level]), GEO)[0] == {cell(25): z_dm(0.6)}, \
        "a level run between two cells 1.2 m apart is within one cell's rise of both"


def test_a_run_past_a_platform_is_cut_there():
    # A wall stood on at cell 25: the cells within PLATFORM_R_M of it are dropped, and what is left of a run on
    # either side is two runs. One run across the gap would be a crossing nobody made on the ground.
    cells = tuple(cell(c) for c in range(12, 40))
    run = hm.Walk(0, 0, 0.0, 5.0, cells, tuple(range(len(cells))))
    plat = [(0.0, 9.0, 25 * 8 + 4.0, Y)]
    left, right = hb.off_platforms([run], plat, GEO)
    assert left.cells == cells[:len(left.cells)] and right.cells == cells[-len(right.cells):]
    assert left.low == tuple(range(len(left.cells))) and len(left.cells) + len(right.cells) < len(cells)
    assert left.cells[-1] % GRID < 25 < right.cells[0] % GRID
    seen = hb.crossings([left, right])
    assert all(abs(a % GRID - b % GRID) <= 2 * hc.CROSS_REACH for a, _, b in seen), "nothing spans the gap"
    stand = hb.Stand(0, 0, 0.0, 5.0, z_dm(0.0), cells, 0.0, 0.0)
    assert [type(s) for s in hb.off_platforms([stand], plat, GEO)] == [hb.Stand, hb.Stand]

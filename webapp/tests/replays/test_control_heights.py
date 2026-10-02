"""The height build on toy maps (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 3):
stands, floors, fill, connections, unresolved areas, readiness, the asset and the command."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import geometry as cg
from app.control import height_build as hb
from app.control import heights as hc
from app.replays import format as fmt
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
    """Stands at x0 (height z0), then is at x1 (height z1) 0.5 s later: a climb or a drop."""
    return side, [(0.0, x0 + 4, Y, 0, z0), (t_move, x0 + 4, Y, 0, z0), (t_move + 0.5, x1 + 4, Y, 0, z1),
                  (10.0, x1 + 4, Y, 0, z1)]


def edges_of(b):
    return {tuple(e) for e in b.asset.edges.tolist()}


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
    assert all(len(row) == 4 for row in b.asset.edges.tolist()) and b.report["edges"] == len(e)


def test_a_floor_reached_only_through_the_air_is_listed():
    # A platform 6 m up over columns 40-41 that nobody walks onto (a rope), in a hall whose ground is all
    # resolved (an unresolved cell beside it would join it to the ground, as the engine walks it).
    def players(m, n):
        return {**{k: sweep(k) for k in range(9)}, 9: level_walk(320, 328, 6.0, side="B")}

    b = built(players)
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

    b = built(players)
    big, stack = b.report["unresolved_areas"]
    assert big["why"] == {hb.NO_SAMPLES: big["cells"]} and big["cells"] == 5 * 40 and big["bbox"][1] == 256
    assert stack == {"cells": 1, "bbox": [160, 200, 167, 207], "why": {hb.TOO_MANY: 1}}
    assert b.report["refused_cells"] == 1


def sweep(k: int, z_m: float = 0.0):
    """Player k walks three rows of the hall, end to end, at one height (rows 12 + 3k .. 14 + 3k)."""
    y = 100 + 24 * k
    pts = [(0.0, 100, y), (2.8, 412, y), (3.0, 412, y + 8), (5.8, 100, y + 8), (6.0, 100, y + 16), (8.8, 412, y + 16)]
    return "A" if k < 5 else "B", [(t, x, yy, 0, z_m) for t, x, yy in pts] + [(10.0, 412, y + 16, 0, z_m)]


def test_the_bar_refuses_a_thin_map_and_passes_a_covered_one():
    thin = built(lambda m, n: {0: level_walk(100, 192, 0.0)})
    assert not thin.ready and thin.report["ready"] is False
    assert thin.report["supported"] < hc.HEIGHT_SUPPORTED_MIN and "supported" in thin.report["not_ready"][0]
    covered = built(lambda m, n: {k: sweep(k) for k in range(9)})
    assert covered.ready and covered.report["not_ready"] == []
    assert covered.report["supported"] >= 0.95 and covered.report["unresolved_cells"] == 0


def test_the_report_gives_visited_and_supported_separately():
    # Everyone sweeps the hall in one round of one match only: visited everywhere, supported nowhere.
    def players(m, n):
        return {k: sweep(k) for k in range(9)} if (m, n) == (0, 1) else {0: level_walk(100, 192, 0.0)}

    r = built(players).report
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
    """A hall walked end to end at 0 m in six rounds of two matches (ready), plus `extra` players."""
    return height_rounds(lambda m, n: {**{k: sweep(k) for k in range(9)}, **(extra or {})})


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


def test_the_command_writes_a_ready_maps_asset_and_index_entry(tmp_path, capsys, no_picture):
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    write_blobs(blobs, [("other", 1, height_blob({0: standing(X, Y, 9.0)}, t_end=5.0))], name="Bind")
    assert command.main(["--map", "Ascent", "--blobs-dir", str(blobs)], asset_dir=assets) == 0
    out = capsys.readouterr().out
    assert "6 rounds read" in out and "1 skipped (other_map)" in out and "READY" in out and "WROTE" in out
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
    with pytest.raises(SystemExit):
        command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--preview"], asset_dir=assets)
    with pytest.raises(SystemExit):
        command.main(["--map", "Ascent", "--blobs-dir", str(blobs), "--out", str(tmp_path / "x")], asset_dir=assets)


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

"""Box-top stands: a steady stand inside a cover piece (W5a), kept as a top when it is a clear step above the floor
round the piece (the owner's review of 2026-10-27-box-tops)."""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.control import box_tops as bt  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.control.geometry import CELL, GRID, PX, Geometry  # noqa: E402

HZ = 125
UV = 10000 / PX
GROUND = {1: 20, 2: 20}         # dm: both pieces stand on a floor at 2.0 m


def cover():
    c = np.zeros((PX, PX), np.int16)
    c[100:140, 100:140] = 1
    c[100:140, 140:180] = 2
    return c


def seg(t0, xs, ys, zs):
    """A blob segment from per-sample px positions and z (dm); the diffs are of the rounded cumulative values."""
    u = np.round(np.asarray(xs, float) * UV).astype(np.int64)
    v = np.round(np.asarray(ys, float) * UV).astype(np.int64)
    z = np.round(np.asarray(zs, float)).astype(np.int64)
    d = lambda a: np.diff(a, prepend=0).tolist()  # noqa: E731
    return {"t0": t0, "u": d(u), "v": d(v), "z": d(z)}


def blob(*segs, slot=3):
    return {"hz": HZ, "tracks": {str(slot): list(segs)}}


def n(s):
    return int(s * HZ)


def const(x, y, z, secs):
    k = n(secs)
    return [x] * k, [y] * k, [z] * k


def walk_in(z_ground, secs=1.0):
    """Ground-level walk from x 80 to x 120 (the edge of piece 1 is x 100)."""
    k = n(secs)
    return list(np.linspace(80, 120, k)), [120.0] * k, [z_ground] * k


def cat(*parts):
    return [sum((list(p[i]) for p in parts), []) for i in range(3)]


def tops(b, c=None, ground=GROUND):
    return bt.on_a_step(bt.top_stands(b, cover() if c is None else c, m_per_px=1.0), ground)


def test_jump_up_is_a_top_stand():
    xs, ys, zs = cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, 32, 25))),
                     const(121, 120, 30, 1.0))
    st = bt.on_a_step(bt.top_stands(blob(seg(5.0, xs, ys, zs)), cover(), round_index=4, m_per_px=1.0), GROUND)
    assert len(st) == 1
    s = st[0]
    assert (s.round_index, s.slot, s.seg, s.piece, s.z_dm) == (4, 3, 0, 1, 30)
    assert abs(s.px[0] - 121) < 0.5 and abs(s.px[1] - 120) < 0.5
    assert s.t1 - s.t0 >= 0.5


def test_hugger_is_a_box_stand_but_not_a_top():
    xs, ys, zs = cat(walk_in(20), const(120, 120, 20, 2.0))
    b = blob(seg(0.0, xs, ys, zs))
    assert [s.z_dm for s in bt.top_stands(b, cover(), m_per_px=1.0)] == [20]
    assert tops(b) == []


def test_teleport_landing_is_a_top_stand():
    first = seg(0.0, *const(50, 50, 20, 1.0))
    xs, ys, zs = const(120, 120, 40, 1.0)
    assert [(s.seg, s.z_dm, s.piece) for s in tops(blob(first, seg(20.0, xs, ys, zs)))] == [(1, 40, 1)]


def test_a_top_needs_no_climb_a_round_can_start_on_it():
    xs, ys, zs = const(120, 120, 40, 1.0)
    assert [(s.seg, s.z_dm, s.piece) for s in tops(blob(seg(0.0, xs, ys, zs)))] == [(0, 40, 1)]


def test_the_step_is_at_least_box_step_m():
    step = round(hc.BOX_STEP_M * 10)
    for z, kept in ((20 + step, True), (20 + step - 1, False)):
        xs, ys, zs = const(120, 120, z, 1.0)
        assert bool(tops(blob(seg(0.0, xs, ys, zs)))) is kept, z


def test_a_piece_with_no_floor_round_it_has_no_tops():
    xs, ys, zs = const(120, 120, 40, 1.0)
    assert tops(blob(seg(0.0, xs, ys, zs)), ground={2: 20}) == []


def test_short_stand_is_not():
    xs, ys, zs = cat(walk_in(20), ([121.0] * 10, [120.0] * 10, list(np.linspace(20, 32, 10))),
                     const(121, 120, 30, 0.3))
    assert tops(blob(seg(0.0, xs, ys, zs))) == []


def test_wobble_is_not_steady():
    k = n(1.5)
    zs_top = [30 + (3 if (i // 10) % 2 else 0) for i in range(k)]          # 0.3 m swings
    xs, ys, zs = cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, 30, 25))),
                     ([121.0] * k, [120.0] * k, zs_top))
    assert tops(blob(seg(0.0, xs, ys, zs))) == []


def _two_stands(z_a, z_b, xa=121.0, xb=122.0):
    a = cat(walk_in(20), ([xa] * 25, [120.0] * 25, list(np.linspace(20, z_a, 25))), const(xa, 120, z_a, 1.0))
    b = cat(walk_in(20), ([xb] * 25, [120.0] * 25, list(np.linspace(20, z_b, 25))), const(xb, 120, z_b, 1.0))
    return blob(seg(0.0, *a), seg(10.0, *b))


def test_close_levels_merge_into_one_row_at_the_median():
    st = tops(_two_stands(30, 31))
    assert len(st) == 2
    rows = bt.top_rows(st)
    assert rows.dtype == np.int32 and rows.shape == (1, 3)
    assert rows[0].tolist()[2] in (30, 31)


def test_far_levels_stay_two_rows():
    rows = bt.top_rows(tops(_two_stands(30, 45)))
    assert rows.shape == (2, 3)
    assert sorted(rows[:, 2].tolist()) == [30, 45]


def test_empty_rows():
    r = bt.top_rows([])
    assert r.shape == (0, 3) and r.dtype == np.int32


def test_stand_where_two_pieces_meet_keeps_its_pixel_and_piece():
    xs, ys, zs = cat(walk_in(20), ([141.0] * 25, [120.0] * 25, list(np.linspace(20, 32, 25))),
                     const(141, 120, 30, 1.0))
    st = tops(blob(seg(0.0, xs, ys, zs)))
    assert len(st) == 1 and st[0].piece == 2
    rows = bt.top_rows(st)
    assert rows.tolist() == [[141, 120, 30]]


def test_a_stand_near_a_piece_edge_stores_a_pixel_on_that_piece():
    # x 179.7 is on piece 2 (x 140-179) when truncated; rounding the median would give 180, off every piece
    xs, ys, zs = cat(walk_in(20), ([179.7] * 25, [120.0] * 25, list(np.linspace(20, 32, 25))),
                     const(179.7, 120, 30, 1.0))
    st = tops(blob(seg(0.0, xs, ys, zs)))
    assert len(st) == 1 and st[0].piece == 2
    rows = bt.top_rows(st)
    assert cover()[rows[0, 1], rows[0, 0]] == 2


def test_a_level_takes_a_members_pixel():
    # two stands on one fine cell straddling a piece edge: the level's pixel is one of theirs, on a piece
    c = cover()
    c[100:140, 141] = 0                    # a notch: x 141 is off piece 2
    a = cat(walk_in(20), ([140.2] * 25, [120.0] * 25, list(np.linspace(20, 30, 25))), const(140.2, 120, 30, 1.0))
    b = cat(walk_in(20), ([142.2] * 25, [120.0] * 25, list(np.linspace(20, 30, 25))), const(142.2, 120, 30, 1.0))
    st = tops(blob(seg(0.0, *a), seg(10.0, *b)), c)
    assert len(st) == 2
    rows = bt.top_rows(st)
    assert rows.shape == (1, 3) and c[rows[0, 1], rows[0, 0]] == 2


def test_max_floors_per_fine_cell():
    parts = []
    for i, z in enumerate((30, 45, 60, 75)):
        parts.append(cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, z, 25))), const(121, 120, z, 1.0)))
    segs = [seg(10.0 * i, *p) for i, p in enumerate(parts)]
    rows = bt.top_rows(tops(blob(*segs)))
    assert rows.shape[0] == 3


# ---------------------------------------------------------------- the floor round a piece


def floors_at(cells_z):
    """A coarse floor grid (GRID*GRID, 3) with -1 everywhere but the given {(px x, px y): dm} cells' first floor."""
    f = -np.ones((GRID * GRID, 3), np.int16)
    for (x, y), z in cells_z.items():
        f[y // CELL * GRID + x // CELL, 0] = z
    return f


def test_ground_is_the_floor_most_of_the_ring_stands_on():
    # piece 1 is x 100-139, y 100-139, at a ledge's edge: 2.0 m round three sides, a drop to 0.0 m on one cell
    g = bt.ground_of(cover(), floors_at({(92, 112): 20, (92, 128): 20, (120, 92): 20, (112, 144): 0}))
    assert g[1] == 20


def test_a_stand_at_the_upper_floor_of_a_ledge_is_no_top():
    """Ascent's piece 17: players stood at 2.0 m on its outline, beside a drop to 0.0 m on one side."""
    f = floors_at({(92, 112): 20, (92, 128): 20, (120, 92): 20, (112, 144): 0})
    xs, ys, zs = const(120, 120, 20, 1.0)
    assert tops(blob(seg(0.0, xs, ys, zs)), ground=bt.ground_of(cover(), f)) == []


def test_a_tie_takes_the_lower_floor():
    g = bt.ground_of(cover(), floors_at({(92, 120): 20, (120, 92): 40}))
    assert g[1] == 20


def test_ground_ignores_floors_on_cover_and_beyond_the_ring():
    far = 100 - hc.BOX_RING_PX - 2 * CELL
    g = bt.ground_of(cover(), floors_at({(120, 120): 5, (far, 120): 5, (92, 120): 20}))
    assert g[1] == 20                      # the floor on piece 1 itself and the one past the ring don't count
    # off the corner: the cell x, y 80-87 is inside the padded crop, but every pixel of it is over 16 px away
    assert bt.ground_of(cover(), floors_at({(85, 85): 5, (92, 120): 20}))[1] == 20


def test_ground_reads_a_cells_other_floors():
    f = floors_at({(92, 120): 40})
    f[120 // CELL * GRID + 92 // CELL, 1] = 20
    assert bt.ground_of(cover(), f)[1] == 20


def test_a_piece_with_no_floor_round_it_is_left_out():
    g = bt.ground_of(cover(), floors_at({(190, 120): 20}))     # right of piece 2 only
    assert 1 not in g and g[2] == 20


# W6: judging is in world dm; diagnostic records are in world metres.
def stand(z=40, inside=1, candidates=(1,), px=(120, 120), **kw):
    return bt.TopStand(0, 4, 0, 17.448, 26.856, inside, z, px,
                       candidates=candidates, match="toy-match", round=13, **kw)


def test_reach_collects_all_candidates_and_splits_when_leaving_reach():
    parts = cat(const(139, 120, 40, 1), const(90, 120, 40, 1), const(99, 120, 40, 1))
    found = bt.top_stands(blob(seg(0, *parts)), cover(), m_per_px=0.14)
    assert len(found) == 2
    assert (found[0].inside, found[0].candidates) == (1, (1, 2))
    assert (found[1].inside, found[1].candidates) == (0, (1,))


def test_reach_is_in_metres_and_does_not_limit_candidate_count():
    c = np.zeros((PX, PX), np.int16)
    for p, xy in enumerate(((120, 120), (119, 120), (121, 120), (120, 119)), 1):
        c[xy[1], xy[0]] = p
    b = blob(seg(0, *const(120, 120, 40, 1)))
    assert bt.top_stands(b, c, m_per_px=0.14)[0].candidates == (1, 2, 3, 4)
    assert bt.top_stands(b, c, m_per_px=1)[0].candidates == (1,)


def test_reach_records_each_candidates_edt_distance_in_candidate_order():
    c = cover()
    reach = bt.reach_distances(c, 0.14)
    found = bt.top_stands(blob(seg(0, *const(139, 99, 49, 1))), c, m_per_px=0.14, reach=reach)
    assert len(found) == 1
    s = found[0]
    assert s.candidates == (1, 2)
    assert len(s.cand_dist) == len(s.candidates)
    for piece, distance in zip(s.candidates, s.cand_dist):
        (ys, xs), edt = reach[piece]
        assert isinstance(distance, float) and 0 <= distance <= hc.TOP_REACH_M
        assert distance == edt[s.px[1] - ys.start, s.px[0] - xs.start]


def reach_geo():
    c = np.zeros((PX, PX), np.int16)
    c[100:140, 100:140] = 1
    return Geometry("Reach", np.zeros_like(c, bool), c == 0, np.ones((GRID, GRID), bool),
                    m_per_px=0.1302, uv_per_unit=1, tol_hits=0, cover_px=c,
                    cover_sha="toy-reach-cover", height_sha="toy-reach-height",
                    heights=SimpleNamespace(box_tops=np.array([[120, 120, 40]], np.int32)))


def test_view_reach_keeps_eight_pixels_but_not_nine_at_summit_scale():
    geo = reach_geo()
    view = bt.piece_reach(geo, 1.1, metric=True)
    assert tuple(view[120, 100 - 8]) == (1, 0, 0)
    assert tuple(view[120, 100 - 9]) == (0, 0, 0)
    assert geo.box_view_reach is view
    assert not view.flags.writeable


def test_default_reach_is_byte_identical_to_the_old_pixel_formula():
    from scipy import ndimage

    geo = reach_geo()
    # Independent copy of the old formula for this one-piece toy (R4).
    reach = math.ceil(0.75 / geo.m_per_px - 1e-9)
    d = ndimage.distance_transform_edt(geo.cover_px != 1).astype(np.float32)
    d[d > reach] = np.inf
    old = np.zeros((PX, PX, bt.REACH_SLOTS), np.int16)
    old[:, :, 0] = np.where(np.isfinite(d), 1, 0)
    current = bt._reach_of(geo, bt.piece_tops(geo))
    assert np.array_equal(current, old)
    assert current.tobytes() == old.tobytes()
    assert tuple(current[120, 100 - 6]) == (1, 0, 0)
    assert tuple(current[120, 100 - 7]) == (0, 0, 0)
    assert np.array_equal(bt.piece_reach(geo), old)


def test_reach_cache_separates_distance_and_metric(monkeypatch):
    monkeypatch.setattr(bt, "_reach_cache", {})
    geo = reach_geo()
    standing = bt.piece_reach(geo)
    view = bt.piece_reach(geo, 1.1, metric=True)
    rounded_view = bt.piece_reach(geo, 1.1)
    assert standing is not view and view is not rounded_view and standing is not rounded_view
    assert tuple(rounded_view[120, 100 - 9]) == (1, 0, 0)
    assert bt.piece_reach(geo) is standing
    assert bt.piece_reach(geo, 1.1, metric=True) is view
    assert bt.piece_reach(geo, 1.1) is rounded_view


@pytest.mark.parametrize("rows", [None, np.empty((0, 3), np.int32)])
def test_view_reach_is_none_without_tops(rows):
    geo = reach_geo()
    geo.height_sha = None
    geo.heights.box_tops = rows
    assert geo.box_view_reach is None


def test_one_segment_has_several_steady_stands_including_a_crouch():
    parts = cat(const(120, 120, 40, 1), const(120, 120, 37, 1), const(120, 120, 40, 1))
    assert [s.z_dm for s in bt.top_stands(blob(seg(0, *parts)), cover(), m_per_px=0.14)] == [40, 37, 40]


def test_blackout_cuts_a_stand_without_joining_its_two_sides():
    b = blob(seg(0, *const(120, 120, 40, 3)))
    found = bt.top_stands(b, cover(), m_per_px=0.14, skip={3: [(1., 2.)]})
    assert len(found) == 2 and found[0].t1 < 1 and found[1].t0 > 2
    assert all(s.seg == 0 and s.t1 - s.t0 >= hc.BOX_SETTLE_S for s in found)


def test_ring_ground_uses_child_floors_when_the_parent_has_been_refined():
    f = floors_at({})
    child = 120 // 4 * (PX // 4) + 92 // 4
    ground = bt.ground_of(cover(), f, fine_cells=np.array([child], np.int32),
                          fine_floors=np.array([[20, -1, -1]], np.int16))
    assert ground == {1: 20}


def test_only_inside_single_candidate_stands_teach_and_others_assign():
    teacher = stand()
    outside = stand(inside=0, px=(99, 120))
    rows, assigned, conflicts, counts = bt.judge([outside, teacher], GROUND, lambda px: ())
    assert rows.tolist() == [[120, 120, 40]]
    assert assigned == [(outside, 1, 40)] and conflicts == []
    assert counts == dict(teach=1, assign=1, conflict=0, ground=0, platform=0, blackout=0)
    rows, assigned, conflicts, counts = bt.judge([outside], GROUND, lambda px: ())
    assert rows.shape == (0, 3) and not assigned and counts['conflict'] == 1
    assert conflicts[0]['candidates'] == [[1, []]]


def test_real_shaped_ambiguous_stand_assigns_without_teaching():
    row = {"slot": 4, "t0": 17.448, "t1": 26.856, "z": 5.5, "px": [503, 661], "inside": 26,
           "d": {"26": 0.0, "27": 0.469}, "match": "toy-match", "round": 13,
           "cands": [26, 27], "fate": "assigned", "to": [26, 5.5]}
    s = bt.TopStand(0, row['slot'], 0, row['t0'], row['t1'], row['inside'], round(row['z'] * 10),
                    tuple(row['px']), candidates=tuple(row['cands']),
                    cand_dist=tuple(row['d'][str(p)] for p in row['cands']),
                    match=row['match'], round=row['round'])
    teacher = stand(55, inside=26, candidates=(26,), px=(500, 660))
    rows, assigned, conflicts, counts = bt.judge([s, teacher], {26: 20, 27: 20}, lambda px: ())
    assert rows.tolist() == [[500, 660, 55]]
    assert assigned == [(s, 26, 55)] and not conflicts and counts['teach'] == 1


def test_real_shaped_ground_row_keeps_candidate_distances_without_changing_its_fate():
    row = {"mode": "rule", "slot": 5, "t0": 1.648, "t1": 5.528, "z": 2.0, "px": [774, 414],
           "inside": 0, "d": {"15": 0.582}, "leaf": 6624, "floors": [[6624, 2.0, True]],
           "plat": False, "match": "toy-match", "round": 1, "cand0": [15], "cands": [],
           "fate": "ground_nocand"}
    s = bt.TopStand(0, row['slot'], 0, row['t0'], row['t1'], row['inside'], round(row['z'] * 10),
                    tuple(row['px']), candidates=tuple(row['cand0']),
                    cand_dist=tuple(row['d'][str(p)] for p in row['cand0']),
                    match=row['match'], round=row['round'])
    assert s.cand_dist == (0.582,)
    rows, assigned, conflicts, counts = bt.judge([s], {15: 20}, lambda px: (20,))
    assert rows.shape == (0, 3) and not assigned and not conflicts and counts['ground'] == 1


def test_conflict_distances_follow_piece_ids_after_sorting_and_filtering():
    s = stand(49, candidates=(2, 3, 1), cand_dist=(0.582, 0.333, 0.004))
    rows, assigned, conflicts, counts = bt.judge([stand(), s], {1: 20, 2: 20, 3: 49}, lambda px: ())
    assert rows.tolist() == [[120, 120, 40]] and not assigned and counts['conflict'] == 1
    assert conflicts[0]['candidates'] == [[1, [4.0], 0.0], [2, [], 0.58]]


def test_candidate_step_filter_is_per_piece_and_connected_ground_is_outside_only():
    ambiguous = stand(candidates=(1, 2))
    outside = stand(inside=0)
    rows, assigned, conflicts, counts = bt.judge([ambiguous, outside], {1: 20, 2: 40}, lambda px: (40,))
    assert rows.tolist() == [[120, 120, 40]] and not assigned and not conflicts
    assert counts['teach'] == counts['ground'] == 1
    assert bt.judge([stand(20)], GROUND, lambda px: ())[3]['ground'] == 1
    assert bt.judge([outside], GROUND, lambda px: (44,))[3]['conflict'] == 1


@pytest.mark.parametrize('lower', [35, 37, 38])
def test_crouch_joins_a_better_supported_level_across_the_same_piece(lower):
    rows, _, _, counts = bt.judge([stand(lower, px=(108, 120)), stand(), stand()], GROUND, lambda px: ())
    assert set(rows[:, 2]) == {40} and counts['teach'] == 3


def test_crouch_does_not_fold_equal_support_or_a_larger_drop():
    # Separate cells: the existing row merge can still combine equal levels within FLOOR_TOL_M in one cell.
    assert set(bt.judge([stand(35, px=(108, 120)), stand()], GROUND, lambda px: ())[0][:, 2]) == {35, 40}
    assert set(bt.judge([stand(34), stand(), stand()], GROUND, lambda px: ())[0][:, 2]) == {34, 40}


def test_assignment_uses_the_crouch_window_then_distance_then_lower_piece_id():
    teachers = [stand(40, inside=1, candidates=(1,)), stand(42, inside=2, candidates=(2,), px=(150, 120))]
    for z, expected in ((35, (1, 40)), (39, (1, 40)), (41, (1, 40)), (45, (2, 42))):
        s = stand(z, candidates=(2, 1))
        assert bt.judge([*teachers, s], GROUND, lambda px: ())[1] == [(s, *expected)]


def test_decimal_tie_uses_the_lower_top_within_one_piece():
    teachers = [stand(41, px=(108, 120)), stand(43)]
    s = stand(42, inside=0, px=(99, 120))
    assert bt.judge([*teachers, s], GROUND, lambda px: ())[1] == [(s, 1, 41)]
    assert bt.match_top(4.2, (4.3, 4.1)) == 4.1


def test_conflict_fields_and_world_z_do_not_change_the_teacher():
    s = stand(49, candidates=(2, 1))
    rows, assigned, conflicts, counts = bt.judge([stand(), s], GROUND, lambda px: ())
    assert rows.tolist() == [[120, 120, 40]] and not assigned and counts['conflict'] == 1
    assert conflicts == [{"match": "toy-match", "round": 13, "slot": 4, "t0": 17.448, "t1": 26.856,
                          "z": 4.9, "candidates": [[1, [4.0]], [2, []]], "px": [120, 120]}]

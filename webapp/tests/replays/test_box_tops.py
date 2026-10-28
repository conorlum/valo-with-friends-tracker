"""Box-top stands: a steady stand inside a cover piece (W5a), kept as a top when it is a clear step above the floor
round the piece (the owner's review of 2026-10-27-box-tops)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.control import box_tops as bt  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.control.geometry import CELL, GRID, PX  # noqa: E402

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
    return bt.on_a_step(bt.top_stands(b, cover() if c is None else c), ground)


def test_jump_up_is_a_top_stand():
    xs, ys, zs = cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, 32, 25))),
                     const(121, 120, 30, 1.0))
    st = bt.on_a_step(bt.top_stands(blob(seg(5.0, xs, ys, zs)), cover(), round_index=4), GROUND)
    assert len(st) == 1
    s = st[0]
    assert (s.round_index, s.slot, s.seg, s.piece, s.z_dm) == (4, 3, 0, 1, 30)
    assert abs(s.px[0] - 121) < 0.5 and abs(s.px[1] - 120) < 0.5
    assert s.t1 - s.t0 >= 0.5


def test_hugger_is_a_box_stand_but_not_a_top():
    xs, ys, zs = cat(walk_in(20), const(120, 120, 20, 2.0))
    b = blob(seg(0.0, xs, ys, zs))
    assert [s.z_dm for s in bt.top_stands(b, cover())] == [20]
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


def test_ground_is_the_lowest_floor_round_the_piece():
    # piece 1 is x 100-139, y 100-139: a floor at 2.0 m left of it, a higher ledge at 4.0 m above it
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

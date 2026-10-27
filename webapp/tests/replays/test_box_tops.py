"""Box-top stands: a steady stand inside a cover piece that the player got up to (W5a)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.control import box_tops as bt  # noqa: E402
from app.control.geometry import PX  # noqa: E402

HZ = 125
UV = 10000 / PX


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


def test_jump_up_is_a_top_stand():
    xs, ys, zs = cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, 32, 25))),
                     const(121, 120, 30, 1.0))
    st = bt.top_stands(blob(seg(5.0, xs, ys, zs)), cover(), round_index=4)
    assert len(st) == 1
    s = st[0]
    assert (s.round_index, s.slot, s.seg, s.piece, s.z_dm) == (4, 3, 0, 1, 30)
    assert abs(s.px[0] - 121) < 0.5 and abs(s.px[1] - 120) < 0.5
    assert s.t1 - s.t0 >= 0.5


def test_hugger_is_not_a_top_stand():
    xs, ys, zs = cat(walk_in(20), const(120, 120, 20, 2.0))
    assert bt.top_stands(blob(seg(0.0, xs, ys, zs)), cover()) == []


def test_teleport_landing_is_a_top_stand():
    first = seg(0.0, *const(50, 50, 20, 1.0))
    xs, ys, zs = const(120, 120, 40, 1.0)
    st = bt.top_stands(blob(first, seg(20.0, xs, ys, zs)), cover())
    assert [(s.seg, s.z_dm, s.piece) for s in st] == [(1, 40, 1)]


def test_round_first_segment_inside_a_piece_is_not():
    xs, ys, zs = const(120, 120, 40, 1.0)
    assert bt.top_stands(blob(seg(0.0, xs, ys, zs)), cover()) == []


def test_short_stand_is_not():
    xs, ys, zs = cat(walk_in(20), ([121.0] * 10, [120.0] * 10, list(np.linspace(20, 32, 10))),
                     const(121, 120, 30, 0.3))
    assert bt.top_stands(blob(seg(0.0, xs, ys, zs)), cover()) == []


def test_wobble_is_not_steady():
    k = n(1.5)
    zs_top = [30 + (3 if (i // 10) % 2 else 0) for i in range(k)]          # 0.3 m swings
    xs, ys, zs = cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, 30, 25))),
                     ([121.0] * k, [120.0] * k, zs_top))
    assert bt.top_stands(blob(seg(0.0, xs, ys, zs)), cover()) == []


def _two_stands(z_a, z_b, xa=121.0, xb=122.0):
    a = cat(walk_in(20), ([xa] * 25, [120.0] * 25, list(np.linspace(20, z_a, 25))), const(xa, 120, z_a, 1.0))
    b = cat(walk_in(20), ([xb] * 25, [120.0] * 25, list(np.linspace(20, z_b, 25))), const(xb, 120, z_b, 1.0))
    return blob(seg(0.0, *a), seg(10.0, *b))


def test_close_levels_merge_into_one_row_at_the_median():
    st = bt.top_stands(_two_stands(30, 31), cover())
    assert len(st) == 2
    rows = bt.top_rows(st)
    assert rows.dtype == np.int32 and rows.shape == (1, 3)
    assert rows[0].tolist()[2] in (30, 31)


def test_far_levels_stay_two_rows():
    rows = bt.top_rows(bt.top_stands(_two_stands(30, 45), cover()))
    assert rows.shape == (2, 3)
    assert sorted(rows[:, 2].tolist()) == [30, 45]


def test_empty_rows():
    r = bt.top_rows([])
    assert r.shape == (0, 3) and r.dtype == np.int32


def test_stand_where_two_pieces_meet_keeps_its_pixel_and_piece():
    xs, ys, zs = cat(walk_in(20), ([141.0] * 25, [120.0] * 25, list(np.linspace(20, 32, 25))),
                     const(141, 120, 30, 1.0))
    st = bt.top_stands(blob(seg(0.0, xs, ys, zs)), cover())
    assert len(st) == 1 and st[0].piece == 2
    rows = bt.top_rows(st)
    assert rows.tolist() == [[141, 120, 30]]


def test_max_floors_per_fine_cell():
    parts = []
    for i, z in enumerate((30, 45, 60, 75)):
        parts.append(cat(walk_in(20), ([121.0] * 25, [120.0] * 25, list(np.linspace(20, z, 25))), const(121, 120, z, 1.0)))
    segs = [seg(10.0 * i, *p) for i, p in enumerate(parts)]
    rows = bt.top_rows(bt.top_stands(blob(*segs), cover()))
    assert rows.shape[0] == 3

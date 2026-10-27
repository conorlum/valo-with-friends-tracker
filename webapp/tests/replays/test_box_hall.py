"""The box_hall toy (a cover box mid-hall) and the z helpers for the standing-on-a-box tests."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import (BOX_PX, BOX_RING, BOX_TOY_RISE_M, BOX_WITNESS, box_hall, box_hall_no_tops,  # noqa: E402
                          box_hall_tops, box_top_rows, height_blob, hugging_track, three_boxes_tops,
                          jump_track, on_box_track, open_hall)

OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
ANGLES = np.arange(0, 360, 0.25)


@pytest.mark.parametrize("side", ["N", "S", "E", "W"])
def test_the_box_hides_the_witness_that_the_open_hall_shows(side):
    from app.control import geometry as cg

    geo, open_geo = box_hall(), open_hall()
    x, y = (float(v) for v in geo.centres[BOX_RING[OPPOSITE[side]]])
    witness = BOX_WITNESS[side]
    assert not cg.cast(geo, x, y, ANGLES, [], eye_z=None)[witness]
    assert cg.cast(open_geo, x, y, ANGLES, [], eye_z=None)[witness]


def test_ring_and_witness_cells_are_walkable_and_the_box_is_one_piece():
    geo = box_hall()
    walk = geo.walk.ravel()
    for cell in (*BOX_RING.values(), *BOX_WITNESS.values()):
        assert walk[cell]
    x0, y0, x1, y1 = BOX_PX
    labels = np.unique(geo.cover_px)
    assert list(labels) == [0, 1]
    assert (geo.cover_px[y0:y1, x0:x1] == 1).all()
    assert int((geo.cover_px == 1).sum()) == (x1 - x0) * (y1 - y0)
    assert not geo.walk_px[y0:y1, x0:x1].any()
    assert geo.sight[y0:y1, x0:x1].all()


@pytest.mark.slow
def test_the_z_helpers_make_blobs_the_engine_accepts():
    from app.control import engine as ce

    geo = box_hall_tops()
    players = {0: on_box_track(260, 196), 1: hugging_track(260, 176), 5: jump_track(230, 290, 196, 1.5, 2.0, "B")}
    assert on_box_track(260, 196)[1][0][4] == BOX_TOY_RISE_M
    out = ce.compute_round(height_blob(players, t_end=10.0), geo, None)
    assert out is not None


# ------------------------------------------------------------------ the tops toys (learnt box tops)

def test_box_top_rows_are_int32_k_by_3_rows_inside_the_box_in_decimetres():
    rows = np.array(box_top_rows(BOX_PX), np.int32)
    assert rows.shape[1] == 3 and len(rows) == 36           # a 24 x 24 px box: 6 x 6 fine 4 px cells
    x0, y0, x1, y1 = BOX_PX
    assert ((rows[:, 0] >= x0) & (rows[:, 0] < x1) & (rows[:, 1] >= y0) & (rows[:, 1] < y1)).all()
    assert (rows[:, 2] == round(BOX_TOY_RISE_M * 10)).all()
    assert box_top_rows((349, 308, 353, 312), 4.0) == [(351, 310, 40)]       # a Summit row's shape: [351, 310, 40]


def test_box_hall_tops_has_the_box_top_and_reach_round_the_box():
    geo = box_hall_tops()
    assert dict(geo.box_tops) == {1: (BOX_TOY_RISE_M,)}
    assert geo.heights.box_tops.dtype == np.int32 and geo.heights.box_tops.shape[1] == 3
    reach = geo.box_reach
    x0, y0, x1, y1 = BOX_PX
    assert reach is not None and reach.shape[-1] == 3
    for x, y in ((x0 - 10, 196), (x1 + 10, 196), (260, y0 - 10), (260, y1 + 10), (260, 196)):
        assert reach[y, x, 0] == 1, (x, y)
    assert reach[196, x0 - 100, 0] == 0
    assert (geo.cover_px == box_hall().cover_px).all(), "the same cover as the tops-free toy"


def test_box_hall_no_tops_has_a_heights_asset_and_no_tops():
    geo = box_hall_no_tops()
    assert geo.heights is not None and dict(geo.box_tops) == {} and geo.box_reach is None


def test_three_boxes_tops_gives_each_piece_its_top():
    geo = three_boxes_tops()
    assert dict(geo.box_tops) == {1: (1.0,), 2: (1.0,), 3: (1.0,)}

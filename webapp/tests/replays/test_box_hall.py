"""The box_hall toy (a cover box mid-hall) and the z helpers for the standing-on-a-box tests."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import (BOX_PX, BOX_RING, BOX_TOY_RISE_M, BOX_WITNESS, box_hall, height_blob, hugging_track,  # noqa: E402
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

    geo = box_hall()
    players = {0: on_box_track(260, 196), 1: hugging_track(260, 176), 5: jump_track(230, 290, 196, 1.5, 2.0, "B")}
    assert on_box_track(260, 196)[1][0][4] == BOX_TOY_RISE_M
    out = ce.compute_round(height_blob(players, t_end=10.0), geo, None)
    assert out is not None

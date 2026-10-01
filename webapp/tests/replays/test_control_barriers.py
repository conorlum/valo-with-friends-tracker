"""Placing the buy-phase barriers from the round starts (scripts/place_control_barriers.py) on a toy hall:
a short hand stroke becomes a wall-to-wall line just past the most forward start positions."""

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import place_control_barriers as pcb  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from tests.replays.control_toys import open_hall  # noqa: E402

STEP = cg.PX // cg.PAINT_GRID


def hand_line(x_px, y0_px, y1_px):
    cells = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    cells[y0_px // STEP:y1_px // STEP, x_px // STEP] = True
    return cells


def starts(front_x):
    """Attackers east of the barrier, the most forward pressed against it at `front_x`; defenders far west."""
    att = [("attack", front_x + dx, y) for dx, y in ((0, 150), (4, 200), (10, 250), (30, 180), (60, 220))]
    return att + [("defense", 120, y) for y in (130, 200, 270)]


def test_a_short_stroke_becomes_a_wall_to_wall_line_past_the_front():
    geo = open_hall()     # the hall: x 96-416, y 96-296 px
    rows = starts(272)
    new, notes = pcb.place(geo, hand_line(256, 140, 260), rows)
    cols = np.unique(np.nonzero(new)[1]) * STEP
    assert len(cols) == 1, "one straight line, perpendicular to the hall's walls"
    margin_px = pcb.MARGIN_M / geo.m_per_px
    assert 272 - margin_px - STEP <= cols[0] <= 272 - margin_px + STEP, notes
    ys = np.nonzero(new)[0] * STEP
    assert ys.min() <= 100 and ys.max() >= 288, "it reaches both walls"
    r = pcb.check(geo, new, rows)
    assert not r["leak"] and r["off"] == 0


def test_too_few_positions_keep_the_stroke_and_a_gap_shows_as_a_change_of_owner():
    geo = open_hall()
    hand = hand_line(256, 140, 260)        # stops short of both walls: the sides join through the gaps
    new, notes = pcb.place(geo, hand, [("attack", 300, 200), ("defense", 120, 200)])
    assert "too few start positions" in notes[0]
    assert np.unique(np.nonzero(new)[1]).tolist() == [256 // STEP]
    rows = starts(272)
    assert pcb.check(geo, hand, rows)["leak"], "the hand stroke alone leaks"
    placed, _ = pcb.place(geo, hand, rows)
    changes = pcb.changed_areas(geo, pcb.check(geo, hand, rows), pcb.check(geo, placed, rows))
    assert changes, "the placed line closes what the hand stroke left open"


def test_paint_packs_and_unpacks():
    cells = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    cells[10, 20] = cells[200, 3] = True
    assert (cg.unpack_paint(cg.pack_paint(cells))[::STEP, ::STEP] == cells).all()

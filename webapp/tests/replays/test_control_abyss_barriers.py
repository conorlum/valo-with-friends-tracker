"""Abyss's committed barrier paint (BUG-01, D2): at the barrier drop each side's start ground stays behind its
barriers. A 33 px gap between two paint strokes once let the attackers' leak into the central and lower
corridors (2939 cells instead of 1358).

D2 (changed 2026-10-09: "the blue and red lines are the barriers use those"): the paint is valoplant's ten
barriers, registered onto our map by the walkable area, each redrawn as one straight line a paint cell wide,
plus one seal (paint px x 704-759, y 800-807) along the wall between the attackers' two lower-right barriers,
which our 8 px walk grid lost."""

from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import pytest

from app.control import engine as ce
from app.control import geometry as cg
from app.control.geometry import GRID

# Round 1 of replay 7a278f4b at t = 0 (paint px): group A defends, group B attacks.
DEFENSE = {0: (583, 124), 2: (490, 148), 3: (486, 257), 4: (463, 36), 7: (581, 102)}
ATTACK = {1: (844, 536), 5: (819, 419), 6: (801, 300), 8: (809, 316), 9: (764, 430)}
CENTRAL, LOWER = (500, 520), (600, 900)     # the two corridors the leak reached
DEFENSE_CELLS = 3249                        # measured on valoplant's barriers (3290 on the base paint)
ATTACK_CELLS = 1356                         # 1358 on W8's joined strokes, 2939 with the gap


def _holder(geo, slot, team, x, y):
    z = np.zeros(GRID * GRID, bool)
    return ce.Holder(slot, team, geo.cell_of_px(x, y), float(x), float(y), z.copy(), z.copy(), z.copy(),
                     z.copy(), z.copy(), False, "hold")


@pytest.fixture(scope="module")
def drop():
    geo = cg.load_geometry("Abyss")
    assert geo.barrier is not None, "Abyss has barrier paint"
    holders = [_holder(geo, s, "A", x, y) for s, (x, y) in DEFENSE.items()]
    holders += [_holder(geo, s, "B", x, y) for s, (x, y) in ATTACK.items()]
    tick = SimpleNamespace(t=0.0, holders={h.slot: h for h in holders},
                           rnd=SimpleNamespace(missing=defaultdict(int)))
    return geo, ce.barrier_start(geo, tick), tick


def test_abyss_attack_start_ground_stays_behind_the_barriers(drop):
    geo, areas, tick = drop
    assert "B" in areas, dict(tick.rnd.missing)
    ground = geo.to_cells(areas["B"][0])
    assert set(areas["B"][1]) == set(ATTACK), "every attacker starts in it"
    for x, y in (CENTRAL, LOWER):
        cell = geo.cell_of_px(x, y)
        assert geo.walk.ravel()[cell], f"probe ({x},{y}) is walkable floor"
        assert not ground[cell], f"the attackers' start ground reaches ({x},{y}): {int(ground.sum())} cells"


def test_abyss_start_grounds_are_the_measured_sizes(drop):
    geo, areas, tick = drop
    assert "A" in areas and "B" in areas, dict(tick.rnd.missing)
    assert int(geo.to_cells(areas["A"][0]).sum()) == DEFENSE_CELLS
    assert int(geo.to_cells(areas["B"][0]).sum()) == ATTACK_CELLS


def test_every_abyss_barrier_is_a_straight_line():
    """The owner: "all the barriers on abyss are straight lines with no diagonals". Each painted piece is one
    axis-aligned line a cell wide; the wall seal joins two of them at right angles."""
    from scipy import ndimage

    paint = cg.load_tags()["maps"]["Abyss"]["barrier_paint"]
    px = cg.unpack_paint(paint)
    step = cg.PX // cg.PAINT_GRID
    cells = px[::step, ::step]
    labels, n = ndimage.label(cells, structure=np.ones((3, 3)))
    assert n == 9, "ten valoplant lines, two of them joined by the seal"
    for sl in ndimage.find_objects(labels):
        piece = labels[sl] > 0
        h, w = piece.shape
        if piece.all():
            assert min(h, w) == 1, (sl, h, w)       # one straight line
        else:
            # the seal piece: a horizontal line meeting vertical lines, each part straight
            rows = [r for r in range(h) if piece[r].sum() > 2]
            cols = [c for c in range(w) if piece[:, c].sum() > 2]
            assert len(rows) == 2 and rows[1] == rows[0] + 1, (sl, rows)
            union = np.zeros_like(piece)
            union[rows] = piece[rows]
            for c in cols:
                ys = np.flatnonzero(piece[:, c])
                assert ys[-1] - ys[0] + 1 == len(ys), f"column {c} is one unbroken line"
                union[:, c] |= piece[:, c]
            assert np.array_equal(union, piece), "nothing but the seal's rows and straight columns"
            assert all(np.flatnonzero(piece[r]).size == np.ptp(np.flatnonzero(piece[r])) + 1 for r in rows)

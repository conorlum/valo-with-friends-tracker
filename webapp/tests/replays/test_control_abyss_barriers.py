"""Abyss's committed barrier paint (BUG-01): at the barrier drop the attackers' start ground stays behind
their barriers. A 33 px gap between paint strokes 8 (x 720-723, y 748-803 px) and 9 (x 756-763, y 804-843)
let it leak into the central and lower corridors (2939 cells instead of 1358).

PROVISIONAL(D2): joined strokes 8/9 (option a), paint x 720-763, y 800-807; option (b), extending stroke 8
down to the wall, is the owner's eye call."""

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
DEFENSE_CELLS = 3290                        # measured on the base paint (barrier_sha 279e647ec456)


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


def test_abyss_defense_start_ground_is_unchanged(drop):
    geo, areas, tick = drop
    assert "A" in areas, dict(tick.rnd.missing)
    assert int(geo.to_cells(areas["A"][0]).sum()) == DEFENSE_CELLS

"""The unknown's spread with parents (timing-gaps spec, section 4, "Route history"): the same arrivals as
without, and each reached node's parent is a neighbour (or link end) that arrived earlier."""

import math

import numpy as np
import pytest

from app.control import topology
from app.control.geometry import GRID
from tests.replays.control_toys import door_hall, toy_heights


def _setup(geo):
    topo = topology.of(geo)
    n = geo.n
    room = geo.walk_n.copy() if hasattr(geo, "walk_n") else geo.walk.ravel().copy()
    reached = np.full(n, np.inf)
    start = int(np.flatnonzero(room)[0])
    reached[start] = 0.0
    free = np.zeros(n)
    return topo, room, reached, free, start


@pytest.mark.parametrize("make", [door_hall, lambda: toy_heights("Spread", [(96, 96, 416, 296)],
                                                                  upper=[((200, 150, 260, 200), 3.0)])])
def test_parents_do_not_change_arrivals(make):
    geo = make()
    topo, room, reached, free, _ = _setup(geo)
    plain = topo.spread(reached, room, free, 20.0, 0.35, [])
    with_parents, parent = topo.spread(reached, room, free, 20.0, 0.35, [], parents=True)
    assert np.array_equal(plain, with_parents)
    assert parent.dtype == np.int64 and len(parent) == geo.n


def test_every_reached_node_points_to_an_earlier_neighbour():
    geo = door_hall()
    topo, room, reached, free, start = _setup(geo)
    arr, parent = topo.spread(reached, room, free, 20.0, 0.35, [], parents=True)
    for node in np.flatnonzero(np.isfinite(arr)).tolist():
        if node == start:
            assert parent[node] == -1
            continue
        p = int(parent[node])
        assert p >= 0 and arr[p] < arr[node]
        py, px = divmod(p, GRID)
        ny, nx = divmod(node, GRID)
        assert max(abs(py - ny), abs(px - nx)) == 1, "a parent is one of the 8 neighbours"
        step = math.sqrt(2) if py != ny and px != nx else 1.0
        assert arr[node] == pytest.approx(max(arr[p], free[node]) + 0.35 * step)


def test_a_link_is_a_parent():
    geo = door_hall()
    topo, room, reached, free, start = _setup(geo)
    far = int(np.flatnonzero(room)[-1])
    arr, parent = topo.spread(reached, room, free, 0.5, 0.35, [(start, far, True)], parents=True)
    assert parent[far] == start and arr[far] == pytest.approx(0.35)

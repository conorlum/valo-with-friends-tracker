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


def _first_in_order(arr, free, reached, straight=0.35):
    """The definition: for each finite node, the first neighbour in SPREAD_ORDER whose time equals its
    arrival (-1 if none, or if the arrival is the node's starting value)."""
    want = np.full(len(arr), -1, np.int64)
    for node in np.flatnonzero(np.isfinite(arr)).tolist():
        if arr[node] >= reached[node]:
            continue
        y, x = divmod(node, GRID)
        for dy, dx in topology.SPREAD_ORDER:
            ny, nx = y - dy, x - dx
            if 0 <= ny < GRID and 0 <= nx < GRID:
                p = ny * GRID + nx
                if max(arr[p], free[node]) + straight * (math.sqrt(2) if dy and dx else 1.0) == arr[node]:
                    want[node] = p
                    break
    return want


def test_parents_follow_the_final_state_with_patchy_free_and_unreached_are_minus_one():
    geo = door_hall()
    topo, room, reached, _, _ = _setup(geo)
    rng = np.random.default_rng(3)
    free = np.where(rng.random(geo.n) < 0.3, 4.0, 0.0)
    arr, parent = topo.spread(reached, room, free, 20.0, 0.35, [], parents=True)
    assert np.array_equal(parent, _first_in_order(arr, free, reached))
    assert (parent[~np.isfinite(arr)] == -1).all() and (~np.isfinite(arr)).any()


def test_equal_neighbours_tie_goes_to_the_first_in_spread_order():
    geo = door_hall()
    topo, room, _, free, _ = _setup(geo)
    ys, xs = np.nonzero(room.reshape(GRID, GRID))
    c = int(np.flatnonzero(room)[len(ys) // 2])
    y, x = divmod(c, GRID)
    reached = np.full(geo.n, np.inf)
    reached[(y - 1) * GRID + x] = 1.0      # north and west are equally early; (0, 1), a step east, is listed before (1, 0), a step south, so west wins
    reached[y * GRID + x - 1] = 1.0
    arr, parent = topo.spread(reached, room, free, 1.4, 0.35, [], parents=True)
    assert room[(y - 1) * GRID + x] and room[y * GRID + x - 1] and arr[c] == pytest.approx(1.35)
    assert parent[c] == y * GRID + x - 1


def test_node_topology_parents_match_the_definition_and_unreached_are_minus_one():
    geo = toy_heights("Spread", [(96, 96, 416, 296)], upper=[((200, 150, 260, 200), 3.0)])
    topo, room, reached, _, _ = _setup(geo)
    free = np.where(np.random.default_rng(5).random(geo.n) < 0.3, 4.0, 0.0)
    arr, parent = topo.spread(reached, room, free, 20.0, 0.35, [], parents=True)
    cost = topo.in_cost * 0.35
    for node in range(geo.n):
        if not np.isfinite(arr[node]) or arr[node] >= reached[node]:
            assert parent[node] == -1
            continue
        times = np.maximum(np.append(arr, np.inf)[topo.in_from[node]], free[node]) + cost[node]
        assert parent[node] == topo.in_from[node][int(np.flatnonzero(times == arr[node])[0])]


def test_a_link_is_a_parent():
    geo = door_hall()
    topo, room, reached, free, start = _setup(geo)
    far = int(np.flatnonzero(room)[-1])
    arr, parent = topo.spread(reached, room, free, 0.5, 0.35, [(start, far, True)], parents=True)
    assert parent[far] == start and arr[far] == pytest.approx(0.35)

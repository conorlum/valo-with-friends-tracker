"""Frontier relaxation preserves arrival values and route parent tie-breaking."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from app.control import topology


def graph(seed):
    rng = np.random.default_rng(seed)
    n = 31
    edges = sorted({(int(a), int(b)) for a, b in rng.integers(0, n, (110, 2)) if a != b})
    src, dst = np.array(edges).T
    width = int(np.bincount(dst, minlength=n).max())
    node = topology.NodeTopology.__new__(topology.NodeTopology)
    node.n, node.geo = n, SimpleNamespace(adaptive=True)
    node.walk = rng.random(n) > .1
    node._out = csr_matrix((np.ones(len(src), bool), (src, dst)), shape=(n, n))
    node.in_from = np.full((n, width), n, np.int64)
    node.in_cost = np.full((n, width), np.inf)
    for b in range(n):
        incoming = src[dst == b]
        node.in_from[b, :len(incoming)] = incoming
        node.in_cost[b, :len(incoming)] = rng.choice([0., .25, .5, .75, 1., np.sqrt(2)], len(incoming))
    node.in_dilation_cost = node.in_cost.copy()
    node.in_cost_quiet = np.where(rng.random(node.in_cost.shape) < .2, np.inf, node.in_cost)
    none = topology.GRID ** 2 + 256 ** 2
    node.in_side = np.full((2, n, width), none, np.int64)
    node.in_side[0][rng.random(node.in_cost.shape) < .2] = 0
    cut = rng.random(node.in_cost.shape) < .1
    node._cut = lambda solid: cut
    return node, rng


def reference(node, reached, costs, allowed, limit, free=None, links=()):
    current = reached.copy()
    links = list(links)
    while True:
        incoming = np.append(current, np.inf)[node.in_from]
        if free is not None:
            incoming = np.maximum(incoming, free[:, None])
        grown = np.minimum(current, (incoming + costs).min(1))
        for a, b, one_way, cost in links:
            grown[b] = min(grown[b], max(current[a], free[b]) + cost)
            if not one_way:
                grown[a] = min(grown[a], max(current[b], free[a]) + cost)
        grown[~allowed | (grown > limit)] = np.inf
        if np.array_equal(grown, current):
            return grown
        current = grown


@pytest.mark.parametrize('seed', range(8))
def test_frontier_dilation_matches_full_graph_with_direction_cuts_and_limits(seed):
    node, rng = graph(seed)
    for eight in (False, True):
        for iterations in (0, 1, 2, 5):
            for use_within in (False, True):
                mask = rng.random(node.n) < .15
                within = rng.random(node.n) < .7 if use_within else None
                solid = rng.random(node.n) < .15
                cost = (node.in_dilation_cost if eight else node.in_cost).copy()
                if not eight:
                    cost[node.in_side[0] != topology.GRID ** 2 + 256 ** 2] = np.inf
                if solid.any():
                    cost[node._cut(solid)] = np.inf
                expected = reference(node, np.where(mask, 0., np.inf), cost,
                    node.walk if within is None else within | mask, max(1, iterations) + 1e-8)
                assert np.array_equal(node.dilate(mask, eight, iterations, within, solid),
                                      mask | np.isfinite(expected))


@pytest.mark.parametrize('seed', range(8))
def test_frontier_spread_keeps_waits_quiet_edges_timed_routes_and_parent_ties(seed, monkeypatch):
    node, rng = graph(seed)
    original = node._frontier
    for quiet in (False, True):
        reached = np.where(rng.random(node.n) < .15, rng.choice([0., .5, 1.], node.n), np.inf)
        room = rng.random(node.n) < .8
        free = rng.choice([0., .25, 1., np.inf], node.n)
        solid = rng.random(node.n) < .1
        links = [(0, 30, True, 1.5), (3, 23, False, 2.), (4, 27, False)]
        monkeypatch.setattr(node, '_frontier', lambda *a: reference(node, *a))
        expected = node.spread(reached, room, free, 4., .7, links, True, solid, quiet)
        monkeypatch.setattr(node, '_frontier', original)
        actual = node.spread(reached, room, free, 4., .7, links, True, solid, quiet)
        assert np.array_equal(actual[0], expected[0])
        assert np.array_equal(actual[1], expected[1])

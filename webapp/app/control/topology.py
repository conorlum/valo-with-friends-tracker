"""How the places of a map connect, for the control engine
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4, "Nodes" and "Walking per floor").

The engine keeps one value per node (geometry.py: a cell on a flat map, a floor of a cell on a map with
heights) in flat arrays of length `geo.n`, and asks a topology for everything that depends on which
nodes are next to which: connected pieces, growing a mask by a step, walking distances, the unknown's
spread.

`FlatTopology` is a flat map's: the GRID x GRID image operations the engine always used, verbatim, so a
map without heights computes exactly what it did (tests/replays/test_control_reference.py pins the bytes).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from app.control.geometry import GRID, Geometry

EIGHT = np.ones((3, 3), bool)
# The order a way back looks for its next cell, and a player on a barrier line for open ground.
BACK_ORDER = [(oy, ox) for oy in (-1, 0, 1) for ox in (-1, 0, 1)]
AROUND_ORDER = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
SPREAD_ORDER = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


class FlatTopology:
    """A flat map: every cell joins its neighbours in the grid (4 by default, 8 with `eight`)."""

    def __init__(self, geo: Geometry):
        self.geo = geo
        self.n = GRID * GRID
        self.walk = geo.walk.ravel()
        self.links: list = []       # one-way walks that aren't the map's specials: none on a flat map

    def label(self, mask: np.ndarray, eight: bool = False) -> np.ndarray:
        """Each node's connected piece of `mask` (1, 2, ...; 0 outside it)."""
        lab, _ = ndimage.label(mask.reshape(GRID, GRID), EIGHT if eight else None)
        return lab.ravel()

    def dilate(self, mask: np.ndarray, eight: bool = False, iterations: int = 1,
               within: np.ndarray | None = None) -> np.ndarray:
        """`mask` grown by `iterations` steps, through `within` only when given (nodes outside it keep
        their own value)."""
        return ndimage.binary_dilation(mask.reshape(GRID, GRID), EIGHT if eight else None, iterations=iterations,
                                       mask=None if within is None else within.reshape(GRID, GRID)).ravel()

    def edge_out(self, mask: np.ndarray) -> np.ndarray:
        """The nodes of `mask` next to anything that isn't in it (8-connected; walls and the map's edge
        count)."""
        return mask & self.dilate(~mask, eight=True)

    def dist(self, start: int) -> np.ndarray:
        """Walking distance in 8-connected steps from `start` over walkable nodes; -1 where unreachable."""
        walk = self.walk.reshape(GRID, GRID)
        dist = np.full((GRID, GRID), -1, np.int32)
        front = np.zeros((GRID, GRID), bool)
        front.flat[start] = True
        seen = front.copy()
        d = 0
        while front.any():
            dist[front] = d
            d += 1
            nxt = ndimage.binary_dilation(front, EIGHT) & walk & ~seen
            seen |= nxt
            front = nxt
        return dist.ravel()

    def back(self, dist: np.ndarray, cur: int) -> int:
        """From `cur`, a neighbour one step nearer the start of `dist` (the first in BACK_ORDER)."""
        cy, cx = divmod(cur, GRID)
        want = dist[cur] - 1
        for oy, ox in BACK_ORDER:
            ny, nx = cy + oy, cx + ox
            if 0 <= ny < GRID and 0 <= nx < GRID and dist[ny * GRID + nx] == want:
                return ny * GRID + nx
        raise ValueError("no way back")

    def around(self, node: int) -> list[int]:
        """`node`'s neighbours: the four beside it, then the diagonals."""
        y, x = divmod(node, GRID)
        return [ny * GRID + nx for ny, nx in ((y + dy, x + dx) for dy, dx in AROUND_ORDER)
                if 0 <= ny < GRID and 0 <= nx < GRID]

    def spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float, straight: float,
               links: list) -> np.ndarray:
        """The unknown's arrival times relaxed through `room` up to time `t` (engine.Unknown._spread):
        each node's earliest arrival from a neighbour, after the node was last freed (`free`); a straight
        step costs `straight` seconds, a diagonal sqrt(2) of it, a link (a, b, one way) one straight step.
        Arrivals later than `t` aren't there yet."""
        g = reached.reshape(GRID, GRID).copy()
        f = free.reshape(GRID, GRID)
        r = room.reshape(GRID, GRID)
        while True:
            best = g.copy()
            for dy, dx in SPREAD_ORDER:
                src = np.full((GRID, GRID), np.inf)
                src[max(dy, 0):GRID + min(dy, 0), max(dx, 0):GRID + min(dx, 0)] = \
                    g[max(-dy, 0):GRID + min(-dy, 0), max(-dx, 0):GRID + min(-dx, 0)]
                np.minimum(best, np.maximum(src, f) + straight * (math.sqrt(2) if dy and dx else 1.0), out=best)
            for a, b, one_way in links:
                best.flat[b] = min(best.flat[b], max(g.flat[a], f.flat[b]) + straight)
                if not one_way:
                    best.flat[a] = min(best.flat[a], max(g.flat[b], f.flat[a]) + straight)
            best[~r | (best > t)] = np.inf
            if np.array_equal(best, g):
                return best.ravel()
            g = best


def of(geo: Geometry):
    """The geometry's topology, built once and kept on it."""
    topo = getattr(geo, "topo", None)
    if topo is None or topo.n != geo.n:
        topo = geo.topo = FlatTopology(geo)
    return topo

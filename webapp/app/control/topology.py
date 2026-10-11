"""How the places of a map connect, for the control engine
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4, "Nodes" and "Walking per floor").

The engine keeps one value per node (geometry.py: a cell on a flat map, a floor of a cell on a map with
heights) in flat arrays of length `geo.n`, and asks a topology for everything that depends on which
nodes are next to which: connected pieces, growing a mask by a step, walking distances, the unknown's
spread.

`FlatTopology` is a flat map's: the GRID x GRID image operations the engine always used, verbatim, so a
map without heights computes exactly what it did (tests/replays/test_control_reference.py pins the bytes).

`NodeTopology` is a map with heights: a graph of walks between nodes. Between resolved floors the walks
are the height asset's (heights.py `edges`: neighbouring floors within a step, and bigger steps where
players walked them; a drop is one-way). A drop is a slide or a fall (heights.py EDGE_*): the unknown's spread,
asked with `quiet`, takes a slide of any height and a fall of at most SILENT_DROP_M, because a higher landing
always makes a sound. Everything else walks every drop. An unresolved cell has one node with today's 2D walking: it
joins every floor of each walkable cell around it, both ways. A cell's own floors are never neighbours.
Walks run along the grid's 8 directions; 4-connected operations use the straight ones only. One-way
walks count in the direction they go (growing a mask, distances, the unknown's spread); connected pieces
are over the two-way walks, with the one-way ones as `links`, like the map's one-way specials.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, shortest_path

from app.control import heights as hc
from app.control.geometry import GRID, Geometry

EIGHT = np.ones((3, 3), bool)
# The order a way back looks for its next cell, and a player on a barrier line for open ground.
BACK_ORDER = [(oy, ox) for oy in (-1, 0, 1) for ox in (-1, 0, 1)]
AROUND_ORDER = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
SPREAD_ORDER = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def timed_links(links, straight):
    """Legacy links cost a walk step; reviewed rope links carry explicit seconds."""
    for link in links:
        a, b, one_way = link[:3]
        cost = straight if len(link) == 3 else link[3]
        if not math.isfinite(cost) or cost <= 0:
            raise ValueError('link cost must be finite and positive')
        yield a, b, one_way, cost


def _shift(a: np.ndarray, dy: int, dx: int, fill=False) -> np.ndarray:
    """`a` moved by (dy, dx) on the grid: out[y, x] = a[y - dy, x - dx], `fill` off the edge."""
    out = np.full(a.shape, fill, a.dtype)
    out[max(dy, 0):GRID + min(dy, 0), max(dx, 0):GRID + min(dx, 0)] = \
        a[max(-dy, 0):GRID + min(-dy, 0), max(-dx, 0):GRID + min(-dx, 0)]
    return out


def _cuts(solid: np.ndarray | None) -> dict:
    """(dy, dx) -> where a diagonal step of that direction into (y, x) is cut: it passes a `solid` cell (a
    trip's), (y - dy, x) or (y, x - dx). Empty with no solid cells."""
    if solid is None or not solid.any():
        return {}
    s = solid.reshape(GRID, GRID)
    return {(dy, dx): _shift(s, dy, 0) | _shift(s, 0, dx) for dy, dx in SPREAD_ORDER if dy and dx}


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
               within: np.ndarray | None = None, solid: np.ndarray | None = None) -> np.ndarray:
        """`mask` grown by `iterations` steps, through `within` only when given (nodes outside it keep
        their own value). With `solid` (and `eight`), no diagonal step past a solid cell, as in `spread`."""
        cut = _cuts(solid) if eight else {}
        if cut:
            cur = mask.reshape(GRID, GRID).astype(bool)
            w = None if within is None else within.reshape(GRID, GRID)
            for _ in range(max(1, iterations)):
                grown = cur.copy()
                for dy, dx in SPREAD_ORDER:
                    step = _shift(cur, dy, dx)
                    if (dy, dx) in cut:
                        step &= ~cut[dy, dx]
                    grown |= step
                cur = grown if w is None else np.where(w, grown, cur)
            return cur.ravel()
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
               links: list, parents: bool = False, solid: np.ndarray | None = None, quiet: bool = False):
        """The unknown's arrival times relaxed through `room` up to time `t` (engine.Unknown._spread; `quiet`
        means nothing on a flat map, which has no falls):
        each node's earliest arrival from a neighbour, after the node was last freed (`free`); a straight
        step costs `straight` seconds, a diagonal sqrt(2) of it, a link (a, b, one way) one straight step.
        A diagonal step may not cut past a `solid` cell (a trip's) beside it. Arrivals later than `t`
        aren't there yet. With `parents`, also each arrival's source node, taken from the final arrivals:
        the first neighbour in SPREAD_ORDER that gives exactly that time, then a link; -1 for an unreached
        node and for one whose value is its starting value."""
        g = reached.reshape(GRID, GRID).copy()
        f = free.reshape(GRID, GRID)
        r = room.reshape(GRID, GRID)
        cut = _cuts(solid)
        while True:
            best = g.copy()
            for dy, dx in SPREAD_ORDER:
                src = _shift(g, dy, dx, np.inf)
                if (dy, dx) in cut:
                    src[cut[dy, dx]] = np.inf
                np.minimum(best, np.maximum(src, f) + straight * (math.sqrt(2) if dy and dx else 1.0), out=best)
            for a, b, one_way, cost in timed_links(links, straight):
                best.flat[b] = min(best.flat[b], max(g.flat[a], f.flat[b]) + cost)
                if not one_way:
                    best.flat[a] = min(best.flat[a], max(g.flat[b], f.flat[a]) + cost)
            best[~r | (best > t)] = np.inf
            if np.array_equal(best, g):
                break
            g = best
        arr = best.ravel()
        if not parents:
            return arr
        # recompute from the final arrivals, with the same expressions the relaxation used
        idx = np.arange(GRID * GRID).reshape(GRID, GRID)
        par = np.full((GRID, GRID), -1, np.int64)
        todo = np.isfinite(best) & (best < reached.reshape(GRID, GRID))
        for dy, dx in SPREAD_ORDER:
            src = np.full((GRID, GRID), np.inf)
            who = np.full((GRID, GRID), -1, np.int64)
            ys, xs = slice(max(dy, 0), GRID + min(dy, 0)), slice(max(dx, 0), GRID + min(dx, 0))
            yo, xo = slice(max(-dy, 0), GRID + min(-dy, 0)), slice(max(-dx, 0), GRID + min(-dx, 0))
            src[ys, xs] = best[yo, xo]
            who[ys, xs] = idx[yo, xo]
            if (dy, dx) in cut:
                src[cut[dy, dx]] = np.inf
            hit = todo & (par < 0) & (np.maximum(src, f) + straight * (math.sqrt(2) if dy and dx else 1.0) == best)
            par[hit] = who[hit]
        pf = par.ravel()
        for a, b, one_way, cost in timed_links(links, straight):
            if np.isfinite(arr[b]) and arr[b] < reached[b] and pf[b] < 0 and max(arr[a], free[b]) + cost == arr[b]:
                pf[b] = a
            if not one_way and np.isfinite(arr[a]) and arr[a] < reached[a] and pf[a] < 0 \
                    and max(arr[b], free[a]) + cost == arr[a]:
                pf[a] = b
        return arr, pf


class NodeTopology:
    """A map with heights: the walks between nodes as a graph (see the module docstring)."""

    def __init__(self, geo: Geometry):
        self.geo = geo
        self.n = n = geo.n
        self.walk = geo.walk_n
        cell_walk = geo.walk.ravel()
        walks: set[tuple[int, int]] = set()
        node_of = geo.node_of
        loud: set[tuple[int, int]] = set()    # falls too high to land quietly: closed to the unknown's spread
        floors = geo.heights.floors.reshape(GRID * GRID, -1)
        for a, fa, b, fb, kind in geo.heights.edges.tolist():
            na, nb = int(node_of[a, fa]), int(node_of[b, fb])
            if na >= 0 and nb >= 0 and cell_walk[a] and cell_walk[b] and not geo.unresolved[a] and not geo.unresolved[b]:
                walks.add((na, nb))
                if kind == hc.EDGE_FALL and int(floors[a, fa]) - int(floors[b, fb]) > hc.SILENT_DROP_M * 10:
                    loud.add((na, nb))
        for cell in np.flatnonzero(geo.unresolved).tolist():
            y, x = divmod(cell, GRID)
            for dy, dx in SPREAD_ORDER:
                ny, nx = y + dy, x + dx
                if 0 <= ny < GRID and 0 <= nx < GRID and cell_walk[ny * GRID + nx]:
                    for other in node_of[ny * GRID + nx]:
                        if other >= 0:
                            walks.add((cell, int(other)))
                            walks.add((int(other), cell))
        pairs = np.array(sorted(walks), np.int64).reshape(-1, 2)
        src, dst = pairs[:, 0], pairs[:, 1]
        cs, cd = geo.node_cell[src], geo.node_cell[dst]
        diag = (cs // GRID != cd // GRID) & (cs % GRID != cd % GRID)
        self.src, self.dst, self.diag = src, dst, diag
        two_way = np.array([(b, a) in walks for a, b in pairs.tolist()], bool) if len(pairs) else np.zeros(0, bool)
        # one-way walks (drops), in the engine's link shape: (from, to, one way)
        self.links = [(int(a), int(b), True) for a, b in pairs[~two_way].tolist()]
        ones = np.ones(len(src), bool)

        def matrix(keep):
            return csr_matrix((ones[keep], (src[keep], dst[keep])), shape=(n, n))

        self._both = {False: matrix(two_way & ~diag), True: matrix(two_way)}     # for connected pieces
        self._step = {False: matrix(~diag).T.tocsr(), True: matrix(ones).T.tocsr()}   # row b: who walks into b
        self._out = matrix(ones)
        # each node's incoming walks as a padded table, for the unknown's spread
        order = np.argsort(dst, kind="stable")
        counts = np.bincount(dst, minlength=n)
        width = int(counts.max()) if len(dst) else 1
        self.in_from = np.full((n, max(width, 1)), n, np.int64)        # n: a dummy node that is never reached
        self.in_cost = np.full((n, max(width, 1)), np.inf)
        starts = np.concatenate([[0], np.cumsum(counts)])[:-1]
        column = np.arange(len(dst)) - starts[dst[order]]
        self.in_from[dst[order], column] = src[order]
        self.in_cost[dst[order], column] = np.where(diag[order], math.sqrt(2), 1.0)
        # the same table for a quiet walker: a fall of more than SILENT_DROP_M is not a way in
        is_loud = np.array([pair in loud for pair in map(tuple, pairs.tolist())], bool) if len(pairs) else np.zeros(0, bool)
        self.in_cost_quiet = self.in_cost.copy()
        self.in_cost_quiet[dst[order], column] = np.where(is_loud[order], np.inf, self.in_cost[dst[order], column])
        # a diagonal walk's two side cells (a solid one, a trip's, stops it); GRID * GRID: no side cell
        none = GRID * GRID
        self.in_side = np.full((2, n, max(width, 1)), none, np.int64)
        cs_o, cd_o, dg = cs[order], cd[order], diag[order]
        self.in_side[0, dst[order], column] = np.where(dg, (cs_o // GRID) * GRID + cd_o % GRID, none)
        self.in_side[1, dst[order], column] = np.where(dg, (cd_o // GRID) * GRID + cs_o % GRID, none)
        self._adj = (self._out + self._out.T).tocsc()          # a walk either way, for `_cut`
        self._cut_key = self._cut_val = None
        # nodes beside a wall or the map's edge: their cell has a neighbour that isn't walkable
        grid = geo.walk
        beside = (grid & ndimage.binary_dilation(~grid, EIGHT)).ravel()
        self._rim = beside[geo.node_cell] & self.walk

    def label(self, mask: np.ndarray, eight: bool = False) -> np.ndarray:
        lab = np.zeros(self.n, np.int32)
        nodes = np.flatnonzero(mask)
        if len(nodes):
            graph = self._both[eight][nodes][:, nodes]
            _, piece = connected_components(graph, directed=False)
            lab[nodes] = piece + 1
        return lab

    def _cut(self, solid: np.ndarray) -> np.ndarray:
        """Per incoming walk (the `in_from` table): a diagonal walk past a `solid` node (a trip's) on its own
        floor, i.e. one in a side cell that is a walk away from either end of it. A trip on another floor of
        that cell doesn't cut it. Kept for the last `solid`: a tick's trips are the same for every enemy."""
        key = solid.tobytes()
        if key == self._cut_key:
            return self._cut_val
        none = GRID * GRID
        cut = np.zeros(self.in_from.shape, bool)
        trips = np.flatnonzero(solid)
        if len(trips):
            near = self._adj[:, trips].tocoo()        # (node, solid node) a walk apart
            pairs = np.unique(near.row.astype(np.int64) * (none + 1) + self.geo.node_cell[trips[near.col]])
            ends = (self.in_from, np.broadcast_to(np.arange(self.n)[:, None], self.in_from.shape))
            for side in self.in_side:
                diag = side != none
                for end in ends:
                    cut[diag] |= np.isin(end[diag] * (none + 1) + side[diag], pairs)
        self._cut_key, self._cut_val = key, cut
        return cut

    def dilate(self, mask: np.ndarray, eight: bool = False, iterations: int = 1,
               within: np.ndarray | None = None, solid: np.ndarray | None = None) -> np.ndarray:
        cur = mask.astype(bool)
        if solid is not None and eight and solid.any():   # through the walks `spread` may take (`_cut`)
            ok = np.isfinite(self.in_cost) & ~self._cut(solid)
            for _ in range(max(1, iterations)):
                grown = cur | (np.append(cur, False)[self.in_from] & ok).any(1)
                cur = grown if within is None else np.where(within, grown, cur)
            return cur
        step = self._step[eight]
        for _ in range(max(1, iterations)):
            grown = cur | (step @ cur.astype(np.int32) > 0)
            cur = grown if within is None else np.where(within, grown, cur)
        return cur

    def edge_out(self, mask: np.ndarray) -> np.ndarray:
        return mask & (self.dilate(~mask & self.walk, eight=True) | self._rim)

    def dist(self, start: int) -> np.ndarray:
        d = shortest_path(self._out, unweighted=True, indices=start)
        return np.where(np.isfinite(d), d, -1).astype(np.int32)

    def back(self, dist: np.ndarray, cur: int) -> int:
        row = self._step[True]
        nearer = [int(p) for p in row.indices[row.indptr[cur]:row.indptr[cur + 1]] if dist[p] == dist[cur] - 1]
        if not nearer:
            raise ValueError("no way back")
        return min(nearer)

    def around(self, node: int) -> list[int]:
        out = self._out
        near = out.indices[out.indptr[node]:out.indptr[node + 1]].tolist()
        cell = self.geo.node_cell
        straight = [p for p in near if cell[p] // GRID == cell[node] // GRID or cell[p] % GRID == cell[node] % GRID]
        return sorted(straight) + sorted(set(near) - set(straight))

    def spread(self, reached: np.ndarray, room: np.ndarray, free: np.ndarray, t: float, straight: float,
               links: list, parents: bool = False, solid: np.ndarray | None = None, quiet: bool = False):
        """As `FlatTopology.spread`. With `parents`, a node's source is the first of its `in_from` columns
        (ascending source node id) that gives exactly its final arrival, then a link; -1 as there. With
        `quiet`, no arrival comes down a fall of more than SILENT_DROP_M (heights.py EDGE_FALL)."""
        g = reached.copy()
        cost = (self.in_cost_quiet if quiet else self.in_cost) * straight
        if solid is not None and solid.any():
            cost = np.where(self._cut(solid), np.inf, cost)
        freed = free[:, None]
        while True:
            padded = np.append(g, np.inf)
            best = np.minimum(g, (np.maximum(padded[self.in_from], freed) + cost).min(1))
            for a, b, one_way, link_cost in timed_links(links, straight):
                best[b] = min(best[b], max(g[a], free[b]) + link_cost)
                if not one_way:
                    best[a] = min(best[a], max(g[b], free[a]) + link_cost)
            best[~room | (best > t)] = np.inf
            if np.array_equal(best, g):
                break
            g = best
        if not parents:
            return best
        rows = np.arange(self.n)
        cand = np.maximum(np.append(best, np.inf)[self.in_from], freed) + cost
        match = cand == best[:, None]
        j = match.argmax(1)
        todo = np.isfinite(best) & (best < reached) & match.any(1)
        par = np.where(todo, self.in_from[rows, j], -1).astype(np.int64)
        for a, b, one_way, link_cost in timed_links(links, straight):
            if np.isfinite(best[b]) and best[b] < reached[b] and par[b] < 0 \
                    and max(best[a], free[b]) + link_cost == best[b]:
                par[b] = a
            if not one_way and np.isfinite(best[a]) and best[a] < reached[a] and par[a] < 0 \
                    and max(best[b], free[a]) + link_cost == best[a]:
                par[a] = b
        return best, par


def of(geo: Geometry):
    """The geometry's topology, built once and kept on it."""
    topo = getattr(geo, "topo", None)
    if topo is None or topo.n != geo.n or (geo.heights is not None) != isinstance(topo, NodeTopology):
        topo = geo.topo = NodeTopology(geo) if geo.heights is not None else FlatTopology(geo)
    return topo

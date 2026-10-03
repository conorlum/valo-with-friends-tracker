"""Choke detection (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2); the asset itself is
app/replays/choke_assets.py.

A cell is a pinch when, across some direction, the walkable span through it is at most MAX_CHOKE_M and the
span WIDEN_CELLS further along the passage, on both sides, is wider by at least WIDEN_CELLS. A wider span
counts only when every along-axis cell for steps 1..WIDEN_CELLS in that direction is walkable, so a wall
corner is not a choke (no jumping over it). Touching pinch cells are one choke, whose cells are the spans
across them (the line across the passage). Each of the map's special links (teleporters, ropes, drops) is a
choke of its two end cells. Chokes never affect control: the engine only labels routes with them."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from app.control.geometry import GRID
from app.replays.choke_assets import Choke

MAX_CHOKE_M = 8.0
WIDEN_CELLS = 2
# (along, across): unit steps; across is perpendicular to along
_AXES = [((0, 1), (1, 0)), ((1, 0), (0, 1)), ((1, 1), (1, -1)), ((1, -1), (1, 1))]


def _span(walk: np.ndarray, y: int, x: int, dy: int, dx: int) -> list[int]:
    """The walkable cells on the line through (y, x) in direction +-(dy, dx), up to the first non-walkable."""
    out = [y * GRID + x]
    for sign in (1, -1):
        cy, cx = y + sign * dy, x + sign * dx
        while 0 <= cy < GRID and 0 <= cx < GRID and walk[cy, cx]:
            out.append(cy * GRID + cx)
            cy, cx = cy + sign * dy, cx + sign * dx
    return sorted(out)


def _walkable_run(walk: np.ndarray, y: int, x: int, ay: int, ax: int, sign: int) -> bool:
    for step in range(1, WIDEN_CELLS + 1):
        ny, nx = y + sign * step * ay, x + sign * step * ax
        if not (0 <= ny < GRID and 0 <= nx < GRID and walk[ny, nx]):
            return False
    return True


def detect(geo) -> list[list[int]]:
    walk = geo.walk
    limit = max(1, int(math.floor(MAX_CHOKE_M / geo.cell_m)))
    pinch = np.zeros((GRID, GRID), bool)
    across_of: dict[int, set[int]] = {}
    ys, xs = np.nonzero(walk)
    for y, x in zip(ys.tolist(), xs.tolist()):
        for (ay, ax), (cy, cx) in _AXES:
            here = _span(walk, y, x, cy, cx)
            if len(here) > limit:
                continue
            wider = 0
            for sign in (1, -1):
                if not _walkable_run(walk, y, x, ay, ax, sign):
                    continue
                ny, nx = y + sign * WIDEN_CELLS * ay, x + sign * WIDEN_CELLS * ax
                if len(_span(walk, ny, nx, cy, cx)) >= len(here) + WIDEN_CELLS:
                    wider += 1
            if wider == 2:
                pinch[y, x] = True
                across_of.setdefault(y * GRID + x, set()).update(here)
    lab, n = ndimage.label(pinch, np.ones((3, 3), bool))
    found = []
    for i in range(1, n + 1):
        cells = set()
        for c in np.flatnonzero(lab.ravel() == i).tolist():
            cells |= across_of[c]
        found.append(sorted(cells))
    for sp in geo.specials:
        try:
            a = geo.cell_of_px(*geo.px_of_uv(*sp["a"]))
            b = geo.cell_of_px(*geo.px_of_uv(*sp["b"]))
        except (KeyError, TypeError):
            continue
        found.append(sorted({a, b}))
    return sorted(found)


def node_chokes(geo, chokes: list[Choke] | None) -> np.ndarray:
    """Per node, the id of the choke its cell lies on (-1 for none). A choke covers every floor of its cells."""
    by_cell = np.full(GRID * GRID, -1, np.int32)
    for c in chokes or []:
        if not c.deleted:
            by_cell[c.cells] = c.id
    if geo.heights is None:
        return by_cell[: geo.n].copy()
    return by_cell[geo.node_cell].astype(np.int32)

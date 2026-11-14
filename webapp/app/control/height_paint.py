"""Apply reviewed fine-grid floor constraints using a donor snapshot, without I/O."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy import ndimage

from app.control import heights as hc, height_motion as hm


def painted_parents(paint, walk, fine_walk=None) -> set[int]:
    parents = {hm.coarse_of_fine(int(cell)) for mask in paint.values()
               for cell in np.flatnonzero(mask if fine_walk is None else mask & fine_walk)}
    return {parent for parent in parents if walk.ravel()[parent]}


def _neighbours(cell):
    y, x = divmod(cell, 256)
    for ny in range(max(0, y-1), min(256, y+2)):
        for nx in range(max(0, x-1), min(256, x+2)):
            if (ny, nx) != (y, x):
                yield ny*256+nx


def _median(values):
    ordered = sorted(values)
    return ordered[(len(ordered)-1)//2] if ordered else None


def _open_floors(own, highest, area_low, touching):
    high = own[-1] if own else _median(highest)
    if high is None:
        return []
    limit = high - int(hc.FLOOR_SEP_M * 10)
    low = own[0] if len(own) >= 2 and own[0] <= limit else None
    if low is None:
        low = _median(z for z in area_low if z <= limit)
    if low is None:
        low = _median(z for z in touching if z <= limit)
    return [] if low is None else [low, high]


def apply(children: list[dict], paint: dict, coarse_floors_world_dm: dict[int, list[int]], *, walk=None) -> dict:
    """Resolve each brush area from a snapshot, so mutations never become donor evidence.

    A refined parent's unknown child cannot borrow its old whole-parent floor.
    Unrefined neighbours contribute once per touching fine footprint.
    The fine walk mask includes parent eligibility and excludes targets and donors alike.
    """
    if walk is None:
        walk = np.ones((256, 256), bool)
    paint = {brush: mask & walk for brush, mask in paint.items()}
    rows = {row['cell']: row for row in children}
    snapshot = {cell: [int(z) for z, _ in row['floors_world_dm']] for cell, row in rows.items()}
    refined = {hm.coarse_of_fine(cell) for cell in rows}
    painted = np.zeros((256, 256), bool)
    for mask in paint.values():
        painted |= mask

    def floors(cell):
        if not walk.ravel()[cell]:
            return []
        parent = hm.coarse_of_fine(cell)
        if parent in refined:
            return snapshot.get(cell, [])
        return coarse_floors_world_dm.get(parent, [])

    counts = dict.fromkeys(('open', 'ground', 'top', 'unresolved'), 0)
    for brush, mask in paint.items():
        labels, _ = ndimage.label(mask, np.ones((3, 3), bool))
        areas = defaultdict(list)
        for cell in np.flatnonzero(mask):
            areas[int(labels.ravel()[cell])].append(int(cell))
        for area in areas.values():
            touching = {n for cell in area for n in _neighbours(cell) if not painted.ravel()[n]}
            area_floors = [floors(cell) for cell in area]
            touching_floors = [floors(cell) for cell in sorted(touching)]
            donors = [zs for zs in area_floors + touching_floors if zs]
            lowest = [zs[0] for zs in donors]
            highest = [zs[-1] for zs in donors]
            area_high = [zs[-1] for zs in area_floors if zs]
            area_low = [zs[0] for zs in area_floors if len(zs) >= 2]
            touching_values = [z for zs in touching_floors for z in zs]
            fallback = _median(lowest if brush == 'ground' else highest)
            for cell in area:
                if cell not in rows:
                    continue
                own = snapshot[cell]
                if brush == 'open':
                    chosen = _open_floors(own, area_high, area_low, touching_values)
                else:
                    value = (own[0] if brush == 'ground' else own[-1]) if own else fallback
                    chosen = [] if value is None else [value]
                rows[cell]['floors_world_dm'] = [[int(z), 0] for z in chosen]
                rows[cell]['kind'] = hc.KIND_FILLED
                rows[cell]['reason'] = None if chosen else 'height paint has no qualifying floor donor'
                counts[brush if chosen else 'unresolved'] += 1
    return counts

"""Wall-bounded drafts for the local map tagger."""

from collections import Counter, defaultdict, deque
from itertools import groupby

import numpy as np

from app.coach import areas
from app.replays.control_format import GRID

REGIONS = areas.SITES | {"Mid", "Attacker Side", "Defender Side"}


def encode_cells(cells) -> list[list[int]]:
    return [[int(value), sum(1 for _ in run)] for value, run in groupby(cells)]


def _neighbours(cell: int, grid: int):
    """Adjacent cell and the two orthogonal cells needed to walk to it."""
    y, x = divmod(cell, grid)
    for yy in range(max(0, y - 1), min(grid, y + 2)):
        for xx in range(max(0, x - 1), min(grid, x + 2)):
            neighbour = yy * grid + xx
            if neighbour != cell:
                yield neighbour, y * grid + xx, yy * grid + x


def _nearest_walk(cell: int, walk: list[bool], grid: int) -> int:
    """A label in a wall can move to the nearest walk cell through the wall."""
    queue = deque([cell])
    seen = {cell}
    while queue:
        cell = queue.popleft()
        if walk[cell]:
            return cell
        for neighbour, _, _ in _neighbours(cell, grid):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    raise ValueError("cannot seed a label on an empty walk mask")


def draft(map_name: str, walk, labels: list[dict] | None = None, *, grid: int = GRID) -> dict:
    """Assign reachable cells to the first label at the shortest walking distance."""
    labels = areas.callouts(map_name) if labels is None else labels
    if isinstance(walk, (set, frozenset)):
        walk = [cell in walk for cell in range(grid * grid)]
    else:
        walk = np.asarray(walk, dtype=bool).ravel().tolist()
        if len(walk) != grid * grid:
            raise ValueError("walk bitmap does not match the grid size")
    names = [
        {"id": i, "name": label["name"], "parent": None, "super": label["super"], "super_source": "auto"}
        for i, label in enumerate(sorted(labels, key=lambda label: label["name"]), 1)
    ]
    name_ids = {row["name"]: row["id"] for row in names}
    cells = [0] * (grid * grid)
    queue = deque()
    if any(walk):
        # Each BFS wave visits labels in callout order. First assignment wins a tie.
        for label in labels:
            seed = _nearest_walk(areas.cell_at(label["u"], label["v"], grid=grid), walk, grid)
            if cells[seed] == 0:
                cells[seed] = name_ids[label["name"]]
                queue.append(seed)
    while queue:
        cell = queue.popleft()
        for neighbour, across, down in _neighbours(cell, grid):
            if not cells[neighbour] and walk[neighbour] and walk[across] and walk[down]:
                cells[neighbour] = cells[cell]
                queue.append(neighbour)
    return {"names": names, "cells": encode_cells(cells)}


def auto_super(names: list[dict], cells: list[int], draft_cells: list[str | None]) -> list[dict]:
    """Resolve regions from the original draft super at each cell. Children vote with their root."""
    by_id = {row["id"]: row for row in names}
    roots = {name_id: areas._ancestors(by_id, name_id)[0] for name_id in by_id}
    votes = defaultdict(Counter)
    for name_id, region in zip(cells, draft_cells, strict=True):
        if name_id and region:
            votes[roots[name_id]][region] += 1
    supers = {}
    for row in names:
        if row["parent"] is not None:
            continue
        name_id = row["id"]
        region = row["super"]
        if row["super_source"] == "auto":
            if row["name"] in REGIONS:
                region = row["name"]
            elif votes[name_id]:
                region = min(votes[name_id], key=lambda key: (-votes[name_id][key], key))
        supers[name_id] = (region, row["super_source"])
    out = []
    for row in names:
        region, source = supers[roots[row["id"]]]
        out.append({**row, "super": region, "super_source": source})
    return out

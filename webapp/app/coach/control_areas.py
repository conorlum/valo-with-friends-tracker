"""A round's stored map control (app/replays/control_format.py) reduced to what the coach asks about: who held
each named area at each tick, and the sightings each side had. Decoded once per round when a snapshot is built."""

from __future__ import annotations

from bisect import bisect_right

from app.coach import areas
from app.replays import control_format as cf
from app.replays import format as fmt

# index into [a, b, contested] per state code; None is unowned
_OWNER = (None, 0, 0, 0, 1, 1, 1, 2, 2)


def area_shares(data: bytes, asset_key: str, table: dict | None = None) -> dict:
    header, streams = cf.unpack_data(data)
    walk = cf.walk_bitmap(header)
    painted = areas.load_painted(asset_key)
    cell_indices: dict[str, list[int]] = {}
    for index, cell in enumerate(walk):
        if painted is not None:
            # Ancestors include self and deduplicate roots reached through multiple parents.
            labels = {painted.names[name_id]["name"]
                      for name_id in painted.ancestors.get(painted.cells[cell], ())}
        else:
            u = (cell % cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
            v = (cell // cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
            area = areas._nearest_label(asset_key, u, v, table)
            labels = {area["name"]} if area and area["name"] else set()
        for name in labels:
            cell_indices.setdefault(name, []).append(index)
    names = sorted(cell_indices)
    frames = cf.decode_states(streams["states"], len(header["ticks"]), len(walk))
    per_area = {name: [] for name in names}
    for frame in frames:
        owners = [_OWNER[code] for code in frame]
        for name in names:
            counts = [0, 0, 0]
            for index in cell_indices[name]:
                owner = owners[index]
                if owner is not None:
                    counts[owner] += 1
            per_area[name].append(counts)
    return {"t": [tick / header["hz"] for tick in header["ticks"]],
            "approximate": painted is None,
            "cells": {name: len(cell_indices[name]) for name in names}, "areas": per_area,
            "knew": header.get("knew"), "signals": header.get("knowledge_events"),
            "group_side": header.get("group_side")}


def control_over(shares: dict, area: str, t0: float, t1: float) -> dict | None:
    rows, times = shares["areas"].get(area), shares["t"]
    if rows is None or not shares["cells"].get(area):
        return None
    totals, covered = [0.0, 0.0, 0.0], 0.0
    if t1 <= t0:                                    # one moment: the tick in force then
        i = bisect_right(times, t0) - 1
        if i < 0:
            return None
        totals, covered = [float(x) for x in rows[i]], 1.0
        times = []
    for i, start in enumerate(times):
        end = times[i + 1] if i + 1 < len(times) else max(t1, start)
        span = min(end, t1) - max(start, t0)
        if span <= 0 and not (i + 1 == len(times) and t0 <= start <= t1):
            continue
        span = max(span, 1e-9)
        covered += span
        for k in range(3):
            totals[k] += rows[i][k] * span
    if covered == 0:
        return None
    cells = shares["cells"][area]
    a, b, contested = (round(v / covered / cells, 2) for v in totals)
    return {"a": a, "b": b, "contested": contested, "none": round(max(0.0, 1 - a - b - contested), 2)}

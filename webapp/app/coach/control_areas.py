"""A round's stored map control (app/replays/control_format.py) reduced to what the coach asks about: who held
each named area at each tick, and the sightings each side had. Decoded once per round when a snapshot is built."""

from __future__ import annotations

from bisect import bisect_right

from app.coach import areas
from app.replays import control_format as cf
from app.replays import format as fmt

# index into [a, b, contested] per state code; None is unowned
_OWNER = (None, 0, 0, 0, 1, 1, 1, 2, 2)


def area_shares(data: bytes, map_name: str, table: dict | None = None) -> dict:
    header, streams = cf.unpack_data(data)
    walk = cf.walk_bitmap(header)
    labels = []
    for cell in walk:
        u = (cell % cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        v = (cell // cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        area = areas.area_at(map_name, u, v, table)
        labels.append(area["name"] if area else None)
    names = sorted({name for name in labels if name})
    frames = cf.decode_states(streams["states"], len(header["ticks"]), len(walk))
    per_area = {name: [] for name in names}
    for frame in frames:
        counts = {name: [0, 0, 0] for name in names}
        for label, code in zip(labels, frame):
            owner = _OWNER[code]
            if label is not None and owner is not None:
                counts[label][owner] += 1
        for name in names:
            per_area[name].append(counts[name])
    return {"t": [tick / header["hz"] for tick in header["ticks"]],
            "cells": {name: labels.count(name) for name in names}, "areas": per_area,
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

"""What a side knew of each enemy by a time, from map control's stored rows: `header["knew"]` (sightings, each
with a place) and `header["knowledge_events"]` (every locating event, sound included, with a reason and only a
rough place: a quarter direction and a distance band from the nearest teammate). The spec's "knowable": team
knowledge, perfect comms."""

from __future__ import annotations

import math

# How closely a signal places an enemy (the owner, 2026-10-12): a sound tells you the direction to within a 90
# degree quarter and the distance to within 5 m, so the answer is a piece of a ring around the teammate, never
# a spot or a named area. The quarters and the bands are fixed, so neither gives the exact value away.
ROUGH_BAND_M = 10.0         # the band's width: its middle is within 5 m of any distance in it


def rough_place(view, side: str, enemy: int, t: float) -> dict | None:
    """Where the signal put the enemy, as roughly as a sound does: from the nearest living teammate, one of four
    minimap directions and a distance band. The stored event names no listener, so the nearest teammate stands
    in for whoever heard it. None when either has no position at that time."""
    there = view.position(enemy, t)
    if there.get("u") is None:
        return None
    nearest = None
    for mate in view.living(side, t):
        here = view.position(mate, t)
        if here.get("u") is None:
            continue
        du, dv = there["u"] - here["u"], there["v"] - here["v"]
        metres = view.metres(math.hypot(du, dv))
        if nearest is None or metres < nearest[0]:
            nearest = (metres, mate, du, dv)
    if nearest is None:
        return None
    metres, mate, du, dv = nearest
    if abs(du) >= abs(dv):
        direction = "right" if du >= 0 else "left"
    else:
        direction = "down" if dv >= 0 else "up"         # v grows down the minimap
    low = math.floor(metres / ROUGH_BAND_M) * ROUGH_BAND_M
    return {"from": view.names.get(mate), "direction": direction, "metres_min": low, "metres_max": low + ROUGH_BAND_M}


def signals(events: dict | None, side: str, t: float, names: dict[int, str], view=None) -> list[dict] | None:
    if events is None:
        return None
    out = []
    for row in events.get(side) or []:
        if row[0] > t:
            continue
        signal = {"t": row[0], "name": names.get(int(row[1])), "reason": row[2]}
        rough = rough_place(view, side, int(row[1]), float(row[0])) if view is not None else None
        if rough is not None:
            signal["rough_place"] = rough
        out.append(signal)
    return out


def known_enemies(knew: dict, side: str, t: float, view) -> list[dict]:
    out = []
    for slot_text, runs in (knew.get(side) or {}).items():
        seen = [run for run in runs if run[0] <= t]
        if not seen:
            continue
        slot = int(slot_text)
        t0, t1, u, v = seen[-1]
        in_view = t <= t1
        if in_view:
            now = view.position(slot, t)
            if now.get("alive") and now.get("u") is not None:
                u, v = now["u"], now["v"]
        out.append({"slot": slot, "name": view.names.get(slot), "first_seen": seen[0][0],
                    "last_seen": t if in_view else t1, "in_view": in_view, "u": u, "v": v,
                    "area": view.area(u, v), "alive": view.alive(slot, t)})
    return sorted(out, key=lambda e: e["slot"])

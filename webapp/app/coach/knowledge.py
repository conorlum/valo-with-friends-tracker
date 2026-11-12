"""What a side knew of each enemy by a time, from map control's stored rows: `header["knew"]` (sightings, each
with a place) and `header["knowledge_events"]` (time, slot, reason, and optionally kind). Only found and
roughly_placed events are signals with a rough place: a quarter direction and a distance band from the nearest
teammate. Ruled-out pulses may name the device's area, never the enemy's place. Lost-track events have no place;
other events are dropped.
The spec's "knowable": team knowledge, perfect comms."""

from __future__ import annotations

import math

from app.replays import control_reasons
from app.replays.destroyed import DESTROYABLE

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


def _pulse_area(view, side: str, reason: str, t: float) -> dict | None:
    """The active own-side device's callout, without reading any player's position.

    Events store no source id. If concurrent devices leave more than one possible position, omit the area.
    """
    positions = set()
    for device in view.blob.get("util") or []:
        if device.get("k") != "ability" or DESTROYABLE.get((device.get("code"), device.get("name"))) != reason:
            continue
        owner = device.get("by")
        if owner not in view.slots or view.side(owner) != side:
            continue
        start = device.get("t")
        ends = [device[key] for key in ("t1", "gone") if device.get(key) is not None]
        if start is None or not float(start) <= t <= min(ends, default=view.t_end):
            continue
        if device.get("u") is not None and device.get("v") is not None:
            positions.add((device["u"], device["v"]))
    if len(positions) != 1:
        return None
    return view.area(*next(iter(positions)))


def signals(events: dict | None, side: str, t: float, names: dict[int, str], view=None) -> dict | None:
    if events is None:
        return None
    out = {"signals": [], "ruled_out": [], "lost_track": []}
    for row in events.get(side) or []:
        if row[0] > t:
            continue
        event_class = control_reasons.classify(row[2], row[3] if len(row) > 3 else None)
        if event_class == "other":
            continue
        signal = {"t": row[0], "name": names.get(int(row[1])), "reason": row[2]}
        if event_class in ("ruled_out", "lost_track"):
            if event_class == "ruled_out" and row[2] in ("haunt", "recon") and view is not None:
                area = _pulse_area(view, side, row[2], float(row[0]))
                if area is not None:
                    signal["area"] = area
            out[event_class].append(signal)
            continue
        signal["class"] = event_class
        rough = rough_place(view, side, int(row[1]), float(row[0])) if view is not None else None
        if rough is not None:
            signal["rough_place"] = rough
        out["signals"].append(signal)
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

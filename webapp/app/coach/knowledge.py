"""What a side knew of each enemy by a time, from map control's stored rows: `header["knew"]` (sightings, each
with a place) and `header["knowledge_events"]` (time, slot, reason, and optionally kind). Only found and
roughly_placed events are signals with a rough place: a quarter direction and a distance band from the nearest
teammate, with destroyed devices requiring a heard shot. Ruled-out pulses may name the emitting source's area,
never the enemy's place. Lost-track events have no place;
other events are dropped.
The spec's "knowable": team knowledge, perfect comms."""

from __future__ import annotations

import json
import math
from pathlib import Path

from app.control.utility import PULSE_SOURCES
from app.replays import control_reasons

# How closely a signal places an enemy (the owner, 2026-10-12): a sound tells you the direction to within a 90
# degree quarter and the distance to within 5 m, so the answer is a piece of a ring around the teammate, never
# a spot or a named area. The quarters and the bands are fixed, so neither gives the exact value away.
ROUGH_BAND_M = 10.0         # the band's width: its middle is within 5 m of any distance in it
# Read the engine's own ranges without importing the control engine into the coach.
_HEARING = json.loads((Path(__file__).parents[1] / "control" / "hearing.json").read_text(encoding="utf-8"))


def _heard_destroying_shot(view, side: str, enemy: int, t: float) -> bool:
    """A shot by the destroyer in the engine's destruction window, heard by a living teammate.

    RoundView has no nearsight/deafening state; this uses the recorded positions and gun range.
    """
    for shot in view.blob.get("util") or []:
        if shot.get("k") != "shot" or shot.get("by") != enemy or shot.get("t") is None:
            continue
        when = float(shot["t"])
        if not t - 0.5 <= when <= t + 0.05:
            continue
        there = view.position(enemy, when)
        if there.get("u") is None or there.get("v") is None:
            continue
        gun = shot.get("gun")
        ranges, default = _HEARING["guns"], _HEARING["default_gun_m"]
        reach = max(ranges.values(), default=default) if gun is None else ranges.get(gun, default)
        for mate in view.living(side, when):
            here = view.position(mate, when)
            if here.get("u") is None or here.get("v") is None:
                continue
            distance = view.metres(math.hypot(there["u"] - here["u"], there["v"] - here["v"]))
            if distance <= reach:
                return True
    return False


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
    """The own-side emitting pulse's callout, without reading any player's position.

    Events store no source id and round times to milliseconds. If matching sources disagree, omit the area.
    """
    areas = []
    for source in view.blob.get("util") or []:
        if source.get("k") != "ability" or PULSE_SOURCES.get(f"{source.get('code')}_{source.get('name')}") != reason:
            continue
        owner = source.get("by")
        if owner not in view.slots or view.side(owner) != side:
            continue
        start = source.get("t")
        if start is None or abs(float(start) - t) > 0.002:
            continue
        if source.get("u") is not None and source.get("v") is not None:
            areas.append(view.area(source["u"], source["v"]))
    if not areas or any(area != areas[0] for area in areas):
        return None
    return areas[0]


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
        placeable = view is not None and (row[2] not in ("trip_destroyed", "camera_destroyed")
                                         or _heard_destroying_shot(view, side, int(row[1]), float(row[0])))
        rough = rough_place(view, side, int(row[1]), float(row[0])) if placeable else None
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
        _, t1, u, v = seen[-1]
        in_view = t <= t1
        now = view.position(slot, t) if in_view else None
        if now and now["alive"] and now.get("u") is not None:
            u, v, area = now["u"], now["v"], now["area"]
        else:
            z = None if in_view else view.sighting_height(slot, t1)
            area = view.area(u, v, z)
        out.append({"slot": slot, "name": view.names.get(slot), "first_seen": seen[0][0],
                    "last_seen": t if in_view else t1, "in_view": in_view, "u": u, "v": v,
                    "area": area, "alive": view.alive(slot, t)})
    return sorted(out, key=lambda e: e["slot"])

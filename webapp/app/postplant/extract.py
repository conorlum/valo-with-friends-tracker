"""Worker-side post-plant evidence, using replay slots and world-z decimetres.

This records physical spike lines and positions only. The site assigns areas and
computes crossfire and value from the stored evidence.
"""

from __future__ import annotations

import math

import numpy as np

from app.control import engine, stance
from app.critutil.extract import _EPS, _alive_counts, _sides
from app.replays.format import ARENA_Z_M

STEP_S = 0.5
VERSION = 2


def _in_arena(rnd, slot, t) -> bool:
    """Use raw world metres at the nearest track sample, as the control engine does."""
    zs = rnd.track_z.get(slot)
    i = rnd._sample(slot, t) if zs is not None else None
    # Unknown z (NaN) compares false, matching the engine's nan=0 arena test.
    return i is not None and bool(zs[i] < ARENA_Z_M)


def _map_pos(rnd, slot, t):
    return None if _in_arena(rnd, slot, t) else rnd.pos(slot, t)


def spike_line(rnd, slot, t, x_px, y_px) -> bool:
    """A physical line to the spike through walls and current smokes, without FOV or blinds."""
    pos = _map_pos(rnd, slot, t)
    if pos is None:
        return False
    boxed = stance.boxed_stance(rnd, slot, t)
    ex, ey = pos[:2] if boxed is None else boxed.eye_px
    sv = () if boxed is None else boxed.see_over
    st = ()
    if len(rnd.geo.top_nodes):
        tops = np.flatnonzero(rnd.geo.top_leaf == rnd.geo.space_at_px(x_px, y_px))
        if len(tops):
            st = (int(rnd.geo.top_piece[tops[0]]),)
    return bool(engine.los(rnd.geo, (ex, ey, None), (x_px, y_px, None),
                           rnd.smokes_at(t), see_over=(sv, st)))


def _world_z_dm(rnd, slot, t) -> int | None:
    """Use the same nearest track sample as pos, independent of map height assets."""
    zs = rnd.track_z.get(slot)
    i = rnd._sample(slot, t) if zs is not None else None
    if i is None or not math.isfinite(zs[i]):
        return None
    return round(float(zs[i]) * 10)


def _uv(pos) -> tuple[int | None, int | None]:
    if pos is None:
        return None, None
    return round(pos[0] * 10000 / engine.PX), round(pos[1] * 10000 / engine.PX)


def round_postplant(rnd: engine.RoundInputs, blob: dict) -> dict:
    """Sample alive, located players and kills from the first plant through round decision."""
    sides = _sides(rnd)
    end = blob.get("t_decided")
    if end is None:
        end = blob["t_end"]
    spike = next((e for e in blob.get("util") or ()
                  if e.get("k") == "ability" and e.get("kind") == "Bomb"), None)
    plant = {key: spike.get(key) for key in ("t", "u", "v", "by")} if spike is not None else None
    out = {
        "v": VERSION, "plant": plant, "end": end, "step": STEP_S,
        "sides": {str(slot): side for slot, side in sides.items()},
        "sides_known": all(side in ("attack", "defense") for side in sides.values()),
        "samples": [], "kills": [],
    }
    if plant is None or plant["t"] >= end:
        return out

    plant_t = plant["t"]
    sx, sy = plant["u"] * engine.PX / 10000, plant["v"] * engine.PX / 10000
    k = 0
    while (t := plant_t + k * STEP_S) <= end:
        for slot in sorted(sides):
            if not rnd.alive(slot, t):
                continue
            pos = _map_pos(rnd, slot, t)
            if pos is None:
                continue
            u, v = _uv(pos)
            out["samples"].append([round(t, 2), slot, u, v, _world_z_dm(rnd, slot, t),
                                   int(spike_line(rnd, slot, t, sx, sy))])
        k += 1

    for event in blob.get("kills") or ():
        t = event["t"]
        if not plant_t <= t <= end:
            continue
        killer, victim = event.get("killer"), event.get("victim")
        pos = _map_pos(rnd, killer, t)
        victim_arena = _in_arena(rnd, victim, t)
        ku, kv = _uv(pos)
        before = _alive_counts(rnd, sides, t - _EPS)
        out["kills"].append({
            "t": t, "killer": killer, "victim": victim,
            "k_side": sides.get(killer), "v_side": sides.get(victim),
            "before": [before["attack"], before["defense"]],
            "ku": ku, "kv": kv, "kz": None if pos is None else _world_z_dm(rnd, killer, t),
            "vu": None if victim_arena else event.get("u"),
            "vv": None if victim_arena else event.get("v"),
            "vz": None if victim_arena else _world_z_dm(rnd, victim, t),
            "k_line": None if pos is None else int(spike_line(rnd, killer, t, sx, sy)),
        })
    return out

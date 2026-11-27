"""Aim events: where a player's crosshair was when each enemy of a fight first came into line of sight, and how long
it took to get on them (crosshair placement). Pure: reads one round's `RoundInputs` and blob, writes nothing.

One event per fight of a player and opponent in it (R1): the first sample from SIGHT_LOOK_S before the fight to its
end where both are alive and `peeks.line_clear` holds (walls, smokes, boxed stances; no view cone, no flash check),
refined by bisection to the blob's native step. The modules `fights` and `peeks` are used by attribute, never by
name: peeks imports fights at its top, and fights will call this module.
"""

from __future__ import annotations

import math

import numpy as np

from app.control import engine
from app.moments import fights, peeks
from app.moments.constants import (AIM_BEHIND_DEG, AIM_LADDER_DEG, AIM_PROBE_S, AIM_TURN_DEG, AIM_TURN_WINDOW_S,
                                   SIGHT_LOOK_S, SIGHT_STEP_S)
from app.replays import format as fmt
from app.replays import player_state

_EPS = 1e-9
_BLIND_KINDS = ("flash", "nearsight")


def fine_yaw(blob: dict) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """slot -> (sample times, fine yaw in degrees), the times built as `RoundInputs` builds its track times. A slot
    with any segment lacking a valid `yq` is absent: its yaw is the whole-degree track (D13)."""
    hz = blob["hz"]
    out = {}
    for s, segs in (blob.get("tracks") or {}).items():
        parts = []
        for g in segs:
            deg = fmt.fine_yaw_deg(g)
            if deg is None:
                parts = None
                break
            parts.append((g["t0"] + np.arange(len(g["u"])) / hz, np.asarray(deg, float)))
        if parts:
            out[int(s)] = (np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts]))
    return out


def yaw_at(rnd, fine: dict, slot: int, t: float):
    """(yaw in degrees, whether it is the fine channel) at the sample `rnd.pos` uses at t; None without a sample."""
    j = rnd._sample(slot, t)
    if j is None:
        return None
    if slot in fine:
        return float(fine[slot][1][j]) % 360, True
    return float(rnd.tracks[slot][3][j]), False


def _clear(rnd, viewer: int, target: int, t: float) -> bool:
    return bool(peeks.line_clear(rnd, viewer, target, t))


def first_clear(rnd, viewer: int, target: int, t_lo: float, t1: float):
    """(t, in_view_at_start) of the first sample from t_lo, step SIGHT_STEP_S, up to t1, where the line is clear,
    refined by bisection between the last blocked and the first clear sample down to one native step; (None, False)
    when none is."""
    native = 1 / rnd.hz
    k = 0
    while t_lo + k * SIGHT_STEP_S <= t1 + _EPS:
        t = t_lo + k * SIGHT_STEP_S
        if _clear(rnd, viewer, target, t):
            if k == 0:
                return t, True
            lo, hi = t - SIGHT_STEP_S, t
            while hi - lo > native + _EPS:
                mid = (lo + hi) / 2
                if _clear(rnd, viewer, target, mid):
                    hi = mid
                else:
                    lo = mid
            return hi, False
        k += 1
    return None, False


def _arc(a: float, b: float) -> float:
    """The shortest signed angle from b to a, degrees."""
    return (a - b + 180) % 360 - 180


def error(rnd, fine: dict, viewer: int, target: int, t: float):
    """(degrees between the viewer's yaw and the bearing to the target, fine?) at t; None without both positions
    and a yaw sample."""
    p, q = rnd.pos(viewer, t), rnd.pos(target, t)
    y = yaw_at(rnd, fine, viewer, t)
    if p is None or q is None or y is None:
        return None
    bearing = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
    return abs(_arc(bearing, y[0])), y[1]


def impaired(rnd, blob: dict, viewer: int, t: float) -> bool:
    """A flash or nearsight hit on the viewer covering t, or a status on them running through t, cast by the other
    team. A blind with no known duration covers only its own instant."""
    mine = rnd.team.get(viewer)
    for row in blob.get("util") or ():
        k, by = row.get("k"), row.get("by")
        if by is None or rnd.team.get(by) in (None, mine):
            continue
        if k in _BLIND_KINDS:
            for hit in row.get("hits") or ():
                if hit[0] == viewer and hit[1] <= t + _EPS and t <= hit[1] + (hit[2] or 0.0) + _EPS:
                    return True
        elif k == "status" and row.get("target") == viewer:
            end = row["t"] if row.get("t1") is None else row["t1"]
            if row["t"] <= t + _EPS and t <= end + _EPS:
                return True
    return False


def classify(first: bool, impaired_: bool, err: float, turn: float | None) -> str:
    """R3, first match wins."""
    if first:
        return "in_view_at_start"
    if impaired_:
        return "impaired"
    if err > AIM_BEHIND_DEG:
        return "behind"
    if turn is not None and abs(turn) > AIM_TURN_DEG:
        return "turning"
    return "ok"


def _life_index(rnd, slot: int, t: float):
    return next((i for i, (a, b) in enumerate(rnd.lives.get(slot, [])) if a <= t < b), None)


def _hp(rnd, blob: dict, slot: int, t: float):
    life = _life_index(rnd, slot, t)
    if life is None:
        return None
    v = player_state.vitals_at(blob.get("player_state"), slot, t, life=life)
    if v is None or v.get("hp") is None:
        return None
    return int(v["hp"]) + int(v.get("sh") or 0)


def _round(x, n):
    return None if x is None else round(float(x), n)


def _event(rnd, blob: dict, fine: dict, fight: dict, opp: int):
    slot = fight["slot"]
    t1 = float(fight["t1"])
    t, first = first_clear(rnd, slot, opp, float(fight["t0"]) - SIGHT_LOOK_S, t1)
    if t is None:
        return None
    here = error(rnd, fine, slot, opp, t)
    if here is None:
        return None
    err, used_fine = here
    t_end = min(t1, rnd.life_end(slot, t), rnd.life_end(opp, t))
    probes = []
    for dt in AIM_PROBE_S:
        e = error(rnd, fine, slot, opp, t + dt) if t + dt <= t_end + _EPS else None
        probes.append(_round(None if e is None else e[0], 2))
    on = [None] * len(AIM_LADDER_DEG)
    step, k = 1 / rnd.hz, 0
    while t + k * step <= t_end + _EPS and any(v is None for v in on):
        e = error(rnd, fine, slot, opp, t + k * step)
        if e is not None:
            for j, x in enumerate(AIM_LADDER_DEG):
                if on[j] is None and e[0] <= x:
                    on[j] = round(k * step, 3)
        k += 1
    before = yaw_at(rnd, fine, slot, t - AIM_TURN_WINDOW_S)
    now = yaw_at(rnd, fine, slot, t)
    turn = None if before is None or now is None else _arc(now[0], before[0])
    cls = classify(first, impaired(rnd, blob, slot, t), err, turn)
    shots = [float(r["t"]) for r in blob.get("util") or ()
             if r.get("k") == "shot" and r.get("by") == slot and t - _EPS <= r["t"] <= t_end + _EPS]
    hits = [float(r["t"]) for r in blob.get("util") or ()
            if r.get("k") == "damage" and r.get("src") == "gun" and r.get("by") == slot and r.get("target") == opp
            and t - _EPS <= r["t"] <= t_end + _EPS]
    killed = any(kl["victim"] == opp for kl in fight.get("kills") or ())
    died = fight.get("died")
    outcome = "won" if killed else "lost" if died and died.get("killer") == opp else "none"
    return {
        "slot": slot, "i": fight["i"], "opponent": opp, "t": round(t, 3), "cls": cls,
        "err": _round(err, 2), "err_125": probes[0], "err_250": probes[1],
        "d_m": _round(fights._dist_m(rnd, slot, opp, t), 1), "fine": bool(used_fine),
        "hold": bool(peeks.still(rnd, slot, t)), "opener": peeks.opener(rnd, slot, opp, t),
        "gun_class": (fight.get("moving") or {}).get("gun_class"),
        "hp": _hp(rnd, blob, slot, t), "enemy_hp": _hp(rnd, blob, opp, t),
        "outcome": outcome, "t_end": round(t_end, 3), "on": on,
        "shot": _round(min(shots) - t, 3) if shots else None,
        "dmg": _round(min(hits) - t, 3) if hits else None,
    }


def round_aim(rnd: engine.RoundInputs, blob: dict, fights_out) -> list[dict]:
    """One event per fight in `fights_out` (the list `fights.round_moments` builds, or its dict) and opponent in it,
    in the fight's order; a pair that never had a clear line has none."""
    fights_out = fights_out["fights"] if isinstance(fights_out, dict) else fights_out
    fine = fine_yaw(blob)
    out = []
    for fight in fights_out:
        for opp in fight["opponents"]:
            ev = _event(rnd, blob, fine, fight, opp)
            if ev is not None:
                out.append(ev)
    return out

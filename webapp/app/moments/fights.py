"""Critical Moments: one round's fights per player (design: docs/superpowers/specs/2026-10-17-critical-moments-design.md).

Pure: imports only app.control and app.moments.constants. A fight is a run of one player's damage and kill events
against the other team with no gap longer than FIGHT_GAP_S. Slots only, never names.
"""

from __future__ import annotations

import math
from bisect import bisect_left

from app.control import engine
from app.moments.constants import (DASH_EXCLUDE_S, FIGHT_GAP_S, GUN_CLASS, MOVING_MPS, SHOT_LEAD_S,
                                   SPEED_MAX_SPAN_S, TRADE_RANGE_M, TRADE_WINDOW_S, UNTAGGED_CLASSES)

VERSION = 1
_EPS = 0.01        # alive counts and positions are read this long before a fight's first event


def _r2(x):
    return None if x is None else round(float(x), 2)


def _events(rnd, blob: dict, slot: int) -> list[tuple]:
    """(t, kind, by, other) for every cross-team damage or kill row touching `slot`, up to t_decided, sorted by t."""
    team, out = rnd.team, []
    mine = team.get(slot)
    for row in blob.get("util") or ():
        if row.get("k") != "damage":
            continue
        by, target = row.get("by"), row.get("target")
        if by is None or target is None or slot not in (by, target):
            continue
        other = target if by == slot else by
        if team.get(other) is None or team.get(other) == mine:
            continue
        out.append((float(row["t"]), "damage", by, other))
    for t, killer, victim in rnd.kills:
        if slot not in (killer, victim):
            continue
        other = victim if killer == slot else killer
        if team.get(other) is None or team.get(other) == mine:
            continue
        out.append((t, "kill", killer, other))
    out = [e for e in out if e[0] <= rnd.t_decided]
    out.sort(key=lambda e: (e[0], e[1] != "damage"))
    return out


def _split(events: list[tuple]) -> list[list[tuple]]:
    groups: list[list[tuple]] = []
    for e in events:
        if groups and e[0] - groups[-1][-1][0] <= FIGHT_GAP_S:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


def _man(rnd, slot: int, t: float) -> list[int]:
    mine = rnd.team[slot]
    own = sum(1 for s, g in rnd.team.items() if g == mine and rnd.alive(s, t))
    enemy = sum(1 for s, g in rnd.team.items() if g != mine and rnd.alive(s, t))
    return [own, enemy]


def _dist_m(rnd, a: int, b: int, t: float):
    pa, pb = rnd.pos(a, t), rnd.pos(b, t)
    if pa is None or pb is None:
        return None
    return math.hypot(pa[0] - pb[0], pa[1] - pb[1]) * rnd.geo.m_per_px


def _trade(rnd, slot: int, died: dict | None, kills: list[dict]) -> dict:
    """Same rule as app/coach/detectors.py:36-44: the killer later killed by a teammate of the victim, 0 <= dt <=
    TRADE_WINDOW_S, kills only up to t_decided."""
    mates = {s for s, g in rnd.team.items() if g == rnd.team[slot] and s != slot}
    live = [k for k in rnd.kills if k[0] <= rnd.t_decided]
    out = {"died": None, "by": None, "after_s": None, "avenged": []}
    if died is not None:
        out["died"] = "not_traded"
        if died["killer"] is not None and died["killer"] != slot:
            for t2, killer2, victim2 in live:
                if victim2 == died["killer"] and killer2 in mates and 0 <= t2 - died["t"] <= TRADE_WINDOW_S:
                    out.update(died="traded", by=killer2, after_s=_r2(t2 - died["t"]))
                    break
    for k in kills:                         # a victim of this fight who had killed a teammate shortly before
        for t2, killer2, victim2 in live:
            if killer2 == k["victim"] and victim2 in mates and 0 <= k["t"] - t2 <= TRADE_WINDOW_S:
                out["avenged"].append({"victim": k["victim"], "teammate": victim2})
                break
    return out


def _mates(rnd, blob: dict, slot: int, t0: float, t1: float, opponents: list[int]) -> dict:
    before = t0 - _EPS
    mine = rnd.team[slot]
    alive = [s for s, g in rnd.team.items() if g == mine and s != slot and rnd.alive(s, before)]
    res = {"state": None, "nearest_m": None, "nearest_slot": None, "with_slot": None, "with_m": None}
    if not alive:
        res["state"] = "none"
        return res
    dists = {s: _dist_m(rnd, slot, s, before) for s in alive}
    dists = {s: d for s, d in dists.items() if d is not None}
    if not dists:
        return res
    near = min(dists, key=lambda s: (dists[s], s))
    res.update(nearest_m=round(dists[near], 1), nearest_slot=near)
    fired = {row["by"] for row in blob.get("util") or ()
             if row.get("k") == "damage" and row.get("target") in opponents and t0 <= row["t"] <= t1}
    firing = [s for s in dists if dists[s] <= TRADE_RANGE_M and s in fired]
    if firing:
        w = min(firing, key=lambda s: (dists[s], s))
        res.update(state="with", with_slot=w, with_m=round(dists[w], 1))
    elif dists[near] > TRADE_RANGE_M:
        res["state"] = "alone"
    return res


def _speed(rnd, slot: int, t: float):
    """m/s from the 16 Hz samples either side of t; None when either is missing or they span too long."""
    tr = rnd.tracks.get(slot)
    if tr is None:
        return None
    ts = tr[0]
    i = int(bisect_left(ts, t))
    a, b = i - 1, i + 1
    if a < 0 or b >= len(ts) or ts[b] - ts[a] > SPEED_MAX_SPAN_S:
        return None
    dist = math.hypot(float(tr[1][b] - tr[1][a]), float(tr[2][b] - tr[2][a])) * engine.PX / 10000
    return dist * rnd.geo.m_per_px / float(ts[b] - ts[a])


def _moving(rnd, blob: dict, slot: int, t0: float, t1: float) -> tuple[dict, bool]:
    util = blob.get("util") or ()
    shots = sorted((row for row in util if row.get("k") == "shot" and row.get("by") == slot
                    and t0 - SHOT_LEAD_S <= row["t"] <= t1), key=lambda r: r["t"])
    casts = [row["t"] for row in util if row.get("k") == "cast" and row.get("by") == slot] \
        if blob.get("movement_casts") else []
    speeds = []
    for row in shots:
        if any(abs(row["t"] - c) < DASH_EXCLUDE_S for c in casts):
            continue
        v = _speed(rnd, slot, row["t"])
        if v is not None:
            speeds.append(v)
    counts: dict[str, int] = {}
    for row in shots:
        c = GUN_CLASS.get(row.get("gun"), "other")
        counts[c] = counts.get(c, 0) + 1
    gun_class = None
    if counts:   # max keeps the first key on a tie, and keys are in order of each class's earliest shot
        gun_class = max(counts, key=counts.get)
    above = sum(1 for v in speeds if v > MOVING_MPS)
    tagged = above >= 2 and above * 2 >= len(speeds) and gun_class not in UNTAGGED_CLASSES
    return {"measured": len(speeds), "above": above, "first_mps": _r2(speeds[0]) if speeds else None,
            "tagged": tagged, "gun_class": gun_class}, tagged


def _fight(rnd, blob: dict, slot: int, i: int, group: list[tuple]) -> dict:
    t0, t1 = group[0][0], group[-1][0]
    opponents = sorted({e[3] for e in group})
    kills = [{"victim": e[3], "t": _r2(e[0])} for e in group if e[1] == "kill" and e[2] == slot]
    deaths = [e for e in group if e[1] == "kill" and e[2] != slot]
    died = {"t": _r2(deaths[0][0]), "killer": deaths[0][2]} if deaths else None
    trade = _trade(rnd, slot, {"t": deaths[0][0], "killer": deaths[0][2]} if deaths else None,
                   [{"victim": k["victim"], "t": k["t"]} for k in kills])
    mates = _mates(rnd, blob, slot, t0, t1, opponents)
    tags = []
    if mates["state"] == "alone":
        tags.append("alone")
    elif mates["state"] == "with":
        tags.append("with_teammate")
    elif mates["state"] == "none":
        tags.append("no_teammate")
    if trade["died"] == "traded":
        tags.append("died_traded")
    elif trade["died"] == "not_traded":
        tags.append("died_not_traded")
    if trade["avenged"]:
        tags.append("traded_teammate")
    moving, moving_tag = _moving(rnd, blob, slot, t0, t1)
    if moving_tag:
        tags.append("moving")
    return {
        "slot": slot, "i": i, "t0": _r2(t0), "t1": _r2(t1), "opponents": opponents,
        "first": "player" if group[0][2] == slot else "opponent",
        "man": _man(rnd, slot, t0 - _EPS),
        "kills": kills, "died": died, "trade": trade,
        "moving": moving,
        "mates": mates,
        "util_on": [], "util_near": [], "smoke_near": [], "util_on_you": [], "mollied": [], "duels": [],
        "kill_detail": [], "death_detail": None, "tags": tags,
    }


def round_moments(rnd: engine.RoundInputs, blob: dict, *, sight: bool = True) -> dict:
    """Every fight of the round's ten players: {"v": 1, "sight": bool, "fights": [fight, ...]}."""
    out = []
    for slot in sorted(rnd.team):
        for i, group in enumerate(_split(_events(rnd, blob, slot))):
            out.append(_fight(rnd, blob, slot, i, group))
    return {"v": VERSION, "sight": sight, "fights": out}

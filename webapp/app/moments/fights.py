"""Critical Moments: one round's fights per player (design: docs/superpowers/specs/2026-10-17-critical-moments-design.md).

Pure: imports only app.control and app.moments.constants. A fight is a run of one player's damage and kill events
against the other team with no gap longer than FIGHT_GAP_S. Slots only, never names.
"""

from __future__ import annotations

import math

from app.control import engine
from app.moments.constants import FIGHT_GAP_S, TRADE_RANGE_M, TRADE_WINDOW_S

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
    return {
        "slot": slot, "i": i, "t0": _r2(t0), "t1": _r2(t1), "opponents": opponents,
        "first": "player" if group[0][2] == slot else "opponent",
        "man": _man(rnd, slot, t0 - _EPS),
        "kills": kills, "died": died, "trade": trade,
        "moving": {"measured": 0, "above": 0, "first_mps": None, "tagged": False, "gun_class": None},
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

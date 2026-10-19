"""Critical Moments: one round's fights per player (design: docs/superpowers/specs/2026-10-17-critical-moments-design.md).

Pure: imports only app.control and app.moments.constants. A fight is a run of one player's damage and kill events
against the other team with no gap longer than FIGHT_GAP_S. Slots only, never names.
"""

from __future__ import annotations

import math
from bisect import bisect_left

from app.control import engine
from app.control.utility import _sees_point
from app.moments.constants import (ABILITY_KIND, CLEAN_MAX_MOVING, CLEAN_SPOT_TO_SHOT_S, CROSSHAIR_NEAR_DEG,
                                   CROSSHAIR_ON_DEG, DASH_EXCLUDE_S, FIGHT_GAP_S, GUN_CLASS, GUN_DAMAGE_LOOKBACK_S,
                                   HIT_KIND, MOVING_MPS, NEAR_THEM_KINDS, RUN_GAP_S, SHOT_AFTER_KILL_S, SPOT_GAP_S,
                                   SPOT_LOOKBACK_S, SPOT_TOLERANCE_S, WITH_AIM_DEG,
                                   SHOT_LEAD_S, SIGHT_LOOK_S, SIGHT_STEP_S, SPEED_MAX_SPAN_S, TRADE_RANGE_M,
                                   TRADE_WINDOW_S, UNTAGGED_CLASSES, UTIL_LOOKBACK_S, UTIL_NEAR_M)

VERSION = 1
PLACED_KINDS = ("Zone", "Patch", "GameObject", "Pawn")
_EPS = 0.01        # alive counts and positions are read this long before a fight's first event


def _r2(x):
    return None if x is None else round(float(x), 2)


def _live_pos(rnd, slot: int, t: float):
    """The slot's position at t while alive, else None: a track can outlive its player (and a revived player's
    corpse is not them), so sight never lands on the dead."""
    return rnd.pos(slot, t) if rnd.alive(slot, t) else None


def _events(rnd, blob: dict, slot: int) -> list[tuple]:
    """(t, kind, by, other, end) for every cross-team damage or kill row touching `slot`, up to t_decided, sorted by
    t. A damage run's end is its t1 (clipped to t_decided); a kill's is its t."""
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
        t = float(row["t"])
        end = row.get("t1")
        out.append((t, "damage", by, other, t if end is None else min(max(float(end), t), rnd.t_decided)))
    for t, killer, victim in rnd.kills:
        if slot not in (killer, victim):
            continue
        other = victim if killer == slot else killer
        if team.get(other) is None or team.get(other) == mine:
            continue
        out.append((t, "kill", killer, other, t))
    out = [e for e in out if e[0] <= rnd.t_decided]
    out.sort(key=lambda e: (e[0], e[1] != "damage"))
    return out


def _split(events: list[tuple]) -> list[list[tuple]]:
    """Fights: a gap of more than FIGHT_GAP_S from the end of everything so far (a damage run's t1) splits them."""
    groups: list[list[tuple]] = []
    for e in events:
        if groups and e[0] - max(g[4] for g in groups[-1]) <= FIGHT_GAP_S:
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


def _aimed_at(rnd, row: dict, opponents: list[int]) -> bool:
    """The shot's line (u,v)->(u1,v1) points within WITH_AIM_DEG of a living opponent. uv and px share axes."""
    u, v, u1, v1 = row.get("u"), row.get("v"), row.get("u1"), row.get("v1")
    if None in (u, v, u1, v1) or (u1 == u and v1 == v):
        return False
    t = row["t"]
    p = rnd.pos(row["by"], t)
    if p is None:
        return False
    aim = math.atan2(v1 - v, u1 - u)
    for o in opponents:
        if not rnd.alive(o, t):
            continue
        q = rnd.pos(o, t)
        if q is None:
            continue
        bearing = math.atan2(q[1] - p[1], q[0] - p[0])
        if abs((math.degrees(aim - bearing) + 180) % 360 - 180) <= WITH_AIM_DEG:
            return True
    return False


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
             if row.get("k") == "damage" and row.get("target") in opponents
             and row["t"] <= t1 and t0 <= (row["t"] if row.get("t1") is None else max(row["t"], row["t1"]))}
    if WITH_AIM_DEG >= 0:   # A shot aimed at a living opponent counts like damage
        fired |= {row["by"] for row in blob.get("util") or ()
                  if row.get("k") == "shot" and t0 <= row["t"] <= t1 and row.get("by") in dists
                  and _aimed_at(rnd, row, opponents)}
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


def _hits(blob: dict, t0: float, t1: float):
    """(row, by, target, t, dur) for every utility row that names a target, inside [t0 - UTIL_LOOKBACK_S, t1]."""
    lo = t0 - UTIL_LOOKBACK_S
    for row in blob.get("util") or ():
        k, by = row.get("k"), row.get("by")
        if k in ("flash", "nearsight"):
            for hit in row.get("hits") or ():
                if lo <= hit[1] <= t1:
                    yield row, by, hit[0], float(hit[1]), None if hit[2] is None else float(hit[2])
        elif k in ("status", "reveal"):
            end = row.get("t1")
            end = row["t"] if end is None else end
            if row["t"] <= t1 and end >= lo:
                yield row, by, row.get("target"), float(row["t"]), _r2(end - row["t"])
        elif k == "damage" and row.get("src") == "ability" and lo <= row["t"] <= t1:
            yield row, by, row.get("target"), float(row["t"]), None


def _utility_on(rnd, blob: dict, slot: int, t0: float, t1: float, opponents: list[int]) -> tuple[list, list]:
    mine = rnd.team[slot]
    on, on_you = [], []
    for row, by, target, t, dur in _hits(blob, t0, t1):
        if by is None or rnd.team.get(by) is None or target is None:
            continue
        entry = {"by": by, "kind": row["k"], "util_kind": HIT_KIND.get(row["k"]), "name": row.get("name"),
                 "t": _r2(t), "dur": _r2(dur)}
        if rnd.team[by] == mine and target in opponents:
            on.append({**entry, "on": target})
        elif rnd.team[by] != mine and target == slot:
            on_you.append(entry)
    key = lambda e: (e["t"], e["by"])  # noqa: E731
    return sorted(on, key=key), sorted(on_you, key=key)


def _near(rnd, blob: dict, slot: int, t0: float, opponents: list[int]) -> tuple[list, list]:
    mine = rnd.team[slot]
    before = t0 - _EPS
    spots = {s: rnd.pos(s, before) for s in opponents}
    spots = {s: p for s, p in spots.items() if p is not None}
    near, smoke = [], []
    if not spots:
        return near, smoke
    for row in blob.get("util") or ():
        if row.get("k") != "ability" or row.get("kind") not in PLACED_KINDS or row.get("u") is None \
                or row.get("v") is None or rnd.team.get(row.get("by")) != mine:
            continue
        name = row.get("name")
        util_kind = ABILITY_KIND.get(name)
        group = smoke if util_kind == "smoke" else near if util_kind in NEAR_THEM_KINDS else None
        end = row.get("t1")
        if group is None or not (row["t"] <= t0 and (end is None or t0 <= end)):
            continue
        x, y = row["u"] * engine.PX / 10000, row["v"] * engine.PX / 10000
        d = {s: math.hypot(p[0] - x, p[1] - y) * rnd.geo.m_per_px for s, p in spots.items()}
        s = min(d, key=lambda k: (d[k], k))
        if d[s] <= UTIL_NEAR_M:
            group.append({"by": row["by"], "name": name, "util_kind": util_kind, "on": s, "m": round(d[s], 1)})
    return near, smoke


def _mollied(rnd, slot: int, t0: float, t1: float) -> list[dict]:
    mine = rnd.team[slot]
    out = []
    for side, z, nodes in getattr(rnd, "mollies", ()):
        if side == mine or z.t0 > t1 or z.t1 < t0 - UTIL_LOOKBACK_S:
            continue

        def inside(t):
            p = rnd.pos(slot, t)
            return None if p is None else bool((nodes == rnd.node(slot, t, p[0], p[1])).any())

        if not rnd.alive(slot, z.t0) or not inside(z.t0):
            continue
        end = min(z.t1, rnd.life_end(slot, z.t0))
        k = 1
        while z.t0 + k * SIGHT_STEP_S < end:
            t = z.t0 + k * SIGHT_STEP_S
            if inside(t) is False:
                if t < t0 - UTIL_LOOKBACK_S:    # left it before the fight's utility window: not this fight's
                    break
                out.append({"by": z.by, "name": "molly", "t_in": _r2(z.t0), "t_out": _r2(t)})
                break
            k += 1
    return out


def _off(rnd, viewer: int, target: int, t: float):
    """Horizontal angle in degrees between the viewer's yaw and the bearing to the target; None without both positions."""
    p, q = rnd.pos(viewer, t), rnd.pos(target, t)
    if p is None or q is None:
        return None
    return abs((math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) - p[2] + 180) % 360 - 180)


def _band(angle: float) -> str:
    return "on" if angle <= CROSSHAIR_ON_DEG else "near" if angle <= CROSSHAIR_NEAR_DEG else "off"


def _ttk(rnd, blob: dict, tk: float, killer: int, victim: int, kd: dict) -> None:
    """Fill spot, first_shot, ttk, clean/reason, angle and band on `kd` (the kill has gun damage)."""
    shots = sorted(float(row["t"]) for row in blob.get("util") or ()
                   if row.get("k") == "shot" and row.get("by") == killer
                   and tk - SPOT_LOOKBACK_S <= row["t"] <= tk + SHOT_AFTER_KILL_S)
    run = [shots[-1]] if shots else []
    for s in reversed(shots[:-1]):
        if run[-1] - s > RUN_GAP_S:
            break
        run.append(s)
    first_shot = run[-1] if run else None
    spot = last_seen = None
    k = 0
    while k * SIGHT_STEP_S < SPOT_LOOKBACK_S:
        t = tk - k * SIGHT_STEP_S
        p = _live_pos(rnd, victim, t)
        if p is not None and _sees_point(rnd, killer, t, p[0], p[1]):
            spot = last_seen = t
        elif last_seen is not None and last_seen - t > SPOT_GAP_S:
            break
        k += 1
    kd["spot"], kd["first_shot"] = _r2(spot), _r2(first_shot)
    kd["ttk_shot"] = None if first_shot is None else _r2(tk - first_shot)
    kd["ttk_spot"] = None if spot is None else _r2(tk - spot)
    if spot is not None:
        angle = _off(rnd, killer, victim, spot)
        if angle is not None:
            kd["angle"], kd["band"] = int(round(angle)), _band(angle)
    if spot is None:
        reason = "never_saw"
    elif first_shot is None:
        reason = "no_shots"
    elif first_shot < spot - SPOT_TOLERANCE_S:
        reason = "fired_before_spot"
    elif first_shot - spot > CLEAN_SPOT_TO_SHOT_S:
        reason = "slow_start"
    elif sum(1 for s in run if (_speed(rnd, killer, s) or 0) > MOVING_MPS) > CLEAN_MAX_MOVING:
        reason = "moving"
    else:
        reason = None
    kd["clean"], kd["reason"] = reason is None, reason


def _first_sight(rnd, viewer: int, target: int, t_lo: float, t1: float):
    """(t, angle) of the first sample from t_lo, step SIGHT_STEP_S, up to t1, where viewer sees target; else (None, None).
    A dead target is never seen (its track can outlive it); _sees_point checks the viewer is alive."""
    k = 0
    while t_lo + k * SIGHT_STEP_S <= t1:
        t = t_lo + k * SIGHT_STEP_S
        p = _live_pos(rnd, target, t)
        if p is not None and _sees_point(rnd, viewer, t, p[0], p[1]):
            angle = _off(rnd, viewer, target, t)
            return t, None if angle is None else int(round(angle)), angle
        k += 1
    return None, None, None


def _duels(rnd, slot: int, t0: float, t1: float, opponents: list[int]) -> tuple[list[dict], list[str]]:
    duels, tags = [], []
    firsts = []
    for o in opponents:
        yt, ya, yraw = _first_sight(rnd, slot, o, t0 - SIGHT_LOOK_S, t1)
        tt, ta, _ = _first_sight(rnd, o, slot, t0 - SIGHT_LOOK_S, t1)
        duels.append({"opponent": o, "you_saw_t": _r2(yt), "they_saw_t": _r2(tt), "you_angle": ya, "they_angle": ta})
        firsts.append((yt, tt, yraw))
    seen = [(min(t for t in (yt, tt) if t is not None), yt, tt, yraw) for yt, tt, yraw in firsts
            if yt is not None or tt is not None]
    if seen:
        earliest = min(e[0] for e in seen)
        top = [e for e in seen if e[0] == earliest]
        mine = any(e[1] == earliest and (e[2] is None or e[2] > earliest) for e in top)
        theirs = any(e[2] == earliest and (e[1] is None or e[1] > earliest) for e in top)
        if mine and not theirs:
            tags.append("saw_first")
        elif theirs and not mine:
            tags.append("seen_first")
    if any(tt is not None and (yt is None or tt < yt) and yt is not None and yraw is not None
           and yraw > CROSSHAIR_NEAR_DEG for yt, tt, yraw in firsts):
        tags.append("caught_off_angle")
    return duels, tags


def _kd(rnd, blob: dict, tk: float, killer: int, victim: int, sight: bool) -> dict:
    """One kill's sight record: did either side have the other in view from the first gun damage minus
    SIGHT_LOOK_S to the kill. W8 fills the fields that default here."""
    kd = {"killer": killer, "victim": victim, "t": _r2(tk), "measurable": False, "killer_saw": False,
          "victim_saw": False, "wall": False, "through_smoke": False, "spot": None, "first_shot": None,
          "ttk_shot": None, "ttk_spot": None, "clean": False, "reason": None, "angle": None, "band": None}
    if not sight:
        return kd
    gun = [row for row in blob.get("util") or ()
           if row.get("k") == "damage" and row.get("src") == "gun" and row.get("by") == killer
           and row.get("target") == victim and tk - GUN_DAMAGE_LOOKBACK_S <= row["t"] <= tk]
    if not gun:
        return kd
    _ttk(rnd, blob, tk, killer, victim, kd)
    start = min(float(row["t"]) for row in gun)
    kd["wall"] = any(row.get("wall") for row in gun)
    t_lo = start - SIGHT_LOOK_S
    samples = []
    k = 0
    while t_lo + k * SIGHT_STEP_S < tk:
        samples.append(t_lo + k * SIGHT_STEP_S)
        k += 1
    for t in samples:
        pk, pv = _live_pos(rnd, killer, t), _live_pos(rnd, victim, t)
        if not kd["killer_saw"] and pv is not None and _sees_point(rnd, killer, t, pv[0], pv[1]):
            kd["killer_saw"] = True
        if not kd["victim_saw"] and pk is not None and _sees_point(rnd, victim, t, pk[0], pk[1]):
            kd["victim_saw"] = True
    if kd["killer_saw"] or kd["victim_saw"]:
        kd["measurable"] = True
        return kd
    clear = False
    for t in samples:
        pk, pv = _live_pos(rnd, killer, t), _live_pos(rnd, victim, t)
        # engine.los with no smokes also drops ability walls and map features: "through smoke" means through
        # smoke, an ability wall or a door, so the bare line must be clear AND the line with that moment's
        # smokes blocked (neither seeing the other can also be facing, a flash or nearsight).
        if pk is None or pv is None:
            continue
        a, b = (pk[0], pk[1], None), (pv[0], pv[1], None)
        if not engine.los(rnd.geo, a, b, []):
            continue
        clear = True    # a clear bare line: who saw whom is measurable even when facing or a flash hid it
        if not engine.los(rnd.geo, a, b, rnd.smokes_at(t)):
            kd["through_smoke"] = True
            break
    kd["measurable"] = clear or kd["wall"]
    return kd


def _fight(rnd, blob: dict, slot: int, i: int, group: list[tuple], kds: dict | None = None,
           sight: bool = False) -> dict:
    t0, t1 = group[0][0], max(e[4] for e in group)
    opponents = sorted({e[3] for e in group})
    kills = [{"victim": e[3], "t": _r2(e[0])} for e in group if e[1] == "kill" and e[2] == slot]
    deaths = [e for e in group if e[1] == "kill" and e[2] != slot]
    kds = kds if kds is not None else {}
    kill_detail = [kds[(e[0], e[2], e[3])] for e in group if e[1] == "kill" and e[2] == slot]
    death_detail = kds[(deaths[0][0], deaths[0][2], slot)] if deaths else None
    died = {"t": _r2(deaths[0][0]), "killer": deaths[0][2]} if deaths else None
    trade = _trade(rnd, slot, {"t": deaths[0][0], "killer": deaths[0][2]} if deaths else None,
                   [{"victim": e[3], "t": e[0]} for e in group if e[1] == "kill" and e[2] == slot])  # unrounded
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
    util_on, util_on_you = _utility_on(rnd, blob, slot, t0, t1, opponents)
    util_near, smoke_near = _near(rnd, blob, slot, t0, opponents)
    mollied = _mollied(rnd, slot, t0, t1)
    for tag, field in (("util_on_them", util_on), ("util_near_them", util_near), ("smoke_near", smoke_near),
                       ("util_on_you", util_on_you), ("mollied", mollied)):
        if field:
            tags.append(tag)
    if death_detail and death_detail["measurable"] and not death_detail["victim_saw"]:
        tags.append("died_unseen")
    if any(k["measurable"] and not k["victim_saw"] for k in kill_detail):
        tags.append("killed_unseen")
    if any(k["through_smoke"] for k in kill_detail + ([death_detail] if death_detail else [])):
        tags.append("through_smoke")
    duels = []
    if sight:
        duels, duel_tags = _duels(rnd, slot, t0, t1, opponents)
        tags.extend(duel_tags)
    return {
        "slot": slot, "i": i, "t0": _r2(t0), "t1": _r2(t1), "opponents": opponents,
        "first": "player" if group[0][2] == slot else "opponent",
        "man": _man(rnd, slot, t0 - _EPS),
        "kills": kills, "died": died, "trade": trade,
        "moving": moving,
        "mates": mates,
        "util_on": util_on, "util_near": util_near, "smoke_near": smoke_near, "util_on_you": util_on_you,
        "mollied": mollied, "duels": duels,
        "kill_detail": kill_detail, "death_detail": death_detail, "tags": tags,
    }


def round_moments(rnd: engine.RoundInputs, blob: dict, *, sight: bool = True) -> dict:
    """Every fight of the round's ten players: {"v": 1, "sight": bool, "fights": [fight, ...]}."""
    out = []
    kds = {(t, killer, victim): _kd(rnd, blob, t, killer, victim, sight)
           for t, killer, victim in rnd.kills if t <= rnd.t_decided}
    for slot in sorted(rnd.team):
        for i, group in enumerate(_split(_events(rnd, blob, slot))):
            out.append(_fight(rnd, blob, slot, i, group, kds, sight))
    return {"v": VERSION, "sight": sight, "fights": out}

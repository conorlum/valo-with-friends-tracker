"""An ult's lift window: its active time (windows.window, with Gekko's detain) plus a per-ult tail.

`ult_segments(round_data, item, entry)` returns `(segments, mode)`, mode "round" or "window". The entry's
`ult_window` (abilities.json, a site-side field the extractor never reads) is a number (tail seconds after the
active time), "total:N" (N seconds from the start) or "round" (the use to the round's end). Never capped at 45 s.
"""

from __future__ import annotations

import math

import numpy as np

from app.services import util_values

DEFAULT_TAIL_S = 5.0
THRASH_DETAIN_S = 6.0       # the wiki's detain, from Thrash last seen
THRASH_END_S = 2.0          # the owner's: the cocoon's end, plus 2 s


MAX_ULT_L = 150
_SIDES = ("attack", "defense")


def use_items(round_data: dict) -> list:
    """The round's ult uses, one per slot, earliest first: the slot's earliest ult cast (not after the round was
    decided), else its stored `ults[slot]["use"]` with a time (given `by` and `side`)."""
    sides = round_data.get("sides") or {}
    by_slot: dict = {}
    for c in sorted((c for c in round_data.get("casts") or () if c.get("type") == "ult" and not c.get("after_decided")),
                    key=lambda c: float(c.get("t") or 0.0)):
        by_slot.setdefault(str(c.get("by")), c)
    for slot, ult in (round_data.get("ults") or {}).items():
        use = (ult or {}).get("use")
        if str(slot) in by_slot or not use or use.get("t") is None:
            continue
        by_slot[str(slot)] = {**use, "by": int(slot), "side": sides.get(str(slot))}
    return sorted(by_slot.values(), key=lambda i: float(i["t"]))


def _entry_of(item: dict) -> dict | None:
    return util_values._abilities_by_key().get(item.get("ability_id"))


def _item_side(round_data: dict, item: dict):
    return item.get("side") or (round_data.get("sides") or {}).get(str(item.get("by")))


def ult_moments(round_data: dict, model, won_side) -> dict:
    """{level-0 key: [n, swing_1..swing_150, END_sum]} over every BASE_STEP_S to t_decided, per side, for moments with
    no ult window of that side up. END = (1 if the side won else 0) - P(t). Nothing without a known winner."""
    if won_side not in _SIDES:
        return {}
    td = float(round_data.get("t_decided") or 0.0)
    items = [(_item_side(round_data, i), ult_segments(round_data, i, _entry_of(i))[0]) for i in use_items(round_data)]
    out: dict = {}
    ls = np.arange(1, MAX_ULT_L + 1, dtype=float)
    for side in _SIDES:
        times, P = util_values.swing_grid(round_data, model, side)
        up = [seg for s, segs in items if s == side for seg in segs]
        t = 0.0
        while t < td:
            if not any(a <= t < b for a, b in up):
                p = float(util_values.p_on_grid(times, P, t))
                swings = util_values.p_on_grid(times, P, np.minimum(t + ls, td)) - p
                row = np.concatenate(([1.0], swings, [(1.0 if side == won_side else 0.0) - p]))
                util_values._acc(out, util_values.levels(round_data, side, t)[0], row)
            t += util_values.BASE_STEP_S
    return out


def ult_swing(round_data: dict, model, segments: list, side: str):
    """As util_values.cast_swing_v2, capped at MAX_ULT_L seconds of on-time instead of 45. (None, 0) without segments."""
    times, P = util_values.swing_grid(round_data, model, side)
    left, total, swing = float(MAX_ULT_L), 0.0, 0.0
    for s, e in segments:
        if left <= 0:
            break
        e = min(e, s + left)
        if e <= s:
            continue
        at = util_values.start_time(round_data, s)
        start = util_values._p_at(round_data, model, side, at) if at != s else float(util_values.p_on_grid(times, P, s))
        swing += float(util_values.p_on_grid(times, P, e)) - start
        total += e - s
        left -= e - s
    if total <= 0:
        return None, 0
    return swing, max(1, min(MAX_ULT_L, int(math.floor(total + 0.5))))


def _baseline(table: dict, keys: list, idx: int, own: dict):
    for i, key in enumerate(keys):
        arr = table.get(key)
        if arr is None:
            continue
        if key in own:
            arr = arr - own[key]
        if arr[0] >= util_values.BASE_MIN_N:
            return float(arr[idx]) / arr[0], int(round(arr[0])), util_values.BASE_LEVELS[i]
    return None


def ult_lift(round_data: dict, model, item: dict, table: dict, match: dict, won_side) -> dict:
    """The use's window swing against the ult table's slot L, or for a "round" ult its END at the use against the END
    slot. `table` and `match` are expanded {level key: array}; the match's own cells come out first."""
    out = {"lift": None, "swing": None, "L": 0, "mode": "window", "base_n": 0, "level": None}
    side = _item_side(round_data, item)
    segs, mode = ult_segments(round_data, item, _entry_of(item))
    out["mode"] = mode
    if not segs or side not in _SIDES:
        return out
    at = util_values.start_time(round_data, float(segs[0][0]))
    if mode == "round":
        if won_side not in _SIDES:
            return out
        swing = (1.0 if side == won_side else 0.0) - util_values._p_at(round_data, model, side, at)
        idx = MAX_ULT_L + 1
    else:
        swing, L = ult_swing(round_data, model, segs, side)
        if swing is None:
            return out
        out["L"], idx = L, L
    out["swing"] = round(swing, 4)
    base = _baseline(table, util_values.levels(round_data, side, max(at, 0.0)), idx, match or {})
    if base is not None:
        out.update(lift=round(swing - base[0], 4), base_n=base[1], level=base[2])
    return out


def _thrash_end(item: dict) -> float | None:
    ends = [e["t1"] for e in item.get("effects") or [] if e.get("t1") is not None]
    if ends:
        return max(ends) + THRASH_END_S
    seen = (item.get("signals") or {}).get("seen_end")
    return None if seen is None else seen + THRASH_DETAIN_S + THRASH_END_S


def ult_segments(round_data: dict, item: dict, entry: dict | None) -> tuple[list, str]:
    td = float(round_data.get("t_decided") or 0.0)
    spec = (entry or {}).get("ult_window", DEFAULT_TAIL_S)
    segs = [list(s) for s in util_values.item_segments(round_data, item, "ult")]
    start = segs[0][0] if segs else item.get("t")
    if start is None:
        return [], "window"
    if spec == "round":
        return ([[start, td]] if td > start else []), "round"
    if isinstance(spec, str) and spec.startswith("total:"):
        end = min(start + float(spec.split(":", 1)[1]), td)
        return ([[start, end]] if end > start else []), "window"
    if (entry or {}).get("ability") == "Thrash" and (entry or {}).get("agent") == "Gekko":
        end = _thrash_end(item)
        if end is not None:
            segs = [[start, end]]
    if not segs:
        return [], "window"
    segs[-1][1] += float(spec)
    out = []
    for a, b in segs:
        b = min(b, td)
        if b > a:
            out.append([a, b])
    return out, "window"


# --- charges, the ranking and friend rows

MIN_USES = 5
_SHRINK_K = 5.0


def proven_costs(rounds: dict) -> dict:
    """{slot: cost}: the most points each slot held just before a use in any round of the match (as
    replay_util.unused_ults). A slot that never proved a cost is absent."""
    caps: dict = {}
    for data in rounds.values():
        for slot, u in (data.get("ults") or {}).items():
            if (u or {}).get("cap_seen"):
                caps[str(slot)] = max(caps.get(str(slot), 0), int(u["cap_seen"]))
    return caps


def charges(rounds: dict, lifts: dict, ctx: dict) -> list:
    """One match's charges: [agent, player_id, match, lift|None, cost, used]. `lifts` is {(round, slot): lift};
    `ctx` is {"match", "agents": {slot: agent}, "players": {slot: player id}}. A use is one charge (used 1, its
    lift, None when uncompared). A full ult held at the match's last round and never used since it became full is
    one wasted charge (lift 0, used 0). A slot with no proven cost has none."""
    costs = proven_costs(rounds)
    out: list = []
    order = sorted(rounds, key=int)
    for n in order:
        for item in use_items(rounds[n]):
            slot = str(item.get("by"))
            if slot not in costs:
                continue
            out.append([ctx["agents"].get(slot), ctx["players"].get(slot), ctx["match"],
                        lifts.get((int(n), slot)), costs[slot], 1])
    if order:
        for slot, cost in costs.items():
            held = False
            for n in reversed(order):
                u = ((rounds[n].get("ults") or {}).get(slot)) or {}
                if u.get("end") is None or u["end"] < cost:
                    break
                if u.get("used") or u.get("use"):
                    held = False
                    break
                held = True
            if held and (rounds[order[-1]].get("ults") or {}).get(slot):
                out.append([ctx["agents"].get(slot), ctx["players"].get(slot), ctx["match"], 0, cost, 0])
    return out


def cluster_ci(groups: list, fn, draws: int = 1000, seed: int = 0) -> tuple:
    """Percentile 95% CI of fn(rows) with whole groups (matches) resampled. A draw with no value is skipped; no usable
    draw gives (None, None)."""
    if not groups:
        return None, None
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(draws):
        pick = rng.integers(0, len(groups), len(groups))
        rows = [r for i in pick for r in groups[i]]
        v = fn(rows)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(v)
    if not vals:
        return None, None
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def _lifts(rows: list) -> list:
    return [float(c[3]) for c in rows if c[3] is not None]


def _mean_lift(rows: list):
    v = _lifts(rows)
    return sum(v) / len(v) if v else None


def _counts(rows: list) -> dict:
    return {"n_uses": sum(1 for c in rows if c[5]), "n_charges": len(_lifts(rows)),
            "n_uncompared": sum(1 for c in rows if c[3] is None), "wasted": sum(1 for c in rows if not c[5])}


def _by_match(rows: list) -> list:
    groups: dict = {}
    for c in rows:
        groups.setdefault(c[2], []).append(c)
    return list(groups.values())


def aggregate(chs: list) -> dict:
    """{agent: {n_uses, n_charges, n_uncompared, wasted, value}}: raw. value is the mean lift of the compared charges
    (a wasted charge counts as 0); None with none compared."""
    by: dict = {}
    for c in chs:
        by.setdefault(c[0], []).append(c)
    return {a: {**_counts(rows), "value": _mean_lift(rows)} for a, rows in by.items()}


def rank(chs: list, agents=None) -> dict:
    """The ranking: a row for every ult agent (zero uses included), best first, thin rows last, and the cast mean."""
    if agents is None:
        from app.critutil import abilities
        agents = sorted({e["agent"] for e in abilities.load() if e.get("type") == "ult"})
    by: dict = {}
    for c in chs:
        by.setdefault(c[0], []).append(c)
    rows = []
    for a in agents:
        mine = by.get(a, [])
        row = {"agent": a, **_counts(mine), "value": None, "lo": None, "hi": None, "diff": None}
        if row["n_uses"] >= MIN_USES and row["n_charges"]:
            row["value"] = _mean_lift(mine)
            row["lo"], row["hi"] = cluster_ci(_by_match(mine), _mean_lift)
        rows.append(row)
    vals = [r["value"] for r in rows if r["value"] is not None]
    cast_mean = sum(vals) / len(vals) if vals else None
    if cast_mean is not None:
        for r in rows:
            if r["value"] is not None:
                r["diff"] = r["value"] - cast_mean
    rows.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0.0), -r["n_uses"], str(r["agent"])))
    return {"rows": rows, "cast_mean": cast_mean}


def friend_rows(chs: list, circle) -> list:
    """Per (player id, agent) of the circle: the value shrunk toward the agent's (util_values.shrunk, k = 5), raw
    counts, a CI by match. None figures under 5 uses, with no compared lift, or with no agent value."""
    circle = set(circle)
    agent_value = {a: v["value"] for a, v in aggregate(chs).items()}
    by: dict = {}
    for c in chs:
        if c[1] in circle:
            by.setdefault((c[1], c[0]), []).append(c)
    out = []
    for (pid, agent), mine in sorted(by.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        row = {"player_id": pid, "agent": agent, **_counts(mine), "value": None, "lo": None, "hi": None}
        prior = agent_value.get(agent)
        if row["n_uses"] >= MIN_USES and row["n_charges"] and prior is not None:
            def fn(rows, prior=prior):
                v = _lifts(rows)
                return util_values.shrunk(v, prior, _SHRINK_K) if v else None
            row["value"] = fn(mine)
            row["lo"], row["hi"] = cluster_ci(_by_match(mine), fn)
        out.append(row)
    return out


# --- the orb chart


def gain_pmf(rounds_by_match: dict) -> dict:
    """{agent: {gain: share}}: points gained in a round, over the rounds where the slot started below its proven cost
    and did not use its ult. `rounds_by_match` is {match: {"rounds": {round: data}, "agents": {slot: agent}}}."""
    counts: dict = {}
    for m in rounds_by_match.values():
        rounds, agents = m.get("rounds") or {}, m.get("agents") or {}
        costs = proven_costs(rounds)
        for data in rounds.values():
            for slot, u in (data.get("ults") or {}).items():
                u = u or {}
                slot = str(slot)
                agent = agents.get(slot)
                if agent is None or slot not in costs or u.get("start") is None or u["start"] >= costs[slot]:
                    continue
                if u.get("used") or u.get("use"):
                    continue
                g = int(u.get("gained") or 0)
                d = counts.setdefault(agent, {})
                d[g] = d.get(g, 0) + 1
    return {a: {g: n / sum(d.values()) for g, n in sorted(d.items())} for a, d in counts.items()}


def rounds_left(ctx: dict) -> dict:
    """{left: share}: one count for each round n of each match, at left = R - n, R the match's full round count from
    ctx[rid]["rounds"]. The final round's 0 stays in."""
    counts: dict = {}
    for m in ctx.values():
        rounds = (m or {}).get("rounds") or {}
        R = len(rounds)
        for n in rounds:
            left = R - int(n)
            counts[left] = counts.get(left, 0) + 1
    total = sum(counts.values())
    return {left: c / total for left, c in counts.items()} if total else {}


def extra_ults(pmf: dict, cost: int, points: int, left: int) -> float:
    """Ults one more point buys by the match's end, exactly: E(left, points + 1) - E(left, points)."""
    memo: dict = {}

    def E(n: int, p: int) -> float:
        if n == 0:
            return 0.0
        key = (n, p)
        if key not in memo:
            if p >= cost:
                memo[key] = 1.0 + sum(s * E(n - 1, g) for g, s in pmf.items())
            else:
                memo[key] = sum(s * E(n - 1, min(p + g, cost)) for g, s in pmf.items())
        return memo[key]

    return E(left, points + 1) - E(left, points)


def orb_chart(ranked, pmfs: dict, costs: dict, left_dist: dict) -> dict:
    """{"max_cost", "rows": [{agent, cost, muted, cells}]}: rows in ranking order, a cell per points held 0..cost-1 =
    value x the mean of extra_ults over left_dist. No row under 5 uses, without a value, a cost or a pmf."""
    ranked_rows = ranked["rows"] if isinstance(ranked, dict) else ranked
    rows = []
    for r in ranked_rows:
        agent = r["agent"]
        cost, pmf = costs.get(agent), pmfs.get(agent)
        if r.get("value") is None or r.get("n_uses", 0) < MIN_USES or not cost or not pmf:
            continue
        cells = [r["value"] * sum(s * extra_ults(pmf, cost, p, left) for left, s in left_dist.items())
                 for p in range(cost)]
        lo, hi = r.get("lo"), r.get("hi")
        muted = lo is None or hi is None or lo <= 0 <= hi
        rows.append({"agent": agent, "cost": cost, "muted": muted, "cells": cells})
    return {"max_cost": max((r["cost"] for r in rows), default=0), "rows": rows}

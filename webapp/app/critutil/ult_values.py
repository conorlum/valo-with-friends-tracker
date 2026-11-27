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


# --- held ults and orb risk
# Both take `rounds_by_match` = {match: {"rounds": {round int: util row}, ...}} (as gain_pmf) and `ctx` = {match:
# {"map", "players": {slot: {"team", ...}}, "rounds": {n: {"winner": a side}}, "loadout": {(n, team): avg}}} with the
# same match keys. A slot's team is ctx players[slot]["team"], its side that round is the util row's sides[slot], and
# a round's winner is ctx rounds[n]["winner"] (a side, "attack" or "defense").

_PISTOL_ROUNDS = (1, 13)    # corpus.PISTOL_ROUNDS (not imported: ult_values may not import corpus)
ORB_CAPTURE_S = 2.0         # a capture is [t1 - 2 s, t1]: the orb data has no capture start (declared)
ORB_RISK_S = 3.0            # a death this long after a try ends still counts
_BUY_CATS = ((True, True), (True, False), (False, True))     # joint buy (first full, second full); other/other = reference


def _held_row(row: dict, m_ctx: dict, costs: dict, n: int, full_buy: float):
    """One round's regression row [won, ready gap, used gap, unused gap, loadout gap / 1000, side, buy x3], or None
    when the round can't be used (pistol, no winner, a player missing a start or proven cost, a team without a loadout)."""
    if n in _PISTOL_ROUNDS:
        return None
    players = m_ctx.get("players") or {}
    winner = ((m_ctx.get("rounds") or {}).get(n) or {}).get("winner")
    teams = sorted({(p or {}).get("team") for p in players.values()} - {None})
    if winner not in _SIDES or len(teams) != 2:
        return None
    ults, sides = row.get("ults") or {}, row.get("sides") or {}
    ready = {t: 0 for t in teams}
    used = {t: 0 for t in teams}
    for slot, p in players.items():
        slot = str(slot)
        u = ults.get(slot) or {}
        if u.get("start") is None or slot not in costs or sides.get(slot) not in _SIDES:
            return None
        if u["start"] >= costs[slot]:
            t = p["team"]
            ready[t] += 1
            used[t] += 1 if (u.get("used") or u.get("use")) else 0
    loads = [(m_ctx.get("loadout") or {}).get((n, t)) for t in teams]
    if any(v is None for v in loads):
        return None
    first, second = teams
    first_slot = next(str(s) for s, p in players.items() if p["team"] == first)
    side = sides[first_slot]
    won = 1.0 if side == winner else 0.0
    ready_gap = ready[first] - ready[second]
    used_gap = used[first] - used[second]
    unused_gap = (ready[first] - used[first]) - (ready[second] - used[second])
    cat = (loads[0] >= full_buy, loads[1] >= full_buy)
    return [won, ready_gap, used_gap, unused_gap, (loads[0] - loads[1]) / 1000.0, 1.0 if side == "attack" else -1.0,
            *[1.0 if cat == c else 0.0 for c in _BUY_CATS]]


def _solve(xtx, xty):
    return np.linalg.lstsq(xtx, xty, rcond=None)[0]


def held_ults(rounds_by_match: dict, ctx: dict, full_buy: float = 4200) -> dict:
    """{"rounds", "matches", "any", "used", "unused"}: the change in the first team's round-win chance per extra ready
    ult it holds at the round's start (pp), same match (each match's mean removed), loadout gap and joint buy category.
    "any" regresses on the ready gap; "used"/"unused" split it by whether the ult was used that round. Each is
    {pp, lo, hi}; a 95% CI by whole-match bootstrap (1000 draws, seed 0)."""
    cols_any = [1, 4, 5, 6, 7, 8]
    cols_split = [2, 3, 4, 5, 6, 7, 8]
    per_match = []
    for mk, m in rounds_by_match.items():
        m_ctx = (ctx or {}).get(mk) or {}
        urows = (m or {}).get("rounds") or {}
        costs = proven_costs(urows)
        rows = [r for n, row in sorted(urows.items(), key=lambda kv: int(kv[0]))
                if (r := _held_row(row, m_ctx, costs, int(n), full_buy)) is not None]
        if rows:
            a = np.array(rows, dtype=float)
            per_match.append(a - a.mean(axis=0))    # the match's own mean removed from y and every column
    out = {"rounds": sum(len(a) for a in per_match), "matches": len(per_match)}
    nulls = {"pp": None, "lo": None, "hi": None}
    designs = {"any": (cols_any, 0), "used": (cols_split, 0), "unused": (cols_split, 1)}
    if not per_match:
        return out | {k: dict(nulls) for k in designs}
    sums = {}
    for name, cols in (("any", cols_any), ("split", cols_split)):
        sums[name] = [(a[:, cols].T @ a[:, cols], a[:, cols].T @ a[:, 0]) for a in per_match]
    for key, (cols, idx) in designs.items():
        parts = sums["any" if key == "any" else "split"]

        def fn(rows, idx=idx):
            return float(_solve(sum(r[0] for r in rows), sum(r[1] for r in rows))[idx]) * 100.0

        lo, hi = cluster_ci([[p] for p in parts], fn)
        out[key] = {"pp": fn(parts, idx), "lo": lo, "hi": hi}
    return out


def _intervals(row: dict) -> list:
    """[(slot, start, end)] of the row's orb tries, every interval cut at t_decided (one whose start is past it is
    dropped: the round was over)."""
    td = row.get("t_decided")
    out = []
    for orb in row.get("orbs") or ():
        if orb.get("t1") is not None and orb.get("slot") is not None:
            out.append((str(orb["slot"]), orb["t1"] - ORB_CAPTURE_S, orb["t1"]))
        for t in orb.get("tries") or ():
            if len(t) < 3 or t[0] is None or t[2] is None:
                continue
            end = t[1] if t[1] is not None else td
            out.append((str(t[2]), t[0], end))
    cut = []
    for slot, a, b in out:
        if td is not None:
            if a > td:
                continue
            b = td if b is None else min(b, td)
        if b is None:
            continue
        cut.append((slot, a, b))
    return cut


def orb_risk(rounds_by_match: dict, ctx: dict, model) -> dict:
    """{"overall": {...}, "maps": [{"map", ...}]}, each {tries, deaths, cost_pp, lo, hi}. Every capture and let-go try
    is one try; the capturer's death inside it or within ORB_RISK_S after it counts once, at the signed change in
    that player's side's win chance (minus util_values.swings of the kill). cost_pp is the mean per try."""
    by_map: dict = {}
    for mk, m in rounds_by_match.items():
        map_name = ((ctx or {}).get(mk) or {}).get("map")
        costs, deaths = [], 0
        for _n, row in sorted(((m or {}).get("rounds") or {}).items(), key=lambda kv: int(kv[0])):
            tries = _intervals(row)
            if not tries:
                continue
            sw = util_values.swings(row, model)
            td = row.get("t_decided")
            kills = sorted((k for k in row.get("kills") or () if td is None or k["t"] <= td), key=lambda k: k["t"])
            for slot, a, b in tries:
                hit = next((k for k in kills if str(k["victim"]) == slot and a <= k["t"] <= b + ORB_RISK_S), None)
                if hit is None:
                    costs.append(0.0)
                else:
                    deaths += 1
                    costs.append(-sw.get(util_values._kill_key(hit["t"], hit["killer"], hit["victim"]), 0.0))
        if costs:
            by_map.setdefault(map_name, []).append((costs, deaths))

    def summary(groups):
        cost_groups = [g[0] for g in groups]
        tries = sum(len(c) for c in cost_groups)
        if not tries:
            return {"tries": 0, "deaths": 0, "cost_pp": None, "lo": None, "hi": None}

        def mean_pp(rows):
            flat = [x for r in rows for x in r]
            return 100.0 * sum(flat) / len(flat) if flat else None

        lo, hi = cluster_ci([[c] for c in cost_groups], mean_pp)
        return {"tries": tries, "deaths": sum(g[1] for g in groups), "cost_pp": mean_pp(cost_groups), "lo": lo, "hi": hi}

    everything = [g for gs in by_map.values() for g in gs]
    maps = [{"map": name, **summary(gs)} for name, gs in sorted(by_map.items(), key=lambda kv: str(kv[0]))]
    return {"overall": summary(everything), "maps": maps}


# --- the whole block

PLACES = 4


def _clean(x):
    """JSON-ready: numpy scalars become Python ones, floats are rounded to PLACES, a non-finite float is None."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return round(float(x), PLACES) if math.isfinite(float(x)) else None
    if isinstance(x, dict):
        return {(k if isinstance(k, str) else str(k)): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _winner(m_ctx: dict, n):
    rounds = (m_ctx or {}).get("rounds") or {}
    row = rounds.get(n)
    if row is None:
        try:
            row = rounds.get(int(n))
        except (TypeError, ValueError):
            row = None
    return (row or {}).get("winner")


def _add_cells(total: dict, cells: dict) -> None:
    for key, arr in cells.items():
        total[key] = total[key] + arr if key in total else np.array(arr, dtype=float)


def _agent_costs(rounds_by_match: dict) -> dict:
    """{agent: cost}: the cost proven most often over the slots that played it (ties go to the higher cost)."""
    seen: dict = {}
    for m in rounds_by_match.values():
        for slot, cost in proven_costs(m["rounds"]).items():
            agent = m["agents"].get(slot)
            if agent is not None:
                d = seen.setdefault(agent, {})
                d[cost] = d.get(cost, 0) + 1
    return {a: max(d, key=lambda c: (d[c], c)) for a, d in seen.items()}


def build(stored: dict, ctx: dict, model, full_buy: float) -> dict:
    """{"ranking", "charges", "orbs", "held", "risk"} for a corpus. `stored` is {replay id: {round: row}} with each row
    prepared as corpus._round_data does (clock_offset, on_known); `ctx` is the replay context. A match is keyed by its
    `match_id`; when several replays hold one match the lowest replay id stands for it. Every figure is rounded to
    four places and the result is JSON-encodable (`charges` is a list of [agent, player, match, lift, cost, used])."""
    matches: dict = {}
    for rid in sorted(stored):
        c = (ctx or {}).get(rid)
        if c is None:
            continue
        key = c.get("match_id", rid)
        if key in matches:
            continue
        players = c.get("players") or {}
        matches[key] = {"rounds": stored[rid], "ctx": c,
                        "agents": {str(s): (p or {}).get("agent") for s, p in players.items()},
                        "players": {str(s): (p or {}).get("player_id") for s, p in players.items()}}
    # the ult table: every match's moments with no ult of that side up, each match kept apart to be left out of its own
    per_match: dict = {}
    total: dict = {}
    for key, m in matches.items():
        cells: dict = {}
        for n, row in m["rounds"].items():
            for k, arr in ult_moments(row, model, _winner(m["ctx"], n)).items():
                util_values._acc(cells, k, arr)
        per_match[key] = util_values.expand(cells)
        _add_cells(total, per_match[key])
    all_charges: list = []
    for key, m in matches.items():
        lifts: dict = {}
        for n, row in m["rounds"].items():
            won = _winner(m["ctx"], n)
            for item in use_items(row):
                lifts[(int(n), str(item.get("by")))] = ult_lift(row, model, item, total, per_match[key], won)["lift"]
        all_charges += charges(m["rounds"], lifts, {"match": key, "agents": m["agents"], "players": m["players"]})
    ranked = rank(all_charges)
    by_match = {k: {"rounds": m["rounds"], "agents": m["agents"]} for k, m in matches.items()}
    ctx_by_match = {k: m["ctx"] for k, m in matches.items()}
    orbs = orb_chart(ranked, gain_pmf(by_match), _agent_costs(by_match), rounds_left(ctx_by_match))
    return _clean({"ranking": ranked, "charges": all_charges, "orbs": orbs,
                   "held": held_ults(by_match, ctx_by_match, full_buy), "risk": orb_risk(by_match, ctx_by_match, model)})

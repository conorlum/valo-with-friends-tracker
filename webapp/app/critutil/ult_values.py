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

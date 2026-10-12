"""Player conditions as `[t0, t1)` intervals (docs/superpowers/specs/2026-10-06-replay-player-state-design.md section 2;
the plan's P02 step 3). Stdlib only: the upload worker copies app/replays. replay.js `normalizeConditions` mirrors
this exactly, with the same POLICY (tests/replays/test_replay_viewer.py runs both on one fixture).

Input: a round's `util` rows, its `alive` lives and its end. Output: {slot: [{"c", "t0", "t1", "est", "src"}]}.

- A `flash` hit `[slot, t, dur]` is `blinded`, a `nearsight` hit `nearsighted`, from the hit's own `t` (not the
  cast). A positive `dur` is a confirmed interval; zero (or negative) gives no interval; a null `dur` uses the
  fallback below and is estimated (`est`). A row with no `hits` (condensed before revision 10) gives each target an
  estimated interval from the explosion (the thrower's ability row whose `thrown.t0` is the cast, else the cast
  plus FLASH_FUSE_S), or for a nearsight from the cast.
- A `status` row is its `status` from `t` to `t1`; `t1 <= t` gives none; no `t1` is estimated (`status_default_s`).
- Each interval is cut at the end of the life it starts in (a revived player doesn't keep an earlier life's flash;
  one starting while dead gives none) and at the round end.
- Overlapping or touching intervals of one condition on one slot merge for display, keeping every source in `src`
  (`{"k", "by", "ability", "t0", "t1", "est"}`); a merge with any estimated part is estimated.

The fallbacks are the engine's (app/control/engine.py imports them from here, so the engine and the browser can't
drift). Veto's immunity while ulting isn't applied here: the engine filters those hits itself."""

from __future__ import annotations

import math

# Placeholders for blobs from before revision 10 and hits with no duration (the control plan: "Inputs the blob
# lacks"). A flash row's `t` is the cast, not the hit.
FLASH_FULL_S = {"phoenix": 1.5, "yoru": 1.5, "breach": 2.25, "kayo": 2.25, "skye": 2.25}
FLASH_DEFAULT_S = 1.5
FLASH_FUSE_S = 0.5
NEARSIGHT_S = {"omen_paranoia": 2.0}
NEARSIGHT_DEFAULT_S = 1.0      # also a hit with no configured duration (Reyna's Leer)
STATUS_DEFAULT_S = 1.0         # a status row with no end (extras.py STATUS_PING_MS; replay.js statusesAt)

POLICY = {"flash_full_s": FLASH_FULL_S, "flash_default_s": FLASH_DEFAULT_S, "flash_fuse_s": FLASH_FUSE_S,
          "nearsight_s": NEARSIGHT_S, "nearsight_default_s": NEARSIGHT_DEFAULT_S,
          "status_default_s": STATUS_DEFAULT_S}

CONDITION = {"flash": "blinded", "nearsight": "nearsighted"}


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _slot(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9 else None


def flash_fallback_s(ability) -> float:
    return FLASH_FULL_S.get((ability if isinstance(ability, str) else "").split("_")[0], FLASH_DEFAULT_S)


def nearsight_fallback_s(ability) -> float:
    return NEARSIGHT_S.get(ability, NEARSIGHT_DEFAULT_S) if isinstance(ability, str) else NEARSIGHT_DEFAULT_S


def _same_by(a, b) -> bool:
    """The flash row's thrower is the ability row's: both absent, or the same non-boolean value."""
    if a is None or b is None:
        return a is None and b is None
    return not isinstance(a, bool) and not isinstance(b, bool) and a == b


def _raw(util: list) -> list[tuple[int, str, float, float, bool, dict]]:
    """(slot, condition, t0, t1, est, row) per hit or status, uncut."""
    out = []
    for e in util or []:
        if not isinstance(e, dict):
            continue
        k = e.get("k")
        if k in CONDITION:
            hits = e.get("hits")
            if isinstance(hits, list):
                for hit in hits:
                    if not isinstance(hit, list) or len(hit) != 3:
                        continue
                    slot, t, dur = _slot(hit[0]), _num(hit[1]), hit[2]
                    if slot is None or t is None:
                        continue
                    if dur is None:
                        fallback = flash_fallback_s(e.get("ability")) if k == "flash" else NEARSIGHT_DEFAULT_S
                        out.append((slot, CONDITION[k], t, t + fallback, True, e))
                    elif _num(dur) is not None and dur > 0:
                        out.append((slot, CONDITION[k], t, t + float(dur), False, e))
                continue
            t = _num(e.get("t"))
            if t is None:
                continue
            if k == "flash":
                start = next((_num(a.get("t")) for a in util if isinstance(a, dict) and a.get("k") == "ability"
                              and _same_by(a.get("by"), e.get("by")) and isinstance(a.get("thrown"), dict)
                              and _num(a["thrown"].get("t0")) == t and _num(a.get("t")) is not None),
                             t + FLASH_FUSE_S)
                dur = flash_fallback_s(e.get("ability"))
            else:
                start, dur = t, nearsight_fallback_s(e.get("ability"))
            targets = e.get("targets")
            for target in targets if isinstance(targets, list) else []:
                if _slot(target) is not None:
                    out.append((target, CONDITION[k], start, start + dur, True, e))
        elif k == "status":
            slot, t, status = _slot(e.get("target")), _num(e.get("t")), e.get("status")
            if slot is None or t is None or not isinstance(status, str) or not status:
                continue
            if e.get("t1") is None:
                out.append((slot, status, t, t + STATUS_DEFAULT_S, True, e))
            elif _num(e.get("t1")) is not None and e["t1"] > t:
                out.append((slot, status, t, float(e["t1"]), False, e))
    return out


def _ability(e: dict):
    if isinstance(e.get("ability"), str) and e["ability"]:
        return e["ability"]
    code, name = e.get("code"), e.get("name")
    return f"{code}_{name}" if isinstance(code, str) and code and isinstance(name, str) and name else None


def _life_end(alive: dict | None, slot: int, t0: float) -> tuple[bool, float | None]:
    """(alive at t0, the end of that life or None when open). Unknown lives cut nothing."""
    if not isinstance(alive, dict) or not isinstance(alive.get(str(slot)), list):
        return True, None
    for life in alive[str(slot)]:
        if not isinstance(life, list) or len(life) < 2 or _num(life[0]) is None:
            continue
        end = _num(life[1])
        if life[0] <= t0 and (end is None or t0 < end):
            return True, end
    return False, None


def normalize_conditions(util: list, alive: dict | None = None, t_end: float | None = None) -> dict[int, list[dict]]:
    groups: dict[tuple[int, str], list[dict]] = {}
    for slot, c, t0, t1, est, e in _raw(util):
        living, end = _life_end(alive, slot, t0)
        if not living:
            continue
        if end is not None:
            t1 = min(t1, end)
        if _num(t_end) is not None:
            t1 = min(t1, float(t_end))
        if t1 <= t0:
            continue
        by = e.get("by")
        groups.setdefault((slot, c), []).append({"k": e.get("k"), "by": by if _slot(by) is not None else None,
                                                 "ability": _ability(e), "t0": t0, "t1": t1, "est": est})
    out: dict[int, list[dict]] = {}
    for (slot, c), sources in groups.items():
        sources.sort(key=lambda s: (s["t0"], s["t1"]))
        merged: list[dict] = []
        for s in sources:
            if merged and s["t0"] <= merged[-1]["t1"]:
                cur = merged[-1]
                cur["t1"] = max(cur["t1"], s["t1"])
                cur["est"] = cur["est"] or s["est"]
                cur["src"].append(s)
            else:
                merged.append({"c": c, "t0": s["t0"], "t1": s["t1"], "est": s["est"], "src": [s]})
        out.setdefault(slot, []).extend(merged)
    for slot in out:
        out[slot].sort(key=lambda x: (x["t0"], x["c"], x["t1"]))
    return dict(sorted(out.items()))


def conditions_at(conditions: dict, slot: int, t: float) -> list[dict]:
    """The slot's conditions active at `t`: `t0 <= t < t1`."""
    return [x for x in conditions.get(slot, []) if x["t0"] <= t < x["t1"]]

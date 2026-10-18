"""Pure functions that turn stored per-round moments, control deaths and
locating events into the JSON payload of the replay moments route.

No DB, no SQLAlchemy, no FastAPI.
"""
from __future__ import annotations

import statistics

from app.moments.constants import DEATH_MATCH_S, INFO_MERGE_S, MEDIAN_MIN_CLEAN


def _slot_list(by_slot, slot) -> list:
    if not by_slot:
        return []
    if slot in by_slot:
        return by_slot[slot]
    return by_slot.get(str(slot), by_slot.get(_as_int(slot), []))


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _fights_of(slot, rnd_moments) -> list:
    fights = (rnd_moments or {}).get("fights") or []
    return [f for f in fights if str(f.get("slot")) == str(slot)]


def attach_deaths(fights, round_deaths) -> list[dict]:
    out = []
    for f in fights:
        c = dict(f)
        if f.get("died") is not None:
            died = dict(f["died"])
            best = None
            for d in _slot_list(round_deaths, f.get("slot")):
                gap = abs(d["t"] - died["t"])
                if gap <= DEATH_MATCH_S and (best is None or gap < best[0]):
                    best = (gap, d)
            died["control_m2"] = best[1].get("control_m2") if best else None
            died["share"] = best[1].get("share_of_team") if best else None
            c["died"] = died
        out.append(c)
    return out


def _merge(times: list[float]) -> list[float]:
    kept: list[float] = []
    prev = None
    for t in sorted(times):
        if prev is None or not (t - prev < INFO_MERGE_S):
            kept.append(t)
        prev = t
    return kept


def player_info(slot, events, moments) -> dict:
    slot = _as_int(slot)
    heard, gunfire, seen, util = [], [], [], []
    for n, groups in (events or {}).items():
        rnd = int(n)
        fights = _fights_of(slot, (moments or {}).get(n, (moments or {}).get(rnd)))
        by_reason: dict[str, list[float]] = {}
        for side in (groups or {}).values():
            for t, s, reason in side:
                if _as_int(s) != slot:
                    continue
                if reason in ("damage", "kill"):
                    continue
                if reason == "gunfire" and any(f["t0"] <= t <= f["t1"] for f in fights):
                    continue
                by_reason.setdefault(reason, []).append(t)
        for reason, ts in by_reason.items():
            for t in _merge(ts):
                if reason == "footsteps":
                    heard.append({"round": rnd, "t": t})
                elif reason == "gunfire":
                    gunfire.append({"round": rnd, "t": t})
                elif reason == "seen":
                    seen.append(t)
                else:
                    util.append({"round": rnd, "t": t, "reason": reason})
    key = lambda m: (m["round"], m["t"])
    return {
        "heard": sorted(heard, key=key),
        "gunfire_outside": sorted(gunfire, key=key),
        "found_by_utility": sorted(util, key=lambda m: (m["round"], m["t"], m["reason"])),
        "seen": len(seen),
    }


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _bucket(kds):
    n = len(kds)
    if n < MEDIAN_MIN_CLEAN:
        return {"ttk_shot": None, "ttk_spot": None, "spot_to_shot": None, "n": n}
    s2s = [k["first_shot"] - k["spot"] for k in kds
           if k.get("first_shot") is not None and k.get("spot") is not None]
    return {
        "ttk_shot": _median([k.get("ttk_shot") for k in kds]),
        "ttk_spot": _median([k.get("ttk_spot") for k in kds]),
        "spot_to_shot": _median(s2s),
        "n": n,
    }


def player_summary(slot, moments, deaths) -> dict:
    slot = _as_int(slot)
    rows = []
    for n, by_slot in (deaths or {}).items():
        rnd = int(n)
        fights = _fights_of(slot, (moments or {}).get(n, (moments or {}).get(rnd)))
        for d in _slot_list(by_slot, slot):
            fi = None
            for f in fights:
                if f.get("died") and abs(f["died"]["t"] - d["t"]) <= DEATH_MATCH_S:
                    fi = f.get("i")
                    break
            rows.append({"round": rnd, "t": d["t"], "control_m2": d.get("control_m2"),
                         "share": d.get("share_of_team"), "fight_i": fi})
    rows.sort(key=lambda r: (-(r["control_m2"] or 0), r["round"], r["t"]))

    clean = []
    for n, rm in (moments or {}).items():
        for f in _fights_of(slot, rm):
            clean.extend(k for k in f.get("kill_detail") or [] if k.get("clean") is True)
    buckets = {"all": clean}
    for b in ("on", "near", "off"):
        buckets[b] = [k for k in clean if k.get("band") == b]
    return {"expensive_deaths": rows[:3],
            "medians": {k: _bucket(v) for k, v in buckets.items()}}


def build_payload(*, players, attack_round1, moments, deaths, events, stale) -> dict:
    rounds = {}
    for n, m in (moments or {}).items():
        ok = m.get("status") == "ok"
        rd = (deaths or {}).get(n, (deaths or {}).get(_as_int(n), {}))
        rounds[str(n)] = {
            "status": m.get("status"),
            "fights": attach_deaths(m.get("fights") or [], rd) if ok else [],
        }
    summary = {}
    for slot in players:
        summary[str(slot)] = {**player_summary(slot, moments, deaths),
                              "info": player_info(slot, events, moments)}
    return {"players": players, "attack_round1": attack_round1, "rounds": rounds,
            "summary": summary, "stale": stale}

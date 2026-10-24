"""What each cast was worth, from the worker's evidence (app/critutil/extract.py) and the round-win estimator
(app/services/util_model.py). Design: docs/superpowers/specs/2026-10-21-critical-util-design.md, "No double
counting" and "Scoring method".

Every value is a change in the caster's side's round-win probability (0..1; the pages show percentage points).
Pure: plain dicts in and out, no DB.

- The **ledger**: each kill in the round is one item worth the swing it caused (the estimator's P after minus
  before, for the killer's side). Its value is split once between the casts with evidence on it and the gunplay
  (constants.EVIDENCE_WEIGHT, GUN_WEIGHT), so util never takes a whole kill and no kill is counted twice. A
  teammate killed by an enemy we affected, or while blinded by our own util, is a negative item the same way.
- The **jobs** with no kill to point at (sight lines cut and teammates covered, enemies held back or waiting,
  devices triggered, defuses stopped, heals that kept someone alive, soft teammate harm) are counted per cast
  and turned into win odds by a few weights fitted on round wins (`fit_weights`), never below zero, with the
  harm columns entering negatively: the score can never reward harm.
"""

from __future__ import annotations

import functools
import math

import numpy as np

from app.critutil import abilities, windows
from app.critutil.constants import EVIDENCE_WEIGHT, GUN_WEIGHT
from app.services import util_model

VERSION = 2
# Starting weights in win probability per unit, used when the corpus is too small to fit them (fit_weights).
DEFAULT_WEIGHTS = {
    "blocked_s": 0.0010,       # per second of enemy sight lines cut (summed over pairs)
    "saves": 0.0150,           # per teammate covered by the smoke who lived through it
    "held_back": 0.0100,       # per enemy who came to the util's edge and didn't go through
    "waiting_s": 0.0015,       # per enemy-second stopped at its edge
    "revealed": 0.0080,        # per enemy revealed with no kill to show for it
    "triggered": 0.0080,       # a device that fired or was broken (active info)
    "defuse_stopped": 0.0600,  # per defuse attempt that overlapped it and didn't finish
    "heal_saved": 0.0500,      # a heal that kept the player alive through damage above their health
    "heal_hp_fought": 0.0002,  # per HP healed before a fight the healed player then took
    "harm_soft": 0.0080,       # per teammate blinded, nearsighted or statused, who lived (negative)
    "front_s": 0.0015,         # per second the map-control unknown stopped at a device's or molly's edge
    "held_m2s": 0.000005,      # per m2 x second of ground only that device or molly held (no teammate saw it)
}
NEGATIVE = frozenset({"harm_soft"})
JUDGED_KINDS = frozenset({"blind", "smoke", "wall", "damaging", "status", "info", "device"})   # can be wasted
MIN_ROUNDS_TO_FIT = 300
RIDGE = 50.0                # pull of every fitted weight toward its default, in rounds


def _kill_key(t, killer, victim):
    return (round(float(t), 2), killer, victim)


def swings(round_data: dict, model: dict | None) -> dict:
    """{(t, killer, victim): swing for the killer's side}, from the alive counts just before each kill. The
    estimator was fitted on the tracker.gg clock, so replay times are moved onto it by the round's
    `clock_offset` (t_db = t_replay + offset; app/services/replays.py)."""
    out = {}
    offset = float(round_data.get("clock_offset") or 0.0)
    plant = round_data.get("plant")
    plant_db = None if plant is None else plant + offset
    for k in round_data.get("kills") or ():
        ks, vs = k.get("k_side"), k.get("v_side")
        if ks not in ("attack", "defense") or vs not in ("attack", "defense") or ks == vs:
            continue
        a, d = k["before"]
        planted = plant is not None and plant <= k["t"]
        after_a, after_d = (a - 1, d) if vs == "attack" else (a, d - 1)
        t_db = k["t"] + offset
        before = util_model.p_side(model, ks, a, d, planted, t_db, plant_db)
        after = util_model.p_side(model, ks, max(after_a, 0), max(after_d, 0), planted, t_db, plant_db)
        out[_kill_key(k["t"], k["killer"], k["victim"])] = after - before
    return out


def features(cast: dict) -> dict:
    """The counts each non-ledger job is measured by. Zero where a job doesn't apply."""
    f = dict.fromkeys(DEFAULT_WEIGHTS, 0.0)
    cover = cast.get("cover") or {}
    f["blocked_s"] = float(cover.get("blocked_s") or 0.0)
    f["saves"] = float(len(cover.get("covered_lived") or ()))
    presence = cast.get("presence") or {}
    if presence and not (set(cast.get("kinds") or ()) & {"blind"}):
        f["held_back"] = float(len(presence.get("held_back") or ()))
        f["waiting_s"] = float(presence.get("waiting_s") or 0.0)
    killed = {k["victim"] for k in cast.get("kills") or () if k.get("ours")}
    f["revealed"] = float(len({e["target"] for e in cast.get("effects") or ()
                               if e.get("how") == "reveal" and e["target"] not in killed}))
    f["triggered"] = 1.0 if cast.get("trigger") is not None else 0.0
    f["defuse_stopped"] = float((cast.get("spike") or {}).get("stopped") or 0)
    # owner, 2026-10-21: flashing or statusing a teammate matters more than damaging one
    f["harm_soft"] = float(sum(0.5 if h.get("how") == "damage" else 1.0 for h in cast.get("harm") or ()
                               if not h.get("died")))
    hold = cast.get("hold") or {}
    f["front_s"] = float(hold.get("front_s") or 0.0)
    f["held_m2s"] = float(hold.get("held_m2s") or 0.0)
    return f


def heal_features(heal: dict) -> dict:
    f = dict.fromkeys(DEFAULT_WEIGHTS, 0.0)
    share = heal.get("saved_share")
    f["heal_saved"] = float(share) if share is not None else (1.0 if heal.get("saved") else 0.0)
    f["heal_hp_fought"] = float(heal.get("amount") or 0.0) if heal.get("fought") and heal.get("pool") == "hp" else 0.0
    return f


def _evidence(cast: dict, key) -> tuple[float, bool] | None:
    """(weight, positive) of this cast's evidence on one kill, or None."""
    best = None
    for k in cast.get("kills") or ():
        if _kill_key(k["t"], k["killer"], k["victim"]) != key:
            continue
        w = EVIDENCE_WEIGHT.get(k["ev"], 0.0)
        if best is None or w > best[0]:
            best = (w, bool(k.get("ours")))
    for h in cast.get("harm") or ():
        # this teammate killed while our util had them blinded: that death only (`died_t`), never a later life's
        if h.get("died") and key[2] == h["mate"] and h.get("died_t") is not None \
                and round(float(h["died_t"]), 2) == key[0]:
            w = EVIDENCE_WEIGHT["harmed_teammate"]
            if best is None or w > best[0]:
                best = (w, False)
    return best


def ledger(round_data: dict, model: dict | None) -> dict[int, dict]:
    """{cast i: {"value": sum of its shares, "items": [{t, victim, share, swing, ev}]}}."""
    sw = swings(round_data, model)
    casts = round_data.get("casts") or []
    out = {c["i"]: {"value": 0.0, "items": []} for c in casts}
    for key, swing in sw.items():
        claims = []
        for c in casts:
            if c.get("after_decided"):
                continue
            ev = _evidence(c, key)
            if ev is not None and ev[0] > 0:
                claims.append((c, ev))
        if not claims:
            continue
        total = sum(w for _, (w, _) in claims) + GUN_WEIGHT
        for c, (w, positive) in claims:
            share = w / total
            # `swing` is for the killer's side: our kill is +swing for us, an enemy's kill on us is -swing for us
            value = share * swing if positive else -share * swing
            out[c["i"]]["value"] += value
            out[c["i"]]["items"].append({"t": key[0], "killer": key[1], "victim": key[2], "share": round(share, 3),
                                         "swing": round(swing, 4), "value": round(value, 4), "positive": positive})
    return out


def job_value(f: dict, weights: dict) -> float:
    return sum((-1.0 if k in NEGATIVE else 1.0) * weights.get(k, 0.0) * v for k, v in f.items())


def value_round(round_data: dict, model: dict | None, weights: dict | None = None) -> dict:
    """{"casts": {i: {...}}, "heals": [{...}]}: each cast's ledger value, job value and total."""
    weights = weights or DEFAULT_WEIGHTS
    led = ledger(round_data, model)
    casts = {}
    for c in round_data.get("casts") or []:
        if c.get("after_decided"):
            casts[c["i"]] = {"ledger": 0.0, "jobs": 0.0, "total": 0.0, "features": {}, "items": [], "scored": False}
            continue
        f = features(c)
        jobs = job_value(f, weights)
        l = led[c["i"]]["value"]
        casts[c["i"]] = {"ledger": round(l, 4), "jobs": round(jobs, 4), "total": round(l + jobs, 4),
                         "features": {k: v for k, v in f.items() if v}, "items": led[c["i"]]["items"],
                         "scored": True, "wasted": is_wasted(c, l, f),
                         "found_nobody": set(c.get("kinds") or ()) <= {"info", "ult"} and not c.get("enemies")}
    heals = []
    for h in round_data.get("heals") or []:
        f = heal_features(h)
        heals.append({**h, "value": round(job_value(f, weights), 4)})
    return {"casts": casts, "heals": heals}


def is_wasted(cast: dict, ledger_value: float, f: dict) -> bool:
    """Decided last, only when nothing else applies: no enemy affected, no kill evidence, no sight line cut, no
    enemy at its edge, never triggered, no defuse stopped. A device that holds a lane nobody tried still counts
    as holding it while armed (the watch), so a device is wasted only when it was gone at once and showed no
    enemy."""
    kinds = set(cast.get("kinds") or ())
    if not kinds & JUDGED_KINDS or kinds & {"mobility", "teleport"}:
        return False            # movement, teleports, decoys and the like: never called wasted
    if kinds <= {"info", "ult"}:
        return False            # recon that found nobody still told the team the area was empty (found_nobody)
    if cast.get("enemies") or cast.get("kills") or ledger_value:
        return False
    if any(v for k, v in f.items() if k != "harm_soft"):
        return False
    if "device" in (cast.get("kinds") or ()):
        presence = cast.get("presence") or {}
        return (presence.get("armed_s") or 0) < 3.0 and not presence.get("entered")
    if "smoke" in (cast.get("kinds") or ()) or "wall" in (cast.get("kinds") or ()):
        return not (cast.get("cover") or {}).get("pairs") and not (cast.get("presence") or {}).get("entered")
    return True


# --- fitting the job weights on round wins


def team_rounds(rounds: list[dict], model: dict | None) -> list[dict]:
    """One row per (round, side): y = won - P(5v5 start) - that side's ledger total, x = summed job features.
    `rounds` items: {"match", "data" (extract output), "winner": "attack" | "defense"}."""
    rows = []
    for r in rounds:
        data, winner = r["data"], r.get("winner")
        if winner not in ("attack", "defense"):
            continue
        led = ledger(data, model)
        for side in ("attack", "defense"):
            p0 = util_model.p_side(model, side, 5, 5, False, 0.0, None)
            x = dict.fromkeys(DEFAULT_WEIGHTS, 0.0)
            led_sum = 0.0
            for c in data.get("casts") or []:
                if c.get("side") != side or c.get("after_decided"):
                    continue
                led_sum += led[c["i"]]["value"]
                for k, v in features(c).items():
                    x[k] += v
            sides = data.get("sides") or {}
            for h in data.get("heals") or []:
                if sides.get(str(h.get("by"))) == side:
                    for k, v in heal_features(h).items():
                        x[k] += v
            rows.append({"match": r["match"], "y": (1.0 if winner == side else 0.0) - p0 - led_sum, "x": x})
    return rows


def fit_weights(rows: list[dict], *, folds: int = 5) -> dict:
    """Non-negative weights (harm columns negated), ridge-pulled toward DEFAULT_WEIGHTS, fitted on round wins.
    Held-out check: k folds by match, the residual's mean square with fitted weights vs with none.

    When adopted, a match in the corpus is valued with its own fold's weights, fitted without it (`by_match`;
    spec, "Baseline": weights are set inside training folds, never on the matches being reported). `weights`,
    fitted on every match, are for a match the fit never saw."""
    names = list(DEFAULT_WEIGHTS)
    if len(rows) < MIN_ROUNDS_TO_FIT:
        return {"weights": dict(DEFAULT_WEIGHTS), "fitted": False, "rounds": len(rows), "check": None}
    import numpy as np

    def solve(train):
        X = np.array([[(-1.0 if n in NEGATIVE else 1.0) * r["x"][n] for n in names] for r in train])
        y = np.array([r["y"] for r in train])
        scale = np.maximum(np.abs(X).max(axis=0), 1e-9)
        Xs = X / scale
        prior = np.array([DEFAULT_WEIGHTS[n] for n in names]) * scale
        lam = math.sqrt(RIDGE)
        A = np.vstack([Xs, lam * np.eye(len(names))])
        b = np.concatenate([y, lam * prior])
        from scipy.optimize import nnls

        w, _ = nnls(A, b)
        return w / scale

    def mse(weights, test):
        if not test:
            return None
        err = 0.0
        for r in test:
            pred = sum((-1.0 if n in NEGATIVE else 1.0) * weights[i] * r["x"][n] for i, n in enumerate(names))
            err += (r["y"] - pred) ** 2
        return err / len(test)

    matches = sorted({r["match"] for r in rows})
    defaults = [DEFAULT_WEIGHTS[n_] for n_ in names]
    fitted_mse, zero_mse, default_mse, n = 0.0, 0.0, 0.0, 0
    by_match = {}
    for k in range(folds):
        held = set(matches[k::folds])
        train = [r for r in rows if r["match"] not in held]
        test = [r for r in rows if r["match"] in held]
        if not train or not test:
            continue
        w = solve(train)
        for m in held:
            by_match[str(m)] = {n_: float(w[i]) for i, n_ in enumerate(names)}
        fitted_mse += mse(w, test) * len(test)
        zero_mse += mse([0.0] * len(names), test) * len(test)
        default_mse += mse(defaults, test) * len(test)
        n += len(test)
    if not n:
        return {"weights": dict(DEFAULT_WEIGHTS), "fitted": False, "rounds": len(rows), "check": None}
    check = {"mse_fitted": round(float(fitted_mse / n), 5), "mse_default": round(float(default_mse / n), 5),
             "mse_none": round(float(zero_mse / n), 5)}
    if check["mse_fitted"] >= check["mse_default"]:
        # fitting didn't predict held-out rounds better than the starting weights: too little data to trust it
        return {"weights": dict(DEFAULT_WEIGHTS), "fitted": False, "rounds": len(rows), "check": check}
    w = solve(rows)
    return {"weights": {n_: float(w[i]) for i, n_ in enumerate(names)}, "fitted": True, "rounds": len(rows),
            "check": check, "by_match": by_match}


def weights_for(fit: dict | None, match_id) -> dict:
    """The weights a match is valued with: its own fold's when the fit was adopted and saw it, else the fit's."""
    fit = fit or {}
    own = (fit.get("by_match") or {}).get(str(match_id)) if fit.get("fitted") else None
    return own or fit.get("weights") or dict(DEFAULT_WEIGHTS)


# --- the no-util baseline (spec, "Baseline")
#
# What a cast is compared with: moments in the corpus in the same state (the estimator's cell: who is alive, the
# spike, the time bucket) and phase, for the same side, when no util of that kind by that side was up. A moment's
# outcome, and a cast's, is the change in that side's round-win chance over the next BASE_WINDOW_S (to t_decided),
# from the kills in the round. A cast's `lift` is its own change minus the comparison set's mean. Thin cells fall
# back a level (state + phase + time, then state + phase, then man advantage), and the level is kept with the
# lift. The comparison set never includes the cast's own match (`minus`). This baseline is not the ability prior:
# the prior is the ability's mean lift, which shrinks a small sample (`shrunk`).

BASE_WINDOW_S = 10.0
BASE_STEP_S = 2.0
BASE_MIN_N = 30             # comparison moments a level needs before it is used
BASE_LEVELS = ("state, phase and time", "state and phase", "man advantage")
KIND_ORDER = ("ult", "blind", "smoke", "wall", "damaging", "status", "info", "device", "heal", "other")


def main_kind(kinds) -> str:
    """The one kind a cast is grouped and compared by."""
    for k in KIND_ORDER:
        if k in (kinds or ()):
            return k
    return "other"


def _phase(t, contact, plant) -> str:
    if plant is not None and t >= plant:
        return "post_plant"
    if contact is None or t < contact:
        return "pre_contact"
    return "contact"


def _alive_at(round_data: dict, t: float) -> tuple[int, int]:
    """(attackers, defenders) alive at t, from the round's kills (each with its `before` counts)."""
    kills = sorted((k for k in round_data.get("kills") or () if k.get("before")), key=lambda k: k["t"])
    for k in kills:
        if k["t"] > t:
            return int(k["before"][0]), int(k["before"][1])
    if not kills:
        return 5, 5
    a, d = (int(x) for x in kills[-1]["before"])
    return (a - 1, d) if kills[-1].get("v_side") == "attack" else (a, d - 1)


def _p_at(round_data: dict, model, side: str, t: float) -> float:
    offset = float(round_data.get("clock_offset") or 0.0)
    plant = round_data.get("plant")
    a, d = _alive_at(round_data, t)
    return util_model.p_side(model, side, max(a, 0), max(d, 0), plant is not None and plant <= t, t + offset,
                             None if plant is None else plant + offset)


def levels(round_data: dict, side: str, t: float) -> list[str]:
    """The comparison cells of a moment, most specific first (BASE_LEVELS)."""
    offset = float(round_data.get("clock_offset") or 0.0)
    plant = round_data.get("plant")
    planted = plant is not None and plant <= t
    a, d = _alive_at(round_data, t)
    tb = util_model.time_bucket(t + offset, planted, None if plant is None else plant + offset)
    ph = _phase(t, round_data.get("contact"), plant)
    return [f"{side}|{a}v{d}|{int(planted)}|{ph}|{tb}", f"{side}|{a}v{d}|{int(planted)}|{ph}", f"{side}|{a}v{d}"]


# --- the no-util baseline, version 2: by type, over each cast's own up-time (plan 5, decision D14)
#
# A moment's outcome is a vector: the change in the side's round-win chance over the next L seconds, L = 1..45
# (cut at t_decided). A cast is compared at its own length L, and only its on-time counts: the sum of the swings
# over its window segments (app/critutil/windows.py), the first 45 s of them.

TYPES = ("flash", "smoke", "wall", "molly", "status", "recon", "placeable", "heal", "ult", "other")
MAX_L = 45
_SIDES = ("attack", "defense")


@functools.lru_cache(maxsize=1)
def _abilities_by_key() -> dict:
    return {f"{e['agent']}/{e['ability']}": e for e in abilities.load()}


def type_of(item: dict, default: str | None = None) -> str:
    """A cast's stored type if it is one of TYPES, else "other". A row with no `type` is `default` (a heal row is
    "heal"; the ult-use dicts pass "ult")."""
    if "type" in item:
        return item["type"] if item["type"] in TYPES else "other"
    return default or "heal"


def swing_grid(round_data: dict, model, side: str):
    """(times, P): the side's round-win chance at whole seconds 0..floor(t_decided), and t_decided itself."""
    td = max(float(round_data.get("t_decided") or 0.0), 0.0)
    times = list(np.arange(0, math.floor(td) + 1, dtype=float))
    if td > times[-1]:
        times.append(td)
    times = np.array(times)
    return times, np.array([_p_at(round_data, model, side, float(t)) for t in times])


def p_on_grid(times, P, x):
    """P at x by linear interpolation, x clamped to [0, t_decided]."""
    return np.interp(np.clip(x, 0.0, times[-1]), times, P)


def item_segments(round_data: dict, item: dict, typ: str | None = None) -> list:
    """The item's up-time segments (windows.window with its abilities table entry)."""
    entry = None
    if "type" in item and item.get("ability_id") is not None:
        entry = _abilities_by_key().get(item["ability_id"])
    out = windows.window(item, entry, float(round_data.get("t_decided") or 0.0), bool(round_data.get("on_known", False)))
    return out["segments"]


def up_segments(round_data: dict) -> list:
    """[(side, type, segments)] for the round's casts, heals and ult uses."""
    out = []
    sides = round_data.get("sides") or {}
    for c in round_data.get("casts") or ():
        if c.get("after_decided"):
            continue
        out.append((c.get("side"), type_of(c), item_segments(round_data, c)))
    for h in round_data.get("heals") or ():
        out.append((sides.get(str(h.get("by"))), "heal", item_segments(round_data, h)))
    for slot, ult in (round_data.get("ults") or {}).items():
        use = (ult or {}).get("use")
        if use is None or use.get("t") is None:
            continue
        out.append((sides.get(str(slot)), "ult", item_segments(round_data, use, "ult")))
    return out


def _grid_swings(times, P, t: float, td: float):
    ls = np.arange(1, MAX_L + 1, dtype=float)
    return p_on_grid(times, P, np.minimum(t + ls, td)) - float(p_on_grid(times, P, t))


def moment_rows(round_data: dict, model):
    """Every BASE_STEP_S of the round to t_decided, per side: (side, t, keys, up, row, alive, next_start).
    keys = levels(); up = the set of types that side has up at t; row = [1, swing_1..swing_45]; alive = the slots
    of that side alive at t; next_start = {type: the earliest start s > t of one of that side's segments of that
    type} (only types that have one)."""
    td = float(round_data.get("t_decided") or 0.0)
    steps = []
    t = 0.0
    while t < td:
        steps.append(t)
        t += BASE_STEP_S
    if not steps:
        return
    segs = up_segments(round_data)
    sides = round_data.get("sides") or {}
    deaths: dict = {}
    for k in round_data.get("kills") or ():
        v = k["victim"]
        deaths[v] = min(deaths.get(v, 1e9), k["t"])
    for side in _SIDES:
        times, P = swing_grid(round_data, model, side)
        mine = [(typ if typ in TYPES else "other", ss) for s_side, typ, ss in segs if s_side == side]
        slots = [int(s) for s, sd in sides.items() if sd == side]
        for t in steps:
            up = {typ for typ, ss in mine if any(a <= t < b for a, b in ss)}
            nxt: dict = {}
            for typ, ss in mine:
                for a, _b in ss:
                    if a > t and a < nxt.get(typ, 1e18):
                        nxt[typ] = a
            alive = {s for s in slots if deaths.get(s, 1e9) > t}
            row = np.concatenate(([1.0], _grid_swings(times, P, t, td)))
            yield side, t, levels(round_data, side, t), up, row, alive, nxt


def _acc(cells: dict, key: str, row) -> None:
    if key in cells:
        cells[key] += row
    else:
        cells[key] = np.array(row, dtype=float)


def expand(cells: dict) -> dict:
    """Level-0 cells {key: arr} -> {key at all three levels: arr}: children summed into their parents."""
    out: dict = {}
    for key, arr in cells.items():
        parts = key.split("|")
        for k in (key, "|".join(parts[:4]), "|".join(parts[:2])):
            _acc(out, k, arr)
    return out


def add_moments_v2(table: dict, round_data: dict, model) -> None:
    """Adds a round's moments to {type: {level key: array [n, sum_1..sum_45]}}: each counts for every type not up."""
    l0: dict = {}
    for _side, _t, keys, up, row, _alive, _nxt in moment_rows(round_data, model):
        for typ in TYPES:
            if typ not in up:
                _acc(l0.setdefault(typ, {}), keys[0], row)
    for typ, cells in l0.items():
        out = table.setdefault(typ, {})
        for key, arr in expand(cells).items():
            out[key] = out[key] + arr if key in out else arr


def merge_tables_v2(into: dict, table: dict) -> None:
    for typ, cells in table.items():
        out = into.setdefault(typ, {})
        for key, arr in cells.items():
            out[key] = out[key] + arr if key in out else np.array(arr, dtype=float)


def cast_swing_v2(round_data: dict, model, item: dict, segments: list, side: str | None = None):
    """(swing, L): the sum of P(end) - P(start) over the first 45 s of on-time, and that on-time rounded to a second
    (1..45). (None, 0) without segments."""
    side = side or item.get("side")
    times, P = swing_grid(round_data, model, side)
    left, total, swing = float(MAX_L), 0.0, 0.0
    for s, e in segments:
        if left <= 0:
            break
        e = min(e, s + left)
        if e <= s:
            continue
        swing += float(p_on_grid(times, P, e) - p_on_grid(times, P, s))
        total += e - s
        left -= e - s
    if total <= 0:
        return None, 0
    return swing, max(1, min(MAX_L, int(math.floor(total + 0.5))))


def baseline_v2(table: dict, typ: str, keys: list, L: int, minus: dict | None = None) -> dict | None:
    """{"mean", "n", "level"} at length L from the most specific level with BASE_MIN_N moments; None when none has."""
    cells = table.get(typ) or {}
    own = (minus or {}).get(typ) or {}
    for i, key in enumerate(keys):
        arr = cells.get(key)
        if arr is None:
            continue
        if key in own:
            arr = arr - own[key]
        n = arr[0]
        if n >= BASE_MIN_N:
            return {"mean": float(arr[L]) / n, "n": int(round(n)), "level": BASE_LEVELS[i]}
    return None


def cast_lift_v2(round_data: dict, model, item: dict, table: dict, minus: dict | None = None,
                 typ: str | None = None, side: str | None = None) -> dict:
    """An item (cast, heal row or ult use; pass `typ`/`side` for an ult use) against the v2 baseline."""
    sides = round_data.get("sides") or {}
    side = side or item.get("side") or sides.get(str(item.get("by")))
    typ = typ if typ in TYPES else type_of(item, typ)
    out = {"swing": None, "base": None, "base_n": 0, "base_level": None, "lift": None, "L": 0, "type": typ}
    segs = item_segments(round_data, item, typ)
    if not segs or side not in _SIDES:
        return out
    swing, L = cast_swing_v2(round_data, model, item, segs, side)
    if swing is None:
        return out
    out.update(swing=round(swing, 4), L=L)
    base = baseline_v2(table, typ, levels(round_data, side, max(float(segs[0][0]), 0.0)), L, minus)
    if base is not None:
        out.update(base=round(base["mean"], 4), base_n=base["n"], base_level=base["level"],
                   lift=round(swing - base["mean"], 4))
    return out


# --- v2 per-match tables, leave-one-match-out gate, Yours and device tables (plan 5, R1-R3, R10, D15)
#
# Tables are kept per match at level 0 only (sparse); `expand` gives the three levels and `totals` sums matches.


def match_tables(items: list, model, friend_players=frozenset()) -> dict:
    """items = [{"match", "map", "data" (round data), "player_of" {slot: player id}}]. Returns
    "all" {match: {type: {L0 key: arr}}}; "yours" {player: {match: {type: {L0 key: arr}}}} (each moment also for
    each alive slot of that side whose player is in friend_players); "device" {"<ability_id>|<map>|<side>":
    {match: {L0 key: arr}}} (every moment of that map and side in rounds where that side placed no cast of that
    ability_id: D15, no other filter); "overlap" {type: [n, o_1..o_45]} (n comparison moments; o_L those whose
    next same-type start s has s < t + L)."""
    out_all: dict = {}
    yours: dict = {}
    device: dict = {}
    overlap: dict = {}
    ls = np.arange(1, MAX_L + 1, dtype=float)
    ids: set = set()
    maps: set = set()
    for it in items:
        maps.add(it["map"])
        for c in it["data"].get("casts") or ():
            if type_of(c) == "placeable" and c.get("ability_id") is not None:
                ids.add(c["ability_id"])
    for it in items:
        m, rd = it["match"], it["data"]
        player_of = it.get("player_of") or {}
        placed = {s: {c.get("ability_id") for c in rd.get("casts") or ()
                      if c.get("side") == s and type_of(c) == "placeable"} for s in _SIDES}
        all_m = out_all.setdefault(m, {})
        for side, t, keys, up, row, alive, nxt in moment_rows(rd, model):
            k0 = keys[0]
            pids = [player_of[s] for s in alive if player_of.get(s) in friend_players]
            for typ in TYPES:
                if typ in up:
                    continue
                _acc(all_m.setdefault(typ, {}), k0, row)
                for pid in pids:
                    _acc(yours.setdefault(pid, {}).setdefault(m, {}).setdefault(typ, {}), k0, row)
                ov = overlap.get(typ)
                if ov is None:
                    ov = overlap[typ] = np.zeros(MAX_L + 1)
                ov[0] += 1
                if typ in nxt:
                    ov[1:] += (nxt[typ] < t + ls)
            for aid in ids:
                if aid not in placed[side]:
                    _acc(device.setdefault(f"{aid}|{it['map']}|{side}", {}).setdefault(m, {}), k0, row)
    for aid in ids:                     # every key exists even when no round qualified
        for mp in maps:
            for side in _SIDES:
                device.setdefault(f"{aid}|{mp}|{side}", {})
    return {"all": out_all, "yours": yours, "device": device, "overlap": overlap}


def _expand_typed(typed: dict) -> dict:
    return {typ: expand(cells) for typ, cells in typed.items()}


def _add_typed(into: dict, typed: dict, sign: float = 1.0) -> None:
    for typ, cells in typed.items():
        out = into.setdefault(typ, {})
        for key, arr in cells.items():
            if key in out:
                out[key] = out[key] + sign * arr
            else:
                out[key] = sign * arr


def totals(per_match: dict, leave_out=(), typed: bool = True) -> dict:
    """The expanded {type: {key at 3 levels: arr}} summed over the matches not in leave_out. With typed=False the
    per-match value is {L0 key: arr} (a device's) and the result is {key: arr}."""
    out: dict = {}
    for m, v in per_match.items():
        if m in leave_out:
            continue
        if typed:
            _add_typed(out, _expand_typed(v))
        else:
            for key, arr in expand(v).items():
                _acc(out, key, arr)
    return out


def lift_v2(totals_table: dict, per_match: dict, typ: str, keys: list, L: int, swing: float, leave_out=()):
    """A cast's lift against `totals_table` minus the expanded cells of every match in leave_out (Yours leaves out
    all of a lineup's matches), through the levels and BASE_MIN_N like baseline_v2. None without a baseline."""
    minus = totals({m: v for m, v in per_match.items() if m in set(leave_out)})
    base = baseline_v2(totals_table, typ, keys, L, minus)
    if base is None:
        return None
    return {**base, "lift": swing - base["mean"]}


def make_predictor(per_match: dict, typ: str) -> dict:
    """A prepared outside predictor for `gate`: the main table's per-match cells (typed), its totals, and the type
    whose cells predict (the device gate uses "placeable")."""
    exp = {m: _expand_typed(v) for m, v in per_match.items()}
    tot: dict = {}
    for e in exp.values():
        _add_typed(tot, e)
    return {"exp": exp, "tot": tot, "type": typ}


def _pick(tot: dict, minus: dict | None, typ: str, keys: list, min_n: float):
    """(mean vector [45], index of the level) from the most specific key with n >= min_n after taking `minus` out."""
    cells = tot.get(typ) or {}
    own = (minus or {}).get(typ) or {}
    for i, key in enumerate(keys):
        arr = cells.get(key)
        if arr is None:
            continue
        if key in own:
            arr = arr - own[key]
        if arr[0] >= min_n:
            return arr[1:] / arr[0], i
    return None


def _gate_sums(per_match: dict, min_n: float, predictor: dict | None = None) -> dict:
    """{type: {"n", "cell" [45], "ma" [45], "diffs" [per-match arrays of (se_cell - se_ma)]}}."""
    exp = {m: _expand_typed(v) for m, v in per_match.items()}
    tot: dict = {}
    for e in exp.values():
        _add_typed(tot, e)
    out: dict = {}
    for m, v in per_match.items():
        em = exp[m]
        pm = predictor["exp"].get(m) if predictor else None
        for typ, cells in v.items():
            for k0, arr in cells.items():
                parts = k0.split("|")
                keys = [k0, "|".join(parts[:4]), "|".join(parts[:2])]
                cell = _pick(tot, em, typ, keys, min_n)
                if cell is None:
                    continue
                if predictor is None:
                    ma = _pick(tot, em, typ, keys[2:], min_n)
                else:
                    ma = _pick(predictor["tot"], pm, predictor["type"], keys, min_n)
                if ma is None:
                    continue
                n_k, S = arr[0], arr[1:]
                e_cell = -2 * cell[0] * S + n_k * cell[0] ** 2
                e_ma = -2 * ma[0] * S + n_k * ma[0] ** 2
                o = out.setdefault(typ, {"n": 0.0, "cell": np.zeros(MAX_L), "ma": np.zeros(MAX_L), "diffs": {}})
                o["n"] += n_k
                o["cell"] += e_cell
                o["ma"] += e_ma
                o["diffs"][m] = o["diffs"].get(m, 0.0) + (e_cell - e_ma)
    return out


def _finish(sums: list) -> dict:
    """Pools a list of _gate_sums entries ({"n", "cell", "ma", "diffs": {unit: array}}) into {L: {...}}."""
    n = sum(s["n"] for s in sums)
    cell = sum((s["cell"] for s in sums), np.zeros(MAX_L))
    ma = sum((s["ma"] for s in sums), np.zeros(MAX_L))
    by_unit: dict = {}                  # one difference per match (or player-match), summed over types
    for s in sums:
        for unit, d in s["diffs"].items():
            by_unit[unit] = by_unit[unit] + d if unit in by_unit else np.array(d, dtype=float)
    diffs = list(by_unit.values())
    out = {}
    for L in range(1, MAX_L + 1):
        ds = [float(d[L - 1]) for d in diffs]
        sd = float(np.std(ds, ddof=1)) if len(ds) > 1 else 0.0
        c, a = float(cell[L - 1]), float(ma[L - 1])
        # strictly less; a float-noise margin keeps equal errors from passing
        passed = n > 0 and c < a - 1e-12 * max(1.0, abs(a))
        out[L] = {"n": int(round(n)), "se_cell": c, "se_ma": a, "pass": bool(passed),
                  "diff_mean": float(np.mean(ds)) if ds else 0.0, "diff_sd": sd, "matches": len(ds)}
    return out


def _gate_result(sums_by_type: dict) -> dict:
    return {"all": _finish(list(sums_by_type.values())),
            "by_type": {typ: _finish([s]) for typ, s in sums_by_type.items()}}


def gate(per_match: dict, min_n: float = BASE_MIN_N, predictor: dict | None = None) -> dict:
    """Leave-one-match-out: does the cell mean (most specific level with min_n moments in the other matches) predict
    a held-out match's moments better than the man-advantage mean (level 2, also min_n, other matches)? Sums of
    squared errors per L = 1..45, the moments' own sum of squares being the same for both and left out (D15). A cell
    missing either predictor is skipped. `predictor` (from make_predictor) replaces the man-advantage side with
    another table's cell, at the level chosen from it with the same match out. `per_match` is
    {match: {type: {L0 key: arr}}}. Returns {"all": {L: {n, se_cell, se_ma, pass, diff_mean, diff_sd, matches}},
    "by_type": {type: {L: ...}}}."""
    return _gate_result(_gate_sums(per_match, min_n, predictor))


def _pool(units: list) -> dict:
    """Pools several _gate_sums results (by type) into one {type: sums}, units keyed (unit, match)."""
    out: dict = {}
    for ui, sums in enumerate(units):
        for typ, s in sums.items():
            o = out.setdefault(typ, {"n": 0.0, "cell": np.zeros(MAX_L), "ma": np.zeros(MAX_L), "diffs": {}})
            o["n"] += s["n"]
            o["cell"] += s["cell"]
            o["ma"] += s["ma"]
            for m, d in s["diffs"].items():
                o["diffs"][(ui, m)] = d
    return out


def gate_yours(yours: dict, min_n: float = BASE_MIN_N) -> dict:
    """`gate` pooled over players, each on its own tables (own leave-one-match-out); diff stats over
    (player, match) pairs."""
    return _gate_result(_pool([_gate_sums(pm, min_n) for pm in yours.values()]))


def gate_device(device: dict, main_all: dict, min_n: float = BASE_MIN_N) -> dict:
    """Per device key, the held-out device cell predicted by the device's own cell mean (levels, own match out)
    against the main "all" table's placeable cell at the level chosen from it (own match out); pooled over
    devices. Returns {L: {...}} (one pool, no type split)."""
    pred = make_predictor(main_all, "placeable")
    units = [_gate_sums({m: {"device": cells} for m, cells in pm.items()}, min_n, pred) for pm in device.values()]
    pooled = _pool(units)
    return _finish(list(pooled.values()))


def gate_all_modes(tables: dict, min_n: float = BASE_MIN_N) -> dict:
    """{"all": {L}, "yours": {L}, "device": {L}, "by_type": {"all": {type: {L}}, "yours": {type: {L}}}}."""
    a = gate(tables["all"], min_n)
    y = gate_yours(tables["yours"], min_n)
    return {"all": a["all"], "yours": y["all"], "device": gate_device(tables["device"], tables["all"], min_n),
            "by_type": {"all": a["by_type"], "yours": y["by_type"]}}


# --- rankings


def shrunk(values: list[float], prior_mean: float, k: float = 5.0) -> float:
    n = len(values)
    return (sum(values) + k * prior_mean) / (n + k) if n + k else prior_mean


def call(score: float, spread: float) -> str:
    """The plain call for a lineup's score (owner decision, 2026-10-21), graded by the corpus's spread of
    lineup scores."""
    if spread <= 0:
        return "no clear value"
    z = score / spread
    if z >= 1.0:
        return "this wins rounds, keep throwing it"
    if z >= 0.25:
        return "keep throwing it"
    if z <= -0.25:
        return "this is costing you"
    return "no clear value"

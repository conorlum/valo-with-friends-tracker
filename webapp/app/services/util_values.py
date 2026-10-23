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

import math

from app.critutil.constants import EVIDENCE_WEIGHT, GUN_WEIGHT
from app.services import util_model

VERSION = 1
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


def window_swing(round_data: dict, model, side: str, t: float) -> float:
    """The change in `side`'s round-win chance from t over the next BASE_WINDOW_S, cut at t_decided."""
    end = min(t + BASE_WINDOW_S, float(round_data.get("t_decided") or t + BASE_WINDOW_S))
    return _p_at(round_data, model, side, max(end, t)) - _p_at(round_data, model, side, t)


def levels(round_data: dict, side: str, t: float) -> list[str]:
    """The comparison cells of a moment, most specific first (BASE_LEVELS)."""
    offset = float(round_data.get("clock_offset") or 0.0)
    plant = round_data.get("plant")
    planted = plant is not None and plant <= t
    a, d = _alive_at(round_data, t)
    tb = util_model.time_bucket(t + offset, planted, None if plant is None else plant + offset)
    ph = _phase(t, round_data.get("contact"), plant)
    return [f"{side}|{a}v{d}|{int(planted)}|{ph}|{tb}", f"{side}|{a}v{d}|{int(planted)}|{ph}", f"{side}|{a}v{d}"]


def moments(round_data: dict, model):
    """(side, levels, kinds of that side's util up, outcome, slots of that side alive) every BASE_STEP_S of the
    round to t_decided."""
    end = float(round_data.get("t_decided") or 0.0)
    casts = [c for c in round_data.get("casts") or () if not c.get("after_decided")]
    deaths = {}
    for k in round_data.get("kills") or ():
        deaths.setdefault(k["victim"], k["t"])
    sides = round_data.get("sides") or {}
    t = 0.0
    while t < end:
        for side in ("attack", "defense"):
            up = {main_kind(c.get("kinds")) for c in casts
                  if c.get("side") == side and c["t"] <= t < (c.get("t_end") or c["t"])}
            alive = {int(s) for s, sd in sides.items() if sd == side and deaths.get(int(s), 1e9) > t}
            yield side, levels(round_data, side, t), up, window_swing(round_data, model, side, t), alive
        t += BASE_STEP_S


def add_moments(table: dict, round_data: dict, model, personal: dict | None = None,
                player_of: dict | None = None) -> None:
    """Adds a round's moments to a baseline table {kind: {level key: [sum, n]}}: each moment counts for every kind
    that wasn't up. With `personal` (and `player_of`, slot -> player id), each alive player's own moments too,
    keyed "player|kind|side": the personal baseline shown beside the corpus's."""
    for side, keys, up, swing, alive in moments(round_data, model):
        for kind in KIND_ORDER:
            if kind in up:
                continue
            cells = table.setdefault(kind, {})
            for key in keys:
                cell = cells.setdefault(key, [0.0, 0])
                cell[0] += swing
                cell[1] += 1
            if personal is not None:
                for slot in alive:
                    pid = (player_of or {}).get(slot)
                    if pid is not None:
                        cell = personal.setdefault(f"{pid}|{kind}|{side}", [0.0, 0])
                        cell[0] += swing
                        cell[1] += 1


def merge_tables(into: dict, table: dict) -> None:
    for kind, cells in table.items():
        out = into.setdefault(kind, {})
        for key, (s, n) in cells.items():
            cell = out.setdefault(key, [0.0, 0])
            cell[0] += s
            cell[1] += n


def baseline(table: dict, kind: str, keys: list[str], minus: dict | None = None) -> dict | None:
    """{"mean", "n", "level"} from the most specific level with BASE_MIN_N moments; None when none has. `minus`
    (the cast's own match's table) is taken out first."""
    cells = table.get(kind) or {}
    own = (minus or {}).get(kind) or {}
    for i, key in enumerate(keys):
        s, n = cells.get(key, (0.0, 0))
        s_own, n_own = own.get(key, (0.0, 0))
        s, n = s - s_own, n - n_own
        if n >= BASE_MIN_N:
            return {"mean": s / n, "n": int(n), "level": BASE_LEVELS[i]}
    return None


def cast_lift(round_data: dict, model, cast: dict, table: dict, minus: dict | None = None) -> dict:
    """A cast against the no-util baseline: its own outcome (`swing`), the comparison set's (`base`, `base_n`,
    `base_level`) and the difference (`lift`, None without a comparison set)."""
    side, t = cast.get("side"), float(cast["t"])
    if side not in ("attack", "defense"):
        return {"swing": None, "base": None, "base_n": 0, "base_level": None, "lift": None}
    swing = window_swing(round_data, model, side, max(t, 0.0))
    base = baseline(table, main_kind(cast.get("kinds")), levels(round_data, side, max(t, 0.0)), minus)
    return {"swing": round(swing, 4), "base": None if base is None else round(base["mean"], 4),
            "base_n": 0 if base is None else base["n"], "base_level": None if base is None else base["level"],
            "lift": None if base is None else round(swing - base["mean"], 4)}


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

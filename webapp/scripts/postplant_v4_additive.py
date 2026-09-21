r"""Run from webapp/, read-only:

    DATABASE_URL=... .\.venv313\Scripts\python.exe scripts\postplant_v4_additive.py --out DIR

DECLARATION 10. The first ADDITIVE arm: `K(s) + f`, not `K(s) * T(t)`.

Read the declaration before this file. The design point is that `alpha` is NOT
taken from a grid -- an additive term raises the post-plant payout, and C's
optimum is ~1.97 while the flat-1.0 base is far below it, so an additive arm
would beat `F-1.00` merely by raising the level and prove nothing.

So a calibration pass reads the scorer's OWN per-kill values through
`kill_observer` (`kill_order_bonus_raw`, `seconds_to_plant`), and `alpha` is
solved per arm so the arm's MEAN post-plant payout equals a flat arm already
measured on C. Each additive arm is then contrasted against that level-matched
twin, which isolates additive-vs-flat with the level held constant.

    mean T = 1 + (1/N) * SUM_applies (alpha * kbar * shape_i / K_i)

Self-kills, phantom plants and K=0 events take the flat 1.0 base: they count in
N and contribute nothing to the sum, exactly as the variant treats them.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import SessionLocal
from app.scoring.impact import build_impact_rows_for_match
from app.services.impact_eval import (
    _feature_value,
    load_all_observations,
    paired_oof_log_loss_delta,
    stable_folds,
)
from app.services.stats_math import (
    fit_logistic,
    predict_proba,
    standardize,
    weighted_log_loss,
)

import postplant_v4_variants as variants

N_FOLDS, SEED, DRAWS, L2 = 5, 0, 2000, 1.0
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff",
                    "full_buy_count_diff"]
FEATURES = ["impact_diff"] + CONTROLS_CONTEXT

# derived_tables.json swing_vs_K, kill-weighted over 153,450 post-plant kills.
KBAR = 137.71
SPIKE = 45.0
# Declared constant, NOT fitted here. Rounded from A1's per-fold fits; see
# declaration 10 for the optimistic-bias caveat recorded in advance.
SIDE_WEIGHTS = {True: 1.25, False: 0.79}
# C's demonstrated floor (declaration 8). T2's is 0.107%.
C_FLOOR = 0.000407

# arm -> (uses time shape, uses side split, flat twin it is level-matched to)
ADDITIVE_ARMS = {
    "ADD-T@1.26":  (True,  False, "F-1.26"),
    "ADD-T@1.6":   (True,  False, "F-1.6"),
    "ADD-T@1.9":   (True,  False, "F-1.9"),
    "ADD-S@1.6":   (False, True,  "F-1.6"),
    "ADD-TS@1.6":  (True,  True,  "F-1.6"),
}
FLATS = ["F-1.26", "F-1.6", "F-1.9"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def calibrate(db):
    """Post-plant kill population, read from the scorer's own observer.

    Returns (n_postplant, rows) where each row is (shape_time, K,
    victim_is_attacker) for the kills the additive term actually applies to.
    """
    n_all = 0
    rows = []
    match_ids = [r[0] for r in db.execute(text("SELECT id FROM matches ORDER BY id"))]
    for match_id in match_ids:
        def observe(round_number, kill_index, kill, context):
            nonlocal n_all
            stp = context["seconds_to_plant"]
            if not (context["planted"] and stp is not None and stp <= 0):
                return
            n_all += 1
            outcome = context.get("round_outcome") or ""
            if context["self_kill"] or "Time Win" in outcome:
                return                      # phantom plant / self-kill: flat base
            k = context.get("kill_order_bonus_raw")
            if not k:
                return
            t = -float(stp)                 # seconds since the plant
            rows.append((min(max(t / SPIKE, 0.0), 1.0),
                         float(k),
                         not context["killer_is_attacker"]))
        build_impact_rows_for_match(db, match_id, kill_observer=observe)
        db.rollback()
    return n_all, rows


def solve_alpha(n_all, rows, target_mean_T, use_time, use_side):
    """alpha such that the arm's mean post-plant T equals target_mean_T."""
    total = 0.0
    for shape_t, k, victim_is_attacker in rows:
        shape = shape_t if use_time else 1.0
        if use_side:
            shape *= SIDE_WEIGHTS[victim_is_attacker]
        total += KBAR * shape / k
    if total <= 0:
        raise SystemExit("degenerate calibration")
    return (target_mean_T - 1.0) * n_all / total


def round_dataset(obs):
    rows, y, w, mids = [], [], [], []
    for o in obs:
        if o.round_won_by_team_a is None:
            continue
        rows.append([_feature_value(o, f) for f in FEATURES])
        y.append(1.0 if o.round_won_by_team_a else 0.0)
        w.append(1.0)
        mids.append(o.match_id)
    return (np.array(rows, dtype=float), np.array(y), np.array(w),
            np.array(mids, dtype=int))


def replay(db, variant):
    variants.activate(variant)
    obs = load_all_observations(db)
    variants.activate(None)
    db.rollback()
    return round_dataset(obs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()

    log("calibration pass: reading the scorer's own per-kill values ...")
    n_all, rows = calibrate(db)
    log(f"  {n_all:,} post-plant scored kills, {len(rows):,} the additive term applies to")
    log(f"  mean t/45 = {np.mean([r[0] for r in rows]):.4f}, mean K = {np.mean([r[1] for r in rows]):.2f}")

    alphas = {}
    for arm, (use_time, use_side, twin) in ADDITIVE_ARMS.items():
        target = float(twin.split("-")[1])
        a = solve_alpha(n_all, rows, target, use_time, use_side)
        alphas[arm] = a
        log(f"  {arm:12s} level-matched to {twin:7s} (mean T {target})  ->  alpha = {a:.5f}")

    variants.install()
    oofs, diffs, results = {}, {}, {}
    base = None
    order = ["P0"] + FLATS + list(ADDITIVE_ARMS)
    for name in order:
        if name == "P0":
            v = None
        elif name in ADDITIVE_ARMS:
            use_time, use_side, _twin = ADDITIVE_ARMS[name]
            v = variants.variant_for("ADD-", alpha=alphas[name], kbar=KBAR,
                                     use_time=use_time,
                                     side_weights=SIDE_WEIGHTS if use_side else None)
        else:
            v = variants.variant_for(name)
        log(f"replaying {name} ...")
        X, y, w, mids = replay(db, v)
        if base is None:
            base = (y, w, mids, stable_folds(mids.tolist(), n_folds=N_FOLDS, seed=SEED))
        elif not np.array_equal(mids, base[2]):
            raise SystemExit(f"row misalignment on {name}")
        y, w, mids, folds = base
        preds = np.zeros(len(y))
        for fold in range(N_FOLDS):
            te = np.array([folds.get(int(m), -1) == fold for m in mids])
            s_tr, s_te, _c, _s = standardize(X[~te], X[te])
            beta = fit_logistic(s_tr, y[~te], weights=w[~te], l2=L2)
            preds[te] = predict_proba(beta, s_te)
        loss = weighted_log_loss(preds, y, w)
        oofs[name] = {"scores": preds, "y": y, "w": w, "match_ids": mids}
        diffs[name] = X[:, 0].copy()
        results[name] = {"log_loss": float(loss), "n": int(len(y)),
                         "alpha": alphas.get(name)}
        log(f"  {name}: round-target log loss {loss:.8f}")
    variants.uninstall()

    pairs = [(a, ADDITIVE_ARMS[a][2]) for a in ADDITIVE_ARMS]
    pairs += [("ADD-TS@1.6", "ADD-T@1.6")]
    pairs += [(a, "P0") for a in ADDITIVE_ARMS]
    pairs += [(f, "P0") for f in FLATS]

    print()
    print("PROTOCOL GATE -- separability, before any bootstrap")
    print(f"  C's demonstrated floor {C_FLOOR*100:.4f}%")
    gate = {}
    for arm, ref in pairs:
        r2 = float(np.corrcoef(diffs[arm], diffs[ref])[0, 1]) ** 2
        resid = 1.0 - r2
        gate[f"{arm} vs {ref}"] = {"r2": r2, "residual_variance": resid,
                                   "below_c_floor": bool(resid < C_FLOOR)}
        print(f"  {arm:12s} vs {ref:8s}  R2 {r2:.6f}  resid var {resid*100:8.4f}%  "
              f"{'BELOW FLOOR -- UNTESTABLE' if resid < C_FLOOR else 'testable'}")
    results["_gate"] = {"c_floor": C_FLOOR, "arms": gate}

    print()
    print("DECLARATION 10 -- additive vs its LEVEL-MATCHED flat twin")
    for arm, ref in pairs:
        point, lo, hi = paired_oof_log_loss_delta(oofs[arm], oofs[ref], draws=DRAWS)
        verdict = ("INCONCLUSIVE" if lo <= 0 <= hi else
                   ("HARM" if point > 0 else "IMPROVEMENT"))
        key = f"{arm} vs {ref}"
        results[key] = {"point": point, "lo": lo, "hi": hi, "verdict": verdict,
                        "residual_variance": gate[key]["residual_variance"],
                        "below_c_floor": gate[key]["below_c_floor"]}
        note = "  [UNTESTABLE]" if gate[key]["below_c_floor"] else ""
        print(f"  {key:26s} {point:+.6e}  [{lo:+.6e}, {hi:+.6e}]  {verdict}{note}")
    print("  NOTE: C is partly circular -- declaration 7. A favourable result")
    print("        here is NOT a shipping result; it needs T2 confirmation.")

    results["_calibration"] = {"kbar": KBAR, "n_postplant": n_all,
                               "n_applies": len(rows), "alphas": alphas,
                               "side_weights": {str(k): v for k, v in SIDE_WEIGHTS.items()}}
    (out_dir / "additive_on_c.json").write_text(json.dumps(results, indent=2))
    db.rollback()
    db.close()


if __name__ == "__main__":
    main()

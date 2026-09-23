r"""Run from webapp/, read-only:

    DATABASE_URL=... .\.venv313\Scripts\python.exe scripts\postplant_v4_side_on_c.py --out DIR

DECLARATION 9. Ask the side-asymmetry question on target C.

`A1` -- the post-plant factor as a constant PER VICTIM SIDE, at level 0.40 so
`A1 vs F-0.40` isolates the split and nothing else -- was UNTESTABLE on T2:
0.0498% residual variance against that target's demonstrated floor of 0.107%.

Declaration 8 established the floor is a property of the TARGET, not the
harness, and C's demonstrated floor is at most 0.0407%. So the question may be
askable on C. Read the declaration before reading this file.

Three things this script must get right, each of which would silently produce a
meaningless number:

  * `A1` is a PER-FOLD arm. Its weights differ by fold, so it is replayed FIVE
    times and fold f's test-row predictions come from fold f's replay, whose
    weights were fitted on the complement of f. Replaying it once would leak.
  * The weights are reused verbatim from `a1_weights.json`. Legitimate because
    the fitter reads the mean measured swing per victim side off the KILL
    POPULATION and never sees a target, and because the fold split is identical
    (`stable_folds(seed=0)`, fold_mapping_hash cebae50f85e94736).
  * The gate is RECOMPUTED on C's rows, never inherited. `build_target` drops
    rows for T2 (53,730 of 67,251) where C keeps all with a known winner, so
    0.0498% is a property of T2's row set.
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

# C's demonstrated floor, set by F-1.26 registering decisively there
# (declaration 8). T2's is 0.107%. The floor is per-target.
C_FLOOR = 0.000407
WEIGHTS_PATH = (Path(__file__).resolve().parent.parent.parent
                / "docs" / "superpowers" / "postplant-v4" / "a1_weights.json")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


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


def load_a1_weights():
    """Per-fold weights, keys back to the bools `_v_side` indexes with."""
    blob = json.loads(WEIGHTS_PATH.read_text())
    per_fold = {}
    for fold, w in blob["per_fold"].items():
        if any(k.startswith("(") for k in w):
            raise SystemExit("banded (A2) weights found; this script is A1 only")
        per_fold[int(fold)] = {(k == "True"): float(v) for k, v in w.items()}
    return float(blob["level"]), per_fold


def fit_across_folds(X, y, w, mids, folds, per_fold_X=None):
    """Out-of-fold predictions, plus the out-of-fold assembly of the arm's own
    impact_diff column. `per_fold_X` supplies a DIFFERENT design matrix per
    fold, which is what a per-fold arm needs."""
    preds = np.zeros(len(y))
    diff = np.zeros(len(y))
    for fold in range(N_FOLDS):
        Xf = X if per_fold_X is None else per_fold_X[fold]
        te = np.array([folds.get(int(m), -1) == fold for m in mids])
        s_tr, s_te, _c, _s = standardize(Xf[~te], Xf[te])
        beta = fit_logistic(s_tr, y[~te], weights=w[~te], l2=L2)
        preds[te] = predict_proba(beta, s_te)
        diff[te] = Xf[te, 0]
    return preds, diff


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

    level, a1_weights = load_a1_weights()
    log(f"A1 level {level}, per-fold weights {len(a1_weights)} folds")
    for f in sorted(a1_weights):
        log("  fold %d: %s" % (f, ", ".join(
            f"victim_is_attacker={k}: {v:.6f}" for k, v in sorted(a1_weights[f].items()))))

    variants.install()
    oofs, diffs, results = {}, {}, {}
    base = None

    # --- the two single-replay arms -----------------------------------------
    for name in ("P0", "F-0.4"):
        log(f"replaying {name} ...")
        X, y, w, mids = replay(db, None if name == "P0" else variants.variant_for(name))
        if base is None:
            base = (y, w, mids, stable_folds(mids.tolist(), n_folds=N_FOLDS, seed=SEED))
        elif not np.array_equal(mids, base[2]):
            raise SystemExit(f"row misalignment on {name}")
        y, w, mids, folds = base
        preds, diff = fit_across_folds(X, y, w, mids, folds)
        loss = weighted_log_loss(preds, y, w)
        oofs[name] = {"scores": preds, "y": y, "w": w, "match_ids": mids}
        diffs[name] = diff
        results[name] = {"log_loss": float(loss), "n": int(len(y))}
        log(f"  {name}: round-target log loss {loss:.8f}  n={len(y):,}")

    # --- A1, replayed once per fold -----------------------------------------
    y, w, mids, folds = base
    per_fold_X = {}
    for fold in range(N_FOLDS):
        log(f"replaying A1 for fold {fold} (of {N_FOLDS}) ...")
        v = variants.variant_for("A1", level=level, weights=a1_weights[fold])
        Xf, yf, wf, midsf = replay(db, v)
        if not np.array_equal(midsf, mids):
            raise SystemExit(f"row misalignment on A1 fold {fold}")
        per_fold_X[fold] = Xf
    preds, diff = fit_across_folds(None, y, w, mids, folds, per_fold_X=per_fold_X)
    loss = weighted_log_loss(preds, y, w)
    oofs["A1"] = {"scores": preds, "y": y, "w": w, "match_ids": mids}
    diffs["A1"] = diff
    results["A1"] = {"log_loss": float(loss), "n": int(len(y))}
    log(f"  A1: round-target log loss {loss:.8f}  n={len(y):,}")
    variants.uninstall()

    # --- the gate, BEFORE any bootstrap, recomputed on C's rows -------------
    print()
    print("PROTOCOL GATE -- recomputed on C's row set, not inherited from T2")
    print(f"  C's demonstrated floor {C_FLOOR*100:.4f}% (T2's is 0.107%)")
    gate = {}
    for arm, ref in (("A1", "F-0.4"), ("A1", "P0"), ("F-0.4", "P0")):
        r2 = float(np.corrcoef(diffs[arm], diffs[ref])[0, 1]) ** 2
        resid = 1.0 - r2
        below = bool(resid < C_FLOOR)
        gate[f"{arm} vs {ref}"] = {
            "r2": r2, "residual_variance": resid,
            "resid_sd_over_signal_sd": float(np.sqrt(resid)),
            "below_c_floor": below,
        }
        print(f"  {arm:6s} vs {ref:6s}  R2 {r2:.6f}  residual variance {resid*100:7.4f}%  "
              f"{'BELOW C FLOOR -- UNTESTABLE' if below else 'TESTABLE on C'}")
    results["_gate"] = {"c_floor": C_FLOOR, "t2_floor": 0.00107,
                        "t2_recorded_A1_vs_F040": 0.000498, "arms": gate}

    # --- contrasts -----------------------------------------------------------
    print()
    print("DECLARATION 9 -- the side split on the round's own outcome")
    for arm, ref in (("A1", "F-0.4"), ("A1", "P0"), ("F-0.4", "P0")):
        point, lo, hi = paired_oof_log_loss_delta(oofs[arm], oofs[ref], draws=DRAWS)
        verdict = ("INCONCLUSIVE" if lo <= 0 <= hi else
                   ("HARM" if point > 0 else "IMPROVEMENT"))
        key = f"{arm} vs {ref}"
        results[key] = {"point": point, "lo": lo, "hi": hi, "verdict": verdict,
                        "residual_variance": gate[key]["residual_variance"],
                        "below_c_floor": gate[key]["below_c_floor"]}
        note = "  [UNTESTABLE: below C's floor]" if gate[key]["below_c_floor"] else ""
        print(f"  {key:16s} {point:+.6e}  [{lo:+.6e}, {hi:+.6e}]  {verdict}{note}")
    print("  NOTE: C is partly circular by construction -- see declaration 7.")
    print("  NOTE: a favourable A1 is NOT a licence to ship a side-asymmetric")
    print("        factor; see declaration 9's closing paragraph.")

    (out_dir / "side_on_c.json").write_text(json.dumps(results, indent=2))
    db.rollback()
    db.close()


if __name__ == "__main__":
    main()

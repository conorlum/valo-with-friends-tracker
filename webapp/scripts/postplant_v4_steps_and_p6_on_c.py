r"""Run from webapp/, read-only:

    DATABASE_URL=... .\.venv313\Scripts\python.exe scripts\postplant_v4_steps_and_p6_on_c.py --out DIR

DECLARATION 11. Two questions the banded swing table raised, both on target C.

The measured swing of a kill is FLAT to 30s and then collapses -- 17.94 / 19.77 /
20.03 / 13.62 / 2.54 pp across the five bands, an 8x drop from 20-30s to 38-45s
-- while the shipped model pays its MAXIMUM (1.75) in that last window. Every
shape arm tested so far was smooth and RISING. Nothing has tested a cliff DOWN.

  STEP arms  a banded step profile, level-matched so `level` alone sets the
             level and the profile carries only the step. Breaks at 30, 38 and
             41.5 -- 38 is the last moment a full defuse can start, 41.5 the
             last a half-defuse can finish.

  P6 on C    Part 4's (a, d, t, victim_side) state table, the ONLY arm that can
             express the close-vs-decided interaction the per-state table shows
             (2v3 RISES 23.79 -> 36.21pp into 30-38s while 4v2 collapses to
             0.00pp). P6 was a genuine null on T2 at 0.220% separability, but it
             has never been asked on C, whose floor is 0.0407%. Same gap that
             made A1-on-C worth running.

P6 is a PER-FOLD arm and is NOT delivered through the _time_factor wrapper: it
goes through the scorer's own post-plant leverage path via scoring_kwargs, with
tables built on training matches only. Five replays, fold f predicted from fold
f's table.
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
import run_postplant_v4_report as runner

N_FOLDS, SEED, DRAWS, L2 = 5, 0, 2000, 1.0
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff",
                    "full_buy_count_diff"]
FEATURES = ["impact_diff"] + CONTROLS_CONTEXT
C_FLOOR = 0.000407
KILL_MASS = (Path(__file__).resolve().parent.parent.parent
             / "docs" / "superpowers" / "postplant-v4" / "derived_tables.json")

# Band multipliers taken from the MEASURED swing table, normalised to the
# 0-38s kill-weighted mean (18.70pp) or the 0-30s mean (19.06pp) as noted.
#   0-10 17.94 | 10-20 19.77 | 20-30 20.03 | 30-38 13.62 | 38-45 2.54
STEP_PROFILES = {
    # one cliff, at the last moment a full defuse can start
    "STEP38@1.6":   [(0.0, 1.0), (38.0, 0.136)],
    # the same cliff moved to the last moment a half-defuse can finish
    "STEP41.5@1.6": [(0.0, 1.0), (41.5, 0.136)],
    # the measured profile: the decline starts at 30, not 38
    "STEP30@1.6":   [(0.0, 1.0), (30.0, 0.715), (38.0, 0.133)],
}
STEP_LEVEL = 1.6
FLATS = ["F-1.26", "F-1.6"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def kill_weighted_norm(profile):
    """Kill-weighted mean of the profile over the post-plant kill population,
    so `level` alone sets the level and the profile carries only the shape."""
    km = json.loads(KILL_MASS.read_text())["kill_mass_by_second"]
    num = den = 0.0
    for sec, n in km.items():
        t = float(sec)
        mult = profile[0][1]
        for t_lo, m in profile:
            if t >= t_lo:
                mult = m
        num += mult * n
        den += n
    return num / den


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


def replay(db, variant=None, scoring_kwargs=None):
    variants.activate(variant)
    try:
        obs = load_all_observations(db, scoring_kwargs=scoring_kwargs or {})
    finally:
        variants.activate(None)
        db.rollback()
    return round_dataset(obs)


def fit_oof(X, y, w, mids, folds, per_fold_X=None):
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

    norms = {k: kill_weighted_norm(p) for k, p in STEP_PROFILES.items()}
    for k, n in norms.items():
        log(f"{k}: kill-weighted norm {n:.6f}  (level held at {STEP_LEVEL})")

    variants.install()
    oofs, diffs, results = {}, {}, {}
    base = None

    single = ["P0"] + FLATS + list(STEP_PROFILES)
    for name in single:
        if name == "P0":
            v = None
        elif name in STEP_PROFILES:
            v = variants.variant_for("STEP", level=STEP_LEVEL,
                                     profile=STEP_PROFILES[name], norm=norms[name])
        else:
            v = variants.variant_for(name)
        log(f"replaying {name} ...")
        X, y, w, mids = replay(db, v)
        if base is None:
            base = (y, w, mids, stable_folds(mids.tolist(), n_folds=N_FOLDS, seed=SEED))
        elif not np.array_equal(mids, base[2]):
            raise SystemExit(f"row misalignment on {name}")
        y, w, mids, folds = base
        preds, diff = fit_oof(X, y, w, mids, folds)
        loss = weighted_log_loss(preds, y, w)
        oofs[name], diffs[name] = ({"scores": preds, "y": y, "w": w,
                                    "match_ids": mids}, diff)
        results[name] = {"log_loss": float(loss), "n": int(len(y))}
        log(f"  {name}: round-target log loss {loss:.8f}")
    variants.uninstall()

    # --- P6: per-fold tables, through the scorer's own post-plant path -------
    y, w, mids, folds = base
    log("P6: extracting post-plant round-seconds and kills ...")
    all_seconds = runner.extract_postplant_round_seconds(db)
    all_kills = runner.extract_postplant_kills(db)
    log(f"  {len(all_seconds):,} round-seconds, {len(all_kills):,} kills")
    log("P6: building per-fold tables (training matches only) ...")
    tables, centring = runner.build_fold_tables(all_seconds, all_kills, folds,
                                                N_FOLDS, "P6")
    per_fold_X = {}
    for fold in range(N_FOLDS):
        log(f"P6: replaying fold {fold} (of {N_FOLDS}) ...")
        Xf, _y, _w, midsf = replay(db, None, scoring_kwargs={
            "enable_postplant_leverage": True,
            "postplant_factor_table": tables[fold],
        })
        if not np.array_equal(midsf, mids):
            raise SystemExit(f"row misalignment on P6 fold {fold}")
        per_fold_X[fold] = Xf
    preds, diff = fit_oof(None, y, w, mids, folds, per_fold_X=per_fold_X)
    loss = weighted_log_loss(preds, y, w)
    oofs["P6"], diffs["P6"] = ({"scores": preds, "y": y, "w": w,
                                "match_ids": mids}, diff)
    results["P6"] = {"log_loss": float(loss), "n": int(len(y))}
    log(f"  P6: round-target log loss {loss:.8f}")
    results["_p6_centring"] = {str(f): (None if r is None else r.c)
                               for f, r in centring.items()}

    pairs = [(s, "F-1.6") for s in STEP_PROFILES]
    pairs += [("STEP41.5@1.6", "STEP38@1.6"), ("STEP30@1.6", "STEP38@1.6")]
    pairs += [("P6", "F-1.6"), ("P6", "F-1.26"), ("P6", "P0")]
    pairs += [(s, "P0") for s in STEP_PROFILES] + [(f, "P0") for f in FLATS]

    print()
    print("PROTOCOL GATE -- separability, before any bootstrap")
    print(f"  C's demonstrated floor {C_FLOOR*100:.4f}%")
    gate = {}
    for arm, ref in pairs:
        r2 = float(np.corrcoef(diffs[arm], diffs[ref])[0, 1]) ** 2
        resid = 1.0 - r2
        gate[f"{arm} vs {ref}"] = {"r2": r2, "residual_variance": resid,
                                   "below_c_floor": bool(resid < C_FLOOR)}
        print(f"  {arm:14s} vs {ref:12s}  R2 {r2:.6f}  resid var {resid*100:8.4f}%  "
              f"{'BELOW FLOOR -- UNTESTABLE' if resid < C_FLOOR else 'testable'}")
    results["_gate"] = {"c_floor": C_FLOOR, "arms": gate}

    print()
    print("DECLARATION 11 -- a cliff DOWN, and Part 4 on the target that can hear it")
    for arm, ref in pairs:
        point, lo, hi = paired_oof_log_loss_delta(oofs[arm], oofs[ref], draws=DRAWS)
        verdict = ("INCONCLUSIVE" if lo <= 0 <= hi else
                   ("HARM" if point > 0 else "IMPROVEMENT"))
        key = f"{arm} vs {ref}"
        results[key] = {"point": point, "lo": lo, "hi": hi, "verdict": verdict,
                        "residual_variance": gate[key]["residual_variance"],
                        "below_c_floor": gate[key]["below_c_floor"]}
        note = "  [UNTESTABLE]" if gate[key]["below_c_floor"] else ""
        print(f"  {key:30s} {point:+.6e}  [{lo:+.6e}, {hi:+.6e}]  {verdict}{note}")
    print("  NOTE: C is partly circular -- declaration 7.")

    results["_norms"] = norms
    results["_profiles"] = {k: [list(x) for x in p] for k, p in STEP_PROFILES.items()}
    (out_dir / "steps_and_p6_on_c.json").write_text(json.dumps(results, indent=2))
    db.rollback()
    db.close()


if __name__ == "__main__":
    main()

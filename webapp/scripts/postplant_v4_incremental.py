r"""Run from webapp/, read-only:

    DATABASE_URL=... .\.venv313\Scripts\python.exe scripts\postplant_v4_incremental.py \
        --out DIR --base F-0.4 --extra A1

The measurement declared in docs/superpowers/2026-09-07-predeclared-values.md,
entry "2026-09-20 -- DECLARATION 5". Read it first.

WHY THIS EXISTS. The paired-loss contrast in run_postplant_v4_report.py scores
each arm as a fixed composite with ONE free coefficient, which makes weighted log
loss invariant to an affine rescale of that composite. A1 turned out to be
`0.99672 * F-0.40 - 2.53` (R^2 0.999502), so the two arms make near-identical
predictions and their loss difference is zero by construction -- the test could
not see the arm, and INCONCLUSIVE there meant "not asked", not "does not help".

This asks the question that test could not: does `impact_diff` under the extra
arm earn a non-zero coefficient BEYOND the base arm's? Two nested models,

    baseline     y ~ impact_diff[base]                      + controls
    incremental  y ~ impact_diff[base] + impact_diff[extra] + controls

so the second column is fitted on exactly the residual the paired comparison
discarded. It does not violate the fixed-composite rule of econ spec 8d-i:
both terms are frozen scoring configurations and nothing searches over the
owner's locked A/B/C/D weights.

The two arms' observations must describe the SAME rounds in the SAME order --
they come from one corpus replayed twice -- and that is asserted, not assumed,
because a silent misalignment would fabricate an incremental signal out of row
shuffling.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.services.impact_eval import (
    PRIMARY_T2,
    FitDataset,
    build_target,
    controls_for,
    load_all_observations,
    paired_oof_log_loss_delta,
    split_observations,
    stable_folds,
)
from app.services.stats_math import weighted_log_loss
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import postplant_v4_variants as variants

COMPOSITE = "impact_diff"
L2_GRID = (0.1, 1.0, 10.0)
N_FOLDS, INNER_FOLDS, SEED, DRAWS = 5, 3, 0, 2000

# The arms this test knows how to build. Kept explicit so a typo names nothing
# rather than silently replaying the shipped configuration.
ARM_WEIGHTS = {"A1": {True: 1.2539, False: 0.7873}}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build_variant(name, db):
    if name.startswith("F-") or name.startswith("L-") or name in ("P1", "P2b", "P3a", "P3b", "PC"):
        return variants.variant_for(name)
    if name in ARM_WEIGHTS:
        return variants.variant_for(name, level=0.40, weights=ARM_WEIGHTS[name])
    raise SystemExit(f"unknown arm {name!r}")


def _fit(X_tr, y_tr, w_tr, X_te, l2):
    from app.services.stats_math import fit_logistic, predict_proba, standardize
    s_tr, s_te, _c, _s = standardize(X_tr, X_te)
    beta = fit_logistic(s_tr, y_tr, weights=w_tr, l2=l2)
    return predict_proba(beta, s_te), beta


def _select_l2(X, y, w, mids, inner_folds):
    best, best_loss = L2_GRID[0], float("inf")
    for l2 in L2_GRID:
        tot, wt = 0.0, 0.0
        for f in range(INNER_FOLDS):
            te = np.array([inner_folds.get(int(m), -1) == f for m in mids])
            if te.sum() == 0 or (~te).sum() == 0:
                continue
            p, _ = _fit(X[~te], y[~te], w[~te], X[te], l2)
            weight = float(w[te].sum())
            tot += weighted_log_loss(p, y[te], w[te]) * weight
            wt += weight
        if wt and tot / wt < best_loss:
            best_loss, best = tot / wt, l2
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--extra", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    variants.install()

    def replay(name):
        log(f"replaying {name} ...")
        variants.activate(build_variant(name, db))
        try:
            return load_all_observations(db)
        finally:
            variants.activate(None)
            db.rollback()

    base_obs = replay(args.base)
    extra_obs = replay(args.extra)
    variants.uninstall()
    db.close()

    # Alignment is asserted, never assumed: a silent row misalignment would
    # manufacture incremental signal out of nothing.
    assert len(base_obs) == len(extra_obs), "arms describe different row counts"
    for a, b in zip(base_obs, extra_obs):
        assert a.match_id == b.match_id and a.round_id == b.round_id, "row misalignment"
    log(f"{len(base_obs):,} observations, alignment verified")

    features = [COMPOSITE] + controls_for(PRIMARY_T2)
    folds = stable_folds([o.match_id for o in base_obs], n_folds=N_FOLDS, seed=SEED)

    oof = {k: {"scores": [], "y": [], "w": [], "match_ids": [], "baseline": []}
           for k in ("base", "incr")}
    coefs = {}
    for fold in range(N_FOLDS):
        b_tr, b_te = split_observations(base_obs, folds, fold)
        e_tr, e_te = split_observations(extra_obs, folds, fold)
        d_b_tr = build_target(b_tr, PRIMARY_T2, features)
        d_b_te = build_target(b_te, PRIMARY_T2, features)
        d_e_tr = build_target(e_tr, PRIMARY_T2, features)
        d_e_te = build_target(e_te, PRIMARY_T2, features)
        assert np.allclose(d_b_tr.y, d_e_tr.y) and np.array_equal(
            d_b_tr.match_ids, d_e_tr.match_ids), "target built differently per arm"

        inner = stable_folds(sorted({int(m) for m in d_b_tr.match_ids}),
                             n_folds=INNER_FOLDS, seed=SEED)
        X_tr = {"base": d_b_tr.X,
                "incr": np.hstack([d_b_tr.X, d_e_tr.X[:, [0]]])}
        X_te = {"base": d_b_te.X,
                "incr": np.hstack([d_b_te.X, d_e_te.X[:, [0]]])}
        for model in ("base", "incr"):
            l2 = _select_l2(X_tr[model], d_b_tr.y, d_b_tr.w, d_b_tr.match_ids, inner)
            preds, beta = _fit(X_tr[model], d_b_tr.y, d_b_tr.w, X_te[model], l2)
            o = oof[model]
            o["scores"].extend(preds.tolist())
            o["y"].extend(d_b_te.y.tolist())
            o["w"].extend(d_b_te.w.tolist())
            o["match_ids"].extend(d_b_te.match_ids.tolist())
            o["baseline"].extend([float(np.average(d_b_tr.y, weights=d_b_tr.w))] * len(d_b_te.y))
            if model == "incr":
                # predict_proba computes beta[0] + X @ beta[1:], so beta[0] is
                # the INTERCEPT: feature coefficients start at 1, and the extra
                # arm's column -- appended last -- is beta[-1].
                coefs[fold] = {"intercept": float(beta[0]),
                               "base_composite": float(beta[1]),
                               "extra_composite": float(beta[-1]), "l2": l2}
                log(f"  fold {fold}: beta[base]={beta[1]:+.4f}  "
                    f"beta[extra]={beta[-1]:+.4f}  l2={l2}")

    for k in oof:
        oof[k] = {kk: np.array(vv) for kk, vv in oof[k].items()}
    point, lo, hi = paired_oof_log_loss_delta(oof["incr"], oof["base"], draws=DRAWS)
    verdict = ("INCONCLUSIVE" if lo <= 0 <= hi else
               ("HARM" if point > 0 else "IMPROVEMENT"))
    signs = {np.sign(c["extra_composite"]) for c in coefs.values()}
    stable = len(signs) == 1

    print()
    print("=" * 74)
    print(f"INCREMENTAL VALUE of {args.extra} beyond {args.base}")
    print("=" * 74)
    print(f"  loss(base + {args.extra}) - loss(base):")
    print(f"    {point:+.6e}  [{lo:+.6e}, {hi:+.6e}]  {verdict}")
    print(f"  coefficient on {args.extra}'s column, per fold: "
          + ", ".join(f"{c['extra_composite']:+.4f}" for c in coefs.values()))
    print(f"  coefficient on {args.base}'s column,  per fold: "
          + ", ".join(f"{c['base_composite']:+.4f}" for c in coefs.values()))
    print(f"  sign stable across folds: {'YES' if stable else 'NO -- this is noise being fitted'}")
    result = {"base": args.base, "extra": args.extra, "point": point, "lo": lo,
              "hi": hi, "verdict": verdict, "sign_stable": stable,
              "per_fold": {str(k): v for k, v in coefs.items()}}
    (out_dir / f"incremental_{args.extra}_over_{args.base}.json").write_text(
        json.dumps(result, indent=2))
    print(f"  written to incremental_{args.extra}_over_{args.base}.json")


if __name__ == "__main__":
    main()

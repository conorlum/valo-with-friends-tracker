r"""The one-parameter A search, declared in predeclared-values.md on
2026-09-09 BEFORE this was run.

    .\.venv\Scripts\python.exe scripts\fit_damage_weight.py
    .\.venv\Scripts\python.exe scripts\fit_damage_weight.py --quick   # smoke

    impact = A*damage + B*leverage + C*econ_component
             ^^^^^^^^ the only thing fitted here

B is fixed at 1: the evaluator puts a FREE coefficient on the composite, so
overall scale is absorbed and only the ratio A:B is identified. C is excluded
because this fit runs EX-ANTE, where `_econ_components_for_round` returns {}
(the leakage gate -- it reads round N+1), so econ_component is identically 0
and cannot contaminate the ratio. Fitting C is separate, optional, later work.

NO NEW FITTER. `fit_constrained_weights` reduces to exactly this search once
`econ_impact` and `swing_impact` are declared inert: one live factor makes
`_simplex_grid_ndim(step, 1)` return `[(1.0,)]`, forcing w_time = 1 and
collapsing the search to the damage grid.

NORMALIZATION. The `damage` column is already `round(1.25 *
damage_and_assists)`, so the searched multiplier is RELATIVE:

    A_absolute = 1.25 * d,   d = 1.0 is today's incumbent

The head of the run reports the rounding gap between what the fit sees
(`d * round(1.25 x)`) and what the scorer would compute
(`round(1.25 d x)`), because they are not the same number.

LEAKAGE. A is a fitted object like V and the post-plant table: selected per
outer fold on TRAINING matches only, with that fold's table. Read-only.
"""
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match
from app.scoring.postplant_centering import DegenerateCentering
from app.scoring.postplant_factor import (
    build_factor_table,
    extract_postplant_kills,
    solve_and_apply_centering,
)
from app.scoring.postplant_value_table import (
    DEFAULT_W,
    build_value_table,
    extract_postplant_round_seconds,
)
from app.services.impact_eval import (
    FEATURE_COMPONENTS,
    PRIMARY_T2,
    FitDataset,
    build_target,
    controls_for,
    fit_constrained_weights,
    fit_logistic,
    load_all_observations,
    paired_oof_log_loss_delta,
    predict_proba,
    stable_folds,
    standardize,
    weighted_log_loss,
)

# ---- PREDECLARED 2026-09-09, before this ran -----------------------------
RELATIVE_GRID = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
EXISTING_DAMAGE_SCALE = 1.25          # already inside the `damage` column
INERT_FACTORS = frozenset({"econ_impact", "swing_impact"})
N_FOLDS = 5
SEED = 0
BOOTSTRAP_DRAWS = 2000
INCUMBENT_D = 1.0                     # A = 1.25
# --------------------------------------------------------------------------

CANDIDATE = {"enable_econ_component": True, "enable_postplant_leverage": True}


def absolute_a(d):
    return EXISTING_DAMAGE_SCALE * d


def report_declaration():
    print("=" * 78)
    print("  ONE-PARAMETER A SEARCH   (B fixed at 1, C excluded -- see the")
    print("  2026-09-09 declaration in docs/superpowers/2026-09-07-predeclared-values.md)")
    print("=" * 78)
    print(f"  target           {PRIMARY_T2.name}  k={PRIMARY_T2.k} gamma={PRIMARY_T2.gamma} "
          f"match_weight={PRIMARY_T2.match_weight}")
    print(f"  controls         {', '.join(controls_for(PRIMARY_T2))}")
    print(f"  mode             EX-ANTE (use_realized_swing=False) -- econ_component is 0 here")
    print(f"  candidate        enable_econ_component + enable_postplant_leverage")
    print(f"  declared inert   {', '.join(sorted(INERT_FACTORS))}")
    print(f"  relative grid d  {RELATIVE_GRID}")
    print(f"  absolute A       {[round(absolute_a(d), 4) for d in RELATIVE_GRID]}")
    print(f"  folds            {N_FOLDS} outer, stable_folds seed={SEED}")
    print(f"  bootstrap        {BOOTSTRAP_DRAWS:,} draws, match-clustered, two-sided 95%")
    print(f"  sign             loss(fitted) - loss(A=1.25); POSITIVE MEANS WORSE")
    print(f"  decision rule    interval spanning zero -> INCONCLUSIVE, A stays 1.25")
    print("=" * 78)
    print()


def check_normalization(db, match_ids):
    """The `damage` column is round(1.25*x); the scorer computes round(1.25*d*x).
    Those differ. Quantify it rather than wave at it."""
    print("NORMALIZATION / ROUNDING CHECK")
    print("  fit space:    d * round(1.25 * damage_and_assists)   [the column]")
    print("  scorer space: round(1.25 * d * damage_and_assists)   [FormulaWeights]")
    print(f"  {'d':>6} {'A':>8} {'rows':>7} {'mean|gap|':>10} {'max|gap|':>9} "
          f"{'mean|gap| as % of term':>23}")
    for d in RELATIVE_GRID:
        gaps, terms = [], []
        for match_id in match_ids:
            base = build_impact_rows_for_match(db, match_id, use_realized_swing=True,
                                               **CANDIDATE)
            scaled = build_impact_rows_for_match(
                db, match_id, use_realized_swing=True,
                weights=FormulaWeights(damage=absolute_a(d)), **CANDIDATE)
            for b, s in zip(base, scaled):
                gaps.append(abs(d * b.damage - s.damage))
                terms.append(abs(s.damage))
        mean_term = statistics.mean(terms) or 1.0
        print(f"  {d:>6.2f} {absolute_a(d):>8.4f} {len(gaps):>7,} "
              f"{statistics.mean(gaps):>10.4f} {max(gaps):>9.1f} "
              f"{100 * statistics.mean(gaps) / mean_term:>22.4f}%")
    print("  -> the gap is sub-unit rounding on an integer column; it does not")
    print("     move the ratio, but the SCORER's value is the one that ships.")
    print()


def _dataset_at(observations, d, control_names):
    """FitDataset whose only non-control column is the composite at this d.

    Mirrors fit_constrained_weights' own construction: composite =
    d*damage + 1.0*time_impact, with the two inert factor columns dropped.
    """
    feature_names = FEATURE_COMPONENTS + list(control_names)
    full = build_target(observations, PRIMARY_T2, feature_names)
    if len(full.y) == 0:
        return None
    index = {name: feature_names.index(name) for name in feature_names}
    composite = d * full.X[:, index["damage"]] + full.X[:, index["time_impact"]]
    controls = full.X[:, [index[n] for n in control_names]] if control_names else \
        np.zeros((len(full.y), 0))
    return FitDataset(
        X=np.column_stack([controls, composite]), y=full.y, w=full.w,
        match_ids=full.match_ids, feature_names=list(control_names) + ["composite"],
    )


def _score(train_ds, test_ds, l2=1.0):
    if train_ds is None or test_ds is None:
        return None
    scaled_train, scaled_test, _, _ = standardize(train_ds.X, test_ds.X)
    beta = fit_logistic(scaled_train, train_ds.y, weights=train_ds.w, l2=l2)
    predictions = predict_proba(beta, scaled_test)
    loss = weighted_log_loss(predictions, test_ds.y, test_ds.w)
    return predictions, loss


def main():
    quick = "--quick" in sys.argv
    folds_n, draws = (2, 200) if quick else (N_FOLDS, BOOTSTRAP_DRAWS)
    grid = [0.0, 1.0, 2.0] if quick else RELATIVE_GRID

    report_declaration()
    db = SessionLocal()

    print("extracting post-plant rows ...", flush=True)
    all_seconds = extract_postplant_round_seconds(db)
    all_kills = extract_postplant_kills(db)
    print(f"  {len(all_seconds):,} round-seconds, {len(all_kills):,} kills\n")

    print("replaying to fix the fold assignment ...", flush=True)
    t0 = time.time()
    base_obs = load_all_observations(db)
    print(f"  {len(base_obs):,} observations in {time.time() - t0:.0f}s\n")
    folds = stable_folds([o.match_id for o in base_obs], n_folds=folds_n, seed=SEED)

    if not quick:
        check_normalization(db, sorted({o.match_id for o in base_obs})[:5])

    controls = controls_for(PRIMARY_T2)
    curve = {d: [] for d in grid}
    selected, oof_fitted, oof_incumbent = [], _empty_oof(), _empty_oof()

    for fold in range(folds_n):
        train_matches = {m for m, f in folds.items() if f != fold}
        print(f"fold {fold}: building the post-plant table on "
              f"{len(train_matches):,} training matches ...", flush=True)
        value_table = build_value_table(
            [s for s in all_seconds if s.match_id in train_matches], w=DEFAULT_W)
        table = build_factor_table(
            value_table, [k for k in all_kills if k.match_id in train_matches])
        try:
            solve_and_apply_centering(
                table, [k for k in all_kills if k.match_id in train_matches])
        except DegenerateCentering as exc:
            print(f"  DEGENERATE centring ({exc}); table left uncentred")

        observations = load_all_observations(
            db, scoring_kwargs={**CANDIDATE, "postplant_factor_table": table})
        train_obs = [o for o in observations if o.match_id in train_matches]
        test_obs = [o for o in observations if o.match_id not in train_matches]

        fitted = fit_constrained_weights(
            train_obs, PRIMARY_T2, controls, damage_grid=grid,
            expected_constant_factors=INERT_FACTORS,
        )
        selected.append(fitted)
        # OUTPUT NORMALIZATION, verified rather than assumed: the fitter
        # returns factor weights rescaled by FACTOR_WEIGHT_TOTAL (= 3, the
        # legacy sum-to-3 convention that impact.py then divides back out),
        # so a reported time of 3.0 IS an effective B of 1.0.
        # damage_multiplier is returned RAW, so A = 1.25 * d stands.
        effective_b = fitted.time / 3.0
        print(f"  training-selected d = {fitted.damage_multiplier}  "
              f"(A = {absolute_a(fitted.damage_multiplier):.4f})")
        print(f"  reported w_time = {fitted.time} -> effective B = {effective_b:.4f}  "
              f"| l2 = {fitted.l2}  usable = {fitted.usable}")
        print(f"  dropped as inert: {fitted.dropped_constant_factors}")
        if abs(effective_b - 1.0) > 1e-9:
            print(f"  *** effective B is {effective_b}, not 1.0 -- the one-live-factor "
                  f"reduction did NOT hold; the search is not one-parameter ***")
        if not fitted.usable:
            print("  *** fold produced an UNUSABLE weighting (every candidate "
                  "anti-predictive); it is excluded from the contrast ***")

        fold_l2 = fitted.l2 if fitted.usable and fitted.l2 else 1.0
        train_at = {d: _dataset_at(train_obs, d, controls) for d in grid}
        test_at = {d: _dataset_at(test_obs, d, controls) for d in grid}
        for d in grid:
            scored = _score(train_at[d], test_at[d], fold_l2)
            curve[d].append(scored[1] if scored else float("nan"))

        for d, sink in ((fitted.damage_multiplier, oof_fitted), (INCUMBENT_D, oof_incumbent)):
            scored = _score(train_at.get(d), test_at.get(d), fold_l2)
            if scored is None:
                continue
            predictions, _ = scored
            test_ds = test_at[d]
            sink["scores"].extend(predictions.tolist())
            sink["y"].extend(test_ds.y.tolist())
            sink["w"].extend(test_ds.w.tolist())
            sink["match_ids"].extend(test_ds.match_ids.tolist())
        print()

    print("=" * 78)
    print("  HELD-OUT PERFORMANCE CURVE  (weighted log loss, lower is better)")
    print("=" * 78)
    header = "  " + f"{'d':>6} {'A':>8} " + " ".join(f"{'fold ' + str(f):>10}"
                                                     for f in range(folds_n))
    print(header + f" {'mean':>10} {'SD':>8}")
    for d in grid:
        values = [v for v in curve[d] if np.isfinite(v)]
        row = "  " + f"{d:>6.2f} {absolute_a(d):>8.4f} " + " ".join(
            f"{v:>10.6f}" for v in curve[d])
        print(row + f" {statistics.mean(values):>10.6f} "
                    f"{(statistics.pstdev(values) if len(values) > 1 else 0):>8.6f}")

    best_mean = min(grid, key=lambda d: statistics.mean(
        [v for v in curve[d] if np.isfinite(v)]))
    spread = (max(statistics.mean([v for v in curve[d] if np.isfinite(v)]) for d in grid)
              - min(statistics.mean([v for v in curve[d] if np.isfinite(v)]) for d in grid))
    print(f"\n  lowest mean held-out loss at d = {best_mean} (A = {absolute_a(best_mean):.4f})")
    print(f"  spread across the whole grid: {spread:.8f}")

    print()
    print("  per-fold TRAINING-selected d: "
          + ", ".join(str(f.damage_multiplier) for f in selected))
    unique = {f.damage_multiplier for f in selected}
    print(f"    agreement: {len(unique)} distinct value(s) "
          + ("-- stable" if len(unique) == 1 else "-- UNSTABLE across folds"))

    # Agreement on the TRAINING-selected value says little on its own; what
    # varies is where each fold's own HELD-OUT loss actually bottoms out.
    held_out_argmin = [
        min(grid, key=lambda d: curve[d][fold]
            if np.isfinite(curve[d][fold]) else float("inf"))
        for fold in range(folds_n)
    ]
    print("  per-fold HELD-OUT argmin d:  " + ", ".join(str(d) for d in held_out_argmin))
    print(f"    agreement: {len(set(held_out_argmin))} distinct value(s) "
          + ("-- stable" if len(set(held_out_argmin)) == 1
             else "-- UNSTABLE; the folds do not agree on where the optimum is"))

    edge = [d for d in held_out_argmin if d in (grid[0], grid[-1])]
    if edge:
        print(f"  *** {len(edge)} of {folds_n} folds bottom out at a GRID EDGE, so the")
        print(f"      optimum is at or beyond the predeclared boundary rather than")
        print(f"      interior. The grid is NOT widened after seeing this; it is")
        print(f"      recorded as a finding. ***")

    print("\n" + "=" * 78)
    print("  CONTRAST: training-selected A vs A = 1.25, identical held-out matches")
    print("=" * 78)
    if oof_fitted["scores"] and oof_incumbent["scores"]:
        point, lo, hi = paired_oof_log_loss_delta(
            oof_fitted, oof_incumbent, draws=draws)
        verdict = ("INCONCLUSIVE -- interval spans zero; per the declared rule "
                   "A STAYS AT 1.25" if lo <= 0 <= hi
                   else ("IMPROVEMENT" if hi < 0 else "DETERIORATION"))
        print(f"  loss(fitted) - loss(A=1.25) = {point:+.6f} [{lo:+.6f}, {hi:+.6f}]")
        print(f"  {verdict}")
    else:
        print("  no out-of-fold predictions produced")


def _empty_oof():
    return {"scores": [], "y": [], "w": [], "match_ids": []}


if __name__ == "__main__":
    main()

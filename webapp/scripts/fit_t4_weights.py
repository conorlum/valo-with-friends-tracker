r"""T4: fit A and C (B fixed at 1) against the 4/2/1 round target.

    .\.venv\Scripts\python.exe scripts\fit_t4_weights.py
    .\.venv\Scripts\python.exe scripts\fit_t4_weights.py --quick

Declared in predeclared-values.md on 2026-09-09 BEFORE this ran, together
with a falsifiable PREDICTION about what it would return.

THE TARGET, at the project owner's direction:

    y = weighted mean of "team A won", over
        round N    weight 4/7      <-- the round the components were scored in
        round N+1  weight 2/7
        round N+2  weight 1/7

`round_result` LEAVES the controls -- it is the label now. Controls are
CONTROLS_CONTEXT only.

READ THIS BEFORE QUOTING ANY NUMBER FROM HERE. The rest of the evaluation
design excludes round N deliberately: a round's own kills are, near
deterministically, that round's outcome. Measured against round N,
leverage correlates at 0.94, damage at 0.85, econ_component at 0.55. A fit
whose label is 57% round N can therefore rank the three terms by how
directly each RESTATES the outcome rather than by what each is worth. The
prediction recorded before the run says exactly that will happen; if it
does, this fit is an artifact of its target and must not be adopted
however tight its interval.

Three parameters, two identified: the evaluator puts a free coefficient on
the composite, so scale is absorbed. B = 1, and the search is a 2-D grid
over (A, C). REALIZED mode, or econ_component is identically 0.

Read-only.
"""
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
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
    CONTROLS_CONTEXT,
    FitDataset,
    _feature_value,
    _half_of,
    fit_logistic,
    group_by_match,
    load_all_observations,
    paired_oof_log_loss_delta,
    predict_proba,
    stable_folds,
    standardize,
    weighted_log_loss,
)

# ---- PREDECLARED 2026-09-09, before this ran -----------------------------
ROUND_WEIGHTS = {0: 4.0 / 7.0, 1: 2.0 / 7.0, 2: 1.0 / 7.0}
A_GRID = [0.0, 0.3125, 0.625, 0.9375, 1.25, 1.5625, 1.875, 2.5, 3.75, 5.0]
C_GRID = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
INCUMBENT = (1.25, 1.0)
CONTROLS = list(CONTROLS_CONTEXT)          # round_result is the LABEL now
N_FOLDS, SEED, DRAWS = 5, 0, 2000
COMPONENTS = ["damage", "time_impact", "econ_component"]
# --------------------------------------------------------------------------

CANDIDATE = {"enable_econ_component": True, "enable_postplant_leverage": True}


def t4_target(observations):
    """One row per source round: features from round N, label a 4/2/1
    weighted mean of rounds N, N+1, N+2. Windows stop at the half boundary
    exactly as T2's do; round N itself is always available."""
    names = COMPONENTS + CONTROLS
    rows, ys, ws, mids = [], [], [], []
    for _, obs in group_by_match(observations).items():
        by_number = {o.round_number: o for o in obs}
        for o in obs:
            numerator = denominator = 0.0
            for step, weight in ROUND_WEIGHTS.items():
                future = o if step == 0 else by_number.get(o.round_number + step)
                if future is None:
                    break
                if step and _half_of(future.round_number) != _half_of(o.round_number):
                    break
                if future.round_won_by_team_a is None:
                    continue
                numerator += weight * (1.0 if future.round_won_by_team_a else 0.0)
                denominator += weight
            if denominator == 0:
                continue
            rows.append([_feature_value(o, n) for n in names])
            ys.append(numerator / denominator)
            ws.append(denominator)
            mids.append(o.match_id)
    return FitDataset(np.asarray(rows, dtype=float), np.asarray(ys, dtype=float),
                      np.asarray(ws, dtype=float), np.asarray(mids), names)


def _composite_dataset(full: FitDataset, a, c):
    index = {n: full.feature_names.index(n) for n in full.feature_names}
    composite = (a * full.X[:, index["damage"]]
                 + 1.0 * full.X[:, index["time_impact"]]
                 + c * full.X[:, index["econ_component"]])
    controls = full.X[:, [index[n] for n in CONTROLS]]
    return FitDataset(np.column_stack([controls, composite]), full.y, full.w,
                      full.match_ids, CONTROLS + ["composite"])


def _score(train, test, a, c, l2=1.0):
    tr, te = _composite_dataset(train, a, c), _composite_dataset(test, a, c)
    if tr.X[:, -1].std() == 0:
        return None
    scaled_train, scaled_test, _, _ = standardize(tr.X, te.X)
    beta = fit_logistic(scaled_train, tr.y, weights=tr.w, l2=l2)
    if beta[-1] <= 0:          # anti-predictive weighting; refuse it
        return None
    predictions = predict_proba(beta, scaled_test)
    return predictions, weighted_log_loss(predictions, te.y, te.w), te


def main():
    quick = "--quick" in sys.argv
    folds_n, draws = (2, 200) if quick else (N_FOLDS, DRAWS)
    a_grid = [0.0, 1.25, 5.0] if quick else A_GRID
    c_grid = [0.0, 1.0, 3.0] if quick else C_GRID

    print("=" * 78)
    print("  T4 -- the 4/2/1 target.  round N 4/7, N+1 2/7, N+2 1/7")
    print("  B fixed at 1; 2-D grid over (A, C); REALIZED mode")
    print(f"  controls: {', '.join(CONTROLS)}   [round_result is the LABEL]")
    print("  PREDICTION on record: this resolves, C -> bottom of grid, A -> down,")
    print("  surface far steeper than the A search. If so it is an ARTIFACT.")
    print("=" * 78)

    db = SessionLocal()
    print("\nextracting post-plant rows ...", flush=True)
    all_seconds = extract_postplant_round_seconds(db)
    all_kills = extract_postplant_kills(db)

    print("replaying to fix folds ...", flush=True)
    t0 = time.time()
    base = load_all_observations(db, use_realized_swing=True)
    print(f"  {len(base):,} observations in {time.time() - t0:.0f}s")
    folds = stable_folds([o.match_id for o in base], n_folds=folds_n, seed=SEED)

    curve = {(a, c): [] for a in a_grid for c in c_grid}
    picks, oof_fit, oof_inc = [], _oof(), _oof()

    for fold in range(folds_n):
        train_matches = {m for m, f in folds.items() if f != fold}
        print(f"\nfold {fold}: table on {len(train_matches):,} training matches ...",
              flush=True)
        table = build_factor_table(
            build_value_table([s for s in all_seconds if s.match_id in train_matches],
                              w=DEFAULT_W),
            [k for k in all_kills if k.match_id in train_matches])
        try:
            solve_and_apply_centering(
                table, [k for k in all_kills if k.match_id in train_matches])
        except DegenerateCentering as exc:
            print(f"  DEGENERATE centring ({exc})")

        observations = load_all_observations(
            db, use_realized_swing=True,
            scoring_kwargs={**CANDIDATE, "postplant_factor_table": table})
        train = t4_target([o for o in observations if o.match_id in train_matches])
        test = t4_target([o for o in observations if o.match_id not in train_matches])

        # TRAINING selection: fit and score on train only.
        best, best_loss = None, float("inf")
        for a in a_grid:
            for c in c_grid:
                scored = _score(train, train, a, c)
                if scored and scored[1] < best_loss:
                    best, best_loss = (a, c), scored[1]
        picks.append(best)
        print(f"  training-selected (A, C) = {best}")

        for a in a_grid:
            for c in c_grid:
                scored = _score(train, test, a, c)
                curve[(a, c)].append(scored[1] if scored else float("nan"))

        for pick, sink in ((best, oof_fit), (INCUMBENT, oof_inc)):
            scored = _score(train, test, *pick)
            if not scored:
                continue
            predictions, _, te = scored
            sink["scores"].extend(predictions.tolist())
            sink["y"].extend(te.y.tolist())
            sink["w"].extend(te.w.tolist())
            sink["match_ids"].extend(te.match_ids.tolist())

    print("\n" + "=" * 78)
    print("  HELD-OUT LOSS SURFACE (mean over folds; rows A, columns C)")
    print("=" * 78)
    print("     A\\C " + " ".join(f"{c:>9.2f}" for c in c_grid))
    for a in a_grid:
        cells = []
        for c in c_grid:
            vals = [v for v in curve[(a, c)] if np.isfinite(v)]
            cells.append(f"{statistics.mean(vals):>9.6f}" if vals else "      n/a")
        print(f"  {a:>6.4f} " + " ".join(cells))

    finite = {k: statistics.mean([v for v in vs if np.isfinite(v)])
              for k, vs in curve.items() if any(np.isfinite(v) for v in vs)}
    best_cell = min(finite, key=finite.get)
    spread = max(finite.values()) - min(finite.values())
    print(f"\n  lowest mean held-out loss at (A, C) = {best_cell}")
    print(f"  spread across the whole surface: {spread:.8f}")
    print(f"  (the A search's 1-D spread was 0.00018757)")
    print(f"  per-fold training-selected (A, C): {picks}")
    print(f"  distinct picks: {len(set(picks))}")

    print("\n" + "=" * 78)
    print("  CONTRAST vs the incumbent (A, C) = (1.25, 1.0)")
    print("=" * 78)
    if oof_fit["scores"] and oof_inc["scores"]:
        point, lo, hi = paired_oof_log_loss_delta(oof_fit, oof_inc, draws=draws)
        spans_zero = lo <= 0 <= hi
        print(f"  loss(fitted) - loss(incumbent) = {point:+.6f} [{lo:+.6f}, {hi:+.6f}]")
        print("  " + ("INCONCLUSIVE -- interval spans zero; incumbent stands"
                      if spans_zero else
                      ("IMPROVEMENT" if hi < 0 else "DETERIORATION")))
        print("\n  CHECK AGAINST THE RECORDED PREDICTION")
        c_picked = [c for _, c in picks]
        a_picked = [a for a, _ in picks]
        print(f"    1. contrast excludes zero      -> {not spans_zero}")
        print(f"    2. C at bottom of its grid     -> "
              f"{all(c <= c_grid[1] for c in c_picked)}  (picked {c_picked})")
        print(f"    3. A below 1.25                -> "
              f"{all(a < 1.25 for a in a_picked)}  (picked {a_picked})")
        print(f"    4. surface steeper than 0.00019 -> "
              f"{spread > 0.00018757}  ({spread:.8f})")


def _oof():
    return {"scores": [], "y": [], "w": [], "match_ids": []}


if __name__ == "__main__":
    main()

"""Does any fitted weighting actually beat the Impact score we SHIP?

Every gap in the evaluation tooling is measured against `kill_diff`, which
answers "is this better than counting kills". It does not answer the question
an adoption decision turns on: **is this better than what the site shows
today?** That needs a paired comparison against `current_impact` on identical
rows, and nothing in the project computed one.

    .venv\\Scripts\\python.exe scripts\\cache_observations.py    # once
    .venv\\Scripts\\python.exe scripts\\compare_to_shipped.py --out shipped.json

Protocol:
  * Fitted candidates are fitted per outer fold on training matches and scored
    only on that fold's held-out matches, via the same fold_candidates path
    the yardstick matrix uses.
  * `current_impact` reads the exact stored impact differential -- it was
    never fitted to this data, so it is scored on all rows.
  * The comparison is a PAIRED cluster bootstrap of the AUC difference,
    resampling matches and recomputing both AUCs on the same resample. A
    difference of two separately-bootstrapped intervals is not a test of the
    difference.
  * AUC, not log loss: it depends only on ranking, so it is unaffected by the
    in-sample Platt calibration issue documented in the findings.

Read-only. Adopts nothing.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.impact_eval import (
    CURRENT_IMPACT_CANDIDATE,
    FEATURE_COMPONENTS,
    PRIMARY_T1,
    PRIMARY_T2,
    YARDSTICKS,
    TargetConfig,
    cross_validate,
    fold_candidates,
    group_by_match,
    stable_folds,
)
from app.services.impact_eval_cache import load_observations
from app.services.stats_math import auc

L2_GRID = [0.01, 0.1, 1.0, 10.0]

TARGETS = [
    ("fitted_T1", PRIMARY_T1),
    ("fitted_T2", PRIMARY_T2),
    ("fitted_T3", TargetConfig(name="T3", k=3, gamma=0.7, match_share=0.67)),
]


def _fixed_scores(observations, yardstick_fn, candidate):
    return yardstick_fn(observations, candidate)


def _fitted_scores(observations, yardstick_fn, per_fold, folds):
    """Pool each fold's held-out scores. Same path as the yardstick matrix."""
    by_match = group_by_match(observations)
    scores, labels, mids = [], [], []
    for fold_index, candidate in per_fold.items():
        fold = folds.get(fold_index)
        if fold is None:
            continue
        test_obs = [o for mid in fold.test_match_ids for o in by_match.get(mid, [])]
        s, l, m = yardstick_fn(test_obs, candidate)
        scores.extend(s)
        labels.extend(l)
        mids.extend(m)
    return scores, labels, mids


def paired_auc_delta(a, b, draws=2000, seed=0):
    """CI for AUC(a) - AUC(b), both recomputed on each resample.

    `a` and `b` are (scores, labels, match_ids). They are joined on match id,
    so only matches present in BOTH contribute -- a fitted candidate that
    dropped rows cannot be compared against a fixed one on a wider row set.
    """
    a_by, b_by = {}, {}
    for store, (scores, labels, mids) in ((a_by, a), (b_by, b)):
        for s, l, m in zip(scores, labels, mids):
            store.setdefault(int(m), []).append((float(s), int(l)))
    shared = sorted(set(a_by) & set(b_by))
    if not shared:
        return None

    def auc_of(store, keys):
        flat = [pair for k in keys for pair in store[k]]
        return auc([p[0] for p in flat], [p[1] for p in flat])

    point = auc_of(a_by, shared) - auc_of(b_by, shared)
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(draws):
        drawn = [shared[i] for i in rng.integers(0, len(shared), len(shared))]
        d = auc_of(a_by, drawn) - auc_of(b_by, drawn)
        if np.isfinite(d):
            deltas.append(d)
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return {
        "delta": float(point),
        "ci": [float(lo), float(hi)],
        "excludes_zero": bool(lo > 0 or hi < 0),
        "matches": len(shared),
        "draws": len(deltas),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    observations = load_observations(None)
    report: dict = {
        "note": (
            "Paired AUC difference against the SHIPPED impact score "
            "(current_impact, the exact stored impact_diff). Positive = the "
            "fitted weighting ranks better than what the site shows today."
        ),
        "comparisons": {},
    }

    fitted: dict[str, dict] = {}
    for label, config in TARGETS:
        result = cross_validate(
            observations, [config], FEATURE_COMPONENTS, L2_GRID,
            seed=args.seed, fold_fn=stable_folds,
        )
        per_fold, _weights = fold_candidates(observations, result["folds"], label)
        fitted[label] = {"per_fold": per_fold,
                         "folds": {f.fold: f for f in result["folds"]}}

    header = f"{'candidate':12s} {'yardstick':22s} {'AUC delta vs shipped':>24s}  verdict"
    print(header)
    print("-" * len(header))
    for yardstick_name, fn in YARDSTICKS.items():
        shipped = _fixed_scores(observations, fn, CURRENT_IMPACT_CANDIDATE)
        for label in fitted:
            got = _fitted_scores(observations, fn, fitted[label]["per_fold"],
                                 fitted[label]["folds"])
            result = paired_auc_delta(got, shipped, draws=args.draws, seed=args.seed)
            if result is None:
                continue
            report["comparisons"].setdefault(yardstick_name, {})[label] = result
            mark = ("BETTER" if result["delta"] > 0 else "WORSE") if result["excludes_zero"] \
                else "no detectable difference"
            print(f"{label:12s} {yardstick_name:22s} "
                  f"{result['delta']:+.5f} [{result['ci'][0]:+.5f},{result['ci'][1]:+.5f}]  {mark}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=float))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

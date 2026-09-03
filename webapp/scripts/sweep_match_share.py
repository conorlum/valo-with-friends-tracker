"""How should Impact weight "winning the match" against "winning rounds"?

Fits the constrained FACTOR_WEIGHTS against T3 -- a target whose match/round
balance is an explicit, constant share -- across a sweep of that share, and
scores every fitted weighting on the SAME fixed binary yardsticks the rest of
the project uses.

    .venv\\Scripts\\python.exe scripts\\cache_observations.py     # once
    .venv\\Scripts\\python.exe scripts\\sweep_match_share.py --out sweep.json

Protocol, which is the whole reason this is a sweep and not a search:

  * Targets are FROZEN, never selected. `match_share` changes the definition
    of y, so comparing configurations by their own losses would reward
    whichever outcome is easiest to predict. The primary is declared up front
    (PRIMARY_T3 below); every other point on the sweep is a SENSITIVITY, and
    all of them are compared only on the fixed yardsticks, whose labels are
    identical across configurations.
  * Weights are fitted per outer fold on that fold's TRAINING matches and
    scored only on its held-out matches, via the existing fold_candidates /
    yardstick_matrix path.
  * AUC gaps are reported against kill_diff on identical rows. AUC is used
    rather than log loss because it depends only on ranking, so it is immune
    to the in-sample Platt calibration issue in the findings doc.

Read-only. Changes nothing that ships.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    CURRENT_IMPACT_CANDIDATE,
    FEATURE_COMPONENTS,
    PRIMARY_T1,
    PRIMARY_T2,
    TargetConfig,
    cross_validate,
    dataset_fingerprint,
    fold_candidates,
    fold_mapping_hash,
    stable_folds,
    yardstick_matrix,
)
from app.services.impact_eval_cache import load_observations

L2_GRID = [0.01, 0.1, 1.0, 10.0]

# THE PRIMARY IS FROZEN. Declared here, before any fitting, from the stated
# design intent: winning the match matters more than winning the round. Two
# thirds is "the match counts twice as much as the rounds do" -- the simplest
# reading of that sentence, chosen a priori rather than picked off the sweep.
PRIMARY_T3 = TargetConfig(name="T3", k=3, gamma=0.7, match_share=0.67)

# Sensitivities. 0.0 is round-only; 0.3135 is where the frozen T2 nominally
# sits; everything above 0.5 is match-primary and has never been fitted here.
SWEEP = [0.0, 0.1, 0.2, 0.3135, 0.4, 0.5, 0.6, 0.67, 0.75, 0.85, 0.95]


def _cells(matrix, name):
    out = {}
    for yardstick, candidates in matrix.items():
        cell = candidates.get(name)
        if not cell:
            continue
        out[yardstick] = {
            "auc": cell["auc"],
            "gap_over_kill_diff": cell.get("gap_over_kill_diff"),
            "gap_ci": cell.get("gap_ci"),
            "n": cell["n"],
        }
    return out


def _weights_row(fold_weights):
    usable = [w for w in fold_weights.values() if w.usable]
    if not usable:
        return {"usable_folds": 0, "total_folds": len(fold_weights)}
    import numpy as np

    row = {"usable_folds": len(usable), "total_folds": len(fold_weights)}
    for field in ("damage_multiplier", "econ", "time", "swing"):
        values = [getattr(w, field) for w in usable]
        row[field] = {
            "median": float(np.median(values)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
    return row


def run_one(observations, config, label, draws, seed):
    result = cross_validate(
        observations, [config], FEATURE_COMPONENTS, L2_GRID, seed=seed,
        fold_fn=stable_folds,
    )
    candidates, fold_weights = fold_candidates(observations, result["folds"], label)
    folds = {f.fold: f for f in result["folds"]}
    matrix = yardstick_matrix(
        observations, [], {label: candidates}, folds, draws=draws, seed=seed
    )
    return {
        "config": {k: v for k, v in vars(config).items() if v is not None},
        "weights": _weights_row(fold_weights),
        "yardsticks": _cells(matrix, label),
        "selected_l2_per_fold": [{"fold": f.fold, "l2": f.l2} for f in result["folds"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the full sweep as JSON")
    parser.add_argument("--draws", type=int, default=400,
                        help="bootstrap draws for the sweep points (default 400)")
    parser.add_argument("--primary-draws", type=int, default=2000,
                        help="bootstrap draws for the frozen primary and the reference rows")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--shares", type=float, nargs="*", default=None,
                        help="override the sweep points")
    args = parser.parse_args()

    observations = load_observations(None)
    match_ids = sorted({o.match_id for o in observations})
    folds = stable_folds(match_ids, n_folds=5, seed=args.seed)
    identity = {
        "dataset_fingerprint": dataset_fingerprint(match_ids),
        "fold_mapping_hash": fold_mapping_hash(folds),
        "fold_fn": "stable_folds",
        "n_observations": len(observations),
    }
    print(json.dumps(identity, indent=2))

    report = {"identity": identity, "primary": None, "sweep": [], "reference": {}}

    # Reference rows: the fixed candidates plus the two existing frozen
    # targets, on the same folds, so the sweep can be read against them.
    print("\n== reference (fixed candidates + existing frozen targets) ==")
    fixed = [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES]
    ref_matrix = yardstick_matrix(
        observations, fixed, {}, {}, draws=args.primary_draws, seed=args.seed
    )
    for candidate in fixed:
        report["reference"][candidate.name] = _cells(ref_matrix, candidate.name)
    for label, config in (("fitted_T1", PRIMARY_T1), ("fitted_T2", PRIMARY_T2)):
        t0 = time.time()
        report["reference"][label] = run_one(
            observations, config, label, args.primary_draws, args.seed
        )
        print(f"  {label:12s} {time.time() - t0:5.1f}s")

    print("\n== frozen primary: T3 at match_share = "
          f"{PRIMARY_T3.match_share} ==")
    t0 = time.time()
    report["primary"] = run_one(
        observations, PRIMARY_T3, "fitted_T3", args.primary_draws, args.seed
    )
    print(f"  fitted_T3    {time.time() - t0:5.1f}s")

    print("\n== sensitivity sweep (compared ONLY on the fixed yardsticks) ==")
    print(f"{'share':>7} {'econ':>6} {'time':>6} {'swing':>6} {'dmg':>6}   "
          f"{'first-half gap':>22} {'forward-rounds gap':>22}")
    for share in (args.shares if args.shares is not None else SWEEP):
        config = TargetConfig(name="T3", k=3, gamma=0.7, match_share=share)
        entry = run_one(observations, config, "sens", args.draws, args.seed)
        report["sweep"].append(entry)
        w = entry["weights"]
        fh = entry["yardsticks"].get("first_half_to_match", {})
        fr = entry["yardsticks"].get("forward_rounds", {})

        def fmt(cell):
            gap, ci = cell.get("gap_over_kill_diff"), cell.get("gap_ci")
            if gap is None or not ci:
                return f"{'--':>22}"
            star = "*" if (ci[1] < 0 or ci[0] > 0) else " "
            return f"{gap:+.5f} [{ci[0]:+.5f},{ci[1]:+.5f}]{star}"

        if w.get("usable_folds"):
            print(f"{share:>7.4f} {w['econ']['median']:>6.1f} {w['time']['median']:>6.1f} "
                  f"{w['swing']['median']:>6.1f} {w['damage_multiplier']['median']:>6.2f}   "
                  f"{fmt(fh)} {fmt(fr)}")
        else:
            print(f"{share:>7.4f} {'no usable fold weighting':>28}")

    print("\n* = interval excludes zero")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=float))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

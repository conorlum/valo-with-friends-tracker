"""Impact-vs-winning evaluation report.

Debug-only CLI, not exposed on any page. Follows scripts/validate_fight_ev.py's
conventions: prints a summary, optionally writes JSON, honours a DATABASE_URL
override.

Usage:
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --stage0-only
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --out report.json
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --include-realized --draws 500

Point at a specific database the same way every other script here does:
    $env:DATABASE_URL = (Get-Content .env.remote | Select-String DATABASE_URL).Line.Split('=',2)[1]
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    BASELINE_DAMAGE,
    CONTROLS_CONTEXT,
    CONTROLS_RESULT,
    CURRENT_IMPACT_CANDIDATE,
    FEATURE_COMPONENTS,
    PRIMARY_T1,
    PRIMARY_T2,
    T2_SENSITIVITY_GRID,
    TargetConfig,
    coefficient_diagnostics,
    controls_for,
    cross_validate,
    fit_constrained_weights,
    fold_candidates,
    load_all_observations,
    load_player_matches,
    load_player_matches_acs,
    load_stored_observations,
    oof_metrics,
    paired_oof_log_loss_delta,
    yardstick_matrix,
)
from app.services.impact_stage0 import stage0_report
from app.services.site_stats import resolve_roster_player_ids
from app.services.win_probability import econ_increment_report, fit_value_model

L2_GRID = [0.01, 0.1, 1.0, 10.0]

# The control ladder. The 3 -> 4 step is the headline: the only number showing
# the components carry information beyond who won the round and what the teams
# could afford next. Every step predicts the SAME frozen target, so their
# losses are directly comparable and the delta can be bootstrapped paired.
LADDER = [
    ("1_round_result", CONTROLS_RESULT),
    ("2_plus_context", CONTROLS_RESULT + CONTROLS_CONTEXT),
    ("3_plus_damage", CONTROLS_RESULT + CONTROLS_CONTEXT + BASELINE_DAMAGE),
    ("4_plus_components", CONTROLS_RESULT + CONTROLS_CONTEXT + FEATURE_COMPONENTS),
]

def _value_context(train_obs, seed: int = 0, inner_folds: int = 3):
    """Stage B's context: a value model fitted on the training half, PLUS
    inner-OOF value models so a training row's leverage does not come from a
    model that saw its own match."""
    from app.services.impact_eval import assign_folds, split_observations

    full = fit_value_model(train_obs)
    inner = assign_folds([o.match_id for o in train_obs], n_folds=inner_folds, seed=seed + 7)
    by_match = {}
    for fold in range(inner_folds):
        inner_train, inner_test = split_observations(train_obs, inner, fold)
        if not inner_train or not inner_test:
            continue
        model = fit_value_model(inner_train)
        for o in inner_test:
            by_match[o.match_id] = model
    return {"value_beta": full, "value_beta_by_match": by_match}


def _control_ladder(observations, config, draws, seed):
    """Every step run through the SAME outer folds on the SAME frozen target,
    with its L2 selected inside each fold's training half. The headline 3 -> 4
    step gets a PAIRED interval, because a point estimate cannot tell you
    whether the components added anything."""
    runs = {name: cross_validate(observations, [config], features, L2_GRID, seed=seed)
            for name, features in LADDER}
    out = {name: oof_metrics(run["oof"], draws=draws, seed=seed) for name, run in runs.items()}

    point, lo, hi = paired_oof_log_loss_delta(
        runs["4_plus_components"]["oof"], runs["3_plus_damage"]["oof"], draws=draws, seed=seed
    )
    out["headline"] = {
        "metric": "weighted log loss, components minus damage-only",
        "delta": point,
        "delta_ci": [lo, hi],
        "reading": (
            "negative delta = adding the components IMPROVED held-out prediction. "
            "An interval spanning zero means they added nothing measurable."
        ),
    }
    return out


def _weights_summary(fold_weights: dict) -> dict:
    """Per-fold constrained weights, serialized rather than discarded --
    'do T1 and T2 agree on the weighting?' is unanswerable without them."""
    usable = [w for w in fold_weights.values() if w.usable]
    summary = {
        "per_fold": {str(k): vars(v) for k, v in sorted(fold_weights.items())},
        "usable_folds": len(usable),
        "total_folds": len(fold_weights),
    }
    if usable:
        for field in ("damage_multiplier", "econ", "time", "swing"):
            values = [getattr(w, field) for w in usable]
            summary.setdefault("across_folds", {})[field] = {
                "median": float(np.median(values)),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            }
    return summary


def _proposal(observations, config, label: str) -> dict:
    """An all-data fit: the weighting to CONSIDER adopting, never an estimate
    of its own performance."""
    weights = fit_constrained_weights(observations, config, controls_for(config))
    return {
        "target": label,
        "frozen_config": vars(config),
        "controls": controls_for(config),
        "weights": vars(weights),
        "usable": weights.usable,
        "units": (
            "econ/time/swing map directly onto FACTOR_WEIGHTS. damage_multiplier "
            "multiplies the STORED damage component, which impact.py already computed "
            "as round(damage_and_assists * 1.25) -- so a proposed d means changing that "
            "1.25 to 1.25 * d, NOT setting the raw multiplier to d."
        ),
        "warning": (
            "Fitted on ALL matches. NOT an unbiased estimate of its own performance -- "
            "read the per-fold fitted_* rows of the yardstick matrix for that."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    parser.add_argument("--draws", type=int, default=200, help="bootstrap draws (default 200)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stage0-only", action="store_true", help="skip all fitting")
    parser.add_argument("--sensitivity", action="store_true",
                        help="also run the T2 grid, compared ONLY on the fixed yardsticks")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        roster = set(resolve_roster_player_ids(db))
        report = {
            "stage0": stage0_report(
                load_player_matches(db), roster, draws=args.draws, seed=args.seed
            ),
            # User-requested comparison: does Impact beat straight ACS on the
            # SAME cohort methodology? A weaker "about the same as kill diff"
            # finding would look very different from "can't beat raw ACS."
            "stage0_acs": stage0_report(
                load_player_matches_acs(db), roster, draws=args.draws, seed=args.seed
            ),
        }
        report["stage0_acs"]["note"] = (
            "Same Stage 0 methodology (cohorts, within-player centering, CIs) "
            "applied to plain ACS instead of Impact, for direct comparison "
            "against report['stage0']."
        )

        stored_report = {}
        stored = load_stored_observations(db, report=stored_report)
        report["stage0"]["loading_realized"] = stored_report
        report["stage0"]["match_level_diagnostics"] = yardstick_matrix(
            stored, [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES], {}, {},
            draws=args.draws, seed=args.seed,
        )
        print("== Stage 0: Impact as it ships today (realized components) ==")
        print(json.dumps(report["stage0"], indent=2, default=float))
        print("\n== Stage 0, same methodology, applied to straight ACS ==")
        print(json.dumps(report["stage0_acs"], indent=2, default=float))

        if args.stage0_only:
            if args.out:
                args.out.write_text(json.dumps(report, indent=2, default=float))
                print(f"\nwrote {args.out}")
            return 0

        load_report = {}
        observations = load_all_observations(db, use_realized_swing=False, report=load_report)
        report["loading_ex_ante"] = {"n_observations": len(observations), **load_report}
        report["component_variant"] = "ex_ante"

        # --- The frozen Stage A/B targets, each nested end to end ---
        per_fold_candidates = {}
        all_folds = {}
        targets = [
            ("T1", PRIMARY_T1, None),
            ("T2", PRIMARY_T2, None),
            ("WPA", TargetConfig(name="WPA"), lambda o: _value_context(o, args.seed)),
        ]
        for name, config, context_builder in targets:
            result = cross_validate(
                observations, [config], FEATURE_COMPONENTS, L2_GRID,
                seed=args.seed, context_builder=context_builder,
            )
            candidates, fold_weights = fold_candidates(
                observations, result["folds"], f"fitted_{name}",
                context_builder=context_builder,
            )
            per_fold_candidates[f"fitted_{name}"] = candidates
            all_folds.update({f.fold: f for f in result["folds"]})
            report[name] = {
                "frozen_config": vars(config),
                "controls": controls_for(config),
                "metrics": oof_metrics(result["oof"], draws=args.draws, seed=args.seed),
                "selected_l2_per_fold": [{"fold": f.fold, "l2": f.l2} for f in result["folds"]],
                "unconstrained_fold_coefficients": {
                    "feature_order": ["intercept"] + FEATURE_COMPONENTS,
                    "per_fold": [f.beta_raw.tolist() for f in result["folds"]],
                },
                "constrained_weights": _weights_summary(fold_weights),
            }

        report["T2_control_ladder"] = {
            "config": vars(PRIMARY_T2),
            **_control_ladder(observations, PRIMARY_T2, args.draws, args.seed),
        }

        fixed = [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES]
        report["yardstick_matrix_ex_ante"] = yardstick_matrix(
            observations, fixed, per_fold_candidates, all_folds, draws=args.draws, seed=args.seed
        )
        report["yardstick_matrix_realized"] = yardstick_matrix(
            stored, fixed, per_fold_candidates, all_folds, draws=args.draws, seed=args.seed
        )
        report["realized_note"] = (
            "Fitted candidates evaluated on realized components too: adopting weights "
            "fitted on ex_ante applies them to a scorer that still computes realized, "
            "so this row is what adoption would actually buy."
        )

        report["diagnostics_T2"] = coefficient_diagnostics(
            observations, PRIMARY_T2, FEATURE_COMPONENTS, draws=args.draws, seed=args.seed
        )

        report["WPA"]["framing"] = (
            "attribution, not independent validation -- dV is dominated by the round's "
            "own outcome. Its yardstick-matrix row IS comparable to T1/T2, because every "
            "candidate is scored there on the same fixed binary labels."
        )
        report["econ_increment"] = econ_increment_report(observations, seed=args.seed)

        if args.sensitivity:
            sensitivity = []
            for config in T2_SENSITIVITY_GRID:
                result = cross_validate(
                    observations, [config], FEATURE_COMPONENTS, L2_GRID, seed=args.seed
                )
                candidates, _ = fold_candidates(observations, result["folds"], "sens")
                folds = {f.fold: f for f in result["folds"]}
                cell = yardstick_matrix(
                    observations, [], {"sens": candidates}, folds, draws=50, seed=args.seed
                )["forward_rounds"]["sens"]
                sensitivity.append({"config": vars(config), "forward_rounds": cell})
            report["T2_sensitivity"] = {
                "note": (
                    "Compared on the FIXED forward-rounds yardstick, never on each "
                    "target's own loss -- those losses measure different outcomes."
                ),
                "runs": sensitivity,
            }

        # One all-data proposal PER TARGET, so the agreement question is answerable.
        report["deployment_proposals"] = {
            "T1": _proposal(observations, PRIMARY_T1, "T1"),
            "T2": _proposal(observations, PRIMARY_T2, "T2"),
            "note": (
                "Compare these against each other and against the per-fold spreads in "
                "T1/T2.constrained_weights. Disagreement is a finding to report, not a "
                "tie to break."
            ),
        }

        print("\n== T2 control ladder (headline: 3 -> 4) ==")
        print(json.dumps(report["T2_control_ladder"], indent=2, default=float))
        print("\n== Targets x yardsticks (ex-ante) ==")
        print(json.dumps(report["yardstick_matrix_ex_ante"], indent=2, default=float))
        print("\n== Deployment proposals ==")
        print(json.dumps(report["deployment_proposals"], indent=2, default=float))

        if args.out:
            args.out.write_text(json.dumps(report, indent=2, default=float))
            print(f"\nwrote {args.out}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

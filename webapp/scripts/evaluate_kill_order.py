"""Stage C: refit the kill-order graph and report whether it helps.

    .venv\\Scripts\\python.exe scripts\\evaluate_kill_order.py --out scratch-kill-order.json
    .venv\\Scripts\\python.exe scripts\\evaluate_kill_order.py --quick

Requires a live Postgres. Costs a full replay of every match (minutes).
Changes nothing that ships: this reads impact.py and writes a report.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scoring.impact import IMPACT_CALCULATION_VERSION  # noqa: E402
from app.services.impact_eval import (  # noqa: E402
    PRIMARY_T2, dataset_fingerprint, fold_mapping_hash, load_all_observations, stable_folds,
)
from app.services.kill_order_curves import family_a_leverage  # noqa: E402
from app.services.kill_order_leverage import (  # noqa: E402
    load_all_leverage, shipped_graph, state_visits_for_match,
)
from app.services.kill_order_refit import (  # noqa: E402
    PRIMARY_COMPARISONS, REPORT_SECTIONS, STAGE_C_SCHEMA_VERSION, RunIdentity, control_ladder,
    paired_delta, player_level_report, run_nested_cv, stage_c0_report, stability_report,
    verdict_report,
)


def _calculation_version() -> str:
    return f"{IMPACT_CALCULATION_VERSION}/{STAGE_C_SCHEMA_VERSION}"


def _shipped():
    return shipped_graph()


def _exposure(team_rows):
    import numpy as np

    return np.abs(family_a_leverage(team_rows)).sum(axis=0)


def _match_outcomes(observations) -> dict:
    """match_id -> did team A win the match. Skips ties (None), matching
    match_win()'s own contract -- a player_level_report can't score a
    match that has no winner."""
    out: dict = {}
    for obs in observations:
        if obs.match_won_by_team_a is not None:
            out[obs.match_id] = obs.match_won_by_team_a
    return out


def _practically_equivalent(report) -> bool:
    """PRACTICAL_EQUIVALENCE_RMS is 1% of the score sd: reuses Stage C0's
    own current-vs-swing-plugin comparison, since that is exactly a
    sd(difference) vs sd(reference) measurement already in the report."""
    from app.services.kill_order_refit import PRACTICAL_EQUIVALENCE_RMS

    round_level = report["stage_c0"]["current_vs_swing_plugin"]["round_level"]
    if round_level["sd_reference"] == 0:
        return False
    share = round_level["sd_difference"] / round_level["sd_reference"]
    return share < PRACTICAL_EQUIVALENCE_RMS


def _jsonable(value):
    """Recursively convert numpy arrays/scalars to plain JSON types.
    json.dumps(default=...) only gets called for values it cannot encode
    directly, and a multi-element numpy array raises inside that fallback
    too (float(array) only works for 0-d/1-element arrays) -- so arrays
    are converted up front rather than left to the fallback."""
    import numpy as np

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        return value.item()
    return value


def _summarize(result) -> dict:
    """A CandidateResult, reduced to what a report/JSON dump needs: the
    pooled OOF loss plus each fold's recovered graph, d and deployability.
    Full per-round OOF arrays are omitted -- they are large and are what
    paired_delta/verdict_report consume directly from the live `results`
    dict, not from this summary."""
    from app.services.stats_math import weighted_log_loss

    oof_loss = None
    if result.oof_probabilities is not None:
        oof_loss = float(
            weighted_log_loss(result.oof_probabilities, result.oof_y, result.oof_weights)
        )
    return {
        "oof_weighted_log_loss": oof_loss,
        "n_oof_rows": None if result.oof_row_ids is None else int(len(result.oof_row_ids)),
        "per_fold": {
            str(fold): {
                "l2": fitted.l2,
                "d": fitted.d,
                "deployable": fitted.deployable,
                "reasons": list(fitted.reasons),
                "graph": None if fitted.graph is None else _jsonable(fitted.graph),
                "train_matches": len(fitted.train_match_ids),
                "test_matches": len(fitted.test_match_ids),
            }
            for fold, fitted in result.per_fold.items()
        },
    }


def _print_stage_c0(section: dict) -> None:
    print("\n=== Stage C0: how much can the graph move Impact at all ===")
    print(section["note"])
    fit = section["shipped_vs_swing"]
    print(f"shipped graph ~ {fit['intercept']:.1f} + {fit['slope']:.1f} * dP, "
          f"R^2 = {fit['r_squared']:.4f}")
    swap = section["current_vs_swing_plugin"]["round_level"]
    print(f"current vs swing-plugin: pearson r = {swap['pearson']:.5f}, "
          f"sign flips = {swap['sign_flip_rate']:.4%}, n = {swap['n']}")
    print(section["reading"])


def _print_verdicts(section: dict) -> None:
    print("\n=== Verdicts (never merged) ===")
    print(section["note"])
    for key, verdict in section["verdicts"].items():
        print(f"  {key} ({verdict['question']}): helped = {verdict['helped']}")
        for note in verdict["notes"]:
            print(f"      - {note}")


def _emit(report: dict, out: str | None) -> None:
    if not out:
        return
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(_jsonable(report), fh, indent=2, default=float)
    print(f"\nwrote {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write the full report as JSON")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--draws", type=int, default=200, help="bootstrap draws")
    parser.add_argument("--quick", action="store_true",
                        help="Stage C0 only -- the block that runs before any fitting")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app.db import SessionLocal

    db = SessionLocal()
    report: dict = {section: None for section in REPORT_SECTIONS}

    loading: dict = {}
    team_rows, player_rows = load_all_leverage(db, report=loading)
    report["loading"] = loading

    match_ids = sorted({row.match_id for row in team_rows})
    folds = stable_folds(match_ids)
    identity = RunIdentity(
        dataset_fingerprint=dataset_fingerprint(match_ids),
        fold_mapping_hash=fold_mapping_hash(folds),
        calculation_version=_calculation_version(),
    )
    report["identity"] = identity.__dict__

    visits = []
    for match_id in match_ids:
        visits.extend(state_visits_for_match(db, match_id))

    # FIRST, always: if the metric does not move, everything below is read
    # in that light rather than as a headline of its own.
    report["stage_c0"] = stage_c0_report(team_rows, player_rows, visits, draws=args.draws)
    _print_stage_c0(report["stage_c0"])

    if args.quick:
        _emit(report, args.out)
        return 0

    observations = load_all_observations(db, use_realized_swing=False)
    results = run_nested_cv(
        team_rows, observations, PRIMARY_T2,
        candidates=["current_graph", "swing_plugin", "swing_affine", "swing_basis",
                    "pooled", "free"],
        l2_grid=[0.01, 0.1, 1.0, 10.0, 100.0], state_visits=visits,
    )
    report["family_a"] = {name: _summarize(result) for name, result in results.items()}
    report["control_ladder"] = control_ladder(team_rows, observations, PRIMARY_T2,
                                              draws=args.draws)
    report["player_level"] = player_level_report(
        player_rows, _match_outcomes(observations), _shipped(), draws=args.draws
    )
    report["stability"] = {
        name: stability_report(result, _shipped(), _exposure(team_rows), draws=args.draws)
        for name, result in results.items() if name not in ("current_graph",)
    }
    report["deferral_check"] = {
        "matches": len(match_ids), "reopen_threshold": 4000,
        "reachable": len(match_ids) >= 4000,
        "note": "4,000 re-opens the deferred per-component fits for a LOOK, not a verdict.",
    }

    primaries = {
        spec["name"]: paired_delta(results[spec["candidate"]], results[spec["against"]],
                                   alpha=spec["alpha"], draws=args.draws)
        for spec in PRIMARY_COMPARISONS
        if spec["candidate"] in results and spec["against"] in results
    }
    report["verdicts"] = verdict_report(
        primaries=primaries,
        deployable={n: all(f.deployable for f in r.per_fold.values())
                    for n, r in results.items()},
        practically_equivalent=_practically_equivalent(report),
        targets_agree=False,
        max_component_correlation=1.0,
        econ_negative_every_fold=True,
        beats_kill_diff_t1=False,
        stability=report["stability"],
    )
    _print_verdicts(report["verdicts"])
    _emit(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

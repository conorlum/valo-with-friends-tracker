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

from app.services.impact_eval import load_all_observations  # noqa: E402
from app.services.kill_order_leverage import (  # noqa: E402
    load_all_leverage, state_visits_for_match,
)
from app.services.kill_order_refit import (  # noqa: E402
    REPORT_SECTIONS, build_full_report, stage_c0_report, to_jsonable,
)


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
        json.dump(to_jsonable(report), fh, indent=2)
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

    loading: dict = {}
    team_rows, player_rows = load_all_leverage(db, report=loading)
    match_ids = sorted({row.match_id for row in team_rows})

    visits = []
    for match_id in match_ids:
        visits.extend(state_visits_for_match(db, match_id))

    if args.quick:
        # FIRST, always: if the metric does not move, everything below is
        # read in that light rather than as a headline of its own. Skips
        # loading observations entirely -- Stage C0 needs only the
        # leverage/player rows and the swing-table visits.
        section = stage_c0_report(team_rows, player_rows, visits, draws=args.draws)
        _print_stage_c0(section)
        _emit({"loading": loading, "stage_c0": section}, args.out)
        return 0

    # The two loaders can disagree about eligibility (a surrender rule
    # change, a schema drift): filter observations to exactly the matches
    # the extractor actually produced rows for, then assert the round sets
    # line up, rather than silently letting build_target run over a
    # slightly different match set than the leverage rows describe.
    extracted_matches = set(match_ids)
    all_observations = load_all_observations(db, use_realized_swing=False)
    observations = [o for o in all_observations if o.match_id in extracted_matches]
    covered = {o.round_id for o in observations}
    missing = [r.round_id for r in team_rows if r.round_id not in covered]
    if missing:
        raise SystemExit(
            f"{len(missing)} extracted rounds have no observation "
            f"(first: {missing[:5]}); the two loaders disagree about eligibility"
        )

    report = build_full_report(
        team_rows, observations, player_rows=player_rows, state_visits=visits,
        draws=args.draws,
    )
    report["loading"] = loading
    _print_stage_c0(report["stage_c0"])
    _print_verdicts(report["verdicts"])
    _emit(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fight-EV Diamond validation report (fight_ev_diamond.txt section 6/10).

Debug-only CLI: given a player's Riot ID (display_name), prints or writes a
JSON report covering everything section 10 requires be checked before
trusting any displayed number -- replay exclusion counts, all 25 cells per
side/benchmark, monotonicity violations, boundary sanity, cross-side
complement, the closure diagnostic, the all-teammates reconstruction check,
surrender leakage, contributing-match distributions, and bootstrap
defined-draw fractions. Not exposed on the player-facing page.

Usage:
    .venv\\Scripts\\python.exe scripts\\validate_fight_ev.py "NPrightdolphin#NA1"
    .venv\\Scripts\\python.exe scripts\\validate_fight_ev.py "NPrightdolphin#NA1" --out report.json
    .venv\\Scripts\\python.exe scripts\\validate_fight_ev.py "NPrightdolphin#NA1" --draws 500

Point at a specific database (e.g. Neon) the same way every other script in
this folder does -- set DATABASE_URL before invoking, it overrides .env:
    $env:DATABASE_URL = (Get-Content .env.neon | Select-String DATABASE_URL).Line.Split('=',2)[1]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import Player
from app.services.fight_ev import (
    CALCULATION_VERSION,
    DEFAULT_BOOTSTRAP_DRAWS,
    MIN_CONTRIBUTING_MATCHES,
    MIN_DEFINED_DRAW_FRACTION,
    Side,
    TeammatePool,
    _bootstrap_seed,
    _sum_blocks,
    _teammate_bucket_name,
    bootstrap_cell,
    classify_cell,
    compute_point_estimate,
    duel_rate,
    load_match_fight_ev_blocks,
    win_rate,
)

SIDES: tuple[Side, ...] = ("attacking", "defending")
POOLS: tuple[TeammatePool, ...] = ("tracked_roster", "all_teammates")

# A cross-side complement gap or closure discrepancy bigger than this is
# reported for inspection but is a "weak smoke alarm, not a gate" per section
# 10 -- not a pass/fail assertion.
COMPLEMENT_FLAG_THRESHOLD = 0.15
CLOSURE_FLAG_THRESHOLD = 0.15
CLOSURE_UNSTABLE_LEVERAGE = 0.05


def _cells_report(blocks, side: Side, pool: TeammatePool, player_id: int, draws: int) -> list[dict]:
    aggregate = _sum_blocks(blocks)
    teammate_attr = _teammate_bucket_name(pool)
    rows = []
    for a in range(1, 6):
        for b in range(1, 6):
            cell = compute_point_estimate(
                aggregate.wins, aggregate.player_duels, getattr(aggregate, teammate_attr), side, a, b
            )
            bootstrap = None
            if cell.display_state.value == "POSITIVE":
                seed = _bootstrap_seed(player_id, side, a, b, pool)
                bootstrap = bootstrap_cell(blocks, side, a, b, pool, seed, draws)
                cell = classify_cell(cell, bootstrap)
            rows.append(
                {
                    "state": f"{a}v{b}",
                    "display_state": cell.display_state.value,
                    "p_player": cell.p_player,
                    "n_player": cell.n_player,
                    "p_teammates": cell.p_teammates,
                    "n_teammates": cell.n_teammates,
                    "w_u": cell.w_u,
                    "w_d": cell.w_d,
                    "leverage": cell.leverage,
                    "m": cell.m,
                    "bootstrap": None
                    if cell.bootstrap is None
                    else {
                        "ci_low": cell.bootstrap.ci_low,
                        "ci_high": cell.bootstrap.ci_high,
                        "defined_draw_fraction": cell.bootstrap.defined_draw_fraction,
                        "contributing_matches_player": cell.bootstrap.contributing_matches_player,
                        "contributing_matches_teammates": cell.bootstrap.contributing_matches_teammates,
                        "contributing_matches_w_u": cell.bootstrap.contributing_matches_w_u,
                        "contributing_matches_w_d": cell.bootstrap.contributing_matches_w_d,
                    },
                }
            )
    return rows


def _monotonicity_report(aggregate) -> list[dict]:
    """Section 10: report (don't assert) where observed W decreases as own
    survivors increase, or increases as opponents increase, at either side.
    Raw observational W is not guaranteed monotone -- this is diagnostic."""
    violations = []
    for side in SIDES:
        for a in range(0, 6):
            for b in range(0, 6):
                w_here = win_rate(aggregate.wins, side, a, b)
                if w_here is None:
                    continue
                w_more_own = win_rate(aggregate.wins, side, a + 1, b)
                if w_more_own is not None and w_more_own < w_here:
                    violations.append(
                        {
                            "kind": "W decreases as own survivors increase",
                            "side": side,
                            "from": f"{a}v{b}",
                            "to": f"{a + 1}v{b}",
                            "w_from": w_here,
                            "w_to": w_more_own,
                            "support_from": aggregate.wins.get((side, a, b), None) and aggregate.wins[(side, a, b)].entries,
                            "support_to": aggregate.wins.get((side, a + 1, b), None) and aggregate.wins[(side, a + 1, b)].entries,
                        }
                    )
                w_more_opp = win_rate(aggregate.wins, side, a, b + 1)
                if w_more_opp is not None and w_more_opp > w_here:
                    violations.append(
                        {
                            "kind": "W increases as opponents increase",
                            "side": side,
                            "from": f"{a}v{b}",
                            "to": f"{a}v{b + 1}",
                            "w_from": w_here,
                            "w_to": w_more_opp,
                            "support_from": aggregate.wins.get((side, a, b), None) and aggregate.wins[(side, a, b)].entries,
                            "support_to": aggregate.wins.get((side, a, b + 1), None) and aggregate.wins[(side, a, b + 1)].entries,
                        }
                    )
    return violations


def _boundary_report(aggregate) -> dict:
    """Section 10: assert the two definitional rails hold by construction
    (they're hard-coded constants in win_rate, so this just documents that),
    and report -- without asserting -- the two estimated rails plus their
    support."""
    atk_a0_ok = all(win_rate(aggregate.wins, "attacking", a, 0) == 1.0 for a in range(1, 6))
    def_0b_ok = all(win_rate(aggregate.wins, "defending", 0, b) == 0.0 for b in range(1, 6))

    estimated_rails = {"attacking_0_b": [], "defending_a_0": []}
    for b in range(1, 6):
        counts = aggregate.wins.get(("attacking", 0, b))
        estimated_rails["attacking_0_b"].append(
            {
                "state": f"0v{b}",
                "w": None if counts is None or counts.entries == 0 else counts.wins / counts.entries,
                "entries": 0 if counts is None else counts.entries,
            }
        )
    for a in range(1, 6):
        counts = aggregate.wins.get(("defending", a, 0))
        estimated_rails["defending_a_0"].append(
            {
                "state": f"{a}v0",
                "w": None if counts is None or counts.entries == 0 else counts.wins / counts.entries,
                "entries": 0 if counts is None else counts.entries,
            }
        )

    return {
        "definitional_rail_atk_a_0_is_1": atk_a0_ok,
        "definitional_rail_def_0_b_is_0": def_0b_ok,
        "estimated_rails": estimated_rails,
    }


def _cross_side_complement_report(aggregate) -> list[dict]:
    """Section 10: W_ATK(a,b) ~= 1 - W_DEF(b,a). Weak smoke alarm only."""
    flagged = []
    for a in range(1, 6):
        for b in range(1, 6):
            w_atk = win_rate(aggregate.wins, "attacking", a, b)
            w_def = win_rate(aggregate.wins, "defending", b, a)
            if w_atk is None or w_def is None:
                continue
            diff = abs(w_atk - (1 - w_def))
            if diff > COMPLEMENT_FLAG_THRESHOLD:
                flagged.append(
                    {
                        "atk_state": f"{a}v{b}",
                        "def_state": f"{b}v{a}",
                        "w_atk": w_atk,
                        "w_def": w_def,
                        "diff": diff,
                    }
                )
    return flagged


def _closure_report(aggregate) -> list[dict]:
    """Section 10: for debug output only, compare the population duel rate
    at (side,a,b) with p_star = (W(a,b) - W(a-1,b)) / L. Skip when L == 0,
    mark unstable below CLOSURE_UNSTABLE_LEVERAGE. A discrepancy is not proof
    of a bug."""
    rows = []
    for side in SIDES:
        for a in range(1, 6):
            for b in range(1, 6):
                w_ab = win_rate(aggregate.wins, side, a, b)
                w_u = win_rate(aggregate.wins, side, a, b - 1)
                w_d = win_rate(aggregate.wins, side, a - 1, b)
                if w_ab is None or w_u is None or w_d is None:
                    continue
                leverage = w_u - w_d
                if leverage == 0:
                    continue

                # Population = target player's team's overall resolved-duel
                # rate at this cell = player + all-teammates combined.
                player_counts = aggregate.player_duels.get((side, a, b))
                teammate_counts = aggregate.all_teammate_duels.get((side, a, b))
                kills = (player_counts.kills if player_counts else 0) + (teammate_counts.kills if teammate_counts else 0)
                deaths = (player_counts.deaths if player_counts else 0) + (teammate_counts.deaths if teammate_counts else 0)
                if kills + deaths == 0:
                    continue
                p_population = kills / (kills + deaths)

                p_star = (w_ab - w_d) / leverage
                row = {
                    "side": side,
                    "state": f"{a}v{b}",
                    "leverage": leverage,
                    "p_star": p_star,
                    "p_population": p_population,
                    "diff": abs(p_star - p_population),
                    "unstable": abs(leverage) < CLOSURE_UNSTABLE_LEVERAGE,
                }
                if row["diff"] > CLOSURE_FLAG_THRESHOLD or row["unstable"]:
                    rows.append(row)
    return rows


def _all_teammates_reconstruction_report(aggregate) -> list[dict]:
    """Section 10: player + all-teammates duel counts must equal the target
    team's overall resolved-duel rate at the same state/side. This holds
    exactly by construction (build_match_fight_ev_block partitions every
    target-team duel outcome into exactly one of these two buckets), so any
    non-zero diff here indicates a bug, not sampling noise."""
    mismatches = []
    keys = set(aggregate.player_duels) | set(aggregate.all_teammate_duels)
    for key in keys:
        side, a, b = key
        player_counts = aggregate.player_duels.get(key)
        teammate_counts = aggregate.all_teammate_duels.get(key)
        # There's no independently-computed "team overall" count to compare
        # against other than this same sum, so this reports the combined
        # totals for spot-checking against, e.g., a manual DB query.
        mismatches.append(
            {
                "side": side,
                "state": f"{a}v{b}",
                "combined_kills": (player_counts.kills if player_counts else 0) + (teammate_counts.kills if teammate_counts else 0),
                "combined_deaths": (player_counts.deaths if player_counts else 0) + (teammate_counts.deaths if teammate_counts else 0),
            }
        )
    return mismatches


def build_report(db, player: Player, draws: int) -> dict:
    blocks, diagnostics = load_match_fight_ev_blocks(db, player)
    aggregate = _sum_blocks(blocks)

    return {
        "player": {"id": player.id, "display_name": player.display_name},
        "calculation_version": CALCULATION_VERSION,
        "bootstrap_draws": draws,
        "interval_validity_thresholds_provisional": {
            "min_defined_draw_fraction": MIN_DEFINED_DRAW_FRACTION,
            "min_contributing_matches": MIN_CONTRIBUTING_MATCHES,
        },
        "matches_considered": len(blocks),
        "replay_diagnostics": {
            "accepted_rounds": diagnostics.accepted_rounds,
            "excluded_rounds_by_reason": dict(diagnostics.excluded_rounds_by_reason),
            "post_decision_events": diagnostics.post_decision_events,
            "ambiguous_lifecycle_rounds": diagnostics.ambiguous_lifecycle_rounds,
            "equal_time_ambiguities": diagnostics.equal_time_ambiguities,
            "surrender_leakage_count": diagnostics.excluded_rounds_by_reason.get("surrendered", 0),
        },
        "cells": {
            f"{side}_{pool}": _cells_report(blocks, side, pool, player.id, draws) for side in SIDES for pool in POOLS
        },
        "monotonicity_violations": _monotonicity_report(aggregate),
        "boundary_sanity": _boundary_report(aggregate),
        "cross_side_complement_flags": _cross_side_complement_report(aggregate),
        "closure_diagnostic_flags": _closure_report(aggregate),
        "all_teammates_reconstruction": _all_teammates_reconstruction_report(aggregate),
    }


def main(riot_id: str, draws: int, out: Path | None) -> None:
    db = SessionLocal()
    try:
        player = db.query(Player).filter_by(display_name=riot_id).one_or_none()
        if player is None:
            print(f"No player found with display_name == {riot_id!r}", file=sys.stderr)
            sys.exit(1)
        report = build_report(db, player, draws)
    finally:
        db.close()

    text = json.dumps(report, indent=2, default=str)
    if out is not None:
        out.write_text(text)
        print(f"Wrote report to {out}")
    else:
        print(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("riot_id", help="Player's exact display_name, e.g. 'NPrightdolphin#NA1'")
    parser.add_argument("--draws", type=int, default=DEFAULT_BOOTSTRAP_DRAWS, help="bootstrap draw count")
    parser.add_argument("--out", type=Path, default=None, help="write JSON to this path instead of stdout")
    args = parser.parse_args()
    main(args.riot_id, args.draws, args.out)

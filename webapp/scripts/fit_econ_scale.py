r"""Run from webapp/:
    .\.venv\Scripts\python.exe scripts\fit_econ_scale.py

Computes the section 9 ANCHOR for ECON_SCALE and the predeclared early-regime
diagnostics. Read-only; never writes to the database.

The anchor: a single constant chosen so econ_component's standard deviation
over all scored player-rounds (realized mode) equals time_impact's current
standard deviation.

It is a DISPERSION CONVENTION, not an estimate, and "comparable influence"
would be too strong a claim for it: the components enter under different
top-level coefficients and with different covariance against the rest of the
score, and matching an SD will happily amplify a noisy allocation until its
spread reaches the target. What it guarantees is only that the new term is not
introduced at an arbitrary scale.

It exists because the evaluation harness CANNOT fit this constant. The harness
runs an ex-ante replay, and under use_realized=False the component is exactly 0
for every row -- that is the spec's own leakage gate. A constant multiplied by
a constant-zero column is zero, so the harness is structurally blind to it.
That is not a gap to engineer around; it follows from the component being
ex-post, which is a deliberate product decision.

Also reports the four predeclared diagnostics from
docs/superpowers/2026-09-07-predeclared-values.md, and the sensitivity grid
over ZERO_AT x SAVE_FLOOR x FULL_COMMIT plus the hard-step shape variant --
reported across, never selected on.
"""

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring import econ_component
from app.scoring.impact import build_impact_rows_for_match

ZERO_AT_GRID = (5250, 6300, 7350)
SAVE_FLOOR_GRID = (600, 1000, 1400)
FULL_COMMIT_GRID = (3400, 3900, 4400)
RESERVE_SHIFT = 1294  # M28's observed early total-wealth contrast


def _scored_rows(db, match_ids):
    econ_values, time_values = [], []
    early_zero = early_total = 0
    for match_id in match_ids:
        for row in build_impact_rows_for_match(db, match_id, enable_econ_component=True):
            econ_values.append(row.econ_component)
            time_values.append(row.time_impact)
    return econ_values, time_values, early_zero, early_total


def _early_diagnostics(db):
    """The predeclared early-regime diagnostics, computed directly from the
    round-player stats rather than from scored rows, so saturation is measured
    on f itself rather than after attribution."""
    rounds = defaultdict(dict)
    for row in db.execute(text(
        "SELECT r.match_id, r.round_number, rps.match_player_id, rps.loadout, rps.remaining, "
        "mp.team, mp.agent "
        "FROM round_player_stats rps "
        "JOIN rounds r ON r.id = rps.round_id "
        "JOIN match_players mp ON mp.id = rps.match_player_id"
    )).mappings():
        rounds[(row["match_id"], row["round_number"])][row["match_player_id"]] = dict(row)

    at_zero = at_ceiling = eligible = 0
    reserve_unchanged = reserve_eligible = 0
    for (match_id, round_number), players in rounds.items():
        if not econ_component.is_early_regime(round_number):
            continue
        next_players = rounds.get((match_id, round_number + 1))
        if not next_players:
            continue
        by_team = defaultdict(list)
        for match_player_id, row in players.items():
            by_team[row["team"]].append(match_player_id)

        for team, members in by_team.items():
            enemies = [m for m in players if players[m]["team"] != team]
            if not enemies or any(m not in next_players for m in enemies):
                continue
            wealth = sum(
                next_players[m]["loadout"] + next_players[m]["remaining"] for m in enemies
            )
            value = econ_component.denial_early(wealth, len(enemies))
            eligible += 1
            if value <= 0.0:
                at_zero += 1
            elif value >= 1.5:
                at_ceiling += 1

            shifted = max(0.0, wealth - RESERVE_SHIFT * len(enemies))
            reserve_eligible += 1
            if econ_component.denial_early(shifted, len(enemies)) == value:
                reserve_unchanged += 1

    return {
        "eligible": eligible, "at_zero": at_zero, "at_ceiling": at_ceiling,
        "reserve_eligible": reserve_eligible, "reserve_unchanged": reserve_unchanged,
    }


def main():
    db = SessionLocal()
    match_ids = [row[0] for row in db.execute(text("SELECT id FROM matches ORDER BY id")).all()]
    limit = 400
    for index, arg in enumerate(sys.argv):
        if arg == "--matches" and index + 1 < len(sys.argv):
            limit = int(sys.argv[index + 1])
    sample = match_ids[:limit]

    print("=" * 72)
    print("ECON_SCALE ANCHOR")
    print("=" * 72)
    print(f"  matches in DB: {len(match_ids):,}   sampled for the anchor: {len(sample):,}")
    print(f"  ECON_SCALE currently in the module: {econ_component.ECON_SCALE}")

    econ_values, time_values, _, _ = _scored_rows(db, sample)
    print(f"  scored player-rounds: {len(econ_values):,}")

    # The component is scored at whatever ECON_SCALE the module currently
    # carries, so divide it back out before solving for the anchor.
    raw = [v / econ_component.ECON_SCALE for v in econ_values] if econ_component.ECON_SCALE else econ_values
    econ_sd = statistics.pstdev(raw) if len(raw) > 1 else 0.0
    time_sd = statistics.pstdev(time_values) if len(time_values) > 1 else 0.0
    print(f"  SD(time_impact)            = {time_sd:.4f}")
    print(f"  SD(econ_component raw)     = {econ_sd:.6f}")
    if econ_sd > 0:
        anchor = time_sd / econ_sd
        print()
        print(f"  ANCHOR: ECON_SCALE = {anchor:.4f}")
        print("  Transcribe into app/scoring/econ_component.py's ECON_SCALE.")
        print("  Population: all scored player-rounds, realized mode. The constant")
        print("  multiplies (credit - debit) in section 6, i.e. it sits on the")
        print("  component and nowhere else.")
        print()
        print("  NOTE, and it matters: econ_component persists as an INTEGER number")
        print("  of Impact points. At the shipped placeholder of 1.0 the component's")
        print("  natural 0..1.5 range rounds to zero for every row, so it is")
        print("  invisible until this anchor is transcribed.")
    else:
        print("  DEGENERATE: the component has zero spread on this sample, so no")
        print("  anchor can be solved. Investigate before transcribing anything.")

    print()
    print("=" * 72)
    print("PREDECLARED EARLY-REGIME DIAGNOSTICS")
    print("=" * 72)
    diagnostics = _early_diagnostics(db)
    eligible = max(1, diagnostics["eligible"])
    zero_rate = 100.0 * diagnostics["at_zero"] / eligible
    ceiling_rate = 100.0 * diagnostics["at_ceiling"] / eligible
    print(f"  eligible early enemy-team-rounds: {diagnostics['eligible']:,}")
    print(f"  f saturated at 0:   {diagnostics['at_zero']:,} ({zero_rate:.2f}%)")
    print(f"  f saturated at 1.5: {diagnostics['at_ceiling']:,} ({ceiling_rate:.2f}%)")
    for label, rate in (("0", zero_rate), ("1.5", ceiling_rate)):
        if rate > 50.0:
            print(f"  *** FINDING: over 50% saturated at the {label} clamp "
                  f"({rate:.2f}%) -- reported, not gated ***")
    reserve_eligible = max(1, diagnostics["reserve_eligible"])
    print(f"  reserve insensitivity: denial_early unchanged after reducing average")
    print(f"    wealth by {RESERVE_SHIFT} credits in "
          f"{diagnostics['reserve_unchanged']:,} of {diagnostics['reserve_eligible']:,} "
          f"({100.0 * diagnostics['reserve_unchanged'] / reserve_eligible:.2f}%)")
    print("    (a sensitivity scenario built on M28's observed contrast between")
    print("     kill-count strata, NOT a causal estimate of wealth removed by kills)")

    zero_rows = sum(1 for v in econ_values if v == 0)
    print(f"  scored player-rounds with econ_component == 0: {zero_rows:,} of "
          f"{len(econ_values):,}")

    print()
    print("=" * 72)
    print("SENSITIVITY GRID -- reported across, never selected on")
    print("=" * 72)
    print(f"  {'ZERO_AT':>8} {'SAVE_FLOOR':>11} {'FULL_COMMIT':>12} {'sat@0%':>8} {'sat@1.5%':>9}")
    original = (econ_component.ZERO_AT, econ_component.SAVE_FLOOR, econ_component.FULL_COMMIT)
    try:
        for zero_at in ZERO_AT_GRID:
            for save_floor in SAVE_FLOOR_GRID:
                for full_commit in FULL_COMMIT_GRID:
                    econ_component.ZERO_AT = zero_at
                    econ_component.SAVE_FLOOR = save_floor
                    econ_component.FULL_COMMIT = full_commit
                    grid = _early_diagnostics(db)
                    n = max(1, grid["eligible"])
                    print(f"  {zero_at:>8} {save_floor:>11} {full_commit:>12} "
                          f"{100.0 * grid['at_zero'] / n:>7.2f}% "
                          f"{100.0 * grid['at_ceiling'] / n:>8.2f}%")
    finally:
        econ_component.ZERO_AT, econ_component.SAVE_FLOOR, econ_component.FULL_COMMIT = original

    print()
    print("  Shape variant (g as a hard step at 2,000/player) is a change to g,")
    print("  which does not enter f's saturation table above; it is reported with")
    print("  the component's SD once the anchor is transcribed.")
    db.close()


if __name__ == "__main__":
    main()

r"""Run from webapp/:
    .\.venv\Scripts\python.exe scripts\fit_postplant_factor.py

Part 4's fit-and-report script. Read-only; never writes to the database.

Reports, in the order the spec fixes:
  1. The OUT-OF-FOLD calibration numbers and the fold_mapping_hash -- the FIRST
     lines, above the fitted parameters, not an appendix and not a log line
     (spec, "Calibration against what actually happened": '"Loudly" is a
     requirement, not a tone').
  2. The shipped full-data table's support, pooling and diagnostics.
  3. The centring constant, its effective bounds, and the death-side residual.
  4. The predeclared sensitivity grids: nine FLOOR x CEIL pairs at W=2, and
     W in {0,1,2,3,4,6} at the shipped clamp.

Calibration is computed from OUT-OF-FOLD predictions: V is fitted on round
outcomes, so an in-sample reliability curve is near-perfect by construction and
would be a vacuous number reported as reassurance. Scoring, by contrast, uses
the full-data table -- it is an attribution computed with everything known, not
an estimate of an unseen outcome, and it has no holdout to protect.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.scoring.impact import _time_factor
from app.scoring.postplant_centering import (
    DegenerateCentering,
    solve_postplant_centering,
)
from app.scoring.postplant_factor import (
    CEIL_DEFAULT,
    FLOOR_DEFAULT,
    build_factor_table,
    extract_postplant_kills,
)
from app.scoring.postplant_value_table import (
    DEFAULT_W,
    RUNG_ANALYTIC,
    RUNG_BAND,
    RUNG_D_POOLED,
    RUNG_EXACT,
    build_value_table,
    extract_postplant_round_seconds,
)
from app.services.impact_eval import fold_mapping_hash, stable_folds

CALIBRATION_TOLERANCE = 0.05  # predeclared, over t >= 38. A finding, NOT a gate.
N_BINS = 10                   # fixed-width on [0, 1], declared in advance
FLOOR_GRID = (0.02, 0.05, 0.1)
CEIL_GRID = (1.5, 2.0, 2.5)
W_GRID = (0, 1, 2, 3, 4, 6)


class _RoundShim:
    """What _time_factor reads, so today's ramp can be evaluated per kill."""

    def __init__(self, plant_time):
        self.plant_time = plant_time
        self.planted = True
        self.exploded = False
        self.defused = False
        self.defuse_time = None


def _calibration(observations, folds):
    """Out-of-fold reliability, weighted by round-seconds.

    Deterministic terminal seconds are excluded: after a side reaches zero the
    round-second is already decided and trivially predictable, and leaving them
    in flatters the number and pads the support counts with rows that carry no
    evidence about live fights.
    """
    live = [
        o for o in observations
        if o.attackers_alive > 0 and o.defenders_alive > 0
    ]
    by_fold = defaultdict(list)
    for o in live:
        by_fold[folds.get(o.match_id, 0)].append(o)

    predictions = []  # (predicted, actual, t)
    excluded_unsupported = 0
    for fold, held_out in by_fold.items():
        training = [o for o in live if folds.get(o.match_id, 0) != fold]
        table = build_value_table(training, w=DEFAULT_W)
        for o in held_out:
            lookup = table.value(o.attackers_alive, o.defenders_alive, o.t)
            if not lookup.supported:
                excluded_unsupported += 1
                continue
            predictions.append((lookup.value, 1.0 if o.atk_won else 0.0, o.t))

    def _mace(rows):
        if not rows:
            return None, []
        bins = defaultdict(lambda: [0.0, 0.0, 0])
        for predicted, actual, _ in rows:
            index = min(int(predicted * N_BINS), N_BINS - 1)
            bins[index][0] += predicted
            bins[index][1] += actual
            bins[index][2] += 1
        total = sum(b[2] for b in bins.values())
        error = 0.0
        curve = []
        for index in sorted(bins):
            predicted_sum, actual_sum, n = bins[index]
            mean_predicted, mean_actual = predicted_sum / n, actual_sum / n
            curve.append((index / N_BINS, mean_predicted, mean_actual, n))
            error += (n / total) * abs(mean_predicted - mean_actual)
        return error, curve

    overall, curve = _mace(predictions)
    late, late_curve = _mace([r for r in predictions if r[2] >= 38])
    return {
        "overall": overall, "curve": curve,
        "late": late, "late_curve": late_curve,
        "n_scored": len(predictions), "n_excluded_unsupported": excluded_unsupported,
    }


def _rung_census(value_table, kills):
    census = defaultdict(int)
    for kill in kills:
        lookup = value_table.value(kill.attackers_alive, kill.defenders_alive, kill.t)
        census[lookup.rung] += 1
    return census


def _score_population(factors, kills, plant_times):
    """Per-kill ramp factor, new factor and support flag."""
    ramp, new, supported, raw_ratios = [], [], [], []
    for kill in kills:
        shim = _RoundShim(plant_times[kill.round_id])
        ramp.append(_time_factor(shim, plant_times[kill.round_id] + kill.t))
        ratio = factors.raw_ratio(
            kill.attackers_alive, kill.defenders_alive, kill.t, kill.victim_is_attacker
        )
        is_supported = ratio is not None
        supported.append(is_supported)
        if is_supported:
            raw_ratios.append(ratio)
        new.append(factors.factor(
            kill.attackers_alive, kill.defenders_alive, kill.t, kill.victim_is_attacker
        ))
    return ramp, new, supported, raw_ratios


def _percentile(values, q):
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def main():
    db = SessionLocal()
    observations = extract_postplant_round_seconds(db)
    kills = extract_postplant_kills(db)
    plant_times = {}
    from sqlalchemy import text
    for row in db.execute(text("SELECT id, plant_time FROM rounds WHERE plant_time IS NOT NULL")).mappings():
        plant_times[row["id"]] = row["plant_time"]
    db.close()

    match_ids = sorted({o.match_id for o in observations})
    folds = stable_folds(match_ids)

    # ---------------------------------------------------------------- 1
    print("=" * 72)
    print("CALIBRATION (out-of-fold) -- reported first, per the spec")
    print("=" * 72)
    calibration = _calibration(observations, folds)
    print(f"  fold_mapping_hash: {fold_mapping_hash(folds)}")
    late = calibration["late"]
    overall = calibration["overall"]
    print(f"  mean absolute calibration error, t >= 38: "
          f"{late:.4f}" if late is not None else "  t >= 38: no scored rows")
    print(f"  mean absolute calibration error, overall: "
          f"{overall:.4f}" if overall is not None else "  overall: no scored rows")
    if late is not None and late > CALIBRATION_TOLERANCE:
        worst = max(calibration["late_curve"], key=lambda r: abs(r[1] - r[2]))
        print(f"  *** CALIBRATION EXCEEDS THE PREDECLARED TOLERANCE of "
              f"{CALIBRATION_TOLERANCE}: {late:.4f} ***")
        print(f"  *** worst-calibrated band: [{worst[0]:.1f}, {worst[0] + 1 / N_BINS:.1f}) "
              f"predicted {worst[1]:.3f} actual {worst[2]:.3f} over {worst[3]:,} round-seconds ***")
        print("  *** This is a FINDING, not a gate: Part 4 still ships and still "
              "scores. Record it in the measurement register, and carry this "
              "number with any later report quoting post-plant factors. ***")
    else:
        print(f"  within the predeclared tolerance of {CALIBRATION_TOLERANCE}")
    print(f"  scored round-seconds: {calibration['n_scored']:,}   "
          f"excluded (unsupported): {calibration['n_excluded_unsupported']:,}")
    print("  reliability curve, t >= 38 (bin, predicted, actual, n):")
    for lower, predicted, actual, n in calibration["late_curve"]:
        print(f"    [{lower:.1f}, {lower + 1 / N_BINS:.1f})  pred={predicted:.3f}  "
              f"act={actual:.3f}  n={n:,}")

    # ---------------------------------------------------------------- 2
    print()
    print("=" * 72)
    print("SHIPPED TABLE (full data)")
    print("=" * 72)
    print(f"  post-plant round-seconds: {len(observations):,}")
    print(f"  scored post-plant kills:  {len(kills):,}")
    value_table = build_value_table(observations, w=DEFAULT_W)
    factors = build_factor_table(value_table, kills)
    census = _rung_census(value_table, kills)
    print("  pooling rung of each scored kill's own V cell:")
    for rung in (RUNG_EXACT, RUNG_D_POOLED, RUNG_BAND, RUNG_ANALYTIC, "unsupported"):
        print(f"    {rung:<12} {census.get(rung, 0):,}")
    ramp, new, supported, raw_ratios = _score_population(factors, kills, plant_times)
    print(f"  supported (a, d, victim_side) cells: {factors.diagnostics['supported_cells']:,}")
    print(f"  kills falling back to 1.0:           {sum(1 for s in supported if not s):,}")
    for key in ("non_positive_denominators", "cells_with_too_few_eligible_seconds",
                "cells_with_no_scored_weight", "negative_numerators",
                "floor_bindings", "ceiling_bindings"):
        print(f"    {key:<36} {factors.diagnostics.get(key, 0):,}")
    if raw_ratios:
        print("  pre-clamp raw ratio distribution (the quantity 'bound inert' is "
              "defined on):")
        print(f"    min={min(raw_ratios):.4f}  p1={_percentile(raw_ratios, 0.01):.4f}  "
              f"p50={_percentile(raw_ratios, 0.50):.4f}  "
              f"p99={_percentile(raw_ratios, 0.99):.4f}  max={max(raw_ratios):.4f}")

    # ---------------------------------------------------------------- 3
    print()
    print("=" * 72)
    print("CENTRING")
    print("=" * 72)
    # Centring is on CONTRIBUTION, not on the factor: the scorer computes
    # kill_order_bonus * time_factor, and mean(f) = 1 preserves the mean
    # contribution only if f and kill_order_bonus are independent -- which they
    # demonstrably are not, since both key on the same alive counts.
    kill_order_bonuses = [k.kill_order_bonus for k in kills]
    print(f"  weighted by real kill_order_bonus (mean "
          f"{sum(kill_order_bonuses) / max(1, len(kill_order_bonuses)):.1f})")
    print("  NOTE: traded_factor is not threaded here, so the death-side residual")
    print("  below is computed at T=1 for every kill and is a placeholder, not the")
    print("  reportable residual.")
    try:
        centering = solve_postplant_centering(
            kill_order_bonuses=kill_order_bonuses, ramp_factors=ramp,
            new_factors=new, supported=supported,
            traded_factors=[1.0] * len(kills),
        )
        print(f"  c = {centering.c:.6f}   |c-1| = {abs(centering.c - 1):.4f}")
        print(f"  effective bounds: [{centering.c * FLOOR_DEFAULT:.4f}, "
              f"{centering.c * CEIL_DEFAULT:.4f}]")
        print(f"  death-side residual: {centering.death_side_residual:+.4%}  "
              f"(tolerance 2%, reported not gated)")
        print(f"  supported kills: {centering.supported_kills:,}   "
              f"fallback kills: {centering.fallback_kills:,}")
    except DegenerateCentering as exc:
        print(f"  DEGENERATE: {exc}")

    # ---------------------------------------------------------------- 4
    print()
    print("=" * 72)
    print("SENSITIVITY: the nine FLOOR x CEIL pairs at W=2")
    print("=" * 72)
    print(f"  {'FLOOR':>6} {'CEIL':>6} {'floor%':>8} {'ceil%':>8} {'c':>9} "
          f"{'eff_lo':>8} {'eff_hi':>8}")
    for floor in FLOOR_GRID:
        for ceil in CEIL_GRID:
            grid_factors = build_factor_table(value_table, kills, floor=floor, ceil=ceil)
            g_ramp, g_new, g_supported, _ = _score_population(grid_factors, kills, plant_times)
            n_scored = max(1, sum(1 for s in g_supported if s))
            floor_rate = 100.0 * grid_factors.diagnostics.get("floor_bindings", 0) / n_scored
            ceil_rate = 100.0 * grid_factors.diagnostics.get("ceiling_bindings", 0) / n_scored
            try:
                g_c = solve_postplant_centering(
                    kill_order_bonuses=kill_order_bonuses, ramp_factors=g_ramp,
                    new_factors=g_new, supported=g_supported,
                    traded_factors=[1.0] * len(kills),
                ).c
                print(f"  {floor:>6.2f} {ceil:>6.2f} {floor_rate:>7.2f}% {ceil_rate:>7.2f}% "
                      f"{g_c:>9.4f} {g_c * floor:>8.4f} {g_c * ceil:>8.4f}")
            except DegenerateCentering:
                print(f"  {floor:>6.2f} {ceil:>6.2f} {floor_rate:>7.2f}% {ceil_rate:>7.2f}% "
                      f"{'DEGENERATE':>9}")

    print()
    print("=" * 72)
    print("SENSITIVITY: W at the shipped clamp")
    print("=" * 72)
    print(f"  {'W':>3} {'supported_cells':>16} {'fallback_kills':>15} {'c':>9}")
    for w in W_GRID:
        w_table = build_value_table(observations, w=w)
        w_factors = build_factor_table(w_table, kills)
        w_ramp, w_new, w_supported, _ = _score_population(w_factors, kills, plant_times)
        try:
            w_c = f"{solve_postplant_centering(kill_order_bonuses=kill_order_bonuses, ramp_factors=w_ramp, new_factors=w_new, supported=w_supported, traded_factors=[1.0] * len(kills)).c:.4f}"
        except DegenerateCentering:
            w_c = "DEGENERATE"
        print(f"  {w:>3} {w_factors.diagnostics['supported_cells']:>16,} "
              f"{sum(1 for s in w_supported if not s):>15,} {w_c:>9}")


if __name__ == "__main__":
    main()

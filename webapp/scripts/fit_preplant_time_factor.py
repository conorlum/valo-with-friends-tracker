"""Run from webapp/:
    .\\.venv\\Scripts\\python.exe scripts\\fit_preplant_time_factor.py

Fits Part 3's exact-state logistic regression against the real DB, runs the
predeclared nested comparison and temporal-split checks, selects k per the
predeclared rule, solves the centring constant, and prints the constants to
transcribe into app/scoring/preplant_scalar.py. Read-only; never writes to
the database.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if os.path.exists(".env.remote"):
    for line in open(".env.remote"):
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.preplant_centering import solve_kill_side_centering
from app.scoring.preplant_fit_support import extract_preplant_observations_with_factors
from app.scoring.preplant_k_selection import select_k
from app.scoring.preplant_time_model import fit_preplant_time_model
from app.services.stats_math import cluster_bootstrap_ci


TARGET_DENOMINATOR_SQL = """
    SELECT COUNT(*) FROM kill_events k
    JOIN match_players mp_k ON mp_k.id = k.killer_match_player_id
    JOIN match_players mp_d ON mp_d.id = k.death_match_player_id
    JOIN rounds r ON r.id = k.round_id
    WHERE mp_k.team != mp_d.team AND (r.outcome IS NULL OR r.outcome NOT LIKE '%Surrendered%')
"""

# The 70th-percentile temporal split point is computed on match played_at
# once, then held fixed for both the pre- and post-split fits -- "chosen by
# date and fixed before fitting" (spec, "Temporal split").
TEMPORAL_SPLIT_SQL = """
    SELECT m.id, m.played_at
    FROM matches m
    WHERE m.played_at IS NOT NULL
    ORDER BY m.played_at
"""


def _print_k_table(result):
    for row in result.table:
        print(f"  k={row['k']:.2f}  floor%all={row['floor_rate_all']:.2f}  "
              f"ceil%all={row['ceiling_rate_all']:.2f}  floor%aff={row['floor_rate_affected']:.2f}  "
              f"ceil%aff={row['ceiling_rate_affected']:.2f}")
    print(f"  SELECTED k={result.selected_k}  (branch {result.branch})")


def main():
    db = SessionLocal()
    total_kills = db.execute(text(TARGET_DENOMINATOR_SQL)).scalar()
    print(f"target denominator (all non-self kills, non-surrendered rounds): {total_kills:,}")

    observations, kobs, trades = extract_preplant_observations_with_factors(db)
    print(f"affected population (pre-plant kills, non-phantom planted rounds): {len(observations):,}")

    # -- Nested comparison: does the side interaction earn its keep? --------
    with_side = fit_preplant_time_model(observations, include_side_interaction=True)
    without_side = fit_preplant_time_model(observations, include_side_interaction=False)
    print("\n=== nested comparison: side interaction ===")
    print(f"  with side:    atk={with_side.intercept_atk:+.4f} {with_side.slope_atk:+.4f}*adv   "
          f"def={with_side.intercept_def:+.4f} {with_side.slope_def:+.4f}*adv   "
          f"shape_mid_ratio={with_side.shape_mid_ratio:+.4f}")
    print(f"  without side: pooled={without_side.intercept_atk:+.4f} {without_side.slope_atk:+.4f}*adv   "
          f"shape_mid_ratio={without_side.shape_mid_ratio:+.4f}")
    from app.services.stats_math import weighted_log_loss
    import numpy as np

    def _predicted(fit, obs_list):
        # The fit's own linear predictor: intercept + state_effect +
        # shape(dt)*logit_lift(adv,side). Review finding 13: the global
        # intercept used to be missing here, because PreplantFit had no field
        # for it -- state_effects pins the reference state to 0.0, so the
        # reconstruction had no level at all and the loss and temporal
        # reliability figures below were not predictions from the fitted
        # model. Both remain descriptive, and neither is a gate.
        preds = []
        for o in obs_list:
            eta = fit.linear_predictor(o.dt, o.adv, o.is_attacker, o.exact_state)
            preds.append(1.0 / (1.0 + np.exp(-eta)))
        return preds

    usable = [o for o in observations if o.round_won_by_killer_team is not None]
    labels = [1.0 if o.round_won_by_killer_team else 0.0 for o in usable]
    loss_with = weighted_log_loss(_predicted(with_side, usable), labels)
    loss_without = weighted_log_loss(_predicted(without_side, usable), labels)
    print(f"  in-sample log loss: with_side={loss_with:.5f}  without_side={loss_without:.5f}  "
          f"(lower is better fit; this is descriptive, not a held-out comparison)")

    # -- k selection, shipped (full-data) population -------------------------
    print("\n=== k selection, shipped (full-data) population ===")
    result = select_k(with_side, observations, total_kills_denominator=total_kills)
    _print_k_table(result)

    # -- Centring --------------------------------------------------------
    print("\n=== centring ===")
    centering = solve_kill_side_centering(with_side, result.selected_k, observations, kobs, trades)
    tolerance_note = "WITHIN" if abs(centering.death_side_residual) <= 0.02 else "EXCEEDS"
    print(f"  c={centering.c:.4f}   |c-1|={abs(centering.c - 1):.4f}   "
          f"death-side residual={centering.death_side_residual:+.4%}  "
          f"({tolerance_note} the predeclared 2% tolerance)")

    # -- Temporal split stability check (a stability check, not a holdout) --
    print("\n=== temporal split (70th percentile of played_at) ===")
    match_dates = list(db.execute(text(TEMPORAL_SPLIT_SQL)).fetchall())
    if len(match_dates) < 10:
        print("  too few dated matches to run the split; skipping")
    else:
        split_index = int(len(match_dates) * 0.70)
        pre_ids = {row[0] for row in match_dates[:split_index]}
        post_ids = {row[0] for row in match_dates[split_index:]}
        pre_obs = [o for o in observations if o.match_id in pre_ids]
        post_obs = [o for o in observations if o.match_id in post_ids]
        print(f"  pre-70th: {len(pre_obs):,} observations, {len(pre_ids):,} matches")
        print(f"  post-70th: {len(post_obs):,} observations, {len(post_ids):,} matches")
        pre_fit = fit_preplant_time_model(pre_obs, include_side_interaction=True)
        pre_k_result = select_k(pre_fit, pre_obs, total_kills_denominator=max(len(pre_obs), 1))
        print(f"  pre-70th fit: atk={pre_fit.intercept_atk:+.4f} {pre_fit.slope_atk:+.4f}*adv   "
              f"def={pre_fit.intercept_def:+.4f} {pre_fit.slope_def:+.4f}*adv")
        print(f"  pre-70th's OWN k selection (per predeclared-values.md 1.5.1, "
              f"never reused for shipped): k={pre_k_result.selected_k} (branch {pre_k_result.branch})")

        # Reliability check: does the pre-70th fit's amplitude line
        # reproduce the post-70th observations' win rate within a
        # match-clustered bootstrap interval? Reported, not gated (spec,
        # "Primary: a temporal STABILITY CHECK, not a holdout").
        post_usable = [o for o in post_obs if o.round_won_by_killer_team is not None]
        post_groups: dict[int, list] = {}
        for o in post_usable:
            post_groups.setdefault(o.match_id, []).append(o)

        def _mean_predicted_minus_actual(sample):
            flat = [o for group in sample for o in group]
            if not flat:
                return None
            predicted = _predicted(pre_fit, flat)
            actual = [1.0 if o.round_won_by_killer_team else 0.0 for o in flat]
            return float(np.mean(predicted) - np.mean(actual))

        lo, hi = cluster_bootstrap_ci(_mean_predicted_minus_actual, post_groups, draws=500)
        point = _mean_predicted_minus_actual(list(post_groups.values()))
        print(f"  pre-70th fit vs post-70th outcomes, mean(predicted - actual): "
              f"{point:+.4f} [{lo:+.4f}, {hi:+.4f}]")

    # -- What to transcribe -----------------------------------------------
    print("\n=== transcribe into app/scoring/preplant_scalar.py ===")
    print(f"INTERCEPT_ATK = {with_side.intercept_atk:.6f}")
    print(f"SLOPE_ATK = {with_side.slope_atk:.6f}")
    print(f"INTERCEPT_DEF = {with_side.intercept_def:.6f}")
    print(f"SLOPE_DEF = {with_side.slope_def:.6f}")
    print(f"SHAPE_MID_RATIO = {with_side.shape_mid_ratio:.6f}")
    print(f"K = {result.selected_k}")
    print(f"CENTERING_C = {centering.c:.6f}")


if __name__ == "__main__":
    main()

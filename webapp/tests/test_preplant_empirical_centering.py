"""The Part 3 kill-side centring gate, run against the SHIPPED empirical
curve (app.scoring.preplant_empirical_factor) rather than the superseded
parametric model that solve_kill_side_centering scores.

Population, declared once and asserted here (spec, 'The gate is on the KILL
side'; candidate doc, 'Boundaries and remaining integration work'):

  AFFECTED  -- non-self pre-plant kills in non-phantom, non-surrendered
               planted rounds; every observation extract_preplant_
               observations_with_factors returns. M5: 168,432.
  SCORED    -- the subset with 0 < dt <= 30, the only kills the curve moves.
  FALLBACK  -- the rest (dt > 30). The curve returns exactly 1.0 there and
               the centring constant is NOT applied to them, so they pay
               exactly what they pay today.

c is therefore solved over SCORED alone. Applying it to FALLBACK too would
mark down 37,926 kills the curve has no estimate for; leaving them at 1.0
still preserves the AFFECTED total, because they are unchanged on both
sides of the equation.
"""

import pytest

from app.scoring.preplant_centering import (
    DEATH_RESIDUAL_TOLERANCE,
    solve_empirical_kill_side_centering,
)
from app.scoring.preplant_empirical_factor import empirical_preplant_factor
from app.scoring.preplant_time_model import PreplantKillObservation

STRENGTH = 3.0


def _obs(dt, is_attacker=True):
    return PreplantKillObservation(0, 0, dt, 0, is_attacker, "5v5", True)


def _scored_factor(obs):
    return empirical_preplant_factor(obs.dt, obs.is_attacker, strength=STRENGTH)


def test_total_kill_side_contribution_over_the_declared_population_is_preserved():
    # THE GATE. sum over AFFECTED of kill_order_bonus * F must equal its value
    # under today's flat 1.0, where F = c*s inside SCORED and exactly 1.0 in
    # FALLBACK. Mixed sides and a fallback kill, so neither the side split nor
    # the population split can cancel out by accident.
    observations = [_obs(2.5), _obs(12.0, False), _obs(28.75), _obs(41.0, False)]
    bonuses = [130.0, 250.0, 90.0, 175.0]
    result = solve_empirical_kill_side_centering(
        observations, bonuses, [1.0] * 4, strength=STRENGTH,
    )

    scored_total = sum(
        kob * result.c * _scored_factor(o)
        for o, kob in zip(observations, bonuses)
        if 0 < o.dt <= 30
    )
    fallback_total = sum(
        kob for o, kob in zip(observations, bonuses) if not 0 < o.dt <= 30
    )
    assert scored_total + fallback_total == pytest.approx(sum(bonuses), rel=1e-12)


def test_a_fallback_kill_does_not_move_c():
    # Trap 2. The FITTING population and the SCORED population are not the
    # same set. c is solved on SCORED, so adding a dt>30 kill -- which the
    # curve returns exactly 1.0 for, and which c is not applied to -- must
    # leave c bit-identical. Solving over the whole affected population
    # instead would dilute c toward 1 and fail here.
    scored = [_obs(3.0), _obs(18.0, False)]
    without = solve_empirical_kill_side_centering(
        scored, [100.0, 100.0], [1.0, 1.0], strength=STRENGTH,
    )
    with_fallback = solve_empirical_kill_side_centering(
        [*scored, _obs(55.0)], [100.0, 100.0, 100.0], [1.0, 1.0, 1.0], strength=STRENGTH,
    )
    assert with_fallback.c == without.c


def test_c_is_solved_on_the_exact_fractional_dt_not_a_floored_second():
    # Trap 3, at the API level. Part 4's constant came out 0.7% low because
    # its centring baseline read floor(t) while runtime paid the real
    # timestamp. dt=10.0 and dt=10.9 sit on different points of the fitted
    # interpolation, so they must produce different constants.
    on_the_second = solve_empirical_kill_side_centering(
        [_obs(10.0)], [100.0], [1.0], strength=STRENGTH,
    )
    fractional = solve_empirical_kill_side_centering(
        [_obs(10.9)], [100.0], [1.0], strength=STRENGTH,
    )
    assert on_the_second.c != fractional.c


def test_scored_and_fallback_split_is_reported():
    observations = [_obs(1.0), _obs(30.0), _obs(30.5), _obs(70.0, False)]
    result = solve_empirical_kill_side_centering(
        observations, [100.0, 200.0, 400.0, 800.0], [1.0] * 4, strength=STRENGTH,
    )
    # dt == 30 is scored (the curve's cutoff is inclusive); dt == 30.5 is not.
    assert (result.scored_kills, result.fallback_kills) == (2, 2)
    assert result.scored_kill_order_mass == pytest.approx(300.0)
    assert result.fallback_kill_order_mass == pytest.approx(1200.0)


def test_death_side_residual_is_reported_over_the_whole_affected_population():
    # mean(K*T*c*s) / mean(K*T) - 1, with FALLBACK kills entering both means
    # at their unchanged 1.0. Never solved for, only reported.
    observations = [_obs(4.0), _obs(22.0, False), _obs(48.0)]
    bonuses = [150.0, 120.0, 200.0]
    trades = [1.0, 0.3, 0.8]
    result = solve_empirical_kill_side_centering(
        observations, bonuses, trades, strength=STRENGTH,
    )
    expected_numerator = sum(
        kob * t * (result.c * _scored_factor(o) if 0 < o.dt <= 30 else 1.0)
        for o, kob, t in zip(observations, bonuses, trades)
    )
    expected_denominator = sum(kob * t for kob, t in zip(bonuses, trades))
    assert result.death_side_residual == pytest.approx(
        expected_numerator / expected_denominator - 1.0
    )


def test_residual_is_zero_when_the_traded_factor_is_uniform():
    # A uniform T reduces the death-side mean to the kill-side mean, which
    # the gate has already pinned exactly -- so any nonzero residual on the
    # real data comes from the T/s correlation (M20), not from T itself.
    observations = [_obs(2.0), _obs(16.0, False), _obs(29.0)]
    result = solve_empirical_kill_side_centering(
        observations, [110.0, 240.0, 95.0], [1.0, 1.0, 1.0], strength=STRENGTH,
    )
    assert result.death_side_residual == pytest.approx(0.0, abs=1e-12)
    assert abs(result.death_side_residual) <= DEATH_RESIDUAL_TOLERANCE


def test_c_is_one_when_every_kill_falls_back():
    result = solve_empirical_kill_side_centering(
        [_obs(35.0), _obs(60.0, False)], [100.0, 100.0], [1.0, 1.0], strength=STRENGTH,
    )
    assert result.c == 1.0
    assert result.death_side_residual == pytest.approx(0.0)


def test_strength_zero_is_neutral_and_gives_c_of_one():
    observations = [_obs(3.0), _obs(20.0, False)]
    result = solve_empirical_kill_side_centering(
        observations, [100.0, 100.0], [1.0, 1.0], strength=0.0,
    )
    assert result.c == pytest.approx(1.0)


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        solve_empirical_kill_side_centering(
            [_obs(2.0), _obs(3.0)], [100.0], [1.0, 1.0], strength=STRENGTH,
        )

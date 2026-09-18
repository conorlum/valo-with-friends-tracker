"""The kill-side centring gate (spec, 'The gate is on the KILL side') and
its predeclared 2%-tolerance death-side residual (M20). dt is
seconds_to_plant -- POSITIVE before the plant."""

import pytest

from app.scoring.preplant_centering import solve_kill_side_centering
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation


def _fit(intercept_atk=0.0, slope_atk=0.0, intercept_def=0.0, slope_def=0.0, shape_mid_ratio=1.0):
    return PreplantFit(
        intercept_atk=intercept_atk, slope_atk=slope_atk,
        intercept_def=intercept_def, slope_def=slope_def,
        shape_mid_ratio=shape_mid_ratio,
        state_effects={}, include_side_interaction=True, n_observations=0,
    )


def _obs(dt, adv=0, is_attacker=True):
    return PreplantKillObservation(0, 0, dt, adv, is_attacker, "5v5", True)


def test_zero_amplitude_gives_c_equal_to_one():
    # With logit_lift == 0 everywhere, the raw scalar is exactly 1 for every
    # kill, so c must come out to exactly 1 (no rescaling needed).
    obs = [_obs(5.0), _obs(20.0), _obs(2.0)]
    result = solve_kill_side_centering(
        _fit(), k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 150.0, 200.0], traded_factors=[1.0, 1.0, 1.0],
    )
    assert result.c == pytest.approx(1.0)
    assert result.death_side_residual == pytest.approx(0.0)


def test_c_is_positive_and_below_one_when_scalar_averages_above_one():
    fit = _fit(intercept_atk=0.5, intercept_def=0.5)
    obs = [_obs(2.0), _obs(3.0), _obs(1.0)]  # all near the plateau -> scalar > 1
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 100.0, 100.0], traded_factors=[1.0, 1.0, 1.0],
    )
    assert 0.0 < result.c < 1.0


def test_c_is_above_one_when_scalar_averages_below_one():
    fit = _fit(intercept_atk=-0.5, intercept_def=-0.5)
    obs = [_obs(2.0), _obs(3.0), _obs(1.0)]
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 100.0, 100.0], traded_factors=[1.0, 1.0, 1.0],
    )
    assert result.c > 1.0


def test_death_side_residual_is_zero_when_traded_factor_is_uniform():
    # T uniform means the T-weighted residual reduces to the same average
    # as the kill side, which the centring already pins to exactly 1 --
    # this is a proxy for M20's claim that the residual comes from the T/s
    # CORRELATION, not from T itself.
    fit = _fit(intercept_atk=0.6, intercept_def=0.6)
    obs = [_obs(2.0), _obs(20.0), _obs(28.0)]
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 100.0, 100.0], traded_factors=[1.0, 1.0, 1.0],
    )
    assert result.death_side_residual == pytest.approx(0.0, abs=1e-9)


def test_death_side_residual_is_nonzero_when_traded_factor_correlates_with_scalar():
    # T correlates with s: the untraded (T=1) kill is the near-plant one
    # (large s), the traded (T=0) kill is the far one (s~1) -- this is the
    # M20 mechanism (T and proximity co-vary through advantage), and the
    # T-weighted residual must pick it up even though the kill-side c
    # (weighted by K alone, not K*T) does not correct for it.
    fit = _fit(intercept_atk=0.6, intercept_def=0.6)
    obs = [_obs(2.0), _obs(28.0)]  # near (big lift), far (~no lift)
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 100.0], traded_factors=[1.0, 0.0],
    )
    assert result.death_side_residual != pytest.approx(0.0, abs=1e-9)


def test_c_falls_back_to_one_when_every_kill_order_bonus_is_zero():
    fit = _fit(intercept_atk=0.6)
    obs = [_obs(2.0)]
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs, kill_order_bonuses=[0.0], traded_factors=[1.0],
    )
    assert result.c == pytest.approx(1.0)
    assert result.death_side_residual == pytest.approx(0.0)


def test_mismatched_lengths_raise():
    fit = _fit()
    with pytest.raises(ValueError):
        solve_kill_side_centering(
            fit, k=1.0, observations=[_obs(2.0), _obs(3.0)],
            kill_order_bonuses=[100.0], traded_factors=[1.0, 1.0],
        )

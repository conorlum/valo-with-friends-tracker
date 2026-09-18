"""Behavioral checks for the review candidate; no DB or scoring writes."""

import math

import numpy as np
import pytest

from app.scoring.preplant_empirical_factor import (
    EmpiricalTimingCurve, empirical_preplant_factor, smooth_bucket_rates,
)


def test_factor_units_and_interpolation():
    curve = EmpiricalTimingCurve((.5, 1.5), (.70, .80), .65)
    assert curve.adjusted_rate(1.) == pytest.approx(.75)
    assert curve.factor(1.) == pytest.approx(1.10)
    assert curve.factor(1., strength=2.) == pytest.approx(1.20)
    assert curve.factor(1., strength=0.) == 1.


@pytest.mark.parametrize('dt', [None, math.nan, math.inf, -math.inf, -1., 0., 30.00001, 45.])
def test_unsupported_events_are_neutral(dt):
    assert empirical_preplant_factor(dt, True) == 1.


def test_forward_evaluation_is_neutral_for_both_sides():
    for side in [False, True]:
        for dt in [.001, 4., 16., 30.]:
            assert empirical_preplant_factor(dt, side, strength=100., use_realized=False) == 1.


def test_empirical_attacker_order_is_retained():
    # The measured association is higher at b16 than b4. No constraint or
    # assertion says the factor must rise monotonically toward planting.
    assert empirical_preplant_factor(15.5, True) > empirical_preplant_factor(3.5, True)
    assert empirical_preplant_factor(3.5, False) > empirical_preplant_factor(15.5, False)


def test_factor_is_bounded_under_extreme_strength():
    for side in [False, True]:
        factors = [empirical_preplant_factor(float(t), side, strength=100.) for t in np.linspace(.01, 30., 500)]
        assert min(factors) >= .2
        assert max(factors) <= 1.7


@pytest.mark.parametrize('strength', [-1., math.nan, math.inf])
def test_invalid_strength_is_rejected(strength):
    with pytest.raises(ValueError):
        empirical_preplant_factor(4., True, strength=strength)


def test_smoothing_preserves_constant_and_linear_signals():
    for p in [np.full(30, .6), np.linspace(.4, .8, 30)]:
        fitted, _ = smooth_bucket_rates(p, p-.03, p+.03, penalty=4.)
        assert fitted == pytest.approx(p)


def test_smoothing_reduces_alternating_noise_without_forcing_monotonicity():
    t = np.arange(30)
    signal = .6 + .1*np.sin(t/5)
    observations = signal + .025*(-1.)**t
    fitted, _ = smooth_bucket_rates(observations, observations-.04, observations+.04)
    assert np.mean((fitted-signal)**2) < np.mean((observations-signal)**2)
    assert np.any(np.diff(fitted)>0) and np.any(np.diff(fitted)<0)


def test_zero_penalty_recovers_input_rates():
    p = np.linspace(.5, .8, 30)
    fitted, _ = smooth_bucket_rates(p, p-.02, p+.02, penalty=0.)
    assert fitted == pytest.approx(p)


def test_reference_is_a_category_not_second_31():
    curve = EmpiricalTimingCurve((.5,29.5),(.7,.8),.6)
    assert curve.factor(30.) == pytest.approx(1.2)
    assert curve.factor(30.000001) == 1.


@pytest.mark.parametrize('times,rates,ref', [((1.,1.),(.5,.6),.5), ((1.,2.),(.5,1.1),.5), ((1.,2.),(.5,.6),math.nan)])
def test_invalid_curve_is_rejected(times,rates,ref):
    with pytest.raises(ValueError):
        EmpiricalTimingCurve(times,rates,ref)

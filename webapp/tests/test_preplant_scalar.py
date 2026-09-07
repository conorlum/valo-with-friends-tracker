"""Leakage gate (exact) and clamp/monotonicity behaviour for the dormant
Part 3 runtime scalar. dt is seconds_to_plant -- POSITIVE before the plant,
so 'near the plant' is a SMALL dt and 'far' is a LARGE one."""

import pytest

from app.scoring import preplant_scalar
from app.scoring.preplant_scalar import preplant_proximity_scalar


def test_use_realized_false_is_exactly_one_regardless_of_inputs():
    for dt in (45.0, 30.0, 20.0, 10.0, 1.0):
        for adv in (-3, -1, 0, 1, 2):
            for is_attacker in (True, False):
                assert preplant_proximity_scalar(dt, adv, is_attacker, use_realized=False) == 1.0


def test_placeholder_constants_make_realized_mode_also_identity():
    # Confirms the module's own claim: with the shipped placeholders, even
    # use_realized=True changes nothing, so importing this module today is
    # inert everywhere it might accidentally be wired in.
    for dt in (30.0, 20.0, 10.0, 5.0, 1.0):
        assert preplant_proximity_scalar(dt, 1, True, use_realized=True) == pytest.approx(1.0)


def test_scalar_rises_toward_plant_for_positive_amplitude(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", 0.6)
    monkeypatch.setattr(preplant_scalar, "SLOPE_ATK", 0.0)
    monkeypatch.setattr(preplant_scalar, "K", 1.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)

    far = preplant_proximity_scalar(30.0, 0, True, use_realized=True)
    near = preplant_proximity_scalar(2.0, 0, True, use_realized=True)
    assert far == pytest.approx(1.0)
    assert near > far  # positive amplitude -> scalar RISES toward the plant


def test_scalar_falls_toward_plant_for_negative_amplitude_but_stays_positive(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", -0.6)
    monkeypatch.setattr(preplant_scalar, "SLOPE_ATK", 0.0)
    monkeypatch.setattr(preplant_scalar, "K", 1.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)

    far = preplant_proximity_scalar(30.0, 0, True, use_realized=True)
    near = preplant_proximity_scalar(2.0, 0, True, use_realized=True)
    assert near < far  # negative amplitude -> scalar FALLS toward the plant
    assert near > 0.0   # never at or below zero


def test_scalar_never_at_or_below_zero_even_with_extreme_amplitude(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", -50.0)
    monkeypatch.setattr(preplant_scalar, "K", 10.0)
    for dt in (25.0, 10.0, 1.0):
        assert preplant_proximity_scalar(dt, 2, True, use_realized=True) > 0.0


def test_far_end_is_always_exactly_one_before_centering(monkeypatch):
    # dt >= 30 pins shape() to 0 regardless of the fitted amplitude, so the
    # raw scalar is exactly 1 there -- centering_c aside.
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", 0.9)
    monkeypatch.setattr(preplant_scalar, "SLOPE_ATK", 0.5)
    monkeypatch.setattr(preplant_scalar, "K", 2.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)
    assert preplant_proximity_scalar(30.0, 2, True, use_realized=True) == pytest.approx(1.0)


def test_centering_c_scales_the_whole_clamped_scalar(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", 0.6)
    monkeypatch.setattr(preplant_scalar, "K", 1.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)
    base = preplant_proximity_scalar(2.0, 0, True, use_realized=True)

    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 0.9)
    scaled = preplant_proximity_scalar(2.0, 0, True, use_realized=True)
    assert scaled == pytest.approx(base * 0.9)

"""Correctness tests for the pure numeric layer. Every case has an
analytically known answer -- no fixtures, no DB, no randomness except
explicitly seeded bootstrap draws."""

import numpy as np
import pytest

from app.services.stats_math import (
    auc,
    log_loss,
    point_biserial,
    sigmoid,
    weighted_log_loss,
)


def test_sigmoid_does_not_overflow_on_large_magnitudes():
    with np.errstate(over="raise"):
        out = sigmoid(np.array([-1e6, 0.0, 1e6]))
    assert out[0] == 0.0
    assert out[1] == 0.5
    assert out[2] == 1.0


def test_auc_perfect_separation():
    assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0


def test_auc_perfectly_inverted():
    assert auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0


def test_auc_all_tied_is_one_half():
    assert auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.5


def test_auc_single_class_is_nan():
    assert np.isnan(auc([0.1, 0.9], [1, 1]))


def test_log_loss_coin_flip_is_ln2():
    assert abs(log_loss([0.5, 0.5], [1, 0]) - np.log(2)) < 1e-12


def test_weighted_log_loss_respects_weights():
    """Row 0 is predicted well, row 1 badly. Up-weighting row 1 must raise
    the loss."""
    light = weighted_log_loss([0.99, 0.01], [1.0, 1.0], [1.0, 1.0])
    heavy = weighted_log_loss([0.99, 0.01], [1.0, 1.0], [1.0, 9.0])
    assert heavy > light


def test_weighted_log_loss_accepts_fractional_targets():
    assert weighted_log_loss([0.5], [0.5], [1.0]) < weighted_log_loss([0.9], [0.5], [1.0])


def test_weighted_log_loss_matches_unweighted_when_uniform():
    probs, labels = [0.7, 0.2, 0.6], [1.0, 0.0, 1.0]
    assert abs(weighted_log_loss(probs, labels) - log_loss(probs, labels)) < 1e-12


def test_weighted_log_loss_zero_total_weight_is_nan():
    assert np.isnan(weighted_log_loss([0.5], [1.0], [0.0]))


def test_auc_rejects_non_binary_labels():
    with pytest.raises(ValueError, match="binary labels"):
        auc([0.1, 0.2, 0.3], [0.0, 0.66, 1.0])


def test_auc_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="length mismatch"):
        auc([0.1, 0.2], [1])


def test_weighted_log_loss_rejects_targets_outside_unit_interval():
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        weighted_log_loss([0.5], [1.5])


def test_point_biserial_perfect_positive():
    assert abs(point_biserial([1.0, 2.0, 3.0, 4.0], [0, 0, 1, 1]) - 0.8944271909999159) < 1e-9


def test_point_biserial_zero_variance_is_nan():
    assert np.isnan(point_biserial([1.0, 1.0, 1.0], [0, 1, 0]))


def test_mismatched_lengths_raise():
    from app.services.stats_math import _validate_xy

    with pytest.raises(ValueError, match="length"):
        _validate_xy(np.zeros((3, 2)), np.zeros(2), None)


def test_non_finite_input_raises():
    from app.services.stats_math import _validate_xy

    with pytest.raises(ValueError, match="finite"):
        _validate_xy(np.array([[np.nan]]), np.array([1.0]), None)


def test_target_outside_unit_interval_raises():
    from app.services.stats_math import _validate_xy

    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        _validate_xy(np.array([[1.0]]), np.array([1.5]), None)


def test_empty_input_raises():
    from app.services.stats_math import _validate_xy

    with pytest.raises(ValueError, match="empty"):
        _validate_xy(np.zeros((0, 2)), np.zeros(0), None)

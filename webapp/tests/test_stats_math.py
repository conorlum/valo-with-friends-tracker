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


from app.services.stats_math import fit_logistic, predict_proba


def test_fit_logistic_recovers_known_coefficient():
    """y is the EXACT logistic mean of 2*x, so an unpenalised fit must
    recover slope 2 and intercept 0."""
    x = np.linspace(-3, 3, 61).reshape(-1, 1)
    y = 1.0 / (1.0 + np.exp(-2.0 * x.ravel()))
    beta = fit_logistic(x, y, l2=0.0)
    assert abs(beta[0]) < 1e-6
    assert abs(beta[1] - 2.0) < 1e-6


def test_fit_logistic_l2_shrinks_coefficient():
    x = np.linspace(-3, 3, 61).reshape(-1, 1)
    y = 1.0 / (1.0 + np.exp(-2.0 * x.ravel()))
    assert fit_logistic(x, y, l2=50.0)[1] < fit_logistic(x, y, l2=0.0)[1]


def test_fit_logistic_intercept_is_not_penalised():
    x = np.zeros((40, 1))
    y = np.full(40, 0.75)
    beta = fit_logistic(x, y, l2=1000.0)
    assert abs(predict_proba(beta, x)[0] - 0.75) < 1e-6


def test_fit_logistic_respects_sample_weights():
    x = np.array([[0.0], [1.0]])
    y = np.array([0.0, 1.0])
    heavy_zero = fit_logistic(x, y, weights=np.array([100.0, 1.0]), l2=1.0)
    heavy_one = fit_logistic(x, y, weights=np.array([1.0, 100.0]), l2=1.0)
    assert predict_proba(heavy_zero, x)[0] < predict_proba(heavy_one, x)[0]


def test_fit_logistic_survives_perfect_separation():
    """Unpenalised MLE has no finite solution here. It must terminate with
    finite coefficients rather than hanging or overflowing."""
    x = np.linspace(-3, 3, 40).reshape(-1, 1)
    y = (x.ravel() > 0).astype(float)
    beta = fit_logistic(x, y, l2=1e-3)
    assert np.all(np.isfinite(beta))
    assert beta[1] > 0


def test_fit_logistic_survives_a_singular_hessian():
    """A duplicated column makes the unpenalised Hessian singular; the fit
    must fall back to a pseudo-inverse instead of raising."""
    base = np.linspace(-2, 2, 50)
    X = np.column_stack([base, base])
    y = (base > 0).astype(float)
    assert np.all(np.isfinite(fit_logistic(X, y, l2=0.0)))


def test_fit_logistic_warns_when_it_does_not_converge(caplog):
    x = np.linspace(-3, 3, 40).reshape(-1, 1)
    y = (x.ravel() > 0).astype(float)
    with caplog.at_level("WARNING"):
        fit_logistic(x, y, l2=0.0, max_iter=2)
    assert any("converge" in record.message for record in caplog.records)


def test_fit_logistic_rejects_bad_input():
    with pytest.raises(ValueError):
        fit_logistic(np.zeros((3, 1)), np.array([0.0, 1.0]))

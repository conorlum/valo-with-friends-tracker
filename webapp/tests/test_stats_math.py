"""Correctness tests for the pure numeric layer. Every case has an
analytically known answer -- no fixtures, no DB, no randomness except
explicitly seeded bootstrap draws."""

import numpy as np
import pytest

from app.services.stats_math import (
    apply_calibration,
    auc,
    calibrate_fractional,
    log_loss,
    platt_calibrate,
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


from app.services.stats_math import (
    apply_calibration,
    back_transform,
    cluster_bootstrap_ci,
    paired_bootstrap_delta,
    platt_calibrate,
    standardize,
    tercile_buckets,
)


def test_standardize_uses_training_statistics_only():
    train = np.array([[0.0], [10.0]])
    train_scaled, apply_scaled, centre, scale = standardize(train, np.array([[20.0]]))
    assert abs(train_scaled.mean()) < 1e-12
    assert centre[0] == 5.0 and scale[0] == 5.0
    assert abs(apply_scaled[0][0] - 3.0) < 1e-9


def test_standardize_handles_constant_column():
    train_scaled, _, _, scale = standardize(np.array([[1.0], [1.0]]), np.array([[1.0], [1.0]]))
    assert np.all(np.isfinite(train_scaled))
    assert scale[0] == 1.0


def test_back_transform_recovers_the_raw_fit():
    """The whole point: a fit on standardized columns, back-transformed,
    must equal a fit on raw columns -- INTERCEPT INCLUDED."""
    rng = np.random.default_rng(0)
    X = rng.normal(loc=50, scale=10, size=(2000, 2))
    y = 1.0 / (1.0 + np.exp(-(0.3 * (X[:, 0] - 50) - 0.2 * (X[:, 1] - 50))))

    raw = fit_logistic(X, y, l2=0.0)
    scaled_X, _, centre, scale = standardize(X, X)
    recovered = back_transform(fit_logistic(scaled_X, y, l2=0.0), centre, scale)

    assert np.allclose(recovered, raw, atol=1e-6)


def test_naive_back_transform_would_be_wrong():
    """Guards against reintroducing the bug: keeping the scaled intercept
    is materially different from the correct value."""
    rng = np.random.default_rng(0)
    X = rng.normal(loc=50, scale=10, size=(500, 1))
    y = (X[:, 0] > 50).astype(float)
    scaled_X, _, centre, scale = standardize(X, X)
    beta = fit_logistic(scaled_X, y, l2=1.0)
    assert abs(back_transform(beta, centre, scale)[0] - beta[0]) > 1.0


def test_platt_calibration_survives_perfect_separation():
    scores = np.linspace(-5, 5, 101)
    labels = (scores > 0).astype(int)
    probs = apply_calibration(platt_calibrate(scores, labels), scores)
    assert np.all(np.isfinite(probs))
    assert probs[0] < 0.5 < probs[-1]
    assert np.all((probs > 0.0) & (probs < 1.0))


def test_tercile_buckets_splits_evenly():
    assert list(tercile_buckets(list(range(9)))) == [0, 0, 0, 1, 1, 1, 2, 2, 2]


def test_tercile_buckets_too_few_values_returns_sentinel():
    assert list(tercile_buckets([1.0, 2.0])) == [-1, -1]


def test_tercile_buckets_collapsed_boundaries_are_unestimable():
    """All-equal values have no meaningful thirds. Returning bucket 0 would
    feed the player's whole history into the BOTTOM win rate."""
    assert list(tercile_buckets([5.0, 5.0, 5.0, 5.0])) == [-1, -1, -1, -1]


def test_tercile_buckets_ties_at_a_boundary_go_down():
    assert list(tercile_buckets([1.0, 1.0, 1.0, 2.0, 3.0, 4.0])) == [0, 0, 0, 1, 2, 2]


def test_cluster_bootstrap_resamples_whole_groups():
    groups = {1: [0.0, 0.0], 2: [1.0, 1.0], 3: [2.0, 2.0]}
    fn = lambda sample: float(np.mean([v for rows in sample for v in rows]))
    lo, hi = cluster_bootstrap_ci(fn, groups, draws=500, seed=7)
    assert 0.0 <= lo <= hi <= 2.0


def test_cluster_bootstrap_is_seed_deterministic():
    groups = {i: [float(i)] for i in range(10)}
    fn = lambda sample: float(np.mean([v for rows in sample for v in rows]))
    assert cluster_bootstrap_ci(fn, groups, draws=200, seed=3) == cluster_bootstrap_ci(
        fn, groups, draws=200, seed=3
    )


def test_paired_bootstrap_delta_is_tight_for_a_constant_offset():
    """B is always A plus 1. The paired interval must be tight around 1.0,
    whereas independently bootstrapping each would be far wider."""
    groups = {i: [float(i)] for i in range(40)}
    fn_a = lambda sample: float(np.mean([v for rows in sample for v in rows]))
    fn_b = lambda sample: float(np.mean([v + 1.0 for rows in sample for v in rows]))
    lo, hi = paired_bootstrap_delta(fn_b, fn_a, groups, draws=400, seed=1)
    assert abs(lo - 1.0) < 1e-9 and abs(hi - 1.0) < 1e-9


def test_fit_logistic_rejects_all_zero_weights():
    with pytest.raises(ValueError, match="total sample weight"):
        fit_logistic(np.zeros((3, 1)), np.zeros(3), weights=np.zeros(3))


def test_bootstraps_on_empty_groups_return_nan():
    fn = lambda sample: 0.0
    assert all(np.isnan(v) for v in cluster_bootstrap_ci(fn, {}, draws=10))
    assert all(np.isnan(v) for v in paired_bootstrap_delta(fn, fn, {}, draws=10))


def test_paired_bootstrap_delta_detects_no_difference():
    groups = {i: [float(i)] for i in range(40)}
    fn = lambda sample: float(np.mean([v for rows in sample for v in rows]))
    lo, hi = paired_bootstrap_delta(fn, fn, groups, draws=200, seed=2)
    assert lo <= 0.0 <= hi



def test_fit_logistic_penalty_defaults_to_uniform():
    """An explicit all-ones mask must be identical to no mask at all --
    otherwise every existing caller silently changes."""
    rng = np.random.default_rng(21)
    X = rng.normal(size=(300, 3))
    y = (X @ np.array([1.0, -0.5, 0.25]) + rng.normal(scale=0.3, size=300) > 0).astype(float)
    assert np.allclose(
        fit_logistic(X, y, l2=2.0), fit_logistic(X, y, l2=2.0, penalty=np.ones(3)), atol=1e-10
    )


def test_a_zero_mask_entry_leaves_that_coefficient_unpenalised():
    """Column 0 exempt must stay large under a penalty that crushes the
    others."""
    rng = np.random.default_rng(22)
    X = rng.normal(size=(800, 3))
    y = (X @ np.array([2.0, 1.0, -1.0]) + rng.normal(scale=0.4, size=800) > 0).astype(float)

    masked = fit_logistic(X, y, l2=500.0, penalty=np.array([0.0, 1.0, 1.0]))
    uniform = fit_logistic(X, y, l2=500.0)

    assert abs(masked[1]) > abs(uniform[1]) * 3
    assert abs(masked[2]) < abs(masked[1])


def test_the_intercept_is_never_penalised_whatever_the_mask():
    rng = np.random.default_rng(23)
    X = rng.normal(size=(400, 2))
    y = np.ones(400)
    y[:40] = 0.0
    beta = fit_logistic(X, y, l2=1e6, penalty=np.ones(2))
    assert beta[0] > 1.0, "intercept was shrunk; the base rate is now biased"


def test_the_mask_delivers_prior_shrinkage_on_the_deployable_graph():
    """The reason this argument exists. Composite damage column plus free
    delta: with d exempt, a large penalty must drive b = prior + delta/d to
    the prior. With d penalised it stalls -- that is the bug."""
    rng = np.random.default_rng(7)
    X = rng.normal(size=(4000, 4))
    damage = rng.normal(size=4000)
    prior = np.full(4, 0.6)
    truth = np.array([1.0, -0.5, 0.8, 0.2])
    eta = 3.0 * damage + X @ (3.0 * truth)
    y = (rng.uniform(size=4000) < 1 / (1 + np.exp(-eta))).astype(float)

    design = np.column_stack([damage + X @ prior, X])
    mask = np.array([0.0, 1.0, 1.0, 1.0, 1.0])
    beta = fit_logistic(design, y, l2=1e7, penalty=mask)
    recovered = prior + beta[2:] / beta[1]
    assert np.allclose(recovered, prior, atol=0.01)

    unmasked = fit_logistic(design, y, l2=1e7)
    stalled = prior + unmasked[2:] / unmasked[1]
    assert not np.allclose(stalled, prior, atol=0.1)


def test_penalty_length_is_validated():
    with pytest.raises(ValueError, match="penalty"):
        fit_logistic(np.zeros((10, 3)), np.zeros(10), penalty=np.ones(2))


# --------------------------------------------------------------------------
# Fractional-target calibration (code review finding 1)
# --------------------------------------------------------------------------

def test_platt_calibrate_binarizes_and_is_therefore_wrong_for_fractional_targets():
    """Documents the defect the fractional path exists to avoid, so nobody
    'simplifies' the two back into one. platt_calibrate thresholds at 0.5,
    which is correct for a genuinely binary yardstick and wrong for T2.

    Uses a sample large enough that Platt's smoothing is negligible -- on two
    rows the smoothing legitimately dominates either path (see the next
    test), which would hide the defect rather than show it."""
    n = 400
    fractional = np.tile([0.2, 0.8], n // 2)
    scores = np.tile([-1.0, 1.0], n // 2)

    binarized = apply_calibration(platt_calibrate(scores, fractional), scores)
    preserved = apply_calibration(calibrate_fractional(scores, fractional), scores)

    # The binarising path pushes toward the extremes it was told to see.
    assert binarized[0] < 0.05 and binarized[1] > 0.95
    # The fractional path recovers the structure that is actually there.
    assert preserved[0] == pytest.approx(0.2, abs=0.02)
    assert preserved[1] == pytest.approx(0.8, abs=0.02)


def test_smoothing_still_shrinks_hard_when_there_is_almost_no_data():
    """Platt smoothing is retained, not bypassed: on a two-row sample the
    effective class mass is ~1, so labels are pulled well toward the middle.
    That is the smoothing working, not the fractional structure being lost."""
    scores = np.array([-1.0, 1.0])
    preserved = apply_calibration(
        calibrate_fractional(scores, np.array([0.2, 0.8])), scores
    )

    assert 0.2 < preserved[0] < 0.5
    assert 0.5 < preserved[1] < 0.8


def test_calibrate_fractional_reduces_exactly_to_platt_on_binary_labels():
    """The generalisation must not change behaviour where Platt was already
    right, or swapping it in would silently move every yardstick number."""
    rng = np.random.default_rng(3)
    scores = rng.normal(size=200)
    labels = (rng.random(200) < 0.4).astype(float)
    weights = rng.uniform(0.5, 1.5, 200)

    assert calibrate_fractional(scores, labels, weights=weights) == pytest.approx(
        platt_calibrate(scores, labels, weights=weights)
    )


def test_calibrate_fractional_beats_binarising_against_fractional_labels():
    """The reason this matters: on realistic fractional targets the binarising
    path is worse by far more than the contrasts this tooling reports."""
    rng = np.random.default_rng(7)
    n = 2000
    y = np.clip(rng.beta(2.2, 2.2, n), 0.0, 1.0)
    scores = (y - 0.5) * 4 + rng.normal(0, 1.2, n)
    train, test = np.arange(n) < n // 2, np.arange(n) >= n // 2

    binarized = apply_calibration(platt_calibrate(scores[train], y[train]), scores[test])
    preserved = apply_calibration(calibrate_fractional(scores[train], y[train]), scores[test])

    assert weighted_log_loss(preserved, y[test]) < weighted_log_loss(binarized, y[test])


def test_calibrate_fractional_keeps_labels_inside_the_open_unit_interval():
    """Smoothing still has to happen -- a label of exactly 0 or 1 fed to IRLS
    with near-zero regularisation drives the fit to a degenerate extreme."""
    scores = np.array([-2.0, 0.0, 2.0])
    beta = calibrate_fractional(scores, np.array([0.0, 0.5, 1.0]))
    probs = apply_calibration(beta, scores)

    assert np.all(probs > 0.0) and np.all(probs < 1.0)

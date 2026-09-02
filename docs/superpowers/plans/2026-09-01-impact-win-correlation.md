# Impact-vs-Winning Evaluation Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build internal tooling that measures how much the custom Impact score relates to winning, fits better `FACTOR_WEIGHTS` against leakage-free forward-looking targets under honest nested cross-validation, and reports the plain descriptive correlation the project was originally asked for.

**Architecture:** A pure numeric layer (`stats_math`) with no domain knowledge; a domain layer (`impact_eval`, `impact_stage0`, `win_probability`) that turns DB rows into one differential observation per round; and a CLI. The cross-validation orchestrator takes **raw observations plus a target-builder callback**, not a prebuilt dataset, because every hyperparameter configuration produces different rows — building the dataset once and selecting on it is the central correctness trap this design exists to avoid.

**Tech Stack:** Python 3, SQLAlchemy 2.0, numpy, pytest. No scipy, no sklearn, no pandas.

**Spec:** `docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md`

## Global Constraints

- **numpy only.** `scipy` is installed locally but absent from `requirements.txt`, which `render.yaml` installs from. Do not add it, and do not import it.
- **No new tables, no Alembic migrations.**
- **No change to `impact.py`'s formula.** The only permitted edit is extracting `build_impact_rows_for_match` so calculation and persistence separate, plus threading a `use_realized_swing` flag through it. `compute_impact_for_match` keeps its exact signature and behaviour.
- **Nothing in this plan may be imported by `app/main.py`, any router, or any template.**
- **Every extraction query excludes surrender placeholder rounds** using `app.services.surrender_rounds.NOT_A_SURRENDER_ROUND`.
- **Fitting uses `ex_ante` components only.** `realized` components appear only in Stage 0 and retrospective contexts, and are always labelled.
- **Nothing may be selected, fitted, or calibrated on data it is later scored against.** Folds and bootstrap resamples are always by match; all rounds of a match live in the same fold. This covers hyperparameters, constrained weights, probability calibration, and the Stage B value model.
- **Every objective is weighted.** Sample weights drive the fit, so they must also drive selection, reporting and bootstrapping — otherwise `gamma` and `match_weight` change the model but not the criterion that judges it.
- **Run everything from `webapp/`** with `.\.venv\Scripts\python.exe`. Tests: `.\.venv\Scripts\python.exe -m pytest tests/<file> -v`.
- **Test style:** plain ORM construction with no DB session, following `tests/test_player_profile_types.py`. **Tasks 4, 5 and 6 require a live Postgres** (`docker compose -p valomaths-private up -d`) and skip cleanly when unreachable; every other task's tests are pure.

## Revision note (third review)

A further review found four issues that would still have made the report
dishonest, all fixed here. **T2 hyperparameters were being selected by each
configuration's own log loss** -- but k/gamma/match_weight change the
definition of y, so that comparison rewards whichever outcome is easiest to
predict and lets different folds pool predictions of different quantities.
Targets are now frozen, alternatives are sensitivity runs judged on fixed
yardsticks, and `_select_config` raises if handed two different target
definitions. **Stage B never entered the common matrix**, so T1/T2/WPA were
still incomparable -- the exact thing the matrix exists to prevent, and the
reason Stage B is in this plan at all; `fitted_WPA` is now a first-class
candidate. **The control ladder used a config chosen across all folds and
printed a bare point estimate** despite the plan telling the reader to judge it
by its interval; it now runs on the frozen target with a paired bootstrap
delta. **AUC was computed by rounding a fractional target at 0.5**, changing
the estimand and discarding the weights; AUC is now confined to the binary
yardsticks, with weighted log loss everywhere else.

Also: `current_impact` reads an exact `impact_diff` column instead of being
rebuilt from rounded components; missing impact rows raise and are counted
rather than becoming zero-impact observations; economy controls are team
averages, not sums; calibration for fitted candidates happens inside each outer
fold; the value model gives training rows inner-OOF leverage; `V(after)`
refuses econ-aware evaluation rather than reusing round N's economy; drop-one
diagnostics report paired weighted-log-loss costs and signed stability;
`fold_candidates` passes each fold's own L2; Stage 0's cohorts and per-player
medians gained intervals and terciles gained an explicit tie policy; and the
scorer-equivalence test checks every persisted field across regulation and
overtime matches.

## Revision note (second review)

Rewritten after a methodology review found the previous version would have produced optimistically biased results presented as held-out. Substantive changes: outer CV now receives raw observations and a target-builder callback, so `k`/`gamma`/`match_weight` are selected inside training folds; constrained weights are fitted per outer fold and scored only on that fold's held-out matches, with a separate all-data fit labelled the deployment proposal; every objective became weighted; the constrained search runs with nuisance controls present, so reported weights come from the same model the control ladder validates; Stage B cross-fits `V(state)` inside each training fold; the standardization back-transform's intercept bug is fixed; the duplicate kill baseline is removed; and Stage 0 gained the analyses the spec required but the plan omitted.

## Task map

| Tasks | Layer |
|---|---|
| 1-3 | `stats_math` — metrics, robust logistic fit, standardization/calibration/bootstraps |
| 4 | Task 0 reconstruction gate |
| 5-6 | Scorer calculation/persistence split, ex-ante swing |
| 7-9 | Observations, folds, collapsed target builders |
| 10-12 | Nested CV orchestration, constrained weights with controls, diagnostics |
| 13 | Stage 0 |
| 14-15 | Yardsticks and matrix, CLI |
| 16-17 | Stage B: cross-fit `V(state)`, WPA target |

**Two rules that the whole design hangs on, stated once here:**

1. **Targets are frozen, never selected.** `k`, `gamma` and `match_weight`
   change the definition of `y`, so comparing configurations by their own
   losses rewards whichever outcome is easiest to predict. `PRIMARY_T1`,
   `PRIMARY_T2` and the WPA target are declared up front; alternatives run as
   sensitivity analyses and are compared *only* on the fixed binary
   yardsticks.
2. **AUC is for yardsticks; weighted log loss is for targets.** T2's target is
   a weighted fraction, and rounding it to manufacture a binary label would
   change the estimand and discard the weights `gamma` exists to set.

---

### Task 1: `stats_math` metrics and input validation

**Files:**
- Create: `webapp/app/services/stats_math.py`
- Test: `webapp/tests/test_stats_math.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sigmoid(eta)`, `auc(scores, labels)`, `log_loss(probs, labels)`, `weighted_log_loss(probs, y, weights=None)`, `point_biserial(values, labels)`, `_average_ranks(values)`, `_validate_xy(X, y, weights)`.

**Why `weighted_log_loss` exists from the start:** it is the single selection and reporting objective for every fit in this project. AUC is reserved for the yardsticks, which have genuinely binary labels; training targets carry fractional `y` and per-row weights, and there is no defensible unweighted reading of those.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_stats_math.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.stats_math'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/stats_math.py
"""Pure numeric helpers for the impact-evaluation tooling.

NEUTRAL LEAF: imports numpy and logging, nothing else from app/. No domain
knowledge, no DB, no ORM -- so impact_eval, impact_stage0 and
win_probability can all import from here without cycle risk.

numpy only, on purpose: scipy is not in requirements.txt (which
render.yaml installs from), and nothing here is heavy enough to justify
adding it to the deploy.

weighted_log_loss is the project's single fitting/selection objective.
AUC is reserved for the yardsticks, whose labels are genuinely binary;
every training target here carries fractional y and per-row weights.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


def sigmoid(eta) -> np.ndarray:
    """Overflow-safe logistic. exp() is only ever applied to non-positive
    values, so a large-magnitude eta saturates instead of raising."""
    eta = np.asarray(eta, dtype=float)
    out = np.empty_like(eta)
    positive = eta >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-eta[positive]))
    exp_eta = np.exp(eta[~positive])
    out[~positive] = exp_eta / (1.0 + exp_eta)
    return out


def _validate_xy(X, y, weights):
    """Shared guard for every fitting entry point. Fails loudly rather than
    letting a shape or NaN problem surface later as a silently wrong
    coefficient."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim != 2:
        raise ValueError(f"X must be 2-D, got shape {X.shape}")
    if X.shape[0] == 0:
        raise ValueError("cannot fit on empty input")
    if X.shape[0] != y.shape[0]:
        raise ValueError(f"X/y length mismatch: {X.shape[0]} vs {y.shape[0]}")
    if not np.isfinite(X).all() or not np.isfinite(y).all():
        raise ValueError("X and y must be finite (no NaN or inf)")
    if y.min() < 0.0 or y.max() > 1.0:
        raise ValueError("y must lie in [0, 1] (binary or fractional)")
    if weights is None:
        w = np.ones(X.shape[0])
    else:
        w = np.asarray(weights, dtype=float)
        if w.shape[0] != X.shape[0]:
            raise ValueError(f"weights length mismatch: {w.shape[0]} vs {X.shape[0]}")
        if not np.isfinite(w).all() or w.min() < 0:
            raise ValueError("weights must be finite and non-negative")
        if w.sum() == 0:
            raise ValueError("total sample weight is zero; nothing to fit")
    return X, y, w


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks with ties averaged, which a tie-correct Mann-Whitney
    AUC needs."""
    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    ranks = np.empty(len(values), dtype=float)
    positions = np.arange(1, len(values) + 1, dtype=float)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        ranks[order[i : j + 1]] = positions[i : j + 1].mean()
        i = j + 1
    return ranks


def auc(scores, labels) -> float:
    """Rank-based (Mann-Whitney) AUC. NaN when only one class is present:
    a slice with no losses has no discrimination to measure, and a silent
    0.5 would hide that."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels)
    if scores.shape[0] != labels.shape[0]:
        raise ValueError(f"scores/labels length mismatch: {scores.shape[0]} vs {labels.shape[0]}")
    if not np.isfinite(scores).all():
        raise ValueError("scores must be finite")
    # Binary labels only. Casting an arbitrary float to int would silently
    # turn a fractional target into a made-up class -- the exact mistake this
    # project removed from oof_metrics.
    unique = set(np.unique(labels).tolist())
    if not unique <= {0, 1, 0.0, 1.0}:
        raise ValueError(f"auc needs binary labels, got values {sorted(unique)[:5]}")
    labels = labels.astype(int)
    n_pos = int((labels == 1).sum())
    n_neg = int((labels == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = _average_ranks(scores)
    return float((ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def weighted_log_loss(probs, y, weights=None, eps: float = 1e-12) -> float:
    """Cross-entropy against a possibly-fractional target, weighted per row.
    NaN when total weight is zero rather than dividing by it."""
    p = np.clip(np.asarray(probs, dtype=float), eps, 1 - eps)
    y = np.asarray(y, dtype=float)
    w = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=float)
    if not (p.shape[0] == y.shape[0] == w.shape[0]):
        raise ValueError(f"length mismatch: probs {p.shape[0]}, y {y.shape[0]}, w {w.shape[0]}")
    if not (np.isfinite(y).all() and np.isfinite(w).all()):
        raise ValueError("y and weights must be finite")
    if y.size and (y.min() < 0.0 or y.max() > 1.0):
        raise ValueError("y must lie in [0, 1]")
    total = float(w.sum())
    if total == 0:
        return float("nan")
    per_row = y * np.log(p) + (1 - y) * np.log(1 - p)
    return float(-np.sum(w * per_row) / total)


def log_loss(probs, labels, eps: float = 1e-12) -> float:
    return weighted_log_loss(probs, labels, None, eps)


def point_biserial(values, labels) -> float:
    """Pearson correlation between a continuous value and a 0/1 label.
    NaN when either side has zero variance."""
    v = np.asarray(values, dtype=float)
    l = np.asarray(labels, dtype=float)
    if v.std() == 0 or l.std() == 0:
        return float("nan")
    return float(np.corrcoef(v, l)[0, 1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add stats_math metrics with weighted log loss and input validation"
```

---

### Task 2: `stats_math` robust weighted logistic fit

**Files:**
- Modify: `webapp/app/services/stats_math.py`
- Test: `webapp/tests/test_stats_math.py`

**Interfaces:**
- Consumes: `sigmoid`, `_validate_xy` (Task 1).
- Produces: `fit_logistic(X, y, weights=None, l2=1.0, max_iter=100, tol=1e-9) -> np.ndarray` where `beta[0]` is the intercept and `beta[1:]` are coefficients in `X` column order; `predict_proba(beta, X) -> np.ndarray`.

**Robustness requirements, each with its own test:** perfect separation must not hang or overflow, a singular Hessian must fall back to a pseudo-inverse rather than raising (the impact components are collinear by construction — `impact.py:496-502`), and non-convergence must log a warning rather than silently returning a half-converged fit.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_stats_math.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -k logistic -v`
Expected: FAIL with `ImportError: cannot import name 'fit_logistic'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/stats_math.py

def predict_proba(beta: np.ndarray, X) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    return sigmoid(beta[0] + X @ beta[1:])


def fit_logistic(X, y, weights=None, l2: float = 1.0, max_iter: int = 100, tol: float = 1e-9) -> np.ndarray:
    """Weighted IRLS logistic regression with a ridge penalty.

    `y` may be fractional in [0, 1] (quasi-binomial) -- a forward window of
    "weighted fraction of later rounds won" is not a 0/1 label.

    The intercept is never penalised: shrinking it would bias the base
    rate, which is not what L2 is for.

    Degenerate cases are handled rather than propagated: a singular Hessian
    falls back to a pseudo-inverse (collinear components are expected, see
    impact.py:496-502), and failure to converge logs a warning instead of
    silently returning a half-fitted model.

    Returns beta with beta[0] = intercept, beta[1:] = coefficients in the
    column order of X.
    """
    X, y, w = _validate_xy(X, y, weights)
    n, p = X.shape

    design = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(p + 1)
    penalty = np.eye(p + 1) * l2
    penalty[0, 0] = 0.0

    for _ in range(max_iter):
        eta = design @ beta
        mu = sigmoid(eta)
        variance = np.clip(mu * (1.0 - mu), 1e-9, None)
        s = variance * w
        z = eta + (y - mu) / variance
        weighted = design.T * s
        hessian = weighted @ design + penalty
        gradient = weighted @ z
        try:
            new_beta = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            new_beta = np.linalg.pinv(hessian) @ gradient
        if not np.all(np.isfinite(new_beta)):
            logger.warning("fit_logistic: non-finite step, returning last finite estimate")
            return beta
        if np.max(np.abs(new_beta - beta)) < tol:
            return new_beta
        beta = new_beta

    logger.warning("fit_logistic: did not converge in %d iterations", max_iter)
    return beta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: PASS, 24 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add robust weighted IRLS logistic fit to stats_math"
```

---

### Task 3: `stats_math` standardization, calibration, terciles, bootstraps

**Files:**
- Modify: `webapp/app/services/stats_math.py`
- Test: `webapp/tests/test_stats_math.py`

**Interfaces:**
- Consumes: `fit_logistic`, `predict_proba` (Task 2).
- Produces: `standardize(X_train, X_apply) -> (train_scaled, apply_scaled, centre, scale)`; `back_transform(beta, centre, scale) -> np.ndarray`; `platt_calibrate(scores, labels, weights=None) -> np.ndarray`; `apply_calibration(beta, scores) -> np.ndarray`; `tercile_buckets(values) -> np.ndarray`; `cluster_bootstrap_ci(fn, groups, draws=1000, seed=0, alpha=0.05)`; `paired_bootstrap_delta(fn_a, fn_b, groups, draws=1000, seed=0, alpha=0.05)`.

**Two bugs this task exists to prevent:**

1. **The intercept back-transform.** Standardizing subtracts a centre, so `raw_intercept = scaled_intercept - sum(raw_slope * centre)`. Carrying the scaled intercept through unchanged is wrong — on a worked example it yields **−0.06 where the true value is −5.0**. `standardize` therefore returns `centre`, and `back_transform` is the only sanctioned way to undo it.
2. **Unregularised Platt calibration on separable scores has no finite MLE.** `platt_calibrate` applies Platt's own target smoothing (`t+ = (N+ + 1)/(N+ + 2)`, `t- = 1/(N- + 2)`) plus a small ridge.

**Why paired bootstrap:** every headline quantity is a *difference* (fitted Impact minus kill differential; ladder step 3 → 4). Subtracting two independently bootstrapped point estimates gives no interval for the difference. Both candidates must be evaluated on the *same* resampled matches with the delta taken per draw.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_stats_math.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -k "standardize or back_transform or platt or tercile or bootstrap" -v`
Expected: FAIL with `ImportError: cannot import name 'standardize'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/stats_math.py

def standardize(X_train, X_apply):
    """Centre and scale by TRAINING statistics. Returns centre and scale
    too -- back_transform needs BOTH, and an earlier version of this
    project shipped a back-transform that dropped the centre and produced
    an intercept off by ~5 logits.

    A constant column gets scale 1.0 rather than 0, so it contributes
    nothing instead of producing NaN.
    """
    X_train = np.asarray(X_train, dtype=float)
    X_apply = np.asarray(X_apply, dtype=float)
    centre = X_train.mean(axis=0)
    scale = X_train.std(axis=0)
    scale = np.where(scale == 0, 1.0, scale)
    return (X_train - centre) / scale, (X_apply - centre) / scale, centre, scale


def back_transform(beta: np.ndarray, centre: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """Convert coefficients fitted on standardized columns back to raw
    units.

        eta = b0 + sum b_j (x_j - c_j)/s_j
            = (b0 - sum (b_j/s_j) c_j) + sum (b_j/s_j) x_j

    so the intercept MUST absorb the centring term.
    """
    slope = beta[1:] / scale
    intercept = beta[0] - float(np.sum(slope * centre))
    return np.concatenate([[intercept], slope])


def platt_calibrate(scores, labels, weights=None) -> np.ndarray:
    """Fit a 1-D logistic mapping raw scores -> probabilities.

    Uses Platt's target smoothing plus a small ridge: with perfectly
    separable scores the unregularised MLE diverges, and separable slices
    are common here (a candidate that happens to order a small fold
    perfectly).

    Callers MUST fit this on training-fold data only. AUC is rank-based and
    needs no calibration; log loss does, and calibrating on the rows being
    scored would leak.
    """
    scores = np.asarray(scores, dtype=float).reshape(-1, 1)
    labels = np.asarray(labels, dtype=float)
    # Class totals are WEIGHTED when weights are supplied: Platt's smoothing
    # is a function of how much evidence each class carries, and unweighted
    # counts would smooth a heavily-weighted class as if it were sparse.
    w = np.ones(len(labels)) if weights is None else np.asarray(weights, dtype=float)
    n_pos = float(w[labels >= 0.5].sum())
    n_neg = float(w[labels < 0.5].sum())
    high = (n_pos + 1.0) / (n_pos + 2.0)
    low = 1.0 / (n_neg + 2.0)
    smoothed = np.where(labels >= 0.5, high, low)
    return fit_logistic(scores, smoothed, weights=weights, l2=1e-6)


def apply_calibration(beta: np.ndarray, scores) -> np.ndarray:
    return predict_proba(beta, np.asarray(scores, dtype=float).reshape(-1, 1))


def tercile_buckets(values) -> np.ndarray:
    """0 = bottom third, 1 = middle, 2 = top. All -1 when there are fewer
    than 3 values, so callers filter rather than crash.

    TIE POLICY, explicit because it changes what a lift means: boundaries
    are strict `>`, so a value exactly on a quantile falls into the LOWER
    bucket. When the two boundaries COLLAPSE (a player whose Impact barely
    varies), there is no meaningful top or bottom third at all, so every row
    returns -1 -- unestimable -- rather than piling the player's whole history
    into the bottom bucket and dragging the pooled lift down.
    """
    v = np.asarray(values, dtype=float)
    if len(v) < 3:
        return np.full(len(v), -1, dtype=int)
    lower, upper = np.quantile(v, [1 / 3, 2 / 3])
    if lower == upper:
        # Boundaries collapsed: there is no meaningful top or bottom third.
        # Assigning everything to bucket 0 would silently feed a player's
        # whole history into the BOTTOM win rate and bias the lift downward.
        return np.full(len(v), -1, dtype=int)
    out = np.zeros(len(v), dtype=int)
    out[v > lower] = 1
    out[v > upper] = 2
    return out


def _resample(groups: dict, rng) -> list:
    keys = list(groups.keys())
    picked = rng.integers(0, len(keys), size=len(keys))
    return [groups[keys[int(i)]] for i in picked]


def cluster_bootstrap_ci(fn, groups: dict, draws: int = 1000, seed: int = 0, alpha: float = 0.05):
    """Percentile CI from resampling WHOLE GROUPS with replacement.

    `groups` maps a cluster key (always a match_id here) to that cluster's
    rows; `fn` receives a list of row-lists. Resampling rows independently
    would treat one match's ~21 rounds as independent evidence and
    understate every interval, so it is not offered.
    """
    if not groups:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(draws):
        value = fn(_resample(groups, rng))
        if value is not None and np.isfinite(value):
            stats.append(value)
    if not stats:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (float(lo), float(hi))


def paired_bootstrap_delta(fn_a, fn_b, groups: dict, draws: int = 1000, seed: int = 0, alpha: float = 0.05):
    """CI for (fn_a - fn_b), both evaluated on the SAME resample each draw.

    Every headline comparison here is a difference -- fitted Impact vs kill
    differential, ladder step 3 vs 4. Subtracting two independently
    bootstrapped point estimates gives no interval for the difference.
    """
    if not groups:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(draws):
        sample = _resample(groups, rng)
        a, b = fn_a(sample), fn_b(sample)
        if a is not None and b is not None and np.isfinite(a) and np.isfinite(b):
            deltas.append(a - b)
    if not deltas:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(deltas, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (float(lo), float(hi))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: PASS, 35 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add standardization with correct back-transform, smoothed Platt calibration and paired bootstrap"
```

---

### Task 4: Task 0 gate — the reconstruction identity

**Files:**
- Create: `webapp/tests/test_impact_reconstruction.py`

**Interfaces:**
- Consumes: `app.models.ImpactScore`, `app.scoring.impact.FACTOR_WEIGHTS`.
- Produces: nothing importable. This is a gate: **if it fails, stop and re-plan.**

**Why it validates every row, not a sample:** Stage A's entire approach assumes `impact` is a linear function of the four stored columns. A 5,000-row `LIMIT` with no `ORDER BY` returns whatever Postgres finds first — likely all from the same few early matches — and would miss a formula change that only affects, say, overtime rounds. A SQL aggregate over all 241,570 rows costs one query.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_impact_reconstruction.py
"""TASK 0 GATE (see the spec's 'The tuning surface already exists').

Stage A fits FACTOR_WEIGHTS by regressing on four stored columns. That is
only valid if `impact` really is the linear combination of them that
impact.py's arithmetic implies. This asserts the identity over EVERY row,
via a SQL aggregate rather than a sample.

Skips when no database is reachable -- it is a data gate, not a unit test.
Start Postgres with: docker compose -p valomaths-private up -d
"""

import pytest
from sqlalchemy import text

from app.scoring.impact import FACTOR_WEIGHTS

# impact.py round()s kill_impact, death_impact and each component
# independently, so exact equality is not expected.
TOLERANCE = 2


def _session():
    try:
        from app.db import SessionLocal

        return SessionLocal()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database available: {exc}")


def test_impact_reconstructs_from_stored_components():
    db = _session()
    try:
        total = sum(FACTOR_WEIGHTS.values())
        row = db.execute(
            text(
                """
                select count(*) as rows,
                       max(abs(err)) as max_err,
                       sum(case when abs(err) > :tol then 1 else 0 end) as breaches
                from (
                  select impact - (
                      damage
                      + (:we * econ_impact + :wt * time_impact + :ws * swing_impact) / :total
                  ) as err
                  from impact_scores
                ) t
                """
            ),
            {
                "we": FACTOR_WEIGHTS["econ"],
                "wt": FACTOR_WEIGHTS["time"],
                "ws": FACTOR_WEIGHTS["swing"],
                "total": total,
                "tol": TOLERANCE,
            },
        ).one()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"impact_scores unreadable: {exc}")
    finally:
        db.close()

    if row.rows == 0:
        pytest.skip("impact_scores is empty")

    assert row.breaches == 0, (
        f"{row.breaches} of {row.rows} rows break the linear identity Stage A "
        f"depends on (max error {row.max_err}). Do NOT proceed to fitting and "
        f"do NOT widen TOLERANCE -- re-read impact.py's kill_impact/"
        f"death_impact combination step instead."
    )
```

- [ ] **Step 2: Run the gate**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_reconstruction.py -v`
Expected: PASS (or SKIP if Postgres is down)

If it FAILS: stop. Report `max_err` and `breaches`, and re-plan Stage A. Do not widen the tolerance to make it pass.

- [ ] **Step 3: Commit**

```bash
git add webapp/tests/test_impact_reconstruction.py
git commit -m "Add Task 0 gate asserting impact reconstructs from stored components"
```

---

### Task 5: Split calculation from persistence in the scorer

**Files:**
- Modify: `webapp/app/scoring/impact.py:371` (function head) and `:622-657` (persistence block)
- Test: `webapp/tests/test_impact_exante_swing.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `CalculatedImpact` dataclass; `build_impact_rows_for_match(db, match_id, use_realized_swing=True) -> list[CalculatedImpact]`. `compute_impact_for_match(db, match_id) -> None` keeps its exact signature.

**Why this is a blocker fix, not a refactor for taste:** `compute_impact_for_match` queries `ImpactScore` (`impact.py:624`), `db.add`s (`:630`), mutates every column, and calls `db.commit()` unconditionally (`:657`). Adding an ex-ante flag to it without this split would **overwrite the stored scores** the first time the evaluator ran.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_impact_exante_swing.py
"""The evaluator must compute impact components WITHOUT writing them.

This is the regression test for a data-corruption bug: an earlier design
added a use_realized_swing flag directly to compute_impact_for_match,
which commits unconditionally, so an ex-ante evaluation run would have
overwritten every stored score.

Requires a live database; skips cleanly without one.
"""

import pytest

from app.models import ImpactScore, Round
from app.scoring.impact import build_impact_rows_for_match

# EVERY persisted field, not a subset: the spec asks for field-by-field
# equality, and a drift in e.g. clutch_kill or trade_detail would otherwise
# pass unnoticed while silently changing what the tooling reads.
PERSISTED_FIELDS = (
    "kill_impact", "death_impact", "impact", "damage", "econ_impact",
    "time_impact", "swing_impact", "econ_kill", "econ_death", "clutch_kill",
    "clutch_death", "post_plant_kill", "post_plant_death", "traded_teammate",
    "traded_by_teammate", "trade_detail",
)


class _SpyDB:
    """Wraps a real session and records any write attempt."""

    def __init__(self, inner):
        self.inner = inner
        self.added = []
        self.commits = 0

    def query(self, *a, **kw):
        return self.inner.query(*a, **kw)

    def add(self, obj):
        self.added.append(obj)
        return self.inner.add(obj)

    def commit(self):
        self.commits += 1
        return self.inner.commit()


@pytest.fixture
def db_session():
    try:
        from app.db import SessionLocal

        db = SessionLocal()
        db.query(ImpactScore.round_id).limit(1).scalar()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database available: {exc}")
    yield db
    db.close()


def _representative_match_ids(db, per_kind: int = 3) -> list[int]:
    """A few regulation matches AND a few overtime ones. Overtime exercises
    the round>24 branches of the swing factor and the side rule, which a
    single arbitrary match would never touch."""
    from sqlalchemy import func

    rows = (
        db.query(Round.match_id, func.max(Round.round_number).label("last"))
        .group_by(Round.match_id)
        .all()
    )
    regulation = [m for m, last in rows if last <= 24][:per_kind]
    overtime = [m for m, last in rows if last > 24][:per_kind]
    if not regulation and not overtime:
        pytest.skip("no matches in the database")
    return regulation + overtime


@pytest.fixture
def db_and_match(db_session):
    ids = _representative_match_ids(db_session, per_kind=1)
    return db_session, ids[0]


def test_builder_writes_nothing(db_and_match):
    db, match_id = db_and_match
    spy = _SpyDB(db)
    rows = build_impact_rows_for_match(spy, match_id)
    assert rows, "expected calculated rows"
    assert spy.added == [], "builder must not add ORM objects"
    assert spy.commits == 0, "builder must not commit"


def test_builder_matches_stored_values(db_session):
    """Field-by-field over several regulation AND overtime matches."""
    checked = 0
    for match_id in _representative_match_ids(db_session):
        rows = build_impact_rows_for_match(db_session, match_id, use_realized_swing=True)
        stored = {
            (s.round_id, s.match_player_id): s
            for s in db_session.query(ImpactScore)
            .join(ImpactScore.round)
            .filter_by(match_id=match_id)
            .all()
        }
        if not stored:
            continue
        for row in rows:
            existing = stored[(row.round_id, row.match_player_id)]
            for field in PERSISTED_FIELDS:
                assert getattr(row, field) == getattr(existing, field), (
                    f"{field} drifted for match {match_id} "
                    f"round {row.round_id}/{row.match_player_id}"
                )
            checked += 1
    assert checked, "no stored scores found to compare against"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_exante_swing.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_impact_rows_for_match'`

- [ ] **Step 3: Write the implementation**

Add near the top of `app/scoring/impact.py`:

```python
from dataclasses import dataclass


@dataclass
class CalculatedImpact:
    """One (round, match_player)'s computed impact, with NO ORM identity and
    no session attachment. build_impact_rows_for_match returns these;
    compute_impact_for_match is the only thing that turns them into rows."""

    round_id: int
    match_player_id: int
    kill_impact: int
    death_impact: int
    impact: int
    damage: int
    econ_impact: int
    time_impact: int
    swing_impact: int
    econ_kill: int
    econ_death: int
    clutch_kill: int
    clutch_death: int
    post_plant_kill: int
    post_plant_death: int
    traded_teammate: int
    traded_by_teammate: int
    trade_detail: dict | None
```

Rename `compute_impact_for_match` to `build_impact_rows_for_match`, change its signature to `(db: Session, match_id: int, use_realized_swing: bool = True) -> list[CalculatedImpact]`, and make three edits inside it:

1. Initialise an accumulator immediately before the aggregate loop at `impact.py:546`:

```python
    calculated: list[CalculatedImpact] = []
```

2. Replace the entire persistence block (originally `impact.py:622-655`, from `impact_score = (db.query(ImpactScore)...` through the `trade_detail` assignment) with:

```python
            calculated.append(
                CalculatedImpact(
                    round_id=round_row.id,
                    match_player_id=match_player_id,
                    kill_impact=kill_impact,
                    death_impact=death_impact,
                    impact=impact,
                    damage=damages,
                    econ_impact=round(kill_order_bonus_x_econ_sum - death_order_bonus_x_econ_sum),
                    time_impact=round(kill_order_bonus_x_time_sum - death_order_bonus_x_time_sum),
                    swing_impact=round(kill_order_bonus_x_swing_sum - death_order_bonus_x_swing_sum),
                    econ_kill=round(econ_mismatch_kill_sum),
                    econ_death=round(econ_mismatch_death_sum),
                    clutch_kill=round(clutch_kill_sum),
                    clutch_death=round(clutch_death_sum),
                    post_plant_kill=round(post_plant_kill_sum),
                    post_plant_death=round(post_plant_death_sum),
                    traded_teammate=trade_kill_counts[round_number].get(match_player_id, 0),
                    traded_by_teammate=trade_death_counts[round_number].get(match_player_id, 0),
                    trade_detail=(
                        {"t": traded_teammate_targets, "s": traded_by_teammate_sources}
                        if (traded_teammate_targets or traded_by_teammate_sources)
                        else None
                    ),
                )
            )
```

3. Replace the trailing `db.commit()` (originally `impact.py:657`) with:

```python
    return calculated
```

Then add the wrapper at the end of the module:

```python
_PERSISTED_FIELDS = (
    "kill_impact", "death_impact", "impact", "damage", "econ_impact",
    "time_impact", "swing_impact", "econ_kill", "econ_death", "clutch_kill",
    "clutch_death", "post_plant_kill", "post_plant_death", "traded_teammate",
    "traded_by_teammate", "trade_detail",
)


def compute_impact_for_match(db: Session, match_id: int) -> None:
    """Unchanged public behaviour: compute and persist. The calculation now
    lives in build_impact_rows_for_match so the evaluation tooling can call
    it read-only -- see
    docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md."""
    for calculated in build_impact_rows_for_match(db, match_id, use_realized_swing=True):
        impact_score = (
            db.query(ImpactScore)
            .filter_by(round_id=calculated.round_id, match_player_id=calculated.match_player_id)
            .one_or_none()
        )
        if impact_score is None:
            impact_score = ImpactScore(
                round_id=calculated.round_id, match_player_id=calculated.match_player_id
            )
            db.add(impact_score)
        for field in _PERSISTED_FIELDS:
            setattr(impact_score, field, getattr(calculated, field))
    db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_exante_swing.py tests/test_recompute_impact_script.py -v`
Expected: PASS. `test_recompute_impact_script.py` is the existing regression guard on the persistence path and must stay green.

- [ ] **Step 5: Verify the full suite is unaffected**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/scoring/impact.py webapp/tests/test_impact_exante_swing.py
git commit -m "Split impact calculation from persistence

build_impact_rows_for_match returns CalculatedImpact rows and writes
nothing; compute_impact_for_match keeps its signature and persists them.
This is what lets the evaluation tooling compute ex-ante components
without overwriting stored scores."
```

---

### Task 6: Ex-ante swing variant

**Files:**
- Modify: `webapp/app/scoring/impact.py:473-476`
- Test: `webapp/tests/test_impact_exante_swing.py`

**Interfaces:**
- Consumes: `build_impact_rows_for_match` (Task 5).
- Produces: an honoured `use_realized_swing=False` that suppresses `_realized_econ_swing_factor` entirely.

**Why:** `_realized_econ_swing_factor` (`impact.py:309`) reads `round_player_stats.get(round_number + 1)`. Its output flows through `_combine_swing_factors` into `kill_order_bonus_x_swing` (`:502`) and the stored `swing_impact` (`:639`). Predicting round N+1 from that column leaks. `_econ_swing_risk_factor` projects only from current-round credits and is clean.

**Test design note:** asserting "ex-ante and realized differ on some arbitrary match" is data-dependent and flaky — a match where the two factors never disagree would fail the test for no real reason. Instead, monkeypatch the realized helper to raise. The ex-ante path passes only if that function is genuinely never called, which is the actual property being claimed.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_exante_swing.py
import app.scoring.impact as impact_module


def test_wrapper_still_persists_and_commits(db_and_match, monkeypatch):
    """The spec requires compute_impact_for_match's behaviour be unchanged.
    The builder test proves the CALCULATION matches; this proves the WRAPPER
    still writes -- otherwise the split could silently turn the scorer into a
    no-op and every ingest would stop scoring."""
    from app.scoring.impact import compute_impact_for_match

    db, match_id = db_and_match
    spy = _SpyDB(db)
    before = db.query(ImpactScore).join(ImpactScore.round).filter_by(match_id=match_id).count()
    compute_impact_for_match(spy, match_id)
    assert spy.commits >= 1, "wrapper must commit"
    after = db.query(ImpactScore).join(ImpactScore.round).filter_by(match_id=match_id).count()
    assert after == before, "re-scoring an existing match must update, not duplicate"


def test_ex_ante_never_calls_the_realized_factor(db_and_match, monkeypatch):
    """The direct proof: if the ex-ante path touched round N+1 data, this
    would raise. Deterministic, unlike comparing outputs on a match that
    may happen to have no disagreement."""
    db, match_id = db_and_match

    def _forbidden(*args, **kwargs):
        raise AssertionError("ex-ante path must not read round N+1 data")

    monkeypatch.setattr(impact_module, "_realized_econ_swing_factor", _forbidden)
    rows = build_impact_rows_for_match(db, match_id, use_realized_swing=False)
    assert rows, "expected calculated rows"


def test_realized_path_does_call_the_realized_factor(db_and_match, monkeypatch):
    """Confirms the monkeypatch above actually has teeth -- otherwise the
    ex-ante test would pass even if the flag were ignored."""
    db, match_id = db_and_match
    calls = []
    original = impact_module._realized_econ_swing_factor

    def _counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(impact_module, "_realized_econ_swing_factor", _counting)
    build_impact_rows_for_match(db, match_id, use_realized_swing=True)
    assert calls, "realized path must consult the realized factor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_exante_swing.py -k realized -v`
Expected: FAIL on `test_ex_ante_never_calls_the_realized_factor` with `AssertionError: ex-ante path must not read round N+1 data` — the flag is accepted but ignored.

- [ ] **Step 3: Write minimal implementation**

Replace `impact.py:473-476` with:

```python
        # Ex-ante mode drops the realized term entirely. _realized_econ_swing_factor
        # reads round N+1's loadouts, so any forward-looking model trained on a
        # swing_impact that includes it is leaking. See the spec's LEAKAGE section.
        if use_realized_swing:
            team1_realized_swing = _realized_econ_swing_factor(
                round_player_stats, match_players, round_number, Team.TEAM_1
            )
            team2_realized_swing = _realized_econ_swing_factor(
                round_player_stats, match_players, round_number, Team.TEAM_2
            )
            team1_combined_swing = _combine_swing_factors(team1_swing, team1_realized_swing)
            team2_combined_swing = _combine_swing_factors(team2_swing, team2_realized_swing)
        else:
            team1_combined_swing = team1_swing
            team2_combined_swing = team2_swing
```

**Note for the implementer:** the monkeypatch targets the module attribute, so the call site must remain a plain module-level lookup (`_realized_econ_swing_factor(...)`). Do not bind it to a local alias earlier in the function.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_exante_swing.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/impact.py webapp/tests/test_impact_exante_swing.py
git commit -m "Add ex-ante swing variant that reads no next-round data"
```

---

### Task 7: Per-round differential observations

**Files:**
- Create: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `build_impact_rows_for_match` (Tasks 5-6), `NOT_A_SURRENDER_ROUND`, `attacking_team_for_round`, `FULL_BUY_THRESHOLD`.
- Produces: `RoundObservation` dataclass; `build_observations_for_match(match, calculated_rows) -> list[RoundObservation]`; `SURRENDER_SUFFIX`.

**One row per round, not two.** A round's two (round, team) rows have perfectly complementary outcomes; counting both would double every apparent sample size. Features are team-A-minus-team-B differentials and labels are "did team A ...".

**Only ONE kill baseline.** An earlier draft carried `kills`, `deaths` and `kill_diff` as separate baseline features. They are not independent: `kills_and_deaths·[1,−1]` is algebraically identical to `kills − deaths`, and measured on this DB **`deaths_A == kills_B` in 23,933 of 24,157 rounds (99.1%)**, so `deaths ≈ −kills` in the differential representation and all three collapse to one column. The single baseline is `kill_diff = kills_A − kills_B`.

**Side is a constant in the first half.** `attacking_team_for_round` returns `TEAM_1` for every round ≤ 12, so `attacking_is_team_a` is always `True` there. That is why the first-half yardstick is never split by side — the other subset is empty.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_impact_eval.py
"""Observation extraction: one differential row per round, team A minus
team B. Fixtures are plain ORM construction with no session, following
tests/test_player_profile_types.py."""

import numpy as np
import pytest

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.scoring.impact import CalculatedImpact
from app.services.impact_eval import RoundObservation, build_observations_for_match


def _match_with_two_rounds():
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED, map_name="Bind",
        team1_rounds_won=13, team2_rounds_won=7,
    )
    a = MatchPlayer(id=1, match_id=1, player_id=10, agent="Jett", team=Team.TEAM_1)
    b = MatchPlayer(id=2, match_id=1, player_id=20, agent="Sova", team=Team.TEAM_2)
    a.player = Player(id=10, display_name="A#1")
    b.player = Player(id=20, display_name="B#2")
    match.match_players = [a, b]

    r1 = Round(id=101, match_id=1, round_number=1, outcome="Team A Wins")
    r1.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=2, deaths=0, assists=0, loadout=800),
        RoundPlayerStat(match_player_id=2, kills=0, deaths=2, assists=0, loadout=800),
    ]
    r2 = Round(id=102, match_id=1, round_number=2, outcome="Team B Wins")
    r2.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=0, deaths=1, assists=0, loadout=4500),
        RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000),
    ]
    match.rounds = [r1, r2]
    return match


def _calculated():
    return [
        CalculatedImpact(101, 1, 300, 0, 300, 100, 60, 30, 20, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(101, 2, 0, 250, -250, 10, -40, -20, -10, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(102, 1, 0, 200, -200, 20, -30, -10, -5, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(102, 2, 280, 0, 280, 90, 50, 25, 15, 0, 0, 0, 0, 0, 0, 0, 0, None),
    ]


def test_one_observation_per_round():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert [o.round_number for o in obs] == [1, 2]


def test_features_are_team_a_minus_team_b():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].damage == 100 - 10
    assert obs[0].econ_impact == 60 - (-40)
    assert obs[0].swing_impact == 20 - (-10)


def test_kill_diff_is_team_kill_differential():
    """kills_A - kills_B, not kills-minus-deaths: deaths_A == kills_B in
    99.1% of real rounds, so the latter is the same column doubled."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].kill_diff == 2 - 0
    assert obs[1].kill_diff == 0 - 1


def test_score_differential_excludes_the_current_round():
    """Round 1's control must be 0-0, not 1-0: the round's own result is a
    separate control and must never leak into pre-round score."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].score_diff_before == 0
    assert obs[1].score_diff_before == 1


def test_economy_controls_are_start_of_round():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].loadout_diff == 800 - 800
    assert obs[1].loadout_diff == 4500 - 2000
    assert obs[1].full_buy_count_diff == 1 - 0


def test_round_and_match_outcomes():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].round_won_by_team_a is True
    assert obs[1].round_won_by_team_a is False
    assert all(o.match_won_by_team_a is True for o in obs)


def test_first_half_is_always_attack_first_for_team_a():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert all(o.attacking_is_team_a for o in obs)


def test_last_round_is_terminal():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[-1].is_terminal
    assert not obs[0].is_terminal


def test_surrender_rounds_are_dropped():
    match = _match_with_two_rounds()
    match.rounds[1].outcome = "Team A Surrendered Win"
    obs = build_observations_for_match(match, _calculated())
    assert [o.round_number for o in obs] == [1]


def test_impact_diff_is_the_exact_stored_differential():
    """Not reconstructed from components: round 1 is 300 - (-250) = 550."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].impact_diff == 300 - (-250)
    assert obs[1].impact_diff == -200 - 280


def test_economy_control_is_a_team_average_not_a_sum():
    """A sum would encode how many player-stat rows a round happens to have."""
    match = _match_with_two_rounds()
    # Drop one of team B's stat rows from round 2; the average must not move.
    match.rounds[1].player_stats = [
        s for s in match.rounds[1].player_stats if s.match_player_id == 1
    ] + [RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000)]
    obs = build_observations_for_match(match, _calculated())
    assert obs[1].loadout_diff == 4500 - 2000


def test_missing_impact_rows_raise_rather_than_becoming_zero():
    from app.services.impact_eval import MissingImpactRows

    with pytest.raises(MissingImpactRows, match="round 2"):
        build_observations_for_match(
            _match_with_two_rounds(), [r for r in _calculated() if r.round_id == 101]
        )


def test_tied_match_has_no_match_label():
    match = _match_with_two_rounds()
    match.team1_rounds_won = match.team2_rounds_won = 12
    obs = build_observations_for_match(match, _calculated())
    assert all(o.match_won_by_team_a is None for o in obs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.impact_eval'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/impact_eval.py
"""Turns match data into ONE differential observation per round, then fits
and scores candidate Impact weightings against forward-looking targets
under nested cross-validation.

Internal tooling only -- nothing here is imported by app/main.py, any
router, or any template. See
docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md.

Observation unit is one row per round with team-A-minus-team-B features.
The two (round, team) rows of a round have perfectly complementary
outcomes, so treating them as two observations would double every
apparent sample size.
"""

from dataclasses import dataclass

import numpy as np

from app.models.match import Team
from app.scoring.impact import FULL_BUY_THRESHOLD
from app.services.map_side_stats import attacking_team_for_round

SURRENDER_SUFFIX = "Surrendered Win"


class MissingImpactRows(Exception):
    """Raised when a playable round has no impact rows. Never swallowed into
    a zero-valued observation -- absent data is not zero impact."""


@dataclass
class RoundObservation:
    match_id: int
    round_id: int
    round_number: int

    # Component differentials (team A minus team B).
    damage: float
    econ_impact: float
    time_impact: float
    swing_impact: float

    # The single kill baseline: kills_A - kills_B. Deaths are ~redundant
    # (deaths_A == kills_B in 99.1% of rounds in this DB), so carrying them
    # separately would be the same column twice.
    kill_diff: float

    # The EXACT stored/calculated impact differential, carried alongside the
    # components rather than reconstructed from them. impact.py round()s
    # kill_impact, death_impact and each component independently, so
    # rebuilding "current Impact" from the four component columns accumulates
    # a couple of points of error per player-round -- across 10 players and
    # ~21 rounds that is enough to move a close comparison. The
    # current_impact candidate reads this field directly.
    impact_diff: float

    # Controls. Score is BEFORE this round, economy is at the START of this
    # round, side is DURING this round, and the round's own result is kept
    # as its own separate control -- never folded into the others.
    score_diff_before: int
    attacking_is_team_a: bool
    loadout_diff: float
    full_buy_count_diff: int

    # Outcomes.
    round_won_by_team_a: bool | None
    match_won_by_team_a: bool | None
    is_terminal: bool


def _winner_is_team_a(outcome: str | None) -> bool | None:
    if not outcome or outcome.endswith(SURRENDER_SUFFIX):
        return None
    if outcome.startswith("Team A"):
        return True
    if outcome.startswith("Team B"):
        return False
    return None


def _match_won_by_team_a(match) -> bool | None:
    """None for a tie -- excluded from every denominator, matching
    match_win()'s contract in app.services.player_profile_types."""
    if match.team1_rounds_won == match.team2_rounds_won:
        return None
    return match.team1_rounds_won > match.team2_rounds_won


def build_observations_for_match(match, calculated_rows) -> list[RoundObservation]:
    """`calculated_rows` are CalculatedImpact objects from
    build_impact_rows_for_match for this match only. Surrender placeholder
    rounds are dropped -- nobody played them."""
    team_by_mp = {
        mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team)
        for mp in match.match_players
    }
    team_a = Team.TEAM_1.value

    def team_of(match_player_id: int) -> str:
        # An unknown id silently defaulting to "not team A" would quietly
        # assign a stranger's kills and impact to team B.
        if match_player_id not in team_by_mp:
            raise MissingImpactRows(
                f"match {match.id}: match_player {match_player_id} is not in this match"
            )
        return team_by_mp[match_player_id]

    impact_by_round: dict[int, dict[str, float]] = {}
    impact_rows_by_round: dict[int, set[int]] = {}
    for row in calculated_rows:
        impact_rows_by_round.setdefault(row.round_id, set()).add(row.match_player_id)
        sign = 1.0 if team_of(row.match_player_id) == team_a else -1.0
        bucket = impact_by_round.setdefault(
            row.round_id,
            {"damage": 0.0, "econ_impact": 0.0, "time_impact": 0.0,
             "swing_impact": 0.0, "impact_diff": 0.0},
        )
        bucket["damage"] += sign * row.damage
        bucket["econ_impact"] += sign * row.econ_impact
        bucket["time_impact"] += sign * row.time_impact
        bucket["swing_impact"] += sign * row.swing_impact
        bucket["impact_diff"] += sign * row.impact

    playable = [
        r for r in sorted(match.rounds, key=lambda r: r.round_number)
        if not (r.outcome or "").endswith(SURRENDER_SUFFIX)
    ]
    match_result = _match_won_by_team_a(match)

    observations: list[RoundObservation] = []
    score_a = score_b = 0
    for index, round_row in enumerate(playable):
        kills_a = kills_b = 0
        loadout_a = loadout_b = 0
        players_a = players_b = 0
        full_buy_a = full_buy_b = 0
        for stat in round_row.player_stats:
            if team_of(stat.match_player_id) == team_a:
                kills_a += stat.kills
                loadout_a += stat.loadout
                players_a += 1
                full_buy_a += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0
            else:
                kills_b += stat.kills
                loadout_b += stat.loadout
                players_b += 1
                full_buy_b += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0

        # A round with no impact rows would otherwise silently become a
        # "zero impact" observation, which is a data point that says
        # something false. Fail loudly; the loader counts and reports
        # excluded matches.
        if round_row.id not in impact_by_round:
            raise MissingImpactRows(
                f"match {match.id} round {round_row.round_number} has no impact rows"
            )
        # PARTIAL coverage is as corrupting as none: a round scored for 7 of 10
        # players has component totals and full-buy counts that are simply
        # wrong, and would enter the regression looking like a legitimate
        # observation. Every participant with a stat row must also have an
        # impact row, and vice versa.
        stat_ids = {s.match_player_id for s in round_row.player_stats}
        impact_ids = impact_rows_by_round.get(round_row.id, set())
        if stat_ids != impact_ids:
            raise MissingImpactRows(
                f"match {match.id} round {round_row.round_number}: "
                f"{len(stat_ids)} stat rows vs {len(impact_ids)} impact rows"
            )
        impact = impact_by_round[round_row.id]
        won_by_a = _winner_is_team_a(round_row.outcome)

        observations.append(
            RoundObservation(
                match_id=match.id,
                round_id=round_row.id,
                round_number=round_row.round_number,
                damage=impact["damage"],
                econ_impact=impact["econ_impact"],
                time_impact=impact["time_impact"],
                swing_impact=impact["swing_impact"],
                impact_diff=impact["impact_diff"],
                kill_diff=kills_a - kills_b,
                score_diff_before=score_a - score_b,
                attacking_is_team_a=attacking_team_for_round(round_row.round_number) == Team.TEAM_1,
                # TEAM-AVERAGE, not sum: a sum silently encodes how many
                # player-stat rows a round happens to have, so a round
                # missing a player would read as a poorer economy.
                loadout_diff=(loadout_a / players_a if players_a else 0.0)
                - (loadout_b / players_b if players_b else 0.0),
                full_buy_count_diff=full_buy_a - full_buy_b,
                round_won_by_team_a=won_by_a,
                match_won_by_team_a=match_result,
                is_terminal=index == len(playable) - 1,
            )
        )

        if won_by_a is True:
            score_a += 1
        elif won_by_a is False:
            score_b += 1

    return observations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add per-round differential observation extraction"
```

---

### Task 8: Fold assignment and grouping

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `RoundObservation` (Task 7).
- Produces: `assign_folds(match_ids, n_folds=5, seed=0) -> dict[int, int]`; `group_by_match(observations) -> dict[int, list[RoundObservation]]`; `FEATURE_COMPONENTS`, `CONTROLS_RESULT`, `CONTROLS_CONTEXT`, `BASELINE_DAMAGE`, `BASELINE_KILL_DIFF`; `_feature_value(obs, name) -> float`; `_row(obs, names) -> list[float]`; `_half_of(round_number) -> int`; `FIRST_HALF_ROUNDS`, `SECOND_HALF_END`.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import (
    CONTROLS_CONTEXT,
    CONTROLS_RESULT,
    FEATURE_COMPONENTS,
    FIRST_HALF_ROUNDS,
    _feature_value,
    _half_of,
    assign_folds,
    group_by_match,
)


def test_every_match_gets_exactly_one_fold():
    folds = assign_folds([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], n_folds=5, seed=0)
    assert set(folds) == set(range(1, 11))
    assert set(folds.values()) <= {0, 1, 2, 3, 4}


def test_folds_are_balanced():
    folds = assign_folds(list(range(100)), n_folds=5, seed=0)
    counts = [sum(1 for f in folds.values() if f == k) for k in range(5)]
    assert max(counts) - min(counts) <= 1


def test_fold_assignment_is_seed_deterministic():
    assert assign_folds(list(range(50)), seed=11) == assign_folds(list(range(50)), seed=11)


def test_grouping_keeps_a_match_together():
    grouped = group_by_match(build_observations_for_match(_match_with_two_rounds(), _calculated()))
    assert list(grouped) == [1]
    assert len(grouped[1]) == 2


def test_half_boundaries_match_the_scorer_convention():
    """impact.py:309 already encodes rounds 12/24 as the economy resets."""
    assert _half_of(1) == _half_of(FIRST_HALF_ROUNDS) == 1
    assert _half_of(13) == _half_of(24) == 2
    assert _half_of(25) == 3


def test_round_result_control_is_signed_and_separate():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert _feature_value(obs[0], "round_result") == 1.0
    assert _feature_value(obs[1], "round_result") == -1.0
    assert "round_result" in CONTROLS_RESULT
    assert "round_result" not in CONTROLS_CONTEXT
    assert "round_result" not in FEATURE_COMPONENTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "fold or grouping or half or round_result" -v`
Expected: FAIL with `ImportError: cannot import name 'CONTROLS_CONTEXT'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py

FIRST_HALF_ROUNDS = 12
SECOND_HALF_END = 24

FEATURE_COMPONENTS = ["damage", "econ_impact", "time_impact", "swing_impact"]
BASELINE_DAMAGE = ["damage"]
BASELINE_KILL_DIFF = ["kill_diff"]
# The round's own result is a control in its own right. It is deliberately
# NOT merged into CONTROLS_CONTEXT: the control ladder's whole point is to
# measure what the components add ON TOP of knowing who won the round.
CONTROLS_RESULT = ["round_result"]
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff", "full_buy_count_diff"]

# Which nuisance controls belong with which target. DERIVED from the config
# rather than passed in, because the right answer differs per target and a
# caller passing the wrong set produces a plausible-looking but meaningless
# weighting.
#
#   T2  -> result + context. The whole claim is "the components add something
#          beyond knowing who won the round and what the teams could afford
#          next", which is exactly the control ladder's step 3 -> 4. Fitting
#          the weights without round_result would report weights from a
#          different model than the ladder validates.
#   WPA -> context only. round_result IS the WPA label; controlling for the
#          label would be circular.
#   T1  -> none. Its rows are whole-match aggregates, where a summed
#          per-round result control is just the halftime score, and
#          "does first-half Impact predict the match" is the question as
#          asked. Stated explicitly rather than defaulted.
TARGET_CONTROLS = {
    "T1": [],
    "T2": CONTROLS_RESULT + CONTROLS_CONTEXT,
    "WPA": CONTROLS_CONTEXT,
}


def controls_for(config) -> list[str]:
    if config.name not in TARGET_CONTROLS:
        raise ValueError(f"no control set declared for target {config.name!r}")
    return list(TARGET_CONTROLS[config.name])


def _feature_value(obs: RoundObservation, name: str) -> float:
    if name == "round_result":
        return 0.0 if obs.round_won_by_team_a is None else (1.0 if obs.round_won_by_team_a else -1.0)
    if name == "attacking_is_team_a":
        return 1.0 if obs.attacking_is_team_a else 0.0
    return float(getattr(obs, name))


def _row(obs: RoundObservation, feature_names: list[str]) -> list[float]:
    return [_feature_value(obs, name) for name in feature_names]


def _half_of(round_number: int) -> int:
    """1 = first half, 2 = second half, 3 = overtime. Mirrors the boundary
    impact.py:309 already uses -- the economy resets at halftime, so a
    forward window must never cross it."""
    if round_number <= FIRST_HALF_ROUNDS:
        return 1
    if round_number <= SECOND_HALF_END:
        return 2
    return 3


def assign_folds(match_ids, n_folds: int = 5, seed: int = 0) -> dict[int, int]:
    """match_id -> fold index. Folds are assigned by MATCH, never by row:
    two rounds of the same match are not independent, so splitting them
    across folds would leak."""
    unique = sorted(set(int(m) for m in match_ids))
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(unique))
    return {unique[int(pos)]: int(i % n_folds) for i, pos in enumerate(order)}


def group_by_match(observations) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for obs in observations:
        grouped.setdefault(obs.match_id, []).append(obs)
    return grouped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add match-level fold assignment, feature accessors and half boundaries"
```

---

### Task 9: Collapsed target builders — T1 and T2

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: Task 8's constants and helpers.
- Produces: `FitDataset` dataclass (`X`, `y`, `w`, `match_ids`, `feature_names`); `TargetConfig` frozen dataclass (`name`, `k`, `gamma`, `match_weight`); `build_target(observations, config, feature_names) -> FitDataset`; `first_half_target(observations, feature_names)`; `forward_window_target(observations, feature_names, k=3, gamma=0.7, match_weight=1.0)`.

**One collapsed row per source round, not one per future round.** For a weighted quasi-binomial fit, expanding round N into `k` rows with weights `γ^j` is mathematically identical to a single row whose target is the weighted mean of those outcomes and whose weight is `Σ γ^j`. Collapsing keeps `n` honest (~24k rounds rather than an inflated ~70k), makes the cluster bootstrap straightforward, and uses `fit_logistic`'s existing fractional-`y` support.

**Rules encoded here:**
- T1 requires all 12 genuine first-half rounds — 22 matches in the live DB fall short, so its `n` is 1,129, not 1,151.
- T2 never crosses the halftime reset or the OT boundary, skips terminal rounds, and attaches the match outcome only for `N ≤ 12` (for later rounds the match result is substantially determined by round N, reintroducing the tautology).

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import (
    FitDataset,
    TargetConfig,
    build_target,
    first_half_target,
    forward_window_target,
)


def _obs(round_number, damage, won_by_a, match_won, terminal=False, match_id=1):
    return RoundObservation(
        match_id=match_id, round_id=1000 * match_id + round_number, round_number=round_number,
        damage=damage, econ_impact=0.0, time_impact=0.0, swing_impact=0.0,
        impact_diff=damage, kill_diff=0.0,
        score_diff_before=0, attacking_is_team_a=True,
        loadout_diff=0.0, full_buy_count_diff=0,
        round_won_by_team_a=won_by_a, match_won_by_team_a=match_won, is_terminal=terminal,
    )


def _full_half(match_id=1, damage=10.0, won_by_a=True, match_won=True):
    obs = [_obs(n, damage, won_by_a, match_won, match_id=match_id) for n in range(1, 13)]
    obs[-1].is_terminal = True
    return obs


def test_first_half_requires_all_twelve_rounds():
    short = [_obs(n, 10.0, True, True) for n in range(1, 12)]
    assert len(first_half_target(short, FEATURE_COMPONENTS).y) == 0
    assert len(first_half_target(_full_half(), FEATURE_COMPONENTS).y) == 1


def test_first_half_sums_components_over_the_half():
    dataset = first_half_target(_full_half(), FEATURE_COMPONENTS)
    assert dataset.X[0][FEATURE_COMPONENTS.index("damage")] == 120.0
    assert dataset.y[0] == 1.0
    assert dataset.w[0] == 1.0


def test_first_half_excludes_tied_matches():
    obs = _full_half()
    for o in obs:
        o.match_won_by_team_a = None
    assert len(first_half_target(obs, FEATURE_COMPONENTS).y) == 0


def test_forward_window_collapses_to_one_row_per_source_round():
    """Five rounds, k=3: rounds 1 and 2 each get a full window, round 3 a
    partial one, round 4 one, round 5 is terminal. Five rounds in, four
    rows out -- one per non-terminal source round."""
    obs = [_obs(n, 1.0, True, True) for n in range(1, 6)]
    obs[-1].is_terminal = True
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=3, gamma=0.5, match_weight=0.0)
    assert len(dataset.y) == 4


def test_forward_window_target_is_the_weighted_fraction():
    """Round 1 sees rounds 2 (won) and 3 (lost) at gamma=0.5.
    y = (1*1 + 0.5*0) / 1.5 = 0.667, w = 1.5"""
    obs = [
        _obs(1, 1.0, True, True), _obs(2, 1.0, True, True),
        _obs(3, 1.0, False, True), _obs(4, 1.0, True, True, terminal=True),
    ]
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=2, gamma=0.5, match_weight=0.0)
    assert abs(dataset.y[0] - (1.0 / 1.5)) < 1e-12
    assert abs(dataset.w[0] - 1.5) < 1e-12


def test_forward_window_does_not_cross_halftime():
    """Round 12 is the last of the first half, so with no match auxiliary it
    contributes nothing; round 11 still gets its one in-half partner."""
    obs = [_obs(n, 1.0, True, True) for n in range(1, 25)]
    obs[-1].is_terminal = True
    only_twelve = [o for o in obs if o.round_number == 12]
    assert len(forward_window_target(only_twelve, FEATURE_COMPONENTS, k=3, match_weight=0.0).y) == 0

    eleven_twelve = [o for o in obs if o.round_number in (11, 12)]
    assert len(forward_window_target(eleven_twelve, FEATURE_COMPONENTS, k=3, match_weight=0.0).y) == 1


def test_forward_window_skips_terminal_rounds():
    obs = [_obs(1, 1.0, True, True), _obs(2, 1.0, True, True, terminal=True)]
    assert len(forward_window_target(obs, FEATURE_COMPONENTS, k=3, match_weight=0.0).y) == 1


def test_match_auxiliary_only_for_early_rounds():
    """Round 20's window is empty here, so with the auxiliary restricted to
    N <= 12 it contributes no row at all."""
    late = [_obs(20, 1.0, True, True), _obs(21, 1.0, True, True, terminal=True)]
    assert len(forward_window_target(late, FEATURE_COMPONENTS, k=3, match_weight=5.0).y) == 0


def test_match_auxiliary_shifts_target_and_weight_for_early_rounds():
    obs = [
        _obs(1, 1.0, True, False), _obs(2, 1.0, False, False),
        _obs(3, 1.0, True, False, terminal=True),
    ]
    without = forward_window_target(obs, FEATURE_COMPONENTS, k=1, gamma=1.0, match_weight=0.0)
    with_aux = forward_window_target(obs, FEATURE_COMPONENTS, k=1, gamma=1.0, match_weight=1.0)
    assert without.y[0] == 0.0 and without.w[0] == 1.0
    # round 2 lost, match lost -> y stays 0 but total weight doubles
    assert with_aux.w[0] == 2.0


def test_build_target_dispatches_on_config():
    config_one = TargetConfig(name="T1")
    config_two = TargetConfig(name="T2", k=2, gamma=0.5, match_weight=0.0)
    obs = _full_half()
    assert len(build_target(obs, config_one, FEATURE_COMPONENTS).y) == 1
    assert len(build_target(obs, config_two, FEATURE_COMPONENTS).y) > 1
    with pytest.raises(ValueError):
        build_target(obs, TargetConfig(name="nope"), FEATURE_COMPONENTS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "first_half or forward or auxiliary or build_target" -v`
Expected: FAIL with `ImportError: cannot import name 'FitDataset'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py

@dataclass
class FitDataset:
    X: np.ndarray
    y: np.ndarray
    w: np.ndarray
    match_ids: np.ndarray
    feature_names: list[str]


@dataclass(frozen=True)
class TargetConfig:
    """A fully-specified target. Passed INTO the CV orchestrator rather than
    used to pre-build a dataset, because each configuration produces
    different rows -- selecting among prebuilt datasets on the reporting
    folds is exactly the optimism this design avoids."""

    name: str
    k: int = 3
    gamma: float = 0.7
    match_weight: float = 1.0

    def target_identity(self) -> tuple:
        """What makes two configs the SAME prediction problem. Two configs
        differing here define different y, so their losses are not
        comparable -- see PRIMARY_T2."""
        return (self.name, self.k, self.gamma, self.match_weight)


# THE PRIMARY TARGETS ARE FROZEN, NOT SELECTED.
#
# k, gamma and match_weight change the DEFINITION of y, not just how well a
# model predicts a fixed outcome. A smoother target (larger k, higher gamma)
# or one diluted with the more-predictable match result has lower achievable
# entropy, so it wins a log-loss comparison for reasons that have nothing to
# do with whether Impact predicts winning. Selecting among them by their own
# losses would systematically prefer whichever outcome is easiest, and would
# let different outer folds pool predictions of different quantities.
#
# So: one primary target per family, declared up front. The rest are
# SENSITIVITY ANALYSES, compared only on the fixed binary yardsticks -- whose
# labels are identical across configurations -- never on their own losses.
PRIMARY_T1 = TargetConfig(name="T1")
PRIMARY_T2 = TargetConfig(name="T2", k=3, gamma=0.7, match_weight=1.0)
T2_SENSITIVITY_GRID = [
    TargetConfig(name="T2", k=k, gamma=g, match_weight=m)
    for k in (2, 3, 4)
    for g in (0.5, 0.7, 0.9)
    for m in (0.0, 0.5, 1.0)
]


def _empty_dataset(feature_names: list[str]) -> FitDataset:
    return FitDataset(
        X=np.zeros((0, len(feature_names))), y=np.zeros(0), w=np.zeros(0),
        match_ids=np.zeros(0, dtype=int), feature_names=list(feature_names),
    )


def _dataset(rows, ys, ws, mids, feature_names) -> FitDataset:
    if not rows:
        return _empty_dataset(feature_names)
    return FitDataset(
        np.array(rows, dtype=float), np.array(ys, dtype=float), np.array(ws, dtype=float),
        np.array(mids, dtype=int), list(feature_names),
    )


def first_half_target(observations, feature_names: list[str]) -> FitDataset:
    """T1: one row per ELIGIBLE match, components summed over rounds 1-12.

    A match missing any genuine first-half round is excluded rather than
    normalised -- a truncated total is not comparable to a full one. 22 of
    1,151 matches in this DB fall short once surrender placeholders are
    removed, so T1's n is 1,129.
    """
    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        first_half = [o for o in obs if o.round_number <= FIRST_HALF_ROUNDS]
        # The exact round SET, not just the count: a duplicated round number
        # alongside a missing one would pass a length check while silently
        # double-counting one round and dropping another.
        if {o.round_number for o in first_half} != set(range(1, FIRST_HALF_ROUNDS + 1)):
            continue
        result = first_half[0].match_won_by_team_a
        if result is None:
            continue
        rows.append([sum(_feature_value(o, name) for o in first_half) for name in feature_names])
        ys.append(1.0 if result else 0.0)
        ws.append(1.0)
        mids.append(match_id)
    return _dataset(rows, ys, ws, mids, feature_names)


def forward_window_target(
    observations, feature_names: list[str], k: int = 3, gamma: float = 0.7, match_weight: float = 1.0
) -> FitDataset:
    """T2: ONE collapsed row per non-terminal source round.

    y = weighted mean of the next k in-half round outcomes (weights
    gamma**j), w = the total of those weights. For a weighted
    quasi-binomial fit this is identical to expanding into k rows, but it
    keeps n at the true number of source rounds instead of inflating it,
    and makes the match-clustered bootstrap straightforward.

    Windows never cross the halftime reset or the OT boundary -- the same
    rule impact.py:309 encodes. Terminal rounds contribute nothing: they
    have no eligible future. The match-outcome auxiliary is attached only
    for N <= 12, because for later rounds the match result is
    substantially determined by round N.
    """
    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_number = {o.round_number: o for o in obs}
        for o in obs:
            if o.is_terminal:
                continue
            numerator = 0.0
            denominator = 0.0
            for step in range(1, k + 1):
                future = by_number.get(o.round_number + step)
                if future is None or _half_of(future.round_number) != _half_of(o.round_number):
                    break
                if future.round_won_by_team_a is None:
                    continue
                weight = gamma ** (step - 1)
                numerator += weight * (1.0 if future.round_won_by_team_a else 0.0)
                denominator += weight

            if (
                match_weight > 0
                and o.round_number <= FIRST_HALF_ROUNDS
                and o.match_won_by_team_a is not None
            ):
                numerator += match_weight * (1.0 if o.match_won_by_team_a else 0.0)
                denominator += match_weight

            if denominator == 0:
                continue
            rows.append(_row(o, feature_names))
            ys.append(numerator / denominator)
            ws.append(denominator)
            mids.append(match_id)
    return _dataset(rows, ys, ws, mids, feature_names)


def build_target(observations, config: TargetConfig, feature_names: list[str]) -> FitDataset:
    if config.name == "T1":
        return first_half_target(observations, feature_names)
    if config.name == "T2":
        return forward_window_target(
            observations, feature_names, k=config.k, gamma=config.gamma,
            match_weight=config.match_weight,
        )
    raise ValueError(f"unknown target: {config.name!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 26 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add collapsed T1/T2 target builders with half-boundary and auxiliary rules"
```

---

### Task 10: Nested CV orchestration over raw observations

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `build_target`, `TargetConfig`, `assign_folds`, `group_by_match` (Tasks 8-9); `standardize`, `back_transform`, `fit_logistic`, `predict_proba`, `weighted_log_loss` (Tasks 1-3).
- Produces: `FoldResult` dataclass (`fold`, `train_match_ids`, `test_match_ids`, `config`, `l2`, `beta_raw`, `feature_names`); `_select_config(train_obs, configs, feature_names, l2_grid, inner_folds, seed) -> tuple[TargetConfig, float]`; `cross_validate(observations, configs, feature_names, l2_grid, n_folds=5, inner_folds=3, seed=0) -> dict` with keys `"folds"` and `"oof"` (`scores`, `y`, `w`, `match_ids`); `split_observations(observations, folds, fold) -> tuple[list, list]`.

**This task is the reason the plan was rewritten.** The previous version pre-built one `FitDataset` per hyperparameter configuration, ran out-of-fold evaluation on each, picked the lowest loss, and then reported that same number. That selects on the reporting folds. Because different `k`/`gamma`/`match_weight` values produce *different rows*, a prebuilt dataset cannot be nested after the fact — the orchestrator has to receive raw observations and build the target *inside* each split.

**Contract:** within one outer fold, the test matches are touched exactly once, at prediction time. Configuration selection, L2 selection, standardization statistics, and the fit all see training matches only.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services import impact_eval
from app.services.impact_eval import (
    FoldResult,
    _select_config,
    cross_validate,
    oof_metrics,
    split_observations,
)

# One frozen target -- selection across target definitions is refused.
T2_CONFIGS = [TargetConfig(name="T2", k=3, gamma=0.7, match_weight=1.0)]


def _synthetic_matches(n_matches=60, seed=0):
    """Each match is a 12-round half where team A's damage differential
    predicts whether it wins its rounds."""
    rng = np.random.default_rng(seed)
    observations = []
    for match_id in range(n_matches):
        strength = rng.normal()
        obs = []
        for n in range(1, 13):
            won = rng.random() < 1.0 / (1.0 + np.exp(-2.0 * strength))
            o = _obs(n, strength * 10.0, won, strength > 0, match_id=match_id)
            obs.append(o)
        obs[-1].is_terminal = True
        observations.extend(obs)
    return observations


def test_split_puts_each_match_wholly_on_one_side():
    observations = _synthetic_matches(20)
    folds = assign_folds([o.match_id for o in observations], n_folds=5, seed=0)
    train, test = split_observations(observations, folds, 0)
    assert {o.match_id for o in train}.isdisjoint({o.match_id for o in test})
    assert len(train) + len(test) == len(observations)


def test_cross_validate_recovers_a_planted_signal():
    """Judged by weighted log loss against the intercept-only baseline --
    not by AUC on a rounded fractional target."""
    observations = _synthetic_matches(80)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [0.1, 1.0], seed=0)
    metrics = oof_metrics(result["oof"], draws=20, seed=0)
    assert metrics["improvement_over_intercept"] > 0
    assert all(f.beta_raw[FEATURE_COMPONENTS.index("damage") + 1] > 0 for f in result["folds"])


def test_selection_refuses_to_compare_different_targets():
    """The blocking bug this guard exists for: log loss against different y
    is not a comparison, and a smoother target wins for the wrong reason."""
    observations = _synthetic_matches(20)
    mixed = [
        TargetConfig(name="T2", k=2, gamma=0.5, match_weight=0.0),
        TargetConfig(name="T2", k=4, gamma=0.9, match_weight=1.0),
    ]
    with pytest.raises(ValueError, match="different target definitions"):
        _select_config(observations, mixed, FEATURE_COMPONENTS, [1.0], 3, 0)


def test_oof_metrics_reports_no_auc():
    observations = _synthetic_matches(30)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)
    metrics = oof_metrics(result["oof"], draws=20, seed=0)
    assert "auc" not in metrics
    assert "weighted_log_loss" in metrics


def test_every_match_appears_in_exactly_one_test_fold():
    observations = _synthetic_matches(50)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)
    test_ids = [mid for f in result["folds"] for mid in f.test_match_ids]
    assert len(test_ids) == len(set(test_ids))
    assert set(test_ids) == {o.match_id for o in observations}


def test_train_and_test_never_overlap_within_a_fold():
    observations = _synthetic_matches(40)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)
    for fold in result["folds"]:
        assert set(fold.train_match_ids).isdisjoint(fold.test_match_ids)


def test_selection_never_sees_the_test_fold(monkeypatch):
    """The property the whole rewrite exists to guarantee: hyperparameters
    are chosen from training matches only."""
    observations = _synthetic_matches(40)
    seen = []
    original = impact_eval._select_config

    def spy(train_obs, *args, **kwargs):
        seen.append({o.match_id for o in train_obs})
        return original(train_obs, *args, **kwargs)

    monkeypatch.setattr(impact_eval, "_select_config", spy)
    result = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)

    assert len(seen) == len(result["folds"])
    for fold, train_ids in zip(result["folds"], seen):
        assert train_ids.isdisjoint(fold.test_match_ids)


def test_select_config_returns_a_member_of_the_grid():
    observations = _synthetic_matches(30)
    config, l2 = _select_config(observations, T2_CONFIGS, FEATURE_COMPONENTS, [0.1, 1.0], 3, 0)
    assert config in T2_CONFIGS
    assert l2 in (0.1, 1.0)


def test_oof_weights_are_returned():
    """gamma and match_weight change row weights, so the weights must
    survive into reporting -- otherwise they influence the fit but not the
    number that judges it."""
    observations = _synthetic_matches(30)
    oof = cross_validate(observations, T2_CONFIGS, FEATURE_COMPONENTS, [1.0], seed=0)["oof"]
    assert len(oof["w"]) == len(oof["scores"]) == len(oof["y"]) == len(oof["match_ids"])
    assert np.all(oof["w"] > 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "split or cross_validate or selection or select_config or oof" -v`
Expected: FAIL with `ImportError: cannot import name 'FoldResult'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py
from app.services.stats_math import (
    apply_calibration,
    auc,
    back_transform,
    cluster_bootstrap_ci,
    fit_logistic,
    paired_bootstrap_delta,
    platt_calibrate,
    predict_proba,
    standardize,
    weighted_log_loss,
)


def paired_oof_log_loss_delta(oof_a: dict, oof_b: dict, draws: int = 200, seed: int = 0):
    """(point, lo, hi) for weighted-log-loss(a) - weighted-log-loss(b), with
    both sides evaluated on the SAME resampled matches each draw.

    Only valid when a and b predict the SAME target -- differing feature
    sets, yes; differing k/gamma/match_weight, no. See PRIMARY_T2.
    """
    combined: dict[int, tuple[list, list]] = {}
    for index, oof in ((0, oof_a), (1, oof_b)):
        for s, y, w, m in zip(oof["scores"], oof["y"], oof["w"], oof["match_ids"]):
            combined.setdefault(int(m), ([], []))[index].append((s, y, w))

    def side(index):
        def fn(sample):
            flat = [r for pair in sample for r in pair[index]]
            if not flat:
                return float("nan")
            return weighted_log_loss(
                [r[0] for r in flat], [r[1] for r in flat], [r[2] for r in flat]
            )

        return fn

    point = weighted_log_loss(oof_a["scores"], oof_a["y"], oof_a["w"]) - weighted_log_loss(
        oof_b["scores"], oof_b["y"], oof_b["w"]
    )
    lo, hi = paired_bootstrap_delta(side(0), side(1), combined, draws=draws, seed=seed)
    return point, lo, hi


@dataclass
class FoldResult:
    fold: int
    train_match_ids: list[int]
    test_match_ids: list[int]
    config: TargetConfig
    l2: float
    beta_raw: np.ndarray
    feature_names: list[str]


def split_observations(observations, folds: dict[int, int], fold: int):
    train = [o for o in observations if folds[o.match_id] != fold]
    test = [o for o in observations if folds[o.match_id] == fold]
    return train, test


def _fit_and_score(train_ds: FitDataset, test_ds: FitDataset, l2: float):
    """Standardize on TRAIN, fit on TRAIN, predict TEST. Returns
    (predictions, raw-unit beta) or None when either side is unusable."""
    if len(train_ds.y) == 0 or len(test_ds.y) == 0:
        return None
    scaled_train, scaled_test, centre, scale = standardize(train_ds.X, test_ds.X)
    beta = fit_logistic(scaled_train, train_ds.y, weights=train_ds.w, l2=l2)
    return predict_proba(beta, scaled_test), back_transform(beta, centre, scale)


def _select_config(train_obs, configs, feature_names, l2_grid, inner_folds: int, seed: int):
    """Inner CV over TRAINING observations only, selecting L2.

    REFUSES to compare configurations that define different targets. Log
    loss against different y is not a comparison -- see PRIMARY_T2. Pass a
    single frozen target; run alternatives as separate sensitivity runs and
    compare them on the fixed yardsticks instead.

    The target is still rebuilt inside every inner split, because that is
    what lets a target depend on a fold-fitted context (Stage B).
    """
    identities = {c.target_identity() for c in configs}
    if len(identities) > 1:
        raise ValueError(
            "_select_config cannot choose between different target definitions "
            f"({sorted(identities)}): their losses measure different outcomes. "
            "Freeze one target and compare alternatives on a fixed yardstick."
        )
    inner = assign_folds([o.match_id for o in train_obs], n_folds=inner_folds, seed=seed + 1)
    best = (configs[0], l2_grid[0])
    best_loss = float("inf")

    for config in configs:
        for l2 in l2_grid:
            losses, weights = [], []
            for fold in range(inner_folds):
                inner_train, inner_test = split_observations(train_obs, inner, fold)
                if not inner_train or not inner_test:
                    continue
                train_ds = build_target(inner_train, config, feature_names)
                test_ds = build_target(inner_test, config, feature_names)
                fitted = _fit_and_score(train_ds, test_ds, l2)
                if fitted is None:
                    continue
                preds, _ = fitted
                loss = weighted_log_loss(preds, test_ds.y, test_ds.w)
                if np.isfinite(loss):
                    losses.append(loss)
                    weights.append(float(test_ds.w.sum()))
            if not losses:
                continue
            mean_loss = float(np.average(losses, weights=weights))
            if mean_loss < best_loss:
                best, best_loss = (config, l2), mean_loss
    return best


def cross_validate(
    observations, configs, feature_names, l2_grid,
    n_folds: int = 5, inner_folds: int = 3, seed: int = 0,
) -> dict:
    """Outer CV that receives RAW OBSERVATIONS and a config grid.

    Within each outer fold: select (config, l2) on inner splits of the
    training matches, rebuild the target on the full training set, fit,
    and predict the untouched test matches. Nothing about the test fold
    influences selection, standardization, or fitting.
    """
    folds = assign_folds([o.match_id for o in observations], n_folds=n_folds, seed=seed)

    fold_results: list[FoldResult] = []
    scores, ys, ws, mids, baselines = [], [], [], [], []

    for fold in range(n_folds):
        train_obs, test_obs = split_observations(observations, folds, fold)
        if not train_obs or not test_obs:
            continue

        config, l2 = _select_config(train_obs, configs, feature_names, l2_grid, inner_folds, seed)
        train_ds = build_target(train_obs, config, feature_names)
        test_ds = build_target(test_obs, config, feature_names)
        fitted = _fit_and_score(train_ds, test_ds, l2)
        if fitted is None:
            continue
        preds, beta_raw = fitted

        fold_results.append(
            FoldResult(
                fold=fold,
                train_match_ids=sorted({o.match_id for o in train_obs}),
                test_match_ids=sorted({o.match_id for o in test_obs}),
                config=config,
                l2=l2,
                beta_raw=beta_raw,
                feature_names=list(feature_names),
            )
        )
        scores.extend(preds.tolist())
        ys.extend(test_ds.y.tolist())
        ws.extend(test_ds.w.tolist())
        mids.extend(test_ds.match_ids.tolist())

        # The "knows nothing" comparator, built from the TRAINING half's base
        # rate. Computing one base rate over all pooled OOF labels would let
        # each test fold's own outcomes into its own comparator.
        train_rate = float(np.average(train_ds.y, weights=train_ds.w))
        baselines.extend([train_rate] * len(test_ds.y))

    return {
        "folds": fold_results,
        "oof": {
            "scores": np.array(scores),
            "y": np.array(ys),
            "w": np.array(ws),
            "match_ids": np.array(mids, dtype=int),
            "baseline": np.array(baselines),
        },
    }


def oof_metrics(oof: dict, draws: int = 200, seed: int = 0) -> dict:
    """Weighted log loss ONLY, plus the intercept-only baseline it must beat.

    No AUC here, deliberately. T2's target is a weighted fraction of future
    round wins; rounding it at 0.5 to manufacture a binary label changes the
    estimand and discards the observation weights that gamma and
    match_weight exist to set. AUC belongs to the yardsticks, whose labels
    are genuinely binary.
    """
    if len(oof["y"]) == 0:
        return {"n": 0}
    groups: dict[int, list] = {}
    for s, y, w, m in zip(oof["scores"], oof["y"], oof["w"], oof["match_ids"]):
        groups.setdefault(int(m), []).append((s, y, w))

    def loss_of(sample):
        flat = [r for rows in sample for r in rows]
        return weighted_log_loss([r[0] for r in flat], [r[1] for r in flat], [r[2] for r in flat])

    # The "knows nothing" comparator, already computed per fold from TRAINING
    # base rates by cross_validate -- so the improvement below is genuinely
    # out-of-fold and can carry a paired interval.
    fitted_loss = weighted_log_loss(oof["scores"], oof["y"], oof["w"])
    baseline_probs = oof["baseline"]
    baseline_loss = weighted_log_loss(baseline_probs, oof["y"], oof["w"])

    paired: dict[int, tuple[list, list]] = {}
    for s, b, y, w, m in zip(oof["scores"], baseline_probs, oof["y"], oof["w"], oof["match_ids"]):
        entry = paired.setdefault(int(m), ([], []))
        entry[0].append((s, y, w))
        entry[1].append((b, y, w))

    def side(index):
        def fn(sample):
            flat = [r for pair in sample for r in pair[index]]
            if not flat:
                return float("nan")
            return weighted_log_loss(
                [r[0] for r in flat], [r[1] for r in flat], [r[2] for r in flat]
            )

        return fn

    lo, hi = paired_bootstrap_delta(side(1), side(0), paired, draws=draws, seed=seed)

    return {
        "weighted_log_loss": fitted_loss,
        "weighted_log_loss_ci": list(cluster_bootstrap_ci(loss_of, groups, draws=draws, seed=seed)),
        "intercept_only_log_loss": baseline_loss,
        "improvement_over_intercept": baseline_loss - fitted_loss,
        "improvement_ci": [lo, hi],
        "n": int(len(oof["y"])),
        "matches": len(groups),
        "total_weight": float(np.sum(oof["w"])),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 33 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add nested CV orchestration over raw observations

Target hyperparameters are now selected inside training folds. The
previous design pre-built a dataset per configuration, evaluated each
out-of-fold, and reported the winner from the same folds -- selecting on
the reporting data."
```

---

### Task 11: Constrained weights fitted with nuisance controls

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `build_target`, `FEATURE_COMPONENTS`, `CONTROLS_RESULT`, `CONTROLS_CONTEXT` (Tasks 8-9); `fit_logistic`, `weighted_log_loss`, `standardize` (Tasks 1-3).
- Produces: `ConstrainedWeights` dataclass (`damage_multiplier`, `econ`, `time`, `swing`, `train_log_loss`); `FACTOR_WEIGHT_TOTAL`; `_simplex_grid(step)`; `fit_constrained_weights(observations, config, control_names, simplex_step=0.1, damage_grid=None, l2=1.0) -> ConstrainedWeights`.

**Why the controls must be present during the search.** The control ladder's headline is "what do the components add *beyond* round result, context and damage". If the constrained weights are fitted without those controls, they can absorb variance the controls already explain — so the reported `FACTOR_WEIGHTS` would come from a different model than the one the ladder validates. The search therefore fits, for each candidate weighting,

```
logit(y) = nuisance controls + beta * composite(candidate)
```

and scores it by weighted log loss on the training rows.

**Why a search rather than a derivation from `beta`.** IRLS returns unconstrained coefficients. The shipped form is `impact = d*damage + (sum w_i*f_i)/sum(w)` with `w_i >= 0`, which is scale-invariant in `w` and therefore has 3 effective degrees of freedom. It cannot express an arbitrary coefficient vector, so it is searched directly. Negative *unconstrained* coefficients are reported honestly by Task 12, never clipped here.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import ConstrainedWeights, fit_constrained_weights


def _weighted_matches(n_matches=60, econ_weight=0.9, seed=5):
    """Rounds whose FUTURE outcome is driven mostly by econ_impact, so a
    correct search must put its weight there rather than on time/swing."""
    rng = np.random.default_rng(seed)
    observations = []
    for match_id in range(n_matches):
        obs = []
        for n in range(1, 13):
            econ = rng.normal()
            other = rng.normal()
            o = _obs(n, 0.0, None, True, match_id=match_id)
            o.econ_impact = econ * 10
            o.time_impact = other * 10
            o.swing_impact = rng.normal() * 10
            o.round_won_by_team_a = (econ_weight * econ + (1 - econ_weight) * other) > 0
            obs.append(o)
        obs[-1].is_terminal = True
        observations.extend(obs)
    return observations


def test_constrained_search_finds_the_dominant_component():
    obs = _weighted_matches()
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    assert result.econ > result.time
    assert result.econ > result.swing


def test_constrained_weights_are_non_negative_and_normalised():
    obs = _weighted_matches(n_matches=30)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    assert result.econ >= 0 and result.time >= 0 and result.swing >= 0
    assert abs((result.econ + result.time + result.swing) - 3.0) < 1e-6
    assert result.damage_multiplier >= 0


def test_constrained_search_is_deterministic():
    obs = _weighted_matches(n_matches=25)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    a = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    b = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    assert (a.econ, a.time, a.swing, a.damage_multiplier) == (
        b.econ, b.time, b.swing, b.damage_multiplier
    )


def test_controls_are_actually_in_the_design(monkeypatch):
    """If the controls were dropped, the design handed to fit_logistic
    would have exactly one column (the composite)."""
    widths = []
    original = impact_eval.fit_logistic

    def spy(X, *args, **kwargs):
        widths.append(np.asarray(X).shape[1])
        return original(X, *args, **kwargs)

    monkeypatch.setattr(impact_eval, "fit_logistic", spy)
    obs = _weighted_matches(n_matches=15)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    fit_constrained_weights(obs, config, CONTROLS_CONTEXT, simplex_step=0.5, damage_grid=[1.0])
    assert widths, "expected fits"
    assert all(w == len(CONTROLS_CONTEXT) + 1 for w in widths)


def test_empty_observations_return_neutral_weights():
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights([], config, CONTROLS_CONTEXT)
    assert isinstance(result, ConstrainedWeights)
    assert result.econ == result.time == result.swing == 1.0
    assert result.usable is False


def test_anti_predictive_components_do_not_yield_an_adoption_proposal():
    """The upside-down-Impact trap: if every component predicts LOSING, a
    negative composite slope would still fit well. Returning non-negative
    weights then publishes 'higher Impact is better' when the data said the
    opposite. The search must refuse."""
    rng = np.random.default_rng(31)
    observations = []
    for match_id in range(50):
        obs = []
        for n in range(1, 13):
            econ = rng.normal()
            o = _obs(n, 0.0, None, True, match_id=match_id)
            o.econ_impact = econ * 10
            o.time_impact = econ * 8
            o.swing_impact = econ * 6
            o.damage = econ * 12
            # Higher components -> LOSES the next round.
            o.round_won_by_team_a = econ < 0
            obs.append(o)
        obs[-1].is_terminal = True
        observations.extend(obs)

    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(observations, config, CONTROLS_CONTEXT)
    assert result.usable is False, (
        "an anti-predictive weighting was returned as usable; the deployment "
        "proposal would claim higher Impact is better"
    )


def test_usable_result_reports_a_positive_composite_slope():
    obs = _weighted_matches(n_matches=40, seed=32)
    config = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)
    result = fit_constrained_weights(obs, config, CONTROLS_CONTEXT)
    if result.usable:
        assert result.composite_slope > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k constrained -v`
Expected: FAIL with `ImportError: cannot import name 'ConstrainedWeights'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py

# Normalised so the three factor weights sum to 3, matching the shipped
# FACTOR_WEIGHTS = {"econ": 1.0, "time": 1.0, "swing": 1.0} convention.
FACTOR_WEIGHT_TOTAL = 3.0
DEFAULT_DAMAGE_GRID = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]


@dataclass
class ConstrainedWeights:
    damage_multiplier: float
    econ: float
    time: float
    swing: float
    train_log_loss: float
    # The fitted logistic slope on the composite. MUST be positive for the
    # weighting to mean "higher Impact is better"; see fit_constrained_weights.
    composite_slope: float = float("nan")
    usable: bool = True


def _simplex_grid(step: float):
    """All non-negative (a, b, c) with a + b + c == 1 on a `step` lattice."""
    steps = int(round(1.0 / step))
    for i in range(steps + 1):
        for j in range(steps + 1 - i):
            yield (i / steps, j / steps, (steps - i - j) / steps)


def fit_constrained_weights(
    observations, config: TargetConfig, control_names: list[str],
    simplex_step: float = 0.1, damage_grid=None, l2: float | None = None, context=None,
) -> ConstrainedWeights:
    """Search (damage_multiplier, w_econ, w_time, w_swing) under the shipped
    parameterization, WITH the nuisance controls in the design.

    Fitting the composite alone would let the component weights absorb
    variance the controls already explain, so the reported FACTOR_WEIGHTS
    would come from a different model than the control ladder validates.

    MUST be called on training-fold observations only.
    """
    neutral = ConstrainedWeights(1.0, 1.0, 1.0, 1.0, float("nan"), float("nan"), usable=False)
    if not observations:
        return neutral

    feature_names = FEATURE_COMPONENTS + list(control_names)
    # `context` carries a fold-fitted value model for a WPA config; T1/T2
    # ignore it. Passing it here is what lets Stage B produce a constrained
    # candidate on the same footing as T1/T2.
    dataset = build_target(observations, config, feature_names, context)
    if len(dataset.y) == 0:
        return neutral

    component_index = {name: feature_names.index(name) for name in FEATURE_COMPONENTS}
    damage = dataset.X[:, component_index["damage"]]
    factors = np.column_stack(
        [
            dataset.X[:, component_index["econ_impact"]],
            dataset.X[:, component_index["time_impact"]],
            dataset.X[:, component_index["swing_impact"]],
        ]
    )
    controls = (
        dataset.X[:, [feature_names.index(n) for n in control_names]]
        if control_names
        else np.zeros((len(dataset.y), 0))
    )

    # L2 here regularises the CONTROLLED composite design, which is a
    # different model from the feature-only fit whose L2 the outer fold
    # selected. Rather than inherit that value or sweep L2 inside the simplex
    # search (which would multiply the search by the grid size), pick it once
    # from a stand-in composite -- the shipped FACTOR_WEIGHTS -- on the same
    # controlled design, then hold it fixed across the search.
    if l2 is None:
        stand_in = (
            1.0 * damage
            + factors @ (np.array([FACTOR_WEIGHTS["econ"], FACTOR_WEIGHTS["time"],
                                   FACTOR_WEIGHTS["swing"]]) / sum(FACTOR_WEIGHTS.values()))
        )
        design = np.column_stack([controls, stand_in])
        scaled, _, _, _ = standardize(design, design)
        best_l2, best_l2_loss = 1.0, float("inf")
        for candidate_l2 in (0.01, 0.1, 1.0, 10.0):
            beta = fit_logistic(scaled, dataset.y, weights=dataset.w, l2=candidate_l2)
            loss = weighted_log_loss(predict_proba(beta, scaled), dataset.y, dataset.w)
            if np.isfinite(loss) and loss < best_l2_loss:
                best_l2, best_l2_loss = candidate_l2, loss
        l2 = best_l2

    grid = DEFAULT_DAMAGE_GRID if damage_grid is None else damage_grid
    best = None
    for weights in _simplex_grid(simplex_step):
        factor_score = factors @ np.array(weights)
        for d in grid:
            composite = d * damage + factor_score
            if composite.std() == 0:
                continue
            design = np.column_stack([controls, composite])
            scaled, _, _, _ = standardize(design, design)
            beta = fit_logistic(scaled, dataset.y, weights=dataset.w, l2=l2)
            loss = weighted_log_loss(predict_proba(beta, scaled), dataset.y, dataset.w)
            if not np.isfinite(loss):
                continue

            # The composite is the LAST design column, so beta[-1] is its
            # slope. A NEGATIVE slope means this weighting predicts well by
            # saying "more Impact, more likely to LOSE". The search would
            # otherwise happily pick it -- the loss is good -- and the
            # deployment proposal would publish non-negative component weights
            # as though higher meant better. Reject it outright.
            if beta[-1] <= 0:
                continue

            key = (loss, d, weights, float(beta[-1]))
            if best is None or key[:3] < best[:3]:
                best = key

    if best is None:
        # Every candidate was anti-predictive (or degenerate). That is a
        # finding, not a weighting: returning neutral weights marked unusable
        # keeps it out of the deployment proposal.
        return ConstrainedWeights(1.0, 1.0, 1.0, 1.0, float("nan"), float("nan"), usable=False)

    loss, d, weights, slope = best
    scaled_weights = [v * FACTOR_WEIGHT_TOTAL for v in weights]
    return ConstrainedWeights(
        damage_multiplier=float(d),
        econ=float(scaled_weights[0]),
        time=float(scaled_weights[1]),
        swing=float(scaled_weights[2]),
        train_log_loss=float(loss),
        composite_slope=float(slope),
        usable=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 38 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Fit constrained FACTOR_WEIGHTS with nuisance controls present

Without the controls in the design, the component weights absorb
variance the controls already explain, so the reported weights would come
from a different model than the control ladder validates."
```

---

### Task 12: Coefficient diagnostics

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `cross_validate`, `build_target`, `standardize`, `fit_logistic`, `back_transform`, `auc`.
- Produces: `paired_oof_log_loss_delta(oof_a, oof_b, draws=200, seed=0) -> (point, lo, hi)`; `coefficient_diagnostics(observations, config, feature_names, draws=200, seed=0, l2=1.0) -> dict` with keys `"sign_stability"`, `"sign_direction"`, `"correlation_matrix"`, `"drop_one"`, `"full_log_loss"`, `"bootstrap_draws_completed"`.

**Why these are mandatory:** `impact.py:496-502` builds all three components as `kill_order_bonus * <factor>` — they share a multiplicand by construction, so unstable coefficients are expected rather than surprising. A coefficient whose sign flips across resamples is reported as indeterminate, never as a finding.

**Sign stability needs a refitting bootstrap.** Resampling fixed out-of-fold *predictions* gives metric CIs but says nothing about coefficient stability — the model must be re-fit on each resampled set of matches.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import coefficient_diagnostics

DIAG_CONFIG = TargetConfig(name="T2", k=1, gamma=1.0, match_weight=0.0)


def test_sign_stability_is_high_for_a_clean_signal():
    obs = _weighted_matches(n_matches=80, econ_weight=1.0, seed=6)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=40, seed=0)
    assert diag["sign_stability"]["econ_impact"] > 0.9


def test_sign_stability_is_near_chance_for_a_pure_noise_column():
    """swing_impact contributes nothing to the label here, so its sign must
    not be reported as stable."""
    obs = _weighted_matches(n_matches=60, econ_weight=1.0, seed=7)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=40, seed=0)
    assert diag["sign_stability"]["swing_impact"] < 0.95


def test_correlation_matrix_detects_a_duplicated_column():
    obs = _weighted_matches(n_matches=30, seed=8)
    for o in obs:
        o.time_impact = o.econ_impact
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=10, seed=0)
    assert abs(diag["correlation_matrix"]["econ_impact"]["time_impact"] - 1.0) < 1e-9


def test_drop_one_reports_every_component_in_weighted_log_loss():
    obs = _weighted_matches(n_matches=40, seed=9)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=10, seed=0)
    assert set(diag["drop_one"]) == set(FEATURE_COMPONENTS)
    for entry in diag["drop_one"].values():
        assert "log_loss_cost_of_dropping" in entry
        assert "cost_ci" in entry, "the cost of dropping needs a PAIRED interval"
        assert "auc_without" not in entry, "no AUC on a fractional target"


def test_sign_direction_distinguishes_helpful_from_anti_predictive():
    obs = _weighted_matches(n_matches=60, econ_weight=1.0, seed=10)
    diag = coefficient_diagnostics(obs, DIAG_CONFIG, FEATURE_COMPONENTS, draws=30, seed=0)
    assert 0.0 <= diag["sign_direction"]["econ_impact"] <= 1.0
    # stability is the folded magnitude; direction says which way
    assert diag["sign_stability"]["econ_impact"] == max(
        diag["sign_direction"]["econ_impact"], 1 - diag["sign_direction"]["econ_impact"]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "sign_stability or correlation_matrix or drop_one" -v`
Expected: FAIL with `ImportError: cannot import name 'coefficient_diagnostics'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py

def coefficient_diagnostics(
    observations, config: TargetConfig, feature_names: list[str],
    draws: int = 200, seed: int = 0, l2: float = 1.0,
) -> dict:
    """Collinearity reporting for a fit whose components share a
    multiplicand by construction (impact.py:496-502).

    sign_stability is a REFITTING bootstrap over resampled MATCHES: the
    model is re-fit on each draw. Resampling fixed predictions could not
    say anything about coefficient signs.
    """
    grouped = group_by_match(observations)
    keys = list(grouped)
    if not keys:
        return {"sign_stability": {}, "sign_direction": {}, "correlation_matrix": {},
                "drop_one": {}, "full_log_loss": float("nan"),
                "bootstrap_draws_completed": 0}

    rng = np.random.default_rng(seed)
    positives = np.zeros(len(feature_names))
    completed = 0
    for _ in range(draws):
        picked = rng.integers(0, len(keys), size=len(keys))
        sample = [o for i in picked for o in grouped[keys[int(i)]]]
        dataset = build_target(sample, config, feature_names)
        if len(dataset.y) == 0 or len(np.unique(np.round(dataset.y))) < 2:
            continue
        scaled, _, centre, scale = standardize(dataset.X, dataset.X)
        beta = fit_logistic(scaled, dataset.y, weights=dataset.w, l2=l2)
        positives += (back_transform(beta, centre, scale)[1:] > 0).astype(float)
        completed += 1

    # Direction as well as magnitude: max(pos, neg) alone cannot distinguish
    # "consistently helpful" from "consistently anti-predictive", and those
    # mean opposite things for a component that is supposed to measure impact.
    sign_stability = {
        name: (float(max(p, completed - p) / completed) if completed else float("nan"))
        for name, p in zip(feature_names, positives)
    }
    sign_direction = {
        name: (float(p / completed) if completed else float("nan"))
        for name, p in zip(feature_names, positives)
    }

    full_dataset = build_target(observations, config, feature_names)
    corr = np.corrcoef(full_dataset.X, rowvar=False)
    correlation_matrix = {
        a: {b: float(corr[i][j]) for j, b in enumerate(feature_names)}
        for i, a in enumerate(feature_names)
    }

    # Drop-one is measured in WEIGHTED LOG LOSS on the fixed target, not in
    # AUC over a rounded fractional label. The target is identical across
    # every variant here (only the feature set changes), so the losses ARE
    # comparable -- unlike a comparison across target definitions.
    full = cross_validate(observations, [config], feature_names, [l2], seed=seed)
    full_loss = (
        weighted_log_loss(full["oof"]["scores"], full["oof"]["y"], full["oof"]["w"])
        if len(full["oof"]["y"])
        else float("nan")
    )

    drop_one = {}
    for name in feature_names:
        reduced_names = [n for n in feature_names if n != name]
        if not reduced_names:
            continue
        out = cross_validate(observations, [config], reduced_names, [l2], seed=seed)
        without = (
            weighted_log_loss(out["oof"]["scores"], out["oof"]["y"], out["oof"]["w"])
            if len(out["oof"]["y"])
            else float("nan")
        )
        _, lo, hi = paired_oof_log_loss_delta(out["oof"], full["oof"], draws=draws, seed=seed)
        drop_one[name] = {
            "log_loss_without": without,
            "log_loss_cost_of_dropping": without - full_loss,
            "cost_ci": [lo, hi],
        }

    return {
        "sign_stability": sign_stability,
        "sign_direction": sign_direction,
        "correlation_matrix": correlation_matrix,
        "full_log_loss": full_loss,
        "drop_one": drop_one,
        "bootstrap_draws_completed": completed,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 42 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add coefficient diagnostics with a refitting bootstrap for sign stability"
```

---

### Task 13: Stage 0 — cohorts, centring, per-player correlations

**Files:**
- Create: `webapp/app/services/impact_stage0.py`
- Test: `webapp/tests/test_stage0_cohorts.py`

**Interfaces:**
- Consumes: `point_biserial`, `tercile_buckets`, `cluster_bootstrap_ci` (Tasks 1, 3).
- Produces: `PlayerMatch` dataclass (`player_id`, `match_id`, `avg_impact`, `won`); `COHORT_RULES`; `filter_cohort(rows, min_matches)`; `pooled_relationship(rows)`; `within_player_centered(rows, min_matches=2)`; `per_player_correlations(rows, min_matches=10)`; `within_player_tercile_lift(rows, min_matches=None)`; `stage0_report(rows, roster_player_ids, draws=200, seed=0) -> dict`.

**Cohorts are mandatory, and the numbers are extreme.** Measured on this DB: **7,814 of 8,251 players (94.7%) have exactly one match.** A naive all-player within-person calculation is overwhelmingly rows whose centred Impact is exactly 0 by construction — a zero-variance artifact, not a finding. Usable cohorts: 437 players with ≥2 matches, 81 with ≥9 (three per tercile), 71 with ≥10.

**Four distinct analyses, not one repeated.** An earlier draft filtered rows by cohort and re-ran the *same* pooled correlation, which does not compute per-player correlations at all. Stage 0 reports:

1. **Raw pooled** relationship — Impact vs win/loss across all player-matches.
2. **Within-player-centred pooled** relationship — each player's Impact minus their own mean, which removes between-player skill differences.
3. **Per-player correlation distribution** — one correlation per eligible player, summarised by median/IQR/fraction positive. This is a different object from a pooled statistic.
4. **Within-player tercile lift** — the form the P2 card will display.

**Everything is recomputed inside each bootstrap resample** — player means, cohort eligibility and tercile boundaries all derive from the rows passed in, so a resample recomputes them rather than reusing fixed ones.

**Stage 0 uses stored (realized) scores** — that is what the live scorer wrote and therefore what "Impact as it ships today" is. It feeds no forward-looking fit, so the leakage constraint does not apply.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_stage0_cohorts.py
"""Stage 0 answers the original question -- does a player's Impact track
their wins -- on the CURRENT stored scores, before any fitting.

The cohort rules exist because 94.7% of players in this DB have exactly
one match, which would make an uncontrolled within-player calculation
almost entirely zero-variance rows."""

import numpy as np

from app.services.impact_stage0 import (
    COHORT_RULES,
    PlayerMatch,
    filter_cohort,
    per_player_correlations,
    pooled_relationship,
    stage0_report,
    within_player_centered,
    within_player_tercile_lift,
)


def _history(player_id, impacts, wins):
    return [
        PlayerMatch(player_id=player_id, match_id=player_id * 100 + i, avg_impact=v, won=w)
        for i, (v, w) in enumerate(zip(impacts, wins))
    ]


def test_cohort_rules_match_the_spec():
    assert COHORT_RULES["recurrent"] == 2
    assert COHORT_RULES["per_player_tercile"] == 9
    assert COHORT_RULES["per_player_correlation"] == 10


def test_filter_cohort_drops_single_match_players():
    rows = _history(1, [100.0], [True]) + _history(2, [100.0, 200.0], [False, True])
    assert {r.player_id for r in filter_cohort(rows, min_matches=2)} == {2}


def test_single_match_player_centres_to_exactly_zero():
    """The exact artifact the cohort rule exists to exclude."""
    result = within_player_centered(_history(1, [500.0], [True]), min_matches=1)
    assert result["n"] == 1
    assert np.isnan(result["point_biserial"])  # zero variance


def test_pooled_relationship_reports_correlation_and_counts():
    result = pooled_relationship(_history(1, [10.0, 20.0, 30.0, 40.0], [False, False, True, True]))
    assert result["n"] == 4
    assert result["point_biserial"] > 0.8
    assert result["win_rate"] == 0.5
    assert result["mean_impact_in_wins"] > result["mean_impact_in_losses"]


def test_within_player_centering_removes_between_player_offsets():
    """Two players with opposite absolute levels but identical internal
    patterns: pooled correlation is destroyed by the offset, centred is
    not."""
    strong = _history(1, [900.0, 1000.0], [False, True])
    weak = _history(2, [100.0, 200.0], [False, True])
    rows = strong + weak
    for r in strong:
        r.won = not r.won  # strong player loses when scoring high
    centred = within_player_centered(rows, min_matches=2)
    assert centred["players"] == 2
    assert np.isfinite(centred["point_biserial"])


def test_per_player_correlations_are_one_per_player():
    """A distribution, not a pooled number: three eligible players give
    three correlations."""
    rows = []
    for pid in (1, 2, 3):
        impacts = [float(i) for i in range(10)]
        rows += _history(pid, impacts, [i >= 5 for i in range(10)])
    result = per_player_correlations(rows, min_matches=10)
    assert result["players"] == 3
    assert len(result["values"]) == 3
    assert result["median"] > 0
    assert result["fraction_positive"] == 1.0


def test_per_player_correlations_skip_ineligible_players():
    result = per_player_correlations(_history(1, [1.0, 2.0], [True, False]), min_matches=10)
    assert result["players"] == 0
    assert np.isnan(result["median"])


def test_within_player_terciles_measure_lift_against_own_baseline():
    impacts = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
    wins = [False, False, False, False, True, False, True, True, True]
    result = within_player_tercile_lift(_history(1, impacts, wins), min_matches=9)
    assert result["top_win_rate"] == 1.0
    assert result["bottom_win_rate"] == 0.0
    assert result["lift"] == 1.0
    assert result["players"] == 1


def test_within_player_terciles_skip_ineligible_players():
    result = within_player_tercile_lift(_history(1, [1.0, 2.0], [True, False]), min_matches=9)
    assert result["players"] == 0
    assert np.isnan(result["lift"])


def test_stage0_report_has_every_required_section():
    rows = []
    for pid in range(1, 6):
        impacts = [float(i * 10) for i in range(10)]
        rows += _history(pid, impacts, [i >= 5 for i in range(10)])
    report = stage0_report(rows, roster_player_ids={1, 2}, draws=20, seed=0)

    assert set(report) >= {
        "variant", "pooled", "within_player_centered", "per_player_correlations",
        "within_player_terciles", "cohorts",
    }
    assert report["variant"] == "realized"
    assert "roster" in report["cohorts"] and "recurrent" in report["cohorts"]
    assert report["cohorts"]["roster"]["players"] == 2
    for section in ("pooled", "within_player_centered", "within_player_terciles"):
        assert "ci" in report[section], f"{section} must carry a bootstrap CI"
    assert "median_ci" in report["per_player_correlations"]
    for cohort in report["cohorts"].values():
        assert "ci" in cohort["pooled"]
        assert "median_ci" in cohort["per_player_correlations"]
        assert "ci" in cohort["within_player_terciles"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stage0_cohorts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.impact_stage0'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/impact_stage0.py
"""Stage 0: what does Impact, exactly as it ships today, say about winning?

Runs BEFORE any fitting, on the CURRENT stored scores -- which means the
`realized` swing variant, since that is what the live scorer wrote. Stage 0
describes the shipped metric rather than feeding a forward-looking fit, so
the leakage constraint does not apply here.

Cohorts are not optional. Measured 2026-09-01: 7,814 of 8,251 players
(94.7%) have exactly one match, so an uncontrolled within-person
calculation is almost entirely rows whose centred Impact is 0 by
construction.
"""

from dataclasses import dataclass

import numpy as np

from app.services.stats_math import cluster_bootstrap_ci, point_biserial, tercile_buckets

COHORT_RULES = {
    "recurrent": 2,                # >= 2 decided matches      (437 players)
    "per_player_tercile": 9,       # >= 3 matches per bucket   (81 players)
    "per_player_correlation": 10,  # per-player correlation    (71 players)
}


@dataclass
class PlayerMatch:
    player_id: int
    match_id: int
    avg_impact: float
    won: bool


def _by_player(rows) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row.player_id, []).append(row)
    return grouped


def filter_cohort(rows, min_matches: int) -> list[PlayerMatch]:
    grouped = _by_player(rows)
    return [row for row in rows if len(grouped[row.player_id]) >= min_matches]


def pooled_relationship(rows) -> dict:
    """Raw pooled Impact vs win/loss. Confounded by between-player skill --
    that is what within_player_centered exists to remove."""
    impacts = np.array([r.avg_impact for r in rows], dtype=float)
    wins = np.array([1 if r.won else 0 for r in rows], dtype=int)
    if len(impacts) == 0:
        return {"n": 0, "point_biserial": float("nan"), "win_rate": float("nan")}
    return {
        "n": len(impacts),
        "players": len(_by_player(rows)),
        "point_biserial": point_biserial(impacts, wins),
        "win_rate": float(wins.mean()),
        "mean_impact_in_wins": float(impacts[wins == 1].mean()) if (wins == 1).any() else float("nan"),
        "mean_impact_in_losses": float(impacts[wins == 0].mean()) if (wins == 0).any() else float("nan"),
    }


def within_player_centered(rows, min_matches: int = 2) -> dict:
    """Each player's Impact minus their OWN mean, then pooled. Removes
    between-player skill level, so what is left is "when this player plays
    above their own baseline, do they win more?"."""
    grouped = _by_player(rows)
    values, labels, eligible = [], [], 0
    for player_rows in grouped.values():
        if len(player_rows) < min_matches:
            continue
        eligible += 1
        mean = float(np.mean([r.avg_impact for r in player_rows]))
        for r in player_rows:
            values.append(r.avg_impact - mean)
            labels.append(1 if r.won else 0)
    if not values:
        return {"n": 0, "players": 0, "point_biserial": float("nan")}
    return {
        "n": len(values),
        "players": eligible,
        "point_biserial": point_biserial(values, labels),
    }


def per_player_correlations(rows, min_matches: int | None = None) -> dict:
    """ONE correlation per eligible player, summarised as a distribution.

    Distinct from a pooled statistic: filtering rows by cohort and re-running
    a pooled correlation does not compute per-player correlations.
    """
    threshold = COHORT_RULES["per_player_correlation"] if min_matches is None else min_matches
    values = []
    for player_rows in _by_player(rows).values():
        if len(player_rows) < threshold:
            continue
        r = point_biserial(
            [row.avg_impact for row in player_rows],
            [1 if row.won else 0 for row in player_rows],
        )
        if np.isfinite(r):
            values.append(float(r))
    if not values:
        return {"players": 0, "values": [], "median": float("nan"),
                "iqr": [float("nan"), float("nan")], "fraction_positive": float("nan")}
    arr = np.array(values)
    q1, q3 = np.percentile(arr, [25, 75])
    return {
        "players": len(values),
        "values": values,
        "median": float(np.median(arr)),
        "iqr": [float(q1), float(q3)],
        "fraction_positive": float((arr > 0).mean()),
    }


def within_player_tercile_lift(rows, min_matches: int | None = None) -> dict:
    """Terciles computed WITHIN each player, then pooled -- the form the
    player page will display ("a top-third game for me"), not global
    terciles after centring."""
    threshold = COHORT_RULES["per_player_tercile"] if min_matches is None else min_matches

    top_wins = top_total = bottom_wins = bottom_total = 0
    eligible = 0
    for player_rows in _by_player(rows).values():
        if len(player_rows) < threshold:
            continue
        eligible += 1
        buckets = tercile_buckets([r.avg_impact for r in player_rows])
        for row, bucket in zip(player_rows, buckets):
            if bucket == 2:
                top_total += 1
                top_wins += 1 if row.won else 0
            elif bucket == 0:
                bottom_total += 1
                bottom_wins += 1 if row.won else 0

    top_rate = top_wins / top_total if top_total else float("nan")
    bottom_rate = bottom_wins / bottom_total if bottom_total else float("nan")
    return {
        "players": eligible,
        "top_win_rate": top_rate,
        "bottom_win_rate": bottom_rate,
        "lift": top_rate - bottom_rate,
        "top_n": top_total,
        "bottom_n": bottom_total,
    }


def _grouped_by_match(rows) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row.match_id, []).append(row)
    return grouped


def _ci(metric_fn, rows, draws: int, seed: int):
    """Bootstrap clustered by MATCH. metric_fn receives a flat row list, so
    player means, eligibility and tercile boundaries are all recomputed
    inside each resample rather than held fixed."""
    groups = _grouped_by_match(rows)

    def wrapped(sample):
        flat = [r for rows_ in sample for r in rows_]
        return metric_fn(flat)

    return list(cluster_bootstrap_ci(wrapped, groups, draws=draws, seed=seed))


def stage0_report(rows, roster_player_ids=None, draws: int = 200, seed: int = 0) -> dict:
    """Everything Stage 0 owes the spec, each headline number with a
    match-clustered CI."""
    roster_player_ids = set(roster_player_ids or ())
    roster_rows = [r for r in rows if r.player_id in roster_player_ids]
    recurrent_rows = filter_cohort(rows, COHORT_RULES["recurrent"])

    report = {
        "variant": "realized",
        "note": "stored scores as the live scorer wrote them; not an input to any forward-looking fit",
        "pooled": {
            **pooled_relationship(rows),
            "ci": _ci(lambda r: pooled_relationship(r)["point_biserial"], rows, draws, seed),
        },
        "within_player_centered": {
            **within_player_centered(rows),
            "ci": _ci(lambda r: within_player_centered(r)["point_biserial"], rows, draws, seed),
        },
        "per_player_correlations": per_player_correlations(rows),
        "within_player_terciles": {
            **within_player_tercile_lift(rows),
            "ci": _ci(lambda r: within_player_tercile_lift(r)["lift"], rows, draws, seed),
        },
        "cohorts": {},
    }

    # Every headline number gets an interval, in the cohorts too -- a cohort
    # of 71 players is exactly where an uncertainty-free point estimate
    # misleads most.
    for name, cohort_rows in (("roster", roster_rows), ("recurrent", recurrent_rows)):
        report["cohorts"][name] = {
            "players": len(_by_player(cohort_rows)),
            "pooled": {
                **pooled_relationship(cohort_rows),
                "ci": _ci(lambda r: pooled_relationship(r)["point_biserial"],
                          cohort_rows, draws, seed),
            },
            "within_player_centered": {
                **within_player_centered(cohort_rows),
                "ci": _ci(lambda r: within_player_centered(r)["point_biserial"],
                          cohort_rows, draws, seed),
            },
            "per_player_correlations": {
                **per_player_correlations(cohort_rows),
                "median_ci": _ci(lambda r: per_player_correlations(r)["median"],
                                 cohort_rows, draws, seed),
            },
            "within_player_terciles": {
                **within_player_tercile_lift(cohort_rows),
                "ci": _ci(lambda r: within_player_tercile_lift(r)["lift"],
                          cohort_rows, draws, seed),
            },
        }

    report["per_player_correlations"]["median_ci"] = _ci(
        lambda r: per_player_correlations(r)["median"], rows, draws, seed
    )
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stage0_cohorts.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_stage0.py webapp/tests/test_stage0_cohorts.py
git commit -m "Add Stage 0 with cohorts, within-player centring and per-player correlations"
```

---

### Task 14: Yardsticks, per-fold candidates and the comparison matrix

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `cross_validate`, `fit_constrained_weights`, `FoldResult` (Tasks 10-11); `paired_bootstrap_delta`, `platt_calibrate`, `apply_calibration` (Task 3).
- Produces: `Candidate` dataclass (`name`, `feature_names`, `weights`); `CURRENT_IMPACT_CANDIDATE`; `BASELINE_CANDIDATES`; `candidate_from_constrained(name, weights) -> Candidate`; `yardstick_first_half`, `yardstick_full_match`, `yardstick_forward_rounds`, each `(observations, candidate) -> (scores, labels, match_ids)`; `YARDSTICKS`; `fold_candidates(observations, fold_results, name, context_builder=None) -> (dict[int, Candidate], dict[int, ConstrainedWeights])` (controls are derived from the target via `controls_for`, not passed); `yardstick_matrix(observations, fixed_candidates, per_fold_candidates, folds, draws, seed) -> dict`.

**The three yardsticks:**

1. **First half → match outcome.** A single number — not split by attack/defense, since `attacking_team_for_round` makes every first-half row attack-first for team A.
2. **Full match → match outcome.** Read **only as the paired gap over kill differential**; the absolute figure is inflated because the features contain the outcome's own kills.
3. **Round N → rounds N+2 onward** within the same half. Starting at N+2 keeps the immediately-following round — the strongest post-round mediator — out of the label.

**Fitted candidates are per-fold.** A weighting fitted on all matches and then scored on all matches is scored on its own training data; cross-validating only the downstream *calibration* does not fix that. So `fold_candidates` fits one `ConstrainedWeights` per outer fold from that fold's **training** matches, and the matrix applies each fold's candidate only to that fold's **test** matches before pooling. A separate all-data fit is reported by the CLI as the *deployment proposal*, explicitly labelled as not its own evaluation.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    CURRENT_IMPACT_CANDIDATE,
    Candidate,
    fold_candidates,
    yardstick_first_half,
    yardstick_forward_rounds,
    yardstick_full_match,
    yardstick_matrix,
)


def _decisive_match(match_id, damage, team_a_wins):
    obs = [_obs(n, damage, team_a_wins, team_a_wins, match_id=match_id) for n in range(1, 13)]
    obs[-1].is_terminal = True
    return obs


def test_first_half_yardstick_scores_one_row_per_eligible_match():
    obs = _decisive_match(1, 10.0, True) + _decisive_match(2, -10.0, False)
    scores, labels, mids = yardstick_first_half(obs, CURRENT_IMPACT_CANDIDATE)
    assert len(scores) == 2
    assert labels == [1, 0]
    assert scores[0] > scores[1]
    assert sorted(mids) == [1, 2]


def test_first_half_yardstick_skips_incomplete_matches():
    short = [_obs(n, 1.0, True, True, match_id=9) for n in range(1, 6)]
    assert yardstick_first_half(short, CURRENT_IMPACT_CANDIDATE)[0] == []


def test_full_match_yardstick_uses_every_round():
    obs = _decisive_match(1, 10.0, True) + [_obs(13, 10.0, True, True, match_id=1)]
    half, _, _ = yardstick_first_half(obs, CURRENT_IMPACT_CANDIDATE)
    full, _, _ = yardstick_full_match(obs, CURRENT_IMPACT_CANDIDATE)
    assert full[0] > half[0]


def test_forward_rounds_yardstick_labels_rounds_two_ahead():
    """Round 1's label comes from rounds 3+, never rounds 1 or 2."""
    obs = [_obs(n, 1.0, n > 2, True, match_id=1) for n in range(1, 13)]
    obs[-1].is_terminal = True
    scores, labels, _ = yardstick_forward_rounds(obs, CURRENT_IMPACT_CANDIDATE)
    assert labels[0] == 1


def test_baselines_are_not_duplicates():
    """kills and deaths were the same column twice; only one kill baseline
    survives."""
    names = {c.name for c in BASELINE_CANDIDATES}
    assert "kill_diff" in names
    assert "kills_and_deaths" not in names
    assert all(isinstance(c, Candidate) for c in BASELINE_CANDIDATES)


def test_fold_candidates_are_fitted_on_training_matches_only(monkeypatch):
    observations = _weighted_matches(n_matches=40, seed=11)
    result = cross_validate(observations, [DIAG_CONFIG], FEATURE_COMPONENTS, [1.0], seed=0)

    seen = []
    original = impact_eval.fit_constrained_weights

    def spy(obs, *args, **kwargs):
        seen.append({o.match_id for o in obs})
        return original(obs, *args, **kwargs)

    monkeypatch.setattr(impact_eval, "fit_constrained_weights", spy)
    fold_candidates(observations, result["folds"], "fitted")

    assert len(seen) == len(result["folds"])
    for fold, train_ids in zip(result["folds"], seen):
        assert train_ids.isdisjoint(fold.test_match_ids)


def test_fold_candidates_returns_the_weights_for_reporting():
    observations = _weighted_matches(n_matches=30, seed=14)
    result = cross_validate(observations, [DIAG_CONFIG], FEATURE_COMPONENTS, [1.0], seed=0)
    candidates, weights = fold_candidates(observations, result["folds"], "fitted")
    assert set(candidates) == set(weights)
    assert all(hasattr(w, "econ") for w in weights.values())


def test_controls_are_derived_per_target():
    """round_result belongs with T2 (the ladder's claim) but never with WPA,
    where it is the label."""
    from app.services.impact_eval import controls_for

    assert "round_result" in controls_for(TargetConfig(name="T2"))
    assert "round_result" not in controls_for(TargetConfig(name="WPA"))
    assert controls_for(TargetConfig(name="T1")) == []
    with pytest.raises(ValueError, match="no control set"):
        controls_for(TargetConfig(name="nope"))


def test_matrix_scores_each_fold_candidate_on_its_own_test_matches():
    observations = _weighted_matches(n_matches=40, seed=12)
    result = cross_validate(observations, [DIAG_CONFIG], FEATURE_COMPONENTS, [1.0], seed=0)
    folds = {f.fold: f for f in result["folds"]}
    per_fold = fold_candidates(observations, result["folds"], "fitted")

    matrix = yardstick_matrix(
        observations, [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES],
        {"fitted": per_fold[0]}, folds, draws=20, seed=0,
    )
    assert "forward_rounds" in matrix
    cell = matrix["forward_rounds"]["fitted"]
    assert cell is not None and cell["n"] > 0
    assert "gap_over_kill_diff" in cell
    assert "gap_ci" in cell, "the gap needs a PAIRED interval, not two separate ones"
    assert "log_loss_ci" in cell, "every cell carries CIs for both metrics"


def test_matrix_reports_paired_gap_ci_not_a_difference_of_point_estimates():
    observations = _weighted_matches(n_matches=30, seed=13)
    matrix = yardstick_matrix(
        observations, [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES], {}, {}, draws=20, seed=0
    )
    cell = matrix["forward_rounds"]["current_impact"]
    lo, hi = cell["gap_ci"]
    assert lo <= cell["gap_over_kill_diff"] <= hi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "yardstick or baselines or fold_candidates or matrix" -v`
Expected: FAIL with `ImportError: cannot import name 'BASELINE_CANDIDATES'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py
from app.scoring.impact import FACTOR_WEIGHTS


@dataclass
class Candidate:
    """A weighting to be scored. Baselines share the shape of fitted
    weightings so every candidate goes through identical code."""

    name: str
    feature_names: list[str]
    weights: list[float]


# Reads the EXACT impact differential rather than rebuilding it from the four
# components. impact.py round()s kill_impact, death_impact and each component
# independently, so a reconstruction carries a couple of points of error per
# player-round -- across 10 players and ~21 rounds that is enough to move a
# close comparison against a fitted candidate.
CURRENT_IMPACT_CANDIDATE = Candidate(
    name="current_impact",
    feature_names=["impact_diff"],
    weights=[1.0],
)

# ONE kill baseline. kills and deaths as separate columns was the same
# column twice: kills_and_deaths.[1,-1] == kills - deaths algebraically, and
# deaths_A == kills_B in 99.1% of this DB's rounds.
BASELINE_CANDIDATES = [
    Candidate("kill_diff", BASELINE_KILL_DIFF, [1.0]),
    Candidate("damage_only", BASELINE_DAMAGE, [1.0]),
]


def candidate_from_constrained(name: str, weights: ConstrainedWeights) -> Candidate:
    return Candidate(
        name=name,
        feature_names=FEATURE_COMPONENTS,
        weights=[
            weights.damage_multiplier,
            weights.econ / FACTOR_WEIGHT_TOTAL,
            weights.time / FACTOR_WEIGHT_TOTAL,
            weights.swing / FACTOR_WEIGHT_TOTAL,
        ],
    )


def _score_of(observation, candidate: Candidate) -> float:
    return sum(
        weight * _feature_value(observation, name)
        for name, weight in zip(candidate.feature_names, candidate.weights)
    )


def yardstick_first_half(observations, candidate: Candidate):
    """Y1. One row per eligible match: candidate score summed over rounds
    1-12 versus the match result. Not split by side -- every first-half row
    is attack-first for team A, so the other subset is empty."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        first_half = [o for o in obs if o.round_number <= FIRST_HALF_ROUNDS]
        if len(first_half) != FIRST_HALF_ROUNDS or first_half[0].match_won_by_team_a is None:
            continue
        scores.append(sum(_score_of(o, candidate) for o in first_half))
        labels.append(1 if first_half[0].match_won_by_team_a else 0)
        mids.append(match_id)
    return scores, labels, mids


def yardstick_full_match(observations, candidate: Candidate):
    """Y2. Every round. Absolute discrimination is inflated because the
    features contain the outcome's own kills -- read only as the paired gap
    over kill_diff."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        if not obs or obs[0].match_won_by_team_a is None:
            continue
        scores.append(sum(_score_of(o, candidate) for o in obs))
        labels.append(1 if obs[0].match_won_by_team_a else 0)
        mids.append(match_id)
    return scores, labels, mids


def yardstick_forward_rounds(observations, candidate: Candidate):
    """Y3. Round N's score versus who won the majority of rounds N+2 onward
    within the same half. Skipping N+1 keeps the strongest post-round
    mediator out of the label."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_half: dict[int, list] = {}
        for o in obs:
            by_half.setdefault(_half_of(o.round_number), []).append(o)
        for half_obs in by_half.values():
            half_obs.sort(key=lambda o: o.round_number)
            for index, o in enumerate(half_obs):
                future = [f for f in half_obs[index + 2 :] if f.round_won_by_team_a is not None]
                if not future:
                    continue
                won = sum(1 for f in future if f.round_won_by_team_a)
                if won * 2 == len(future):
                    continue  # an exact split has no majority to predict
                scores.append(_score_of(o, candidate))
                labels.append(1 if won * 2 > len(future) else 0)
                mids.append(match_id)
    return scores, labels, mids


YARDSTICKS = {
    "first_half_to_match": yardstick_first_half,
    "full_match_to_match": yardstick_full_match,
    "forward_rounds": yardstick_forward_rounds,
}


def fold_candidates(
    observations, fold_results, name: str, context_builder=None
) -> dict[int, Candidate]:
    """One constrained weighting per outer fold, fitted on that fold's
    TRAINING matches only. The matrix then applies each to its own test
    matches, so a fitted candidate is never scored on data it saw.

    `context_builder` is required for a WPA config, whose target depends on
    a value model; it is built from the same training observations, so the
    leverage weights never see a test match either.

    Returns (candidates_by_fold, weights_by_fold). The weights are returned,
    not discarded, because "do T1 and T2 agree on the weighting?" is one of
    the questions this whole project exists to answer.
    """
    by_match = group_by_match(observations)
    out: dict[int, Candidate] = {}
    fold_weights: dict[int, ConstrainedWeights] = {}
    for fold in fold_results:
        train_obs = [o for mid in fold.train_match_ids for o in by_match.get(mid, [])]
        context = context_builder(train_obs) if context_builder is not None else None
        # Controls are DERIVED from the target, and L2 is chosen for the
        # controlled design inside fit_constrained_weights -- the outer fold's
        # L2 belongs to a different (feature-only, uncontrolled) model.
        weights = fit_constrained_weights(
            train_obs, fold.config, controls_for(fold.config), context=context
        )
        out[fold.fold] = candidate_from_constrained(name, weights)
        fold_weights[fold.fold] = weights
    return out, fold_weights


def _cell(scores, labels, mids, draws, seed, baseline_fn=None, probs=None):
    """One matrix cell.

    `probs` may be supplied pre-calibrated. That matters for FITTED
    candidates: their pooled scores come from several different per-fold
    models, so calibrating with folds drawn over those pooled scores can put
    a score in the calibration-test set whose own model was trained on the
    match being used to calibrate. The caller therefore calibrates inside
    each outer fold instead (train-match scores -> test-match probabilities)
    and passes the result in.

    When `probs` is None the pooled calibration below is used. That is safe
    only for FIXED candidates -- current_impact and the baselines were never
    fitted to this data at all, so no model saw any of it.
    """
    if not scores:
        return None
    groups: dict[int, list] = {}
    for s, l, m in zip(scores, labels, mids):
        groups.setdefault(int(m), []).append((s, l))

    def auc_of(sample):
        flat = [pair for rows in sample for pair in rows]
        return auc([p[0] for p in flat], [p[1] for p in flat])

    lo, hi = cluster_bootstrap_ci(auc_of, groups, draws=draws, seed=seed)

    scores_arr = np.array(scores, dtype=float)
    labels_arr = np.array(labels, dtype=int)
    if probs is None:
        folds = assign_folds(mids, n_folds=5, seed=seed)
        fold_of = np.array([folds[int(m)] for m in mids])
        probs = np.zeros(len(scores_arr))
        for fold in range(5):
            test = fold_of == fold
            if not test.any():
                continue
            train = ~test
            if not train.any() or len(np.unique(labels_arr[train])) < 2:
                probs[test] = labels_arr[train].mean() if train.any() else 0.5
                continue
            probs[test] = apply_calibration(
                platt_calibrate(scores_arr[train], labels_arr[train]), scores_arr[test]
            )
    else:
        probs = np.asarray(probs, dtype=float)

    prob_groups: dict[int, list] = {}
    for pr, l, m in zip(probs, labels_arr, mids):
        prob_groups.setdefault(int(m), []).append((pr, l))

    def loss_of(sample):
        flat = [pair for rows in sample for pair in rows]
        return weighted_log_loss([p[0] for p in flat], [p[1] for p in flat])

    loss_lo, loss_hi = cluster_bootstrap_ci(loss_of, prob_groups, draws=draws, seed=seed)

    cell = {
        "auc": auc(scores, labels),
        "auc_ci": [lo, hi],
        "log_loss": weighted_log_loss(probs, labels_arr),
        "log_loss_ci": [loss_lo, loss_hi],
        "n": len(labels),
        "matches": len(groups),
    }

    if baseline_fn is not None:
        baseline_scores, baseline_labels, baseline_mids = baseline_fn()
        paired: dict[int, list] = {}
        by_match_candidate: dict[int, list] = {}
        for s, l, m in zip(scores, labels, mids):
            by_match_candidate.setdefault(int(m), []).append((s, l))
        for s, l, m in zip(baseline_scores, baseline_labels, baseline_mids):
            paired.setdefault(int(m), []).append((s, l))
        shared = sorted(set(by_match_candidate) & set(paired))
        combined = {m: (by_match_candidate[m], paired[m]) for m in shared}

        def cand_auc(sample):
            flat = [p for pair in sample for p in pair[0]]
            return auc([p[0] for p in flat], [p[1] for p in flat])

        def base_auc(sample):
            flat = [p for pair in sample for p in pair[1]]
            return auc([p[0] for p in flat], [p[1] for p in flat])

        gap_lo, gap_hi = paired_bootstrap_delta(cand_auc, base_auc, combined, draws=draws, seed=seed)

        # BOTH point estimates on exactly the shared rows the bootstrap used.
        # Taking the candidate's AUC over all its rows and the baseline's over
        # all of ITS rows would compare two different populations, and the
        # resulting point would not sit inside its own interval.
        shared_rows = [combined[m] for m in shared]
        cell["gap_over_kill_diff"] = cand_auc(shared_rows) - base_auc(shared_rows)
        cell["gap_ci"] = [gap_lo, gap_hi]
        cell["auc_on_shared_rows"] = cand_auc(shared_rows)
        cell["baseline_auc_on_shared_rows"] = base_auc(shared_rows)

    return cell


def yardstick_matrix(
    observations, fixed_candidates, per_fold_candidates: dict, folds: dict,
    draws: int = 200, seed: int = 0,
) -> dict:
    """Every candidate x every yardstick.

    `fixed_candidates` are weightings that were never fitted to this data
    (current_impact, baselines) and are scored on all matches.
    `per_fold_candidates` maps a name -> {fold index: Candidate}; each is
    scored ONLY on that fold's test matches, then pooled, so a fitted
    weighting is never evaluated on matches it was fitted on.
    """
    by_match = group_by_match(observations)
    matrix: dict[str, dict] = {}

    for yardstick_name, fn in YARDSTICKS.items():
        matrix[yardstick_name] = {}
        kill_baseline = next(c for c in BASELINE_CANDIDATES if c.name == "kill_diff")

        def baseline_fn(fn=fn):
            return fn(observations, kill_baseline)

        for candidate in fixed_candidates:
            scores, labels, mids = fn(observations, candidate)
            matrix[yardstick_name][candidate.name] = _cell(
                scores, labels, mids, draws, seed,
                baseline_fn=None if candidate.name == "kill_diff" else baseline_fn,
            )

        for name, per_fold in per_fold_candidates.items():
            scores, labels, mids, probs = [], [], [], []
            for fold_index, candidate in per_fold.items():
                fold = folds.get(fold_index)
                if fold is None:
                    continue
                test_obs = [o for mid in fold.test_match_ids for o in by_match.get(mid, [])]
                s, l, m = fn(test_obs, candidate)
                if not s:
                    continue

                # Calibration is fitted INSIDE the fold, on this fold's
                # training matches under this fold's own candidate, then
                # applied once to its test matches. Calibrating over the
                # pooled scores instead would mix models across folds.
                train_obs = [o for mid in fold.train_match_ids for o in by_match.get(mid, [])]
                ts, tl, _ = fn(train_obs, candidate)
                if ts and len(set(tl)) >= 2:
                    fold_probs = apply_calibration(platt_calibrate(ts, tl), s)
                else:
                    fold_probs = np.full(len(s), float(np.mean(tl)) if tl else 0.5)

                scores.extend(s)
                labels.extend(l)
                mids.extend(m)
                probs.extend(np.asarray(fold_probs, dtype=float).tolist())

            matrix[yardstick_name][name] = _cell(
                scores, labels, mids, draws, seed,
                baseline_fn=baseline_fn, probs=probs or None,
            )

    return matrix
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 50 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add three yardsticks with per-fold candidates and paired gap CIs

Fitted weightings are now fitted per outer fold and scored only on that
fold's held-out matches. The single kill baseline replaces the duplicate
kills/deaths pair."
```

---

### Task 15: Loaders and the CLI

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Create: `webapp/scripts/evaluate_impact.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: everything from Tasks 5-14.
- Produces: `load_all_observations(db, use_realized_swing=False) -> list[RoundObservation]`; `load_stored_observations(db) -> list[RoundObservation]`; `load_player_matches(db) -> list[PlayerMatch]`; CLI entry point.

**Two loaders, deliberately.** `load_all_observations` replays every match through the scorer to get **ex-ante** components — correct for fitting, but minutes of work. `load_stored_observations` reads the `impact_scores` columns directly, giving the **realized** components with no replay: that is exactly what Stage 0 wants ("Impact as it ships today") and keeps `--stage0-only` fast.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
def test_loaders_use_the_shared_surrender_predicate():
    """Guards the constraint rather than the DB: loaders must reference the
    shared predicate, not hand-roll a filter that can drift."""
    import inspect

    for fn in (impact_eval.load_all_observations, impact_eval.load_stored_observations,
               impact_eval.load_player_matches):
        assert "NOT_A_SURRENDER_ROUND" in inspect.getsource(fn), fn.__name__


def test_ex_ante_loader_defaults_to_ex_ante():
    import inspect

    signature = inspect.signature(impact_eval.load_all_observations)
    assert signature.parameters["use_realized_swing"].default is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k loader -v`
Expected: FAIL with `AttributeError: module 'app.services.impact_eval' has no attribute 'load_all_observations'`

- [ ] **Step 3: Write the loaders**

```python
# append to webapp/app/services/impact_eval.py
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.models import ImpactScore, Match, MatchPlayer, Round
from app.scoring.impact import build_impact_rows_for_match
from app.services.impact_stage0 import PlayerMatch
from app.services.surrender_rounds import NOT_A_SURRENDER_ROUND


def _match_ids(db) -> list[int]:
    return [
        mid
        for (mid,) in db.query(Match.id)
        .join(Round, Round.match_id == Match.id)
        .filter(NOT_A_SURRENDER_ROUND)
        .distinct()
        .all()
    ]


def _hydrated_match(db, match_id):
    return (
        db.query(Match)
        .options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.player_stats),
        )
        .filter(Match.id == match_id)
        .one()
    )


def load_all_observations(db, use_realized_swing: bool = False, report: dict | None = None):
    """Replays every match through the scorer, so components are the
    EX-ANTE variant by default -- the only variant eligible for
    forward-looking fitting. Costs a full replay (minutes).

    A match whose rounds lack impact rows is EXCLUDED and counted, never
    silently turned into zero-impact observations. Pass `report` to receive
    the exclusion count; the CLI prints it.
    """
    observations: list[RoundObservation] = []
    excluded: list[int] = []
    for match_id in _match_ids(db):
        match = _hydrated_match(db, match_id)
        rows = build_impact_rows_for_match(db, match_id, use_realized_swing=use_realized_swing)
        try:
            observations.extend(build_observations_for_match(match, rows))
        except MissingImpactRows:
            excluded.append(match_id)
    if report is not None:
        report["excluded_matches"] = len(excluded)
        report["excluded_match_ids"] = excluded[:20]
    return observations


def load_stored_observations(db, report: dict | None = None) -> list[RoundObservation]:
    """Reads stored impact_scores directly -- the REALIZED components, as
    the live scorer wrote them. No replay, so Stage 0 and the realized
    yardstick pass are fast. Never use these for a forward-looking fit.

    Exclusions are counted separately from the ex-ante loader's: a match can
    be scored but incompletely, and "how much data did we actually have"
    differs between the two passes."""
    observations: list[RoundObservation] = []
    excluded: list[int] = []
    for match_id in _match_ids(db):
        match = _hydrated_match(db, match_id)
        stored = (
            db.query(ImpactScore)
            .join(Round, Round.id == ImpactScore.round_id)
            .filter(Round.match_id == match_id, NOT_A_SURRENDER_ROUND)
            .all()
        )
        try:
            observations.extend(build_observations_for_match(match, stored))
        except MissingImpactRows:
            excluded.append(match_id)
    if report is not None:
        report["excluded_matches"] = len(excluded)
        report["excluded_match_ids"] = excluded[:20]
    return observations


def load_player_matches(db) -> list[PlayerMatch]:
    """One row per (player, match): their average STORED Impact across the
    match, and whether their team won. Stage 0 describes the shipped
    metric, so stored scores are correct here."""
    rows = (
        db.query(
            MatchPlayer.player_id,
            MatchPlayer.match_id,
            MatchPlayer.team,
            func.avg(ImpactScore.impact).label("avg_impact"),
            Match.team1_rounds_won,
            Match.team2_rounds_won,
        )
        .join(ImpactScore, ImpactScore.match_player_id == MatchPlayer.id)
        .join(Round, Round.id == ImpactScore.round_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(NOT_A_SURRENDER_ROUND)
        .group_by(
            MatchPlayer.player_id, MatchPlayer.match_id, MatchPlayer.team,
            Match.team1_rounds_won, Match.team2_rounds_won,
        )
        .all()
    )

    out: list[PlayerMatch] = []
    for player_id, match_id, team, avg_impact, won1, won2 in rows:
        if won1 == won2:
            continue  # ties excluded from every denominator
        team_value = team.value if hasattr(team, "value") else team
        team_a_won = won1 > won2
        out.append(
            PlayerMatch(
                player_id=player_id,
                match_id=match_id,
                avg_impact=float(avg_impact),
                won=team_a_won if team_value == Team.TEAM_1.value else not team_a_won,
            )
        )
    return out
```

**Note:** `build_observations_for_match` reads `.round_id`, `.match_player_id` and the four component attributes, all of which `ImpactScore` exposes under the same names as `CalculatedImpact` — so `load_stored_observations` can pass ORM rows straight through with no adapter.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 52 tests

- [ ] **Step 5: Write the CLI**

```python
# webapp/scripts/evaluate_impact.py
"""Impact-vs-winning evaluation report.

Debug-only CLI, not exposed on any page. Follows scripts/validate_fight_ev.py's
conventions: prints a summary, optionally writes JSON, honours a DATABASE_URL
override.

Usage:
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --stage0-only
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --out report.json
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --include-realized --draws 500

Point at a specific database the same way every other script here does:
    $env:DATABASE_URL = (Get-Content .env.remote | Select-String DATABASE_URL).Line.Split('=',2)[1]
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    BASELINE_DAMAGE,
    CONTROLS_CONTEXT,
    CONTROLS_RESULT,
    CURRENT_IMPACT_CANDIDATE,
    FEATURE_COMPONENTS,
    PRIMARY_T1,
    PRIMARY_T2,
    T2_SENSITIVITY_GRID,
    coefficient_diagnostics,
    controls_for,
    cross_validate,
    fit_constrained_weights,
    fold_candidates,
    load_all_observations,
    load_player_matches,
    load_stored_observations,
    oof_metrics,
    paired_oof_log_loss_delta,
    yardstick_matrix,
)
from app.services.impact_stage0 import stage0_report
from app.services.site_stats import resolve_roster_player_ids

L2_GRID = [0.01, 0.1, 1.0, 10.0]

# The control ladder. The 3 -> 4 step is the headline: the only number showing
# the components carry information beyond who won the round and what the teams
# could afford next. Every step predicts the SAME frozen target, so their
# losses are directly comparable and the delta can be bootstrapped paired.
LADDER = [
    ("1_round_result", CONTROLS_RESULT),
    ("2_plus_context", CONTROLS_RESULT + CONTROLS_CONTEXT),
    ("3_plus_damage", CONTROLS_RESULT + CONTROLS_CONTEXT + BASELINE_DAMAGE),
    ("4_plus_components", CONTROLS_RESULT + CONTROLS_CONTEXT + FEATURE_COMPONENTS),
]

# STAGE A ONLY. Stage B (the WPA target, the value model and the economy
# increment) is added by Task 17, after this report has been produced and read
# -- that ordering is the spec's gate, and it is also what keeps this file
# runnable at this commit: win_probability.py does not exist yet.


def _control_ladder(observations, config, draws, seed):
    """Every step run through the SAME outer folds on the SAME frozen target,
    with its L2 selected inside each fold's training half. The headline 3 -> 4
    step gets a PAIRED interval, because a point estimate cannot tell you
    whether the components added anything."""
    runs = {name: cross_validate(observations, [config], features, L2_GRID, seed=seed)
            for name, features in LADDER}
    out = {name: oof_metrics(run["oof"], draws=draws, seed=seed) for name, run in runs.items()}

    point, lo, hi = paired_oof_log_loss_delta(
        runs["4_plus_components"]["oof"], runs["3_plus_damage"]["oof"], draws=draws, seed=seed
    )
    out["headline"] = {
        "metric": "weighted log loss, components minus damage-only",
        "delta": point,
        "delta_ci": [lo, hi],
        "reading": (
            "negative delta = adding the components IMPROVED held-out prediction. "
            "An interval spanning zero means they added nothing measurable."
        ),
    }
    return out


def _weights_summary(fold_weights: dict) -> dict:
    """Per-fold constrained weights, serialized rather than discarded --
    'do T1 and T2 agree on the weighting?' is unanswerable without them."""
    usable = [w for w in fold_weights.values() if w.usable]
    summary = {
        "per_fold": {str(k): vars(v) for k, v in sorted(fold_weights.items())},
        "usable_folds": len(usable),
        "total_folds": len(fold_weights),
    }
    if usable:
        for field in ("damage_multiplier", "econ", "time", "swing"):
            values = [getattr(w, field) for w in usable]
            summary.setdefault("across_folds", {})[field] = {
                "median": float(np.median(values)),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            }
    return summary


def _proposal(observations, config, label: str) -> dict:
    """An all-data fit: the weighting to CONSIDER adopting, never an estimate
    of its own performance."""
    weights = fit_constrained_weights(observations, config, controls_for(config))
    return {
        "target": label,
        "frozen_config": vars(config),
        "controls": controls_for(config),
        "weights": vars(weights),
        "usable": weights.usable,
        "units": (
            "econ/time/swing map directly onto FACTOR_WEIGHTS. damage_multiplier "
            "multiplies the STORED damage component, which impact.py already computed "
            "as round(damage_and_assists * 1.25) -- so a proposed d means changing that "
            "1.25 to 1.25 * d, NOT setting the raw multiplier to d."
        ),
        "warning": (
            "Fitted on ALL matches. NOT an unbiased estimate of its own performance -- "
            "read the per-fold fitted_* rows of the yardstick matrix for that."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    parser.add_argument("--draws", type=int, default=200, help="bootstrap draws (default 200)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stage0-only", action="store_true", help="skip all fitting")
    parser.add_argument("--sensitivity", action="store_true",
                        help="also run the T2 grid, compared ONLY on the fixed yardsticks")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        roster = set(resolve_roster_player_ids(db))
        report = {
            "stage0": stage0_report(
                load_player_matches(db), roster, draws=args.draws, seed=args.seed
            )
        }

        stored_report = {}
        stored = load_stored_observations(db, report=stored_report)
        report["stage0"]["loading_realized"] = stored_report
        report["stage0"]["match_level_diagnostics"] = yardstick_matrix(
            stored, [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES], {}, {},
            draws=args.draws, seed=args.seed,
        )
        print("== Stage 0: Impact as it ships today (realized components) ==")
        print(json.dumps(report["stage0"], indent=2, default=float))

        if args.stage0_only:
            if args.out:
                args.out.write_text(json.dumps(report, indent=2, default=float))
                print(f"\nwrote {args.out}")
            return 0

        load_report = {}
        observations = load_all_observations(db, use_realized_swing=False, report=load_report)
        report["loading_ex_ante"] = {"n_observations": len(observations), **load_report}
        report["component_variant"] = "ex_ante"

        # --- The frozen Stage A targets, each nested end to end ---
        per_fold_candidates = {}
        all_folds = {}
        for name, config in (("T1", PRIMARY_T1), ("T2", PRIMARY_T2)):
            result = cross_validate(
                observations, [config], FEATURE_COMPONENTS, L2_GRID, seed=args.seed
            )
            candidates, fold_weights = fold_candidates(
                observations, result["folds"], f"fitted_{name}"
            )
            per_fold_candidates[f"fitted_{name}"] = candidates
            all_folds.update({f.fold: f for f in result["folds"]})
            report[name] = {
                "frozen_config": vars(config),
                "controls": controls_for(config),
                "metrics": oof_metrics(result["oof"], draws=args.draws, seed=args.seed),
                "selected_l2_per_fold": [{"fold": f.fold, "l2": f.l2} for f in result["folds"]],
                "unconstrained_fold_coefficients": {
                    "feature_order": ["intercept"] + FEATURE_COMPONENTS,
                    "per_fold": [f.beta_raw.tolist() for f in result["folds"]],
                },
                "constrained_weights": _weights_summary(fold_weights),
            }

        report["T2_control_ladder"] = {
            "config": vars(PRIMARY_T2),
            **_control_ladder(observations, PRIMARY_T2, args.draws, args.seed),
        }

        fixed = [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES]
        report["yardstick_matrix_ex_ante"] = yardstick_matrix(
            observations, fixed, per_fold_candidates, all_folds, draws=args.draws, seed=args.seed
        )
        report["yardstick_matrix_realized"] = yardstick_matrix(
            stored, fixed, per_fold_candidates, all_folds, draws=args.draws, seed=args.seed
        )
        report["realized_note"] = (
            "Fitted candidates evaluated on realized components too: adopting weights "
            "fitted on ex_ante applies them to a scorer that still computes realized, "
            "so this row is what adoption would actually buy."
        )

        report["diagnostics_T2"] = coefficient_diagnostics(
            observations, PRIMARY_T2, FEATURE_COMPONENTS, draws=args.draws, seed=args.seed
        )

        if args.sensitivity:
            sensitivity = []
            for config in T2_SENSITIVITY_GRID:
                result = cross_validate(
                    observations, [config], FEATURE_COMPONENTS, L2_GRID, seed=args.seed
                )
                candidates, _ = fold_candidates(observations, result["folds"], "sens")
                folds = {f.fold: f for f in result["folds"]}
                cell = yardstick_matrix(
                    observations, [], {"sens": candidates}, folds, draws=50, seed=args.seed
                )["forward_rounds"]["sens"]
                sensitivity.append({"config": vars(config), "forward_rounds": cell})
            report["T2_sensitivity"] = {
                "note": (
                    "Compared on the FIXED forward-rounds yardstick, never on each "
                    "target's own loss -- those losses measure different outcomes."
                ),
                "runs": sensitivity,
            }

        # One all-data proposal PER TARGET, so the agreement question is answerable.
        report["deployment_proposals"] = {
            "T1": _proposal(observations, PRIMARY_T1, "T1"),
            "T2": _proposal(observations, PRIMARY_T2, "T2"),
            "note": (
                "Compare these against each other and against the per-fold spreads in "
                "T1/T2.constrained_weights. Disagreement is a finding to report, not a "
                "tie to break."
            ),
        }

        print("\n== T2 control ladder (headline: 3 -> 4) ==")
        print(json.dumps(report["T2_control_ladder"], indent=2, default=float))
        print("\n== Targets x yardsticks (ex-ante) ==")
        print(json.dumps(report["yardstick_matrix_ex_ante"], indent=2, default=float))
        print("\n== Deployment proposals ==")
        print(json.dumps(report["deployment_proposals"], indent=2, default=float))

        if args.out:
            args.out.write_text(json.dumps(report, indent=2, default=float))
            print(f"\nwrote {args.out}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the CLI end to end**

Run: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --stage0-only`
Expected: prints Stage 0 with non-zero `n`, both cohorts, per-player correlation distribution, and CIs on the headline numbers. Fast (no replay).

Then: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --out ..\scratch-impact-report.json`
Expected: completes and writes JSON. Replays every match, so expect minutes.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/scripts/evaluate_impact.py webapp/tests/test_impact_eval.py
git commit -m "Add loaders and the evaluate_impact CLI"
```

---

### Task 16: Stage B — the cross-fit `V(state)` model

**GATE: do not start Tasks 16-17 until Task 15's report has been produced and read.** Stage A's component breakdown may change what the state should condition on. If Stage A shows one component doing all the work, revisit this task's feature set first.

**Files:**
- Create: `webapp/app/services/win_probability.py`
- Test: `webapp/tests/test_win_probability.py`

**Interfaces:**
- Consumes: `fit_logistic`, `predict_proba`, `weighted_log_loss` (Tasks 1-2); `RoundObservation`, `_half_of` (Tasks 7-8); `attacking_team_for_round`.
- Produces: `StateFeatures` dataclass (`score_diff`, `rounds_played`, `attacking_is_team_a`, `is_terminal`, `terminal_result`, `econ_known`); `ValueModel` dataclass (`beta`, `centre`, `scale`, `include_econ`); `state_before(obs)`; `state_after(obs)`; `fit_value_model(observations, l2=1.0, include_econ=False) -> ValueModel`; `value_of(model, state)`; `econ_increment_report(observations, n_folds=5, seed=0)`.

**Three shape fixes over a naive value model:**

1. **A `score_diff × rounds_played` interaction.** A two-round lead at round 3 and at round 22 are not remotely the same state; an additive model cannot express that, and leverage is exactly what this model exists to measure.
2. **`state_after` uses the side of the *next* round, not the current one.** Sides swap at 12→13 and alternate every round in overtime (`attacking_team_for_round`), so carrying the current round's side into the after-state is wrong precisely at the boundaries.
3. **Terminal rounds pin `V(after)` to exactly 1.0 or 0.0.** The match is over; a model extrapolation there is not a probability of anything.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_win_probability.py
"""V(state) = P(team A wins the match | state).

Stage B uses this for leverage-weighted ATTRIBUTION, not independent
validation: dV is dominated by the round's own outcome, which the features
nearly determine."""

import numpy as np

from app.services.impact_eval import RoundObservation
from app.services.win_probability import (
    econ_increment_report,
    fit_value_model,
    state_after,
    state_before,
    value_of,
)


def _obs(round_number, score_diff_before, won_by_a, match_won, terminal=False, match_id=1):
    return RoundObservation(
        match_id=match_id, round_id=round_number, round_number=round_number,
        damage=0.0, econ_impact=0.0, time_impact=0.0, swing_impact=0.0,
        impact_diff=0.0, kill_diff=0.0,
        score_diff_before=score_diff_before, attacking_is_team_a=True,
        loadout_diff=0.0, full_buy_count_diff=0,
        round_won_by_team_a=won_by_a, match_won_by_team_a=match_won, is_terminal=terminal,
    )


def test_state_before_excludes_the_round_result():
    o = _obs(5, 2, True, True)
    assert state_before(o).score_diff == 2
    assert state_before(o).rounds_played == 4


def test_state_after_includes_the_round_result():
    assert state_after(_obs(5, 2, True, True)).score_diff == 3
    assert state_after(_obs(5, 2, False, True)).score_diff == 1


def test_state_after_unresolved_round_leaves_score_unchanged():
    assert state_after(_obs(5, 2, None, True)).score_diff == 2


def test_state_after_uses_the_next_rounds_side_at_halftime():
    """Round 12 -> 13 is a side swap. The after-state belongs to round 13."""
    after_twelve = state_after(_obs(12, 0, True, True))
    assert after_twelve.attacking_is_team_a is False
    assert state_before(_obs(12, 0, True, True)).attacking_is_team_a is True


def test_state_after_terminal_round_is_pinned_to_the_result():
    won = state_after(_obs(21, 5, True, True, terminal=True))
    lost = state_after(_obs(21, -5, False, False, terminal=True))
    assert won.is_terminal and won.terminal_result == 1.0
    assert lost.is_terminal and lost.terminal_result == 0.0


def test_value_of_returns_exactly_one_or_zero_for_terminal_states():
    observations = [_obs(10, i % 5 - 2, True, i % 2 == 0, match_id=i) for i in range(60)]
    model = fit_value_model(observations)
    assert value_of(model, state_after(_obs(21, 5, True, True, terminal=True))) == 1.0
    assert value_of(model, state_after(_obs(21, -5, False, False, terminal=True))) == 0.0


def test_value_model_carries_its_training_scaling():
    """Raw columns differ by orders of magnitude; the model must store the
    statistics it was fitted under and apply them to test states."""
    observations = [_obs(10, i % 5 - 2, True, i % 2 == 0, match_id=i) for i in range(60)]
    model = fit_value_model(observations)
    assert model.centre.shape == model.scale.shape
    assert len(model.beta) == len(model.centre) + 1
    assert np.all(model.scale > 0)


def test_econ_increment_reports_a_paired_interval():
    rng = np.random.default_rng(3)
    observations = []
    for match_id in range(120):
        o = _obs(10, int(rng.integers(-3, 4)), True, rng.random() < 0.5, match_id=match_id)
        o.loadout_diff = rng.normal() * 1000
        o.full_buy_count_diff = int(rng.integers(-2, 3))
        observations.append(o)
    report = econ_increment_report(observations, seed=0)
    lo, hi = report["delta_ci"]
    assert lo <= report["delta"] <= hi


def test_value_model_learns_that_a_lead_is_good():
    observations = []
    for match_id in range(200):
        leading = match_id % 2 == 0
        observations.append(_obs(10, 5 if leading else -5, True, leading, match_id=match_id))
    model = fit_value_model(observations)
    ahead = value_of(model, state_before(_obs(10, 5, True, True)))
    behind = value_of(model, state_before(_obs(10, -5, True, False)))
    assert ahead > behind
    assert 0.0 <= ahead <= 1.0


def test_value_model_uses_a_score_by_progress_interaction():
    """A two-round lead late must be worth more than the same lead early.
    An additive model cannot express this."""
    observations = []
    rng = np.random.default_rng(0)
    for match_id in range(400):
        rounds_played = int(rng.integers(2, 20))
        diff = int(rng.integers(-4, 5))
        # Later leads convert far more reliably.
        p = 0.5 + 0.02 * diff * rounds_played
        observations.append(
            _obs(rounds_played + 1, diff, True, rng.random() < np.clip(p, 0.02, 0.98), match_id=match_id)
        )
    model = fit_value_model(observations)
    early = value_of(model, state_before(_obs(4, 2, True, True)))
    late = value_of(model, state_before(_obs(20, 2, True, True)))
    assert late > early


def test_after_state_refuses_econ_aware_evaluation():
    """Round N+1's pre-buy economy is not extracted, so an econ-aware
    V(after) would silently reuse round N's -- flattering econ. It must
    raise instead."""
    import pytest

    observations = [_obs(10, 1, True, True, match_id=i) for i in range(40)]
    for i, o in enumerate(observations):
        o.match_won_by_team_a = i % 2 == 0
    econ_model = fit_value_model(observations, include_econ=True)
    with pytest.raises(ValueError, match="after-state"):
        value_of(econ_model, state_after(_obs(5, 1, True, True)))


def test_econ_increment_report_measures_the_delta():
    observations = []
    rng = np.random.default_rng(1)
    for match_id in range(120):
        o = _obs(10, int(rng.integers(-3, 4)), True, rng.random() < 0.5, match_id=match_id)
        o.loadout_diff = rng.normal() * 1000
        o.full_buy_count_diff = int(rng.integers(-2, 3))
        observations.append(o)
    report = econ_increment_report(observations, seed=0)
    assert "base_log_loss" in report and "with_econ_log_loss" in report
    assert "delta" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_win_probability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.win_probability'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/win_probability.py
"""V(state) = P(team A wins the match | state), used by Stage B to weight
rounds by leverage.

FRAMING (see the spec): Stage B DEFINES an impact measure; it is not
independent predictive validation. V(after) - V(before) is dominated by
the round's own outcome, which the round's own kills nearly determine, so
it does not escape the tautology. Its value is that a swing in a close,
late, economically pivotal round is worth more than the same swing in a
decided one.

CROSS-FITTING IS MANDATORY. fit_value_model must be called on TRAINING
observations only, inside each outer fold. A model fitted once on every
match encodes evaluation-match outcomes into the leverage weights, and
calling the downstream fit out-of-fold does not undo that.
"""

from dataclasses import dataclass

import numpy as np

from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round
from app.services.stats_math import (
    cluster_bootstrap_ci,
    fit_logistic,
    paired_bootstrap_delta,
    predict_proba,
    standardize,
    weighted_log_loss,
)

ECON_FEATURES = ("loadout_diff", "full_buy_count_diff")


@dataclass
class StateFeatures:
    score_diff: int
    rounds_played: int
    attacking_is_team_a: bool
    is_terminal: bool = False
    terminal_result: float | None = None
    loadout_diff: float = 0.0
    full_buy_count_diff: float = 0.0
    # False for an after-state: round N+1's pre-buy economy is not something
    # this project extracts, and reusing round N's would be a silent lie.
    econ_known: bool = True


def state_before(observation) -> StateFeatures:
    return StateFeatures(
        score_diff=observation.score_diff_before,
        rounds_played=observation.round_number - 1,
        attacking_is_team_a=observation.attacking_is_team_a,
        loadout_diff=observation.loadout_diff,
        full_buy_count_diff=observation.full_buy_count_diff,
    )


def state_after(observation) -> StateFeatures:
    """The state entering the NEXT round.

    Side comes from round N+1, not N: sides swap at the 12->13 boundary and
    alternate every round in overtime, so reusing the current round's side
    is wrong exactly where leverage matters most.

    A terminal round's after-state is the finished match, pinned to the
    actual result rather than extrapolated.
    """
    delta = 0
    if observation.round_won_by_team_a is True:
        delta = 1
    elif observation.round_won_by_team_a is False:
        delta = -1

    if observation.is_terminal:
        result = None
        if observation.match_won_by_team_a is not None:
            result = 1.0 if observation.match_won_by_team_a else 0.0
        return StateFeatures(
            score_diff=observation.score_diff_before + delta,
            rounds_played=observation.round_number,
            attacking_is_team_a=observation.attacking_is_team_a,
            is_terminal=True,
            terminal_result=result,
        )

    next_round = observation.round_number + 1
    # Economy is deliberately NOT carried into the after-state. The
    # observation's loadout is round N's PRE-BUY state; round N+1's economy
    # is a different quantity that this project never extracts. Copying N's
    # economy forward would make an econ-aware V(after) quietly wrong -- and
    # wrong in the direction that flatters econ, since it would look like the
    # economy had not changed. `econ_known=False` makes value_of refuse
    # rather than guess.
    return StateFeatures(
        score_diff=observation.score_diff_before + delta,
        rounds_played=observation.round_number,
        attacking_is_team_a=attacking_team_for_round(next_round) == Team.TEAM_1,
        econ_known=False,
    )


def _design_row(state: StateFeatures, include_econ: bool) -> list[float]:
    row = [
        float(state.score_diff),
        float(state.rounds_played),
        # The interaction is the point: a two-round lead at round 3 and at
        # round 22 are not the same state, and an additive model cannot say so.
        float(state.score_diff) * float(state.rounds_played),
        1.0 if state.attacking_is_team_a else 0.0,
    ]
    if include_econ:
        row.extend([float(state.loadout_diff), float(state.full_buy_count_diff)])
    return row


@dataclass
class ValueModel:
    """Coefficients PLUS the training centre/scale they were fitted under.

    Standardization is not cosmetic here: score_diff spans about +/-13,
    rounds_played 0-24, their interaction a few hundred, and loadout_diff tens
    of thousands. A single ridge penalty applied to raw columns would shrink
    those wildly unevenly -- and would make the econ-increment comparison
    unfair in exactly the direction that buries econ, since its columns are
    the largest and so the most penalised.
    """

    beta: np.ndarray
    centre: np.ndarray
    scale: np.ndarray
    include_econ: bool


def fit_value_model(observations, l2: float = 1.0, include_econ: bool = False) -> ValueModel:
    """MUST be called inside each outer training fold. Fitting once over all
    matches and then running outer CV leaks evaluation outcomes into the
    leverage weights."""
    rows, labels = [], []
    for o in observations:
        if o.match_won_by_team_a is None:
            continue
        rows.append(_design_row(state_before(o), include_econ))
        labels.append(1.0 if o.match_won_by_team_a else 0.0)
    width = 4 + (2 if include_econ else 0)
    if not rows or len(set(labels)) < 2:
        return ValueModel(np.zeros(width + 1), np.zeros(width), np.ones(width), include_econ)

    X = np.array(rows, dtype=float)
    scaled, _, centre, scale = standardize(X, X)
    beta = fit_logistic(scaled, np.array(labels), l2=l2)
    return ValueModel(beta, centre, scale, include_econ)


def value_of(model: ValueModel, state: StateFeatures) -> float:
    """Terminal states short-circuit: the match is decided, so its value is
    exactly 1 or 0, not a model extrapolation.

    Applies the model's OWN training centre/scale, so a test state is
    transformed exactly as the training states were.
    """
    if state.is_terminal and state.terminal_result is not None:
        return float(state.terminal_result)
    if model.include_econ and not state.econ_known:
        raise ValueError(
            "econ-aware V(state) cannot be evaluated on an after-state: round "
            "N+1's pre-buy economy is not extracted. Either use the base model "
            "for leverage, or extract genuine next-round economy first."
        )
    row = np.array([_design_row(state, model.include_econ)], dtype=float)
    return float(predict_proba(model.beta, (row - model.centre) / model.scale)[0])


def econ_increment_report(observations, n_folds: int = 5, seed: int = 0) -> dict:
    """The spec's measured econ step: held-out log loss WITHOUT econ state
    versus WITH it. The delta is the quantitative answer to 'how much does
    econ carryover actually matter'."""
    from app.services.impact_eval import assign_folds, split_observations

    folds = assign_folds([o.match_id for o in observations], n_folds=n_folds, seed=seed)

    def held_out(include_econ: bool):
        rows = []
        for fold in range(n_folds):
            train, test = split_observations(observations, folds, fold)
            if not train or not test:
                continue
            model = fit_value_model(train, include_econ=include_econ)
            for o in test:
                if o.match_won_by_team_a is None:
                    continue
                rows.append(
                    (o.match_id, value_of(model, state_before(o)),
                     1.0 if o.match_won_by_team_a else 0.0)
                )
        return rows

    base_rows = held_out(False)
    econ_rows = held_out(True)
    if not base_rows or not econ_rows:
        return {"base_log_loss": float("nan"), "with_econ_log_loss": float("nan"),
                "delta": float("nan"), "delta_ci": [float("nan"), float("nan")]}

    def loss(rows):
        return weighted_log_loss([r[1] for r in rows], [r[2] for r in rows])

    # Paired by match: both models are scored on the SAME resampled matches
    # each draw, so the interval is for the DIFFERENCE rather than being two
    # independent intervals the reader has to eyeball.
    combined: dict[int, tuple[list, list]] = {}
    for index, rows in ((0, base_rows), (1, econ_rows)):
        for match_id, prob, label in rows:
            combined.setdefault(int(match_id), ([], []))[index].append((prob, label))

    def side(index):
        def fn(sample):
            flat = [r for pair in sample for r in pair[index]]
            if not flat:
                return float("nan")
            return weighted_log_loss([r[0] for r in flat], [r[1] for r in flat])

        return fn

    lo, hi = paired_bootstrap_delta(side(0), side(1), combined, seed=seed)
    base, with_econ = loss(base_rows), loss(econ_rows)
    return {
        "base_log_loss": base,
        "with_econ_log_loss": with_econ,
        "delta": base - with_econ,
        "delta_ci": [lo, hi],
        "note": (
            "positive delta = adding econ state improved held-out prediction. "
            "An interval spanning zero means econ state added nothing measurable "
            "to the win-probability model."
        ),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_win_probability.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/win_probability.py webapp/tests/test_win_probability.py
git commit -m "Add V(state) with progress interaction, next-round side and terminal pinning"
```

---

### Task 17: Stage B — cross-fit leverage-weighted WPA target

**Files:**
- Modify: `webapp/app/services/impact_eval.py`, `webapp/scripts/evaluate_impact.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `fit_value_model`, `state_before`, `state_after`, `value_of` (Task 16).
- Produces: `wpa_target(observations, feature_names, context)`; `build_target` gains an optional `context` parameter; `cross_validate` gains an optional `context_builder` parameter.

**Formulation, corrected:** signed `dV` lies in [−1, 1] and is not a probability, so it cannot be the `y` of a logistic fit. Instead the **label is whether team A won the round** (0/1) and the **sample weight is `abs(dV)`**.

**How cross-fitting is enforced.** `cross_validate` gains a `context_builder(train_obs) -> dict` hook. Inside each outer fold the context is built from **training observations only**, then handed to `build_target` for *both* the training and test datasets. The value model therefore never sees a test match's outcome, while test rows still get leverage weights from a legitimate model. Without this hook there is no way to express "the target depends on a model fitted on the training half", which is why the previous plan simply fitted it on everything and leaked.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
def test_wpa_target_labels_are_round_outcomes_and_weights_are_leverage():
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    obs = _weighted_matches(n_matches=40, seed=21)
    beta = fit_value_model(obs)
    dataset = wpa_target(obs, FEATURE_COMPONENTS, {"value_beta": beta})

    assert set(np.unique(dataset.y)) <= {0.0, 1.0}, "labels must be round outcomes"
    assert np.all(dataset.w >= 0.0)
    assert np.all(dataset.w <= 1.0), "abs(dV) cannot exceed 1"


def test_wpa_target_skips_unresolved_rounds():
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    resolved = _obs(5, 1.0, True, True, match_id=1)
    unresolved = _obs(6, 1.0, None, True, match_id=1)
    beta = fit_value_model([resolved])
    dataset = wpa_target([resolved, unresolved], FEATURE_COMPONENTS, {"value_beta": beta})
    assert len(dataset.y) == 1


def test_training_rows_use_an_inner_oof_value_model():
    """Leverage for a training row must come from a model that did not see
    that row's match."""
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    obs = _weighted_matches(n_matches=30, seed=25)
    full = fit_value_model(obs)
    other = fit_value_model(obs[: len(obs) // 2])
    match_ids = {o.match_id for o in obs}
    context = {
        "value_beta": full,
        "value_beta_by_match": {mid: other for mid in match_ids},
    }
    with_inner = wpa_target(obs, FEATURE_COMPONENTS, context)
    without = wpa_target(obs, FEATURE_COMPONENTS, {"value_beta": full})
    assert not np.allclose(with_inner.w, without.w), "per-match betas must change leverage"


def test_context_builder_only_sees_training_matches(monkeypatch):
    """The Stage B leakage fix, asserted directly."""
    observations = _weighted_matches(n_matches=40, seed=22)
    seen = []

    def builder(train_obs):
        from app.services.win_probability import fit_value_model

        seen.append({o.match_id for o in train_obs})
        return {"value_beta": fit_value_model(train_obs)}

    result = cross_validate(
        observations, [TargetConfig(name="WPA")], FEATURE_COMPONENTS, [1.0],
        seed=0, context_builder=builder,
    )
    assert len(seen) == len(result["folds"])
    for fold, train_ids in zip(result["folds"], seen):
        assert train_ids.isdisjoint(fold.test_match_ids)


def test_build_target_passes_context_to_wpa():
    from app.services.win_probability import fit_value_model

    obs = _weighted_matches(n_matches=20, seed=23)
    beta = fit_value_model(obs)
    dataset = build_target(obs, TargetConfig(name="WPA"), FEATURE_COMPONENTS, {"value_beta": beta})
    assert len(dataset.y) > 0


def test_wpa_without_context_raises():
    obs = _weighted_matches(n_matches=10, seed=24)
    with pytest.raises(ValueError, match="context"):
        build_target(obs, TargetConfig(name="WPA"), FEATURE_COMPONENTS, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "wpa or context" -v`
Expected: FAIL with `ImportError: cannot import name 'wpa_target'`

- [ ] **Step 3: Write minimal implementation**

Add `wpa_target` and extend the two seams:

```python
# append to webapp/app/services/impact_eval.py
from app.services.win_probability import state_after, state_before, value_of


def wpa_target(observations, feature_names: list[str], context: dict) -> FitDataset:
    """Stage B: label = did team A win this round, weight = leverage.

    Signed dV is in [-1, 1] and is not a probability, so it cannot be the
    `y` of a logistic fit. Using abs(dV) as a SAMPLE WEIGHT instead makes
    the fit care more about high-leverage rounds without pretending a
    signed swing is a likelihood.

    `context["value_beta"]` MUST come from a model fitted on training
    observations only -- see cross_validate's context_builder.
    """
    if not context or "value_beta" not in context:
        raise ValueError("wpa_target requires a context carrying 'value_beta'")
    fallback_model = context["value_beta"]
    # Training rows get an INNER-OOF value model (one that did not see their
    # own match), so the leverage weights used to fit the component weights
    # are not this model's in-sample predictions of the very rows it was fit
    # on. Outer-test rows are absent from this map and fall back to the
    # full-training model, which never saw them either.
    per_match = context.get("value_beta_by_match", {})

    rows, ys, ws, mids = [], [], [], []
    for o in observations:
        if o.round_won_by_team_a is None:
            continue
        model = per_match.get(o.match_id, fallback_model)
        leverage = abs(value_of(model, state_after(o)) - value_of(model, state_before(o)))
        rows.append(_row(o, feature_names))
        ys.append(1.0 if o.round_won_by_team_a else 0.0)
        ws.append(leverage)
        mids.append(o.match_id)
    return _dataset(rows, ys, ws, mids, feature_names)
```

Change `build_target`'s signature to `(observations, config, feature_names, context=None)` and add the dispatch branch:

```python
    if config.name == "WPA":
        return wpa_target(observations, feature_names, context)
```

Change `cross_validate` to accept `context_builder=None` and thread it through — inside the outer fold loop, immediately after `_select_config`:

```python
        context = context_builder(train_obs) if context_builder is not None else None
        train_ds = build_target(train_obs, config, feature_names, context)
        test_ds = build_target(test_obs, config, feature_names, context)
```

and in `_select_config`'s inner loop, build a context from the inner-training observations the same way:

```python
                inner_context = context_builder(inner_train) if context_builder is not None else None
                train_ds = build_target(inner_train, config, feature_names, inner_context)
                test_ds = build_target(inner_test, config, feature_names, inner_context)
```

`_select_config` therefore also takes `context_builder` — added as a **keyword parameter defaulting to `None`**, after `seed`, so Task 10's positional call `_select_config(obs, configs, names, grid, 3, 0)` and its test keep working unchanged.

- [ ] **Step 4: Extend the CLI with Stage B**

Task 15 deliberately shipped a **Stage A only** CLI: `win_probability.py` did
not exist yet, and the spec's gate says Stage B waits until the Stage A report
has been read. Add Stage B now.

Extend the imports:

```python
from app.services.impact_eval import (
    ...,
    TargetConfig,
)
from app.services.win_probability import econ_increment_report, fit_value_model
```

Add the context builder next to `_control_ladder`:

```python
def _value_context(train_obs, seed: int = 0, inner_folds: int = 3):
    """Stage B's context: a value model fitted on the training half, PLUS
    inner-OOF value models so a training row's leverage does not come from a
    model that saw its own match."""
    from app.services.impact_eval import assign_folds, split_observations

    full = fit_value_model(train_obs)
    inner = assign_folds([o.match_id for o in train_obs], n_folds=inner_folds, seed=seed + 7)
    by_match = {}
    for fold in range(inner_folds):
        inner_train, inner_test = split_observations(train_obs, inner, fold)
        if not inner_train or not inner_test:
            continue
        model = fit_value_model(inner_train)
        for o in inner_test:
            by_match[o.match_id] = model
    return {"value_beta": full, "value_beta_by_match": by_match}
```

Then, in `main()`, change the Stage A target loop to include WPA and pass its
context builder:

```python
        targets = [
            ("T1", PRIMARY_T1, None),
            ("T2", PRIMARY_T2, None),
            ("WPA", TargetConfig(name="WPA"), lambda o: _value_context(o, args.seed)),
        ]
        for name, config, context_builder in targets:
            result = cross_validate(
                observations, [config], FEATURE_COMPONENTS, L2_GRID,
                seed=args.seed, context_builder=context_builder,
            )
            candidates, fold_weights = fold_candidates(
                observations, result["folds"], f"fitted_{name}",
                context_builder=context_builder,
            )
            ...  # body otherwise unchanged
```

and add, after the diagnostics block:

```python
        report["WPA"]["framing"] = (
            "attribution, not independent validation -- dV is dominated by the round's "
            "own outcome. Its yardstick-matrix row IS comparable to T1/T2, because every "
            "candidate is scored there on the same fixed binary labels."
        )
        report["econ_increment"] = econ_increment_report(observations, seed=args.seed)
```

**`fitted_WPA` must reach the matrix.** That is the entire reason Stage B is in
this plan rather than deferred: without it, T1, T2 and WPA are three models each
judged against its own target, which is no comparison at all.

- [ ] **Step 4b: Verify Stage B actually entered the comparison**

Run: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --out ..\scratch-impact-report.json`

Then:

```bash
.\.venv\Scripts\python.exe -c "import json,sys; r=json.load(open(sys.argv[1])); print(sorted(r['yardstick_matrix_ex_ante']['forward_rounds']))" ..\scratch-impact-report.json
```

Expected: `fitted_WPA` appears alongside `fitted_T1`, `fitted_T2`,
`current_impact`, `kill_diff` and `damage_only`. If it is missing, Stage B is
being compared to nothing and this task is not done.

- [ ] **Step 5: Run the full suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, full suite green.

- [ ] **Step 6: Run the full report**

Run: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --out ..\scratch-impact-report.json`
Expected: completes, printing Stage 0, the T2 control ladder, the yardstick matrix, the deployment proposal, and Stage B.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/scripts/evaluate_impact.py webapp/tests/test_impact_eval.py
git commit -m "Add cross-fit leverage-weighted WPA target for Stage B

cross_validate gains a context_builder hook so the value model is fitted
on training observations only. The previous design fitted V(state) on
every match and then called the downstream fit out-of-fold, which does not
remove the leakage."
```

---

## After the plan

Read the report before designing anything user-facing:

1. **Did the Task 0 gate pass?** If not, nothing downstream is meaningful.
2. **Read `T2_control_ladder.headline`** — the weighted-log-loss delta for step 3 → 4, *with its paired interval*. Negative means the components improved held-out prediction. **An interval spanning zero means they added nothing measurable**, and that is the headline finding whether or not it is the hoped-for one. Do not read the point estimate alone.
3. **Do `fitted_T1`, `fitted_T2` and `fitted_WPA` beat `current_impact` and `kill_diff` on the yardstick matrix?** Read `gap_ci`, not `gap_over_kill_diff` alone — a gap whose paired interval spans zero is not a difference. All three fitted candidates appear in the same matrix on the same labels; that comparison is the reason Stage B is in this plan at all.
4. **Do T1 and T2 agree on the constrained weights?** Disagreement is a finding to report, not a tie to break.
5. **Is any component's `sign_stability` below ~0.9?** Report it as indeterminate rather than as a result.
6. **Stage 0's `within_player_terciles.lift`** is the number P2's player-page card would display. Its effect size, and its CI, determine whether that card is worth building at all. Check the roster cohort separately — it is the population the page actually serves.
7. **`econ_increment.delta`** answers the econ-carryover question that motivated the whole design.
8. **Compare `yardstick_matrix_ex_ante` against `yardstick_matrix_realized`.** The weights are fitted on ex-ante components but today's scorer computes realized ones, so the realized row is what adoption would actually buy. A large gap between them is itself the argument for the Stage C question: should the scorer drop the realized swing term?
9. **Check `loading.excluded_matches`.** Matches whose rounds lacked impact rows were excluded, not zero-filled. A large count means the DB needs a rescore before any of this is trustworthy.

P2 (player page) and P3 (squad page) each get their own spec, written from these numbers.

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


def point_biserial(values, labels) -> float:
    """Pearson correlation between a continuous value and a 0/1 label.
    NaN when either side has zero variance."""
    v = np.asarray(values, dtype=float)
    l = np.asarray(labels, dtype=float)
    if v.std() == 0 or l.std() == 0:
        return float("nan")
    return float(np.corrcoef(v, l)[0, 1])

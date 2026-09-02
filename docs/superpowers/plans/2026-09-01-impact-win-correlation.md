# Impact-vs-Winning Evaluation Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build internal tooling that measures how much the custom Impact score relates to winning, fits better `FACTOR_WEIGHTS` against leakage-free forward-looking targets, and reports the plain descriptive correlation the project was originally asked for.

**Architecture:** A pure numeric layer (`stats_math`) with no domain knowledge; a domain layer (`impact_eval`, `impact_stage0`, `win_probability`) that turns DB rows into one differential observation per round and scores candidate weightings under nested cross-validation; and a CLI (`scripts/evaluate_impact.py`) that prints and serializes the report. Nothing is imported by the web app. The one structural change to existing code is splitting calculation from persistence in `app/scoring/impact.py` so the evaluator can compute components without writing them.

**Tech Stack:** Python 3, SQLAlchemy 2.0, numpy, pytest. No scipy, no sklearn, no pandas.

**Spec:** `docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md`

## Global Constraints

- **numpy only.** `scipy` is installed locally but absent from `requirements.txt`, which `render.yaml` installs from. Do not add it, and do not import it.
- **No new tables, no Alembic migrations.**
- **No change to `impact.py`'s formula.** The only permitted edit is extracting `build_impact_rows_for_match` so calculation and persistence separate. `compute_impact_for_match` keeps its exact signature and behaviour.
- **Nothing in this plan may be imported by `app/main.py`, any router, or any template.** The deploy path stays untouched.
- **Every extraction query excludes surrender placeholder rounds** using `app.services.surrender_rounds.NOT_A_SURRENDER_ROUND`.
- **Fitting uses `ex_ante` components only.** `realized` components appear only in retrospective/attribution contexts and are always labelled.
- **Cross-validation folds and bootstrap resamples are always by match**, never by row. All rounds of a match live in the same fold.
- **Run everything from `webapp/`** with `.\.venv\Scripts\python.exe`. Tests: `.\.venv\Scripts\python.exe -m pytest tests/<file> -v`.
- **Test style:** plain ORM model construction with relationships assigned by hand, no DB session, following `tests/test_player_profile_types.py`. Only Task 4 and Task 6 touch a live DB, and both skip cleanly when it is unreachable.

---

### Task 1: `stats_math` metrics — AUC, log loss, point-biserial

**Files:**
- Create: `webapp/app/services/stats_math.py`
- Test: `webapp/tests/test_stats_math.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `auc(scores, labels) -> float`, `log_loss(probs, labels) -> float`, `point_biserial(values, labels) -> float`, `_average_ranks(values) -> np.ndarray`.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_stats_math.py
"""Correctness tests for the pure numeric layer. Every case has an
analytically known answer -- no fixtures, no DB, no randomness except
explicitly seeded bootstrap draws."""

import numpy as np

from app.services.stats_math import auc, log_loss, point_biserial


def test_auc_perfect_separation():
    assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0


def test_auc_perfectly_inverted():
    assert auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0


def test_auc_all_tied_is_one_half():
    assert auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.5


def test_auc_single_class_is_nan():
    assert np.isnan(auc([0.1, 0.9], [1, 1]))


def test_log_loss_perfect_prediction_is_zero():
    assert log_loss([1.0, 0.0], [1, 0]) < 1e-9


def test_log_loss_coin_flip_is_ln2():
    assert abs(log_loss([0.5, 0.5], [1, 0]) - np.log(2)) < 1e-12


def test_point_biserial_perfect_positive():
    assert abs(point_biserial([1.0, 2.0, 3.0, 4.0], [0, 0, 1, 1]) - 0.8944271909999159) < 1e-9


def test_point_biserial_zero_variance_is_nan():
    assert np.isnan(point_biserial([1.0, 1.0, 1.0], [0, 1, 0]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.stats_math'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/stats_math.py
"""Pure numeric helpers for the impact-evaluation tooling.

NEUTRAL LEAF: imports numpy and nothing else from app/. No domain
knowledge, no DB, no ORM -- so app.services.impact_eval,
app.services.impact_stage0 and app.services.win_probability can all import
from here without any cycle risk.

numpy only, on purpose: scipy is not in requirements.txt (which
render.yaml installs from), and nothing here is heavy enough to justify
adding it to the deploy.
"""

import numpy as np


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks with ties averaged, which is what a tie-correct
    Mann-Whitney AUC needs."""
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
    """Rank-based (Mann-Whitney) AUC. NaN when only one class is present --
    a caller with an all-win or all-loss slice has no discrimination to
    measure, and a silent 0.5 would hide that."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels).astype(int)
    n_pos = int((labels == 1).sum())
    n_neg = int((labels == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = _average_ranks(scores)
    return float((ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def log_loss(probs, labels, eps: float = 1e-12) -> float:
    p = np.clip(np.asarray(probs, dtype=float), eps, 1 - eps)
    y = np.asarray(labels, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


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
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add AUC, log loss and point-biserial to stats_math"
```

---

### Task 2: `stats_math` weighted logistic fit (IRLS)

**Files:**
- Modify: `webapp/app/services/stats_math.py`
- Test: `webapp/tests/test_stats_math.py`

**Interfaces:**
- Consumes: nothing from Task 1 (same module, independent function).
- Produces: `fit_logistic(X, y, weights=None, l2=1.0, max_iter=100, tol=1e-9) -> np.ndarray` returning `beta` where `beta[0]` is the intercept and `beta[1:]` are coefficients in column order of `X`; `predict_proba(beta, X) -> np.ndarray`.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_stats_math.py
from app.services.stats_math import fit_logistic, predict_proba


def test_fit_logistic_recovers_known_coefficient():
    """y is set to the EXACT logistic mean of 2*x, so an unpenalised fit
    must recover slope 2 and intercept 0. Fractional y is legal here
    (quasi-binomial) and is what the WPA/forward-window targets produce."""
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
    """A constant-offset label set must still reach its intercept even
    under heavy L2; only slopes shrink."""
    x = np.zeros((40, 1))
    y = np.full(40, 0.75)
    beta = fit_logistic(x, y, l2=1000.0)
    assert abs(predict_proba(beta, x)[0] - 0.75) < 1e-6


def test_fit_logistic_respects_sample_weights():
    x = np.array([[0.0], [1.0]])
    y = np.array([0.0, 1.0])
    heavy_on_zero = fit_logistic(x, y, weights=np.array([100.0, 1.0]), l2=1.0)
    heavy_on_one = fit_logistic(x, y, weights=np.array([1.0, 100.0]), l2=1.0)
    assert predict_proba(heavy_on_zero, x)[0] < predict_proba(heavy_on_one, x)[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -k logistic -v`
Expected: FAIL with `ImportError: cannot import name 'fit_logistic'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/stats_math.py

def predict_proba(beta: np.ndarray, X) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    eta = beta[0] + X @ beta[1:]
    return 1.0 / (1.0 + np.exp(-eta))


def fit_logistic(X, y, weights=None, l2: float = 1.0, max_iter: int = 100, tol: float = 1e-9) -> np.ndarray:
    """Weighted IRLS logistic regression with ridge penalty.

    `y` may be fractional in [0, 1] (quasi-binomial), which is what both
    target builders produce -- a forward window of "fraction of next k
    rounds won" is not a 0/1 label.

    The intercept column is never penalised: shrinking it would bias the
    base rate, which is not what L2 is for here.

    Returns beta with beta[0] = intercept, beta[1:] = coefficients in the
    column order of X.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, p = X.shape
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)

    design = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(p + 1)
    penalty = np.eye(p + 1) * l2
    penalty[0, 0] = 0.0

    for _ in range(max_iter):
        eta = design @ beta
        mu = 1.0 / (1.0 + np.exp(-eta))
        variance = np.clip(mu * (1.0 - mu), 1e-9, None)
        s = variance * w
        z = eta + (y - mu) / variance
        weighted = design.T * s
        hessian = weighted @ design + penalty
        gradient = weighted @ z
        new_beta = np.linalg.solve(hessian, gradient)
        if np.max(np.abs(new_beta - beta)) < tol:
            return new_beta
        beta = new_beta
    return beta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add weighted IRLS logistic fit to stats_math"
```

---

### Task 3: `stats_math` calibration, terciles, cluster bootstrap

**Files:**
- Modify: `webapp/app/services/stats_math.py`
- Test: `webapp/tests/test_stats_math.py`

**Interfaces:**
- Consumes: `fit_logistic`, `predict_proba` (Task 2).
- Produces: `platt_calibrate(scores, labels) -> np.ndarray`, `apply_calibration(beta, scores) -> np.ndarray`, `tercile_buckets(values) -> np.ndarray` (values 0/1/2, or -1 when fewer than 3 inputs), `cluster_bootstrap_ci(fn, groups, draws=1000, seed=0, alpha=0.05) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_stats_math.py
from app.services.stats_math import (
    apply_calibration,
    cluster_bootstrap_ci,
    platt_calibrate,
    tercile_buckets,
)


def test_platt_calibration_turns_scores_into_probabilities():
    scores = np.linspace(-5, 5, 101)
    labels = (scores > 0).astype(int)
    beta = platt_calibrate(scores, labels)
    probs = apply_calibration(beta, scores)
    assert probs[0] < 0.5 < probs[-1]
    assert np.all((probs >= 0) & (probs <= 1))


def test_tercile_buckets_splits_evenly():
    buckets = tercile_buckets(list(range(9)))
    assert list(buckets) == [0, 0, 0, 1, 1, 1, 2, 2, 2]


def test_tercile_buckets_too_few_values_returns_sentinel():
    assert list(tercile_buckets([1.0, 2.0])) == [-1, -1]


def test_cluster_bootstrap_resamples_whole_groups():
    """Every group has a constant value, so any resample's mean is a mean
    of group values -- the interval must sit inside the group range and
    never split a group."""
    groups = {1: [0.0, 0.0], 2: [1.0, 1.0], 3: [2.0, 2.0]}

    def mean_of(sample):
        flat = [v for rows in sample for v in rows]
        return float(np.mean(flat))

    lo, hi = cluster_bootstrap_ci(mean_of, groups, draws=500, seed=7)
    assert 0.0 <= lo <= hi <= 2.0


def test_cluster_bootstrap_is_seed_deterministic():
    groups = {i: [float(i)] for i in range(10)}
    fn = lambda sample: float(np.mean([v for rows in sample for v in rows]))
    assert cluster_bootstrap_ci(fn, groups, draws=200, seed=3) == cluster_bootstrap_ci(
        fn, groups, draws=200, seed=3
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -k "calibration or tercile or bootstrap" -v`
Expected: FAIL with `ImportError: cannot import name 'platt_calibrate'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/stats_math.py

def platt_calibrate(scores, labels) -> np.ndarray:
    """Fit a 1-D logistic mapping raw scores -> probabilities. Callers MUST
    fit this on training-fold data only: AUC is rank-based and can consume
    raw scores, but log loss needs calibrated probabilities, and calibrating
    on the evaluation fold would leak."""
    return fit_logistic(np.asarray(scores, dtype=float).reshape(-1, 1), labels, l2=0.0)


def apply_calibration(beta: np.ndarray, scores) -> np.ndarray:
    return predict_proba(beta, np.asarray(scores, dtype=float).reshape(-1, 1))


def tercile_buckets(values) -> np.ndarray:
    """0 = bottom third, 1 = middle, 2 = top. Returns all -1 when there are
    fewer than 3 values, so callers can filter rather than crash."""
    v = np.asarray(values, dtype=float)
    if len(v) < 3:
        return np.full(len(v), -1, dtype=int)
    lower, upper = np.quantile(v, [1 / 3, 2 / 3])
    out = np.zeros(len(v), dtype=int)
    out[v > lower] = 1
    out[v > upper] = 2
    return out


def cluster_bootstrap_ci(fn, groups: dict, draws: int = 1000, seed: int = 0, alpha: float = 0.05):
    """Percentile CI from resampling WHOLE GROUPS with replacement.

    `groups` maps a cluster key (always a match_id in this project) to that
    cluster's rows; `fn` receives a list of row-lists. Resampling rows
    independently would treat the ~21 rounds of one match as independent
    evidence and understate every interval, so it is not offered.
    """
    rng = np.random.default_rng(seed)
    keys = list(groups.keys())
    stats = []
    for _ in range(draws):
        picked = rng.integers(0, len(keys), size=len(keys))
        value = fn([groups[keys[i]] for i in picked])
        if value is not None and np.isfinite(value):
            stats.append(value)
    if not stats:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (float(lo), float(hi))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: PASS, 17 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add Platt calibration, terciles and cluster bootstrap to stats_math"
```

---

### Task 4: Task 0 gate — the reconstruction identity

**Files:**
- Create: `webapp/tests/test_impact_reconstruction.py`

**Interfaces:**
- Consumes: `app.models.ImpactScore`, `app.scoring.impact.FACTOR_WEIGHTS`.
- Produces: nothing importable. This is a gate: **if it fails, stop and re-plan.** The whole fitting approach assumes `impact` is a linear function of the four stored columns.

**Why this comes before any fitting:** the spec's entire Stage A rests on `impact = damage + (w_e*econ_impact + w_t*time_impact + w_s*swing_impact) / sum(w)`. Each term is `round()`ed independently in `impact.py`, so exact equality is not expected — a tolerance of 2 covers the three independent roundings.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_impact_reconstruction.py
"""TASK 0 GATE (see the spec's 'The tuning surface already exists').

Stage A fits FACTOR_WEIGHTS by regressing on four stored columns. That is
only valid if `impact` really is the linear combination of them that
impact.py's arithmetic implies. This asserts the identity on live rows.

Skips when no database is reachable -- it is a data gate, not a unit test,
and a developer without the local Postgres up should not be blocked.
"""

import pytest
from sqlalchemy import select

from app.models import ImpactScore
from app.scoring.impact import FACTOR_WEIGHTS

TOLERANCE = 2  # three independent round() calls in impact.py
SAMPLE_SIZE = 5000


def _rows():
    try:
        from app.db import SessionLocal

        db = SessionLocal()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database available: {exc}")
    try:
        return db.execute(select(ImpactScore).limit(SAMPLE_SIZE)).scalars().all()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"impact_scores unreadable: {exc}")
    finally:
        db.close()


def test_impact_reconstructs_from_stored_components():
    rows = _rows()
    if not rows:
        pytest.skip("impact_scores is empty")

    total = sum(FACTOR_WEIGHTS.values())
    bad = []
    for row in rows:
        expected = row.damage + (
            FACTOR_WEIGHTS["econ"] * row.econ_impact
            + FACTOR_WEIGHTS["time"] * row.time_impact
            + FACTOR_WEIGHTS["swing"] * row.swing_impact
        ) / total
        if abs(expected - row.impact) > TOLERANCE:
            bad.append((row.round_id, row.match_player_id, row.impact, expected))

    assert not bad, (
        f"{len(bad)} of {len(rows)} rows break the linear identity Stage A "
        f"depends on. First five: {bad[:5]}. Do NOT proceed to fitting -- "
        f"re-read impact.py's kill_impact/death_impact combination step."
    )
```

- [ ] **Step 2: Run the gate**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_reconstruction.py -v`
Expected: PASS (or SKIP if Postgres is down — start it with `docker compose -p valomaths-private up -d`)

If it FAILS: stop. Report the mismatching rows and re-plan Stage A. Do not "fix" the tolerance to make it pass.

- [ ] **Step 3: Commit**

```bash
git add webapp/tests/test_impact_reconstruction.py
git commit -m "Add Task 0 gate asserting impact reconstructs from stored components"
```

---

### Task 5: Split calculation from persistence in the scorer

**Files:**
- Modify: `webapp/app/scoring/impact.py:371` (function head) and `:622-657` (the persistence block)
- Test: `webapp/tests/test_impact_exante_swing.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `CalculatedImpact` dataclass and `build_impact_rows_for_match(db, match_id, use_realized_swing=True) -> list[CalculatedImpact]`. `compute_impact_for_match(db, match_id) -> None` keeps its exact signature.

**Why this is a blocker fix, not a refactor for taste:** `compute_impact_for_match` queries `ImpactScore` (`impact.py:624`), `db.add`s (`:630`), mutates every column, and calls `db.commit()` unconditionally (`:657`). Adding an ex-ante flag to it without this split would **overwrite the stored scores** the first time the evaluator ran.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_impact_exante_swing.py
"""The evaluator must be able to compute impact components WITHOUT
writing them. This is the regression test for a data-corruption bug: an
earlier design added a use_realized_swing flag directly to
compute_impact_for_match, which commits unconditionally, so an ex-ante
evaluation run would have overwritten every stored score.
"""

import pytest

from app.models import ImpactScore
from app.scoring.impact import build_impact_rows_for_match, compute_impact_for_match


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


def _session_and_match_id():
    try:
        from app.db import SessionLocal

        db = SessionLocal()
        match_id = db.query(ImpactScore.round_id).limit(1).scalar()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database available: {exc}")
    if match_id is None:
        db.close()
        pytest.skip("no scored matches in the database")
    from app.models import Round

    real_match_id = db.query(Round.match_id).filter(Round.id == match_id).scalar()
    return db, real_match_id


def test_builder_writes_nothing():
    db, match_id = _session_and_match_id()
    spy = _SpyDB(db)
    try:
        rows = build_impact_rows_for_match(spy, match_id)
        assert rows, "expected calculated rows"
        assert spy.added == [], "builder must not add ORM objects"
        assert spy.commits == 0, "builder must not commit"
    finally:
        db.close()


def test_builder_matches_stored_values():
    db, match_id = _session_and_match_id()
    try:
        rows = build_impact_rows_for_match(db, match_id, use_realized_swing=True)
        stored = {
            (s.round_id, s.match_player_id): s
            for s in db.query(ImpactScore)
            .join(ImpactScore.round)
            .filter_by(match_id=match_id)
            .all()
        }
        assert stored, "match has no stored scores to compare against"
        for row in rows:
            existing = stored[(row.round_id, row.match_player_id)]
            for field in ("kill_impact", "death_impact", "impact", "damage",
                          "econ_impact", "time_impact", "swing_impact"):
                assert getattr(row, field) == getattr(existing, field), (
                    f"{field} drifted for {row.round_id}/{row.match_player_id}"
                )
    finally:
        db.close()
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

Rename the existing `compute_impact_for_match` to `build_impact_rows_for_match`, change its signature to `(db: Session, match_id: int, use_realized_swing: bool = True) -> list[CalculatedImpact]`, and make three edits inside it:

1. Initialise an accumulator before the aggregate loop at `impact.py:546`:

```python
    calculated: list[CalculatedImpact] = []
```

2. Replace the whole persistence block (originally `impact.py:622-655`, from `impact_score = (db.query(ImpactScore)...` through the `trade_detail` assignment) with:

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

Then add the new wrapper at the end of the module:

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
    it read-only -- see docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md."""
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
- Produces: honoured `use_realized_swing=False`, which suppresses `_realized_econ_swing_factor` so `swing_impact` reads no round N+1 data.

**Why:** `_realized_econ_swing_factor` (`impact.py:309`) reads `round_player_stats.get(round_number + 1)`. Its output flows through `_combine_swing_factors` into `kill_order_bonus_x_swing` (`:502`) and the stored `swing_impact` (`:639`). Predicting round N+1 from that column leaks. The ex-ante factor `_econ_swing_risk_factor` projects only from current-round credits and is clean.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_exante_swing.py

def test_ex_ante_ignores_next_round_stats():
    """With use_realized_swing=False the scorer must never consult round
    N+1. Proven by mutating the input dict the realized factor reads: if
    the ex-ante path touched it, the output would move."""
    from app.scoring.impact import _combine_swing_factors, _realized_econ_swing_factor

    # _realized_econ_swing_factor returns a non-neutral value only when it
    # actually reads next-round stats; a neutral 1.0 makes the combination
    # collapse to the ex-ante factor.
    assert _combine_swing_factors(1.4, 1.0) == 1.0
    assert _realized_econ_swing_factor({}, {}, 5, None) == 1.0


def test_ex_ante_and_realized_differ_somewhere():
    db, match_id = _session_and_match_id()
    try:
        realized = build_impact_rows_for_match(db, match_id, use_realized_swing=True)
        ex_ante = build_impact_rows_for_match(db, match_id, use_realized_swing=False)
        assert len(realized) == len(ex_ante)
        by_key = {(r.round_id, r.match_player_id): r for r in realized}
        differences = sum(
            1 for e in ex_ante if by_key[(e.round_id, e.match_player_id)].swing_impact != e.swing_impact
        )
        assert differences > 0, (
            "ex-ante and realized swing produced identical output for every "
            "row -- the flag is not wired through"
        )
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_exante_swing.py -k ex_ante -v`
Expected: FAIL on `test_ex_ante_and_realized_differ_somewhere` — the flag is accepted but ignored, so both calls return identical rows.

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
- Consumes: `build_impact_rows_for_match` (Tasks 5-6), `NOT_A_SURRENDER_ROUND`, `attacking_team_for_round`.
- Produces: `RoundObservation` dataclass; `build_observations_for_match(match, calculated_rows) -> list[RoundObservation]`; `FULL_BUY_THRESHOLD` re-export is **not** created — import it from `app.scoring.impact`.

**Design note — one row per round, not two.** A round's two (round, team) rows have perfectly complementary outcomes; counting them as two observations would double every apparent sample size. Features are team-A-minus-team-B differentials, labels are "did team A ...". Because `attacking_team_for_round` returns `TEAM_1` for every round <= 12, `attacking_is_team_a` is constant `True` across the whole first half — that is why the first-half yardstick is not split by side.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_impact_eval.py
"""Observation extraction: one differential row per round, team A minus
team B. Fixtures are plain ORM construction with no session, following
tests/test_player_profile_types.py."""

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.scoring.impact import CalculatedImpact
from app.services.impact_eval import build_observations_for_match


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
    assert obs[0].kill_diff == 2 - 0


def test_score_differential_excludes_the_current_round():
    """Round 1's control must be 0-0, not 1-0: the round's own result is a
    separate control and must never leak into pre-round score."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].score_diff_before == 0
    assert obs[1].score_diff_before == 1  # team A won round 1


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.impact_eval'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/impact_eval.py
"""Turns match data into ONE differential observation per round, then fits
and scores candidate Impact weightings against forward-looking targets.

Internal tooling only -- nothing here is imported by app/main.py, any
router, or any template. See
docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md.

Observation unit is one row per round, features as team-A-minus-team-B
differentials. The two (round, team) rows of a round have perfectly
complementary outcomes, so treating them as two observations would
double every apparent sample size.
"""

from dataclasses import dataclass

from app.models.match import Team
from app.scoring.impact import FULL_BUY_THRESHOLD
from app.services.map_side_stats import attacking_team_for_round

SURRENDER_SUFFIX = "Surrendered Win"


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

    # Baseline differentials.
    kills: float
    deaths: float
    kill_diff: float

    # Controls, timed per the spec: score BEFORE this round, economy at the
    # START of this round, side DURING this round, round result kept separate.
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
    if match.team1_rounds_won == match.team2_rounds_won:
        return None
    return match.team1_rounds_won > match.team2_rounds_won


def build_observations_for_match(match, calculated_rows) -> list[RoundObservation]:
    """`calculated_rows` are CalculatedImpact objects from
    build_impact_rows_for_match, for this match only. Surrender placeholder
    rounds are dropped -- nobody played them."""
    team_by_mp = {
        mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team)
        for mp in match.match_players
    }
    team_a = Team.TEAM_1.value

    impact_by_round: dict[int, dict[str, float]] = {}
    for row in calculated_rows:
        sign = 1.0 if team_by_mp.get(row.match_player_id) == team_a else -1.0
        bucket = impact_by_round.setdefault(
            row.round_id, {"damage": 0.0, "econ_impact": 0.0, "time_impact": 0.0, "swing_impact": 0.0}
        )
        bucket["damage"] += sign * row.damage
        bucket["econ_impact"] += sign * row.econ_impact
        bucket["time_impact"] += sign * row.time_impact
        bucket["swing_impact"] += sign * row.swing_impact

    playable = [r for r in sorted(match.rounds, key=lambda r: r.round_number)
                if not (r.outcome or "").endswith(SURRENDER_SUFFIX)]
    match_result = _match_won_by_team_a(match)

    observations: list[RoundObservation] = []
    score_a = 0
    score_b = 0
    for index, round_row in enumerate(playable):
        kills_a = deaths_a = kills_b = deaths_b = 0
        loadout_a = loadout_b = 0
        full_buy_a = full_buy_b = 0
        for stat in round_row.player_stats:
            if team_by_mp.get(stat.match_player_id) == team_a:
                kills_a += stat.kills
                deaths_a += stat.deaths
                loadout_a += stat.loadout
                full_buy_a += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0
            else:
                kills_b += stat.kills
                deaths_b += stat.deaths
                loadout_b += stat.loadout
                full_buy_b += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0

        impact = impact_by_round.get(round_row.id, {})
        won_by_a = _winner_is_team_a(round_row.outcome)

        observations.append(
            RoundObservation(
                match_id=match.id,
                round_id=round_row.id,
                round_number=round_row.round_number,
                damage=impact.get("damage", 0.0),
                econ_impact=impact.get("econ_impact", 0.0),
                time_impact=impact.get("time_impact", 0.0),
                swing_impact=impact.get("swing_impact", 0.0),
                kills=kills_a - kills_b,
                deaths=deaths_a - deaths_b,
                kill_diff=(kills_a - deaths_a) - (kills_b - deaths_b),
                score_diff_before=score_a - score_b,
                attacking_is_team_a=attacking_team_for_round(round_row.round_number) == Team.TEAM_1,
                loadout_diff=loadout_a - loadout_b,
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
Expected: PASS, 7 tests

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
- Produces: `assign_folds(match_ids, n_folds=5, seed=0) -> dict[int, int]`, `group_by_match(observations) -> dict[int, list[RoundObservation]]`.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import assign_folds, group_by_match


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
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    grouped = group_by_match(obs)
    assert list(grouped) == [1]
    assert len(grouped[1]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "fold or grouping" -v`
Expected: FAIL with `ImportError: cannot import name 'assign_folds'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py
import numpy as np


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
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add match-level fold assignment and grouping"
```

---

### Task 9: Target builders — T1 and T2

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `RoundObservation`, `group_by_match` (Tasks 7-8).
- Produces: `FitDataset` dataclass with fields `X: np.ndarray`, `y: np.ndarray`, `w: np.ndarray`, `match_ids: np.ndarray`, `feature_names: list[str]`; `first_half_target(observations, feature_names) -> FitDataset`; `forward_window_target(observations, feature_names, k=3, gamma=0.7, match_weight=1.0) -> FitDataset`; `FIRST_HALF_ROUNDS = 12`.

**Rules encoded here:**
- T1 needs all 12 genuine first-half rounds; 22 matches in the live DB fall short and are excluded, giving n = 1,129 rather than 1,151.
- T2 never crosses the halftime reset (rounds 1-12, 13-24, OT separately), skips terminal rounds, and attaches the match outcome only for N <= 12.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
import numpy as np

from app.services.impact_eval import (
    FEATURE_COMPONENTS,
    FIRST_HALF_ROUNDS,
    first_half_target,
    forward_window_target,
)


def _linear_obs(round_number, damage, won_by_a, match_won, terminal=False, match_id=1):
    from app.services.impact_eval import RoundObservation

    return RoundObservation(
        match_id=match_id, round_id=1000 + round_number, round_number=round_number,
        damage=damage, econ_impact=0.0, time_impact=0.0, swing_impact=0.0,
        kills=0.0, deaths=0.0, kill_diff=0.0,
        score_diff_before=0, attacking_is_team_a=True,
        loadout_diff=0.0, full_buy_count_diff=0,
        round_won_by_team_a=won_by_a, match_won_by_team_a=match_won,
        is_terminal=terminal,
    )


def test_first_half_requires_all_twelve_rounds():
    short = [_linear_obs(n, 10.0, True, True) for n in range(1, 12)]
    assert len(first_half_target(short, FEATURE_COMPONENTS).y) == 0

    full = [_linear_obs(n, 10.0, True, True) for n in range(1, FIRST_HALF_ROUNDS + 1)]
    assert len(first_half_target(full, FEATURE_COMPONENTS).y) == 1


def test_first_half_sums_components_over_the_half():
    full = [_linear_obs(n, 10.0, True, True) for n in range(1, FIRST_HALF_ROUNDS + 1)]
    dataset = first_half_target(full, FEATURE_COMPONENTS)
    assert dataset.X[0][FEATURE_COMPONENTS.index("damage")] == 120.0
    assert dataset.y[0] == 1.0


def test_forward_window_does_not_cross_halftime():
    """Round 12 is the last of the first half. Its window must stop at the
    reset, so with match_weight=0 it contributes nothing at all -- whereas
    round 11 still gets its one in-half partner (round 12)."""
    obs = [_linear_obs(n, 1.0, True, True) for n in range(1, 25)]
    obs[-1].is_terminal = True

    twelve = forward_window_target(
        [o for o in obs if o.round_number == 12], FEATURE_COMPONENTS, k=3, match_weight=0.0
    )
    assert len(twelve.y) == 0

    eleven_and_twelve = forward_window_target(
        [o for o in obs if o.round_number in (11, 12)], FEATURE_COMPONENTS, k=3, match_weight=0.0
    )
    assert len(eleven_and_twelve.y) == 1  # 11 -> 12 only; 12 -> 13 is across the reset


def test_forward_window_starts_a_fresh_window_in_the_second_half():
    obs = [_linear_obs(n, 1.0, True, True) for n in range(13, 17)]
    obs[-1].is_terminal = True
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=2, match_weight=0.0)
    # rounds 13 and 14 each contribute; 15 contributes 1; 16 is terminal.
    assert len(dataset.y) == 5


def test_forward_window_terminal_round_excluded():
    obs = [_linear_obs(1, 1.0, True, True), _linear_obs(2, 1.0, True, True, terminal=True)]
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=3, match_weight=0.0)
    assert len(dataset.y) == 1  # only round 1 contributes


def test_match_auxiliary_only_for_early_rounds():
    late = [_linear_obs(20, 1.0, True, True), _linear_obs(21, 1.0, True, True, terminal=True)]
    with_aux = forward_window_target(late, FEATURE_COMPONENTS, k=3, match_weight=5.0)
    without_aux = forward_window_target(late, FEATURE_COMPONENTS, k=3, match_weight=0.0)
    assert len(with_aux.y) == len(without_aux.y), "round 20 must get no match auxiliary"


def test_gamma_discounts_further_rounds():
    obs = [_linear_obs(n, 1.0, True, True) for n in range(1, 6)]
    obs[-1].is_terminal = True
    dataset = forward_window_target(obs, FEATURE_COMPONENTS, k=3, gamma=0.5, match_weight=0.0)
    first_round_weights = sorted(dataset.w[:3], reverse=True)
    assert first_round_weights == [1.0, 0.5, 0.25]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "first_half or forward or gamma or auxiliary" -v`
Expected: FAIL with `ImportError: cannot import name 'FEATURE_COMPONENTS'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py

FIRST_HALF_ROUNDS = 12
SECOND_HALF_END = 24

FEATURE_COMPONENTS = ["damage", "econ_impact", "time_impact", "swing_impact"]
# Used by the control ladder; the standalone baselines live in
# BASELINE_CANDIDATES (Task 13), which carries weights as well as names.
BASELINE_DAMAGE = ["damage"]
CONTROLS_RESULT = ["round_result"]
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff", "full_buy_count_diff"]


@dataclass
class FitDataset:
    X: "np.ndarray"
    y: "np.ndarray"
    w: "np.ndarray"
    match_ids: "np.ndarray"
    feature_names: list[str]


def _feature_value(obs: RoundObservation, name: str) -> float:
    if name == "round_result":
        return 0.0 if obs.round_won_by_team_a is None else (1.0 if obs.round_won_by_team_a else -1.0)
    if name == "attacking_is_team_a":
        return 1.0 if obs.attacking_is_team_a else 0.0
    return float(getattr(obs, name))


def _row(obs: RoundObservation, feature_names: list[str]) -> list[float]:
    return [_feature_value(obs, name) for name in feature_names]


def _empty_dataset(feature_names: list[str]) -> FitDataset:
    return FitDataset(
        X=np.zeros((0, len(feature_names))), y=np.zeros(0), w=np.zeros(0),
        match_ids=np.zeros(0, dtype=int), feature_names=list(feature_names),
    )


def _half_of(round_number: int) -> int:
    if round_number <= FIRST_HALF_ROUNDS:
        return 1
    if round_number <= SECOND_HALF_END:
        return 2
    return 3  # overtime


def first_half_target(observations, feature_names: list[str]) -> FitDataset:
    """T1: one row per ELIGIBLE match. Components are summed over rounds
    1-12; a match missing any genuine first-half round is excluded, because
    a truncated total is not comparable to a full one."""
    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        first_half = [o for o in obs if o.round_number <= FIRST_HALF_ROUNDS]
        if len(first_half) != FIRST_HALF_ROUNDS:
            continue
        result = first_half[0].match_won_by_team_a
        if result is None:
            continue
        totals = [sum(_feature_value(o, name) for o in first_half) for name in feature_names]
        rows.append(totals)
        ys.append(1.0 if result else 0.0)
        ws.append(1.0)
        mids.append(match_id)

    if not rows:
        return _empty_dataset(feature_names)
    return FitDataset(np.array(rows, dtype=float), np.array(ys), np.array(ws),
                      np.array(mids, dtype=int), list(feature_names))


def forward_window_target(
    observations, feature_names: list[str], k: int = 3, gamma: float = 0.7, match_weight: float = 1.0
) -> FitDataset:
    """T2: round N's features paired with each of rounds N+1..N+k as a
    separate weighted observation.

    Windows never cross the halftime reset or the OT boundary (the same rule
    impact.py:309 already encodes). Terminal rounds contribute nothing --
    they have no eligible future. The match-outcome auxiliary is attached
    only for N <= 12, because for later rounds the match result is
    substantially determined by round N and would reintroduce the tautology.
    """
    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_number = {o.round_number: o for o in obs}
        for o in obs:
            if o.is_terminal:
                continue
            features = _row(o, feature_names)
            for step in range(1, k + 1):
                future = by_number.get(o.round_number + step)
                if future is None or _half_of(future.round_number) != _half_of(o.round_number):
                    break
                if future.round_won_by_team_a is None:
                    continue
                rows.append(features)
                ys.append(1.0 if future.round_won_by_team_a else 0.0)
                ws.append(gamma ** (step - 1))
                mids.append(match_id)

            if match_weight > 0 and o.round_number <= FIRST_HALF_ROUNDS and o.match_won_by_team_a is not None:
                rows.append(features)
                ys.append(1.0 if o.match_won_by_team_a else 0.0)
                ws.append(match_weight)
                mids.append(match_id)

    if not rows:
        return _empty_dataset(feature_names)
    return FitDataset(np.array(rows, dtype=float), np.array(ys), np.array(ws),
                      np.array(mids, dtype=int), list(feature_names))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 18 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add T1 and T2 target builders with half-boundary and auxiliary rules"
```

---

### Task 10: Nested CV harness with standardization and calibrated baselines

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `FitDataset`, `assign_folds` (Tasks 8-9), `fit_logistic`, `predict_proba`, `auc`, `log_loss`, `platt_calibrate`, `apply_calibration` (Tasks 1-3).
- Produces: `standardize(X_train, X_apply) -> tuple[np.ndarray, np.ndarray, np.ndarray]` returning `(train_scaled, apply_scaled, scale)`; `run_nested_cv(dataset, l2_grid, n_folds=5, seed=0) -> dict` with keys `"fold_betas"` (list of raw-scale beta arrays), `"oof_scores"`, `"oof_labels"`, `"oof_match_ids"`.

**Standardization matters:** `damage` and `swing_impact` differ by an order of magnitude in scale, so an unstandardized ridge penalty would shrink them unequally. Statistics come from the training fold only.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import FitDataset, run_nested_cv, standardize


def test_standardize_uses_training_statistics_only():
    train = np.array([[0.0], [10.0]])
    apply_to = np.array([[20.0]])
    train_scaled, apply_scaled, scale = standardize(train, apply_to)
    assert abs(train_scaled.mean()) < 1e-12
    assert abs(apply_scaled[0][0] - 3.0) < 1e-9  # (20 - 5) / 5


def test_standardize_handles_constant_column():
    train = np.array([[1.0], [1.0]])
    train_scaled, _, scale = standardize(train, train)
    assert np.all(np.isfinite(train_scaled))
    assert scale[0] == 1.0


def test_nested_cv_recovers_a_planted_signal():
    """A dataset where the label is a deterministic function of feature 0
    must produce out-of-fold AUC well above chance and a positive
    coefficient in every fold."""
    rng = np.random.default_rng(0)
    n = 400
    x = rng.normal(size=(n, 2))
    y = (x[:, 0] > 0).astype(float)
    dataset = FitDataset(x, y, np.ones(n), np.arange(n), ["signal", "noise"])
    out = run_nested_cv(dataset, l2_grid=[0.1, 1.0], n_folds=5, seed=0)
    from app.services.stats_math import auc as auc_fn

    assert auc_fn(out["oof_scores"], out["oof_labels"]) > 0.9
    assert all(beta[1] > 0 for beta in out["fold_betas"])


def test_nested_cv_keeps_a_match_out_of_its_own_training_fold():
    rng = np.random.default_rng(1)
    n = 50
    dataset = FitDataset(rng.normal(size=(n, 1)), rng.integers(0, 2, n).astype(float),
                         np.ones(n), np.arange(n), ["f"])
    out = run_nested_cv(dataset, l2_grid=[1.0], n_folds=5, seed=0)
    assert len(out["oof_scores"]) == n
    assert sorted(out["oof_match_ids"]) == list(range(n))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "standardize or nested" -v`
Expected: FAIL with `ImportError: cannot import name 'standardize'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py
from app.services.stats_math import (
    apply_calibration,
    auc,
    fit_logistic,
    log_loss,
    platt_calibrate,
    predict_proba,
)


def standardize(X_train, X_apply):
    """Centre and scale by TRAINING statistics. A constant column gets
    scale 1.0 rather than 0, so it contributes nothing instead of producing
    NaN."""
    X_train = np.asarray(X_train, dtype=float)
    X_apply = np.asarray(X_apply, dtype=float)
    centre = X_train.mean(axis=0)
    scale = X_train.std(axis=0)
    scale = np.where(scale == 0, 1.0, scale)
    return (X_train - centre) / scale, (X_apply - centre) / scale, scale


def _select_l2(X, y, w, match_ids, l2_grid, seed) -> float:
    """Inner CV, on the TRAINING data only. Objective is target-specific log
    loss, per the spec."""
    if len(l2_grid) == 1:
        return l2_grid[0]
    inner = assign_folds(match_ids, n_folds=3, seed=seed + 1)
    best, best_loss = l2_grid[0], float("inf")
    for l2 in l2_grid:
        losses = []
        for fold in range(3):
            mask = np.array([inner[int(m)] == fold for m in match_ids])
            if mask.all() or not mask.any():
                continue
            tr, ap, _ = standardize(X[~mask], X[mask])
            beta = fit_logistic(tr, y[~mask], weights=w[~mask], l2=l2)
            losses.append(log_loss(predict_proba(beta, ap), np.round(y[mask])))
        mean_loss = float(np.mean(losses)) if losses else float("inf")
        if mean_loss < best_loss:
            best, best_loss = l2, mean_loss
    return best


def run_nested_cv(dataset: FitDataset, l2_grid, n_folds: int = 5, seed: int = 0) -> dict:
    """Outer folds produce the reported numbers; L2 is chosen by inner folds
    inside each training split, never on the reporting folds."""
    folds = assign_folds(dataset.match_ids, n_folds=n_folds, seed=seed)
    fold_of = np.array([folds[int(m)] for m in dataset.match_ids])

    oof_scores, oof_labels, oof_match_ids, fold_betas = [], [], [], []
    for fold in range(n_folds):
        test_mask = fold_of == fold
        if not test_mask.any() or test_mask.all():
            continue
        X_tr, X_te = dataset.X[~test_mask], dataset.X[test_mask]
        y_tr = dataset.y[~test_mask]
        w_tr = dataset.w[~test_mask]

        l2 = _select_l2(X_tr, y_tr, w_tr, dataset.match_ids[~test_mask], l2_grid, seed)
        tr_scaled, te_scaled, scale = standardize(X_tr, X_te)
        beta = fit_logistic(tr_scaled, y_tr, weights=w_tr, l2=l2)

        raw_beta = np.concatenate([[beta[0]], beta[1:] / scale])
        fold_betas.append(raw_beta)
        oof_scores.extend(predict_proba(beta, te_scaled).tolist())
        oof_labels.extend(np.round(dataset.y[test_mask]).tolist())
        oof_match_ids.extend(dataset.match_ids[test_mask].tolist())

    return {
        "fold_betas": fold_betas,
        "oof_scores": np.array(oof_scores),
        "oof_labels": np.array(oof_labels),
        "oof_match_ids": np.array(oof_match_ids, dtype=int),
    }

```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 22 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add nested CV harness with training-fold standardization"
```

---

### Task 11: Coefficient diagnostics and constrained mapping

**Files:**
- Modify: `webapp/app/services/impact_eval.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `standardize`, `fit_logistic`, `log_loss`, `run_nested_cv`, `assign_folds` (Tasks 2, 8, 10).
- Produces: `ConstrainedWeights` dataclass with `damage_multiplier: float`, `econ: float`, `time: float`, `swing: float`, `train_log_loss: float`; `fit_constrained_weights(X, y, w, feature_names, simplex_step=0.05, damage_grid=None) -> ConstrainedWeights`; `coefficient_diagnostics(dataset, draws=200, seed=0, l2=1.0) -> dict` with keys `"sign_stability"`, `"correlation_matrix"`, `"drop_one"`.

**Why the diagnostics are not optional:** `impact.py:496-502` builds all three components as `kill_order_bonus * <factor>` — they share the same multiplicand, so they are collinear *by construction* and unstable raw coefficients are expected rather than surprising. A coefficient whose sign flips across resamples must be reported as indeterminate, never as a finding.

**Why this is a separate optimization:** IRLS returns unconstrained coefficients. The shipped form is `impact = d*damage + (sum w_i*f_i)/sum(w)` with `w_i >= 0`, which is scale-invariant in `w` and therefore only 3 effective degrees of freedom. It cannot express an arbitrary coefficient vector, so it is searched directly rather than derived from `beta`. Negative unconstrained coefficients are reported by the caller, never clipped here.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import fit_constrained_weights


def test_constrained_search_recovers_a_planted_weighting():
    """Labels generated from a known weighting (econ heavily favoured) must
    be recovered by the search."""
    rng = np.random.default_rng(3)
    n = 800
    damage = rng.normal(size=n)
    econ = rng.normal(size=n)
    time_c = rng.normal(size=n)
    swing = rng.normal(size=n)
    score = 0.5 * damage + (0.8 * econ + 0.1 * time_c + 0.1 * swing)
    y = (score + rng.normal(scale=0.05, size=n) > 0).astype(float)

    X = np.column_stack([damage, econ, time_c, swing])
    result = fit_constrained_weights(X, y, np.ones(n), FEATURE_COMPONENTS)
    assert result.econ > result.time
    assert result.econ > result.swing


def test_constrained_weights_are_non_negative_and_normalised():
    rng = np.random.default_rng(4)
    n = 200
    X = rng.normal(size=(n, 4))
    y = (X[:, 1] > 0).astype(float)
    result = fit_constrained_weights(X, y, np.ones(n), FEATURE_COMPONENTS)
    assert result.econ >= 0 and result.time >= 0 and result.swing >= 0
    assert abs((result.econ + result.time + result.swing) - 3.0) < 1e-6


def test_constrained_search_is_deterministic():
    rng = np.random.default_rng(5)
    X = rng.normal(size=(150, 4))
    y = (X[:, 2] > 0).astype(float)
    a = fit_constrained_weights(X, y, np.ones(150), FEATURE_COMPONENTS)
    b = fit_constrained_weights(X, y, np.ones(150), FEATURE_COMPONENTS)
    assert (a.econ, a.time, a.swing, a.damage_multiplier) == (b.econ, b.time, b.swing, b.damage_multiplier)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k constrained -v`
Expected: FAIL with `ImportError: cannot import name 'fit_constrained_weights'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py

# Normalised so the three factor weights sum to 3, matching the shipped
# FACTOR_WEIGHTS = {"econ": 1.0, "time": 1.0, "swing": 1.0} convention.
FACTOR_WEIGHT_TOTAL = 3.0


@dataclass
class ConstrainedWeights:
    damage_multiplier: float
    econ: float
    time: float
    swing: float
    train_log_loss: float


def _simplex_grid(step: float):
    """All non-negative (a, b, c) with a + b + c == 1, on a `step` lattice."""
    steps = int(round(1.0 / step))
    for i in range(steps + 1):
        for j in range(steps + 1 - i):
            yield (i / steps, j / steps, (steps - i - j) / steps)


def fit_constrained_weights(
    X, y, w, feature_names: list[str], simplex_step: float = 0.05, damage_grid=None
) -> ConstrainedWeights:
    """Search (damage_multiplier, w_econ, w_time, w_swing) under the shipped
    parameterization: impact = d*damage + (sum w_i*f_i)/sum(w), w_i >= 0.

    Scale-invariant in w, so the search is over the simplex times a damage
    multiplier grid -- 3 effective degrees of freedom. Each candidate is
    scored by fitting a 1-D logistic (intercept + slope) on the resulting
    composite score and taking training log loss.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.asarray(w, dtype=float)
    idx = {name: feature_names.index(name) for name in FEATURE_COMPONENTS}
    damage = X[:, idx["damage"]]
    factors = np.column_stack([X[:, idx["econ_impact"]], X[:, idx["time_impact"]], X[:, idx["swing_impact"]]])

    if damage_grid is None:
        damage_grid = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]

    labels = np.round(y)
    best = None
    for weights in _simplex_grid(simplex_step):
        factor_score = factors @ np.array(weights)
        for d in damage_grid:
            composite = d * damage + factor_score
            if composite.std() == 0:
                continue
            scaled = (composite - composite.mean()) / composite.std()
            beta = fit_logistic(scaled.reshape(-1, 1), y, weights=w, l2=0.0)
            loss = log_loss(predict_proba(beta, scaled.reshape(-1, 1)), labels)
            key = (loss, d, weights)
            if best is None or key < best:
                best = key

    loss, d, weights = best
    scaled_weights = [v * FACTOR_WEIGHT_TOTAL for v in weights]
    return ConstrainedWeights(
        damage_multiplier=float(d),
        econ=float(scaled_weights[0]),
        time=float(scaled_weights[1]),
        swing=float(scaled_weights[2]),
        train_log_loss=float(loss),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k constrained -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Write the failing diagnostics test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import coefficient_diagnostics


def test_sign_stability_is_high_for_a_clean_signal():
    rng = np.random.default_rng(6)
    n = 600
    X = rng.normal(size=(n, 4))
    y = (X[:, 1] * 3 + rng.normal(scale=0.1, size=n) > 0).astype(float)
    dataset = FitDataset(X, y, np.ones(n), np.arange(n), FEATURE_COMPONENTS)
    diag = coefficient_diagnostics(dataset, draws=60, seed=0)
    assert diag["sign_stability"]["econ_impact"] > 0.95


def test_sign_stability_is_near_chance_for_pure_noise():
    rng = np.random.default_rng(7)
    n = 300
    X = rng.normal(size=(n, 4))
    y = rng.integers(0, 2, n).astype(float)
    dataset = FitDataset(X, y, np.ones(n), np.arange(n), FEATURE_COMPONENTS)
    diag = coefficient_diagnostics(dataset, draws=60, seed=0)
    assert 0.2 < diag["sign_stability"]["time_impact"] < 0.8


def test_correlation_matrix_detects_a_duplicated_column():
    rng = np.random.default_rng(8)
    n = 200
    base = rng.normal(size=n)
    X = np.column_stack([rng.normal(size=n), base, base, rng.normal(size=n)])
    dataset = FitDataset(X, (base > 0).astype(float), np.ones(n), np.arange(n), FEATURE_COMPONENTS)
    diag = coefficient_diagnostics(dataset, draws=20, seed=0)
    assert abs(diag["correlation_matrix"]["econ_impact"]["time_impact"] - 1.0) < 1e-9


def test_drop_one_reports_a_score_per_component():
    rng = np.random.default_rng(9)
    n = 300
    X = rng.normal(size=(n, 4))
    dataset = FitDataset(X, (X[:, 0] > 0).astype(float), np.ones(n), np.arange(n), FEATURE_COMPONENTS)
    diag = coefficient_diagnostics(dataset, draws=20, seed=0)
    assert set(diag["drop_one"]) == set(FEATURE_COMPONENTS)
    assert diag["drop_one"]["damage"]["auc_without"] < diag["full_auc"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "sign_stability or correlation_matrix or drop_one" -v`
Expected: FAIL with `ImportError: cannot import name 'coefficient_diagnostics'`

- [ ] **Step 7: Write the diagnostics implementation**

```python
# append to webapp/app/services/impact_eval.py

def coefficient_diagnostics(dataset: FitDataset, draws: int = 200, seed: int = 0, l2: float = 1.0) -> dict:
    """Collinearity reporting for a fit whose components share a
    multiplicand by construction (impact.py:496-502).

    `sign_stability` is a REFITTING bootstrap: the model is re-fit on each
    resampled set of matches. Resampling fixed out-of-fold predictions can
    give metric CIs but says nothing about whether a coefficient's sign is
    stable, so it is not used here.
    """
    rng = np.random.default_rng(seed)
    grouped = {}
    for i, m in enumerate(dataset.match_ids):
        grouped.setdefault(int(m), []).append(i)
    keys = list(grouped)

    positives = np.zeros(dataset.X.shape[1])
    completed = 0
    for _ in range(draws):
        picked = rng.integers(0, len(keys), size=len(keys))
        idx = [i for p in picked for i in grouped[keys[int(p)]]]
        X, y, w = dataset.X[idx], dataset.y[idx], dataset.w[idx]
        if len(np.unique(np.round(y))) < 2:
            continue
        scaled, _, scale = standardize(X, X)
        beta = fit_logistic(scaled, y, weights=w, l2=l2)
        positives += (beta[1:] / scale > 0).astype(float)
        completed += 1

    sign_stability = {
        name: float(max(p, completed - p) / completed) if completed else float("nan")
        for name, p in zip(dataset.feature_names, positives)
    }

    corr = np.corrcoef(dataset.X, rowvar=False)
    correlation_matrix = {
        a: {b: float(corr[i][j]) for j, b in enumerate(dataset.feature_names)}
        for i, a in enumerate(dataset.feature_names)
    }

    full = run_nested_cv(dataset, l2_grid=[l2], seed=seed)
    full_auc = auc(full["oof_scores"], full["oof_labels"]) if len(full["oof_labels"]) else float("nan")

    drop_one = {}
    for i, name in enumerate(dataset.feature_names):
        keep = [j for j in range(dataset.X.shape[1]) if j != i]
        reduced = FitDataset(
            dataset.X[:, keep], dataset.y, dataset.w, dataset.match_ids,
            [dataset.feature_names[j] for j in keep],
        )
        out = run_nested_cv(reduced, l2_grid=[l2], seed=seed)
        without = auc(out["oof_scores"], out["oof_labels"]) if len(out["oof_labels"]) else float("nan")
        drop_one[name] = {"auc_without": without, "auc_lost": full_auc - without}

    return {
        "sign_stability": sign_stability,
        "correlation_matrix": correlation_matrix,
        "full_auc": full_auc,
        "drop_one": drop_one,
        "bootstrap_draws_completed": completed,
    }
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 29 tests

- [ ] **Step 9: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add coefficient diagnostics and constrained FACTOR_WEIGHTS search

Sign stability uses a refitting bootstrap: the components share a
kill_order_bonus multiplicand by construction (impact.py:496-502), so
unstable raw coefficients are expected and must be reported as
indeterminate rather than as findings."
```

---

### Task 12: Stage 0 — cohorts and descriptive statistics

**Files:**
- Create: `webapp/app/services/impact_stage0.py`
- Test: `webapp/tests/test_stage0_cohorts.py`

**Interfaces:**
- Consumes: `point_biserial`, `tercile_buckets`, `cluster_bootstrap_ci` (Tasks 1, 3).
- Produces: `PlayerMatch` dataclass with `player_id: int`, `match_id: int`, `avg_impact: float`, `won: bool`; `COHORT_RULES: dict[str, int]`; `filter_cohort(player_matches, min_matches) -> list[PlayerMatch]`; `within_player_tercile_lift(player_matches, min_matches=9) -> dict`; `describe(player_matches) -> dict`.

**Why cohorts are mandatory:** measured on the live DB, **7,814 of 8,251 players (94.7%) have exactly one match**. Within-player centering over all players would be overwhelmingly rows whose centered Impact is exactly 0 by construction — a zero-variance artifact, not a finding. Usable cohorts: 437 players with >= 2 matches, 81 with >= 9 (three per tercile), 71 with >= 10.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_stage0_cohorts.py
"""Stage 0 answers the original question -- does a player's Impact track
their wins -- on the CURRENT stored scores, before any fitting. The cohort
rules exist because 94.7% of players in this DB have exactly one match."""

import numpy as np

from app.services.impact_stage0 import (
    COHORT_RULES,
    PlayerMatch,
    describe,
    filter_cohort,
    within_player_tercile_lift,
)


def _player_history(player_id, impacts, wins):
    return [
        PlayerMatch(player_id=player_id, match_id=player_id * 100 + i, avg_impact=v, won=w)
        for i, (v, w) in enumerate(zip(impacts, wins))
    ]


def test_cohort_rules_match_the_spec():
    assert COHORT_RULES["recurrent"] == 2
    assert COHORT_RULES["per_player_tercile"] == 9
    assert COHORT_RULES["per_player_correlation"] == 10


def test_filter_cohort_drops_single_match_players():
    rows = _player_history(1, [100.0], [True]) + _player_history(2, [100.0, 200.0], [False, True])
    kept = filter_cohort(rows, min_matches=2)
    assert {r.player_id for r in kept} == {2}


def test_single_match_player_would_have_zero_centred_impact():
    """The exact artifact the cohort rule exists to exclude."""
    rows = _player_history(1, [500.0], [True])
    centred = [r.avg_impact - np.mean([x.avg_impact for x in rows]) for r in rows]
    assert centred == [0.0]


def test_within_player_terciles_measure_lift_against_own_baseline():
    """One player, nine matches: the top third wins every time, the bottom
    third never does. Lift must be 1.0."""
    impacts = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
    wins = [False, False, False, False, True, False, True, True, True]
    result = within_player_tercile_lift(_player_history(1, impacts, wins), min_matches=9)
    assert result["top_win_rate"] == 1.0
    assert result["bottom_win_rate"] == 0.0
    assert result["lift"] == 1.0
    assert result["players"] == 1


def test_within_player_terciles_skip_ineligible_players():
    result = within_player_tercile_lift(_player_history(1, [1.0, 2.0], [True, False]), min_matches=9)
    assert result["players"] == 0
    assert np.isnan(result["lift"])


def test_describe_reports_correlation_and_counts():
    impacts = [10.0, 20.0, 30.0, 40.0]
    wins = [False, False, True, True]
    result = describe(_player_history(1, impacts, wins))
    assert result["n"] == 4
    assert result["point_biserial"] > 0.8
    assert result["win_rate"] == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stage0_cohorts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.impact_stage0'`

- [ ] **Step 3: Write minimal implementation**

```python
# webapp/app/services/impact_stage0.py
"""Stage 0: what does Impact, exactly as it ships today, actually say about
winning?

This runs BEFORE any fitting and uses the CURRENT stored scores -- which
means the `realized` swing variant, since that is what the live scorer
wrote. Stage 0 describes the shipped metric rather than feeding a
forward-looking fit, so the leakage constraint does not apply here.

Cohorts are not optional. Measured 2026-09-01: 7,814 of 8,251 players
(94.7%) have exactly one match, so a naive all-player within-person
calculation is almost entirely rows whose centred Impact is 0 by
construction.
"""

from dataclasses import dataclass

import numpy as np

from app.services.stats_math import point_biserial, tercile_buckets

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


def filter_cohort(player_matches, min_matches: int) -> list[PlayerMatch]:
    counts: dict[int, int] = {}
    for row in player_matches:
        counts[row.player_id] = counts.get(row.player_id, 0) + 1
    return [row for row in player_matches if counts[row.player_id] >= min_matches]


def describe(player_matches) -> dict:
    impacts = np.array([r.avg_impact for r in player_matches], dtype=float)
    wins = np.array([1 if r.won else 0 for r in player_matches], dtype=int)
    if len(impacts) == 0:
        return {"n": 0, "point_biserial": float("nan"), "win_rate": float("nan")}
    return {
        "n": len(impacts),
        "point_biserial": point_biserial(impacts, wins),
        "win_rate": float(wins.mean()),
        "mean_impact_in_wins": float(impacts[wins == 1].mean()) if (wins == 1).any() else float("nan"),
        "mean_impact_in_losses": float(impacts[wins == 0].mean()) if (wins == 0).any() else float("nan"),
    }


def within_player_tercile_lift(player_matches, min_matches: int | None = None) -> dict:
    """Terciles computed WITHIN each player, then pooled -- the form the
    player page will display ("a top-third game for me"), not global
    terciles after centering.

    Player means, eligibility and tercile boundaries are all derived from
    whatever rows are passed in, so a bootstrap resample recomputes them
    rather than reusing fixed ones.
    """
    threshold = COHORT_RULES["per_player_tercile"] if min_matches is None else min_matches

    by_player: dict[int, list[PlayerMatch]] = {}
    for row in player_matches:
        by_player.setdefault(row.player_id, []).append(row)

    top_wins = top_total = bottom_wins = bottom_total = 0
    eligible = 0
    for rows in by_player.values():
        if len(rows) < threshold:
            continue
        eligible += 1
        buckets = tercile_buckets([r.avg_impact for r in rows])
        for row, bucket in zip(rows, buckets):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stage0_cohorts.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_stage0.py webapp/tests/test_stage0_cohorts.py
git commit -m "Add Stage 0 descriptive statistics with mandatory cohorts"
```

---

### Task 13: The three yardsticks and the CLI

**Files:**
- Create: `webapp/scripts/evaluate_impact.py`
- Modify: `webapp/app/services/impact_eval.py` (add `load_all_observations`, `load_player_matches`, the yardsticks)
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: everything from Tasks 5-12.
- Produces: `load_all_observations(db, use_realized_swing=False) -> list[RoundObservation]`; `load_player_matches(db) -> list[PlayerMatch]`; `Candidate` dataclass with `name: str`, `feature_names: list[str]`, `weights: list[float]`; `CURRENT_IMPACT_CANDIDATE`, `BASELINE_CANDIDATES`; `yardstick_first_half`, `yardstick_full_match`, `yardstick_forward_rounds`, each `(observations, candidate) -> tuple[list[float], list[int], list[int]]` returning `(scores, labels, match_ids)`; `yardstick_matrix(observations, candidates, draws, seed) -> dict`; CLI entry point.

**The three yardsticks, scored out-of-fold and always with the baselines alongside:**

1. **First half -> match outcome.** A single number — not split by attack/defense, because `attacking_team_for_round` makes every first-half row attack-first for team A.
2. **Full match -> match outcome.** Read only as the **gap over kill differential** on the identical scale; the absolute figure is inflated because the features contain the outcome's own kills.
3. **Round N -> rounds N+2 onward** (within the same half). Highest `n`, tightest intervals, and the one that catches within-half econ carryover.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
def test_load_all_observations_excludes_surrender_rounds_in_query():
    """Guards the constraint rather than the DB: the loader must reference
    the shared surrender predicate, not hand-roll its own filter."""
    import inspect

    from app.services import impact_eval

    source = inspect.getsource(impact_eval.load_all_observations)
    assert "NOT_A_SURRENDER_ROUND" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k load_all -v`
Expected: FAIL with `AttributeError: module 'app.services.impact_eval' has no attribute 'load_all_observations'`

- [ ] **Step 3: Write the loader**

```python
# append to webapp/app/services/impact_eval.py
from sqlalchemy.orm import selectinload

from app.models import Match, Round
from app.scoring.impact import build_impact_rows_for_match
from app.services.surrender_rounds import NOT_A_SURRENDER_ROUND


def load_all_observations(db, use_realized_swing: bool = False) -> list[RoundObservation]:
    """Every match in the DB, replayed through the scorer so components are
    the ex-ante variant by default. Surrender placeholder rounds are
    excluded via the shared NOT_A_SURRENDER_ROUND predicate rather than a
    local filter, so this never drifts from the rest of the app."""
    match_ids = [
        mid
        for (mid,) in db.query(Match.id)
        .join(Round, Round.match_id == Match.id)
        .filter(NOT_A_SURRENDER_ROUND)
        .distinct()
        .all()
    ]

    observations: list[RoundObservation] = []
    for match_id in match_ids:
        match = (
            db.query(Match)
            .options(
                selectinload(Match.match_players),
                selectinload(Match.rounds).selectinload(Round.player_stats),
            )
            .filter(Match.id == match_id)
            .one()
        )
        rows = build_impact_rows_for_match(db, match_id, use_realized_swing=use_realized_swing)
        observations.extend(build_observations_for_match(match, rows))
    return observations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 30 tests

- [ ] **Step 5: Write the failing yardstick test**

```python
# append to webapp/tests/test_impact_eval.py
from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    CURRENT_IMPACT_CANDIDATE,
    Candidate,
    yardstick_first_half,
    yardstick_forward_rounds,
    yardstick_full_match,
)


def _twelve_round_match(match_id, damage_per_round, team_a_wins_match):
    obs = [
        _linear_obs(n, damage_per_round, True, team_a_wins_match, match_id=match_id)
        for n in range(1, 13)
    ]
    obs[-1].is_terminal = True
    return obs


def test_first_half_yardstick_scores_one_row_per_eligible_match():
    obs = _twelve_round_match(1, 10.0, True) + _twelve_round_match(2, -10.0, False)
    scores, labels, mids = yardstick_first_half(obs, CURRENT_IMPACT_CANDIDATE)
    assert len(scores) == 2
    assert labels == [1, 0]
    assert scores[0] > scores[1]


def test_first_half_yardstick_skips_incomplete_matches():
    short = [_linear_obs(n, 1.0, True, True, match_id=9) for n in range(1, 6)]
    scores, _, _ = yardstick_first_half(short, CURRENT_IMPACT_CANDIDATE)
    assert scores == []


def test_full_match_yardstick_uses_every_round():
    obs = _twelve_round_match(1, 10.0, True)
    half_scores, _, _ = yardstick_first_half(obs, CURRENT_IMPACT_CANDIDATE)
    full_scores, _, _ = yardstick_full_match(obs, CURRENT_IMPACT_CANDIDATE)
    assert full_scores[0] == half_scores[0]  # this match is only 12 rounds


def test_forward_rounds_yardstick_labels_later_rounds_only():
    """Round 1's label must come from rounds 3+, never rounds 1 or 2."""
    obs = [_linear_obs(n, 1.0, n > 2, True, match_id=1) for n in range(1, 13)]
    obs[-1].is_terminal = True
    scores, labels, _ = yardstick_forward_rounds(obs, CURRENT_IMPACT_CANDIDATE)
    assert labels[0] == 1  # rounds 3..12 all won by A


def test_baselines_include_kill_differential():
    assert any(c.name == "kill_diff" for c in BASELINE_CANDIDATES)
    assert all(isinstance(c, Candidate) for c in BASELINE_CANDIDATES)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k yardstick -v`
Expected: FAIL with `ImportError: cannot import name 'Candidate'`

- [ ] **Step 7: Write the yardstick implementation**

```python
# append to webapp/app/services/impact_eval.py
from app.scoring.impact import FACTOR_WEIGHTS


@dataclass
class Candidate:
    """A weighting to be scored. Baselines use the same shape as fitted
    weightings so every candidate goes through identical code."""

    name: str
    feature_names: list[str]
    weights: list[float]


_FACTOR_TOTAL = sum(FACTOR_WEIGHTS.values())
CURRENT_IMPACT_CANDIDATE = Candidate(
    name="current_impact",
    feature_names=FEATURE_COMPONENTS,
    weights=[
        1.0,
        FACTOR_WEIGHTS["econ"] / _FACTOR_TOTAL,
        FACTOR_WEIGHTS["time"] / _FACTOR_TOTAL,
        FACTOR_WEIGHTS["swing"] / _FACTOR_TOTAL,
    ],
)

BASELINE_CANDIDATES = [
    Candidate("kill_diff", ["kill_diff"], [1.0]),
    # Kills and deaths as SEPARATE features, never a K/D ratio -- the ratio is
    # undefined at zero deaths.
    Candidate("kills_and_deaths", ["kills", "deaths"], [1.0, -1.0]),
    Candidate("damage_only", ["damage"], [1.0]),
]


def _score_of(observation, candidate: Candidate) -> float:
    return sum(
        weight * _feature_value(observation, name)
        for name, weight in zip(candidate.feature_names, candidate.weights)
    )


def yardstick_first_half(observations, candidate: Candidate):
    """Y1. One row per eligible match: candidate score summed over rounds
    1-12 versus the match result. Not split by side -- every first-half row
    is attack-first for team A."""
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
    """Y2. Every round. Absolute discrimination here is inflated because the
    features contain the outcome's own kills -- the caller must read this
    only as a gap over the kill_diff baseline."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        if not obs or obs[0].match_won_by_team_a is None:
            continue
        scores.append(sum(_score_of(o, candidate) for o in obs))
        labels.append(1 if obs[0].match_won_by_team_a else 0)
        mids.append(match_id)
    return scores, labels, mids


def yardstick_forward_rounds(observations, candidate: Candidate):
    """Y3. Round N's score versus who won the majority of rounds N+2 onward,
    within the same half. Starting at N+2 keeps the immediately-following
    round -- the strongest post-round mediator -- out of the label."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_half: dict[int, list] = {}
        for o in obs:
            by_half.setdefault(_half_of(o.round_number), []).append(o)
        for half_obs in by_half.values():
            half_obs.sort(key=lambda o: o.round_number)
            for index, o in enumerate(half_obs):
                future = [
                    f for f in half_obs[index + 2 :] if f.round_won_by_team_a is not None
                ]
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


def yardstick_matrix(observations, candidates: list[Candidate], draws: int = 200, seed: int = 0) -> dict:
    """Every candidate x every yardstick, with cluster-bootstrapped CIs.
    Baselines are always present, so 'does Impact beat kill differential'
    is answerable from the table itself."""
    matrix: dict[str, dict] = {}
    for yardstick_name, fn in YARDSTICKS.items():
        matrix[yardstick_name] = {}
        for candidate in candidates:
            scores, labels, mids = fn(observations, candidate)
            if not scores:
                matrix[yardstick_name][candidate.name] = None
                continue
            groups: dict[int, list] = {}
            for s, l, m in zip(scores, labels, mids):
                groups.setdefault(m, []).append((s, l))

            def auc_of(sample):
                flat = [pair for rows in sample for pair in rows]
                return auc([p[0] for p in flat], [p[1] for p in flat])

            lo, hi = cluster_bootstrap_ci(auc_of, groups, draws=draws, seed=seed)

            # A raw candidate score is not a probability, so log loss needs a
            # calibration -- fit on the other folds, applied to the held-out
            # one, never fit on the rows it scores.
            folds = assign_folds(mids, n_folds=5, seed=seed)
            scores_arr = np.array(scores, dtype=float)
            labels_arr = np.array(labels, dtype=int)
            fold_of = np.array([folds[m] for m in mids])
            probs = np.zeros(len(scores_arr))
            for fold in range(5):
                test = fold_of == fold
                if not test.any() or test.all() or len(np.unique(labels_arr[~test])) < 2:
                    probs[test] = labels_arr[~test].mean() if (~test).any() else 0.5
                    continue
                calibration = platt_calibrate(scores_arr[~test], labels_arr[~test])
                probs[test] = apply_calibration(calibration, scores_arr[test])

            matrix[yardstick_name][candidate.name] = {
                "auc": auc(scores, labels),
                "auc_ci": [lo, hi],
                "log_loss": log_loss(probs, labels_arr),
                "n": len(labels),
                "matches": len(groups),
            }

        baseline = matrix[yardstick_name].get("kill_diff")
        if baseline:
            for name, cell in matrix[yardstick_name].items():
                if cell and name != "kill_diff":
                    cell["gap_over_kill_diff"] = cell["auc"] - baseline["auc"]
    return matrix
```

Add `cluster_bootstrap_ci` to the existing `from app.services.stats_math import ...` block.

- [ ] **Step 8: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, 35 tests

- [ ] **Step 9: Write the CLI**

```python
# webapp/scripts/evaluate_impact.py
"""Impact-vs-winning evaluation report.

Debug-only CLI, not exposed on any page. Follows scripts/validate_fight_ev.py's
conventions: prints a table, optionally writes JSON, and honours a
DATABASE_URL override so it can be pointed at another database.

Usage:
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --out report.json
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --stage0-only
    .venv\\Scripts\\python.exe scripts\\evaluate_impact.py --draws 500

Point at a specific database the same way every other script here does:
    $env:DATABASE_URL = (Get-Content .env.remote | Select-String DATABASE_URL).Line.Split('=',2)[1]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from app.db import SessionLocal
from app.services.impact_eval import (
    BASELINE_CANDIDATES,
    BASELINE_DAMAGE,
    CONTROLS_CONTEXT,
    CONTROLS_RESULT,
    CURRENT_IMPACT_CANDIDATE,
    Candidate,
    FEATURE_COMPONENTS,
    coefficient_diagnostics,
    fit_constrained_weights,
    first_half_target,
    forward_window_target,
    load_all_observations,
    run_nested_cv,
    yardstick_matrix,
)
from app.services.impact_stage0 import COHORT_RULES, describe, filter_cohort, within_player_tercile_lift
from app.services.stats_math import auc, cluster_bootstrap_ci, log_loss

L2_GRID = [0.01, 0.1, 1.0, 10.0]
K_GRID = [2, 3, 4]
GAMMA_GRID = [0.5, 0.7, 0.9]
MATCH_WEIGHT_GRID = [0.0, 0.5, 1.0]

# The control ladder. The 3 -> 4 increment is the headline result: it is the
# only number showing Impact carries information beyond who won the round and
# what they could afford next.
LADDER = [
    ("1_round_result", CONTROLS_RESULT),
    ("2_plus_context", CONTROLS_RESULT + CONTROLS_CONTEXT),
    ("3_plus_damage", CONTROLS_RESULT + CONTROLS_CONTEXT + BASELINE_DAMAGE),
    ("4_plus_components", CONTROLS_RESULT + CONTROLS_CONTEXT + FEATURE_COMPONENTS),
]


def _oof(dataset, seed):
    out = run_nested_cv(dataset, l2_grid=L2_GRID, seed=seed)
    if len(out["oof_labels"]) == 0:
        return None
    return out


def _metrics(out, draws, seed):
    scores, labels, mids = out["oof_scores"], out["oof_labels"], out["oof_match_ids"]
    groups: dict[int, list] = {}
    for s, l, m in zip(scores, labels, mids):
        groups.setdefault(int(m), []).append((s, l))

    def auc_of(sample):
        flat = [pair for rows in sample for pair in rows]
        return auc([p[0] for p in flat], [p[1] for p in flat])

    lo, hi = cluster_bootstrap_ci(auc_of, groups, draws=draws, seed=seed)
    return {
        "auc": auc(scores, labels),
        "auc_ci": [lo, hi],
        "log_loss": log_loss(scores, labels),
        "n": int(len(labels)),
        "matches": len(groups),
    }


def _stage0(db, draws, seed):
    from app.services.impact_eval import load_player_matches

    rows = load_player_matches(db)
    report = {"all": describe(rows), "cohorts": {}}
    for name, threshold in COHORT_RULES.items():
        cohort = filter_cohort(rows, threshold)
        report["cohorts"][name] = {
            "min_matches": threshold,
            "players": len({r.player_id for r in cohort}),
            **describe(cohort),
        }
    report["within_player_terciles"] = within_player_tercile_lift(rows)

    groups: dict[int, list] = {}
    for r in rows:
        groups.setdefault(r.match_id, []).append(r)

    def lift_of(sample):
        flat = [r for rows_ in sample for r in rows_]
        return within_player_tercile_lift(flat)["lift"]

    lo, hi = cluster_bootstrap_ci(lift_of, groups, draws=draws, seed=seed)
    report["within_player_terciles"]["lift_ci"] = [lo, hi]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    parser.add_argument("--draws", type=int, default=500, help="bootstrap draws (default 500)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stage0-only", action="store_true", help="skip all fitting")
    parser.add_argument("--include-realized", action="store_true",
                        help="also score every candidate on realized (shipped) components")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = {"stage0": _stage0(db, args.draws, args.seed)}
        print("== Stage 0: Impact as it ships today (realized swing) ==")
        print(json.dumps(report["stage0"], indent=2, default=float))

        if not args.stage0_only:
            observations = load_all_observations(db, use_realized_swing=False)
            report["n_observations"] = len(observations)

            t1 = first_half_target(observations, FEATURE_COMPONENTS)
            t1_out = _oof(t1, args.seed)
            report["T1"] = {
                "n_matches": int(len(t1.y)),
                "metrics": _metrics(t1_out, args.draws, args.seed) if t1_out else None,
                "fold_coefficients": [b.tolist() for b in (t1_out["fold_betas"] if t1_out else [])],
                "constrained": vars(fit_constrained_weights(t1.X, t1.y, t1.w, FEATURE_COMPONENTS))
                if len(t1.y)
                else None,
            }

            sweep = []
            for k in K_GRID:
                for gamma in GAMMA_GRID:
                    for mw in MATCH_WEIGHT_GRID:
                        ds = forward_window_target(observations, FEATURE_COMPONENTS, k=k, gamma=gamma, match_weight=mw)
                        out = _oof(ds, args.seed)
                        if out:
                            sweep.append({"k": k, "gamma": gamma, "match_weight": mw, **_metrics(out, 50, args.seed)})
            report["T2_sweep"] = sweep
            best = min(sweep, key=lambda s: s["log_loss"]) if sweep else None
            report["T2_selected"] = best

            ladder = {}
            if best:
                for name, features in LADDER:
                    ds = forward_window_target(
                        observations, features, k=best["k"], gamma=best["gamma"], match_weight=best["match_weight"]
                    )
                    out = _oof(ds, args.seed)
                    ladder[name] = _metrics(out, args.draws, args.seed) if out else None
                if ladder.get("3_plus_damage") and ladder.get("4_plus_components"):
                    ladder["headline_delta_auc"] = (
                        ladder["4_plus_components"]["auc"] - ladder["3_plus_damage"]["auc"]
                    )
            report["T2_control_ladder"] = ladder

            # Targets x yardsticks. Fitted candidates come from the constrained
            # search (the shipped parameterization), so they are directly
            # comparable to current_impact.
            candidates = [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES]
            t1_weights = report["T1"]["constrained"]
            if t1_weights:
                candidates.append(
                    Candidate(
                        "fitted_T1",
                        FEATURE_COMPONENTS,
                        [
                            t1_weights["damage_multiplier"],
                            t1_weights["econ"] / 3.0,
                            t1_weights["time"] / 3.0,
                            t1_weights["swing"] / 3.0,
                        ],
                    )
                )
            if best:
                ds = forward_window_target(
                    observations, FEATURE_COMPONENTS, k=best["k"], gamma=best["gamma"],
                    match_weight=best["match_weight"],
                )
                t2_weights = fit_constrained_weights(ds.X, ds.y, ds.w, FEATURE_COMPONENTS)
                report["T2_constrained"] = vars(t2_weights)
                candidates.append(
                    Candidate(
                        "fitted_T2",
                        FEATURE_COMPONENTS,
                        [
                            t2_weights.damage_multiplier,
                            t2_weights.econ / 3.0,
                            t2_weights.time / 3.0,
                            t2_weights.swing / 3.0,
                        ],
                    )
                )

            report["yardstick_matrix_ex_ante"] = yardstick_matrix(
                observations, candidates, draws=args.draws, seed=args.seed
            )
            report["diagnostics_T1"] = coefficient_diagnostics(t1, draws=args.draws, seed=args.seed) if len(t1.y) else None

            if args.include_realized:
                # The shipped scorer computes the realized variant, so the
                # fitted weights would land on THAT formula if adopted. Scoring
                # the same candidates on realized components makes the gap
                # visible instead of silent.
                realized_obs = load_all_observations(db, use_realized_swing=True)
                report["yardstick_matrix_realized"] = yardstick_matrix(
                    realized_obs, candidates, draws=args.draws, seed=args.seed
                )

            print("\n== T1: first half -> match result ==")
            print(json.dumps(report["T1"], indent=2, default=float))
            print("\n== T2 control ladder (3 -> 4 is the headline) ==")
            print(json.dumps(ladder, indent=2, default=float))
            print("\n== Targets x yardsticks (ex-ante components) ==")
            print(json.dumps(report["yardstick_matrix_ex_ante"], indent=2, default=float))

        if args.out:
            args.out.write_text(json.dumps(report, indent=2, default=float))
            print(f"\nwrote {args.out}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add the Stage 0 loader to `impact_eval.py`:

```python
# append to webapp/app/services/impact_eval.py
from app.models import ImpactScore, MatchPlayer
from app.services.impact_stage0 import PlayerMatch


def load_player_matches(db) -> list[PlayerMatch]:
    """One row per (player, match): their average stored Impact across the
    match, and whether their team won. Uses STORED scores -- Stage 0
    describes the shipped metric."""
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
        .group_by(MatchPlayer.player_id, MatchPlayer.match_id, MatchPlayer.team,
                  Match.team1_rounds_won, Match.team2_rounds_won)
        .all()
    )

    out: list[PlayerMatch] = []
    for player_id, match_id, team, avg_impact, won1, won2 in rows:
        if won1 == won2:
            continue  # ties excluded from every denominator, same as match_win elsewhere
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

Add `from sqlalchemy import func` to the module's imports.

- [ ] **Step 10: Run the CLI end to end**

Run: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --stage0-only`
Expected: prints the Stage 0 block with a non-zero `n`, the four cohorts, and a tercile lift with a CI.

Then the full run: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --out ..\scratch-impact-report.json`
Expected: completes and writes the JSON. This replays every match through the scorer, so expect it to take minutes.

- [ ] **Step 11: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/scripts/evaluate_impact.py webapp/tests/test_impact_eval.py
git commit -m "Add the three yardsticks and the evaluate_impact CLI"
```

---

### Task 14: Stage B — the V(state) win-probability model

**GATE: do not start Task 14 until Task 13's report has been produced and read.** Stage A's component breakdown may change what the state should condition on, which is why the spec wanted Stage B planned separately. If Stage A shows one component doing all the work, revisit this task's feature set before implementing.

**Files:**
- Create: `webapp/app/services/win_probability.py`
- Test: `webapp/tests/test_win_probability.py`

**Interfaces:**
- Consumes: `fit_logistic`, `predict_proba` (Task 2); `RoundObservation` (Task 7).
- Produces: `StateFeatures` dataclass with `score_diff: int`, `rounds_played: int`, `attacking_is_team_a: bool`; `state_before(observation) -> StateFeatures`; `state_after(observation) -> StateFeatures`; `fit_value_model(observations, l2=1.0) -> np.ndarray`; `value_of(beta, state) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_win_probability.py
"""V(state) = P(team A wins the match | state). Stage B uses it for
leverage-weighted ATTRIBUTION, not independent validation -- dV is
dominated by the round's own outcome, which the features nearly determine."""

import numpy as np

from app.services.impact_eval import RoundObservation
from app.services.win_probability import (
    fit_value_model,
    state_after,
    state_before,
    value_of,
)


def _obs(round_number, score_diff_before, won_by_a, match_won, match_id=1):
    return RoundObservation(
        match_id=match_id, round_id=round_number, round_number=round_number,
        damage=0.0, econ_impact=0.0, time_impact=0.0, swing_impact=0.0,
        kills=0.0, deaths=0.0, kill_diff=0.0,
        score_diff_before=score_diff_before, attacking_is_team_a=True,
        loadout_diff=0.0, full_buy_count_diff=0,
        round_won_by_team_a=won_by_a, match_won_by_team_a=match_won, is_terminal=False,
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


def test_value_model_learns_that_a_lead_is_good():
    observations = []
    for match_id in range(200):
        leading = match_id % 2 == 0
        diff = 5 if leading else -5
        observations.append(_obs(10, diff, True, leading, match_id=match_id))
    beta = fit_value_model(observations)
    ahead = value_of(beta, state_before(_obs(10, 5, True, True)))
    behind = value_of(beta, state_before(_obs(10, -5, True, False)))
    assert ahead > behind
    assert 0.0 <= ahead <= 1.0


def test_value_of_returns_a_probability():
    observations = [_obs(3, i % 3 - 1, True, i % 2 == 0, match_id=i) for i in range(60)]
    beta = fit_value_model(observations)
    v = value_of(beta, state_before(observations[0]))
    assert 0.0 <= v <= 1.0
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
independent predictive validation. V(after) - V(before) is dominated by the
round's own outcome, which the round's own kills nearly determine, so it
does not escape the tautology. Its value is that a swing in a close, late
round is worth more than the same swing in a decided one.

Econ state is deliberately absent from the first version: the held-out log
loss delta from adding it is the quantitative answer to "how much does econ
carryover matter", and that number only exists if a base model is measured
first. When econ is added, V(before) and V(after) will need exact
definitions of which economy snapshot each reads -- that is where a second
leakage path could open.
"""

from dataclasses import dataclass

import numpy as np

from app.services.stats_math import fit_logistic, predict_proba


@dataclass
class StateFeatures:
    score_diff: int
    rounds_played: int
    attacking_is_team_a: bool


def state_before(observation) -> StateFeatures:
    return StateFeatures(
        score_diff=observation.score_diff_before,
        rounds_played=observation.round_number - 1,
        attacking_is_team_a=observation.attacking_is_team_a,
    )


def state_after(observation) -> StateFeatures:
    delta = 0
    if observation.round_won_by_team_a is True:
        delta = 1
    elif observation.round_won_by_team_a is False:
        delta = -1
    return StateFeatures(
        score_diff=observation.score_diff_before + delta,
        rounds_played=observation.round_number,
        attacking_is_team_a=observation.attacking_is_team_a,
    )


def _design_row(state: StateFeatures) -> list[float]:
    return [
        float(state.score_diff),
        float(state.rounds_played),
        1.0 if state.attacking_is_team_a else 0.0,
    ]


def fit_value_model(observations, l2: float = 1.0) -> np.ndarray:
    """MUST be called inside each outer training fold. Fitting once over all
    matches and then running outer CV would leak evaluation outcomes into
    the target."""
    rows, labels = [], []
    for o in observations:
        if o.match_won_by_team_a is None:
            continue
        rows.append(_design_row(state_before(o)))
        labels.append(1.0 if o.match_won_by_team_a else 0.0)
    if not rows:
        return np.zeros(4)
    return fit_logistic(np.array(rows, dtype=float), np.array(labels), l2=l2)


def value_of(beta: np.ndarray, state: StateFeatures) -> float:
    return float(predict_proba(beta, np.array([_design_row(state)], dtype=float))[0])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_win_probability.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/win_probability.py webapp/tests/test_win_probability.py
git commit -m "Add V(state) win-probability model for Stage B leverage weighting"
```

---

### Task 15: Stage B — leverage-weighted WPA target

**Files:**
- Modify: `webapp/app/services/impact_eval.py`, `webapp/scripts/evaluate_impact.py`
- Test: `webapp/tests/test_impact_eval.py`

**Interfaces:**
- Consumes: `fit_value_model`, `state_before`, `state_after`, `value_of` (Task 14); `FitDataset` (Task 9).
- Produces: `wpa_target(observations, feature_names, value_beta) -> FitDataset`.

**Formulation, corrected from an earlier draft:** signed `dV` lies in [-1, 1] and is not a probability, so it cannot be the `y` of a logistic fit. Instead the **label is whether team A won the round** (0/1) and the **sample weight is `abs(dV)`**.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_impact_eval.py
def test_wpa_target_labels_are_round_outcomes_and_weights_are_leverage():
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    obs = []
    for match_id in range(40):
        leading = match_id % 2 == 0
        o = _linear_obs(10, 1.0, leading, leading, match_id=match_id)
        o.score_diff_before = 5 if leading else -5
        obs.append(o)

    beta = fit_value_model(obs)
    dataset = wpa_target(obs, FEATURE_COMPONENTS, beta)

    assert set(np.unique(dataset.y)) <= {0.0, 1.0}, "labels must be round outcomes"
    assert np.all(dataset.w >= 0.0), "leverage weights are magnitudes"
    assert np.all(dataset.w <= 1.0), "abs(dV) cannot exceed 1"


def test_wpa_target_skips_unresolved_rounds():
    from app.services.impact_eval import wpa_target
    from app.services.win_probability import fit_value_model

    resolved = _linear_obs(5, 1.0, True, True, match_id=1)
    unresolved = _linear_obs(6, 1.0, None, True, match_id=1)
    beta = fit_value_model([resolved])
    dataset = wpa_target([resolved, unresolved], FEATURE_COMPONENTS, beta)
    assert len(dataset.y) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k wpa -v`
Expected: FAIL with `ImportError: cannot import name 'wpa_target'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to webapp/app/services/impact_eval.py
from app.services.win_probability import state_after, state_before, value_of


def wpa_target(observations, feature_names: list[str], value_beta) -> FitDataset:
    """Stage B: label = did team A win this round, weight = leverage.

    Signed dV is in [-1, 1] and is not a probability, so it cannot be the
    `y` of a logistic fit. Using abs(dV) as a SAMPLE WEIGHT instead makes
    the fit care more about high-leverage rounds without pretending a
    signed swing is a likelihood.

    `value_beta` MUST have been fit on the training fold only.
    """
    rows, ys, ws, mids = [], [], [], []
    for o in observations:
        if o.round_won_by_team_a is None:
            continue
        leverage = abs(value_of(value_beta, state_after(o)) - value_of(value_beta, state_before(o)))
        rows.append(_row(o, feature_names))
        ys.append(1.0 if o.round_won_by_team_a else 0.0)
        ws.append(leverage)
        mids.append(o.match_id)

    if not rows:
        return _empty_dataset(feature_names)
    return FitDataset(np.array(rows, dtype=float), np.array(ys), np.array(ws),
                      np.array(mids, dtype=int), list(feature_names))
```

- [ ] **Step 4: Wire Stage B into the CLI**

Add to `scripts/evaluate_impact.py`, inside `main()` after the T2 ladder block:

```python
            from app.services.impact_eval import wpa_target
            from app.services.win_probability import fit_value_model

            # NOTE: this fits the value model on ALL observations, which is
            # correct only for the descriptive attribution report below. Any
            # comparison of Stage B against T1/T2 must refit V(state) inside
            # each training fold -- see the spec.
            value_beta = fit_value_model(observations)
            wpa = wpa_target(observations, FEATURE_COMPONENTS, value_beta)
            wpa_out = _oof(wpa, args.seed)
            report["stage_b"] = {
                "framing": "attribution, not independent validation -- dV is "
                           "dominated by the round's own outcome",
                "value_model_beta": value_beta.tolist(),
                "metrics": _metrics(wpa_out, args.draws, args.seed) if wpa_out else None,
                "mean_leverage": float(np.mean(wpa.w)) if len(wpa.w) else None,
            }
            print("\n== Stage B: leverage-weighted attribution ==")
            print(json.dumps(report["stage_b"], indent=2, default=float))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, full suite green.

- [ ] **Step 6: Run the full report**

Run: `.\.venv\Scripts\python.exe scripts\evaluate_impact.py --out ..\scratch-impact-report.json`
Expected: completes, prints Stage 0, T1, the T2 ladder, and Stage B.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/scripts/evaluate_impact.py webapp/tests/test_impact_eval.py
git commit -m "Add leverage-weighted WPA target and wire Stage B into the CLI"
```

---

## After the plan

Read the report before designing anything user-facing. Specifically:

1. **Does the Task 0 gate pass?** If not, nothing downstream is meaningful.
2. **What is the 3 -> 4 delta in the T2 control ladder?** If it is near zero with a CI spanning zero, Impact's econ/time/swing machinery adds nothing beyond damage plus who won the round, and that is the headline finding.
3. **Do T1 and T2 agree on the constrained weights?** Disagreement is a finding to report, not a tie to break.
4. **Does Impact beat kill differential on any yardstick?** If not, say so plainly.
5. **Stage 0's tercile lift** is the number P2's player-page card will display. Its effect size determines whether that card is worth building at all.

P2 (player page) and P3 (squad page) each get their own spec, written from these numbers.

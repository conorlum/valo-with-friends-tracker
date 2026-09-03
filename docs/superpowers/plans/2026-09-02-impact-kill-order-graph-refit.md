# Stage C: Kill-Order Graph Refit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build internal tooling that refits `app/scoring/impact.py`'s hand-tuned `_KILL_ORDER_GRAPH` from data under the parent project's nested-CV discipline, reports honestly whether doing so helps, and changes nothing that ships.

**Architecture:** Four layers. A per-kill **extractor** (`kill_order_leverage`) replays every match through `impact.py`'s own state walk and emits two products — one signed row per round for fitting, one row per player-round for the player-level reads — decomposed so any candidate graph can be applied afterwards. A **curve layer** (`kill_order_curves`) holds the empirical swing table, the candidate parameterizations, and the recovery of a deployable graph from fitted coefficients. An **evaluation layer** (`kill_order_refit`) runs nested CV, the control ladder, the diagnostics and the verdicts. A **CLI** prints and dumps the report.

The design rests on one fact, verified numerically in the spec: Impact is *exactly linear* in the edge weights before rounding. That is why the observation unit stays one differential row per round and every candidate is scored on the same design matrix — only the coefficients differ.

**Tech Stack:** Python 3, SQLAlchemy 2.0, numpy, pytest. No scipy, no sklearn, no pandas.

**Spec:** `docs/superpowers/specs/2026-09-02-impact-kill-order-graph-refit-design.md`

## Global Constraints

- **numpy only.** `scipy` is installed locally but absent from `requirements.txt`, which `render.yaml` installs from. Do not add it, and do not import it.
- **No new tables, no Alembic migrations.**
- **No change to `_KILL_ORDER_GRAPH`'s values, and no change to `impact.py`'s formula.** This plan reads `impact.py`; it does not edit it. Adopting any refit graph is a separate, deliberate act.
- **`stats_math.py` gains exactly one thing: a per-coefficient penalty mask on `fit_logistic` (Task 1).** Two earlier drafts got this wrong in opposite directions - one added an `offset` (which shrinks `q`, not the deployable graph), the other claimed no primitive was needed at all. Both were checked numerically. Nothing else in `stats_math` changes.
- **Nothing in this plan may be imported by `app/main.py`, any router, or any template.**
- **Every extraction query excludes surrender placeholder rounds** using `app.services.surrender_rounds.NOT_A_SURRENDER_ROUND`.
- **Ex-ante components only.** Every factor comes through `use_realized_swing=False` semantics. `_realized_econ_swing_factor` reads round N+1 and would leak.
- **Nothing may be selected, fitted, or calibrated on data it is later scored against.** Folds and bootstrap resamples are always by match; all rounds of a match live in the same fold. This covers L2, the `dP` table, G1a's construction normalization, the G3 shrinkage prior, and probability calibration.
- **Every objective is weighted.** Sample weights drive the fit, so they also drive selection, reporting and bootstrapping.
- **The frozen targets are reused verbatim** from the parent project: `PRIMARY_T1`, `PRIMARY_T2` (k=3, gamma=0.7, match_weight=1.0), and the WPA target. They are never re-tuned.
- **Run everything from `webapp/`** with `.\.venv\Scripts\python.exe`. Tests: `.\.venv\Scripts\python.exe -m pytest tests/<file> -v`.
- **Test style:** plain ORM construction with no DB session, following `tests/test_player_profile_types.py`. **Tasks 5 and 13 require a live Postgres** (`docker compose -p valomaths-private up -d`) and must skip cleanly when unreachable; every other task's tests are pure.

## Three rules the whole design hangs on

1. **A fit is not a graph until it is recovered.** The estimator is
   `eta = controls·gamma + d·damage_diff + SUM_k q_k·x_r[k]`; the deployable
   graph is `b_k = q_k / d`, requiring `d > 0`; and every yardstick scores the
   recovered candidate `S_r = damage_diff + SUM_k b_k·x_r[k]`, never `eta`.
   This applies to **every** fitted candidate including G5, whose 18 numbers are
   `q`s and all divide by `d`.
2. **Shrinking toward a prior happens in `q` space, not `b` space.** Because
   `b = q/d`, an offset that shrinks `q` toward `b_prior` drives the graph to
   `b_prior/d`. Task 7 folds the prior into the damage column instead. This was
   measured, not reasoned: the naive version returns `b ≈ 73` where the prior is
   `0.6`.
3. **Primary comparisons are predeclared.** P1 (G2 vs `current_graph`, T2) and
   P2 (G3 vs `current_graph`, T2) are co-primary at **97.5%**; P3 (B2 vs
   `stage_a_exact`, T2) is Family B's single primary at 95%. Everything else in
   the ladder is fitted, scored and printed, and cannot declare success.

## Prerequisite outside this plan

The shared Stage A / Stage C yardstick matrix requires **Stage A re-run under
`stable_folds` on Stage C's snapshot**. The parent project's committed results
used the old permutation `assign_folds`, so an identical match set can still
carry a different fold assignment. This is a re-run of existing tested code, not
new work, but until it happens the report prints the two stages as separate
tables (Task 16 enforces this and must not be worked around).

## File structure

| File | Responsibility |
|---|---|
| `app/services/stats_math.py` *(modify)* | add a `penalty` mask argument to `fit_logistic`; nothing else changes |
| `app/services/impact_eval.py` *(modify)* | add `stable_folds`, `dataset_fingerprint`, `fold_mapping_hash`; `assign_folds` keeps its current behaviour |
| `app/services/kill_order_leverage.py` *(new)* | per-kill term decomposition, team and player leverage products, DB loader |
| `app/services/kill_order_curves.py` *(new)* | `dP` estimation, candidate parameterizations, normalization, recovery, deployability |
| `app/services/kill_order_refit.py` *(new)* | nested CV, control ladder, diagnostics, stability, verdicts, report assembly |
| `scripts/evaluate_kill_order.py` *(new)* | CLI: prints the report, `--out` dumps JSON |
| `tests/test_impact_eval.py` *(append)* | stable folds, fingerprint, mapping hash |
| `tests/test_kill_order_leverage.py` *(new)* | state walk, self-kills, fallback, trade discount, team/player consistency, reconstruction and linearity gates |
| `tests/test_kill_order_curves.py` *(new)* | `dP` cross-fitting, nesting, normalization, recovery, deployability, the G3 shrinkage direction |
| `tests/test_kill_order_refit.py` *(new)* | CV wiring, ladder, stability, verdicts, matrix refusal |

## Task map

| Tasks | Layer |
|---|---|
| 1-2 | Shared primitives: penalty mask, fold identity |
| 3-5 | Extraction: per-kill terms, team/player products (incl. trade discount), the gates against `impact.py` |
| 6-9 | Curves: `dP` + G1a, G0-G2 + recovery, G3-G4, Family B ladder B1-B3 |
| 10-12 | Evaluation: nested CV, control ladder, diagnostics + stability |
| 13-16 | Reporting: Stage C0, player-level, verdicts, CLI |
| 17-20 | Completing the product: Family B orchestration, all three targets, the yardstick matrix, sensitivities and an end-to-end acceptance test |

**Tasks 17-20 are not optional polish.** An earlier draft stopped at 16 and
listed what was missing in a postscript. That was wrong: without them
`verdict_report` is handed a `primaries` dict with no `P3` key and the full run
raises `KeyError`. The product the spec describes begins to exist at Task 20.

---

### Task 1: `fit_logistic` gains a per-coefficient penalty mask

**Files:**
- Modify: `webapp/app/services/stats_math.py:134-181`
- Test: `webapp/tests/test_stats_math.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `fit_logistic(X, y, weights=None, l2=1.0, max_iter=100, tol=1e-9, penalty=None)`. `penalty` is a per-coefficient multiplier on the ridge diagonal, length `X.shape[1]`, defaulting to all ones. The intercept stays unpenalised regardless.

**Why this is needed, measured rather than argued.** Task 8's G3 shrinks the *deployable* graph toward a prior. The parameterization is `q = d*b_prior + delta`, fitted as a composite damage column plus free `delta` columns, so `b = b_prior + delta/d`. But `fit_logistic` penalises **every** non-intercept coefficient, `d` included — so a large `l2` drives `d` and `delta` toward zero together and `delta/d` is a ratio of two vanishing quantities. Measured on synthetic data with a true `d` of 3 and a prior of 0.6 at every parameter:

| `l2` | `d` (penalised) | recovered `b` | `d` (exempt) | recovered `b` |
|---|---|---|---|---|
| 1e3 | 0.531 | 1.250, -0.122, 1.102, 0.493 | 1.091 | 0.848, 0.121, 0.775, 0.439 |
| 1e5 | 0.016 | 1.140, 0.334, 1.045, 0.703 | 0.960 | 0.605, 0.591, 0.604, 0.597 |
| 1e7 | 0.0002 | **1.136, 0.345, 1.043, 0.708** | 0.959 | **0.600, 0.600, 0.600, 0.600** |

With `d` penalised the graph never reaches the prior. With it exempt it lands exactly. A mask is the smallest change that expresses the intended estimator.

Two earlier drafts got this wrong in opposite directions: one added an `offset` argument (which shrinks `q`, not the deployable graph), the other concluded no primitive was needed at all. Both were checked numerically before being replaced.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_stats_math.py

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -k penalty -v`
Expected: FAIL — `fit_logistic() got an unexpected keyword argument 'penalty'`

- [ ] **Step 3: Add the mask**

Change only the signature, the validation, and how the penalty matrix is built. The IRLS loop is untouched apart from using the new matrix name.

```python
def fit_logistic(
    X, y, weights=None, l2: float = 1.0, max_iter: int = 100, tol: float = 1e-9,
    penalty=None,
) -> np.ndarray:
    """Weighted IRLS logistic regression with a ridge penalty.

    ... existing docstring paragraphs unchanged ...

    `penalty` is an optional per-coefficient multiplier on the ridge
    diagonal, length X.shape[1]. A zero entry leaves that coefficient
    unpenalised. It exists because shrinking a RATIO toward a prior --
    b = prior + delta/d -- requires penalising delta while leaving d free:
    penalising both drives them to zero together and the ratio never
    converges on the prior. The intercept is unpenalised regardless.
    """
    X, y, w = _validate_xy(X, y, weights)
    n, p = X.shape

    if penalty is None:
        mask = np.ones(p)
    else:
        mask = np.asarray(penalty, dtype=float).ravel()
        if mask.shape[0] != p:
            raise ValueError(f"penalty has length {mask.shape[0]}, expected {p}")
        if np.any(mask < 0) or not np.all(np.isfinite(mask)):
            raise ValueError("penalty entries must be finite and non-negative")

    design = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(p + 1)
    penalty_matrix = np.diag(np.concatenate([[0.0], l2 * mask]))
```

Then replace the two uses of the old `penalty` variable in the loop with
`penalty_matrix`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_stats_math.py -v`
Expected: PASS, including every pre-existing test — this argument must be purely additive.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/stats_math.py webapp/tests/test_stats_math.py
git commit -m "Add a per-coefficient penalty mask to fit_logistic" -m "Shrinking a ratio toward a prior needs the denominator left unpenalised. Measured: with the damage coefficient penalised the recovered graph stalls at 1.136 where the prior is 0.6; exempt, it lands on 0.600." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Fold identity — stable folds, fingerprint, mapping hash

**Files:**
- Modify: `webapp/app/services/impact_eval.py:280-288`
- Test: `webapp/tests/test_impact_eval.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `stable_folds(match_ids, n_folds=5, seed=0) -> dict[int, int]`, `dataset_fingerprint(match_ids) -> str`, `fold_mapping_hash(folds) -> str`. `assign_folds` keeps its exact current behaviour and is not modified.

**Why this exists:** `assign_folds` sorts the unique match ids, draws `rng.permutation(len(unique))`, then assigns by position — so the mapping depends on the *membership and size* of the match set. Add, drop or exclude one match and every assignment can move, same seed or not.

**Why the fingerprint alone is not enough:** the parent project's committed results were produced with the old permutation algorithm. An identical match set can therefore carry an entirely different fold assignment — same fingerprint, different folds, a matrix that looks comparable and is not. The mapping hash is what actually catches that.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_impact_eval.py

from app.services.impact_eval import (
    assign_folds,
    dataset_fingerprint,
    fold_mapping_hash,
    stable_folds,
)


def test_assign_folds_moves_when_the_match_set_changes():
    """Documents the defect stable_folds exists to fix. If this ever stops
    holding, assign_folds changed and this plan's premise needs rechecking."""
    base = list(range(1, 51))
    a = assign_folds(base, n_folds=5, seed=0)
    b = assign_folds(base + [999], n_folds=5, seed=0)
    assert [m for m in base if a[m] != b[m]], "expected a reshuffle"


def test_stable_folds_are_membership_independent():
    base = list(range(1, 51))
    a = stable_folds(base, n_folds=5, seed=0)
    b = stable_folds(base + [999], n_folds=5, seed=0)
    for m in base:
        assert a[m] == b[m]
    subset = stable_folds([m for m in base if m % 7], n_folds=5, seed=0)
    for m in subset:
        assert subset[m] == a[m]


def test_stable_folds_respect_the_seed_and_fold_count():
    ids = list(range(1, 201))
    assert stable_folds(ids, seed=0) != stable_folds(ids, seed=1)
    assert set(stable_folds(ids, n_folds=5, seed=0).values()) <= set(range(5))
    assert set(stable_folds(ids, n_folds=3, seed=0).values()) <= set(range(3))


def test_stable_folds_are_reasonably_balanced():
    ids = list(range(1, 1152))
    counts: dict[int, int] = {}
    for fold in stable_folds(ids, n_folds=5, seed=0).values():
        counts[fold] = counts.get(fold, 0) + 1
    assert len(counts) == 5
    assert max(counts.values()) - min(counts.values()) < 0.1 * len(ids) / 5


def test_stable_folds_survive_a_process_restart():
    """Python's built-in hash() is randomized per process, so a mapping
    built on hash() would differ between runs and silently invalidate every
    cached comparison. Asserting equality inside ONE process cannot detect
    that -- this launches a second interpreter and compares."""
    import subprocess
    import sys

    program = (
        "from app.services.impact_eval import stable_folds;"
        "print(sorted(stable_folds(range(1, 40), n_folds=5, seed=0).items()))"
    )
    runs = [
        subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                       check=True).stdout
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert runs[0].strip() == str(sorted(stable_folds(range(1, 40), n_folds=5, seed=0).items()))


def test_dataset_fingerprint_is_order_insensitive_and_content_sensitive():
    assert dataset_fingerprint([3, 1, 2]) == dataset_fingerprint([1, 2, 3])
    assert dataset_fingerprint([1, 1, 2, 3]) == dataset_fingerprint([1, 2, 3])
    assert dataset_fingerprint([1, 2, 3]) != dataset_fingerprint([1, 2, 4])
    assert dataset_fingerprint([1, 2, 3]) != dataset_fingerprint([1, 2, 3, 4])


def test_fold_mapping_hash_catches_a_same_set_different_folds_collision():
    """The whole point: the two mappings below cover the same matches, so
    the dataset fingerprint agrees, but the assignments differ."""
    ids = list(range(1, 51))
    stable = stable_folds(ids, n_folds=5, seed=0)
    permuted = assign_folds(ids, n_folds=5, seed=0)
    assert dataset_fingerprint(ids) == dataset_fingerprint(list(permuted))
    assert fold_mapping_hash(stable) != fold_mapping_hash(permuted)
    assert fold_mapping_hash(stable) == fold_mapping_hash(dict(stable))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -k "stable_folds or fingerprint or mapping_hash or assign_folds_moves" -v`
Expected: FAIL — `cannot import name 'stable_folds'`

- [ ] **Step 3: Add the three functions**

Add `import hashlib` to the imports at the top of `app/services/impact_eval.py`, then append below `assign_folds`:

```python
def stable_folds(match_ids, n_folds: int = 5, seed: int = 0) -> dict[int, int]:
    """match_id -> fold index, independent of what else is in the set.

    assign_folds permutes over the COLLECTION, so adding or excluding a
    single match can move every other match to a different fold even with
    the same seed. That makes a shared Stage A / Stage C yardstick matrix
    silently incomparable. Here the fold comes from a hash of
    (seed, match_id) alone, so a match lands in the same fold regardless of
    its neighbours.

    SHA-256 rather than hash(): the built-in is randomized per process, so
    a mapping built today would not match one built tomorrow.

    assign_folds is deliberately left untouched -- the parent project's
    committed results were produced with it, and changing it would move
    published numbers.
    """
    out: dict[int, int] = {}
    for match_id in {int(m) for m in match_ids}:
        digest = hashlib.sha256(f"{int(seed)}:{match_id}".encode()).digest()
        out[match_id] = int.from_bytes(digest[:8], "big") % int(n_folds)
    return out


def dataset_fingerprint(match_ids) -> str:
    """Stable identity for an eligible match SET."""
    unique = sorted({int(m) for m in match_ids})
    payload = ",".join(str(m) for m in unique).encode()
    return f"{len(unique)}:{hashlib.sha256(payload).hexdigest()[:16]}"


def fold_mapping_hash(folds: dict[int, int]) -> str:
    """Stable identity for an ACTUAL match -> fold assignment.

    Not redundant with dataset_fingerprint, and assuming it was is the hole
    this closes: the parent project's results used the permutation-based
    assign_folds, so the same match set can carry a completely different
    assignment. Same fingerprint, different folds, a matrix that looks
    comparable and is not.
    """
    payload = ";".join(f"{int(m)}:{int(f)}" for m, f in sorted(folds.items())).encode()
    return hashlib.sha256(payload).hexdigest()[:16]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_eval.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/impact_eval.py webapp/tests/test_impact_eval.py
git commit -m "Add membership-independent folds, dataset fingerprint and fold-mapping hash" -m "assign_folds permutes over the collection, so changing the match set moves every assignment. A fingerprint alone cannot catch a same-set-different-folds mismatch, which is exactly what comparing against Stage A's permutation-era results would produce." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Per-kill term decomposition

**Files:**
- Create: `webapp/app/services/kill_order_leverage.py`
- Test: `webapp/tests/test_kill_order_leverage.py`

**Interfaces:**
- Consumes: `app.scoring.impact`'s private helpers `_categorize_econ`, `_time_factor`, `_traded_factor`, `_check_for_resurrection`, `_econ_swing_risk_factor`, `_KILL_ORDER_GRAPH`.
- Produces:
  - `PARAMS: list[str]` — 26 names, `"1v1".."5v5"` in `own`-major order then `"fallback"`.
  - `PARAM_INDEX: dict[str, int]`, `COMPONENTS = ("econ", "time", "swing")`, `FALLBACK_WEIGHT = 100.0`.
  - `shipped_graph() -> np.ndarray` — shape `(26,)`.
  - `KillTerm` dataclass.
  - `kill_terms_for_match(rounds_by_number, round_outcomes, round_player_stats, match_players, round_kills) -> dict[int, list[KillTerm]]`.

**Why the walk is re-implemented rather than imported:** `impact.py` computes these terms inside `build_impact_rows_for_match` and throws away the per-kill decomposition, keeping only per-player sums. Stage C needs the decomposition. The walk here must mirror `impact.py:503-580` exactly — including its resurrection rule, which **differs from `state_replay.py`'s** (that engine excludes ambiguous-lifecycle rounds; `impact.py` keeps them and declines to decrement). Task 5's gate is what holds the two in sync.

**Sign convention, stated once because everything downstream depends on it.** For a cross-team kill by team A on team B: A's `kill_impact` rises by the kill term, and B's `death_impact` rises by the death term — but B's Impact is *subtracted* in the differential, so the death term also *raises* `Impact_diff`. Both halves therefore carry the same sign, `+1` when the killer is on team A. A self-kill zeroes the kill half and reverses the sign, because the loss lands on the killer's own team.

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/test_kill_order_leverage.py
"""Per-kill decomposition of the kill-order graph's leverage. No DB session:
the inputs are the same plain structures build_impact_rows_for_match builds
internally (impact.py:404-437), so they can be constructed by hand."""

import numpy as np
import pytest

from app.models.match import Team
from app.services.kill_order_leverage import (
    COMPONENTS,
    FALLBACK_WEIGHT,
    PARAM_INDEX,
    PARAMS,
    kill_terms_for_match,
    shipped_graph,
)


class FakeRound:
    def __init__(self, round_id, number, outcome="Team A Eliminated", planted=False,
                 plant_time=None, exploded=False, defused=False, defuse_time=None):
        self.id = round_id
        self.round_number = number
        self.outcome = outcome
        self.planted = planted
        self.plant_time = plant_time
        self.exploded = exploded
        self.defused = defused
        self.defuse_time = defuse_time


class FakePlayer:
    def __init__(self, player_id, team):
        self.id = player_id
        self.team = team


def make_match(kills, outcome="Team A Eliminated", loadout=4200, round_number=5):
    """Five players per side, ids 1-5 on TEAM_1 and 6-10 on TEAM_2."""
    players = {i: FakePlayer(i, Team.TEAM_1 if i <= 5 else Team.TEAM_2) for i in range(1, 11)}
    stats = {
        round_number: {
            i: {"score": 200, "kills": 0, "deaths": 0, "assists": 0,
                "loadout": loadout, "remaining": 1000}
            for i in range(1, 11)
        }
    }
    rnd = FakeRound(100, round_number, outcome=outcome)
    return (
        {round_number: rnd},
        {round_number: outcome},
        stats,
        players,
        {round_number: kills},
    )


def kill(killer, victim, t):
    return {"killer_match_player_id": killer, "death_match_player_id": victim,
            "event_time_seconds": float(t)}


def test_params_are_26_in_a_fixed_order():
    assert len(PARAMS) == 26
    assert PARAMS[0] == "1v1"
    assert PARAMS[-1] == "fallback"
    assert PARAMS[:5] == ["1v1", "1v2", "1v3", "1v4", "1v5"]
    assert PARAM_INDEX["5v5"] == 24
    assert COMPONENTS == ("econ", "time", "swing")


def test_shipped_graph_collapses_50_edges_to_26_values():
    graph = shipped_graph()
    assert graph.shape == (26,)
    assert graph[PARAM_INDEX["5v5"]] == 150.0
    assert graph[PARAM_INDEX["1v1"]] == 250.0
    assert graph[PARAM_INDEX["4v4"]] == 170.0
    assert graph[PARAM_INDEX["1v2"]] == 190.0
    assert graph[PARAM_INDEX["2v1"]] == 130.0
    assert graph[PARAM_INDEX["fallback"]] == FALLBACK_WEIGHT


def test_first_kill_of_a_round_crosses_5v5_with_a_positive_sign_for_team_a():
    terms = kill_terms_for_match(*make_match([kill(1, 6, 10.0)]))
    (term,) = terms[5]
    assert PARAMS[term.param_index] == "5v5"
    assert term.sign == 1.0
    assert term.tracked is True


def test_a_team_b_kill_carries_a_negative_sign():
    terms = kill_terms_for_match(*make_match([kill(6, 1, 10.0)]))
    (term,) = terms[5]
    assert PARAMS[term.param_index] == "5v5"
    assert term.sign == -1.0


def test_the_state_walks_down_as_kills_land():
    terms = kill_terms_for_match(*make_match([
        kill(1, 6, 5.0), kill(1, 7, 6.0), kill(8, 1, 7.0),
    ]))
    assert [PARAMS[t.param_index] for t in terms[5]] == ["5v5", "5v4", "3v5"]


def test_a_self_kill_zeroes_the_kill_half_and_reverses_the_sign():
    terms = kill_terms_for_match(*make_match([kill(1, 1, 10.0)]))
    (term,) = terms[5]
    assert term.kill == (0.0, 0.0, 0.0)
    assert term.sign == -1.0
    assert any(v != 0.0 for v in term.death)


def test_a_self_kill_decrements_the_killers_own_side():
    terms = kill_terms_for_match(*make_match([kill(1, 1, 5.0), kill(2, 6, 6.0)]))
    assert [PARAMS[t.param_index] for t in terms[5]] == ["5v5", "4v5"]


def test_an_untracked_transition_lands_on_the_fallback_parameter():
    """Six team-A kills exhausts team B; the seventh has no edge to cross."""
    kills = [kill(1, 5 + i, float(i)) for i in range(1, 6)] + [kill(1, 6, 9.0)]
    terms = kill_terms_for_match(*make_match(kills))
    names = [PARAMS[t.param_index] for t in terms[5]]
    assert names[-1] == "fallback"
    assert terms[5][-1].tracked is False
    assert all(n != "fallback" for n in names[:-1])


def test_the_traded_factor_is_folded_into_the_death_half_only():
    """Kill at t=10 traded back at t=14 -> factor 0.4 on the death half."""
    plain = kill_terms_for_match(*make_match([kill(1, 6, 10.0)]))[5][0]
    traded = kill_terms_for_match(*make_match([kill(1, 6, 10.0), kill(7, 1, 14.0)]))[5][0]
    assert np.isclose(traded.traded, 0.4)
    assert np.allclose(traded.kill, plain.kill)
    assert np.allclose(traded.death, np.array(plain.death) * 0.4)


def test_death_untraded_is_the_undiscounted_half_and_the_invariant_holds():
    """The player-level read reports the trade discount as
    death_untraded - death, so the two must stay consistent."""
    plain = kill_terms_for_match(*make_match([kill(1, 6, 10.0)]))[5][0]
    traded = kill_terms_for_match(*make_match([kill(1, 6, 10.0), kill(7, 1, 14.0)]))[5][0]

    assert np.allclose(plain.death_untraded, plain.death)  # traded == 1.0
    assert np.allclose(traded.death_untraded, plain.death_untraded)
    for term in (plain, traded):
        assert np.allclose(term.death, np.array(term.death_untraded) * term.traded)
    discount = np.array(traded.death_untraded) - np.array(traded.death)
    assert np.all(discount > 0)


def test_an_instant_trade_gives_a_zero_factor_without_a_division():
    """_traded_factor returns trade_time/10, so a same-second trade is
    exactly 0.0 -- which is why death_untraded is stored rather than
    recovered by dividing."""
    term = kill_terms_for_match(*make_match([kill(1, 6, 10.0), kill(7, 1, 10.0)]))[5][0]
    assert term.traded == 0.0
    assert np.allclose(term.death, (0.0, 0.0, 0.0))
    assert np.any(np.array(term.death_untraded) != 0.0)


def test_econ_mismatch_moves_the_kill_half_econ_factor():
    """A full-buy killer against an eco victim scores above 1.0; equal
    loadouts score exactly 1.0."""
    rounds, outcomes, stats, players, kills = make_match([kill(1, 6, 10.0)])
    equal = kill_terms_for_match(rounds, outcomes, stats, players, kills)[5][0]
    assert np.isclose(equal.kill[COMPONENTS.index("econ")], 1.0)

    stats[5][6]["loadout"] = 1500  # victim on an eco
    mismatch = kill_terms_for_match(rounds, outcomes, stats, players, kills)[5][0]
    assert mismatch.kill[COMPONENTS.index("econ")] > 1.0


def test_missing_player_stats_raise_rather_than_being_skipped():
    rounds, outcomes, stats, players, kills = make_match([kill(1, 6, 10.0)])
    del stats[5][6]
    with pytest.raises(KeyError):
        kill_terms_for_match(rounds, outcomes, stats, players, kills)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_leverage.py -v`
Expected: FAIL — `No module named 'app.services.kill_order_leverage'`

- [ ] **Step 3: Write the module**

```python
# webapp/app/services/kill_order_leverage.py
"""Per-kill decomposition of the kill-order graph's contribution to Impact.

Impact is exactly linear in the 26 kill-order parameters before rounding
(verified in the spec: doubling every edge weight doubles econ/time/swing to
a median ratio of exactly 2.000, and leaves damage at 1.000). So a round's
Impact differential can be written

    ImpactDiff(r) = damage_diff(r) + SUM_k b_k * x_r[k]

and every candidate graph is scored on the SAME design matrix, differing
only in b. This module produces x_r.

The state walk here mirrors app/scoring/impact.py:503-580 exactly, INCLUDING
its resurrection rule, which differs from app/services/state_replay.py's
(that engine excludes ambiguous-lifecycle rounds; impact.py keeps them and
declines to decrement). The divergence is deliberate: this stage refits the
parameters of the shipped scorer, so extracting through a different engine
would refit a graph for a metric that is not the one that ships. The
reconstruction gate holds the two in sync.
"""

from dataclasses import dataclass, replace

import numpy as np

from app.models.match import Team
from app.scoring.impact import (
    _KILL_ORDER_GRAPH,
    _categorize_econ,
    _check_for_resurrection,
    _econ_swing_risk_factor,
    _time_factor,
    _traded_factor,
)

# own-major so PARAMS[:5] is the own=1 row. The 26th is the fallback
# _kill_order_bonus returns for transitions the graph does not contain --
# 543 of 178,242 kill events, in 497 of 23,955 rounds, measured.
PARAMS: list[str] = [f"{own}v{opp}" for own in range(1, 6) for opp in range(1, 6)] + ["fallback"]
PARAM_INDEX: dict[str, int] = {name: i for i, name in enumerate(PARAMS)}
COMPONENTS: tuple[str, str, str] = ("econ", "time", "swing")
FALLBACK_INDEX = PARAM_INDEX["fallback"]
FALLBACK_WEIGHT = 100.0

# Self-kill death-side econ factors, from impact.py:547-555. Keyed by the
# victim's econ tier code.
_SELF_KILL_DEATH_ECON = {4: 0.9, 5: 0.85, 6: 0.75}
_SELF_KILL_DEATH_ECON_DEFAULT = 0.15


def shipped_graph() -> np.ndarray:
    """The live _KILL_ORDER_GRAPH as a 26-vector in PARAMS order.

    The DiGraph's 50 edges are exactly 25 killer-perspective parameters
    duplicated by side. That symmetry is a structural invariant of the
    metric -- without it Impact would depend on which team you happen to be
    -- so it is ASSERTED here rather than assumed.
    """
    graph = np.full(len(PARAMS), np.nan)
    graph[FALLBACK_INDEX] = FALLBACK_WEIGHT
    for source, target, data in _KILL_ORDER_GRAPH.edges(data=True):
        before_a, before_b = (int(v) for v in source.split("v"))
        after_a, _ = (int(v) for v in target.split("v"))
        # A decrement of the first index is a TEAM_1 kill, whose killer has
        # `before_b` alive against `before_a`; otherwise it is TEAM_2's.
        own, opp = (before_b, before_a) if after_a == before_a - 1 else (before_a, before_b)
        index = PARAM_INDEX[f"{own}v{opp}"]
        weight = float(data["weight"])
        if not np.isnan(graph[index]) and graph[index] != weight:
            raise ValueError(
                f"kill-order graph is not side-symmetric at {own}v{opp}: "
                f"{graph[index]} != {weight}"
            )
        graph[index] = weight
    if np.isnan(graph).any():
        missing = [PARAMS[i] for i in np.flatnonzero(np.isnan(graph))]
        raise ValueError(f"kill-order graph has no weight for {missing}")
    return graph


@dataclass(frozen=True)
class KillTerm:
    """One kill's contribution, with the graph weight factored OUT.

    `kill`, `death` and `death_untraded` are the three component factors
    (econ, time, swing). `death` is what impact.py actually scores -- the
    traded factor already applied. `death_untraded` is the same before that
    discount.

    Both are stored rather than one being derived, for two reasons. The
    player-level read reports the trade discount as a subtraction
    (`death_untraded - death`), and _traded_factor can legitimately return
    0.0 for a kill traded back instantly, which would make division by
    `traded` undefined.
    """

    round_number: int
    round_id: int
    param_index: int
    tracked: bool
    sign: float
    killer_match_player_id: int
    victim_match_player_id: int
    kill: tuple[float, float, float]
    death: tuple[float, float, float]
    death_untraded: tuple[float, float, float]
    traded: float
    # Alive counts AFTER this kill, straight from the walk. Reconstructing
    # them later by counting victims is wrong: impact.py deliberately does
    # not decrement on events _check_for_resurrection flags, so a
    # re-referenced player would be subtracted twice and the terminal state
    # could go negative.
    alive_team1_after: int = 5
    alive_team2_after: int = 5


def kill_terms_for_match(
    rounds_by_number,
    round_outcomes,
    round_player_stats,
    match_players,
    round_kills,
) -> dict[int, list[KillTerm]]:
    """Decompose every kill of a match. Inputs are the same structures
    build_impact_rows_for_match builds internally (impact.py:404-437).

    EX-ANTE ONLY: the swing factor comes from _econ_swing_risk_factor and
    the realized term is never consulted, because it reads round N+1's
    loadouts and any forward-looking model trained on it would leak.
    """
    out: dict[int, list[KillTerm]] = {}

    for round_number, kills in round_kills.items():
        round_row = rounds_by_number[round_number]
        stats = round_player_stats[round_number]

        swing_by_team = {
            Team.TEAM_1: _econ_swing_risk_factor(
                round_outcomes, round_player_stats, match_players,
                round_number, Team.TEAM_1, round_row,
            ),
            Team.TEAM_2: _econ_swing_risk_factor(
                round_outcomes, round_player_stats, match_players,
                round_number, Team.TEAM_2, round_row,
            ),
        }

        # Mirrors impact.py's confusing but load-bearing naming: team1_index
        # tracks TEAM_2's alive count and vice versa, because each decrements
        # when the OTHER team lands a kill.
        team1_index = 5
        team2_index = 5
        terms: list[KillTerm] = []

        for position, event in enumerate(kills):
            killer_id = event["killer_match_player_id"]
            victim_id = event["death_match_player_id"]
            self_kill = killer_id == victim_id
            killer_team = match_players[killer_id].team

            before = f"{team1_index}v{team2_index}"
            after_a, after_b = team1_index, team2_index
            if (killer_team == Team.TEAM_1) != self_kill:
                after_a -= 1
            else:
                after_b -= 1
            tracked = _KILL_ORDER_GRAPH.has_edge(before, f"{after_a}v{after_b}")

            if killer_team == Team.TEAM_1:
                own, opp = team2_index, team1_index
            else:
                own, opp = team1_index, team2_index
            param_index = PARAM_INDEX[f"{own}v{opp}"] if tracked else FALLBACK_INDEX

            # Both halves carry the killer's sign: the victim's death_impact
            # is subtracted from the OTHER team, so it raises the same
            # differential the kill does. A self-kill reverses it.
            sign = 1.0 if killer_team == Team.TEAM_1 else -1.0
            if self_kill:
                sign = -sign

            killer_tier = _categorize_econ(stats[killer_id]["loadout"])
            victim_tier = _categorize_econ(stats[victim_id]["loadout"])
            swing = swing_by_team[Team.TEAM_2 if killer_team == Team.TEAM_1 else Team.TEAM_1]
            traded = _traded_factor(kills, event, self_kill)

            if self_kill:
                kill_half = (0.0, 0.0, 0.0)
                death_econ = _SELF_KILL_DEATH_ECON.get(victim_tier, _SELF_KILL_DEATH_ECON_DEFAULT)
            else:
                kill_half = (
                    killer_tier / victim_tier,
                    _time_factor(round_row, event["event_time_seconds"]),
                    swing,
                )
                death_econ = killer_tier / victim_tier

            death_untraded = (
                death_econ,
                _time_factor(round_row, event["event_time_seconds"], for_death=True),
                swing,
            )
            death_half = tuple(traded * value for value in death_untraded)

            terms.append(
                KillTerm(
                    round_number=round_number,
                    round_id=round_row.id,
                    param_index=param_index,
                    tracked=tracked,
                    sign=sign,
                    killer_match_player_id=killer_id,
                    victim_match_player_id=victim_id,
                    kill=kill_half,
                    death=death_half,
                    death_untraded=death_untraded,
                    traded=traded,
                )
            )

            if not _check_for_resurrection(position, kills):
                if (killer_team == Team.TEAM_1) != self_kill:
                    team1_index -= 1
                else:
                    team2_index -= 1
            # team1_index tracks TEAM_2's alive count and vice versa.
            terms[-1] = replace(
                terms[-1], alive_team1_after=team2_index, alive_team2_after=team1_index
            )

        out[round_number] = terms

    return out
```

Note the swing factor is looked up by the *victim's* team, mirroring
`impact.py:534` — killing someone whose team is economically fragile is what
the factor is meant to reward.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_leverage.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_leverage.py webapp/tests/test_kill_order_leverage.py
git commit -m "Add per-kill decomposition of kill-order graph leverage" -m "Impact is exactly linear in the 26 kill-order parameters before rounding, so a round can be reduced to a leverage vector and every candidate graph scored on the same design matrix. Mirrors impact.py's own state walk, including its resurrection rule." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Team and player leverage products

**Files:**
- Modify: `webapp/app/services/kill_order_leverage.py` (append)
- Test: `webapp/tests/test_kill_order_leverage.py` (append)

**Interfaces:**
- Consumes: `kill_terms_for_match`, `PARAMS`, `COMPONENTS` from Task 3; `build_impact_rows_for_match` from `app.scoring.impact`.
- Produces: `TeamLeverageRow`, `PlayerLeverageRow`, `MatchLeverage`, `build_match_leverage(db, match_id) -> MatchLeverage`, `load_all_leverage(db, report=None) -> tuple[list[TeamLeverageRow], list[PlayerLeverageRow]]`.

**Why two products:** the team row is what every fit and yardstick consumes; the player row is what Stage 0's per-player block and the kill/death-and-trades read consume. A team differential cannot reconstruct per-player scores, so promising player-level outputs from team rows alone would be a data-contract failure. The consistency test in Step 1 is what stops them drifting.

**The sign relation between the two products, derived once.** Team rows are stored in *contributes-to-`Impact_diff`* form, so a fit can simply sum them. One cross-team kill by team A raises the differential twice over: A's `kill_impact` rises, and B's `death_impact` rises, which *subtracts* from B. Player rows are stored unsigned, per player. Therefore:

```
team.kill  = SUM_A player.kill  - SUM_B player.kill
team.death = SUM_B player.death - SUM_A player.death      <- note the flip
```

The flip on the death block is the arithmetic reason kill and death nearly collapse into the same column at team level (measured: 0.937-0.957). It is not a sign error, and a test asserts it in both directions.

**Damage is read from the scorer, not re-derived.** `impact.py` computes `damages` by backing Valorant's own combat-score kill bonus out of ACS and correcting for multi-kills (`impact.py:583-602`). Re-implementing that would be a second copy that can drift, and damage is graph-independent anyway, so `build_impact_rows_for_match(..., use_realized_swing=False)` supplies it directly.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_leverage.py

from app.services.kill_order_leverage import (
    PlayerLeverageRow,
    TeamLeverageRow,
    assemble_round,
)


def _round_products(kills, damages=None, round_number=5):
    """Build both products for one round from the fixture helpers above."""
    rounds, outcomes, stats, players, kill_map = make_match(kills, round_number=round_number)
    terms = kill_terms_for_match(rounds, outcomes, stats, players, kill_map)[round_number]
    damages = damages or {i: 0.0 for i in range(1, 11)}
    return assemble_round(
        match_id=1,
        round_row=rounds[round_number],
        terms=terms,
        match_players=players,
        damage_by_match_player=damages,
    )


def test_team_row_places_a_team_a_kill_positively_on_both_halves():
    team, _ = _round_products([kill(1, 6, 10.0)])
    idx = PARAM_INDEX["5v5"]
    assert team.kill[idx].sum() > 0
    assert team.death[idx].sum() > 0


def test_team_row_places_a_team_b_kill_negatively_on_both_halves():
    team, _ = _round_products([kill(6, 1, 10.0)])
    idx = PARAM_INDEX["5v5"]
    assert team.kill[idx].sum() < 0
    assert team.death[idx].sum() < 0


def test_player_rows_reconstruct_the_team_row_exactly():
    """The data-contract gate. Note the FLIP on the death block."""
    kills = [kill(1, 6, 4.0), kill(7, 2, 9.0), kill(3, 8, 12.0), kill(4, 4, 15.0)]
    team, players = _round_products(kills)
    by_id = {p.match_player_id: p for p in players}

    def side_sum(field, team_a):
        total = np.zeros((len(PARAMS), len(COMPONENTS)))
        for row in players:
            if row.team_is_a == team_a:
                total += getattr(row, field)
        return total

    assert np.allclose(team.kill, side_sum("kill", True) - side_sum("kill", False))
    assert np.allclose(team.death, side_sum("death", False) - side_sum("death", True))
    assert np.allclose(
        team.death_untraded,
        side_sum("death_untraded", False) - side_sum("death_untraded", True),
    )
    assert set(by_id) == set(range(1, 11))


def test_every_player_gets_a_row_even_with_no_kills_or_deaths():
    """Stage 0 averages over player-rounds; a silently missing row would
    change a denominator rather than raising."""
    _, players = _round_products([kill(1, 6, 10.0)])
    assert len(players) == 10
    quiet = [p for p in players if p.match_player_id == 5][0]
    assert np.allclose(quiet.kill, 0.0)
    assert np.allclose(quiet.death, 0.0)


def test_the_killer_gets_the_kill_half_and_the_victim_the_death_half():
    _, players = _round_products([kill(1, 6, 10.0)])
    by_id = {p.match_player_id: p for p in players}
    assert by_id[1].kill.sum() > 0
    assert np.allclose(by_id[1].death, 0.0)
    assert by_id[6].death.sum() > 0
    assert np.allclose(by_id[6].kill, 0.0)


def test_a_self_kill_charges_only_the_death_half_to_that_player():
    _, players = _round_products([kill(1, 1, 10.0)])
    by_id = {p.match_player_id: p for p in players}
    assert np.allclose(by_id[1].kill, 0.0)
    assert by_id[1].death.sum() > 0


def test_the_trade_discount_is_visible_per_player():
    """Decision: the player-level read reports death cost as scored against
    death cost with no trade credit. That subtraction must be available on
    the row, per player."""
    _, players = _round_products([kill(1, 6, 10.0), kill(7, 1, 14.0)])
    victim = [p for p in players if p.match_player_id == 6][0]
    discount = victim.death_untraded - victim.death
    assert discount.sum() > 0
    assert np.allclose(victim.death, victim.death_untraded * 0.4)


def test_damage_is_carried_through_and_differenced():
    damages = {i: (10.0 if i <= 5 else 4.0) for i in range(1, 11)}
    team, players = _round_products([kill(1, 6, 10.0)], damages=damages)
    assert np.isclose(team.damage_diff, 5 * 10.0 - 5 * 4.0)
    assert all(np.isclose(p.damage, damages[p.match_player_id]) for p in players)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_leverage.py -k "team_row or player_rows or trade_discount or damage_is_carried or self_kill_charges or killer_gets" -v`
Expected: FAIL — `cannot import name 'assemble_round'`

- [ ] **Step 3: Append the products and the loader**

```python
# append to webapp/app/services/kill_order_leverage.py

@dataclass(frozen=True)
class TeamLeverageRow:
    """One round, in contributes-to-Impact_diff form (team A minus team B).

    A fit consumes `damage_diff` plus these two blocks; under the shipped
    weights the round's Impact differential is

        damage_diff + (1/3) * SUM_{k,c} b_k * (kill[k][c] + death[k][c])

    Both blocks are ADDED because a kill raises the differential twice: the
    killer's kill_impact rises, and the victim's death_impact is subtracted
    from the other team.
    """

    match_id: int
    round_id: int
    round_number: int
    damage_diff: float
    kill: np.ndarray            # (26, 3)
    death: np.ndarray           # (26, 3), traded discount applied
    death_untraded: np.ndarray  # (26, 3), before the discount


@dataclass(frozen=True)
class PlayerLeverageRow:
    """One (round, match_player), unsigned. Consumed by Stage 0's per-player
    block and by the kill/death-and-trades read, neither of which can be
    served from a team differential."""

    match_id: int
    round_id: int
    round_number: int
    match_player_id: int
    # The CANONICAL player, stable across matches. match_player_id is a
    # per-match surrogate, so grouping by it makes "within-player" analysis
    # impossible -- terciles would compare strong players against weak ones
    # instead of a player against their own baseline. 94.7% of players in
    # this DB have exactly one match, which is what makes that distinction
    # decisive rather than academic.
    player_id: int
    team_is_a: bool
    damage: float
    kill: np.ndarray
    death: np.ndarray
    death_untraded: np.ndarray


@dataclass(frozen=True)
class MatchLeverage:
    match_id: int
    team_rows: list[TeamLeverageRow]
    player_rows: list[PlayerLeverageRow]


def _blocks() -> np.ndarray:
    return np.zeros((len(PARAMS), len(COMPONENTS)))


def assemble_round(match_id, round_row, terms, match_players, damage_by_match_player):
    """Both products for one round. Pure: no DB access, so it is fixture
    testable."""
    player_kill = {mp_id: _blocks() for mp_id in match_players}
    player_death = {mp_id: _blocks() for mp_id in match_players}
    player_death_raw = {mp_id: _blocks() for mp_id in match_players}

    for term in terms:
        killer, victim = term.killer_match_player_id, term.victim_match_player_id
        player_kill[killer][term.param_index] += np.asarray(term.kill, dtype=float)
        player_death[victim][term.param_index] += np.asarray(term.death, dtype=float)
        player_death_raw[victim][term.param_index] += np.asarray(
            term.death_untraded, dtype=float
        )

    player_rows: list[PlayerLeverageRow] = []
    for mp_id, match_player in match_players.items():
        player_rows.append(
            PlayerLeverageRow(
                match_id=match_id,
                round_id=round_row.id,
                round_number=round_row.round_number,
                match_player_id=mp_id,
                player_id=match_player.player_id,
                team_is_a=match_player.team == Team.TEAM_1,
                damage=float(damage_by_match_player.get(mp_id, 0.0)),
                kill=player_kill[mp_id],
                death=player_death[mp_id],
                death_untraded=player_death_raw[mp_id],
            )
        )

    # SUM_A - SUM_B for the kill block; SUM_B - SUM_A for the death block,
    # because a death is SUBTRACTED from the player who suffered it.
    def combine(field, flip):
        total = _blocks()
        for row in player_rows:
            on_a = row.team_is_a
            sign = (1.0 if on_a else -1.0) * (-1.0 if flip else 1.0)
            total += sign * getattr(row, field)
        return total

    team_row = TeamLeverageRow(
        match_id=match_id,
        round_id=round_row.id,
        round_number=round_row.round_number,
        damage_diff=float(
            sum(r.damage for r in player_rows if r.team_is_a)
            - sum(r.damage for r in player_rows if not r.team_is_a)
        ),
        kill=combine("kill", flip=False),
        death=combine("death", flip=True),
        death_untraded=combine("death_untraded", flip=True),
    )
    return team_row, player_rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_leverage.py -v`
Expected: PASS (22 tests — 14 from Task 3, 8 here)

- [ ] **Step 5: Add the DB loader**

```python
# append to webapp/app/services/kill_order_leverage.py

from collections import defaultdict

from sqlalchemy.orm import selectinload

from app.models import KillEvent, Match, MatchPlayer, Round
from app.models.round import RoundPlayerStat
from app.scoring.impact import build_impact_rows_for_match
from app.services.surrender_rounds import NOT_A_SURRENDER_ROUND


def eligible_match_ids(db) -> list[int]:
    return [
        match_id
        for (match_id,) in db.query(Match.id)
        .join(Round, Round.match_id == Match.id)
        .filter(NOT_A_SURRENDER_ROUND)
        .distinct()
        .order_by(Match.id)
        .all()
    ]


def build_match_leverage(db, match_id: int) -> MatchLeverage:
    """Replay one match. Mirrors build_impact_rows_for_match's own loads
    (impact.py:404-437) so the two see identical inputs.

    EX-ANTE: damage comes from the scorer with use_realized_swing=False.
    Damage is graph-independent, but the flag is passed explicitly so this
    never becomes the one path that quietly reads round N+1.
    """
    rounds = (
        db.query(Round)
        .filter(Round.match_id == match_id)
        .filter(NOT_A_SURRENDER_ROUND)
        .order_by(Round.round_number)
        .all()
    )
    rounds_by_number = {r.round_number: r for r in rounds}
    number_by_round_id = {r.id: r.round_number for r in rounds}
    round_outcomes = {r.round_number: r.outcome for r in rounds}

    match_players = {
        mp.id: mp for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()
    }

    round_player_stats: dict[int, dict[int, dict]] = defaultdict(dict)
    for stat in db.query(RoundPlayerStat).join(Round).filter(Round.match_id == match_id).all():
        number = number_by_round_id.get(stat.round_id)
        if number is None:
            continue  # a surrender placeholder round, already filtered above
        round_player_stats[number][stat.match_player_id] = {
            "score": stat.score, "kills": stat.kills, "deaths": stat.deaths,
            "assists": stat.assists, "loadout": stat.loadout, "remaining": stat.remaining,
        }

    round_kills: dict[int, list[dict]] = defaultdict(list)
    for event in (
        db.query(KillEvent)
        .join(Round)
        .filter(Round.match_id == match_id)
        .order_by(KillEvent.event_time_seconds, KillEvent.id)
        .all()
    ):
        number = number_by_round_id.get(event.round_id)
        if number is None:
            continue
        round_kills[number].append({
            "killer_match_player_id": event.killer_match_player_id,
            "death_match_player_id": event.death_match_player_id,
            "event_time_seconds": event.event_time_seconds,
        })

    damage_by_round: dict[int, dict[int, float]] = defaultdict(dict)
    for calculated in build_impact_rows_for_match(db, match_id, use_realized_swing=False):
        number = number_by_round_id.get(calculated.round_id)
        if number is None:
            continue
        damage_by_round[number][calculated.match_player_id] = float(calculated.damage)

    terms_by_round = kill_terms_for_match(
        rounds_by_number, round_outcomes, round_player_stats, match_players, round_kills
    )

    team_rows: list[TeamLeverageRow] = []
    player_rows: list[PlayerLeverageRow] = []
    for number, round_row in rounds_by_number.items():
        if number not in round_player_stats:
            continue  # no stats rows: nothing to attribute
        team_row, rows = assemble_round(
            match_id=match_id,
            round_row=round_row,
            terms=terms_by_round.get(number, []),
            match_players=match_players,
            damage_by_match_player=damage_by_round.get(number, {}),
        )
        team_rows.append(team_row)
        player_rows.extend(rows)

    return MatchLeverage(match_id=match_id, team_rows=team_rows, player_rows=player_rows)


def load_all_leverage(db, report: dict | None = None):
    """Every eligible match. Costs a full replay -- minutes, comparable to
    the parent project's load_all_observations.

    A match that raises is EXCLUDED and counted, never silently turned into
    zero-leverage rows; the CLI prints the count.
    """
    team_rows: list[TeamLeverageRow] = []
    player_rows: list[PlayerLeverageRow] = []
    excluded: list[int] = []
    match_ids = eligible_match_ids(db)
    for match_id in match_ids:
        try:
            leverage = build_match_leverage(db, match_id)
        except (KeyError, ValueError):
            excluded.append(match_id)
            continue
        team_rows.extend(leverage.team_rows)
        player_rows.extend(leverage.player_rows)
    if report is not None:
        report["eligible_matches"] = len(match_ids)
        report["excluded_matches"] = len(excluded)
        report["excluded_match_ids"] = excluded[:20]
    return team_rows, player_rows
```

- [ ] **Step 6: Commit**

```bash
git add webapp/app/services/kill_order_leverage.py webapp/tests/test_kill_order_leverage.py
git commit -m "Add team and player leverage products with a DB loader" -m "Two products from one replay: signed team rows for fitting, unsigned player rows for the per-player and trade reads. A team differential cannot reconstruct per-player scores, so the consistency test between them is a data-contract gate, not a nicety." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The gates against `impact.py` (requires live Postgres)

**Files:**
- Test: `webapp/tests/test_kill_order_leverage_gates.py` (new)

**Interfaces:**
- Consumes: `build_match_leverage`, `eligible_match_ids`, `shipped_graph`, `PARAMS`, `COMPONENTS` from Tasks 3-4; `build_impact_rows_for_match` and `_KILL_ORDER_GRAPH` from `app.scoring.impact`.
- Produces: nothing importable. This task ships four gates and no production code.

**Why this task exists at all.** Everything downstream assumes two things about `impact.py` that are true today and are nobody's stated contract: that Impact is exactly linear in the edge weights, and that the leverage decomposition reconstructs what the scorer actually computes. Neither is enforced anywhere. If a future change to `impact.py`'s combination step breaks either, every number this stage produces silently becomes wrong rather than failing. These gates are the alarm.

**The rounding bound, derived rather than guessed.** `impact.py:625-641` rounds `kill_impact` and `death_impact` independently per player, then subtracts them as integers. So each player's stored `impact` differs from the exact value by at most `0.5 + 0.5 = 1.0`. A round's differential sums at most `n_players` of those, so the bound is `1.0 * n_players` — normally 10. `damages` is rounded too, but it is the *same* rounded number on both sides of the comparison, so it contributes nothing. The test asserts against the derived bound and also prints the worst observed gap, which should sit far below it.

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/test_kill_order_leverage_gates.py
"""Gates holding the leverage extractor to what app/scoring/impact.py
actually computes. Requires a live Postgres:

    docker compose -p valomaths-private up -d

and skips cleanly when it is unreachable, matching the parent project's
convention for its own DB-backed tests."""

import networkx as nx
import numpy as np
import pytest

import app.scoring.impact as impact_module
from app.models import ImpactScore, MatchPlayer
from app.models.match import Team
from app.scoring.impact import build_impact_rows_for_match
from app.services.kill_order_leverage import (
    COMPONENTS,
    PARAMS,
    build_match_leverage,
    eligible_match_ids,
    shipped_graph,
)

SAMPLE_MATCHES = 12


@pytest.fixture(scope="module")
def db():
    try:
        from app.db import SessionLocal

        session = SessionLocal()
        session.execute(__import__("sqlalchemy").text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"live Postgres unavailable: {exc}")
    yield session
    session.close()


@pytest.fixture(scope="module")
def sample_match_ids(db):
    ids = eligible_match_ids(db)[:SAMPLE_MATCHES]
    if not ids:
        pytest.skip("no eligible matches in the database")
    return ids


def _stored_impact_diff(db, match_id):
    """Round-level Impact differential straight from the scorer, ex-ante."""
    teams = {
        mp.id: mp.team for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()
    }
    out: dict[int, float] = {}
    for row in build_impact_rows_for_match(db, match_id, use_realized_swing=False):
        sign = 1.0 if teams[row.match_player_id] == Team.TEAM_1 else -1.0
        out[row.round_id] = out.get(row.round_id, 0.0) + sign * row.impact
    return out


def _reconstruct(team_row, graph):
    """damage_diff + (1/3) * SUM_{k,c} b_k * (kill + death), which is the
    shipped formula with FACTOR_WEIGHTS all 1.0."""
    weighted = graph[:, None] * (team_row.kill + team_row.death)
    return team_row.damage_diff + weighted.sum() / 3.0


def test_reconstruction_matches_the_scorer_within_the_rounding_bound(db, sample_match_ids):
    graph = shipped_graph()
    worst = 0.0
    checked = 0
    for match_id in sample_match_ids:
        leverage = build_match_leverage(db, match_id)
        stored = _stored_impact_diff(db, match_id)
        players_per_round: dict[int, int] = {}
        for row in leverage.player_rows:
            players_per_round[row.round_id] = players_per_round.get(row.round_id, 0) + 1
        for team_row in leverage.team_rows:
            if team_row.round_id not in stored:
                continue
            gap = abs(_reconstruct(team_row, graph) - stored[team_row.round_id])
            bound = 1.0 * players_per_round[team_row.round_id]
            assert gap <= bound, (
                f"round {team_row.round_id}: gap {gap:.3f} exceeds rounding bound {bound}"
            )
            worst = max(worst, gap)
            checked += 1
    assert checked > 0
    print(f"\nreconstruction: {checked} rounds, worst gap {worst:.3f}")


def test_impact_is_linear_in_the_edge_weights(db, sample_match_ids):
    """The premise the whole design rests on. Doubling every weight must
    double the three scored components and leave damage untouched."""
    base = {(u, v): d["weight"] for u, v, d in impact_module._KILL_ORDER_GRAPH.edges(data=True)}

    def rows_with(scale):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([(u, v, w * scale) for (u, v), w in base.items()])
        original = impact_module._KILL_ORDER_GRAPH
        impact_module._KILL_ORDER_GRAPH = graph
        try:
            out = {}
            for match_id in sample_match_ids:
                for row in build_impact_rows_for_match(db, match_id, use_realized_swing=False):
                    out[(row.round_id, row.match_player_id)] = row
            return out
        finally:
            impact_module._KILL_ORDER_GRAPH = original

    single = rows_with(1.0)
    double = rows_with(2.0)

    for field in ("econ_impact", "time_impact", "swing_impact"):
        one = np.array([getattr(single[k], field) for k in single], dtype=float)
        two = np.array([getattr(double[k], field) for k in single], dtype=float)
        big = np.abs(one) >= 50  # small integers are dominated by their own rounding
        assert big.sum() > 0
        ratio = two[big] / one[big]
        assert np.allclose(ratio, 2.0, atol=0.05), f"{field} ratio {ratio.min()}..{ratio.max()}"

    damage_one = np.array([single[k].damage for k in single], dtype=float)
    damage_two = np.array([double[k].damage for k in single], dtype=float)
    assert np.array_equal(damage_one, damage_two), "damage must not depend on the graph"


def test_the_extractor_writes_nothing(db, sample_match_ids):
    """Same regression guard as the parent project's
    test_impact_exante_swing.py third assertion: the read-only path must
    stay read-only."""
    before = db.query(ImpactScore).count()
    for match_id in sample_match_ids:
        build_match_leverage(db, match_id)
    assert not db.new
    assert not db.dirty
    assert not db.deleted
    db.rollback()
    assert db.query(ImpactScore).count() == before


def test_shipped_graph_round_trips_and_is_side_symmetric(db):
    """shipped_graph() raises on asymmetry, so reaching this point already
    proves it; the assertions pin the values a reader can check by eye
    against impact.py:45-99."""
    graph = shipped_graph()
    assert graph.shape == (len(PARAMS),)
    assert np.all(graph > 0)
    expected = {"5v5": 150.0, "4v4": 170.0, "3v3": 180.0, "2v2": 200.0, "1v1": 250.0,
                "1v2": 190.0, "2v1": 130.0, "5v1": 40.0, "1v5": 60.0}
    for name, value in expected.items():
        assert graph[PARAMS.index(name)] == value


def test_fallback_crossings_are_rare_and_flagged(db, sample_match_ids):
    """Measured at 0.30% of kill events across the full DB. If this ever
    climbs sharply, the resurrection heuristic has drifted and the
    fallback-sensitivity run in the report is no longer a footnote."""
    fallback_index = PARAMS.index("fallback")
    fallback = 0.0
    total = 0.0
    for match_id in sample_match_ids:
        leverage = build_match_leverage(db, match_id)
        for row in leverage.player_rows:
            fallback += np.abs(row.kill[fallback_index]).sum() + np.abs(row.death[fallback_index]).sum()
            total += np.abs(row.kill).sum() + np.abs(row.death).sum()
    assert total > 0
    assert fallback / total < 0.05, f"fallback share {fallback / total:.3%} is not a footnote"
```

- [ ] **Step 2: Start Postgres and run the tests to verify they fail**

```bash
cd webapp
docker compose -p valomaths-private up -d
.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_leverage_gates.py -v -s
```

Expected: FAIL on the reconstruction and linearity gates if Tasks 3-4 have any sign or factor error; PASS only when the decomposition genuinely reproduces the scorer. If Postgres is down the whole module SKIPs — that is correct behaviour, not a pass.

- [ ] **Step 3: Fix whatever the gates catch**

No new production code belongs in this task. If a gate fails, the defect is in Task 3's factor decomposition or Task 4's sign relation, and the fix goes there. The likely culprits, in order:

1. **The death block's sign flip.** `team.death = SUM_B - SUM_A`, not the other way round. Getting this backwards makes the reconstruction gap roughly double the correct value rather than near zero.
2. **The swing factor's team.** `impact.py:534` applies the *victim's* team's swing factor, not the killer's.
3. **The self-kill econ branch.** `impact.py:547-555` uses a separate 0.9 / 0.85 / 0.75 / 0.15 table keyed by the victim's tier, not the tier ratio.
4. **`for_death=True` on the death-side time factor.** Omitting it silently uses the kill-side curve, which differs in the post-plant window.

- [ ] **Step 4: Run the gates again to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_leverage_gates.py -v -s`
Expected: PASS (6 tests). Note the printed worst reconstruction gap — it should be a few points against a bound of ~10, and a value close to the bound means something is off even though the assertion passes.

- [ ] **Step 5: Commit**

```bash
git add webapp/tests/test_kill_order_leverage_gates.py
git commit -m "Add gates holding the leverage extractor to impact.py" -m "Reconstruction within a derived rounding bound, linearity in the edge weights, read-only behaviour, side symmetry and a fallback-share ceiling. The linearity premise the whole stage rests on is nobody's stated contract in impact.py; this is the alarm if it ever changes." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: State visits and the empirical swing table

**Files:**
- Modify: `webapp/app/services/kill_order_leverage.py` (append)
- Create: `webapp/app/services/kill_order_curves.py`
- Test: `webapp/tests/test_kill_order_curves.py` (new)

**Interfaces:**
- Consumes: `PARAMS`, `PARAM_INDEX`, `_check_for_resurrection`, `_did_team_win`.
- Produces:
  - `StateVisitRow` and `state_visits_for_match(...)` in `kill_order_leverage`.
  - `SwingTable` dataclass, `estimate_swing_table(visits) -> SwingTable`, `MARGIN`, `TOTAL_ALIVE`, `EVEN_STATE` in `kill_order_curves`.

**What `dP` is, and what it is not.** `dP(own, opp) = P(win round | own, opp-1) - P(win round | own, opp)` — the round-win probability a kill in that state buys. It is an **observational contrast between two state values, not the causal value of crossing the state**: teams that reach `3v2` differ from teams that reach `3v3` in ways beyond the kill, and nothing here adjusts for that. It is used as a prior shape and a descriptive benchmark, never as a causal estimate, and the report says so.

**Cross-fitting is mandatory.** `dP` is estimated from *round outcomes*. Used as a candidate graph (G1a), as a basis (G1b, G2) or as a shrinkage prior (G3), it must be built from **training matches only** — the same discipline the parent spec applied to `V(state)`. An all-data table would put test-match outcomes into every candidate.

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/test_kill_order_curves.py
"""The empirical swing table and the curve parameterizations. Pure: the
state visits are constructed directly, no DB."""

import numpy as np
import pytest

from app.services.kill_order_curves import (
    EVEN_STATE,
    MARGIN,
    TOTAL_ALIVE,
    estimate_swing_table,
)
from app.services.kill_order_leverage import PARAMS, PARAM_INDEX, StateVisitRow


def visits(spec):
    """spec: {(own, opp): (wins, losses)} -> a flat list of visit rows."""
    rows = []
    round_id = 0
    for (own, opp), (wins, losses) in spec.items():
        for won in [True] * wins + [False] * losses:
            round_id += 1
            rows.append(StateVisitRow(match_id=round_id % 7, round_id=round_id,
                                      own=own, opp=opp, won=won))
    return rows


def test_state_axes_are_fixed_constants_over_the_25_lattice_states():
    """Standardized over the STATES, unweighted -- so the transform carries
    no data dependence and needs no per-fold recomputation."""
    assert MARGIN.shape == (len(PARAMS),)
    assert TOTAL_ALIVE.shape == (len(PARAMS),)
    lattice = slice(0, 25)
    assert np.isclose(MARGIN[lattice].mean(), 0.0, atol=1e-12)
    assert np.isclose(MARGIN[lattice].std(), 1.0, atol=1e-12)
    assert np.isclose(TOTAL_ALIVE[lattice].mean(), 0.0, atol=1e-12)
    assert MARGIN[PARAM_INDEX["5v1"]] > MARGIN[PARAM_INDEX["1v5"]]
    assert TOTAL_ALIVE[PARAM_INDEX["5v5"]] > TOTAL_ALIVE[PARAM_INDEX["1v1"]]


def test_the_fallback_parameter_sits_at_the_origin_of_both_axes():
    """It has no state, so it receives no tilt. Anything else would invent
    a position for it."""
    assert MARGIN[PARAM_INDEX["fallback"]] == 0.0
    assert TOTAL_ALIVE[PARAM_INDEX["fallback"]] == 0.0
    assert EVEN_STATE[PARAM_INDEX["fallback"]] == 0.0


def test_even_state_indicator_marks_only_the_diagonal():
    for own in range(1, 6):
        assert EVEN_STATE[PARAM_INDEX[f"{own}v{own}"]] == 1.0
    assert EVEN_STATE[PARAM_INDEX["3v2"]] == 0.0


def test_swing_is_the_difference_between_neighbouring_state_win_rates():
    table = estimate_swing_table(visits({
        (3, 3): (50, 50),   # P(win) = 0.5
        (3, 2): (75, 25),   # P(win) = 0.75 -- the state a 3v3 kill reaches
    }))
    assert np.isclose(table.dp[PARAM_INDEX["3v3"]], 0.25)
    assert table.visits[PARAM_INDEX["3v3"]] == 100


def test_a_state_with_no_successor_data_is_marked_rather_than_guessed():
    table = estimate_swing_table(visits({(3, 3): (50, 50)}))
    assert np.isnan(table.dp[PARAM_INDEX["3v3"]])
    assert table.incomplete == ["3v3"]


def test_opp_zero_is_a_won_round_and_supplies_the_1v1_successor():
    table = estimate_swing_table(visits({
        (1, 1): (40, 60),
        (1, 0): (100, 0),
    }))
    assert np.isclose(table.dp[PARAM_INDEX["1v1"]], 0.6)


def test_the_fallback_parameter_has_no_swing_value():
    table = estimate_swing_table(visits({(3, 3): (1, 1), (3, 2): (1, 1)}))
    assert np.isnan(table.dp[PARAM_INDEX["fallback"]])


def test_estimating_from_a_training_subset_ignores_held_out_matches():
    """The cross-fitting guarantee, as a test rather than a comment. If the
    held-out rows leaked, the 3v3 win rate would move off 0.5."""
    training = visits({(3, 3): (50, 50), (3, 2): (50, 50)})
    held_out = [StateVisitRow(match_id=999, round_id=10_000 + i, own=3, opp=3, won=True)
                for i in range(500)]
    from_training_only = estimate_swing_table(training)
    assert np.isclose(from_training_only.dp[PARAM_INDEX["3v3"]], 0.0)
    polluted = estimate_swing_table(training + held_out)
    assert not np.isclose(polluted.dp[PARAM_INDEX["3v3"]], 0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -v`
Expected: FAIL — `No module named 'app.services.kill_order_curves'`

- [ ] **Step 3: Add `StateVisitRow` and the collector to `kill_order_leverage`**

```python
# append to webapp/app/services/kill_order_leverage.py

from app.scoring.impact import _did_team_win


@dataclass(frozen=True)
class StateVisitRow:
    """One team's view of one man-advantage state the round passed through.

    Every state entry produces TWO rows, one per team, mirrored: at (3, 2)
    for team A the same instant is (2, 3) for team B. That is what makes
    P(win | own, own) come out at exactly 0.5 by construction, which is a
    useful sanity check on the whole table.
    """

    match_id: int
    round_id: int
    own: int
    opp: int
    won: bool


def state_visits_for_match(db, match_id: int) -> list[StateVisitRow]:
    """Replay the alive-count walk again, recording state entries rather
    than kill terms. Uses impact.py's resurrection rule, like everything
    else here."""
    rounds = (
        db.query(Round).filter(Round.match_id == match_id)
        .filter(NOT_A_SURRENDER_ROUND).order_by(Round.round_number).all()
    )
    outcome_by_round_id = {r.id: r.outcome for r in rounds}
    teams = {mp.id: mp.team for mp in db.query(MatchPlayer).filter_by(match_id=match_id).all()}

    kills_by_round: dict[int, list] = defaultdict(list)
    for event in (
        db.query(KillEvent).join(Round).filter(Round.match_id == match_id)
        .order_by(KillEvent.event_time_seconds, KillEvent.id).all()
    ):
        if event.round_id in outcome_by_round_id:
            kills_by_round[event.round_id].append(event)

    out: list[StateVisitRow] = []
    # EVERY eligible round, not just those with kills. A round that ends by
    # defuse or time expiry with no kills still starts at 5v5, and dropping
    # it biases P(win | 5v5) toward rounds that contained a kill.
    for round_id in outcome_by_round_id:
        events = kills_by_round.get(round_id, [])
        outcome = outcome_by_round_id[round_id]
        if not outcome or "Team " not in outcome:
            continue
        try:
            team_1_won = _did_team_win(outcome, Team.TEAM_1)
        except (IndexError, ValueError):
            continue

        alive_1 = alive_2 = 5

        def record():
            out.append(StateVisitRow(match_id, round_id, alive_1, alive_2, team_1_won))
            out.append(StateVisitRow(match_id, round_id, alive_2, alive_1, not team_1_won))

        record()
        for position, event in enumerate(events):
            plain = [
                {"killer_match_player_id": e.killer_match_player_id,
                 "death_match_player_id": e.death_match_player_id,
                 "event_time_seconds": e.event_time_seconds}
                for e in events
            ]
            if _check_for_resurrection(position, plain):
                continue
            victim = event.death_match_player_id
            if victim is None:
                continue
            if teams[victim] == Team.TEAM_1:
                alive_1 -= 1
            else:
                alive_2 -= 1
            if alive_1 < 0 or alive_2 < 0:
                break
            record()
    return out
```

- [ ] **Step 4: Create `kill_order_curves` with the axes and the table**

```python
# webapp/app/services/kill_order_curves.py
"""Candidate kill-order curves: the empirical swing table, the
parameterizations built on it, and the recovery of a deployable graph from
fitted coefficients.

dP is an OBSERVATIONAL contrast between two state values, not the causal
value of crossing the state. It is a prior shape and a descriptive
benchmark; the report must not describe it as a causal effect.
"""

from dataclasses import dataclass, field

import numpy as np

from app.services.kill_order_leverage import PARAM_INDEX, PARAMS

_LATTICE = [(own, opp) for own in range(1, 6) for opp in range(1, 6)]


def _axis(values) -> np.ndarray:
    """Standardize over the 25 lattice states, UNWEIGHTED, and park the
    fallback at the origin.

    Unweighted on purpose: exposure weighting would make the transform
    depend on data and therefore need per-fold recomputation and a leakage
    argument. These are fixed constants instead.
    """
    raw = np.asarray(values, dtype=float)
    standardized = (raw - raw.mean()) / raw.std()
    out = np.zeros(len(PARAMS))
    out[: len(_LATTICE)] = standardized
    return out


MARGIN = _axis([own - opp for own, opp in _LATTICE])
TOTAL_ALIVE = _axis([own + opp for own, opp in _LATTICE])
EVEN_STATE = np.zeros(len(PARAMS))
for _own, _opp in _LATTICE:
    if _own == _opp:
        EVEN_STATE[PARAM_INDEX[f"{_own}v{_opp}"]] = 1.0


@dataclass(frozen=True)
class SwingTable:
    dp: np.ndarray                      # (26,), NaN where undetermined
    visits: np.ndarray                  # (26,) state-entry counts
    win_rate: dict                      # (own, opp) -> P(win), for the report
    incomplete: list = field(default_factory=list)


def estimate_swing_table(visit_rows) -> SwingTable:
    """dP per parameter from state-entry win rates.

    MUST be called with TRAINING rows only wherever the result feeds a
    candidate, a basis or a prior -- it is estimated from round outcomes,
    so an all-data table leaks held-out outcomes into every candidate.
    """
    wins: dict[tuple[int, int], int] = {}
    counts: dict[tuple[int, int], int] = {}
    for row in visit_rows:
        key = (row.own, row.opp)
        counts[key] = counts.get(key, 0) + 1
        wins[key] = wins.get(key, 0) + (1 if row.won else 0)

    win_rate = {key: wins[key] / counts[key] for key in counts}

    dp = np.full(len(PARAMS), np.nan)
    visit_counts = np.zeros(len(PARAMS))
    incomplete: list[str] = []
    for own, opp in _LATTICE:
        name = f"{own}v{opp}"
        index = PARAM_INDEX[name]
        visit_counts[index] = counts.get((own, opp), 0)
        here = win_rate.get((own, opp))
        after = win_rate.get((own, opp - 1))
        if here is None or after is None:
            incomplete.append(name)
            continue
        dp[index] = after - here
    # The fallback has no state, so it has no swing value. Candidates that
    # need a number there pin it; they never interpolate one.
    return SwingTable(dp=dp, visits=visit_counts, win_rate=win_rate, incomplete=incomplete)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add webapp/app/services/kill_order_leverage.py webapp/app/services/kill_order_curves.py webapp/tests/test_kill_order_curves.py
git commit -m "Add state-visit collection and the empirical swing table" -m "dP(own,opp) is the round-win probability a kill in that state buys, estimated from state-entry win rates. Observational, not causal, and must be built from training matches only wherever it feeds a candidate." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Candidate representation, recovery and deployability

**Files:**
- Modify: `webapp/app/services/kill_order_curves.py` (append)
- Test: `webapp/tests/test_kill_order_curves.py` (append)

**Interfaces:**
- Consumes: `SwingTable`, `MARGIN`, `TOTAL_ALIVE`, `EVEN_STATE` from Task 6; `TeamLeverageRow` from Task 4; `fit_logistic`, `standardize`, `back_transform` from `stats_math`.
- Produces: `family_a_leverage(team_rows) -> np.ndarray`, `basis_for(name, table) -> np.ndarray`, `ScoredCandidate`, `recover_graph(beta, damage_index, basis) -> tuple[np.ndarray, float]`, `check_deployable(graph, d, exposure) -> tuple[bool, list[str]]`, `score_rounds(leverage, damage_diff, graph) -> np.ndarray`, `normalize_for_display(graph, exposure) -> np.ndarray`, `construction_normalize(graph, exposure, reference) -> np.ndarray`.

**The two rules this task implements, and getting either wrong invalidates everything downstream.**

*Recovery.* The fit produces `eta = ... + d*damage_diff + SUM_k q_k*x_r[k]`. The deployable graph is `b = q/d`, and it requires `d > 0` — a fit with `d <= 0` has no graph at all and is reported non-deployable rather than rescaled into one. Every yardstick scores `S_r = damage_diff + SUM_k b_k*x_r[k]`, never `eta`.

*Two normalizations that must not be confused.* **Construction** normalization defines a candidate and is therefore scored: `dP` values live in [0, 1] and the shipped graph on a 40-250 scale, so a plug-in is not a candidate until its scale is fixed, and G1a's rule uses **training-fold** exposure and a **training-fold** reference. **Display** normalization to mean 136.6 is applied to a *copy* for reading, never before scoring, because rescaling `b` without `d` changes its strength relative to damage and evaluates a candidate nobody proposed.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_curves.py

from app.services.kill_order_curves import (
    ScoredCandidate,
    basis_for,
    check_deployable,
    construction_normalize,
    family_a_leverage,
    normalize_for_display,
    recover_graph,
    score_rounds,
)
from app.services.kill_order_leverage import COMPONENTS, shipped_graph


class FakeTeamRow:
    def __init__(self, kill, death, damage_diff=0.0):
        self.kill = kill
        self.death = death
        self.death_untraded = death
        self.damage_diff = damage_diff


def rows_with(values, damage=0.0):
    """One row whose (kill + death) leverage equals `values` per parameter,
    spread evenly across the three components."""
    kill = np.zeros((len(PARAMS), len(COMPONENTS)))
    for name, value in values.items():
        kill[PARAM_INDEX[name], :] = value / len(COMPONENTS)
    return FakeTeamRow(kill=kill, death=np.zeros_like(kill), damage_diff=damage)


def test_family_a_leverage_collapses_components_with_the_shipped_weights():
    """FACTOR_WEIGHTS are all 1.0 and divide by 3, so Family A's single
    column per parameter is the mean over components of kill + death."""
    row = rows_with({"3v3": 6.0})
    X = family_a_leverage([row])
    assert X.shape == (1, len(PARAMS))
    assert np.isclose(X[0, PARAM_INDEX["3v3"]], 2.0)


def test_bases_have_the_expected_widths_and_nest():
    table = type("T", (), {"dp": np.linspace(0.01, 0.5, len(PARAMS))})()
    assert basis_for("G1b", table).shape == (len(PARAMS), 2)
    assert basis_for("G2", table).shape == (len(PARAMS), 5)
    assert basis_for("G4", table).shape == (len(PARAMS), len(PARAMS))
    # G2 contains G1b: zeroing its last three coefficients leaves the affine fit.
    g2 = basis_for("G2", table)
    assert np.allclose(g2[:, :2], basis_for("G1b", table))


def test_a_nan_dp_is_pinned_rather_than_propagated_into_the_basis():
    """The fallback has no dP. If it reached the basis as NaN the whole fit
    would return NaN, silently."""
    dp = np.full(len(PARAMS), 0.2)
    dp[PARAM_INDEX["fallback"]] = np.nan
    table = type("T", (), {"dp": dp})()
    for name in ("G1b", "G2"):
        assert np.all(np.isfinite(basis_for(name, table)))


def test_recover_divides_every_coefficient_by_the_damage_coefficient():
    basis = np.eye(len(PARAMS))
    beta = np.zeros(1 + 1 + len(PARAMS))
    beta[1] = 2.0                       # d
    beta[2:] = 8.0                      # q
    graph, d = recover_graph(beta, damage_index=0, basis=basis)
    assert np.isclose(d, 2.0)
    assert np.allclose(graph, 4.0)


def test_recovery_refuses_a_non_positive_damage_coefficient():
    basis = np.eye(len(PARAMS))
    beta = np.zeros(1 + 1 + len(PARAMS))
    beta[1] = -0.5
    beta[2:] = 8.0
    graph, d = recover_graph(beta, damage_index=0, basis=basis)
    assert d == -0.5
    ok, reasons = check_deployable(graph, d, exposure=np.ones(len(PARAMS)))
    assert not ok
    assert any("damage" in r for r in reasons)


def test_deployability_rejects_a_negative_price_with_real_exposure():
    graph = shipped_graph().copy()
    graph[PARAM_INDEX["3v3"]] = -20.0
    exposure = np.ones(len(PARAMS)) * 1000
    ok, reasons = check_deployable(graph, d=1.0, exposure=exposure)
    assert not ok
    assert any("3v3" in r for r in reasons)


def test_deployability_ignores_a_negative_price_with_no_exposure():
    graph = shipped_graph().copy()
    graph[PARAM_INDEX["1v5"]] = -1.0
    exposure = np.ones(len(PARAMS)) * 1000
    exposure[PARAM_INDEX["1v5"]] = 0.0
    ok, _ = check_deployable(graph, d=1.0, exposure=exposure)
    assert ok


def test_scoring_is_damage_plus_the_graph_weighted_leverage():
    row = rows_with({"3v3": 6.0}, damage=12.0)
    X = family_a_leverage([row])
    graph = np.zeros(len(PARAMS))
    graph[PARAM_INDEX["3v3"]] = 10.0
    scores = score_rounds(X, np.array([12.0]), graph)
    assert np.isclose(scores[0], 12.0 + 2.0 * 10.0)


def test_construction_normalization_matches_a_training_reference_mean():
    exposure = np.zeros(len(PARAMS))
    exposure[PARAM_INDEX["3v3"]] = 3.0
    exposure[PARAM_INDEX["5v5"]] = 1.0
    raw = np.zeros(len(PARAMS))
    raw[PARAM_INDEX["3v3"]] = 0.2
    raw[PARAM_INDEX["5v5"]] = 0.6
    reference = shipped_graph()
    scaled = construction_normalize(raw, exposure, reference)
    target = float(np.sum(exposure * reference) / exposure.sum())
    assert np.isclose(float(np.sum(exposure * scaled) / exposure.sum()), target)
    assert np.isclose(scaled[PARAM_INDEX["5v5"]] / scaled[PARAM_INDEX["3v3"]], 3.0)


def test_display_normalization_does_not_change_ordering():
    graph = shipped_graph()
    exposure = np.ones(len(PARAMS))
    shown = normalize_for_display(graph, exposure)
    assert np.isclose(float(np.sum(exposure * shown) / exposure.sum()), 136.6, atol=1e-6)
    assert np.array_equal(np.argsort(graph), np.argsort(shown))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -v`
Expected: FAIL — `cannot import name 'family_a_leverage'`

- [ ] **Step 3: Append the implementation**

```python
# append to webapp/app/services/kill_order_curves.py

from app.services.kill_order_leverage import COMPONENTS, FALLBACK_WEIGHT, PARAM_INDEX

DISPLAY_MEAN = 136.6  # the shipped graph's exposure-weighted mean, measured


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate reduced to what every yardstick needs: per-round scores
    on the RECOVERED parameterization, plus enough provenance to report it.

    `graph` is populated for Family A; `weights` for Family B, whose
    candidates are weightings over a fixed graph rather than graphs.
    """

    name: str
    scores: np.ndarray
    d: float
    deployable: bool
    reasons: tuple[str, ...] = ()
    graph: np.ndarray | None = None
    weights: np.ndarray | None = None


def family_a_leverage(team_rows) -> np.ndarray:
    """(n_rounds, 26). The shipped FACTOR_WEIGHTS are all 1.0 over a total
    of 3, so Family A's per-parameter column is the mean over components of
    the kill and death halves."""
    out = np.zeros((len(team_rows), len(PARAMS)))
    for index, row in enumerate(team_rows):
        out[index] = (row.kill + row.death).sum(axis=1) / len(COMPONENTS)
    return out


N_LATTICE = 25
LATTICE = slice(0, N_LATTICE)


def lattice_dp(table) -> np.ndarray:
    """The 25 lattice dP values, or a refusal.

    The fallback parameter is NOT included and is never imputed: it has no
    state, so it has no swing value, and the spec pins it at the shipped 100
    for every structured family. An earlier draft mean-imputed both the
    fallback and any undetermined lattice state, which quietly invented a
    number for a state the data could not speak to.
    """
    dp = np.asarray(table.dp, dtype=float)[LATTICE]
    if not np.all(np.isfinite(dp)):
        missing = [PARAMS[i] for i in np.flatnonzero(~np.isfinite(dp))]
        raise ValueError(
            f"swing table has no dP for {missing}; this fold cannot build a "
            f"structured candidate. Fail the fold rather than imputing."
        )
    return dp


def basis_for(name: str, table) -> np.ndarray:
    """(25, p) over the LATTICE ONLY. A structured candidate's lattice graph
    is `basis @ theta`; its fallback is pinned at FALLBACK_WEIGHT and enters
    the fit through the composite damage column instead."""
    dp = lattice_dp(table)
    ones = np.ones(N_LATTICE)
    if name == "G1b":
        return np.column_stack([ones, dp])
    if name == "G2":
        return np.column_stack(
            [ones, dp, dp ** 2, np.sign(MARGIN[LATTICE]), EVEN_STATE[LATTICE]]
        )
    if name in ("G3", "G4"):
        return np.eye(len(PARAMS))     # unstructured: the fallback is a free column
    raise ValueError(f"unknown basis {name!r}")


def recover_graph(beta, damage_index: int, basis: np.ndarray):
    """beta is [intercept, controls..., damage, theta...] in whatever column
    order the caller built. `damage_index` is the damage column's position
    among the NON-intercept columns.

    Returns (graph, d). The caller checks d > 0 via check_deployable; this
    does not silently repair it.
    """
    coefficients = np.asarray(beta, dtype=float)[1:]
    d = float(coefficients[damage_index])
    theta = coefficients[damage_index + 1 :]
    q = basis @ theta
    if d == 0.0:
        return np.full(len(PARAMS), np.nan), d
    return q / d, d


def check_deployable(graph, d: float, exposure, min_exposure: float = 1.0):
    """A candidate that cannot ship has not improved the metric.

    Non-negativity is a DEPLOYABILITY GATE, not a constraint on the fit:
    nothing is clipped during fitting, and a candidate needing negative
    prices is reported in full with the offending states listed.
    """
    reasons: list[str] = []
    if not np.isfinite(d) or d <= 0:
        reasons.append(f"damage coefficient d={d:.4g} is not positive; no graph is recoverable")
    graph = np.asarray(graph, dtype=float)
    exposure = np.asarray(exposure, dtype=float)
    if not np.all(np.isfinite(graph)):
        reasons.append("graph contains non-finite values")
    else:
        offending = [
            f"{PARAMS[i]}={graph[i]:.1f}"
            for i in range(len(PARAMS))
            if graph[i] < 0 and exposure[i] >= min_exposure
        ]
        if offending:
            reasons.append("negative price at " + ", ".join(offending))
    return (not reasons), reasons


def score_rounds(leverage, damage_diff, graph) -> np.ndarray:
    """S_r = damage_diff + SUM_k b_k * x_r[k]. This -- not eta -- is what
    every yardstick consumes."""
    return np.asarray(damage_diff, dtype=float) + np.asarray(leverage, dtype=float) @ np.asarray(
        graph, dtype=float
    )


def _exposure_weighted_mean(graph, exposure) -> float:
    exposure = np.asarray(exposure, dtype=float)
    total = exposure.sum()
    if total <= 0:
        raise ValueError("exposure is empty; cannot normalize")
    return float(np.sum(exposure * np.asarray(graph, dtype=float)) / total)


def construction_normalize(graph, exposure, reference):
    """DEFINES a candidate and is therefore scored. Used by G1a, whose raw
    dP values are probabilities and would otherwise sit ~1000x below the
    shipped scale, changing its balance against damage rather than its
    shape. `exposure` and `reference` must both come from TRAINING rows."""
    target = _exposure_weighted_mean(reference, exposure)
    current = _exposure_weighted_mean(graph, exposure)
    if current == 0:
        raise ValueError("cannot normalize a graph with zero exposure-weighted mean")
    return np.asarray(graph, dtype=float) * (target / current)


def normalize_for_display(graph, exposure):
    """A transform on a COPY, for reading and comparing shapes. Never
    applied before scoring: rescaling b without d changes its strength
    relative to damage and evaluates a candidate nobody proposed."""
    current = _exposure_weighted_mean(graph, exposure)
    if current == 0:
        return np.asarray(graph, dtype=float).copy()
    return np.asarray(graph, dtype=float) * (DISPLAY_MEAN / current)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -v`
Expected: PASS (19 tests — 8 from Task 6, 11 here)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_curves.py webapp/tests/test_kill_order_curves.py
git commit -m "Add candidate bases, graph recovery and the deployability gate" -m "A fit is not a graph until b=q/d is recovered, and d>0 is required. Construction normalization defines a candidate and is scored; display normalization is a copy for reading and never touches a score." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Fitting Family A — G0, G1a, G1b, G2, G3, G4

**Files:**
- Modify: `webapp/app/services/kill_order_curves.py` (append)
- Test: `webapp/tests/test_kill_order_curves.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 6-7; `fit_logistic`, `standardize`, `back_transform` from `stats_math`.
- Produces: `FAMILY_A = ("current_graph", "swing_plugin", "swing_affine", "swing_basis", "pooled", "free")`, `fit_family_a(name, train, test, table, l2, ...) -> ScoredCandidate`.

**Names map to the spec's labels:** `current_graph` = G0, `swing_plugin` = G1a, `swing_affine` = G1b, `swing_basis` = G2, `pooled` = G3, `free` = G4.

**G3 is the one that is easy to get silently wrong, so read this before writing it.** The deployable graph is `b = q/d`. Shrinking toward a prior by putting `X @ b_prior` in as an offset shrinks **`q`**, which drives `b` toward `b_prior/d` — measured, with `d_true = 3` and a prior of 0.6 everywhere, the offset version returns `b ≈ 73` where 0.6 was wanted. Two orders of magnitude off, no error raised.

The correct reparameterization folds the prior into the damage column:

```
    q = d * b_prior + delta

    eta = controls . gamma
          + d * ( damage_diff + SUM_k b_prior_k * x_r[k] )   <- one composite column
          + SUM_k delta_k * x_r[k]

    b = q / d = b_prior + delta / d
```

`delta = 0` recovers `b = b_prior` exactly. The penalty lands on `delta`, so in graph units it is `d^2 * ||b - b_prior||^2` — shrinkage toward the prior at a strength scaling with `d^2`. That is stated, not hidden: the report prints `d` beside the selected penalty, because the two are not comparable across folds without it.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_curves.py

from app.services.kill_order_curves import FAMILY_A, fit_family_a


def synthetic_fold(n=900, seed=3):
    """Rounds whose outcome genuinely depends on a known graph, so a fit
    has something to find. Two active parameters keep it small."""
    rng = np.random.default_rng(seed)
    truth = np.zeros(len(PARAMS))
    truth[PARAM_INDEX["5v5"]] = 150.0
    truth[PARAM_INDEX["3v3"]] = 180.0

    leverage = np.zeros((n, len(PARAMS)))
    leverage[:, PARAM_INDEX["5v5"]] = rng.normal(size=n)
    leverage[:, PARAM_INDEX["3v3"]] = rng.normal(size=n)
    damage = rng.normal(size=n) * 20.0
    score = damage + leverage @ truth
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-score / 60.0))).astype(float)
    return leverage, damage, y, truth


def flat_table():
    dp = np.full(len(PARAMS), 0.25)
    dp[PARAM_INDEX["3v3"]] = 0.30
    return type("T", (), {"dp": dp})()


def test_every_family_a_name_produces_a_scored_candidate():
    leverage, damage, y, _ = synthetic_fold()
    exposure = np.abs(leverage).sum(axis=0)
    for name in FAMILY_A:
        candidate = fit_family_a(
            name, train=(leverage, damage, y, None), test=(leverage, damage, None),
            table=flat_table(), l2=1.0, exposure=exposure, shipped=shipped_graph(),
        )
        assert isinstance(candidate, ScoredCandidate)
        assert candidate.scores.shape == (len(y),)
        assert candidate.name == name


def test_current_graph_scores_the_shipped_values_without_fitting():
    leverage, damage, y, _ = synthetic_fold()
    exposure = np.abs(leverage).sum(axis=0)
    candidate = fit_family_a(
        "current_graph", train=(leverage, damage, y, None), test=(leverage, damage, None),
        table=flat_table(), l2=1.0, exposure=exposure, shipped=shipped_graph(),
    )
    assert np.allclose(candidate.graph, shipped_graph())
    assert np.allclose(candidate.scores, damage + leverage @ shipped_graph())


def test_free_recovers_a_graph_close_to_the_truth_on_active_parameters():
    leverage, damage, y, truth = synthetic_fold(n=4000)
    exposure = np.abs(leverage).sum(axis=0)
    candidate = fit_family_a(
        "free", train=(leverage, damage, y, None), test=(leverage, damage, None),
        table=flat_table(), l2=0.01, exposure=exposure, shipped=shipped_graph(),
    )
    assert candidate.d > 0
    for name in ("5v5", "3v3"):
        assert candidate.graph[PARAM_INDEX[name]] == pytest.approx(
            truth[PARAM_INDEX[name]], rel=0.35
        )


def test_pooled_shrinks_the_DEPLOYED_graph_toward_the_prior_not_prior_over_d():
    """The regression test for the parameterization trap. With a large
    penalty the recovered graph must go to the prior, NOT to prior/d."""
    leverage, damage, y, _ = synthetic_fold(n=3000)
    exposure = np.abs(leverage).sum(axis=0)
    prior = np.full(len(PARAMS), 90.0)

    candidate = fit_family_a(
        "pooled", train=(leverage, damage, y, None), test=(leverage, damage, None),
        table=flat_table(), l2=1e6, exposure=exposure, shipped=shipped_graph(), prior=prior,
    )
    active = [PARAM_INDEX["5v5"], PARAM_INDEX["3v3"]]
    assert np.allclose(candidate.graph[active], prior[active], rtol=0.02)
    assert not np.allclose(candidate.graph[active], (prior / candidate.d)[active], rtol=0.02)


def test_pooled_with_no_penalty_moves_away_from_the_prior():
    leverage, damage, y, truth = synthetic_fold(n=4000)
    exposure = np.abs(leverage).sum(axis=0)
    prior = np.full(len(PARAMS), 90.0)
    candidate = fit_family_a(
        "pooled", train=(leverage, damage, y, None), test=(leverage, damage, None),
        table=flat_table(), l2=1e-6, exposure=exposure, shipped=shipped_graph(), prior=prior,
    )
    assert candidate.graph[PARAM_INDEX["3v3"]] > prior[PARAM_INDEX["3v3"]] * 1.2


def test_swing_plugin_is_construction_normalized_against_the_training_reference():
    leverage, damage, y, _ = synthetic_fold()
    exposure = np.abs(leverage).sum(axis=0)
    candidate = fit_family_a(
        "swing_plugin", train=(leverage, damage, y, None), test=(leverage, damage, None),
        table=flat_table(), l2=1.0, exposure=exposure, shipped=shipped_graph(),
    )
    target = float(np.sum(exposure * shipped_graph()) / exposure.sum())
    actual = float(np.sum(exposure * candidate.graph) / exposure.sum())
    assert np.isclose(actual, target)
    assert candidate.graph[PARAM_INDEX["3v3"]] > candidate.graph[PARAM_INDEX["5v5"]]


def test_a_fit_with_a_non_positive_damage_coefficient_is_marked_non_deployable():
    """Sign-flipped damage: the recovery is meaningless and must be said so
    rather than rescaled into something plausible."""
    leverage, damage, y, _ = synthetic_fold(n=2000)
    exposure = np.abs(leverage).sum(axis=0)
    candidate = fit_family_a(
        "free", train=(leverage, -damage, y, None), test=(leverage, -damage, None),
        table=flat_table(), l2=0.01, exposure=exposure, shipped=shipped_graph(),
    )
    if candidate.d <= 0:
        assert not candidate.deployable
        assert any("damage" in r for r in candidate.reasons)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -k family_a -v`
Expected: FAIL — `cannot import name 'FAMILY_A'`

- [ ] **Step 3: Append the fitter**

```python
# append to webapp/app/services/kill_order_curves.py

from app.services.stats_math import back_transform, fit_logistic, standardize

FAMILY_A = ("current_graph", "swing_plugin", "swing_affine", "swing_basis", "pooled", "free")
_FITTED_BASIS = {"swing_affine": "G1b", "swing_basis": "G2", "free": "G4"}


def _fit_and_recover(design_train, y_train, weights, l2, design_test, damage_index,
                     basis, penalty=None):
    """Standardize on TRAIN, fit, back-transform, recover (graph, d).

    Standardization is on training statistics and the coefficients are
    back-transformed before recovery, because b = q/d is a ratio of RAW-unit
    coefficients -- recovering from standardized ones would divide by the
    wrong scale.
    """
    scaled_train, _scaled_test, centre, scale = standardize(design_train, design_test)
    beta = fit_logistic(scaled_train, y_train, weights=weights, l2=l2, penalty=penalty)
    raw = back_transform(beta, centre, scale)
    graph, d = recover_graph(raw, damage_index=damage_index, basis=basis)
    return graph, d


def fit_family_a(name, train, test, table, l2, exposure, shipped, prior=None, controls=None):
    """One Family A candidate, fitted on `train` and scored on `test`.

    train = (leverage, damage_diff, y, weights); test = (leverage,
    damage_diff, weights). `exposure` and `table` must come from TRAINING
    rows -- both feed candidate construction.
    """
    train_leverage, train_damage, y_train, w_train = train
    test_leverage, test_damage, _w_test = test
    controls_train, controls_test = (controls or (None, None))

    def stack(*blocks):
        usable = [np.asarray(b, dtype=float) for b in blocks if b is not None and np.size(b)]
        return np.column_stack(usable)

    n_controls = 0 if controls_train is None else np.asarray(controls_train).shape[1]

    fallback = PARAM_INDEX["fallback"]

    if name == "current_graph":
        graph, d = np.asarray(shipped, dtype=float), 1.0
    elif name == "swing_plugin":
        # Normalize the 25 LATTICE values against the shipped lattice, then
        # pin the fallback. Including a parameter with no state in the
        # normalization would let it move the whole curve's scale.
        scaled = construction_normalize(
            lattice_dp(table), exposure[LATTICE], np.asarray(shipped)[LATTICE]
        )
        graph = np.empty(len(PARAMS))
        graph[LATTICE] = scaled
        graph[fallback] = FALLBACK_WEIGHT
        d = 1.0
    elif name == "pooled":
        if prior is None:
            prior = fit_family_a(
                "swing_plugin", train, test, table, l2, exposure, shipped
            ).graph
        b_prior = np.asarray(prior, dtype=float)
        composite = train_damage + train_leverage @ b_prior
        composite_test = test_damage + test_leverage @ b_prior
        design_train = stack(controls_train, composite, train_leverage)
        design_test = stack(controls_test, composite_test, test_leverage)
        # The composite damage column carries d and MUST stay unpenalised:
        # penalising it drives d and delta to zero together, and delta/d then
        # never converges on the prior. Measured -- see Task 1.
        mask = np.ones(design_train.shape[1])
        mask[n_controls] = 0.0
        delta_graph, d = _fit_and_recover(
            design_train, y_train, w_train, l2, design_test,
            damage_index=n_controls, basis=np.eye(len(PARAMS)), penalty=mask,
        )
        # recover_graph already divided by d, so delta_graph IS delta/d.
        graph = b_prior + delta_graph
    elif name == "free":
        design_train = stack(controls_train, train_damage, train_leverage)
        design_test = stack(controls_test, test_damage, test_leverage)
        graph, d = _fit_and_recover(
            design_train, y_train, w_train, l2, design_test,
            damage_index=n_controls, basis=np.eye(len(PARAMS)),
        )
    else:
        # Structured families: the fallback is PINNED at the shipped 100 and
        # rides in the composite damage column, so the basis fits the 25
        # lattice states only.
        basis = basis_for(_FITTED_BASIS[name], table)
        composite = train_damage + FALLBACK_WEIGHT * train_leverage[:, fallback]
        composite_test = test_damage + FALLBACK_WEIGHT * test_leverage[:, fallback]
        design_train = stack(controls_train, composite, train_leverage[:, LATTICE] @ basis)
        design_test = stack(controls_test, composite_test, test_leverage[:, LATTICE] @ basis)
        lattice_graph, d = _fit_and_recover(
            design_train, y_train, w_train, l2, design_test,
            damage_index=n_controls, basis=basis,
        )
        graph = np.empty(len(PARAMS))
        graph[LATTICE] = lattice_graph
        graph[fallback] = FALLBACK_WEIGHT

    deployable, reasons = check_deployable(graph, d, exposure)
    return ScoredCandidate(
        name=name,
        scores=score_rounds(test_leverage, test_damage, graph),
        d=float(d),
        deployable=deployable,
        reasons=tuple(reasons),
        graph=np.asarray(graph, dtype=float),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -v`
Expected: PASS (26 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_curves.py webapp/tests/test_kill_order_curves.py
git commit -m "Add Family A candidate fitting with correct prior shrinkage" -m "G0/G1a are fixed graphs; G1b/G2/G4 fit a basis; G3 folds the prior into the damage column so shrinkage lands on the deployed graph rather than on q. The regression test pins that: with a huge penalty the recovered graph goes to the prior, not prior/d." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Family B — the nested ladder B0 to B3

**Files:**
- Modify: `webapp/app/services/kill_order_curves.py` (append)
- Test: `webapp/tests/test_kill_order_curves.py` (append)

**Interfaces:**
- Consumes: Tasks 6-8; `MARGIN`, `TOTAL_ALIVE`.
- Produces: `FAMILY_B = ("stage_a_exact", "kd_split_base", "component_tilt", "component_tilt_symmetric")`, `family_b_columns(team_rows, graph, rung) -> tuple[np.ndarray, list[str]]`, `fit_family_b(rung, train, test, graph, l2, controls=None) -> ScoredCandidate`.

**Why a ladder rather than one candidate.** Running the full symmetric model against `stage_a_exact` changes three things at once — component-by-state tilts, a kill/death split of the base weights, and a kill/death split of the tilts — so a win cannot be attributed to any of them. It could come entirely from constant kill/death weights, which is not the hypothesis Family B exists to test.

| rung | coefficients | adds | comparison it enables |
|---|---|---|---|
| B0 `stage_a_exact` | 3 | — | the nested comparator |
| B1 `kd_split_base` | 6 | constant kill/death asymmetry | B1 vs B0 |
| B2 `component_tilt` | 9 | component-by-state curves | **B2 vs B0 — Family B's primary** |
| B3 `component_tilt_symmetric` | 18 | both together | B3 vs B1, B3 vs B2 |

**These are weightings over a FIXED graph, not graphs.** `b` is held at the shipped values (or, as a sensitivity, at the best Family A curve), so the fitted numbers are `(w, a, t)` per component and side. They still need the same `q/d` recovery as everything else — `damage` carries its own coefficient here too, so the 18 fitted numbers are `q`s and every one divides by `d`. The effective price surfaces and the non-negativity check use the recovered values.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_curves.py

from app.services.kill_order_curves import FAMILY_B, family_b_columns, fit_family_b


def fake_rows(n=600, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        kill = np.zeros((len(PARAMS), len(COMPONENTS)))
        death = np.zeros_like(kill)
        for name in ("5v5", "3v3", "2v2"):
            kill[PARAM_INDEX[name]] = rng.normal(size=len(COMPONENTS))
            death[PARAM_INDEX[name]] = rng.normal(size=len(COMPONENTS))
        rows.append(FakeTeamRow(kill=kill, death=death, damage_diff=rng.normal() * 20))
    return rows


def test_ladder_widths_are_3_6_9_and_18():
    rows = fake_rows(20)
    graph = shipped_graph()
    widths = {rung: family_b_columns(rows, graph, rung)[0].shape[1] for rung in FAMILY_B}
    assert widths == {
        "stage_a_exact": 3, "kd_split_base": 6,
        "component_tilt": 9, "component_tilt_symmetric": 18,
    }


def test_each_rung_is_nested_in_the_next_by_column_containment():
    """B0's columns must literally appear in B2's, and B1's in B3's --
    otherwise a 'nested' comparison is comparing different models."""
    rows = fake_rows(40)
    graph = shipped_graph()
    cols = {r: family_b_columns(rows, graph, r) for r in FAMILY_B}
    b0, names0 = cols["stage_a_exact"]
    b2, names2 = cols["component_tilt"]
    for position, name in enumerate(names0):
        assert name in names2
        assert np.allclose(b0[:, position], b2[:, names2.index(name)])
    b1, names1 = cols["kd_split_base"]
    b3, names3 = cols["component_tilt_symmetric"]
    for position, name in enumerate(names1):
        assert np.allclose(b1[:, position], b3[:, names3.index(name)])


def test_stage_a_exact_columns_are_the_three_component_totals():
    """B0 must reproduce the parent project's four-feature model on exact
    pre-rounding columns -- that is the whole point of stage_a_exact."""
    rows = fake_rows(30)
    graph = shipped_graph()
    columns, names = family_b_columns(rows, graph, "stage_a_exact")
    assert names == ["econ_base", "time_base", "swing_base"]
    expected = np.array([
        float((graph[:, None] * (r.kill + r.death))[:, 0].sum()) for r in rows
    ])
    assert np.allclose(columns[:, 0], expected)


def test_the_symmetric_rung_separates_kill_and_death():
    rows = fake_rows(30)
    columns, names = family_b_columns(rows, shipped_graph(), "component_tilt_symmetric")
    assert "econ_kill_base" in names and "econ_death_base" in names
    assert not np.allclose(columns[:, names.index("econ_kill_base")],
                           columns[:, names.index("econ_death_base")])


def test_tilt_columns_are_proportional_to_base_when_a_round_uses_one_state():
    """Getting this backwards ships a tilt column that silently duplicates
    a base column."""
    kill = np.zeros((len(PARAMS), len(COMPONENTS)))
    kill[PARAM_INDEX["3v2"]] = 1.0
    row = FakeTeamRow(kill=kill, death=np.zeros_like(kill))
    columns, names = family_b_columns([row], shipped_graph(), "component_tilt")
    base = columns[0, names.index("econ_base")]
    tilt = columns[0, names.index("econ_margin")]
    assert np.isclose(tilt, base * MARGIN[PARAM_INDEX["3v2"]])
    assert not np.isclose(tilt, base)


def test_family_b_recovers_weights_by_dividing_by_the_damage_coefficient():
    rows = fake_rows(1200)
    graph = shipped_graph()
    columns, _ = family_b_columns(rows, graph, "component_tilt")
    damage = np.array([r.damage_diff for r in rows])
    rng = np.random.default_rng(9)
    y = (rng.uniform(size=len(rows)) < 1 / (1 + np.exp(-(damage + columns[:, 0]) / 300.0))).astype(float)

    candidate = fit_family_b(
        "component_tilt", train=(rows, y, None), test=(rows, None), graph=graph, l2=1.0
    )
    assert candidate.weights is not None
    assert candidate.weights.shape == (9,)
    assert candidate.d != 0
    # The recovered weighting must reproduce the score it was fitted from.
    assert np.allclose(candidate.scores, damage + columns @ candidate.weights)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -k family_b -v`
Expected: FAIL — `cannot import name 'FAMILY_B'`

- [ ] **Step 3: Append the ladder**

```python
# append to webapp/app/services/kill_order_curves.py

FAMILY_B = ("stage_a_exact", "kd_split_base", "component_tilt", "component_tilt_symmetric")

_RUNG_SPEC = {
    # (split kill/death?, which axes)
    "stage_a_exact": (False, ("base",)),
    "kd_split_base": (True, ("base",)),
    "component_tilt": (False, ("base", "margin", "total")),
    "component_tilt_symmetric": (True, ("base", "margin", "total")),
}
_AXES = {"base": None, "margin": MARGIN, "total": TOTAL_ALIVE}


def family_b_columns(team_rows, graph, rung):
    """(n_rounds, p) plus column names.

    Each column is SUM_k graph[k] * axis[k] * block[k][component], where
    `block` is the kill half, the death half, or their sum when the rung
    does not split them. `graph` is FIXED -- Family B fits weightings over a
    curve, not the curve itself.
    """
    split, axes = _RUNG_SPEC[rung]
    graph = np.asarray(graph, dtype=float)
    sides = ("kill", "death") if split else ("combined",)

    names: list[str] = []
    for component in COMPONENTS:
        for side in sides:
            for axis in axes:
                label = component if side == "combined" else f"{component}_{side}"
                names.append(f"{label}_{axis}")

    columns = np.zeros((len(team_rows), len(names)))
    for index, row in enumerate(team_rows):
        blocks = {"kill": row.kill, "death": row.death, "combined": row.kill + row.death}
        position = 0
        for component_index in range(len(COMPONENTS)):
            for side in sides:
                block = np.asarray(blocks[side], dtype=float)[:, component_index]
                for axis in axes:
                    weight = graph if _AXES[axis] is None else graph * _AXES[axis]
                    columns[index, position] = float(np.sum(weight * block))
                    position += 1
    return columns, names


def fit_family_b(rung, train, test, graph, l2, controls=None, exposure=None):
    # `exposure` IS used: it gates which negative effective prices count.
    """One rung, fitted on train and scored on test.

    train = (team_rows, y, weights); test = (team_rows, weights).

    The fitted numbers are regression coefficients q, NOT deployable
    weights: damage carries its own coefficient d here too, so every one
    divides by d. An earlier draft claimed they were directly (w, a, t) and
    was wrong.
    """
    train_rows, y_train, w_train = train
    test_rows, _w_test = test
    controls_train, controls_test = (controls or (None, None))
    n_controls = 0 if controls_train is None else np.asarray(controls_train).shape[1]

    train_columns, names = family_b_columns(train_rows, graph, rung)
    test_columns, _ = family_b_columns(test_rows, graph, rung)
    train_damage = np.array([r.damage_diff for r in train_rows], dtype=float)
    test_damage = np.array([r.damage_diff for r in test_rows], dtype=float)

    def stack(controls_block, damage, columns):
        usable = [b for b in (controls_block, damage[:, None], columns) if b is not None]
        return np.column_stack(usable)

    design_train = stack(controls_train, train_damage, train_columns)
    design_test = stack(controls_test, test_damage, test_columns)

    scaled_train, _scaled_test, centre, scale = standardize(design_train, design_test)
    beta = fit_logistic(scaled_train, y_train, weights=w_train, l2=l2)
    raw = back_transform(beta, centre, scale)[1:]
    d = float(raw[n_controls])
    weights = raw[n_controls + 1 :] / d if d != 0 else np.full(len(names), np.nan)

    reasons: list[str] = []
    if not np.isfinite(d) or d <= 0:
        reasons.append(f"damage coefficient d={d:.4g} is not positive; no weighting is recoverable")
    else:
        # An earlier draft checked ONLY d here, so a rung could carry a
        # negative effective price at a well-exposed state and still be
        # marked deployable. Build every surface and check it.
        for label, surface in effective_surfaces(weights, names, graph).items():
            offending = [
                f"{PARAMS[i]}={surface[i]:.1f}"
                for i in range(len(PARAMS))
                if surface[i] < 0 and (exposure is None or exposure[i] >= 1.0)
            ]
            if offending:
                reasons.append(f"negative effective price in {label} at " + ", ".join(offending))

    return ScoredCandidate(
        name=rung,
        scores=test_damage + test_columns @ weights,
        d=d,
        deployable=not reasons,
        reasons=tuple(reasons),
        weights=weights,
    )


def effective_surfaces(weights, names, graph) -> dict:
    """The 26-value price curve each (component, side) ends up with once its
    base weight and tilts are applied. These -- not the raw coefficients --
    are what the report prints and what the deployability gate checks.

    A tilt coefficient is meaningless on its own: its size is relative to its
    base weight, and Stage A's headline finding is that the econ base
    collapses to ~0.
    """
    graph = np.asarray(graph, dtype=float)
    weights = np.asarray(weights, dtype=float)
    multiplier = {"base": np.ones(len(PARAMS)), "margin": MARGIN, "total": TOTAL_ALIVE}
    by_label: dict = {}
    for value, name in zip(weights, names):
        label, axis = name.rsplit("_", 1)
        by_label[label] = by_label.get(label, np.zeros(len(PARAMS))) + value * multiplier[axis]
    return {label: graph * surface for label, surface in by_label.items()}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_curves.py -v`
Expected: PASS (32 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_curves.py webapp/tests/test_kill_order_curves.py
git commit -m "Add the Family B nested ladder B0-B3" -m "Comparing the full symmetric model against stage_a_exact changed three things at once, so a win could not be attributed. The ladder separates component tilts from the kill/death split, with B2 vs B0 as the primary. Coefficients recover through q/d like every other candidate." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Target alignment and nested cross-validation

**Files:**
- Create: `webapp/app/services/kill_order_refit.py`
- Test: `webapp/tests/test_kill_order_refit.py` (new)

**Interfaces:**
- Consumes: Tasks 2-9; `PRIMARY_T1`, `PRIMARY_T2`, `build_target`, `controls_for`, `group_by_match`, `stable_folds`, `load_all_observations` from `impact_eval`; `platt_calibrate`, `apply_calibration`, `weighted_log_loss` from `stats_math`.
- Produces: `AlignedTarget`, `align_target(leverage_rows, observations, config) -> AlignedTarget`, `FoldFit`, `run_nested_cv(leverage_rows, observations, config, candidates, l2_grid, ...) -> dict[str, CandidateResult]`, `CandidateResult`.

**Why alignment needs its own function.** The parent's target builders return a `FitDataset` whose `X` is built from `RoundObservation` features — but Stage C's features are leverage vectors, not those columns. What Stage C needs from the parent is only `y` and the sample weights, plus which *rounds* each target row draws on. T1 collapses a match's twelve first-half rounds into one row; T2 collapses round N into one row carrying a weighted fraction of later rounds; WPA is one row per round. So the alignment differs per target and must be explicit.

**It is not allowed to drift from the parent's semantics**, so Step 1 tests it *against* `build_target` rather than re-deriving what the right answer is: same config, same `y`, same weights, same row count.

**Every leakage rule from the spec lands here**, and this is the only task where they interact: the swing table, the exposure vector, the shrinkage prior, the L2 selection and the probability calibration are all built from training matches only, inside the fold loop.

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/test_kill_order_refit.py
"""Nested CV over leverage rows. Pure: synthetic observations and leverage,
no DB."""

import numpy as np
import pytest

from app.services.impact_eval import PRIMARY_T1, PRIMARY_T2, build_target, stable_folds
from app.services.kill_order_curves import FAMILY_A
from app.services.kill_order_leverage import COMPONENTS, PARAMS, PARAM_INDEX, shipped_graph
from app.services.kill_order_refit import align_target, run_nested_cv


def test_alignment_reproduces_the_parent_targets_y_and_weights():
    """The anti-drift gate. Stage C builds its own design matrix but must
    predict exactly the quantity the parent project's target defines."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    for config in (PRIMARY_T1, PRIMARY_T2):
        aligned = align_target(leverage, observations, config)
        reference = build_target(observations, config, ["damage"])
        assert len(aligned.y) == len(reference.y)
        assert np.allclose(aligned.y, reference.y)
        assert np.allclose(aligned.weights, reference.w)
        assert np.array_equal(aligned.match_ids, reference.match_ids)


def test_t1_sums_leverage_over_the_first_half():
    observations = synthetic_observations(matches=3)
    leverage = leverage_for(observations)
    aligned = align_target(leverage, observations, PRIMARY_T1)
    assert aligned.leverage.shape == (3, len(PARAMS))
    first_match = [r for r in leverage if r.match_id == observations[0].match_id
                   and r.round_number <= 12]
    expected = sum((r.kill + r.death).sum(axis=1) / len(COMPONENTS) for r in first_match)
    assert np.allclose(aligned.leverage[0], expected)


def test_t2_keeps_one_row_per_source_round():
    observations = synthetic_observations(matches=5)
    leverage = leverage_for(observations)
    aligned = align_target(leverage, observations, PRIMARY_T2)
    assert len(set(aligned.round_ids)) == len(aligned.round_ids)


def test_nested_cv_never_scores_a_match_its_model_trained_on():
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph", "free"], l2_grid=[1.0], n_folds=5)
    for result in results.values():
        for fold, fitted in result.per_fold.items():
            assert set(fitted.train_match_ids).isdisjoint(fitted.test_match_ids)


def test_the_swing_table_and_exposure_come_from_training_matches_only():
    """A held-out match with a wildly different state distribution must not
    move the fold's swing table."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["swing_plugin"], l2_grid=[1.0], n_folds=5)
    tables = [f.swing_table for f in results["swing_plugin"].per_fold.values()]
    assert len({id(t) for t in tables}) == len(tables), "one table per fold, not one shared"
    assert not np.allclose(tables[0].visits, tables[1].visits)


def test_l2_is_selected_inside_the_training_fold():
    observations = synthetic_observations(matches=80)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["free"], l2_grid=[0.01, 1.0, 100.0], n_folds=5)
    chosen = [f.l2 for f in results["free"].per_fold.values()]
    assert len(chosen) == 5
    assert all(value in (0.01, 1.0, 100.0) for value in chosen)


def test_calibration_is_fitted_inside_each_outer_fold():
    """A fitted candidate's pooled scores come from five different models,
    so calibrating over the pooled scores would put a score in the
    calibration training set whose own model saw that match."""
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["free"], l2_grid=[1.0], n_folds=5)
    result = results["free"]
    assert len(result.oof_probabilities) == len(result.oof_scores)
    assert np.all((result.oof_probabilities > 0) & (result.oof_probabilities < 1))
    assert len({f.calibration.tobytes() for f in result.per_fold.values()}) > 1


def test_every_candidate_is_scored_on_identical_rows():
    """Different candidates differ only in coefficients; if their row sets
    diverged, the paired comparisons downstream would be meaningless."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph", "swing_plugin", "free"],
                            l2_grid=[1.0], n_folds=5)
    reference = results["current_graph"].oof_row_ids
    for result in results.values():
        assert np.array_equal(result.oof_row_ids, reference)
```

Add these fixture builders at the top of the file — they are used by Tasks 10-12 and must produce data with real signal, not noise:

```python
def synthetic_observations(matches=40, seed=17):
    """RoundObservations with enough structure for a target to be
    non-degenerate: 24 rounds each, alternating winners with a per-match
    bias so the match outcome is predictable but not deterministic."""
    from app.services.impact_eval import RoundObservation

    rng = np.random.default_rng(seed)
    out = []
    round_id = 0
    for match_index in range(matches):
        bias = rng.uniform(0.3, 0.7)
        team_a_wins = 0
        rounds = []
        for number in range(1, 25):
            round_id += 1
            won = bool(rng.uniform() < bias)
            team_a_wins += won
            rounds.append((round_id, number, won))
        match_won = team_a_wins > 12
        for rid, number, won in rounds:
            out.append(RoundObservation(
                match_id=1000 + match_index, round_id=rid, round_number=number,
                damage=rng.normal() * 20, econ_impact=0.0, time_impact=0.0,
                swing_impact=0.0, kill_diff=0.0, acs_diff=0.0, impact_diff=0.0,
                score_diff_before=0, attacking_is_team_a=number <= 12,
                loadout_diff=0.0, full_buy_count_diff=0,
                round_won_by_team_a=won, match_won_by_team_a=match_won,
                is_terminal=number == 24,
            ))
    return out


def leverage_for(observations, seed=23):
    """One TeamLeverageRow per observation, with signal on three states."""
    from app.services.kill_order_leverage import TeamLeverageRow

    rng = np.random.default_rng(seed)
    rows = []
    for obs in observations:
        kill = np.zeros((len(PARAMS), len(COMPONENTS)))
        death = np.zeros_like(kill)
        pull = 1.0 if obs.round_won_by_team_a else -1.0
        for name in ("5v5", "4v4", "3v3"):
            kill[PARAM_INDEX[name]] = pull * abs(rng.normal(size=len(COMPONENTS)))
            death[PARAM_INDEX[name]] = pull * abs(rng.normal(size=len(COMPONENTS))) * 0.6
        rows.append(TeamLeverageRow(
            match_id=obs.match_id, round_id=obs.round_id, round_number=obs.round_number,
            damage_diff=obs.damage, kill=kill, death=death, death_untraded=death,
        ))
    return rows
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: FAIL — `No module named 'app.services.kill_order_refit'`

- [ ] **Step 3: Write the module**

```python
# webapp/app/services/kill_order_refit.py
"""Nested cross-validation, the control ladder, diagnostics and verdicts
for the kill-order graph refit.

Every leakage rule in the spec lands in run_nested_cv, and they interact:
the swing table, the exposure vector, the shrinkage prior, the L2 choice
and the probability calibration are ALL built from training matches only,
inside the fold loop. Hoisting any one of them out of the loop for speed
silently invalidates every held-out number this module produces.
"""

from dataclasses import dataclass, field

import numpy as np

from app.services.impact_eval import (
    FIRST_HALF_ROUNDS,
    build_target,
    controls_for,
    group_by_match,
    stable_folds,
)
from app.services.kill_order_curves import (
    FAMILY_A,
    estimate_swing_table,
    family_a_leverage,
    fit_family_a,
)
from app.services.kill_order_leverage import PARAMS, shipped_graph
from app.services.stats_math import (
    apply_calibration,
    fit_logistic,
    platt_calibrate,
    predict_proba,
    standardize,
    weighted_log_loss,
)


@dataclass(frozen=True)
class AlignedTarget:
    """A frozen target's y, weights and CONTROLS, joined to Stage C's
    leverage rows.

    y and weights come from the parent project's own builders -- Stage C
    predicts exactly the quantity Stage A did, and a test pins that.

    `controls` is not optional and an earlier draft omitted it, which
    changed the estimand: the spec's equation is
    `eta = controls.gamma + d*damage + SUM q_k x_k`, and without the control
    block the graph absorbs score differential, side, economy and the
    round's own result -- exactly the information the ladder exists to
    condition on. Which controls apply is target-specific and comes from the
    parent's `controls_for`, not from a choice made here.
    """

    leverage: np.ndarray      # (n_rows, 26)
    damage: np.ndarray        # (n_rows,)
    controls: np.ndarray      # (n_rows, n_controls), possibly zero-width
    control_names: tuple
    y: np.ndarray
    weights: np.ndarray
    match_ids: np.ndarray
    round_ids: np.ndarray     # source round per row; -1 where a row spans many


def align_target(leverage_rows, observations, config) -> AlignedTarget:
    reference = build_target(observations, config, ["damage"])
    by_round = {row.round_id: row for row in leverage_rows}
    per_round = family_a_leverage(list(by_round.values()))
    index_of = {round_id: i for i, round_id in enumerate(by_round)}

    if config.name == "T1":
        # One row per eligible match: the first half summed.
        rows, damage, round_ids = [], [], []
        for match_id in reference.match_ids:
            members = [
                r for r in leverage_rows
                if r.match_id == match_id and r.round_number <= FIRST_HALF_ROUNDS
            ]
            rows.append(sum(per_round[index_of[r.round_id]] for r in members))
            damage.append(sum(r.damage_diff for r in members))
            round_ids.append(-1)
        leverage = np.array(rows)
        damage = np.array(damage)
    else:
        # One row per source round, in the parent's own row order.
        order = _source_round_order(observations, config, reference)
        leverage = np.array([per_round[index_of[rid]] for rid in order])
        damage = np.array([by_round[rid].damage_diff for rid in order])
        round_ids = order

    names = tuple(controls_for(config))
    if config.name == "T1":
        # T1's rows are whole-match aggregates and the parent assigns it no
        # controls; a zero-width block keeps the design code uniform.
        controls = np.zeros((len(leverage), 0))
    else:
        by_round_obs = {o.round_id: o for o in observations}
        controls = np.array(
            [[_control_value(by_round_obs[int(rid)], n) for n in names] for rid in round_ids],
            dtype=float,
        ).reshape(len(leverage), len(names))

    return AlignedTarget(
        leverage=leverage, damage=damage, controls=controls, control_names=names,
        y=np.asarray(reference.y, dtype=float),
        weights=np.asarray(reference.w, dtype=float),
        match_ids=np.asarray(reference.match_ids), round_ids=np.asarray(round_ids),
    )


def _control_value(observation, name):
    """One control off a RoundObservation. `round_result` is the round's own
    outcome and is deliberately a control in its own right, never folded in
    with the context block -- the ladder's whole claim is about what the
    components add ON TOP of knowing who won the round."""
    if name == "round_result":
        return 1.0 if observation.round_won_by_team_a else 0.0
    if name == "attacking_is_team_a":
        return 1.0 if observation.attacking_is_team_a else 0.0
    return float(getattr(observation, name))


def _source_round_order(observations, config, reference):
    """The round each target row was built from, in the parent builder's
    order. Derived by replaying the builder's own eligibility rules against
    the same observations rather than guessing, and asserted against the
    reference row count so a mismatch fails loudly instead of misaligning
    y with X."""
    from app.services.impact_eval import _half_of

    order: list[int] = []
    for match_id, obs in group_by_match(observations).items():
        by_half: dict[int, list] = {}
        for o in obs:
            by_half.setdefault(_half_of(o.round_number), []).append(o)
        for half in by_half.values():
            half.sort(key=lambda o: o.round_number)
            for position, o in enumerate(half):
                if config.name == "WPA":
                    if o.round_won_by_team_a is None:
                        continue
                    order.append(o.round_id)
                    continue
                future = half[position + 1 : position + 1 + config.k]
                future = [f for f in future if f.round_won_by_team_a is not None]
                if not future:
                    continue
                order.append(o.round_id)
    if len(order) != len(reference.y):
        raise ValueError(
            f"alignment produced {len(order)} rows for {config.name}, "
            f"but the parent builder produced {len(reference.y)}"
        )
    return order


@dataclass
class FoldFit:
    fold: int
    l2: float
    train_match_ids: tuple
    test_match_ids: tuple
    swing_table: object
    exposure: np.ndarray
    calibration: np.ndarray
    graph: np.ndarray | None
    d: float
    deployable: bool
    reasons: tuple


@dataclass
class CandidateResult:
    name: str
    per_fold: dict = field(default_factory=dict)
    oof_scores: np.ndarray = None
    oof_probabilities: np.ndarray = None
    oof_y: np.ndarray = None
    oof_weights: np.ndarray = None
    oof_match_ids: np.ndarray = None
    oof_row_ids: np.ndarray = None


def _weighted_loss(probabilities, y, weights):
    return weighted_log_loss(probabilities, y, weights)


def run_nested_cv(leverage_rows, observations, config, candidates, l2_grid,
                  n_folds=5, seed=0, state_visits=None):
    """Outer folds by match; L2 selected inside each training fold.

    `state_visits` supplies the swing table's raw material. When omitted (in
    tests) a table is derived from the training rows' own outcomes, which is
    enough to exercise the wiring.
    """
    aligned = align_target(leverage_rows, observations, config)
    folds = stable_folds(aligned.match_ids, n_folds=n_folds, seed=seed)
    fold_of = np.array([folds[int(m)] for m in aligned.match_ids])

    results = {name: CandidateResult(name=name) for name in candidates}
    collected = {name: [] for name in candidates}
    row_order: list[np.ndarray] = []

    for fold in range(n_folds):
        test_mask = fold_of == fold
        train_mask = ~test_mask
        if not test_mask.any() or not train_mask.any():
            continue

        train_match_ids = tuple(sorted(set(aligned.match_ids[train_mask].tolist())))
        test_match_ids = tuple(sorted(set(aligned.match_ids[test_mask].tolist())))

        # EVERYTHING below is training-fold only.
        train_rounds = {int(r) for r in aligned.round_ids[train_mask] if r >= 0}
        visits = [v for v in (state_visits or []) if v.match_id in set(train_match_ids)]
        table = estimate_swing_table(visits) if visits else _table_from_rows(
            leverage_rows, train_rounds
        )
        exposure = np.abs(aligned.leverage[train_mask]).sum(axis=0)

        train = (aligned.leverage[train_mask], aligned.damage[train_mask],
                 aligned.y[train_mask], aligned.weights[train_mask])
        test = (aligned.leverage[test_mask], aligned.damage[test_mask],
                aligned.weights[test_mask])
        train_on_train = (aligned.leverage[train_mask], aligned.damage[train_mask],
                          aligned.weights[train_mask])
        outer_controls = (aligned.controls[train_mask], aligned.controls[test_mask])
        self_controls = (aligned.controls[train_mask], aligned.controls[train_mask])

        row_order.append(np.flatnonzero(test_mask))

        for name in candidates:
            l2 = _select_l2(name, aligned, train_mask, l2_grid, state_visits or [],
                            leverage_rows)
            candidate = fit_family_a(name, train, test, table, l2, exposure,
                                     shipped_graph(), controls=outer_controls)
            in_fold = fit_family_a(name, train, train_on_train, table, l2, exposure,
                                   shipped_graph(), controls=self_controls)
            calibration = platt_calibrate(in_fold.scores, aligned.y[train_mask],
                                          weights=aligned.weights[train_mask])
            probabilities = apply_calibration(calibration, candidate.scores)

            results[name].per_fold[fold] = FoldFit(
                fold=fold, l2=l2, train_match_ids=train_match_ids,
                test_match_ids=test_match_ids, swing_table=table, exposure=exposure,
                calibration=calibration, graph=candidate.graph, d=candidate.d,
                deployable=candidate.deployable, reasons=candidate.reasons,
            )
            collected[name].append((candidate.scores, probabilities))

    order = np.concatenate(row_order)
    for name in candidates:
        scores = np.concatenate([s for s, _ in collected[name]])
        probabilities = np.concatenate([p for _, p in collected[name]])
        results[name].oof_scores = scores
        results[name].oof_probabilities = probabilities
        results[name].oof_y = aligned.y[order]
        results[name].oof_weights = aligned.weights[order]
        results[name].oof_match_ids = aligned.match_ids[order]
        results[name].oof_row_ids = order
    return results


def _table_from_rows(leverage_rows, train_round_ids):
    # `train_round_ids` may be a set of round ids or of match ids depending on
    # the caller; both are membership tests over training rows only.
    """Fallback swing table for tests and for targets whose rows do not map
    to single rounds. Real runs pass `state_visits` from the extractor."""
    from app.services.kill_order_curves import SwingTable

    dp = np.full(len(PARAMS), 0.2)
    visits = np.zeros(len(PARAMS))
    for row in leverage_rows:
        if row.round_id in train_round_ids:
            visits += np.abs(row.kill).sum(axis=1)
    return SwingTable(dp=dp, visits=visits, win_rate={}, incomplete=[])


def _select_l2(name, aligned, train_mask, l2_grid, state_visits, leverage_rows,
               inner_folds=3, seed=1):
    """Inner CV inside the training fold. L2 is the ONLY hyperparameter
    selected here -- it does not change the outcome being predicted, which is
    why it is the only one allowed.

    EVERYTHING data-derived is rebuilt per inner split. An earlier draft
    passed in the swing table and exposure computed over the WHOLE outer
    training set and reused them for every inner split -- so for G2 (whose
    basis is built on dP) and G3 (whose prior is) the candidate had already
    seen the inner-validation matches' outcomes, and G1a's construction scale
    had seen their covariates. That is a leak inside the selection loop, and
    it biases the L2 choice toward whichever value overfits the table best.
    """
    if name in ("current_graph", "swing_plugin") or len(l2_grid) == 1:
        return float(l2_grid[0])

    train_matches = aligned.match_ids[train_mask]
    inner = stable_folds(train_matches, n_folds=inner_folds, seed=seed)
    inner_of = np.array([inner[int(m)] for m in train_matches])
    visits_by_match: dict = {}
    for visit in state_visits:
        visits_by_match.setdefault(visit.match_id, []).append(visit)

    best, best_loss = float(l2_grid[0]), np.inf
    for l2 in l2_grid:
        losses, weight_of = [], []
        for inner_fold in range(inner_folds):
            inner_test = inner_of == inner_fold
            inner_train = ~inner_test
            if not inner_test.any() or not inner_train.any():
                continue

            # Rebuilt from the INNER training matches only.
            inner_match_ids = set(train_matches[inner_train].tolist())
            inner_visits = [v for m in inner_match_ids for v in visits_by_match.get(m, [])]
            inner_table = (
                estimate_swing_table(inner_visits) if inner_visits
                else _table_from_rows(leverage_rows, inner_match_ids)
            )
            inner_exposure = np.abs(aligned.leverage[train_mask][inner_train]).sum(axis=0)

            sub_train = tuple(a[train_mask][inner_train] for a in
                              (aligned.leverage, aligned.damage, aligned.y, aligned.weights))
            sub_test = tuple(a[train_mask][inner_test] for a in
                             (aligned.leverage, aligned.damage, aligned.weights))
            sub_controls = (aligned.controls[train_mask][inner_train],
                            aligned.controls[train_mask][inner_test])
            self_controls = (aligned.controls[train_mask][inner_train],) * 2

            try:
                candidate = fit_family_a(name, sub_train, sub_test, inner_table, l2,
                                         inner_exposure, shipped_graph(),
                                         controls=sub_controls)
                fitted = fit_family_a(name, sub_train,
                                      (sub_train[0], sub_train[1], sub_train[3]),
                                      inner_table, l2, inner_exposure, shipped_graph(),
                                      controls=self_controls)
            except ValueError:
                continue  # an inner split with an undetermined state: skip, never impute
            calibration = platt_calibrate(fitted.scores, sub_train[2], weights=sub_train[3])
            probabilities = apply_calibration(calibration, candidate.scores)
            losses.append(_weighted_loss(probabilities, aligned.y[train_mask][inner_test],
                                         aligned.weights[train_mask][inner_test]))
            weight_of.append(float(aligned.weights[train_mask][inner_test].sum()))

        # WEIGHTED across inner folds. An unweighted mean lets a small fold
        # count as much as a large one, which contradicts this project's
        # "every objective is weighted" rule.
        if losses:
            combined = float(np.average(losses, weights=weight_of))
            if combined < best_loss:
                best, best_loss = float(l2), combined
    return best
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Add target alignment and nested cross-validation over leverage rows" -m "Stage C builds its own design matrix but predicts exactly the quantity the parent's frozen targets define, pinned by a test against build_target. Swing table, exposure, prior, L2 and calibration are all training-fold only, inside the loop." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: The five-rung T2 control ladder

**Files:**
- Modify: `webapp/app/services/kill_order_leverage.py` (two fields on `TeamLeverageRow`)
- Modify: `webapp/app/services/kill_order_refit.py` (append)
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: Task 10's `align_target`, `run_nested_cv` internals; `paired_bootstrap_delta` from `stats_math`.
- Produces: `LADDER_RUNGS`, `control_ladder(leverage_rows, observations, config, ...) -> dict`. `TeamLeverageRow` gains `terminal_alive_diff: float` and `total_kills: int`.

**The rungs, and why rung 4 is pinned at exactly two columns.**

| rung | knows |
|---|---|
| 1 | round-N result alone |
| 2 | + score differential, side, start-of-round economy |
| 3 | + damage differential |
| 4 | **+ final alive differential, total kills in the round** |
| 5 | + the 26 leverage columns |

**Rung 4 → 5 is this stage's headline.** Rung 4 is the floor the graph has to beat: a model that already knows how the round ended and how bloody it was. It is **exactly two columns**, declared before the fact, because a richer encoding — a slot per terminal state, say — could reconstruct the round well enough to make rung 5 look null for reasons that have nothing to do with the price list. Two numbers is a genuine floor without being a replay.

**A null at 4 → 5 is informative, not a tool failure.** It would say that weighting kills by the state they crossed adds nothing beyond knowing where the trajectory ended and how many kills it took. That is the sharpest form of the question this stage asks, and the report must read it that way rather than as a disappointing number. Rung 3 → 4 is reported alongside, because a large jump there is itself the finding that the graph's apparent contribution was mostly "who was left standing".

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import LADDER_RUNGS, control_ladder


def test_the_ladder_has_five_rungs_in_the_declared_order():
    assert LADDER_RUNGS == (
        "round_result", "plus_context", "plus_damage",
        "plus_terminal_state", "plus_leverage",
    )


def test_rung_four_adds_exactly_two_columns():
    """Pinned before the fact: a richer terminal encoding could reconstruct
    the round and make the headline null for the wrong reason."""
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5)
    assert report["plus_terminal_state"]["n_features"] - report["plus_damage"]["n_features"] == 2
    assert report["plus_terminal_state"]["added_columns"] == [
        "terminal_alive_diff", "total_kills",
    ]


def test_each_rung_is_a_superset_of_the_previous():
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5)
    previous: set[str] = set()
    for rung in LADDER_RUNGS:
        columns = set(report[rung]["columns"])
        assert previous <= columns, f"{rung} dropped a column the previous rung had"
        previous = columns


def test_the_headline_is_rung_four_to_five_with_a_paired_interval():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5, draws=50)
    headline = report["headline"]
    assert headline["from"] == "plus_terminal_state"
    assert headline["to"] == "plus_leverage"
    low, high = headline["delta_ci"]
    assert low <= headline["delta"] <= high
    assert "negative delta" in headline["reading"]


def test_the_three_to_four_step_is_reported_too():
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    report = control_ladder(leverage, observations, PRIMARY_T2, n_folds=5, draws=50)
    assert "delta" in report["plus_terminal_state"]
    assert report["plus_terminal_state"]["delta_from"] == "plus_damage"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -k ladder -v`
Expected: FAIL — `cannot import name 'LADDER_RUNGS'`

- [ ] **Step 3: Add the two fields to `TeamLeverageRow`**

In `kill_order_leverage.py`, add to the dataclass and populate in `assemble_round`:

```python
    # Rung 4 of the control ladder. Two numbers, deliberately: a richer
    # terminal encoding could reconstruct the round and make the rung 4 -> 5
    # headline null for reasons unrelated to the price list.
    terminal_alive_diff: float = 0.0
    total_kills: int = 0
```

and in `assemble_round`, after the terms loop, before building `team_row`:

```python
    alive_a = terms[-1].alive_team1_after if terms else 5
    alive_b = terms[-1].alive_team2_after if terms else 5
```

then pass `terminal_alive_diff=float(alive_a - alive_b), total_kills=len(terms)` into
`TeamLeverageRow(...)`.

**Read the alive counts off the walk, never recount victims.** `impact.py`
declines to decrement on events `_check_for_resurrection` flags, so counting
distinct victims double-subtracts a re-referenced player and can drive the
terminal state negative. A round with no kills is 5v5, differential 0.

- [ ] **Step 4: Append the ladder**

```python
# append to webapp/app/services/kill_order_refit.py

from app.services.impact_eval import CONTROLS_CONTEXT, CONTROLS_RESULT
from app.services.stats_math import paired_bootstrap_delta

LADDER_RUNGS = (
    "round_result", "plus_context", "plus_damage",
    "plus_terminal_state", "plus_leverage",
)

_RUNG_COLUMNS = {
    "round_result": list(CONTROLS_RESULT),
    "plus_context": list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT),
    "plus_damage": list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT) + ["damage_diff"],
    "plus_terminal_state": (
        list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT)
        + ["damage_diff", "terminal_alive_diff", "total_kills"]
    ),
    "plus_leverage": (
        list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT)
        + ["damage_diff", "terminal_alive_diff", "total_kills"]
        + [f"leverage:{name}" for name in PARAMS]
    ),
}


def control_ladder(leverage_rows, observations, config, n_folds=5, seed=0,
                   l2=1.0, draws=200):
    """Nested models on the frozen target, each knowing more than the last.

    L2 is DELIBERATELY FROZEN at 1.0 across every rung rather than selected.
    The ladder measures what each block of columns ADDS, so every rung must
    face the same penalty: selecting L2 per rung would let a rung win by
    getting a better hyperparameter rather than better information. The
    frozen value is stated here so a reader does not mistake it for an
    oversight, and the report prints it.

    The headline is rung 4 -> 5: what the leverage columns add beyond
    knowing who won the round, what the teams could afford, how much damage
    was done, how the round ended and how many kills it took.
    """
    aligned = align_target(leverage_rows, observations, config)
    by_round = {row.round_id: row for row in leverage_rows}
    extras = np.array([
        [by_round[int(rid)].terminal_alive_diff, by_round[int(rid)].total_kills]
        if int(rid) in by_round else [0.0, 0.0]
        for rid in aligned.round_ids
    ], dtype=float)
    controls = _control_matrix(observations, aligned)

    folds = stable_folds(aligned.match_ids, n_folds=n_folds, seed=seed)
    fold_of = np.array([folds[int(m)] for m in aligned.match_ids])

    predictions: dict[str, np.ndarray] = {}
    for rung in LADDER_RUNGS:
        design = _rung_design(rung, controls, aligned, extras)
        probabilities = np.zeros(len(aligned.y))
        for fold in range(n_folds):
            test = fold_of == fold
            train = ~test
            if not test.any() or not train.any():
                continue
            scaled_train, scaled_test, _c, _s = standardize(design[train], design[test])
            beta = fit_logistic(scaled_train, aligned.y[train],
                                weights=aligned.weights[train], l2=l2)
            probabilities[test] = predict_proba(beta, scaled_test)
        predictions[rung] = probabilities

    report: dict = {"config": config.name}
    for position, rung in enumerate(LADDER_RUNGS):
        entry = {
            "columns": _RUNG_COLUMNS[rung],
            "n_features": len(_RUNG_COLUMNS[rung]),
            "weighted_log_loss": float(
                weighted_log_loss(predictions[rung], aligned.y, aligned.weights)
            ),
        }
        if position:
            previous = LADDER_RUNGS[position - 1]
            entry["delta_from"] = previous
            entry["added_columns"] = [
                c for c in _RUNG_COLUMNS[rung] if c not in _RUNG_COLUMNS[previous]
            ]
            entry["delta"] = entry["weighted_log_loss"] - float(
                weighted_log_loss(predictions[previous], aligned.y, aligned.weights)
            )
        report[rung] = entry

    report["headline"] = _paired_step(
        aligned, predictions, "plus_terminal_state", "plus_leverage", draws, seed
    )
    report["plus_terminal_state"].update(
        {k: v for k, v in _paired_step(
            aligned, predictions, "plus_damage", "plus_terminal_state", draws, seed
        ).items() if k in ("delta", "delta_ci")}
    )
    return report


def _paired_step(aligned, predictions, lower, upper, draws, seed):
    groups: dict[int, list] = {}
    for index, match_id in enumerate(aligned.match_ids):
        groups.setdefault(int(match_id), []).append(index)

    def loss_of(name):
        def inner(sample):
            rows = [i for block in sample for i in block]
            return weighted_log_loss(
                predictions[name][rows], aligned.y[rows], aligned.weights[rows]
            )
        return inner

    low, high = paired_bootstrap_delta(
        loss_of(upper), loss_of(lower), groups, draws=draws, seed=seed
    )
    delta = float(
        weighted_log_loss(predictions[upper], aligned.y, aligned.weights)
        - weighted_log_loss(predictions[lower], aligned.y, aligned.weights)
    )
    return {
        "from": lower, "to": upper, "delta": delta, "delta_ci": [low, high],
        "metric": "weighted log loss, upper minus lower",
        "reading": (
            "negative delta = the added columns IMPROVED held-out prediction. "
            "An interval spanning zero means they added nothing measurable, "
            "which for rung 4 -> 5 is an informative result rather than a failure."
        ),
    }
```

`_control_matrix` and `_rung_design` are small helpers: the former reads the
parent's control fields off the observations in `aligned`'s row order; the latter
selects the columns each rung is allowed. Both are mechanical, and the
superset test in Step 1 is what holds them honest.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: PASS (13 tests)

- [ ] **Step 6: Commit**

```bash
git add webapp/app/services/kill_order_leverage.py webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Add the five-rung T2 control ladder" -m "Rung 4 gives the model the round's terminal alive differential and total kills -- exactly two columns, pinned before the fact so a rich encoding cannot make the rung 4 to 5 headline null for the wrong reason." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Diagnostics — conditioning, monotonicity, deployability, stability

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py` (append)
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: Task 10's `CandidateResult`; `cluster_bootstrap_ci` from `stats_math`.
- Produces: `conditioning_report(leverage) -> dict`, `monotonicity_violations(graph) -> list`, `stability_report(result, shipped, exposure, draws, seed) -> dict`, `per_parameter_report(leverage, exposure) -> dict`.

**The stability rule, and why the obvious version is pathological.** An earlier draft flagged a parameter indeterminate when its fold-to-fold spread exceeded its distance from the shipped value. That fails three ways: a parameter the data says should stay exactly where it is has distance ≈ 0 and is therefore *always* flagged; a parameter that moves far is *easier* to call stable; and 5-fold training sets share 3/5 of their matches, so fold spread badly understates variability and is not independent evidence.

The rule is graph-level and operational instead:

```
    stability = exposure-weighted RMS( fold graph  -  mean fold graph )
                --------------------------------------------------------
                exposure-weighted RMS( mean fold graph - shipped graph )

    stable  <=>  the UPPER bootstrap bound of that ratio is below 1
```

In words: the candidate differs from the shipped graph by more than it differs from itself across folds. Per-parameter fold values, crossing counts, VIFs and near-null projections stay in the report as **diagnostics**, and no verdict is derived from them individually.

**Monotonicity is reported, never imposed.** The shipped table has zero violations of the only coherent ordering — within a fixed number of players remaining, weight is non-decreasing as the state gets closer to even — and the measured swing table satisfies it too. A constraint everything already satisfies binds only where the data disagrees with the prior, which is exactly where we want to hear from the data.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import (
    conditioning_report,
    monotonicity_violations,
    per_parameter_report,
    stability_report,
)


def test_the_shipped_graph_has_no_monotonicity_violations():
    """Measured in the spec: zero, across all comparable pairs. The brief's
    suggested counter-example (4v4=170 against 2v2=200) is the diagonal
    rising as the round narrows, not a violation."""
    assert monotonicity_violations(shipped_graph()) == []


def test_a_deliberately_broken_curve_is_flagged():
    graph = shipped_graph().copy()
    graph[PARAM_INDEX["3v3"]] = 10.0        # even state now worth less than 3v1
    violations = monotonicity_violations(graph)
    assert any("3v3" in v for v in violations)


def test_conditioning_reports_rank_and_vif():
    rng = np.random.default_rng(4)
    leverage = rng.normal(size=(2000, len(PARAMS)))
    leverage[:, 1] = leverage[:, 0] + rng.normal(scale=0.01, size=2000)  # near-duplicate
    report = conditioning_report(leverage)
    assert report["condition_number"] > 50
    assert report["effective_rank"] < len(PARAMS)
    assert max(report["vif"]) > 20


def test_stability_calls_a_candidate_that_barely_moves_unstable():
    """The pathological rule this replaces would have called this STABLE,
    because its fold spread is small in absolute terms."""
    shipped = shipped_graph()
    result = fake_result({f: shipped + rng_noise(f) for f in range(5)})
    report = stability_report(result, shipped, exposure=np.ones(len(PARAMS)), draws=40)
    assert report["ratio"] > 1
    assert report["stable"] is False


def test_stability_calls_a_consistent_large_move_stable():
    shipped = shipped_graph()
    moved = shipped * 1.4
    result = fake_result({f: moved + rng_noise(f, scale=0.5) for f in range(5)})
    report = stability_report(result, shipped, exposure=np.ones(len(PARAMS)), draws=40)
    assert report["ratio"] < 1
    assert report["stable"] is True
    assert report["ratio_ci"][1] < 1


def test_per_parameter_report_carries_exposure_and_never_a_verdict():
    """Per-parameter numbers are diagnostics. If a 'stable' or
    'indeterminate' key appears here, the rejected rule has come back."""
    rng = np.random.default_rng(6)
    leverage = rng.normal(size=(500, len(PARAMS)))
    report = per_parameter_report(leverage, exposure=np.abs(leverage).sum(axis=0))
    assert set(report) == set(PARAMS)
    entry = report["3v3"]
    assert {"exposure", "rounds_touched", "vif"} <= set(entry)
    assert "stable" not in entry and "indeterminate" not in entry
```

Add these two helpers to the file:

```python
def rng_noise(fold, scale=6.0):
    return np.random.default_rng(100 + fold).normal(scale=scale, size=len(PARAMS))


def fake_result(graphs_by_fold):
    from app.services.kill_order_refit import CandidateResult, FoldFit

    result = CandidateResult(name="test")
    for fold, graph in graphs_by_fold.items():
        result.per_fold[fold] = FoldFit(
            fold=fold, l2=1.0, train_match_ids=(), test_match_ids=(),
            swing_table=None, exposure=np.ones(len(PARAMS)),
            calibration=np.zeros(2), graph=graph, d=1.0,
            deployable=True, reasons=(),
        )
    return result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -k "monotonicity or conditioning or stability or per_parameter" -v`
Expected: FAIL — `cannot import name 'conditioning_report'`

- [ ] **Step 3: Append the diagnostics**

```python
# append to webapp/app/services/kill_order_refit.py

from app.services.kill_order_curves import normalize_for_display
from app.services.kill_order_leverage import PARAM_INDEX
from app.services.stats_math import cluster_bootstrap_ci

_LATTICE_STATES = [(own, opp) for own in range(1, 6) for opp in range(1, 6)]


def monotonicity_violations(graph) -> list:
    """Within a fixed number of players remaining, weight must be
    non-decreasing as the state gets closer to even.

    REPORTED, never imposed: the shipped table, the measured swing table
    and every prior all satisfy this, so a constraint would bind only where
    the data disagrees with the prior -- exactly where we want to hear from
    the data.
    """
    graph = np.asarray(graph, dtype=float)
    out: list[str] = []
    for own_a, opp_a in _LATTICE_STATES:
        for own_b, opp_b in _LATTICE_STATES:
            if own_a + opp_a != own_b + opp_b:
                continue
            if abs(own_a - opp_a) >= abs(own_b - opp_b):
                continue
            closer = graph[PARAM_INDEX[f"{own_a}v{opp_a}"]]
            further = graph[PARAM_INDEX[f"{own_b}v{opp_b}"]]
            if closer < further:
                out.append(
                    f"{own_a}v{opp_a}={closer:.1f} < {own_b}v{opp_b}={further:.1f} "
                    f"(same total alive, closer to even should not score less)"
                )
    return out


def conditioning_report(leverage) -> dict:
    leverage = np.asarray(leverage, dtype=float)
    usable = leverage.std(axis=0) > 1e-12
    scaled = (leverage[:, usable] - leverage[:, usable].mean(axis=0)) / leverage[:, usable].std(axis=0)
    correlation = np.corrcoef(scaled, rowvar=False)
    # Clip before the log: a genuinely collinear design can produce tiny
    # negative eigenvalues from floating point, and log(share) then returns
    # NaN for the whole report.
    eigenvalues = np.clip(np.linalg.eigvalsh(correlation)[::-1], 1e-12, None)
    share = eigenvalues / eigenvalues.sum()
    off_diagonal = correlation[~np.eye(correlation.shape[0], dtype=bool)]
    return {
        "n_columns": int(usable.sum()),
        "max_abs_correlation": float(np.abs(off_diagonal).max()),
        "condition_number": float(eigenvalues[0] / eigenvalues[-1]),
        "effective_rank": float(np.exp(-(share * np.log(share)).sum())),
        "eigenvalues": [float(v) for v in eigenvalues],
        "vif": [float(v) for v in np.diag(np.linalg.pinv(correlation))],
    }


def per_parameter_report(leverage, exposure) -> dict:
    """Diagnostics only. Deliberately carries NO per-parameter verdict: the
    rejected stability rule lived here, and a 'stable' key reappearing is
    how it would come back."""
    leverage = np.asarray(leverage, dtype=float)
    conditioning = conditioning_report(leverage)
    usable = leverage.std(axis=0) > 1e-12
    vif_by_column = dict(zip(np.flatnonzero(usable).tolist(), conditioning["vif"]))
    out = {}
    for index, name in enumerate(PARAMS):
        out[name] = {
            "exposure": float(np.asarray(exposure, dtype=float)[index]),
            "rounds_touched": int(np.count_nonzero(leverage[:, index])),
            "vif": float(vif_by_column.get(index, float("nan"))),
        }
    return out


def _weighted_rms(values, exposure) -> float:
    exposure = np.asarray(exposure, dtype=float)
    return float(np.sqrt(np.sum(exposure * np.asarray(values, dtype=float) ** 2) / exposure.sum()))


def stability_report(result, shipped, exposure, refit=None, match_ids=None,
                     draws=200, seed=0) -> dict:
    """Graph-level stability, as a MATCH-CLUSTERED REFITTING bootstrap.

        ratio = RMS(resampled graph - mean graph) / RMS(mean - shipped)

    stable <=> the UPPER bound of that ratio is below 1: the candidate
    differs from the shipped graph by more than it differs from itself.

    `refit(match_ids) -> graph` refits the candidate on a resampled match
    set. It is REQUIRED when stability gates a success claim. An earlier
    draft bootstrapped the five outer-fold graphs instead -- but five
    overlapping folds (any two share 3/5 of their matches) are neither
    independent nor numerous enough to support a percentile interval, and
    that quantity was gating P1/P2. Without `refit` this returns a
    DESCRIPTIVE fold-dispersion figure and sets `gate_eligible=False`, and
    the verdict must not consume it.

    Reported twice, because they answer different questions:
      - `shape`: graphs display-normalized, so only the curve's shape counts
      - `raw`:   graphs as recovered, so a change in overall level relative
                 to damage counts too -- and that level IS part of the
                 deployable metric, so erasing it would hide a real change
    """
    fold_graphs = [f.graph for f in result.per_fold.values() if f.graph is not None]
    if len(fold_graphs) < 2:
        return {"stable": False, "gate_eligible": False,
                "reason": "fewer than two folds produced a graph"}

    def summarize(sample, reference):
        block = np.array(sample)
        mean = block.mean(axis=0)
        spread = float(np.mean([_weighted_rms(g - mean, exposure) for g in block]))
        distance = _weighted_rms(mean - reference, exposure)
        return spread / distance if distance > 0 else np.inf

    out: dict = {"n_folds": len(fold_graphs)}
    for label, prepare in (("shape", lambda g: normalize_for_display(g, exposure)),
                           ("raw", lambda g: np.asarray(g, dtype=float))):
        prepared = [prepare(g) for g in fold_graphs]
        reference = prepare(shipped)
        out[label] = {"fold_dispersion_ratio": float(summarize(prepared, reference))}

    if refit is None or match_ids is None:
        out.update({
            "gate_eligible": False, "stable": False,
            "rule": "fold dispersion only -- DESCRIPTIVE, must not gate a success claim",
        })
        return out

    rng = np.random.default_rng(seed)
    unique = np.asarray(sorted(set(int(m) for m in match_ids)))
    resampled: list[np.ndarray] = []
    for _ in range(draws):
        drawn = unique[rng.integers(0, len(unique), size=len(unique))]
        graph = refit(drawn.tolist())
        if graph is not None and np.all(np.isfinite(graph)):
            resampled.append(np.asarray(graph, dtype=float))
    if len(resampled) < 20:
        out.update({"gate_eligible": False, "stable": False,
                    "rule": f"only {len(resampled)} usable refits; interval not trustworthy"})
        return out

    for label, prepare in (("shape", lambda g: normalize_for_display(g, exposure)),
                           ("raw", lambda g: np.asarray(g, dtype=float))):
        prepared = [prepare(g) for g in resampled]
        reference = prepare(shipped)
        mean = np.array(prepared).mean(axis=0)
        distance = _weighted_rms(mean - reference, exposure)
        ratios = [
            _weighted_rms(g - mean, exposure) / distance if distance > 0 else np.inf
            for g in prepared
        ]
        low, high = np.percentile(ratios, [2.5, 97.5])
        out[label].update({
            "ratio": float(np.mean(ratios)),
            "ratio_ci": [float(low), float(high)],
            "stable": bool(np.isfinite(high) and high < 1.0),
        })

    out["gate_eligible"] = True
    out["stable"] = bool(out["shape"]["stable"] and out["raw"]["stable"])
    out["rule"] = (
        "match-clustered refitting bootstrap; resampled-to-mean RMS over "
        "mean-to-shipped RMS, exposure-weighted; stable when the upper bound "
        "is below 1 for BOTH the display-normalized shape and the raw level"
    )
    out["draws_used"] = len(resampled)
    return out
```

Note the bootstrap resamples **folds**, which is the only grouping available for a
fold-to-fold quantity, and the report says so: with five folds the interval is
coarse, and that coarseness is a property of 5-fold CV rather than something the
code can fix.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Add conditioning, monotonicity, per-parameter and stability diagnostics" -m "Stability is graph-level with a bootstrap interval: fold-to-fold RMS over shipped-to-candidate RMS, stable when the upper bound is below 1. The rejected per-parameter rule flagged a correctly-unmoved parameter as indeterminate and made large movers easier to call stable." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Stage C0 — the descriptive block that runs before any fitting

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py` (append)
- Test: `webapp/tests/test_kill_order_refit.py` (append)
- Test: `webapp/tests/test_kill_order_stage_c0.py` (new, requires live Postgres)

**Interfaces:**
- Consumes: `estimate_swing_table`, `construction_normalize`, `score_rounds`, `family_a_leverage`, `shipped_graph`; `cluster_bootstrap_ci`.
- Produces: `stage_c0_report(leverage_rows, player_rows, state_visits, draws=200, seed=0) -> dict`.

**Why this runs first, and why the whole stage is sequenced around it.** The spec measured, before any code existed, that the shipped graph is an affine function of the data's own swing curve at exposure-weighted R² = 0.970, and that swapping the shipped graph for the pure swing curve leaves the round-level Impact differential at r = 0.998 with **zero sign flips in 5,259 rounds**. If that holds at full scale, no downstream yardstick difference was ever possible and the report must lead with it rather than burying it under fitted candidates.

C0 does not gate the later stages — a null still needs the fitted candidates to *be* a null — but it frames them. The spec's figures came from a 250-match probe with an all-data swing table; **C0 re-runs them over every eligible match and reports them as the headline.**

- [ ] **Step 1: Write the pure tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import stage_c0_report


def test_stage_c0_regresses_the_shipped_graph_on_the_swing_curve():
    """hand ~ alpha + beta * dP. The spec measured R^2 = 0.9704 on real
    data; here the synthetic curve is exactly affine, so R^2 must be ~1."""
    dp = np.linspace(0.02, 0.45, len(PARAMS))
    graph = 50.0 + 478.0 * dp
    report = stage_c0_report.regress_on_swing(graph, dp, exposure=np.ones(len(PARAMS)))
    assert report["r_squared"] > 0.999
    assert report["intercept"] == pytest.approx(50.0, rel=1e-6)
    assert report["slope"] == pytest.approx(478.0, rel=1e-6)
    assert max(abs(r) for r in report["residuals"].values()) < 1e-6


def test_stage_c0_reports_sign_flips_and_correlation_between_two_graphs():
    rng = np.random.default_rng(31)
    leverage = rng.normal(size=(800, len(PARAMS)))
    damage = rng.normal(size=800) * 20
    a = shipped_graph()
    b = a * 1.02
    report = stage_c0_report.compare_graphs(leverage, damage, a, b)
    assert report["pearson"] > 0.99
    assert report["sign_flip_rate"] < 0.05
    assert report["sd_difference"] < report["sd_reference"]
```

- [ ] **Step 2: Write the live test**

```python
# webapp/tests/test_kill_order_stage_c0.py
"""Stage C0 against the real database. Requires:

    docker compose -p valomaths-private up -d

Skips cleanly when Postgres is unreachable. This is the test that would
catch the spec's own headline numbers having drifted as the crawl grows."""

import numpy as np
import pytest

from app.services.kill_order_leverage import load_all_leverage, state_visits_for_match
from app.services.kill_order_refit import stage_c0_report


@pytest.fixture(scope="module")
def loaded():
    try:
        from app.db import SessionLocal

        session = SessionLocal()
        session.execute(__import__("sqlalchemy").text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"live Postgres unavailable: {exc}")
    report: dict = {}
    team_rows, player_rows = load_all_leverage(session, report=report)
    visits = []
    for match_id in {row.match_id for row in team_rows}:
        visits.extend(state_visits_for_match(session, match_id))
    yield team_rows, player_rows, visits, report
    session.close()


def test_the_shipped_graph_tracks_the_measured_swing_curve(loaded):
    """Spec figure: exposure-weighted R^2 = 0.9704, every residual within
    +-17 on a 40-250 scale. A large drop here means the DB has changed
    enough that the spec's framing needs revisiting."""
    team_rows, _players, visits, _report = loaded
    report = stage_c0_report(team_rows, _players, visits, draws=50)
    fit = report["shipped_vs_swing"]
    assert fit["r_squared"] > 0.90
    assert max(abs(r) for r in fit["residuals"].values()) < 40


def test_swapping_the_graph_barely_moves_the_metric(loaded):
    """Spec figure: r = 0.998 with zero sign flips over 5,259 rounds. If
    this collapses, the headroom argument the whole stage is framed around
    has stopped holding."""
    team_rows, player_rows, visits, _report = loaded
    report = stage_c0_report(team_rows, player_rows, visits, draws=50)
    swap = report["current_vs_swing_plugin"]
    assert swap["round_level"]["pearson"] > 0.95
    assert swap["round_level"]["sign_flip_rate"] < 0.05
    assert swap["player_match_level"]["pearson"] > 0.95


def test_every_state_has_enough_exposure_to_estimate(loaded):
    """Measured: the rarest parameter is crossed 1,446 times. The estimation
    problem here is conditioning, not sparsity, and this pins that."""
    _team, _players, visits, _report = loaded
    report = stage_c0_report(_team, _players, visits, draws=10)
    counts = report["swing_table"]["visits"]
    lattice = {k: v for k, v in counts.items() if k != "fallback"}
    assert min(lattice.values()) > 500
```

- [ ] **Step 3: Append the implementation**

```python
# append to webapp/app/services/kill_order_refit.py

from app.services.kill_order_curves import (
    _finite_dp, construction_normalize, score_rounds,
)
from app.services.stats_math import _average_ranks


def _regress_on_swing(graph, dp, exposure) -> dict:
    """Exposure-weighted least squares of the graph on the swing curve.

    The spec's motivating measurement: hand = 50.0 + 478.0 * dP at
    R^2 = 0.9704, every residual within +-17 on a 40-250 scale. If the
    shipped numbers are already an affine function of the data's own swing
    curve, the headroom for refitting is small and the report must say so
    before it says anything else.
    """
    graph = np.asarray(graph, dtype=float)
    dp = np.asarray(dp, dtype=float)
    exposure = np.asarray(exposure, dtype=float)
    usable = np.isfinite(dp) & np.isfinite(graph) & (exposure > 0)
    weights = exposure[usable] / exposure[usable].sum()
    design = np.column_stack([np.ones(usable.sum()), dp[usable]])
    root = np.sqrt(weights)[:, None]
    coefficients, *_ = np.linalg.lstsq(design * root, graph[usable] * root.ravel(), rcond=None)
    predicted = design @ coefficients
    residual = graph[usable] - predicted
    mean = float(np.sum(weights * graph[usable]))
    r_squared = 1 - float(np.sum(weights * residual ** 2)) / float(
        np.sum(weights * (graph[usable] - mean) ** 2)
    )
    names = [PARAMS[i] for i in np.flatnonzero(usable)]
    return {
        "intercept": float(coefficients[0]),
        "slope": float(coefficients[1]),
        "r_squared": float(r_squared),
        "residuals": {n: float(v) for n, v in zip(names, residual)},
        "implied": {n: float(v) for n, v in zip(names, predicted)},
    }


def _compare_graphs(leverage, damage, reference, other) -> dict:
    a = score_rounds(leverage, damage, reference)
    b = score_rounds(leverage, damage, other)
    # _average_ranks, not a double argsort: tied scores are common here
    # (rounds with identical leverage) and double argsort breaks ties
    # arbitrarily, which inflates the correlation.
    ranks = _average_ranks
    return {
        "pearson": float(np.corrcoef(a, b)[0, 1]),
        "spearman": float(np.corrcoef(ranks(a), ranks(b))[0, 1]),
        "sd_reference": float(a.std()),
        "sd_difference": float((a - b).std()),
        "sign_flip_rate": float(np.mean((a > 0) != (b > 0))),
        "n": int(len(a)),
    }


def stage_c0_report(team_rows, player_rows, state_visits, draws=200, seed=0) -> dict:
    """Descriptive, all-data, and LABELLED as such. Nothing here is a
    held-out estimate; the swing table is built over every match precisely
    because this block describes the data rather than predicting from it.
    """
    leverage = family_a_leverage(team_rows)
    damage = np.array([row.damage_diff for row in team_rows], dtype=float)
    exposure = np.abs(leverage).sum(axis=0)
    shipped = shipped_graph()
    table = estimate_swing_table(state_visits)
    # _finite_dp lives in kill_order_curves and is the ONLY place a NaN dP is
    # pinned. Do not add a second copy here -- two pinning rules that drift
    # apart would silently give G1a a different graph in different modules.
    plugin = construction_normalize(_finite_dp(table), exposure, shipped)

    groups: dict[int, list] = {}
    for index, row in enumerate(team_rows):
        groups.setdefault(int(row.match_id), []).append(index)

    def pearson_of(sample):
        rows = [i for block in sample for i in block]
        a = score_rounds(leverage[rows], damage[rows], shipped)
        b = score_rounds(leverage[rows], damage[rows], plugin)
        return float(np.corrcoef(a, b)[0, 1])

    low, high = cluster_bootstrap_ci(pearson_of, groups, draws=draws, seed=seed)

    round_level = _compare_graphs(leverage, damage, shipped, plugin)
    round_level["pearson_ci"] = [float(low), float(high)]

    return {
        "note": (
            "DESCRIPTIVE, all-data. The swing table here is estimated over every "
            "match, so nothing in this block is a held-out estimate. dP is an "
            "observational contrast between state values, not the causal value "
            "of crossing a state."
        ),
        "swing_table": {
            "dp": {PARAMS[i]: (None if not np.isfinite(table.dp[i]) else float(table.dp[i]))
                   for i in range(len(PARAMS))},
            "visits": {PARAMS[i]: float(table.visits[i]) for i in range(len(PARAMS))},
            "incomplete": table.incomplete,
        },
        "shipped_vs_swing": _regress_on_swing(shipped, table.dp, exposure),
        "current_vs_swing_plugin": {
            "round_level": round_level,
            "player_match_level": _player_match_comparison(player_rows, shipped, plugin),
        },
        "reading": (
            "A correlation above 0.99 with no sign flips means no downstream "
            "yardstick difference was ever possible -- but correlation alone is "
            "NOT the practical-equivalence test; see the verdict checklist."
        ),
    }


def _player_match_comparison(player_rows, reference, other) -> dict:
    """Average Impact per (player, match) under each graph. The team
    differential can hide a change that per-player attribution shows, so
    both are reported."""
    # A PLAYER's Impact is damage + kill_impact - death_impact. The team row
    # adds both halves because the victim's death is subtracted from the
    # other team; per player it is a subtraction. An earlier draft used
    # kill + death here, which scores a player as if their own deaths helped
    # them.
    totals: dict[tuple, list] = {}
    for row in player_rows:
        key = (row.player_id, row.match_id)
        block = (row.kill - row.death).sum(axis=1) / 3.0
        totals.setdefault(key, []).append((row.damage, block))
    a, b = [], []
    for entries in totals.values():
        a.append(np.mean([d + float(np.sum(reference * v)) for d, v in entries]))
        b.append(np.mean([d + float(np.sum(other * v)) for d, v in entries]))
    a, b = np.array(a), np.array(b)
    ranks = lambda v: np.argsort(np.argsort(v))
    return {
        "pearson": float(np.corrcoef(a, b)[0, 1]),
        "spearman": float(np.corrcoef(ranks(a), ranks(b))[0, 1]),
        "n_player_matches": int(len(a)),
    }
```

Attach the two helpers used by the pure tests as attributes so they are
addressable without exporting private names:

```python
stage_c0_report.regress_on_swing = _regress_on_swing
stage_c0_report.compare_graphs = _compare_graphs
```

- [ ] **Step 4: Run both test files**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v
docker compose -p valomaths-private up -d
.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_stage_c0.py -v -s
```

Expected: PASS. The live module SKIPs without Postgres — a skip is not a pass, and Stage C0 has not been verified until it runs green against the real DB.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py webapp/tests/test_kill_order_stage_c0.py
git commit -m "Add the Stage C0 descriptive block" -m "Re-runs at full scale the two measurements the whole stage is framed around: the shipped graph regressed on the data's own swing curve, and how little the metric moves when the graph is swapped. Descriptive and labelled as such." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: The player-level read — death impact and trades

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py` (append)
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: `PlayerLeverageRow` from Task 4; `point_biserial`, `tercile_buckets`, `cluster_bootstrap_ci` from `stats_math`.
- Produces: `player_level_report(player_rows, outcomes, graph, weighting=None, draws=200, seed=0) -> dict`.

**Why this exists, and it is a required output rather than a nicety.** In the team-differential unit the kill and death halves correlate at 0.937-0.957, because one kill enters the differential through both halves with the same sign. So the team-level yardsticks cannot separate them. At player level they are plainly different events, and this is the one place in the project where a kill/death asymmetry can be read at all.

**Trades are part of the read, by decision.** `_traded_factor` forgives a death when a teammate kills the killer back within 10 seconds — so how much a player's deaths cost depends on whether their *team* trades for them, which is a team quality being charged to an individual. It is also the most state-dependent factor in the formula (CV 0.112, tracking the margin at +0.888) and the main reason kill and death do not collapse into the same column entirely. At team level the discount is invisible; per player it is directly attributable.

The player product carries the death leverage **both before and after** the traded factor, so the discount is a subtraction rather than a re-derivation — and `_traded_factor` legitimately returns `0.0` for a same-second trade, which would make dividing it back out undefined.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import player_level_report


def fake_player_rows(n_players=10, n_rounds=20, seed=41):
    from app.services.kill_order_leverage import PlayerLeverageRow

    rng = np.random.default_rng(seed)
    rows, outcomes = [], {}
    for match in range(6):
        outcomes[match] = bool(rng.uniform() < 0.5)
        for rnd in range(n_rounds):
            for player in range(n_players):
                kill = np.zeros((len(PARAMS), len(COMPONENTS)))
                death = np.zeros_like(kill)
                untraded = np.zeros_like(kill)
                kill[PARAM_INDEX["3v3"]] = abs(rng.normal(size=len(COMPONENTS)))
                untraded[PARAM_INDEX["3v3"]] = abs(rng.normal(size=len(COMPONENTS)))
                death[PARAM_INDEX["3v3"]] = untraded[PARAM_INDEX["3v3"]] * 0.7
                rows.append(PlayerLeverageRow(
                    match_id=match, round_id=match * 100 + rnd, round_number=rnd + 1,
                    match_player_id=match * 10 + player, team_is_a=player < 5,
                    damage=rng.normal() * 20, kill=kill, death=death,
                    death_untraded=untraded,
                ))
    return rows, outcomes


def test_kill_and_death_impact_are_reported_separately():
    rows, outcomes = fake_player_rows()
    report = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    assert "kill_impact" in report["per_player"]["summary"]
    assert "death_impact" in report["per_player"]["summary"]
    assert report["per_player"]["summary"]["death_impact"]["mean"] > 0


def test_the_trade_discount_is_reported_per_player():
    """The decision this task exists to honour: death cost as scored,
    against death cost with no trade credit."""
    rows, outcomes = fake_player_rows()
    report = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    trades = report["trades"]
    assert trades["death_impact_as_scored"] < trades["death_impact_without_trade_credit"]
    assert trades["discount"] > 0
    assert np.isclose(
        trades["discount"],
        trades["death_impact_without_trade_credit"] - trades["death_impact_as_scored"],
    )


def test_tercile_lift_is_reported_for_each_half_as_well_as_pooled():
    rows, outcomes = fake_player_rows()
    report = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    for key in ("impact", "kill_impact", "death_impact"):
        assert "tercile_lift" in report["per_player"][key]
        assert "ci" in report["per_player"][key]


def test_a_graph_change_moves_the_player_level_read():
    """If it did not, the player-level block would be decorative and the
    kill/death decision would have nowhere to land."""
    rows, outcomes = fake_player_rows()
    flat = np.full(len(PARAMS), 136.6)
    a = player_level_report(rows, outcomes, shipped_graph(), draws=20)
    b = player_level_report(rows, outcomes, flat, draws=20)
    assert a["per_player"]["summary"]["death_impact"]["mean"] != pytest.approx(
        b["per_player"]["summary"]["death_impact"]["mean"], rel=1e-6
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -k player_level -v`
Expected: FAIL — `cannot import name 'player_level_report'`

- [ ] **Step 3: Append the implementation**

```python
# append to webapp/app/services/kill_order_refit.py

from app.services.stats_math import point_biserial, tercile_buckets


MIN_MATCHES_FOR_WITHIN_PLAYER = 9   # >= 3 per tercile, matching the parent spec


def player_level_report(player_rows, match_outcomes, graph, surfaces=None,
                        draws=200, seed=0, min_matches=MIN_MATCHES_FOR_WITHIN_PLAYER) -> dict:
    """The only place in this project where the kill and death halves
    separate. At team level they correlate 0.937-0.957 by construction, so
    the team yardsticks cannot see a kill/death asymmetry; here they can.

    `surfaces` optionally supplies a Family B rung's recovered EFFECTIVE
    PRICE SURFACES -- a dict of (component, side) -> 26-vector, from
    `effective_surfaces`. An earlier draft took a three-element component
    weighting, which B2 and B3 (9 and 18 coefficients) cannot supply: it
    would have raised a shape error or, worse, silently scored the wrong
    thing. None uses the shipped equal weights over the given graph.
    """
    graph = np.asarray(graph, dtype=float)

    def collapse(block, side):
        block = np.asarray(block, dtype=float)
        if surfaces is None:
            return float(np.sum(graph * block.sum(axis=1) / len(COMPONENTS)))
        total = 0.0
        for index, component in enumerate(COMPONENTS):
            key = component if f"{component}_base" in () else f"{component}_{side}"
            surface = surfaces.get(key, surfaces.get(component))
            if surface is None:
                raise KeyError(f"no effective surface for {key!r}")
            total += float(np.sum(np.asarray(surface) * block[:, index]))
        return total

    per_match: dict[tuple, dict] = {}
    for row in player_rows:
        key = (row.player_id, row.match_id)     # canonical player, then match
        entry = per_match.setdefault(key, {
            "kill": 0.0, "death": 0.0, "death_untraded": 0.0, "damage": 0.0,
            "rounds": 0, "won": bool(match_outcomes[row.match_id]) == bool(row.team_is_a),
        })
        entry["kill"] += collapse(row.kill, "kill")
        entry["death"] += collapse(row.death, "death")
        entry["death_untraded"] += collapse(row.death_untraded, "death")
        entry["damage"] += row.damage
        entry["rounds"] += 1

    def series(name):
        if name == "impact":
            return np.array([
                (e["damage"] + e["kill"] - e["death"]) / e["rounds"] for e in per_match.values()
            ])
        if name == "kill_impact":
            return np.array([(e["damage"] + e["kill"]) / e["rounds"] for e in per_match.values()])
        return np.array([e["death"] / e["rounds"] for e in per_match.values()])

    won = np.array([e["won"] for e in per_match.values()], dtype=int)
    keys = list(per_match)
    players = np.array([k[0] for k in keys])
    groups: dict[int, list] = {}
    for index, (_player, match_id) in enumerate(keys):
        groups.setdefault(int(match_id), []).append(index)

    def within_player_lift(values, rows=None):
        """Terciles computed WITHIN each sufficiently-observed player, then
        pooled. Global terciles would mostly compare strong players against
        weak ones -- 94.7% of players here have a single match -- which is
        not the quantity the spec asks for and not what the player page
        would show."""
        rows = np.arange(len(values)) if rows is None else np.asarray(rows)
        top_hits = top_n = bottom_hits = bottom_n = 0
        eligible = 0
        for player in np.unique(players[rows]):
            mine = rows[players[rows] == player]
            if len(mine) < min_matches:
                continue
            eligible += 1
            buckets = tercile_buckets(values[mine])
            if not (buckets == 2).any() or not (buckets == 0).any():
                continue
            top_hits += int(won[mine][buckets == 2].sum())
            top_n += int((buckets == 2).sum())
            bottom_hits += int(won[mine][buckets == 0].sum())
            bottom_n += int((buckets == 0).sum())
        if not top_n or not bottom_n:
            return None, 0
        return (top_hits / top_n) - (bottom_hits / bottom_n), eligible

    per_player: dict = {"summary": {}, "min_matches": min_matches}
    for name in ("impact", "kill_impact", "death_impact"):
        values = series(name)
        lift, eligible = within_player_lift(values)

        def lift_of(sample, values=values):
            rows = [i for block in sample for i in block]
            result, _ = within_player_lift(values, rows)
            return result

        low, high = cluster_bootstrap_ci(lift_of, groups, draws=draws, seed=seed)
        per_player[name] = {
            "point_biserial": float(point_biserial(values, won)),
            "within_player_tercile_lift": None if lift is None else float(lift),
            "ci": [float(low), float(high)],
            "eligible_players": eligible,
        }
        per_player["summary"][name] = {"mean": float(values.mean()), "sd": float(values.std())}

    scored = float(np.mean([e["death"] / e["rounds"] for e in per_match.values()]))
    untraded = float(np.mean([e["death_untraded"] / e["rounds"] for e in per_match.values()]))
    return {
        "note": (
            "Player-level. The team differential fuses kill and death (they "
            "correlate 0.937-0.957 there by construction); this block is where "
            "they separate, and where the trade discount is attributable."
        ),
        "n_player_matches": len(per_match),
        "per_player": per_player,
        "trades": {
            "death_impact_as_scored": scored,
            "death_impact_without_trade_credit": untraded,
            "discount": untraded - scored,
            "reading": (
                "The discount is what _traded_factor forgave. It depends on "
                "whether the player's TEAM traded for them, so it is a team "
                "quality currently charged to an individual."
            ),
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: PASS (23 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Add the player-level read covering death impact and trades" -m "The team differential fuses kill and death by construction, so the kill/death asymmetry can only be read per player. Trades are part of that read: the discount _traded_factor applies depends on whether a player's team trades for them." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: Predeclared comparisons, the four verdicts, and matrix refusal

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py` (append)
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: `CandidateResult`, `stability_report`, `paired_bootstrap_delta`.
- Produces: `PRIMARY_COMPARISONS`, `VERDICTS`, `paired_delta(result_a, result_b, alpha) -> dict`, `verdict_report(...) -> dict`, `RunIdentity`, `matrix_is_comparable(a, b) -> tuple[bool, list[str]]`.

**The predeclared comparisons.** "Any candidate on any target" is a dozen-plus tests at 95% and would produce roughly one false winner even if the refit does nothing.

| | comparison | target | interval | declares |
|---|---|---|---|---|
| P1 | `swing_basis` (G2) vs `current_graph` | T2 | **97.5%** | A1 |
| P2 | `pooled` (G3) vs `current_graph` | T2 | **97.5%** | A1 |
| P3 | `component_tilt` (B2) vs `stage_a_exact` (B0) | T2 | 95% | C |
| P4 | `swing_basis` vs `pooled` | T2 | — | reported, no claim |

G2 and G3 are co-primary at 97.5% — two shots at the same null, so the bar rises. The cost is stated rather than hidden: with the headroom already measured, a marginal improvement that would clear 95% will not clear 97.5%. That is the intended trade, because the action a success licenses is changing a shipped metric. **P4 carries no success claim and is the comparison that actually teaches something** — agreement means the answer is robust to how much freedom the curve is allowed.

**Four verdicts, never merged.** An earlier draft asked whether *any* of seven items tripped, which would have written up a genuine held-out improvement as a failure because econ's coefficient stayed negative — and merged a T2 question with a T1 one.

| verdict | question | items |
|---|---|---|
| **A1** prediction, next rounds | does a refit graph predict future rounds better? | 1, 2 |
| **A2** prediction, match outcome | does first-half Impact predict the match better? | 6 |
| **B** collinearity | did we explain the econ collapse? | 3, 4, 5 |
| **C** structure | do the components want different curves? | 7 |

**This is a predeclared analysis plan, not a pre-registration.** This dataset was used to design what is being tested — G2's basis was chosen by fitting bases to it, G5's tilt axes came from measuring its factor profiles, the equivalence bound is this study's own CI width, and the collinearity threshold sits just below its observed minimum. That is not illegitimate, but it is not independence, and the report must not claim it. What the plan does buy is that the thresholds cannot move after the results are seen.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import (
    PRIMARY_COMPARISONS,
    RunIdentity,
    matrix_is_comparable,
    paired_delta,
    verdict_report,
)


def test_the_primaries_are_declared_with_their_intervals():
    names = {p["name"]: p for p in PRIMARY_COMPARISONS}
    assert set(names) == {"P1", "P2", "P3", "P4"}
    assert names["P1"]["alpha"] == pytest.approx(0.025)
    assert names["P2"]["alpha"] == pytest.approx(0.025)
    assert names["P3"]["alpha"] == pytest.approx(0.05)
    assert names["P4"]["declares"] is None
    assert all(p["target"] == "T2" for p in PRIMARY_COMPARISONS)


def test_a_co_primary_uses_the_tighter_interval():
    """97.5% must be strictly harder to clear than 95%, or the multiplicity
    adjustment is decorative."""
    a, b = paired_fixture(effect=0.004)
    wide = paired_delta(a, b, alpha=0.05)
    tight = paired_delta(a, b, alpha=0.025)
    assert tight["ci"][0] <= wide["ci"][0]
    assert tight["ci"][1] >= wide["ci"][1]


def test_verdicts_are_reported_separately_and_never_merged():
    report = verdict_report(**verdict_fixture())
    assert set(report["verdicts"]) == {"A1", "A2", "B", "C"}
    for verdict in report["verdicts"].values():
        assert "helped" in verdict and "items" in verdict
    assert "overall" not in report


def test_a_t1_null_does_not_fail_the_t2_verdict():
    """The bug this split exists to fix: the primaries declare on T2, while
    the kill_diff bar is a T1 comparison."""
    fixture = verdict_fixture()
    fixture["beats_kill_diff_t1"] = False
    report = verdict_report(**fixture)
    assert report["verdicts"]["A2"]["helped"] is False
    assert report["verdicts"]["A1"]["helped"] is True


def test_a_non_deployable_candidate_cannot_clear_the_success_bar():
    fixture = verdict_fixture()
    fixture["deployable"] = {"swing_basis": False, "pooled": True}
    report = verdict_report(**fixture)
    assert "not deployable" in " ".join(report["verdicts"]["A1"]["notes"]).lower()


def test_the_analysis_plan_is_labelled_honestly():
    report = verdict_report(**verdict_fixture())
    assert "predeclared analysis plan" in report["note"].lower()
    assert "pre-registration" in report["note"].lower()


def test_the_matrix_refuses_mixed_runs_and_says_which_identity_differed():
    a = RunIdentity(dataset_fingerprint="1151:abc", fold_mapping_hash="deadbeef",
                    calculation_version="1/1")
    assert matrix_is_comparable(a, a) == (True, [])

    for field, value in (("dataset_fingerprint", "1150:abc"),
                         ("fold_mapping_hash", "cafe"),
                         ("calculation_version", "2/1")):
        other = RunIdentity(**{**a.__dict__, field: value})
        ok, reasons = matrix_is_comparable(a, other)
        assert not ok
        assert any(field in r for r in reasons)
```

Fixtures used above:

```python
def paired_fixture(effect=0.0, n=600, seed=71):
    from app.services.kill_order_refit import CandidateResult

    rng = np.random.default_rng(seed)
    y = (rng.uniform(size=n) < 0.5).astype(float)
    match_ids = np.repeat(np.arange(n // 20), 20)[:n]
    base = np.clip(rng.uniform(0.3, 0.7, size=n), 0.01, 0.99)

    def make(name, shift):
        result = CandidateResult(name=name)
        result.oof_probabilities = np.clip(base + shift * (y - 0.5), 0.01, 0.99)
        result.oof_y, result.oof_weights, result.oof_match_ids = y, np.ones(n), match_ids
        return result

    return make("a", effect), make("b", 0.0)


def verdict_fixture():
    return {
        "primaries": {
            "P1": {"delta": -0.002, "ci": [-0.004, -0.0005]},
            "P2": {"delta": -0.001, "ci": [-0.003, 0.001]},
            "P3": {"delta": -0.0015, "ci": [-0.003, -0.0002]},
            "P4": {"delta": -0.001, "ci": [-0.003, 0.001]},
        },
        "deployable": {"swing_basis": True, "pooled": True},
        "practically_equivalent": False,
        "targets_agree": False,
        "max_component_correlation": 0.81,
        "econ_negative_every_fold": True,
        "beats_kill_diff_t1": True,
        "stability": {"swing_basis": {"stable": True}, "pooled": {"stable": True}},
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -k "primar or verdict or matrix" -v`
Expected: FAIL — `cannot import name 'PRIMARY_COMPARISONS'`

- [ ] **Step 3: Append the implementation**

```python
# append to webapp/app/services/kill_order_refit.py

from dataclasses import asdict

PRIMARY_COMPARISONS = (
    {"name": "P1", "candidate": "swing_basis", "against": "current_graph",
     "target": "T2", "alpha": 0.025, "declares": "A1"},
    {"name": "P2", "candidate": "pooled", "against": "current_graph",
     "target": "T2", "alpha": 0.025, "declares": "A1"},
    {"name": "P3", "candidate": "component_tilt", "against": "stage_a_exact",
     "target": "T2", "alpha": 0.05, "declares": "C"},
    {"name": "P4", "candidate": "swing_basis", "against": "pooled",
     "target": "T2", "alpha": 0.05, "declares": None},
)

VERDICTS = {
    "A1": ("prediction, next rounds", (1, 2)),
    "A2": ("prediction, match outcome", (6,)),
    "B": ("collinearity", (3, 4, 5)),
    "C": ("structure", (7,)),
}

PRACTICAL_EQUIVALENCE_LOSS = 0.0008   # the parent report's own T2 CI half-width
PRACTICAL_EQUIVALENCE_RMS = 0.01      # 1% of the score sd
COLLINEARITY_THRESHOLD = 0.70         # just below today's observed minimum of 0.733
AGREEMENT_SPEARMAN = 0.90
AGREEMENT_RMS_SHARE = 0.15


@dataclass(frozen=True)
class RunIdentity:
    dataset_fingerprint: str
    fold_mapping_hash: str
    calculation_version: str


def matrix_is_comparable(left: RunIdentity, right: RunIdentity):
    """Stage A and Stage C rows may share a matrix ONLY if all three match.

    The fold-mapping hash is not redundant with the fingerprint: the parent
    project's committed results used the permutation-based assign_folds, so
    an identical match set can carry a completely different assignment --
    same fingerprint, different folds, a matrix that looks comparable and is
    not.
    """
    reasons = [
        f"{field} differs: {getattr(left, field)!r} != {getattr(right, field)!r}"
        for field in ("dataset_fingerprint", "fold_mapping_hash", "calculation_version")
        if getattr(left, field) != getattr(right, field)
    ]
    return (not reasons), reasons


def paired_delta(result_a, result_b, alpha=0.05, draws=500, seed=0) -> dict:
    """Paired held-out weighted-log-loss difference, clustered by match.

    Negative = A predicts better than B. `alpha` is the two-sided level:
    0.025 for a co-primary, 0.05 otherwise.
    """
    groups: dict[int, list] = {}
    for index, match_id in enumerate(result_a.oof_match_ids):
        groups.setdefault(int(match_id), []).append(index)

    def loss_of(result):
        def inner(sample):
            rows = [i for block in sample for i in block]
            return weighted_log_loss(
                result.oof_probabilities[rows], result.oof_y[rows], result.oof_weights[rows]
            )
        return inner

    low, high = paired_bootstrap_delta(
        loss_of(result_a), loss_of(result_b), groups, draws=draws, seed=seed, alpha=alpha
    )
    delta = float(
        weighted_log_loss(result_a.oof_probabilities, result_a.oof_y, result_a.oof_weights)
        - weighted_log_loss(result_b.oof_probabilities, result_b.oof_y, result_b.oof_weights)
    )
    return {
        "delta": delta, "ci": [float(low), float(high)], "alpha": float(alpha),
        "excludes_zero": bool(high < 0 or low > 0),
        "favours": result_a.name if delta < 0 else result_b.name,
    }


def verdict_report(primaries, deployable, practically_equivalent, targets_agree,
                   max_component_correlation, econ_negative_every_fold,
                   beats_kill_diff_t1, stability) -> dict:
    """Four verdicts, printed side by side and never summarized into one.

    A Verdict A1 null alongside a Verdict C signal is a coherent and
    expected outcome -- 'the shared curve's shape was right, and the mistake
    was sharing it' -- and collapsing it would destroy the finding.
    """
    notes: dict[str, list[str]] = {key: [] for key in VERDICTS}

    cleared = []
    for name in ("P1", "P2"):
        entry = primaries[name]
        candidate = next(p["candidate"] for p in PRIMARY_COMPARISONS if p["name"] == name)
        if not deployable.get(candidate, True):
            notes["A1"].append(f"{name}: {candidate} is not deployable and cannot clear the bar")
            continue
        if not stability.get(candidate, {}).get("stable", False):
            notes["A1"].append(f"{name}: {candidate} did not pass the stability criterion")
            continue
        if entry["ci"][1] < 0:
            cleared.append(name)

    items = {
        1: bool(cleared),
        2: not practically_equivalent,
        3: targets_agree,
        4: max_component_correlation < COLLINEARITY_THRESHOLD,
        5: not econ_negative_every_fold,
        6: beats_kill_diff_t1,
        7: primaries["P3"]["ci"][1] < 0,
    }

    return {
        "note": (
            "A predeclared ANALYSIS PLAN, not a pre-registration: this dataset "
            "was used to design the candidates and set the thresholds, so it "
            "does not carry the independence a pre-registration claims. What it "
            "does buy is that the thresholds cannot move after the results."
        ),
        "thresholds": {
            "practical_equivalence_log_loss": PRACTICAL_EQUIVALENCE_LOSS,
            "practical_equivalence_rms_share": PRACTICAL_EQUIVALENCE_RMS,
            "collinearity_below": COLLINEARITY_THRESHOLD,
            "agreement_spearman_above": AGREEMENT_SPEARMAN,
            "agreement_rms_share_below": AGREEMENT_RMS_SHARE,
        },
        "primaries": primaries,
        "cleared_primaries": cleared,
        "verdicts": {
            key: {
                "question": question,
                "items": {str(i): bool(items[i]) for i in indices},
                "helped": all(items[i] for i in indices),
                "notes": notes[key],
            }
            for key, (question, indices) in VERDICTS.items()
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: PASS (30 tests)

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Add predeclared comparisons, four verdicts and matrix refusal" -m "G2 and G3 co-primary at 97.5%, B2 vs B0 at 95%, G2-vs-G3 reported without a claim. Verdicts split A1/A2 so a T1 null cannot fail a T2 question. The matrix refuses to mix Stage A and Stage C rows unless dataset, folds and calculation version all match." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 16: The CLI

**Files:**
- Create: `webapp/scripts/evaluate_kill_order.py`
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: a runnable script. `python scripts/evaluate_kill_order.py [--out report.json] [--database-url ...] [--draws N] [--quick]`.

**Report order matters and is not cosmetic.** Stage C0 prints **first**, because if the metric does not move when the graph is swapped then no downstream yardstick difference was ever possible and every fitted number below should be read in that light. The verdicts print last, with the four kept separate.

- [ ] **Step 1: Write the failing test**

```python
# append to webapp/tests/test_kill_order_refit.py

def test_the_report_sections_are_ordered_with_stage_c0_first():
    from app.services.kill_order_refit import REPORT_SECTIONS

    # identity is provenance, not a finding; stage_c0 is the first CONTENT
    # section and must precede every fitted number.
    assert REPORT_SECTIONS[0] == "identity"
    assert REPORT_SECTIONS[1] == "stage_c0"
    assert REPORT_SECTIONS[-1] == "verdicts"
    assert REPORT_SECTIONS.index("stage_c0") < REPORT_SECTIONS.index("family_a")
    assert REPORT_SECTIONS.index("control_ladder") < REPORT_SECTIONS.index("verdicts")
    assert REPORT_SECTIONS.index("player_level") < REPORT_SECTIONS.index("verdicts")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -k report_sections -v`
Expected: FAIL — `cannot import name 'REPORT_SECTIONS'`

- [ ] **Step 3: Declare the order and write the script**

Add to `kill_order_refit.py`:

```python
REPORT_SECTIONS = (
    "identity",            # dataset fingerprint, fold mapping hash, calculation version
    "stage_c0",            # FIRST: how much can the graph move Impact at all
    "loading",             # eligible / excluded match counts
    "conditioning",        # condition number, effective rank, VIF
    "per_parameter",       # exposure, rounds touched, VIF -- diagnostics only
    "family_a",            # G0-G4 per fold: recovered graphs, d, deployability
    "family_b",            # the B0-B3 ladder
    "control_ladder",      # five rungs, headline on 4 -> 5
    "yardstick_matrix",    # targets x yardsticks, refusing mixed Stage A rows
    "player_level",        # death impact and trades
    "stability",           # graph-level bootstrap ratio
    "deferral_check",      # match count against the ~4,000 re-open threshold
    "verdicts",            # LAST, four of them, never merged
)
```

Then `scripts/evaluate_kill_order.py`:

```python
"""Stage C: refit the kill-order graph and report whether it helps.

    python scripts/evaluate_kill_order.py --out scratch-kill-order.json

Requires a live Postgres. Costs a full replay of every match (minutes).
Changes nothing that ships: this reads impact.py and writes a report.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.impact_eval import (  # noqa: E402
    PRIMARY_T2, dataset_fingerprint, fold_mapping_hash, load_all_observations, stable_folds,
)
from app.services.kill_order_leverage import (  # noqa: E402
    load_all_leverage, state_visits_for_match,
)
from app.services.kill_order_refit import (  # noqa: E402
    PRIMARY_COMPARISONS, REPORT_SECTIONS, RunIdentity, control_ladder, paired_delta,
    player_level_report, run_nested_cv, stage_c0_report, stability_report, verdict_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write the full report as JSON")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    parser.add_argument("--draws", type=int, default=200, help="bootstrap draws")
    parser.add_argument("--quick", action="store_true",
                        help="Stage C0 only -- the block that runs before any fitting")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app.db import SessionLocal

    db = SessionLocal()
    report: dict = {section: None for section in REPORT_SECTIONS}

    loading: dict = {}
    team_rows, player_rows = load_all_leverage(db, report=loading)
    report["loading"] = loading

    match_ids = sorted({row.match_id for row in team_rows})
    folds = stable_folds(match_ids)
    identity = RunIdentity(
        dataset_fingerprint=dataset_fingerprint(match_ids),
        fold_mapping_hash=fold_mapping_hash(folds),
        calculation_version=_calculation_version(),
    )
    report["identity"] = identity.__dict__

    visits = []
    for match_id in match_ids:
        visits.extend(state_visits_for_match(db, match_id))

    # FIRST, always: if the metric does not move, everything below is read
    # in that light rather than as a headline of its own.
    report["stage_c0"] = stage_c0_report(team_rows, player_rows, visits, draws=args.draws)
    _print_stage_c0(report["stage_c0"])

    if args.quick:
        _emit(report, args.out)
        return 0

    observations = load_all_observations(db, use_realized_swing=False)
    results = run_nested_cv(
        team_rows, observations, PRIMARY_T2,
        candidates=["current_graph", "swing_plugin", "swing_affine", "swing_basis",
                    "pooled", "free"],
        l2_grid=[0.01, 0.1, 1.0, 10.0, 100.0], state_visits=visits,
    )
    report["family_a"] = {name: _summarize(result) for name, result in results.items()}
    report["control_ladder"] = control_ladder(team_rows, observations, PRIMARY_T2,
                                              draws=args.draws)
    report["player_level"] = player_level_report(
        player_rows, _match_outcomes(observations), _shipped(), draws=args.draws
    )
    report["stability"] = {
        name: stability_report(result, _shipped(), _exposure(team_rows), draws=args.draws)
        for name, result in results.items() if name not in ("current_graph",)
    }
    report["deferral_check"] = {
        "matches": len(match_ids), "reopen_threshold": 4000,
        "reachable": len(match_ids) >= 4000,
        "note": "4,000 re-opens the deferred per-component fits for a LOOK, not a verdict.",
    }

    primaries = {
        spec["name"]: paired_delta(results[spec["candidate"]], results[spec["against"]],
                                   alpha=spec["alpha"], draws=args.draws)
        for spec in PRIMARY_COMPARISONS
        if spec["candidate"] in results and spec["against"] in results
    }
    report["verdicts"] = verdict_report(
        primaries=primaries,
        deployable={n: all(f.deployable for f in r.per_fold.values())
                    for n, r in results.items()},
        practically_equivalent=_practically_equivalent(report),
        targets_agree=False,
        max_component_correlation=1.0,
        econ_negative_every_fold=True,
        beats_kill_diff_t1=False,
        stability=report["stability"],
    )
    _print_verdicts(report["verdicts"])
    _emit(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The `_print_*`, `_summarize`, `_emit`, `_shipped`, `_exposure`, `_match_outcomes`,
`_calculation_version` and `_practically_equivalent` helpers are mechanical
formatting and lookups. `_calculation_version` returns
`f"{IMPACT_CALCULATION_VERSION}/{STAGE_C_SCHEMA_VERSION}"`; add
`STAGE_C_SCHEMA_VERSION = 1` to `kill_order_refit.py` and bump it whenever a
change would alter reported numbers.

**Fields left at placeholder values in the verdict call above** — `targets_agree`,
`max_component_correlation`, `econ_negative_every_fold`, `beats_kill_diff_t1` —
require the T1 and WPA runs and the component correlation recompute. Wire them
from those runs rather than shipping the constants; a verdict computed from a
hardcoded input is worse than no verdict.

- [ ] **Step 4: Run the test and a smoke run**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v
docker compose -p valomaths-private up -d
.\.venv\Scripts\python.exe scripts\evaluate_kill_order.py --quick --out scratch-kill-order-c0.json
```

Expected: tests PASS (31), and the quick run prints the Stage C0 block and writes JSON.

- [ ] **Step 5: Full run**

```bash
.\.venv\Scripts\python.exe scripts\evaluate_kill_order.py --out scratch-kill-order.json
```

Expected: minutes. Read Stage C0 before reading anything else.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/scripts/evaluate_kill_order.py webapp/tests/test_kill_order_refit.py
git commit -m "Add the evaluate_kill_order CLI" -m "Stage C0 prints first by design: if the metric does not move when the graph is swapped, every fitted number below is read in that light. Verdicts print last, four of them, never merged." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---


### Task 17: Family B orchestration — making P3 produceable

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py`
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: `fit_family_b`, `effective_surfaces`, `FAMILY_B` from Task 9; `run_nested_cv` from Task 10.
- Produces: `run_nested_cv(..., family="A"|"B")` handling both, and `align_target` gaining `team_rows` so Family B can reach the per-round blocks.

**Why this is a task and not a footnote.** Task 9 defines the whole B0-B3 ladder and Task 10's orchestrator only ever calls `fit_family_a`. The two fitters also take different shapes — `fit_family_a` wants `(leverage, damage, y, weights)`, `fit_family_b` wants `(team_rows, y, weights)`. **So P3, Family B's primary comparison, cannot run**, and Task 15's `verdict_report` indexes `primaries["P3"]` and raises `KeyError` on the full run. This is the single blocking defect in the plan as first written.

**The design decision:** one orchestrator, not two. `AlignedTarget` carries the aligned `team_rows` alongside the collapsed leverage, so `run_nested_cv` can hand Family A its matrices and Family B its rows from the same fold split, the same calibration path and the same identity checks. Two orchestrators would duplicate every leakage rule, and the leakage rules are the thing most worth not duplicating.

**T1 is a special case and must raise, not silently mis-aggregate.** T1 collapses twelve rounds into one row; Family B's columns are per-round sums over a fixed graph, which *do* aggregate linearly — but the `team_rows` list for a T1 row is a group, not a single row. Family B on T1 therefore sums each match's first-half rows into one synthetic row, and the test pins that against a hand-computed value.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_curves import FAMILY_B


def test_family_b_runs_through_the_same_orchestrator():
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=list(FAMILY_B), l2_grid=[1.0], n_folds=5,
                            family="B")
    assert set(results) == set(FAMILY_B)
    for result in results.values():
        assert result.oof_scores is not None
        for fitted in result.per_fold.values():
            assert set(fitted.train_match_ids).isdisjoint(fitted.test_match_ids)


def test_family_b_candidates_are_scored_on_the_same_rows_as_family_a():
    """P3 compares a Family B rung against another Family B rung, but the
    matrix places both families side by side -- so their row sets must
    match or every cross-family number is meaningless."""
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    a = run_nested_cv(leverage, observations, PRIMARY_T2, candidates=["free"],
                      l2_grid=[1.0], n_folds=5, family="A")
    b = run_nested_cv(leverage, observations, PRIMARY_T2, candidates=["component_tilt"],
                      l2_grid=[1.0], n_folds=5, family="B")
    assert np.array_equal(a["free"].oof_row_ids, b["component_tilt"].oof_row_ids)


def test_p3_can_actually_be_produced():
    """The regression test for the blocking defect: verdict_report indexes
    primaries['P3'], and before this task nothing produced it."""
    observations = synthetic_observations(matches=60)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["stage_a_exact", "component_tilt"],
                            l2_grid=[1.0], n_folds=5, family="B")
    delta = paired_delta(results["component_tilt"], results["stage_a_exact"], alpha=0.05)
    assert np.isfinite(delta["delta"])
    assert len(delta["ci"]) == 2


def test_family_b_rungs_carry_their_effective_surfaces():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["component_tilt_symmetric"], l2_grid=[1.0],
                            n_folds=5, family="B")
    fitted = next(iter(results["component_tilt_symmetric"].per_fold.values()))
    assert fitted.surfaces is not None
    assert set(fitted.surfaces) == {
        f"{c}_{s}" for c in COMPONENTS for s in ("kill", "death")
    }
    assert all(v.shape == (len(PARAMS),) for v in fitted.surfaces.values())


def test_an_unknown_family_is_refused():
    observations = synthetic_observations(matches=20)
    with pytest.raises(ValueError, match="family"):
        run_nested_cv(leverage_for(observations), observations, PRIMARY_T2,
                      candidates=["free"], l2_grid=[1.0], family="C")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -k family_b -v`
Expected: FAIL — `run_nested_cv() got an unexpected keyword argument 'family'`

- [ ] **Step 3: Extend `AlignedTarget` and the orchestrator**

Add `team_rows: tuple` to `AlignedTarget` — one `TeamLeverageRow` per output row for T2 and WPA, and for T1 a synthetic row per match holding the first half's summed blocks:

```python
def _aggregate_rows(rows):
    """One synthetic row standing for a group. Family B's columns are linear
    in the blocks, so summing the blocks and summing the columns agree --
    a test pins that rather than trusting it."""
    from app.services.kill_order_leverage import TeamLeverageRow

    first = rows[0]
    return TeamLeverageRow(
        match_id=first.match_id, round_id=-1, round_number=-1,
        damage_diff=float(sum(r.damage_diff for r in rows)),
        kill=sum(r.kill for r in rows), death=sum(r.death for r in rows),
        death_untraded=sum(r.death_untraded for r in rows),
        terminal_alive_diff=float(rows[-1].terminal_alive_diff),
        total_kills=int(sum(r.total_kills for r in rows)),
    )
```

Then in `run_nested_cv`, add `family="A"` and branch the fit:

```python
    if family not in ("A", "B"):
        raise ValueError(f"unknown family {family!r}; expected 'A' or 'B'")
```

```python
        for name in candidates:
            if family == "A":
                l2 = _select_l2(name, aligned, train_mask, l2_grid, state_visits or [],
                                leverage_rows)
                candidate = fit_family_a(name, train, test, table, l2, exposure,
                                         shipped_graph(), controls=outer_controls)
                in_fold = fit_family_a(name, train, train_on_train, table, l2, exposure,
                                       shipped_graph(), controls=self_controls)
                surfaces = None
            else:
                l2 = float(l2_grid[0]) if len(l2_grid) == 1 else _select_l2_b(
                    name, aligned, train_mask, l2_grid
                )
                train_rows = [aligned.team_rows[i] for i in np.flatnonzero(train_mask)]
                test_rows = [aligned.team_rows[i] for i in np.flatnonzero(test_mask)]
                candidate = fit_family_b(
                    name, (train_rows, aligned.y[train_mask], aligned.weights[train_mask]),
                    (test_rows, aligned.weights[test_mask]), shipped_graph(), l2,
                    controls=outer_controls, exposure=exposure,
                )
                in_fold = fit_family_b(
                    name, (train_rows, aligned.y[train_mask], aligned.weights[train_mask]),
                    (train_rows, aligned.weights[train_mask]), shipped_graph(), l2,
                    controls=self_controls, exposure=exposure,
                )
                _, column_names = family_b_columns(train_rows[:1], shipped_graph(), name)
                surfaces = effective_surfaces(candidate.weights, column_names, shipped_graph())
```

and carry `surfaces` and `weights` onto `FoldFit` (both default `None`), so Task 14's
player-level read and Task 19's report can consume them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Run Family B through the shared orchestrator so P3 can be produced" -m "Task 9 defined the B0-B3 ladder and nothing executed it; verdict_report indexes primaries['P3'] and raised KeyError on the full run. One orchestrator rather than two, so the leakage rules are not duplicated." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 18: All three frozen targets, and whether they agree

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py`
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: `PRIMARY_T1`, `PRIMARY_T2`, the WPA target and `fit_value_model` from `win_probability`.
- Produces: `run_all_targets(...) -> dict[str, dict[str, CandidateResult]]`, `target_agreement(graphs_by_target, exposure) -> dict`.

**Why:** the CLI ran T2 only. Verdict item 3 asks whether the graphs fitted against T1, T2 and WPA agree, and verdict A2 is a T1 question — neither has an input without this. The spec's frozen targets are all three, and T1 in particular is the closest thing here to the product question the parent project started from.

**T1's restriction carries through.** G3 and G4 are **not** fitted against T1 — 26 free parameters against 1,114 matches is the ratio this project already calls indefensible. T1 runs G0, G1a, G1b and G2 only, and its column for the others reads `"not fitted -- insufficient matches per parameter"` rather than a number.

**The agreement thresholds are declared, not eyeballed:** all three pairwise Spearman rank correlations above **0.90**, and all three pairwise exposure-weighted RMS differences below **15%** of the exposure-weighted mean price.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import run_all_targets, target_agreement


def test_t1_refuses_the_high_dimensional_candidates():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_all_targets(leverage, observations, l2_grid=[1.0], n_folds=5)
    assert set(results) == {"T1", "T2", "WPA"}
    assert "pooled" not in results["T1"]
    assert "free" not in results["T1"]
    assert "swing_basis" in results["T1"]
    assert "pooled" in results["T2"]


def test_agreement_is_measured_against_declared_thresholds():
    exposure = np.ones(len(PARAMS))
    base = shipped_graph()
    agree = target_agreement(
        {"T1": base, "T2": base * 1.02, "WPA": base * 0.99}, exposure
    )
    assert agree["agree"] is True
    assert min(agree["spearman"].values()) > 0.90

    disagree = target_agreement(
        {"T1": base, "T2": base[::-1].copy(), "WPA": base * 3.0}, exposure
    )
    assert disagree["agree"] is False
    assert disagree["thresholds"]["spearman_above"] == 0.90
    assert disagree["thresholds"]["rms_share_below"] == 0.15
```

- [ ] **Step 2: Run to verify failure, then implement**

```python
# append to webapp/app/services/kill_order_refit.py

T1_ELIGIBLE = ("current_graph", "swing_plugin", "swing_affine", "swing_basis")


def run_all_targets(leverage_rows, observations, l2_grid, n_folds=5, seed=0,
                    state_visits=None, value_model=None):
    """T1, T2 and WPA, each on its own frozen definition.

    G3 and G4 are excluded from T1 by design, not by accident: 26 free
    parameters against 1,114 matches is the ratio this project already calls
    indefensible, and running it anyway then declining to believe it would be
    theatre.
    """
    from app.services.impact_eval import PRIMARY_T1, PRIMARY_T2, TargetConfig

    plans = {
        "T1": (PRIMARY_T1, list(T1_ELIGIBLE)),
        "T2": (PRIMARY_T2, list(FAMILY_A)),
        "WPA": (TargetConfig(name="WPA"), list(FAMILY_A)),
    }
    out: dict = {}
    for label, (config, candidates) in plans.items():
        out[label] = run_nested_cv(
            leverage_rows, observations, config, candidates=candidates,
            l2_grid=l2_grid, n_folds=n_folds, seed=seed, state_visits=state_visits,
        )
    return out


def target_agreement(graphs_by_target, exposure) -> dict:
    """Do the graphs fitted against different targets agree?

    Stage A's answer for the OUTER weights was an emphatic no -- T1 put zero
    weight on econ, T2 on everything but swing, WPA on swing. This asks the
    same question of the curve, against thresholds declared before the run.
    """
    labels = sorted(graphs_by_target)
    normalized = {k: normalize_for_display(v, exposure) for k, v in graphs_by_target.items()}
    mean_price = float(np.mean([_weighted_rms(g, exposure) for g in normalized.values()]))

    spearman, rms_share = {}, {}
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            key = f"{a}~{b}"
            ranks_a, ranks_b = _average_ranks(normalized[a]), _average_ranks(normalized[b])
            spearman[key] = float(np.corrcoef(ranks_a, ranks_b)[0, 1])
            rms_share[key] = float(
                _weighted_rms(normalized[a] - normalized[b], exposure) / mean_price
            )
    return {
        "spearman": spearman,
        "rms_share": rms_share,
        "thresholds": {"spearman_above": AGREEMENT_SPEARMAN,
                       "rms_share_below": AGREEMENT_RMS_SHARE},
        "agree": bool(min(spearman.values()) > AGREEMENT_SPEARMAN
                      and max(rms_share.values()) < AGREEMENT_RMS_SHARE),
    }
```

- [ ] **Step 3: Run the tests and commit**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Run all three frozen targets and measure whether they agree" -m "Verdict item 3 and verdict A2 had no input while only T2 ran. T1 excludes the 26-parameter candidates by design." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 19: The yardstick matrix and the remaining report sections

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py`
- Test: `webapp/tests/test_kill_order_refit.py` (append)

**Interfaces:**
- Consumes: the parent's `YARDSTICKS`, `Candidate`, `CURRENT_IMPACT_CANDIDATE`, `BASELINE_CANDIDATES`; Task 17's per-fold candidates.
- Produces: `yardstick_matrix(...) -> dict`, `component_correlations(team_rows, graph) -> dict`, `factor_profiles(state_terms) -> dict`.

**Three sections the spec requires and nothing produced.** The matrix itself; the component correlation matrix and drop-one costs recomputed under each candidate graph (verdict item 4 reads the first of those, and the CLI was passing a hardcoded `1.0`); and the measured per-state factor profiles with their axis correlations, which are the evidence Family B rests on and which the spec says must be "re-derived rather than trusted from this spec."

**The matrix must refuse rather than mislead.** Stage A rows join only when `matrix_is_comparable` passes on all three identity values. Until Stage A is re-run under `stable_folds`, the two stages print as separate tables — that refusal is correct behaviour.

**`current_impact` rides alongside `current_graph`.** The former reads the rounded stored `impact_diff`; the latter is the same shipped values through the unrounded leverage pipeline. Their gap is printed on its own line so the rounding cost is visible rather than absorbed into a comparison.

- [ ] **Step 1: Write the failing tests**

```python
# append to webapp/tests/test_kill_order_refit.py

from app.services.kill_order_refit import (
    component_correlations, factor_profiles, yardstick_matrix,
)


def test_the_matrix_covers_every_candidate_on_every_yardstick():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph", "free"], l2_grid=[1.0], n_folds=5)
    matrix = yardstick_matrix(leverage, observations, results, draws=20)
    assert set(matrix["cells"]) == {"first_half_to_match", "full_match_to_match",
                                    "forward_rounds"}
    for cells in matrix["cells"].values():
        assert {"current_graph", "free", "kill_diff"} <= set(cells)


def test_the_matrix_prints_the_rounding_gap_between_the_two_baselines():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph"], l2_grid=[1.0], n_folds=5)
    matrix = yardstick_matrix(leverage, observations, results, draws=20)
    assert "rounding_gap" in matrix
    assert "current_impact" in matrix["rounding_gap"]["compared"]


def test_the_matrix_refuses_stage_a_rows_when_identity_differs():
    observations = synthetic_observations(matches=40)
    leverage = leverage_for(observations)
    results = run_nested_cv(leverage, observations, PRIMARY_T2,
                            candidates=["current_graph"], l2_grid=[1.0], n_folds=5)
    a = RunIdentity("1151:abc", "deadbeef", "1/1")
    b = RunIdentity("1151:abc", "OTHER", "1/1")
    matrix = yardstick_matrix(leverage, observations, results, draws=20,
                              stage_a_rows={"fitted_T1": {}}, stage_a_identity=b,
                              identity=a)
    assert matrix["stage_a_joined"] is False
    assert any("fold_mapping_hash" in r for r in matrix["stage_a_refusal"])


def test_component_correlations_are_recomputed_under_a_candidate_graph():
    """Verdict item 4 reads the maximum of these. The CLI was passing a
    hardcoded 1.0, which would have made the verdict meaningless."""
    observations = synthetic_observations(matches=30)
    leverage = leverage_for(observations)
    flat = np.full(len(PARAMS), 136.6)
    under_shipped = component_correlations(leverage, shipped_graph())
    under_flat = component_correlations(leverage, flat)
    assert set(under_shipped["matrix"]) == {"econ", "time", "swing"}
    assert 0.0 <= under_shipped["max_abs"] <= 1.0
    assert under_shipped["max_abs"] != under_flat["max_abs"]
```

- [ ] **Step 2: Implement, run, commit**

`yardstick_matrix` converts each candidate's per-fold recovered graph into a
parent-project `Candidate` over an `impact_diff`-shaped column, scores it through
the parent's own `YARDSTICKS` functions so Stage A and Stage C cells are computed
by identical code, calibrates inside each outer fold, and bootstraps by match.
`component_correlations` rebuilds `econ_impact` / `time_impact` / `swing_impact`
per round under a given graph and returns their correlation matrix plus
`max_abs`. `factor_profiles` averages each per-kill factor by state and
correlates the profiles against `MARGIN` and `TOTAL_ALIVE`.

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_kill_order_refit.py -v
git add webapp/app/services/kill_order_refit.py webapp/tests/test_kill_order_refit.py
git commit -m "Add the yardstick matrix, component correlations and factor profiles" -m "Three report sections the spec requires and nothing produced. Verdict item 4 was reading a hardcoded constant." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 20: Sensitivities and the end-to-end acceptance test

**Files:**
- Modify: `webapp/app/services/kill_order_refit.py`, `webapp/scripts/evaluate_kill_order.py`
- Test: `webapp/tests/test_kill_order_acceptance.py` (new)

**Interfaces:**
- Produces: `fallback_sensitivity(...)`, `outer_weight_sensitivity(...)`, `alternation_sensitivity(...)`, and an acceptance test asserting the full report is placeholder-free.

**The three sensitivities, all reported and none adopted.** The fallback run drops the 497 affected rounds and reports whether the answer moves — a shift there is a data-quality finding about the resurrection heuristic, not a graph finding. The outer-weight run repeats the primary fit with `w` held at each target's Stage A weighting, measuring how much the graph answer depends on the outer weights. The alternation run does `b` → `w` → `b`, **exactly two `b` steps, declared up front**, and reports whether the second moved anything; iterating to convergence is refused because the objective is non-convex and "we stopped when it stopped moving" is a selection surface dressed as a numerical detail.

**The acceptance test is the point of this task.** It runs the whole pipeline on a small synthetic corpus and asserts the report is *complete*: every section populated, P1-P4 all present, all four verdicts computed from real inputs, and no placeholder constants anywhere. An earlier draft of this plan would have failed it at `primaries["P3"]`.

- [ ] **Step 1: Write the acceptance test**

```python
# webapp/tests/test_kill_order_acceptance.py
"""End-to-end: the full report is produced, complete, and free of
placeholders. This is the test that would have caught the plan shipping
fifteen tasks that could not produce their own product."""

import numpy as np
import pytest

from app.services.kill_order_refit import REPORT_SECTIONS, build_full_report
from tests.test_kill_order_refit import leverage_for, synthetic_observations


@pytest.fixture(scope="module")
def report():
    observations = synthetic_observations(matches=80)
    return build_full_report(leverage_for(observations), observations,
                             player_rows=[], state_visits=[], draws=20, l2_grid=[1.0])


def test_every_declared_section_is_populated(report):
    for section in REPORT_SECTIONS:
        assert report.get(section) is not None, f"{section} was never filled in"


def test_all_four_primary_comparisons_exist(report):
    assert set(report["verdicts"]["primaries"]) == {"P1", "P2", "P3", "P4"}
    for entry in report["verdicts"]["primaries"].values():
        assert np.isfinite(entry["delta"])
        assert len(entry["ci"]) == 2


def test_all_four_verdicts_are_computed(report):
    assert set(report["verdicts"]["verdicts"]) == {"A1", "A2", "B", "C"}
    for verdict in report["verdicts"]["verdicts"].values():
        assert isinstance(verdict["helped"], bool)


def test_no_verdict_input_is_a_placeholder(report):
    """The CLI once passed max_component_correlation=1.0 and
    beats_kill_diff_t1=False as constants. A verdict computed from a
    hardcoded input is worse than no verdict."""
    inputs = report["verdicts"]["inputs"]
    assert inputs["max_component_correlation"] != 1.0
    assert inputs["source"]["max_component_correlation"] == "component_correlations"
    assert inputs["source"]["beats_kill_diff_t1"] == "yardstick_matrix"
    assert inputs["source"]["targets_agree"] == "target_agreement"


def test_the_report_is_json_serializable(report):
    """NumPy arrays, NaNs and dataclasses all break json.dump silently or
    loudly; the CLI writes this file and it must survive the round trip."""
    import json

    from app.services.kill_order_refit import to_jsonable

    text = json.dumps(to_jsonable(report))
    restored = json.loads(text)
    assert set(restored) == set(report)
```

- [ ] **Step 2: Implement `build_full_report`, `to_jsonable` and the three sensitivities**

`build_full_report` is the function the CLI calls; the CLI becomes argument
parsing plus printing. `to_jsonable` walks the report converting numpy scalars
and arrays to Python types, `NaN`/`Inf` to `None`, and dataclasses to dicts.
`verdict_report` gains an `inputs` block recording every value it consumed and
where it came from, which is what the placeholder test reads.

- [ ] **Step 3: Fix the CLI's observation filtering**

`load_all_leverage` excludes matches on error, but the CLI passed *all*
observations into `build_target` — so the target could be built over matches the
extractor dropped, misaligning `y` with `X`. Filter first, then assert:

```python
    extracted = {row.match_id for row in team_rows}
    observations = [o for o in load_all_observations(db, use_realized_swing=False)
                    if o.match_id in extracted]
    covered = {o.round_id for o in observations}
    missing = [r.round_id for r in team_rows if r.round_id not in covered]
    if missing:
        raise SystemExit(
            f"{len(missing)} extracted rounds have no observation "
            f"(first: {missing[:5]}); the two loaders disagree about eligibility"
        )
```

and change `load_all_leverage` to record *why* each match was excluded rather
than catching `(KeyError, ValueError)` and moving on — a broad silent catch turns
an extractor bug into a changed study population:

```python
        except (KeyError, ValueError) as exc:
            excluded.append({"match_id": match_id, "reason": f"{type(exc).__name__}: {exc}"})
```

- [ ] **Step 4: Run everything**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -v
docker compose -p valomaths-private up -d
.\.venv\Scripts\python.exe scripts\evaluate_kill_order.py --out scratch-kill-order.json
```

Expected: all tests PASS, and the full run produces a report with no `None`
sections and no placeholder inputs.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/kill_order_refit.py webapp/scripts/evaluate_kill_order.py webapp/tests/test_kill_order_acceptance.py
git commit -m "Add sensitivities and an end-to-end acceptance test" -m "Asserts the report is complete and placeholder-free: every section populated, P1-P4 present, four verdicts from real inputs. Also filters observations to the extracted match set and records exclusion reasons." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## After the plan

Running the tool is not the end of the stage. The report has to be **read**, and
the spec is explicit about what reading it honestly looks like:

- **Stage C0 leads.** If the metric barely moves, say that first and frame
  everything else as confirming or contradicting it.
- **A null is a complete deliverable.** The prior is already measured: R² 0.970
  between the shipped graph and the data's own swing curve, r = 0.998 between the
  resulting metrics, ten of twenty-five directions barely identified. "The
  hand-tuned graph was already approximately right, here is how we know, and
  here is the graph the data would have written" is a real finding.
- **The four verdicts stay separate.** A Family A null beside a Family B signal
  is coherent and expected: *the shared curve's shape was right, and the mistake
  was sharing it.*
- **Nothing here adopts anything.** Changing `_KILL_ORDER_GRAPH` is a separate,
  deliberate act, and a larger one than adopting new `FACTOR_WEIGHTS` was —
  `kill_order_bonus` also feeds the `clutch_*`, `post_plant_*` and `econ_*`
  display columns the site shows, so adoption needs an
  `IMPACT_CALCULATION_VERSION` bump, a full rescore, a `diff_impact_scores.py`
  pass, and the `player_view_cache` invalidation the version bump triggers.

**Prerequisite that is not in any task:** the shared Stage A / Stage C yardstick
matrix needs Stage A re-run under `stable_folds` on Stage C's snapshot. Until
that happens, Task 15's `matrix_is_comparable` refuses the mixed matrix and the
report prints the two stages as separate tables. That refusal is correct
behaviour and must not be worked around.

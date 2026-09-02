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

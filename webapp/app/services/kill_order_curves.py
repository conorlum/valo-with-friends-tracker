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

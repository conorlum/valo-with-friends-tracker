"""Experimental, uncentered time factor fitted to state-adjusted bucket rates.

This is a review candidate, not connected to impact.py. It intentionally uses
separate empirical time curves for attacker/defender kills, without a monotone
constraint or an advantage interaction. State was adjusted during estimation.

The score mapping is a POLICY: factor = clamp(1 + strength * (p(dt) - p_ref)).
At strength=1, one percentage point of rate difference is one percent of the
TIME COMPONENT's kill-order credit, not one Impact point or 1% of total Impact.
The same event factor must be used on the victim side, using the KILLER's side.

The fitted input contains b1..b30 and one heterogeneous dt>30 reference.
REF is a category, NOT an observation at second 31. Consequently the factor
returns exactly 1 past 30s, with an explicitly documented jump at that boundary.
Do not infer a physical threshold or fabricate a long-distance curve from REF.
Before live use: revisit that boundary, validate on held-out matches, and
calibrate contribution-weighted centering plus the death-side residual.
"""

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class EmpiricalTimingCurve:
    times: tuple[float, ...]
    rates: tuple[float, ...]
    reference_rate: float
    cutoff: float = 30.0

    def __post_init__(self):
        if len(self.times) < 2 or len(self.times) != len(self.rates):
            raise ValueError("At least two aligned time/rate observations are required")
        if not all(math.isfinite(t) for t in self.times):
            raise ValueError("Time coordinates must be finite")
        if not all(a < b for a, b in zip(self.times, self.times[1:])):
            raise ValueError("Time coordinates must be strictly increasing")
        if not math.isfinite(self.cutoff) or not 0 < self.times[0] < self.times[-1] <= self.cutoff:
            raise ValueError("Time coordinates must lie inside the pre-plant window")
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in (*self.rates, self.reference_rate)):
            raise ValueError("Rates must be probabilities")

    def adjusted_rate(self, dt: float) -> float:
        """Linear interpolation at 1-second bucket MIDPOINTS; flat end half-bins.

        No fitted value exists at dt<=0 or for a missing/invalid timestamp.
        Those events belong outside this model, including the post-plant regime.
        """
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and strictly pre-plant")
        if dt > self.cutoff:
            return self.reference_rate
        if dt <= self.times[0]:
            return self.rates[0]
        if dt >= self.times[-1]:
            return self.rates[-1]
        right = bisect_right(self.times, dt)
        left = right - 1
        fraction = (dt - self.times[left]) / (self.times[right] - self.times[left])
        return self.rates[left] + fraction * (self.rates[right] - self.rates[left])

    def factor(self, dt: float | None, *, strength: float = 1.0,
               use_realized: bool = True, floor: float = 0.2, ceiling: float = 1.7) -> float:
        """Return the pre-centering multiplier; unsupported events stay neutral."""
        if not math.isfinite(strength) or strength < 0:
            raise ValueError("strength must be finite and nonnegative")
        if not (math.isfinite(floor) and math.isfinite(ceiling) and 0 < floor <= 1 <= ceiling):
            raise ValueError("Bounds must be finite, positive, and include the neutral factor 1")
        if not use_realized or dt is None or not math.isfinite(dt) or dt <= 0 or dt > self.cutoff:
            return 1.0
        raw = 1.0 + strength * (self.adjusted_rate(dt) - self.reference_rate)
        return max(floor, min(ceiling, raw))


def smooth_bucket_rates(rates, ci_low, ci_high, *, penalty: float = 1.0):
    """Fit 30 one-second rate estimates, using NumPy only while fitting.

    Minimize sum(w_i * (f_i - p_i)^2) + penalty * sum((Delta^2 f_i)^2).
    Weights are inverse squared marginal bootstrap-CI widths, normalized to
    mean 1. This is a descriptive precision-weighted smoother, not a model of
    independent Bernoulli trials. Cross-bucket covariance is unavailable.
    Penalty is explicit, not selected using outcomes or claimed held-out skill.
    """
    import numpy as np

    p, lo, hi = (np.asarray(x, dtype=float) for x in (rates, ci_low, ci_high))
    if p.shape != (30,) or lo.shape != p.shape or hi.shape != p.shape:
        raise ValueError("Exactly 30 aligned one-second buckets are required")
    if not np.all(np.isfinite([p, lo, hi])) or np.any(p < 0) or np.any(p > 1):
        raise ValueError("Bucket rates must be finite probabilities")
    if np.any(lo < 0) or np.any(hi > 1) or np.any(hi <= lo):
        raise ValueError("Intervals must have positive width inside [0, 1]")
    if not math.isfinite(penalty) or penalty < 0:
        raise ValueError("penalty must be finite and nonnegative")
    weights = 1.0 / np.square(hi - lo)
    weights /= weights.mean()
    differences = np.diff(np.eye(len(p)), n=2, axis=0)
    fitted = np.linalg.solve(np.diag(weights) + penalty * differences.T @ differences, weights * p)
    # Linear smoothing is not generally probability preserving. Refuse a fit
    # outside the probability range instead of silently clipping an overshoot.
    if np.any(fitted < 0) or np.any(fitted > 1):
        raise ValueError("Smoothing produced invalid probabilities; use a bounded fitting model")
    return fitted, weights


@lru_cache(maxsize=1)
def _fitted_curves() -> dict[str, EmpiricalTimingCurve]:
    payload = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
    if payload['schema_version'] != 1:
        raise ValueError("Unknown empirical timing snapshot schema")
    return {
        name: EmpiricalTimingCurve(tuple(data['times']), tuple(data['fitted_rates']), data['reference_rate'])
        for name, data in payload['sides'].items()
    }


def empirical_preplant_factor(dt: float | None, is_attacker: bool, *,
                             strength: float = 1.0, use_realized: bool = True) -> float:
    """Evaluate the saved fit. is_attacker always refers to the KILLER.

    The existing live scorer does not import this function. A missing plant,
    dt<=0, nonfinite dt, or forward-evaluation mode returns neutral 1.0.
    """
    if not math.isfinite(strength) or strength < 0:
        raise ValueError("strength must be finite and nonnegative")
    if not use_realized or dt is None or not math.isfinite(dt) or dt <= 0 or dt > 30:
        return 1.0
    curve = _fitted_curves()['attacker' if is_attacker else 'defender']
    return curve.factor(dt, strength=strength)

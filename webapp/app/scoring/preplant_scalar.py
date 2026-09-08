"""SUPERSEDED 2026-09-08 -- not wired into app.scoring.impact and not the
shipped Part 3 mechanism. See
docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 3's "DECIDED 2026-09-08" section: Part 3 shipped as
app.scoring.preplant_empirical_factor.empirical_preplant_factor (wired
behind enable_preplant_empirical in impact.py) instead of this model-based
scalar. Retained for its fitting-diagnostics value (the logistic model
behind it is real, tested infrastructure), not as dormant production code
-- do not transcribe further constants here or wire this into impact.py
without first reopening that decision.

Original docstring, for context: every constant below is a placeholder that
makes preplant_proximity_scalar return exactly 1.0 for every input, until
scripts/fit_preplant_time_factor.py's reported constants are transcribed
here -- which per the decision above, will not happen."""

from app.scoring.preplant_k_selection import raw_scalar
from app.scoring.preplant_time_model import PreplantFit

# Placeholders -- see module docstring. logit_lift is identically 0 with
# these, so the raw scalar is identically 1.0 regardless of K.
INTERCEPT_ATK = 0.0
SLOPE_ATK = 0.0
INTERCEPT_DEF = 0.0
SLOPE_DEF = 0.0
SHAPE_MID_RATIO = 1.0
K = 1.0
CENTERING_C = 1.0


class _ObsShim:
    """The minimal shape raw_scalar needs from a PreplantKillObservation --
    avoids constructing a full dataclass (with the fields raw_scalar never
    reads) on every scoring call."""

    __slots__ = ("dt", "adv", "is_attacker")

    def __init__(self, dt, adv, is_attacker):
        self.dt = dt
        self.adv = adv
        self.is_attacker = is_attacker


def _current_fit() -> PreplantFit:
    return PreplantFit(
        intercept_atk=INTERCEPT_ATK, slope_atk=SLOPE_ATK,
        intercept_def=INTERCEPT_DEF, slope_def=SLOPE_DEF,
        shape_mid_ratio=SHAPE_MID_RATIO,
        state_effects={}, include_side_interaction=True, n_observations=0,
    )


def preplant_proximity_scalar(dt: float, adv: int, is_attacker: bool, use_realized: bool) -> float:
    """dt is seconds_to_plant (positive before the plant). Returns exactly
    1.0 when use_realized is False -- Part 3's exact leakage gate, since dt
    is only known once the round's plant time (a future event, at kill
    time) is known."""
    if not use_realized:
        return 1.0
    fit = _current_fit()
    raw = raw_scalar(fit, K, _ObsShim(dt, adv, is_attacker))
    return CENTERING_C * max(0.2, min(1.7, raw))

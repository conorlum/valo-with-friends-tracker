"""The Part 3 runtime scalar. DORMANT: every constant below is a
placeholder that makes preplant_proximity_scalar return exactly 1.0 for
every input, until scripts/fit_preplant_time_factor.py has been run against
the real DB and its reported constants are transcribed here. Do not wire
this into impact.py's default path before that happens -- see
docs/superpowers/plans/2026-09-07-preplant-time-factor-part3.md's Global
Constraints (no rescore, no IMPACT_CALCULATION_VERSION bump until Part 3,
Part 4 and the econ swap ship together)."""

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

"""The Part 3 kill-side centring gate and its reported death-side residual
(spec, 'The gate is on the KILL side'; M19, M20)."""

from dataclasses import dataclass

from app.scoring.preplant_k_selection import raw_scalar
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation

DEATH_RESIDUAL_TOLERANCE = 0.02


@dataclass
class CenteringResult:
    c: float
    death_side_residual: float


def _clamped_scalar(fit: PreplantFit, k: float, obs: PreplantKillObservation) -> float:
    return max(0.2, min(1.7, raw_scalar(fit, k, obs)))


def solve_kill_side_centering(
    fit: PreplantFit, k: float, observations: list[PreplantKillObservation],
    kill_order_bonuses: list[float], traded_factors: list[float],
) -> CenteringResult:
    """c solved so mean(kill_order_bonus * c * s) over affected kills equals
    mean(kill_order_bonus * 1) -- i.e. c = sum(K) / sum(K*s). The death-side
    residual, mean(K*T*c*s)/mean(K*T) - 1, is reported against a predeclared
    2% tolerance (DEATH_RESIDUAL_TOLERANCE), never tuned to zero."""
    if len(observations) != len(kill_order_bonuses) or len(observations) != len(traded_factors):
        raise ValueError("observations, kill_order_bonuses and traded_factors must be aligned")

    sum_k = sum(kill_order_bonuses)
    sum_ks = sum(
        kob * _clamped_scalar(fit, k, obs) for obs, kob in zip(observations, kill_order_bonuses)
    )
    c = sum_k / sum_ks if sum_ks else 1.0

    sum_kt = sum(kob * t for kob, t in zip(kill_order_bonuses, traded_factors))
    sum_ktcs = sum(
        kob * t * c * _clamped_scalar(fit, k, obs)
        for obs, kob, t in zip(observations, kill_order_bonuses, traded_factors)
    )
    residual = (sum_ktcs / sum_kt - 1.0) if sum_kt else 0.0

    return CenteringResult(c=c, death_side_residual=residual)

"""SUPERSEDED 2026-09-08 -- part of the model-based Part 3 scalar that was
not shipped; see preplant_scalar.py's module docstring and
docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md's
"DECIDED 2026-09-08" section. The centering GATE ITSELF (solve c so
mean(kill_order_bonus * c * s) matches today's flat baseline) still applies
to whatever ships -- it has not yet been run against
preplant_empirical_factor's strength=3.0 curve and must be before that
curve is activated. This module's specific solve, over the superseded
model's PreplantFit, is retained for its fitting-diagnostics value.

The Part 3 kill-side centring gate and its reported death-side residual
(spec, 'The gate is on the KILL side'; M19, M20)."""

from dataclasses import dataclass

from app.scoring.preplant_empirical_factor import empirical_preplant_factor
from app.scoring.preplant_k_selection import raw_scalar
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation

DEATH_RESIDUAL_TOLERANCE = 0.02

# The pre-centring clamp (predeclared-values.md, "0.2 - 1.7"). Reported as
# effective bounds [c*FLOOR, c*CEIL], per that row's own instruction.
SCALAR_FLOOR = 0.2
SCALAR_CEILING = 1.7

# The empirical curve returns exactly 1.0 past this, so kills beyond it are
# FALLBACK: outside the scored population, and not multiplied by c.
EMPIRICAL_CUTOFF = 30.0


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


@dataclass(frozen=True)
class EmpiricalCenteringResult:
    """c and the reported death-side residual for the SHIPPED empirical curve,
    plus the population split the gate was run over."""

    c: float
    death_side_residual: float
    scored_kills: int
    fallback_kills: int
    scored_kill_order_mass: float
    fallback_kill_order_mass: float

    @property
    def effective_bounds(self) -> tuple[float, float]:
        """[c*FLOOR, c*CEIL] -- what actually scores is c*s, so the predeclared
        0.2-1.7 clamp lands here once centred (predeclared-values.md)."""
        return self.c * SCALAR_FLOOR, self.c * SCALAR_CEILING


def solve_empirical_kill_side_centering(
    observations: list[PreplantKillObservation],
    kill_order_bonuses: list[float],
    traded_factors: list[float],
    *,
    strength: float,
) -> EmpiricalCenteringResult:
    """Run the Part 3 kill-side gate against app.scoring.preplant_empirical_
    factor -- the curve that actually shipped -- rather than the superseded
    PreplantFit that solve_kill_side_centering above scores.

    POPULATION, declared before the solve and reported alongside it:

      AFFECTED  every observation passed in: non-self pre-plant kills in
                non-phantom, non-surrendered planted rounds (M5: 168,432).
      SCORED    the subset with 0 < dt <= EMPIRICAL_CUTOFF. The only kills
                the curve moves, and the only ones c is applied to.
      FALLBACK  the rest. The curve returns exactly 1.0 there; they keep it,
                so they pay exactly what today's flat legacy factor pays.

    c = sum(K)/sum(K*s) over SCORED. This is NOT Part 4's form: Part 4 must
    subtract a fallback population because its legacy baseline is a ramp,
    whereas pre-plant today's factor is a flat 1.0, so the baseline for the
    scored kills is simply sum(K*1). Solving over AFFECTED instead would
    dilute c and then mark down 37,926 kills the curve has no estimate for;
    solving over SCORED preserves the AFFECTED total either way, because the
    fallback kills are unchanged on both sides of the equation.

    The death-side residual, mean(K*T*c*s)/mean(K*T) - 1 over AFFECTED, is
    REPORTED against DEATH_RESIDUAL_TOLERANCE. One constant cannot pin both
    sides; exceeding the tolerance is a finding, never something to tune."""
    if len(observations) != len(kill_order_bonuses) or len(observations) != len(traded_factors):
        raise ValueError("observations, kill_order_bonuses and traded_factors must be aligned")

    scored_kills = fallback_kills = 0
    scored_mass = fallback_mass = 0.0
    sum_ks = 0.0
    for obs, kob in zip(observations, kill_order_bonuses):
        if 0 < obs.dt <= EMPIRICAL_CUTOFF:
            scored_kills += 1
            scored_mass += kob
            # dt is the EXACT fractional plant_time - kill_time that impact.py
            # itself pays; never round or floor it here (Part 4's constant came
            # out 0.7% low from exactly that).
            sum_ks += kob * empirical_preplant_factor(
                obs.dt, obs.is_attacker, strength=strength, use_realized=True
            )
        else:
            fallback_kills += 1
            fallback_mass += kob

    c = scored_mass / sum_ks if sum_ks else 1.0

    sum_kt = 0.0
    sum_ktf = 0.0
    for obs, kob, traded in zip(observations, kill_order_bonuses, traded_factors):
        sum_kt += kob * traded
        if 0 < obs.dt <= EMPIRICAL_CUTOFF:
            sum_ktf += kob * traded * c * empirical_preplant_factor(
                obs.dt, obs.is_attacker, strength=strength, use_realized=True
            )
        else:
            sum_ktf += kob * traded
    residual = (sum_ktf / sum_kt - 1.0) if sum_kt else 0.0

    return EmpiricalCenteringResult(
        c=c, death_side_residual=residual,
        scored_kills=scored_kills, fallback_kills=fallback_kills,
        scored_kill_order_mass=scored_mass, fallback_kill_order_mass=fallback_mass,
    )

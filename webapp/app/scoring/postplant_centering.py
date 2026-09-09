"""Part 4's centring constant, solved on the post-plant population alone.

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4 ("Centring"). Post-plant and pre-plant are NOT centred jointly -- their
populations, factors and leakage properties all differ, and one constant across
both would let either regime absorb the other's drift.

    c = ( SUM_all(K * ramp) - SUM_fallback(K * 1) ) / SUM_supported(K * s)

SUMS, not means. The mean form is wrong whenever the supported and fallback
populations differ in size, because subgroup means cannot be subtracted from an
overall mean without reweighting -- the spec carries a worked counterexample
where it returns a negative constant and would make every affected kill worth
negative Impact.

Fallback cells are held at exactly 1.0 and excluded from the scaled population:
centring them would multiply the neutral fallback by c, contradicting both the
fallback rule and the test that asserts an unsupported cell returns exactly 1.0.
"""

from dataclasses import dataclass

DEATH_RESIDUAL_TOLERANCE = 0.02  # reported, never tuned to zero -- as in Part 3


class DegenerateCentering(Exception):
    """No cell is supported, so every factor is pinned at 1.0 and there is
    nothing for c to scale. Exact preservation is impossible; this is reported
    rather than answered with some c."""


@dataclass
class PostPlantCenteringResult:
    c: float
    death_side_residual: float
    supported_kills: int
    fallback_kills: int


def solve_postplant_centering(
    kill_order_bonuses: list[float],
    ramp_factors: list[float],
    new_factors: list[float],
    supported: list[bool],
    traded_factors: list[float],
    death_ramp_factors: list[float],
) -> PostPlantCenteringResult:
    """All six lists are aligned index-for-index, one entry per post-plant
    pre-resolution kill. `ramp_factors` is today's shipped `_time_factor`
    value for that kill on the KILL side -- the quantity total contribution is
    preserved against. `death_ramp_factors` is the same call with
    `for_death=True`.

    The two are not interchangeable (review finding 4). In plant+38..45 the
    legacy scorer pays 1.75 on the kill side and 0.5 on the death side, so
    measuring the death-side residual against the kill-side ramp is wrong by a
    factor of 3.5 exactly in the window where the two regimes diverge most --
    and that residual exists to report what a player's DEATHS would actually
    move by. It is REPORTED against DEATH_RESIDUAL_TOLERANCE, never solved
    for: only the kill side is pinned exactly, and forcing the death side to
    zero as well would need a second free constant this design does not have.
    """
    lengths = {
        len(kill_order_bonuses), len(ramp_factors), len(new_factors),
        len(supported), len(traded_factors), len(death_ramp_factors),
    }
    if len(lengths) != 1:
        raise ValueError("all per-kill lists must be aligned index-for-index")

    sum_all_ramp = sum(k * r for k, r in zip(kill_order_bonuses, ramp_factors))
    sum_fallback = sum(
        k for k, sup in zip(kill_order_bonuses, supported) if not sup
    )
    sum_supported_scaled = sum(
        k * s for k, s, sup in zip(kill_order_bonuses, new_factors, supported) if sup
    )

    if sum_supported_scaled <= 0:
        raise DegenerateCentering(
            "no supported post-plant cell carries weight: every factor is pinned "
            "at 1.0, so no constant preserves the ramp's total contribution"
        )

    c = (sum_all_ramp - sum_fallback) / sum_supported_scaled

    # Death side: the same events, weighted additionally by _traded_factor.
    # Reported against the 2% tolerance, never solved for.
    #
    # The NEW regime takes the same factor for a kill and for the death it
    # causes -- the event transfers D, the killer is credited it and the
    # victim debited it (spec, Part 4, "Deaths") -- so `c * s` and the
    # fallback 1.0 are side-blind here. The LEGACY regime is not: its baseline
    # has to be the death-side ramp, which is what death_ramp_factors carries.
    sum_kt_new = sum(
        k * t * (c * s if sup else 1.0)
        for k, t, s, sup in zip(kill_order_bonuses, traded_factors, new_factors, supported)
    )
    sum_kt_ramp = sum(
        k * t * r for k, t, r in zip(kill_order_bonuses, traded_factors, death_ramp_factors)
    )
    residual = (sum_kt_new / sum_kt_ramp - 1.0) if sum_kt_ramp else 0.0

    return PostPlantCenteringResult(
        c=c,
        death_side_residual=residual,
        supported_kills=sum(1 for s in supported if s),
        fallback_kills=sum(1 for s in supported if not s),
    )

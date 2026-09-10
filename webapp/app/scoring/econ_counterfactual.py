"""Experimental allocator; never imported by production scoring.

See the 2026-09-10 declaration in docs/superpowers/2026-09-07-predeclared-values.md.
Inputs come from the existing scorer's econ observer, with its event eligibility.
"""
from app.scoring.econ_component import (
    EconAttribution, PlayerRemoval, R, attribute_econ, denial_early,
    is_early_regime, is_late_regime,
)


def attribute_candidate(
    removals: list[PlayerRemoval], pools: dict, own_wealth_next: dict,
    roster_sizes: dict, round_number: int, *, independent_debit: bool = False,
    remove_late_floor: bool = False, use_realized: bool = True,
    is_final_round: bool = False,
) -> dict:
    """Compare an independent resource-cost debit with the transfer allocator.

    ``pools`` must be the already-guarded incumbent pools. Missing own wealth
    abstains on the independent debit without suppressing a supported credit.
    No claim is made that scarcity is caused by the equipment loss.
    """
    if (not use_realized or is_final_round or
            not (is_early_regime(round_number) or is_late_regime(round_number))):
        return {r.player: EconAttribution(0.0, 0.0) for r in removals}

    adjusted_pools = dict(pools)
    if remove_late_floor and is_late_regime(round_number):
        adjusted_pools = {team: max(0.0, value - 0.5) for team, value in pools.items()}
    transfers = attribute_econ(removals, adjusted_pools)
    if not independent_debit:
        return transfers

    out = {}
    for row in removals:
        wealth = own_wealth_next.get(row.team)
        scarcity = (denial_early(wealth, roster_sizes.get(row.team, 0))
                    if wealth is not None else 0.0)
        out[row.player] = EconAttribution(
            credit=transfers[row.player].credit, debit=scarcity * row.lost / R,
        )
    return out

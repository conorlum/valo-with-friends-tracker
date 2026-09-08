"""The separate econ component.

Spec: docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md.
Constants for the early regime's f and g come from
docs/superpowers/2026-09-07-predeclared-values.md ("The early-regime f/g
decision -- FIXED 2026-09-07"), decided before any of this code was written.

THERE IS NO FITTED PARAMETER ANYWHERE IN THIS COMPONENT. That is deliberate:
it is what makes the design immune to the class of null that blocked it. The
one scalar, ECON_SCALE, is a dispersion convention rather than an estimate --
see its own comment.

The component is EX-POST: it reads round N+1 and is therefore exactly 0 under
use_realized=False. That is the leakage gate, and it must stay exact -- the
enemy's round N+1 economy substantially encodes who won round N through the
loss-bonus ladder, so a component built from it, scored against that round's
own outcome, would look excellent for reasons unrelated to whether it measures
anything.
"""

from dataclasses import dataclass

from app.scoring.agent_economy import free_ability_credits

# ---- Predeclared constants (2026-09-07). None of these are fitted. --------
FULL_BUY_THRESHOLD = 4200  # credits/player to afford a full buy
ZERO_AT = 6300             # 1.5 * FULL_BUY_THRESHOLD; wealth at which f reaches 0
FULL_COMMIT = 3900         # R / 5; the value of a full buy
SAVE_FLOOR = 1000          # a sidearm plus light shields
R = 5 * FULL_COMMIT        # 19500, the fixed full-buy reference

# The section 9 ANCHOR: chosen so econ_component's SD over all scored
# player-rounds (realized mode) equals time_impact's current SD. A
# scale-matching rule, not an estimate -- it exists because the evaluation
# harness is structurally blind to this constant (the component is exactly 0
# in the harness's ex-ante replay, and a constant times a constant-zero column
# is zero). It guarantees only that the term is not introduced at an arbitrary
# scale; it does NOT establish comparable influence.
#
# Fitted 2026-09-08 by scripts/fit_econ_scale.py against the full local DB
# (3,124 matches, 659,290 scored player-rounds, realized mode):
#   SD(time_impact) = 179.0723,  SD(econ_component raw) = 0.177665
#   ECON_SCALE = 179.0723 / 0.177665 = 1007.9209
# Re-run that script to reproduce. The magnitude is not arbitrary-looking by
# accident: the component's raw range is 0..1.5 while Impact points run in the
# hundreds, and econ_component persists as an INTEGER, so at the previous
# placeholder of 1.0 every row rounded to zero and the component was invisible.
ECON_SCALE = 1007.9209

PICKUP_BONUS_ENABLED = False

_EARLY_ROUNDS = frozenset({2, 3, 4, 14, 15, 16})
_LATE_ROUNDS = frozenset(list(range(5, 12)) + list(range(17, 24)))


def is_early_regime(round_number: int) -> bool:
    return round_number in _EARLY_ROUNDS


def is_late_regime(round_number: int) -> bool:
    return round_number in _LATE_ROUNDS


def committed_value(loadout: float | None, agent: str | None) -> float:
    """C(v) -- the victim's committed value. The KILLER's loadout does not
    appear (M9). Free signature charges are stripped, since tracker.gg assigns
    them a phantom value."""
    if loadout is None:
        return 0.0
    return max(0.0, loadout - free_ability_credits(agent))


def denial_late(below_full_buy_count: int) -> float:
    """The LOCKED late-regime quantity, 0.5 .. 1.5.

    Note the 0.5 BASELINE: a team that removes any positive committed value
    collects 0.5 however completely the victims re-buy. That is a property of
    the locked formula, not a defect -- and changing it is out of scope.
    """
    return round(0.5 + 0.2 * below_full_buy_count, 2)


def denial_early(team_wealth_next: float, roster_size: int) -> float:
    """f -- the victim team's resource SCARCITY next round, 0 .. 1.5.

    Reads a LEVEL, not a change, and reads TOTAL WEALTH (loadout + remaining)
    rather than a full-buy count: M28 measures the early denial as 97% bank
    and 3% equipment, and a readout keyed on full-buy count reads only the
    equipment account, which is why it inverts early and works late.

    Pooled across the roster because resources are shareable through drops.
    """
    if not roster_size:
        return 0.0
    average = team_wealth_next / roster_size
    return max(0.0, min(1.5, 1.5 * (1 - average / ZERO_AT)))


def commitment(mean_committed_value: float) -> float:
    """g -- how much equipment value was on the table to be denied, 0 .. 1.

    A clamped ramp from "sidearms only" to "full buy", anchored on Valorant's
    price list rather than this dataset's percentiles, sending a fully-saving
    team to EXACTLY zero. That zero is the Q1 behavioural requirement: you can
    only deny what they bought, and ungated the early rounds pool savers with
    buyers and the outcome inverts (M27f, +19.70pp against -0.48pp).
    """
    span = FULL_COMMIT - SAVE_FLOOR
    return max(0.0, min(1.0, (mean_committed_value - SAVE_FLOOR) / span))


@dataclass(frozen=True)
class EconRoundInputs:
    round_number: int
    is_final_round: bool
    # Late regime: how many of the enemy sit below the full-buy line next round.
    enemy_below_full_buy_next: int | None
    # Early regime: the enemy's pooled loadout + remaining next round, and the
    # mean committed value they had on the table THIS round.
    enemy_wealth_next: float | None
    enemy_roster_size: int
    enemy_mean_committed: float | None


def econ_round(inputs: EconRoundInputs, use_realized: bool = True) -> float:
    """The team-round econ quantity.

    Boundary behaviour is decided, not inherited: _realized_econ_swing_factor
    returns a neutral 1.0 for halftime, OT and missing data, which is neutral
    for a MULTIPLICATIVE factor but a positive QUANTITY when reused as denial
    and would award credit where none was earned. Hence explicit zeros here.
    """
    if not use_realized:
        return 0.0  # the leakage gate -- exact
    if inputs.is_final_round:
        return 0.0
    if not inputs.enemy_roster_size:
        return 0.0

    if is_late_regime(inputs.round_number):
        if inputs.enemy_below_full_buy_next is None:
            return 0.0  # abstain: never the 0.5 baseline
        return denial_late(inputs.enemy_below_full_buy_next)

    if is_early_regime(inputs.round_number):
        if inputs.enemy_wealth_next is None or inputs.enemy_mean_committed is None:
            return 0.0
        return (
            commitment(inputs.enemy_mean_committed)
            * denial_early(inputs.enemy_wealth_next, inputs.enemy_roster_size)
        )

    # Pistols (1, 13), halftime (12, 24), overtime, and anything else.
    return 0.0


@dataclass(frozen=True)
class PlayerRemoval:
    player: object
    team: object
    removed: float  # committed value this player removed from the enemy
    lost: float     # this player's own committed value, lost when they died


@dataclass(frozen=True)
class EconAttribution:
    credit: float
    debit: float

    @property
    def value(self) -> float:
        return self.credit - self.debit


def attribute_econ(
    removals: list[PlayerRemoval], econ_round_by_team: dict,
) -> dict:
    """Section 6: a share, but only of the ALLOCATION.

        credit(p) = econ_round(T)   * C_removed_by(p) / removed(T)
        debit(p)  = econ_round(opp) * C_lost_by(p)    / removed(opp)

    The share appears here and ONLY here -- dividing a team-level magnitude
    among the players who produced it, never computing the magnitude itself. A
    per-player SUM would rise with kill count and re-correlate with every
    other component, which is how damage reached 0.869 against the leverage
    aggregate and killed Stage C.

    The removed(T) > 0 guard is not pedantry: removed(T) == 0 arises both when
    a team killed nobody AND when it killed only enemies holding C(v) == 0, and
    in the second case econ_round(T) is strictly positive while every share is
    0. The allocation ABSTAINS; it does not fail. Both sides use the same event
    eligibility, so zero-sum survives the guard rather than being broken by it.
    """
    removed_by_team: dict = {}
    for row in removals:
        removed_by_team[row.team] = removed_by_team.get(row.team, 0.0) + row.removed

    teams = list(econ_round_by_team)
    opponent_of = {}
    for team in teams:
        others = [t for t in teams if t != team]
        opponent_of[team] = others[0] if len(others) == 1 else None

    out = {}
    for row in removals:
        team_removed = removed_by_team.get(row.team, 0.0)
        credit = (
            econ_round_by_team.get(row.team, 0.0) * row.removed / team_removed
            if team_removed > 0 else 0.0
        )

        opponent = opponent_of.get(row.team)
        opponent_removed = removed_by_team.get(opponent, 0.0) if opponent is not None else 0.0
        debit = (
            econ_round_by_team.get(opponent, 0.0) * row.lost / opponent_removed
            if opponent_removed > 0 else 0.0
        )
        out[row.player] = EconAttribution(credit=credit, debit=debit)

    return out


# --- Section 11: the weapon-pickup extension ------------------------------
#
# Confirmed by the project owner 2026-09-08: tracker.gg DOES return a
# distance-to-kill field, which resolves the spec's "Unverified" note on
# whether the response carries location at all.
#
# NOT yet confirmed, and this is why the feature stays disabled: the field's
# exact key and its UNITS. PICKUP_MAX_DISTANCE below is a number in unknown
# units, so it cannot be allowed to reach scoring until one captured match
# settles what it is measuring (scripts/capture_trackergg_state.py).
#
# The readout. The spec withdrew an earlier value-differential rule as not
# computable, and correctly: KillEvent.weapon is the weapon the KILLER used,
# the victim's held weapon at death is not stored, and distance alone
# establishes neither the dropped weapon's value nor whether anyone took it.
# This rule does NOT revive that one. It reads LOADOUT TIER, which is stored,
# on the project owner's reading: a killer who is saving and a victim on a
# full buy means the killer is going to take the gun.
#
# It remains an INFERENCE, not an observation. Nothing in this schema records
# a pickup happening. Proximity plus an economy mismatch is a proxy for it,
# and should be described that way in anything quoting these numbers.
#
# Why it does not double-count the econ component. The component already
# debits the victim for their committed value C(v), but that measures what the
# VICTIM'S TEAM LOST, and a pickup does not change it -- the gun is gone from
# them whether it was destroyed or taken. What a pickup changes is what the
# KILLER'S TEAM GAINED, which nothing else models. Distinct quantity, not a
# second copy of the denial.
#
# Which is also why it is a strict TRANSFER: the same magnitude credited to
# the killer and debited from the victim, so section 7's zero-sum invariant
# survives it. An asymmetric bonus would break a tested invariant.
PICKUP_BONUS = 0.25          # dimensionless, on econ_round's scale; a policy choice
PICKUP_MAX_DISTANCE = 500.0  # UNITS UNCONFIRMED -- see the block comment above
PICKUP_KILLER_TIERS = frozenset({"SAVE"})
PICKUP_VICTIM_TIERS = frozenset({"FULL_BUY"})


def pickup_bonus(
    distance: float | None,
    killer_loadout: float | None = None,
    victim_loadout: float | None = None,
) -> float:
    """The magnitude TRANSFERRED from victim to killer by an inferred pickup.

    Returns 0 when distance is NULL, which is every one of the 487,844
    currently-ingested kill rows -- the feature must be a no-op on all of
    them, and backfilling would mean re-crawling every match at 5-12s pacing
    (~7 hours), which the spec does not propose.

    Callers CREDIT the killer this amount and DEBIT the victim the same
    amount; it is never applied to one side alone.
    """
    if not PICKUP_BONUS_ENABLED:
        return 0.0
    if distance is None or killer_loadout is None or victim_loadout is None:
        return 0.0
    if distance > PICKUP_MAX_DISTANCE:
        return 0.0
    from app.scoring.impact import econ_tier_name

    if econ_tier_name(killer_loadout) not in PICKUP_KILLER_TIERS:
        return 0.0
    if econ_tier_name(victim_loadout) not in PICKUP_VICTIM_TIERS:
        return 0.0
    return PICKUP_BONUS

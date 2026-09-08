"""The separate econ component (spec:
docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md).

Constants f/g are the PREDECLARED ones from
docs/superpowers/2026-09-07-predeclared-values.md, "The early-regime f/g
decision -- FIXED 2026-09-07"; nothing here is fitted.

Every Testing bullet from the spec has a test below, including the ones it
records as previously unsatisfiable and since rescoped.
"""

import pytest

from app.scoring.econ_component import (
    ECON_SCALE,
    FULL_COMMIT,
    SAVE_FLOOR,
    ZERO_AT,
    EconRoundInputs,
    PlayerRemoval,
    attribute_econ,
    commitment,
    committed_value,
    denial_early,
    denial_late,
    econ_round,
    is_early_regime,
    is_late_regime,
    pickup_bonus,
)


# --------------------------------------------------------------------------
# Section 1 -- committed value
# --------------------------------------------------------------------------

def test_committed_value_strips_free_ability_credits_and_never_goes_negative():
    # Free signature charges are phantom value tracker.gg assigns; the
    # killer's loadout never appears. Reyna carries 250 and Yoru 150; Jett
    # carries none, so it is the control rather than a second positive case.
    assert committed_value(3900, "Reyna") == 3900 - 250
    assert committed_value(3900, "Yoru") == 3900 - 150
    assert committed_value(3900, "Jett") == 3900

    # Never negative, even when the free charge exceeds the loadout.
    assert committed_value(100, "Reyna") == 0
    assert committed_value(0, "Reyna") == 0
    assert committed_value(None, "Reyna") == 0


# --------------------------------------------------------------------------
# Predeclared constants
# --------------------------------------------------------------------------

def test_the_predeclared_constants_are_the_declared_ones():
    assert (ZERO_AT, FULL_COMMIT, SAVE_FLOOR) == (6300, 3900, 1000)


def test_denial_early_reference_points_from_the_declaration():
    for avg_wealth, expected in (
        (0, 1.5), (2100, 1.0), (4200, 0.5), (6300, 0.0), (9000, 0.0),
    ):
        assert denial_early(avg_wealth * 5, 5) == pytest.approx(expected, abs=1e-9)
    assert denial_early(6000 * 5, 5) == pytest.approx(0.071, abs=0.001)


def test_commitment_sends_a_fully_saving_team_to_exactly_zero():
    """g's behavioural requirement: a fully-saving team goes to exactly 0."""
    assert commitment(SAVE_FLOOR) == 0.0
    assert commitment(500) == 0.0
    assert commitment(FULL_COMMIT) == 1.0
    assert commitment(9000) == 1.0
    assert commitment((SAVE_FLOOR + FULL_COMMIT) / 2) == pytest.approx(0.5)


def test_denial_late_is_the_locked_formula_with_its_baseline():
    assert denial_late(0) == pytest.approx(0.5)
    assert denial_late(3) == pytest.approx(1.1)
    assert denial_late(5) == pytest.approx(1.5)


# --------------------------------------------------------------------------
# The scope lock, expressed as a test
# --------------------------------------------------------------------------

def test_the_regimes_fire_only_in_their_declared_rounds():
    early = [r for r in range(1, 30) if is_early_regime(r)]
    late = [r for r in range(1, 30) if is_late_regime(r)]

    assert early == [2, 3, 4, 14, 15, 16]
    assert late == [5, 6, 7, 8, 9, 10, 11, 17, 18, 19, 20, 21, 22, 23]


def test_pistols_halftime_overtime_and_final_round_are_exactly_zero():
    for round_number in (1, 13, 12, 24, 25, 27):
        assert econ_round(EconRoundInputs(
            round_number=round_number, is_final_round=False,
            enemy_below_full_buy_next=3, enemy_wealth_next=5000,
            enemy_roster_size=5, enemy_mean_committed=3900,
        )) == 0.0

    # Final round of a match: no next round to deny.
    assert econ_round(EconRoundInputs(
        round_number=7, is_final_round=True, enemy_below_full_buy_next=5,
        enemy_wealth_next=1000, enemy_roster_size=5, enemy_mean_committed=3900,
    )) == 0.0


# --------------------------------------------------------------------------
# Section 5a / 5a-i -- the two regimes behave differently, deliberately
# --------------------------------------------------------------------------

def _inputs(round_number, **kwargs):
    base = dict(
        round_number=round_number, is_final_round=False,
        enemy_below_full_buy_next=0, enemy_wealth_next=5 * 3000,
        enemy_roster_size=5, enemy_mean_committed=3900,
    )
    base.update(kwargs)
    return EconRoundInputs(**base)


def test_early_regime_sends_a_fully_saving_victim_team_to_approximately_zero():
    """The Q1 case -- the single most important behavioural test in the spec,
    and it is scoped to the EARLY regime."""
    value = econ_round(_inputs(3, enemy_mean_committed=SAVE_FLOOR,
                               enemy_wealth_next=5 * 1000))

    assert value == 0.0


def test_late_regime_pays_the_baseline_against_the_same_saving_team():
    """The same assertion in the late regime would fail, correctly: a 5-kill
    round against a saving team pays denial's baseline, not ~0."""
    value = econ_round(_inputs(7, enemy_mean_committed=SAVE_FLOOR,
                               enemy_below_full_buy_next=0))

    assert value == pytest.approx(0.5)


def test_late_regime_saving_team_that_stays_poor_is_paid_more_than_one_that_rebuys():
    """0.5 + 0.2n, not the floor -- the formula behaving correctly."""
    rebuilt = econ_round(_inputs(7, enemy_below_full_buy_next=0))
    stayed_poor = econ_round(_inputs(7, enemy_below_full_buy_next=3))

    assert rebuilt == pytest.approx(0.5)
    assert stayed_poor == pytest.approx(1.1)
    assert stayed_poor > rebuilt


def test_early_regime_bought_in_victim_team_left_below_zero_at_produces_credit():
    """Scoped per the 2026-09-07 amendment: a bought-in team left at or above
    ZERO_AT correctly scores zero even with g = 1, so the assertion is on a
    team left BELOW it."""
    value = econ_round(_inputs(3, enemy_mean_committed=FULL_COMMIT,
                               enemy_wealth_next=5 * 3000))

    assert value > 0.0


def test_a_bought_in_team_left_rich_correctly_scores_zero_early():
    """f reads next-round scarcity, not round-N commitment."""
    value = econ_round(_inputs(3, enemy_mean_committed=FULL_COMMIT,
                               enemy_wealth_next=5 * ZERO_AT))

    assert value == 0.0


def test_the_two_regimes_read_different_quantities():
    """A round-3 and a round-7 team-round with identical enemy next-round
    full-buy counts but different next-round BANK get different early credit
    and identical late credit -- the early denial is 97% bank, and the late
    readout cannot see bank at all."""
    poor_bank = dict(enemy_below_full_buy_next=2, enemy_wealth_next=5 * 2000)
    rich_bank = dict(enemy_below_full_buy_next=2, enemy_wealth_next=5 * 5500)

    assert econ_round(_inputs(3, **poor_bank)) != econ_round(_inputs(3, **rich_bank))
    assert econ_round(_inputs(7, **poor_bank)) == econ_round(_inputs(7, **rich_bank))


def test_two_rounds_with_identical_credits_destroyed_but_different_full_buy_counts_differ():
    """The (B) finding: the late regime keys on the resulting count, not on
    the price of what was destroyed."""
    assert econ_round(_inputs(8, enemy_below_full_buy_next=1)) != econ_round(
        _inputs(8, enemy_below_full_buy_next=4)
    )


# --------------------------------------------------------------------------
# Guards -- applied BEFORE any division
# --------------------------------------------------------------------------

def test_abstention_is_zero_credit_never_the_baseline():
    """n == 0, or a missing round N / N+1 record, sends econ_round to 0 --
    never the 0.5 baseline, and never an average over whichever rows happen
    to exist."""
    assert econ_round(_inputs(7, enemy_roster_size=0)) == 0.0
    assert econ_round(_inputs(7, enemy_below_full_buy_next=None)) == 0.0
    assert econ_round(_inputs(3, enemy_wealth_next=None)) == 0.0
    assert econ_round(_inputs(3, enemy_mean_committed=None)) == 0.0


# --------------------------------------------------------------------------
# Sections 6 and 7 -- attribution and zero-sum
# --------------------------------------------------------------------------

def test_attribution_shares_sum_to_the_team_total_when_removal_is_positive():
    removals = [
        PlayerRemoval(player="A1", team="A", removed=1000.0, lost=0.0),
        PlayerRemoval(player="A2", team="A", removed=3000.0, lost=0.0),
        PlayerRemoval(player="B1", team="B", removed=0.0, lost=2500.0),
        PlayerRemoval(player="B2", team="B", removed=0.0, lost=1500.0),
    ]

    result = attribute_econ(removals, econ_round_by_team={"A": 1.2, "B": 0.0})

    credits = sum(v.credit for v in result.values())
    assert credits == pytest.approx(1.2)


def test_the_component_is_exactly_zero_sum_over_the_round():
    # The fixture has to respect the physical invariant that makes zero-sum
    # true: what one team REMOVED is exactly what the other team LOST. Team A
    # removes 4,000 (= B's total losses) and loses 1,200 (= B's total
    # removals). A fixture violating that is not a counterexample to zero-sum,
    # it is an impossible round.
    removals = [
        PlayerRemoval(player="A1", team="A", removed=1000.0, lost=700.0),
        PlayerRemoval(player="A2", team="A", removed=3000.0, lost=500.0),
        PlayerRemoval(player="B1", team="B", removed=1200.0, lost=2500.0),
        PlayerRemoval(player="B2", team="B", removed=0.0, lost=1500.0),
    ]

    result = attribute_econ(removals, econ_round_by_team={"A": 1.2, "B": 0.9})

    assert sum(v.value for v in result.values()) == pytest.approx(0.0, abs=1e-9)


def test_removed_zero_abstains_from_credit_without_touching_debits():
    """A team that removed nothing still absorbs debits: its credit is 0 while
    its players' debits, scaled by the ENEMY's econ_round, are non-zero."""
    removals = [
        PlayerRemoval(player="A1", team="A", removed=0.0, lost=2000.0),
        PlayerRemoval(player="A2", team="A", removed=0.0, lost=1000.0),
        PlayerRemoval(player="B1", team="B", removed=3000.0, lost=0.0),
    ]

    result = attribute_econ(removals, econ_round_by_team={"A": 1.2, "B": 0.9})

    assert result["A1"].credit == 0.0 and result["A2"].credit == 0.0
    assert result["A1"].debit > 0 and result["A2"].debit > 0
    assert result["B1"].credit > 0


def test_shares_sum_to_zero_not_to_econ_round_when_removal_is_zero():
    """Section 6's indicator: with removed(T) == 0 the shares sum to 0 while
    econ_round(T) may be positive. A test asserting the unconditional
    identity would fail on a legitimate round."""
    removals = [
        PlayerRemoval(player="A1", team="A", removed=0.0, lost=0.0),
        PlayerRemoval(player="B1", team="B", removed=0.0, lost=0.0),
    ]

    result = attribute_econ(removals, econ_round_by_team={"A": 0.5, "B": 0.5})

    assert sum(v.credit for v in result.values()) == 0.0


# --------------------------------------------------------------------------
# Leakage gate and the inert pickup extension
# --------------------------------------------------------------------------

def test_use_realized_false_produces_exactly_zero_for_every_row():
    """The leakage gate. Exact, not approximate: the component reads round
    N+1, which does not exist at scoring time in ex-ante mode."""
    for round_number in (2, 3, 7, 20):
        assert econ_round(_inputs(round_number), use_realized=False) == 0.0


def test_pickup_bonus_is_zero_for_every_row_with_null_distance():
    assert pickup_bonus(None) == 0
    assert pickup_bonus(250.0) == 0  # PICKUP_BONUS_ENABLED is False


def test_econ_scale_is_positive_and_documented():
    assert ECON_SCALE > 0

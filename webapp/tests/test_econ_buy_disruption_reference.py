"""Numerical acceptance cases for the spec's standalone worked calculator."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docs/superpowers/diagnostics"))
import econ_buy_disruption_reference as ref


def budget(*, paid=None, bank=None, losses=None, targets=None):
    return ref.team_budget(targets or [3900]*5, paid if paid is not None else [3900]*5,
                           bank if bank is not None else [3000]*5,
                           losses if losses is not None else [3900, 0, 0, 0, 0])


def test_rich_replacement_retains_only_small_credit_and_debit():
    b = budget()
    assert b.restorable == 0
    assert ref.event_credit(3900, b) == pytest.approx((0.02, 0))
    assert ref.own_debit(3900, b) == pytest.approx((0.02, 0))


def test_surviving_teammates_can_cover_two_players_gaps():
    b = budget(paid=[3900, 3900, 3900, 0, 0], bank=[7800, 0, 0, 0, 0], losses=[0, 0, 0, 3900, 3900])
    assert b.shortfall == b.restorable == 0
    moved = budget(paid=[3900, 3900, 3900, 0, 0], bank=[0, 3900, 3900, 0, 0], losses=[0, 0, 0, 3900, 3900])
    assert moved == b


def test_same_three_losses_have_high_or_low_credit_based_on_funding():
    losses = [3900]*3+[0, 0]
    rich = budget(losses=losses)
    poor = budget(paid=[0, 0, 0, 3900, 3900], bank=[0]*5, losses=losses)
    assert rich.restorable == 0
    assert poor.restorable == 11700
    assert ref.event_credit(3900, poor) == pytest.approx((0.02, 0.2))


def test_cheap_kill_cannot_claim_a_preexisting_full_team_deficit():
    b = budget(paid=[0]*5, bank=[0]*5, losses=[500, 0, 0, 0, 0])
    assert b.shortfall == 19500
    assert b.restorable == 500
    assert sum(ref.event_credit(500, b)) == pytest.approx(1.1 * 500 / 19500)


def test_no_loss_gives_no_restorable_shortfall_or_credit():
    b = budget(paid=[0]*5, bank=[0]*5, losses=[0]*5)
    assert b.restorable == 0
    assert ref.event_credit(0, b) == (0, 0)


def test_expensive_equipment_is_not_spendable_cash_for_a_teammate():
    b = budget(paid=[10000, 3900, 3900, 3900, 0], bank=[0]*5)
    assert b.funding == 15600
    assert b.shortfall == 3900


def test_bonus_carryover_target_is_different_from_standard_target():
    assert ref.buy_targets([3200, 3000, 100, 5000, 2700], 2, True) == [3200, 3000, 1000, 3900, 2700]
    assert ref.buy_targets([3200]*5, 2, False) == [3900]*5
    assert ref.buy_targets([3200]*5, 3, True) == [3900]*5


def test_repeated_death_counts_one_starting_kit_in_chronological_order():
    events = [dict(id=2, victim=1, time=20), dict(id=1, victim=1, time=10), dict(id=3, victim=2, time=21)]
    assert ref.first_loss_exposures({1: 3900, 2: 500}, events) == {1: 3900, 2: 0, 3: 500}


def test_non_enemy_loss_gets_debit_without_inventing_credit():
    b = budget(paid=[0]*5, bank=[0]*5)
    assert ref.event_credit(3900, b, enemy=False) == (0, 0)
    base, scarcity = ref.own_debit(3900, b)
    assert base == pytest.approx(0.02)
    assert scarcity == pytest.approx(0.3)


def test_event_shares_reconcile_even_when_other_losses_are_non_enemy():
    b = budget(paid=[0]*5, bank=[0]*5, losses=[3900, 1950, 1950, 0, 0])
    # The third loss is environmental; enemies only receive 3/4 of the pool.
    a, c = ref.event_credit(3900, b), ref.event_credit(1950, b)
    assert sum(a)+sum(c) == pytest.approx(1.1 * 5850 / 19500)
    assert a[1] == pytest.approx(2*c[1])


@pytest.mark.parametrize("bad", [None, -1, float("nan"), float("inf")])
def test_bad_data_is_not_imputed_as_poverty(bad):
    with pytest.raises(ValueError):
        budget(bank=[bad, 0, 0, 0, 0])


def test_roster_must_be_complete():
    with pytest.raises(ValueError):
        ref.team_budget([3900]*4, [3900]*4, [0]*4, [0]*4)


def test_coordinated_weak_buy_uses_observed_downgrade_after_funding_gate():
    b = budget(paid=[600, 1900, 450, 700, 4500], bank=[1600, 1800, 1950, 2650, 2200],
               losses=[4850, 4000, 4000, 3250, 0])
    assert b.shortfall == b.restorable == 1750
    # 3300 + 2000 + 3450 + 3200 + 0 = 11950 of observed equipment gap.
    expected_pool = 11950 * (1750 / 3900)
    assert ref.event_credit(4850, b)[1] == pytest.approx(expected_pool / 19500 * 4850 / 16100)


def test_tiny_funding_gap_does_not_activate_a_whole_save_round_bonus():
    b = budget(paid=[2900]*5, bank=[999]*5, losses=[3900]*5)
    assert b.shortfall == 5
    expected_pool = 5000 * 5 / 3900
    assert ref.event_credit(3900, b)[1] == pytest.approx(expected_pool / 19500 * 0.2)


def test_absorbed_death_costs_thirty_percent_of_small_damage_value():
    b = budget()
    assert ref.buy_linked_debit(3900, b) == pytest.approx((0.006, 0))


def test_disrupted_death_costs_eighty_percent_of_full_damage_value():
    b = budget(paid=[0, 0, 0, 3900, 3900], bank=[0]*5, losses=[3900]*3+[0, 0])
    assert ref.buy_linked_debit(3900, b) == pytest.approx((0.016, 0.16))


def test_low_reserves_do_not_charge_extra_if_next_buy_is_funded():
    b = budget(paid=[3900]*5, bank=[0]*5)
    assert b.scarcity > 0
    assert ref.buy_linked_debit(3900, b) == pytest.approx((0.006, 0))


def test_non_enemy_death_uses_same_buy_damage_basis_without_enemy_credit():
    b = budget(paid=[0]*5, bank=[0]*5, losses=[500, 0, 0, 0, 0])
    assert ref.event_credit(500, b, enemy=False) == (0, 0)
    assert sum(ref.buy_linked_debit(500, b)) == pytest.approx(0.8*1.1*500/19500)


def test_no_second_kit_no_buy_linked_penalty():
    b = budget(paid=[0]*5, bank=[0]*5)
    assert ref.buy_linked_debit(0, b) == (0, 0)

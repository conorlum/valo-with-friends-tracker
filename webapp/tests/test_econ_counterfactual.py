"""Behavioral examples for the isolated econ experiment, not production changes."""
import pytest

from app.scoring.econ_component import PlayerRemoval, R, attribute_econ, denial_early
from app.scoring.econ_counterfactual import attribute_candidate


ROWS = [PlayerRemoval("a", "A", 800, 4000), PlayerRemoval("b", "B", 4000, 800)]


def score(*, rows=ROWS, pools=None, wealth=None, round_number=3, **kwargs):
    return attribute_candidate(
        rows, pools or {"A": 0.05, "B": 0.02},
        wealth if wealth is not None else {"A": 0, "B": 0},
        {"A": 5, "B": 5}, round_number, **kwargs,
    )


def test_incumbent_is_reproduced_and_zero_sum():
    pools = {"A": 0.2, "B": 0.8}
    result = score(pools=pools)
    assert result == attribute_econ(ROWS, pools)
    assert sum(x.value for x in result.values()) == pytest.approx(0)


def test_save_gate_cannot_erase_own_loss_when_resources_are_low():
    pools = {"A": 0.05, "B": 0}
    result = score(pools=pools, wealth={"A": 5 * 4750, "B": 0}, independent_debit=True)
    assert result["a"].credit == pytest.approx(0.05)
    assert result["a"].debit == pytest.approx(denial_early(5 * 4750, 5) * 4000 / R)
    assert result["a"].debit > 0


def test_both_teams_can_have_negative_net_econ():
    result = score(independent_debit=True)
    assert result["a"].value < 0
    assert result["b"].value < 0


def test_both_teams_can_have_positive_net_econ_when_debit_is_zero():
    result = score(wealth={"A": 40000, "B": 40000}, independent_debit=True)
    assert result["a"].debit == 0
    assert result["b"].debit == 0
    assert result["a"].value > 0 and result["b"].value > 0


def test_own_debit_does_not_depend_on_opponent_gain():
    first = score(pools={"A": 0.1, "B": 0.1}, independent_debit=True)
    second = score(pools={"A": 0.1, "B": 1.5}, independent_debit=True)
    assert first["a"].debit == second["a"].debit


def test_more_own_loss_costs_more_at_fixed_scarcity():
    small = score(independent_debit=True)
    larger = score(rows=[PlayerRemoval("a", "A", 800, 8000), ROWS[1]], independent_debit=True)
    assert larger["a"].debit == pytest.approx(2 * small["a"].debit)


def test_late_floor_removal_changes_both_transfer_sides():
    result = score(pools={"A": 0.5, "B": 1.1}, round_number=7, remove_late_floor=True)
    assert result["a"].credit == 0
    assert result["a"].debit == pytest.approx(0.6)
    assert result["b"].credit == pytest.approx(0.6)
    assert result["b"].debit == 0
    assert sum(x.value for x in result.values()) == pytest.approx(0)


def test_no_floor_leaves_early_credit_unchanged():
    assert score(remove_late_floor=True) == score()


def test_factorial_combination_changes_credit_without_changing_independent_debit():
    args = dict(pools={"A": 0.5, "B": 1.1}, round_number=7, independent_debit=True)
    first = score(**args)
    second = score(**args, remove_late_floor=True)
    assert first["a"].debit == second["a"].debit
    assert second["a"].credit == 0


def test_missing_own_next_roster_abstains_on_harm_without_imputing_poverty():
    result = score(wealth={"A": None, "B": 0}, independent_debit=True)
    assert result["a"].debit == 0
    assert result["a"].credit == pytest.approx(0.05)


@pytest.mark.parametrize("round_number", [1, 12, 13, 24, 25, 30])
def test_round_boundaries_abstain(round_number):
    result = score(round_number=round_number, independent_debit=True)
    assert all(x.credit == 0 and x.debit == 0 for x in result.values())


@pytest.mark.parametrize("options", [{"use_realized": False}, {"is_final_round": True}])
def test_ex_ante_and_final_round_abstain(options):
    result = score(independent_debit=True, **options)
    assert all(x.credit == 0 and x.debit == 0 for x in result.values())


def test_no_equipment_lost_has_no_debit():
    result = score(rows=[PlayerRemoval("a", "A", 800, 0), ROWS[1]], independent_debit=True)
    assert result["a"].debit == 0

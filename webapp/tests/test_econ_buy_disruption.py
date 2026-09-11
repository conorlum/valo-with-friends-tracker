"""The production buy-disruption economy calculator, as a pure function.

Spec: docs/superpowers/specs/2026-09-10-econ-buy-disruption-implementation.md,
sections 2-6 and 12 (the owner's 30%/80% death penalty, which supersedes the
wealth-based debit of section 7). The wealth debit survives only as the named
historical comparator `buy_disruption_v2_wealth`.

Every expected value below is hand-derived from the spec's formulas, never
read back from the implementation:

    background_e = 0.10 * v_e / 19500
    disruption_e = severity_pool_T / 19500 * v_e / L_T
    rate_T       = 0.80 if severity_pool_T > 0 else 0.30
    debit_i      = rate_T * (0.10 * lost_i / 19500 + severity_pool_T / 19500 * lost_i / L_T)
"""
import math

import pytest

from app.scoring import econ_buy_disruption as bd

A, B = "TEAM_1", "TEAM_2"
A_IDS, B_IDS = (1, 2, 3, 4, 5), (6, 7, 8, 9, 10)
ALL_IDS = A_IDS + B_IDS
R = 19500.0


def _players(*, loadout=None, next_loadout=None, next_bank=None, free=None,
             missing_current=(), missing_next=(), drop=()):
    loadout = {**{i: 3900 for i in ALL_IDS}, **(loadout or {})}
    next_loadout = {**{i: 3900 for i in ALL_IDS}, **(next_loadout or {})}
    next_bank = {**{i: 3000 for i in ALL_IDS}, **(next_bank or {})}
    free = free or {}
    return tuple(
        bd.PlayerEconomy(
            match_player_id=i, team=A if i in A_IDS else B,
            free_ability_credits=free.get(i, 0),
            loadout=loadout[i], next_loadout=next_loadout[i], next_remaining=next_bank[i],
            has_current_stats=i not in missing_current, has_next_stats=i not in missing_next,
        )
        for i in ALL_IDS if i not in drop
    )


def ev(event_id, t, killer, victim):
    return bd.EconEvent(event_id=event_id, time_seconds=t, killer_id=killer, victim_id=victim)


def _inputs(*, round_number=7, last_round_number=24, events=(),
            outcome="Team A Elimination Win", next_outcome="Team A Elimination Win",
            pistol_outcome="Team A Elimination Win", has_next_round=True,
            use_realized=True, players=None, **player_kw):
    return bd.RoundEconInputs(
        round_number=round_number, last_round_number=last_round_number,
        team_a=A, team_b=B, players=players if players is not None else _players(**player_kw),
        events=tuple(events), outcome=outcome, next_outcome=next_outcome,
        pistol_outcome=pistol_outcome, has_next_round=has_next_round,
        use_realized=use_realized,
    )


def score(model=bd.MODEL_V2_30_80, **kw):
    return bd.score_round(_inputs(**kw), model)


# Team B cannot fund its next buy at all: every next loadout and bank is 0.
BROKE_B = dict(next_loadout={i: 0 for i in B_IDS}, next_bank={i: 0 for i in B_IDS})


# ---- 1. absorbed losses ------------------------------------------------------

def test_rich_team_absorbs_a_lost_kit_small_credit_thirty_percent_debit():
    result = score(events=[ev(1, 10.0, 1, 6)])
    assert result.abstention is None
    assert result.teams[B].budget.severity_pool == 0
    assert result.teams[B].penalty_rate == 0.30
    assert result.players[1].background_credit == pytest.approx(0.02)
    assert result.players[1].disruption_credit == 0
    assert result.players[6].debit == pytest.approx(0.30 * 0.02)
    assert result.players[6].raw_net == pytest.approx(-0.006)


def test_low_reserves_with_a_funded_next_buy_charge_no_scarcity():
    # Bank 0 everywhere, but every next paid loadout reaches the 3900 target:
    # the historical wealth debit would charge scarcity here; 30/80 must not.
    result = score(events=[ev(1, 10.0, 1, 6)], next_bank={i: 0 for i in B_IDS})
    assert result.teams[B].budget.shortfall == 0
    assert result.players[6].debit == pytest.approx(0.006)
    assert result.players[6].scarcity_debit == 0


def test_teammate_funded_replacement_is_absorbed_and_bank_location_is_irrelevant():
    losses = [ev(1, 10.0, 1, 9), ev(2, 11.0, 2, 10)]
    funded = score(events=losses, next_loadout={9: 0, 10: 0},
                   next_bank={6: 7800, 7: 0, 8: 0, 9: 0, 10: 0})
    moved = score(events=losses, next_loadout={9: 0, 10: 0},
                  next_bank={6: 0, 7: 3900, 8: 3900, 9: 0, 10: 0})
    assert funded.teams[B].budget.shortfall == 0
    assert funded.teams[B].penalty_rate == 0.30
    assert funded.teams[B].budget == moved.teams[B].budget
    for pid in ALL_IDS:
        assert funded.players[pid].raw_net == pytest.approx(moved.players[pid].raw_net)


# ---- 2. constrained buys -------------------------------------------------------

def test_constrained_next_buy_charges_eighty_percent_of_the_full_damage_value():
    result = score(events=[ev(1, 10.0, 1, 6), ev(2, 20.0, 2, 7), ev(3, 30.0, 3, 8)],
                   next_loadout={6: 0, 7: 0, 8: 0}, next_bank={i: 0 for i in B_IDS})
    budget = result.teams[B].budget
    assert budget.target == 19500
    assert budget.funding == 7800
    assert budget.shortfall == 11700
    assert budget.observed_gap == 11700
    assert budget.activation == 1
    assert budget.severity_pool == 11700
    assert result.teams[B].penalty_rate == 0.80
    for killer, victim in ((1, 6), (2, 7), (3, 8)):
        assert result.players[killer].background_credit == pytest.approx(0.02)
        assert result.players[killer].disruption_credit == pytest.approx(0.2)
        assert result.players[victim].debit == pytest.approx(0.80 * 0.22)
        # Signed: never floored at zero.
        assert result.players[victim].raw_net == pytest.approx(-0.176)


def test_same_losses_score_larger_when_the_next_buy_is_unfunded():
    events = [ev(1, 10.0, 1, 6), ev(2, 20.0, 2, 7), ev(3, 30.0, 3, 8)]
    rich = score(events=events)
    poor = score(events=events, next_loadout={6: 0, 7: 0, 8: 0}, next_bank={i: 0 for i in B_IDS})
    assert rich.players[1].credit == pytest.approx(0.02)
    assert poor.players[1].credit == pytest.approx(0.22)


def test_cheap_loss_cannot_claim_a_preexisting_full_team_shortfall():
    result = score(events=[ev(1, 10.0, 1, 6)], loadout={6: 500, 7: 0, 8: 0, 9: 0, 10: 0},
                   **BROKE_B)
    budget = result.teams[B].budget
    assert budget.shortfall == 19500
    assert budget.lost == 500
    assert budget.severity_pool == 500
    assert result.players[1].credit == pytest.approx(1.1 * 500 / R)
    assert result.players[6].debit == pytest.approx(0.8 * 1.1 * 500 / R)


def test_tiny_funding_gap_keeps_the_binary_rate_step():
    events = [ev(k, 10.0 + k, k, 5 + k) for k in range(1, 6)]
    result = score(events=events, next_loadout={i: 2900 for i in B_IDS},
                   next_bank={i: 999 for i in B_IDS})
    budget = result.teams[B].budget
    assert budget.shortfall == 5
    pool = 5000 * 5 / 3900
    assert budget.severity_pool == pytest.approx(pool)
    assert result.teams[B].penalty_rate == 0.80  # not smoothed
    expected_basis = 0.1 * 3900 / R + pool / R * 3900 / 19500
    assert result.players[6].debit == pytest.approx(0.80 * expected_basis)


def test_expensive_surplus_equipment_is_not_cash_for_a_teammate():
    budget = bd.team_budget([3900] * 5, [10000, 3900, 3900, 3900, 0], [0] * 5, [0] * 5)
    assert budget.funding == 15600
    assert budget.shortfall == 3900


# ---- 3. zero loss, duplicates, ordering -----------------------------------------

def test_zero_paid_loss_has_no_economic_exposure():
    # 150 of raw loadout is exactly the agent's free signature charge.
    result = score(events=[ev(1, 10.0, 1, 6)], loadout={6: 150}, free={6: 150}, **BROKE_B)
    assert result.events[0].exposure == 0
    assert result.players[1].credit == 0
    assert result.players[6].debit == 0


def test_repeated_death_counts_one_kit_in_time_then_id_order():
    events = [ev(20, 20.0, 1, 6), ev(10, 10.0, 2, 6), ev(11, 10.0, 3, 7),
              ev(5, 15.0, 4, 8), ev(4, 15.0, 5, 8)]
    result = score(events=events, loadout={6: 3900, 7: 2000, 8: 1000})
    assert [(e.event_id, e.exposure) for e in result.events] == [
        (10, 3900), (11, 2000), (4, 1000), (5, 0), (20, 0)]
    assert result.players[6].lost == 3900
    assert result.players[8].lost == 1000
    assert result.players[1].credit == 0  # the second death of 6 has no new kit


def test_permuting_distinct_deaths_preserves_economic_shares():
    first = score(events=[ev(1, 10.0, 1, 6), ev(2, 20.0, 2, 7)], loadout={6: 3900, 7: 1300}, **BROKE_B)
    second = score(events=[ev(1, 10.0, 2, 7), ev(2, 20.0, 1, 6)], loadout={6: 3900, 7: 1300}, **BROKE_B)
    for pid in (1, 2, 6, 7):
        assert first.players[pid].raw_net == pytest.approx(second.players[pid].raw_net)
    assert first.players[1].disruption_credit / first.players[2].disruption_credit == pytest.approx(3.0)


# ---- 4. non-enemy deaths --------------------------------------------------------

def _mixed_deaths(model=bd.MODEL_V2_30_80):
    events = [ev(1, 10.0, 1, 6),       # enemy
              ev(2, 20.0, 7, 7),       # self
              ev(3, 30.0, 8, 9),       # team kill
              ev(4, 40.0, None, 10)]   # environmental / unknown killer
    return score(model, events=events, loadout={6: 3900, 7: 1950, 9: 1950, 10: 1950}, **BROKE_B)


def test_non_enemy_deaths_create_own_debit_but_no_enemy_credit():
    result = _mixed_deaths()
    assert [e.kind for e in result.events] == ["enemy", "self", "team", "unknown_killer"]
    budget = result.teams[B].budget
    assert budget.lost == 9750
    assert budget.severity_pool == 9750
    # Only the enemy event is credited, and only its own share of the pool.
    assert result.players[1].credit == pytest.approx(0.02 + 0.2)
    assert result.players[7].credit == 0
    assert result.players[8].credit == 0
    assert sum(result.players[p].credit for p in A_IDS) == pytest.approx(0.22)
    for victim in (7, 9, 10):
        assert result.players[victim].debit == pytest.approx(0.8 * (0.01 + 0.1))
    assert result.events[3].killer_id is None
    assert result.events[3].background_credit == 0
    assert result.events[3].victim_debit == pytest.approx(0.088)


def test_killer_outside_the_roster_gets_no_credit_and_is_flagged():
    result = score(events=[ev(7, 10.0, 99, 6)])
    assert result.events[0].kind == "unknown_killer"
    assert all(result.players[p].credit == 0 for p in ALL_IDS)
    assert result.players[6].debit == pytest.approx(0.006)
    assert any("killer_not_in_roster" in flag and "7" in flag for flag in result.data_quality)


# ---- 5. carryover targets -------------------------------------------------------

B_CARRY_LOADOUT = {6: 2000, 7: 500, 8: 5000, 9: 3000, 10: 150}


@pytest.mark.parametrize("round_two_outcome", ["Team B Elimination Win", "Team A Elimination Win"])
def test_pistol_winner_round_two_targets_do_not_drop_when_it_loses(round_two_outcome):
    result = score(round_number=2, pistol_outcome="Team B Defuse Win", outcome=round_two_outcome,
                   loadout=B_CARRY_LOADOUT, free={10: 150})
    assert result.teams[B].targets == (2000, 1000, 3900, 3000, 1000)
    assert result.teams[B].carryover_targets is True
    assert result.teams[A].targets == (3900,) * 5
    assert result.teams[B].pistol_winner is True
    assert result.teams[B].round_winner is (round_two_outcome.startswith("Team B"))


def test_second_half_carryover_uses_round_thirteen_and_other_rounds_use_3900():
    second_half = score(round_number=14, pistol_outcome="Team B Defuse Win", loadout=B_CARRY_LOADOUT,
                        free={10: 150})
    assert second_half.teams[B].targets == (2000, 1000, 3900, 3000, 1000)
    third = score(round_number=3, pistol_outcome="Team B Defuse Win", loadout=B_CARRY_LOADOUT)
    assert third.teams[B].targets == (3900,) * 5


def test_carryover_target_can_absorb_a_loss_a_rifle_target_would_call_constrained():
    common = dict(round_number=2, outcome="Team A Elimination Win", events=[ev(1, 10.0, 1, 6)],
                  loadout={i: 2000 for i in B_IDS}, next_loadout={i: 2000 for i in B_IDS},
                  next_bank={i: 0 for i in B_IDS})
    carry = score(pistol_outcome="Team B Elimination Win", **common)
    rifle = score(pistol_outcome="Team A Elimination Win", **common)
    assert carry.teams[B].budget.shortfall == 0
    assert carry.teams[B].penalty_rate == 0.30
    assert rifle.teams[B].budget.shortfall == 9500
    assert rifle.teams[B].penalty_rate == 0.80


# ---- 6. boundaries and abstentions ------------------------------------------------

@pytest.mark.parametrize("round_number", [1, 12, 13, 24, 25, 30])
def test_pistol_half_and_overtime_rounds_abstain(round_number):
    result = score(round_number=round_number, last_round_number=40, events=[ev(1, 10.0, 1, 6)])
    assert result.abstention == "pistol_half_or_ot_boundary"
    assert result.raw_net_by_player() == {}


@pytest.mark.parametrize("kwargs, reason", [
    (dict(round_number=19, last_round_number=19), "final_round"),
    (dict(has_next_round=False, next_outcome=None), "missing_next_round"),
    (dict(outcome="Team B Surrendered Win"), "surrender"),
    (dict(next_outcome="Team B Surrendered Win"), "surrender"),
    (dict(use_realized=False), "ex_ante"),
    (dict(pistol_outcome=None), "unknown_pistol_winner"),
    (dict(drop=(10,)), "incomplete_roster"),
    (dict(missing_current=(3,)), "incomplete_stats"),
    (dict(missing_next=(8,)), "incomplete_stats"),
])
def test_boundary_and_missing_history_cases_abstain_with_reasons(kwargs, reason):
    result = score(events=[ev(1, 10.0, 1, 6)], **kwargs)
    assert result.abstention == reason
    assert result.raw_net_by_player() == {}


@pytest.mark.parametrize("field", ["loadout", "next_loadout", "next_bank"])
@pytest.mark.parametrize("bad", [None, -1, float("nan"), float("inf")])
def test_invalid_economy_values_abstain_rather_than_imputing_poverty(field, bad):
    result = score(events=[ev(1, 10.0, 1, 6)], **{field: {9: bad}})
    assert result.abstention == "invalid_economy_data"
    assert result.raw_net_by_player() == {}


@pytest.mark.parametrize("victim", [None, 99])
def test_unknown_victim_abstains_and_names_the_event(victim):
    result = score(events=[ev(1, 10.0, 1, 6), ev(4242, 20.0, 2, victim)])
    assert result.abstention == "unknown_victim"
    assert "4242" in result.abstention_detail


def test_missing_event_identity_abstains():
    result = score(events=[ev(None, 10.0, 1, 6)])
    assert result.abstention == "invalid_event_ids"


# ---- 7. identities, comparator, isolation -----------------------------------------

def test_credit_and_debit_identities_reconcile():
    result = _mixed_deaths()
    events_by_team = {A: 0.0, B: 0.0}
    for e in result.events:
        if e.kind == "enemy":
            events_by_team[e.killer_team] += e.background_credit + e.disruption_credit
    for team, ids in ((A, A_IDS), (B, B_IDS)):
        assert sum(result.players[p].credit for p in ids) == pytest.approx(events_by_team[team])
        assert result.teams[team].credit == pytest.approx(events_by_team[team])
    budget = result.teams[B].budget
    expected_team_debit = 0.8 * (0.10 * budget.lost / R + budget.severity_pool / R)
    assert sum(result.players[p].debit for p in B_IDS) == pytest.approx(expected_team_debit)
    assert result.teams[B].debit == pytest.approx(expected_team_debit)
    for pid in ALL_IDS:
        assert sum(e.victim_debit for e in result.events if e.victim_id == pid) == pytest.approx(
            result.players[pid].debit)


def test_wealth_comparator_keeps_credit_and_uses_the_historical_scarcity_debit():
    kw = dict(events=[ev(1, 10.0, 1, 6), ev(2, 20.0, 2, 7), ev(3, 30.0, 3, 8)],
              next_loadout={6: 0, 7: 0, 8: 0}, next_bank={i: 0 for i in B_IDS})
    wealth = score(bd.MODEL_V2_WEALTH, **kw)
    thirty = score(bd.MODEL_V2_30_80, **kw)
    scarcity = 1.5 * (1 - 7800 / 31500)
    assert wealth.teams[B].budget.scarcity == pytest.approx(scarcity)
    assert wealth.players[6].background_debit == pytest.approx(0.02)
    assert wealth.players[6].scarcity_debit == pytest.approx(scarcity * 3900 / R)
    assert wealth.players[6].disruption_debit == 0
    assert wealth.teams[B].penalty_rate is None
    for pid in ALL_IDS:
        assert wealth.players[pid].credit == thirty.players[pid].credit
    assert [(e.background_credit, e.disruption_credit) for e in wealth.events] == [
        (e.background_credit, e.disruption_credit) for e in thirty.events]


def test_one_teams_extra_loss_cannot_shift_the_other_teams_nets():
    base = score(events=[ev(1, 10.0, 1, 6)], **BROKE_B)
    extra = score(events=[ev(1, 10.0, 1, 6), ev(2, 50.0, None, 5)], **BROKE_B)
    assert extra.players[5].raw_net == pytest.approx(-0.3 * 0.02)
    for pid in ALL_IDS:
        if pid != 5:
            assert extra.players[pid].raw_net == base.players[pid].raw_net


def test_result_carries_model_identity_and_audit_version():
    result = score(events=[ev(1, 10.0, 1, 6)])
    assert result.model == bd.MODEL_V2_30_80
    assert result.audit_version == bd.AUDIT_VERSION
    assert all(math.isfinite(v) for v in result.raw_net_by_player().values())


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        bd.score_round(_inputs(), "buy_disruption_v3")

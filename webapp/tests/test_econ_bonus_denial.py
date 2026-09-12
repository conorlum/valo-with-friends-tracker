"""The round 2/14 bonus-round denial model (spec 2026-09-12).

Hand-derived values (R = 19500):
    qualifying credit = V * 1.10 * net_denied / R, victim debit = 0.8 * that
    non-qualifying: credit 0.10 * paid / R, debit 0.30 * 0.10 * paid / R
Team A (ids 1-5, Jett, utility 550) wins the pistol round in every fixture.
"""
import pytest

from app.scoring import econ_buy_disruption as bd

A, B = "TEAM_1", "TEAM_2"
A_IDS, B_IDS = (1, 2, 3, 4, 5), (6, 7, 8, 9, 10)
ALL = A_IDS + B_IDS
R = 19500.0
NEW = bd.MODEL_V2_30_80_BONUS_DENIAL
WIN_A, WIN_B = "Team A Elimination Win", "Team B Elimination Win"


def players(*, loadout=None, next_loadout=None, next_bank=None, remaining=None, kills=None,
            deaths=None, next_deaths=None, agent=None, omit=()):
    loadout = {**{i: 3900 for i in ALL}, **(loadout or {})}
    next_loadout = {**{i: 3900 for i in ALL}, **(next_loadout or {})}
    next_bank = {**{i: 1000 for i in ALL}, **(next_bank or {})}
    remaining = {**{i: 1000 for i in ALL}, **(remaining or {})}
    kills, deaths, next_deaths = kills or {}, deaths or {}, next_deaths or {}
    agent = {**{i: "Jett" if i in A_IDS else "Sova" for i in ALL}, **(agent or {})}
    out = []
    for i in ALL:
        fields = dict(agent=agent[i], remaining=remaining[i], kills=kills.get(i, 0),
                      deaths=deaths.get(i, 0), next_deaths=next_deaths.get(i, 0))
        for field_name, pid in omit:
            if pid == i:
                fields[field_name] = None
        out.append(bd.PlayerEconomy(
            match_player_id=i, team=A if i in A_IDS else B, free_ability_credits=0,
            loadout=loadout[i], next_loadout=next_loadout[i], next_remaining=next_bank[i], **fields))
    return tuple(out)


def ev(event_id, t, killer, victim, weapon="Vandal"):
    return bd.EconEvent(event_id=event_id, time_seconds=t, killer_id=killer, victim_id=victim, weapon=weapon)


def inputs(*, round_number=2, events=(), next_events=(), outcome=WIN_A, planted=False,
           attacking_team=A, reward=None, player_kw=None, pistol=WIN_A, **over):
    kw = dict(
        round_number=round_number, last_round_number=24, team_a=A, team_b=B,
        players=players(**(player_kw or {})), events=tuple(events), outcome=outcome,
        next_outcome=WIN_A, pistol_outcome=pistol, has_next_round=True,
        next_events=tuple(next_events) if next_events is not None else None,
        planted=planted, attacking_team=attacking_team,
        next_round_reward=reward if reward is not None else {A: 3000 if outcome == WIN_A else 1900, B: 1900},
    )
    kw.update(over)
    return bd.RoundEconInputs(**kw)


def score(model=NEW, **kw):
    return bd.score_round(inputs(**kw), model)


# ---- identity and parity outside the branch ------------------------------------------------

def test_new_model_is_a_buy_disruption_model_with_audit_version_2():
    assert NEW in bd.BUY_DISRUPTION_MODELS
    assert bd.audit_version_for(NEW) == 2
    assert bd.audit_version_for(bd.MODEL_V2_30_80) == 1
    assert (bd.BONUS_DENIAL_THRESHOLD, bd.SWING_VALUE_PER_CREDIT, bd.BONUS_WON_FACTOR,
            bd.BONUS_LOST_FACTOR) == (1500.0, 1.10, 0.8, 1.0)


@pytest.mark.parametrize("rn", [3, 7, 11, 15, 23])
def test_rounds_outside_2_and_14_score_exactly_as_30_80(rn):
    events = [ev(1, 10.0, 6, 1), ev(2, 20.0, 1, 7), ev(3, 30.0, None, 2)]
    kw = dict(round_number=rn, events=events, player_kw=dict(next_loadout={1: 0, 2: 0}, next_bank={1: 0, 2: 0}))
    old, new = score(bd.MODEL_V2_30_80, **kw), score(NEW, **kw)
    assert new.audit_version == 2 and old.audit_version == 1
    assert new.raw_net_by_player() == old.raw_net_by_player()
    assert [(e.credit, e.victim_debit) for e in new.events] == [(e.credit, e.victim_debit) for e in old.events]


def test_rounds_outside_the_branch_do_not_require_the_new_inputs():
    result = bd.score_round(inputs(round_number=7, events=[ev(1, 10.0, 6, 1)], next_events=None,
                                   planted=None, attacking_team=None, reward={}), NEW)
    assert result.abstention is None


# ---- abstentions (spec section 7) ----------------------------------------------------------

def test_unknown_agent_abstains_with_audit_version_2():
    result = score(player_kw=dict(agent={3: "NotAnAgent"}))
    assert (result.abstention, result.audit_version) == ("unknown_agent_utility", 2)


@pytest.mark.parametrize("over", [
    dict(next_events=None), dict(planted=None), dict(attacking_team=None), dict(reward={B: 1900}),
    dict(reward={A: None, B: 1900}), dict(player_kw=dict(omit=[("remaining", 2)])),
    dict(player_kw=dict(omit=[("kills", 2)])), dict(player_kw=dict(omit=[("deaths", 8)])),
    dict(player_kw=dict(omit=[("next_deaths", 8)])), dict(player_kw=dict(omit=[("agent", 4)])),
    dict(events=[ev(1, 10.0, 6, 1, weapon=None)]),
])
def test_missing_bonus_inputs_abstain(over):
    result = score(**over)
    assert (result.abstention, result.audit_version) == ("missing_bonus_inputs", 2)


def test_unrecognised_weapon_by_a_pistol_winner_abstains_but_unidentified_does_not():
    assert score(events=[ev(1, 10.0, 1, 6, weapon="Vandal2")],
                 player_kw=dict(deaths={6: 1})).abstention == "unrecognised_weapon"
    assert score(next_events=[ev(9, 5.0, 2, 7, weapon="Vandal2")],
                 player_kw=dict(next_deaths={7: 1})).abstention == "unrecognised_weapon"
    ok = score(events=[ev(1, 10.0, 1, 6, weapon="Weapon")], player_kw=dict(deaths={6: 1}))
    assert ok.abstention is None
    assert any("unidentified_weapon" in flag for flag in ok.data_quality)


def test_kill_feed_incomplete_is_distinguished_from_a_round_without_kills():
    assert score(events=[], player_kw=dict(deaths={6: 1})).abstention == "kill_feed_incomplete"
    assert score(next_events=[], player_kw=dict(next_deaths={6: 1})).abstention == "kill_feed_incomplete"
    assert score(events=[], next_events=[]).abstention is None


def test_existing_guards_still_come_first():
    assert score(pistol="Draw").abstention == "unknown_pistol_winner"

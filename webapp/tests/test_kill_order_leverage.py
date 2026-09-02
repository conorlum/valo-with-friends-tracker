"""Per-kill decomposition of the kill-order graph's leverage. No DB session:
the inputs are the same plain structures build_impact_rows_for_match builds
internally (impact.py:404-437), so they can be constructed by hand."""

import numpy as np
import pytest

from app.models.match import Team
from app.services.kill_order_leverage import (
    COMPONENTS,
    FALLBACK_WEIGHT,
    PARAM_INDEX,
    PARAMS,
    kill_terms_for_match,
    shipped_graph,
)


class FakeRound:
    def __init__(self, round_id, number, outcome="Team A Eliminated", planted=False,
                 plant_time=None, exploded=False, defused=False, defuse_time=None):
        self.id = round_id
        self.round_number = number
        self.outcome = outcome
        self.planted = planted
        self.plant_time = plant_time
        self.exploded = exploded
        self.defused = defused
        self.defuse_time = defuse_time


class FakePlayer:
    def __init__(self, player_id, team):
        self.id = player_id
        self.team = team


def make_match(kills, outcome="Team A Eliminated", loadout=4200, round_number=5):
    """Five players per side, ids 1-5 on TEAM_1 and 6-10 on TEAM_2.

    round_outcomes carries a placeholder for every round before
    round_number too: _econ_swing_risk_factor walks backward from
    round_number - 1 looking for each team's last win (_rounds_since_last_win),
    and KeyErrors on any earlier round missing from the dict even though
    only round_number's own kills are being decomposed."""
    players = {i: FakePlayer(i, Team.TEAM_1 if i <= 5 else Team.TEAM_2) for i in range(1, 11)}
    stats = {
        round_number: {
            i: {"score": 200, "kills": 0, "deaths": 0, "assists": 0,
                "loadout": loadout, "remaining": 1000}
            for i in range(1, 11)
        }
    }
    rnd = FakeRound(100, round_number, outcome=outcome)
    round_outcomes = {r: "Team A Eliminated" for r in range(1, round_number)}
    round_outcomes[round_number] = outcome
    return (
        {round_number: rnd},
        round_outcomes,
        stats,
        players,
        {round_number: kills},
    )


def kill(killer, victim, t):
    return {"killer_match_player_id": killer, "death_match_player_id": victim,
            "event_time_seconds": float(t)}


def test_params_are_26_in_a_fixed_order():
    assert len(PARAMS) == 26
    assert PARAMS[0] == "1v1"
    assert PARAMS[-1] == "fallback"
    assert PARAMS[:5] == ["1v1", "1v2", "1v3", "1v4", "1v5"]
    assert PARAM_INDEX["5v5"] == 24
    assert COMPONENTS == ("econ", "time", "swing")


def test_shipped_graph_collapses_50_edges_to_26_values():
    graph = shipped_graph()
    assert graph.shape == (26,)
    assert graph[PARAM_INDEX["5v5"]] == 150.0
    assert graph[PARAM_INDEX["1v1"]] == 250.0
    assert graph[PARAM_INDEX["4v4"]] == 170.0
    assert graph[PARAM_INDEX["1v2"]] == 190.0
    assert graph[PARAM_INDEX["2v1"]] == 130.0
    assert graph[PARAM_INDEX["fallback"]] == FALLBACK_WEIGHT


def test_first_kill_of_a_round_crosses_5v5_with_a_positive_sign_for_team_a():
    terms = kill_terms_for_match(*make_match([kill(1, 6, 10.0)]))
    (term,) = terms[5]
    assert PARAMS[term.param_index] == "5v5"
    assert term.sign == 1.0
    assert term.tracked is True


def test_a_team_b_kill_carries_a_negative_sign():
    terms = kill_terms_for_match(*make_match([kill(6, 1, 10.0)]))
    (term,) = terms[5]
    assert PARAMS[term.param_index] == "5v5"
    assert term.sign == -1.0


def test_the_state_walks_down_as_kills_land():
    terms = kill_terms_for_match(*make_match([
        kill(1, 6, 5.0), kill(1, 7, 6.0), kill(8, 1, 7.0),
    ]))
    assert [PARAMS[t.param_index] for t in terms[5]] == ["5v5", "5v4", "3v5"]


def test_a_self_kill_zeroes_the_kill_half_and_reverses_the_sign():
    terms = kill_terms_for_match(*make_match([kill(1, 1, 10.0)]))
    (term,) = terms[5]
    assert term.kill == (0.0, 0.0, 0.0)
    assert term.sign == -1.0
    assert any(v != 0.0 for v in term.death)


def test_a_self_kill_decrements_the_killers_own_side():
    terms = kill_terms_for_match(*make_match([kill(1, 1, 5.0), kill(2, 6, 6.0)]))
    assert [PARAMS[t.param_index] for t in terms[5]] == ["5v5", "4v5"]


def test_an_untracked_transition_lands_on_the_fallback_parameter():
    """5v5 -> ... -> 0v5 exhausts team B; a 6th kill against a BRAND-NEW
    (never-before-referenced) team-B member has no edge FROM 0v5 to cross.

    Reusing an earlier victim's id for the 6th kill (as a first draft of
    this test did) does not exercise the fallback: _check_for_resurrection
    looks FORWARD from each kill for any later kill referencing that same
    match_player_id, so a repeated victim retroactively cancels that
    EARLIER kill's decrement -- the walk then still lands on the tracked
    1v5->0v5 edge with one kill to spare, never reaching an untracked
    transition. The 6th kill's victim must be new to avoid that."""
    players = {i: FakePlayer(i, Team.TEAM_1) for i in range(1, 6)}
    players.update({i: FakePlayer(i, Team.TEAM_2) for i in range(6, 12)})
    stats = {
        5: {
            i: {"score": 200, "kills": 0, "deaths": 0, "assists": 0,
                "loadout": 4200, "remaining": 1000}
            for i in range(1, 12)
        }
    }
    rnd = FakeRound(100, 5, outcome="Team A Eliminated")
    round_outcomes = {r: "Team A Eliminated" for r in range(1, 6)}
    kills = [kill(1, 5 + i, float(i)) for i in range(1, 6)] + [kill(1, 11, 9.0)]

    terms = kill_terms_for_match({5: rnd}, round_outcomes, stats, players, {5: kills})
    names = [PARAMS[t.param_index] for t in terms[5]]
    assert names[-1] == "fallback"
    assert terms[5][-1].tracked is False
    assert all(n != "fallback" for n in names[:-1])


def test_the_traded_factor_is_folded_into_the_death_half_only():
    """Kill at t=10 traded back at t=14 -> factor 0.4 on the death half."""
    plain = kill_terms_for_match(*make_match([kill(1, 6, 10.0)]))[5][0]
    traded = kill_terms_for_match(*make_match([kill(1, 6, 10.0), kill(7, 1, 14.0)]))[5][0]
    assert np.isclose(traded.traded, 0.4)
    assert np.allclose(traded.kill, plain.kill)
    assert np.allclose(traded.death, np.array(plain.death) * 0.4)


def test_death_untraded_is_the_undiscounted_half_and_the_invariant_holds():
    """The player-level read reports the trade discount as
    death_untraded - death, so the two must stay consistent."""
    plain = kill_terms_for_match(*make_match([kill(1, 6, 10.0)]))[5][0]
    traded = kill_terms_for_match(*make_match([kill(1, 6, 10.0), kill(7, 1, 14.0)]))[5][0]

    assert np.allclose(plain.death_untraded, plain.death)  # traded == 1.0
    assert np.allclose(traded.death_untraded, plain.death_untraded)
    for term in (plain, traded):
        assert np.allclose(term.death, np.array(term.death_untraded) * term.traded)
    discount = np.array(traded.death_untraded) - np.array(traded.death)
    assert np.all(discount > 0)


def test_an_instant_trade_gives_a_zero_factor_without_a_division():
    """_traded_factor returns trade_time/10, so a same-second trade is
    exactly 0.0 -- which is why death_untraded is stored rather than
    recovered by dividing."""
    term = kill_terms_for_match(*make_match([kill(1, 6, 10.0), kill(7, 1, 10.0)]))[5][0]
    assert term.traded == 0.0
    assert np.allclose(term.death, (0.0, 0.0, 0.0))
    assert np.any(np.array(term.death_untraded) != 0.0)


def test_econ_mismatch_moves_the_kill_half_econ_factor():
    """The factor is killer_tier / victim_tier (impact.py:483), and tier
    codes run LOWER for a better economy (FULL_BUY=4, ECO=6) -- so beating
    up an eco victim while on a full buy scores BELOW 1.0 (not much of an
    accomplishment); equal loadouts score exactly 1.0."""
    rounds, outcomes, stats, players, kills = make_match([kill(1, 6, 10.0)])
    equal = kill_terms_for_match(rounds, outcomes, stats, players, kills)[5][0]
    assert np.isclose(equal.kill[COMPONENTS.index("econ")], 1.0)

    stats[5][6]["loadout"] = 1500  # victim on an eco
    mismatch = kill_terms_for_match(rounds, outcomes, stats, players, kills)[5][0]
    assert mismatch.kill[COMPONENTS.index("econ")] < 1.0


def test_missing_player_stats_raise_rather_than_being_skipped():
    rounds, outcomes, stats, players, kills = make_match([kill(1, 6, 10.0)])
    del stats[5][6]
    with pytest.raises(KeyError):
        kill_terms_for_match(rounds, outcomes, stats, players, kills)

from app.models.match import Team
from app.services.state_replay import (
    KillEventInput,
    ReplayDiagnostics,
    RoundInput,
    TerminalCause,
    is_valid_resolved_duel,
    is_valid_state_transition,
    replay_round,
)

TEAM1 = frozenset({1, 2, 3, 4, 5})
TEAM2 = frozenset({6, 7, 8, 9, 10})


def make_round(
    round_id=1,
    round_number=1,
    outcome="Team A Elimination Win",
    planted=False,
    plant_time=None,
    exploded=False,
    defused=False,
    defuse_time=None,
    kills=(),
):
    return RoundInput(
        round_id=round_id,
        round_number=round_number,
        outcome=outcome,
        planted=planted,
        plant_time=plant_time,
        exploded=exploded,
        defused=defused,
        defuse_time=defuse_time,
        kill_events=tuple(kills),
    )


def kill(id, killer, victim, t):
    return KillEventInput(id=id, killer_match_player_id=killer, death_match_player_id=victim, event_time_seconds=t)


def replay(round_input, team1=TEAM1, team2=TEAM2):
    diagnostics = ReplayDiagnostics()
    result = replay_round(match_id=1, round_input=round_input, team1_player_ids=team1, team2_player_ids=team2, diagnostics=diagnostics)
    return result, diagnostics


def test_no_kill_objective_round_records_one_initial_entry_and_no_duel():
    round_input = make_round(outcome="Team A Elimination Win", kills=[])
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason is None
    assert len(result.entries) == 1
    assert result.entries[0].team1_alive_ids == TEAM1
    assert result.entries[0].team2_alive_ids == TEAM2
    assert result.duels == []
    assert diagnostics.accepted_rounds == 1


def test_ordinary_elimination_records_initial_and_every_post_casualty_entry_exactly_once():
    # Team 2 gets wiped 5 -> 0 by team-1 kills.
    kills = [kill(i, killer=1, victim=5 + i, t=float(i)) for i in range(1, 6)]
    round_input = make_round(outcome="Team A Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason is None
    # initial + 5 casualties = 6 entries.
    assert len(result.entries) == 6
    assert result.entries[-1].team2_alive_ids == frozenset()
    assert result.entries[-1].terminal_cause == TerminalCause.ELIMINATION
    assert diagnostics.accepted_rounds == 1


def test_elimination_casualty_counts_as_duel_and_later_events_do_not():
    kills = [kill(i, killer=1, victim=5 + i, t=float(i)) for i in range(1, 6)]
    # A bogus extra kill logged after team 2 is already eliminated.
    kills.append(kill(6, killer=2, victim=3, t=6.0))
    round_input = make_round(outcome="Team A Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert len(result.duels) == 5
    assert diagnostics.post_decision_events == 1


def test_defuse_discards_kills_at_and_after_defuse_time():
    kills = [
        kill(1, killer=6, victim=1, t=10.0),
        kill(2, killer=6, victim=2, t=20.0),  # at/after defuse_time -> discarded
    ]
    round_input = make_round(
        outcome="Team B Defused Win", planted=True, plant_time=5.0, defused=True, defuse_time=20.0, kills=kills
    )
    result, diagnostics = replay(round_input)
    assert len(result.duels) == 1
    assert diagnostics.post_decision_events == 1
    assert result.entries[-1].terminal_cause == TerminalCause.DEFUSE


def test_detonation_discards_kills_at_and_after_plant_time_plus_45():
    kills = [
        kill(1, killer=1, victim=6, t=10.0),
        kill(2, killer=1, victim=7, t=50.0),  # plant_time(5) + 45 = 50 -> discarded
    ]
    round_input = make_round(
        outcome="Team A Detonated Win", planted=True, plant_time=5.0, exploded=True, kills=kills
    )
    result, diagnostics = replay(round_input)
    assert len(result.duels) == 1
    assert diagnostics.post_decision_events == 1
    assert result.entries[-1].terminal_cause == TerminalCause.DETONATION


def test_confirmed_time_expiry_discards_later_kills():
    # No stored cutoff for a time win -- every logged kill is trusted as
    # pre-resolution, per the module's data-contract audit.
    kills = [kill(1, killer=1, victim=6, t=30.0)]
    round_input = make_round(outcome="Team B Time Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert len(result.duels) == 1
    assert diagnostics.post_decision_events == 0
    assert result.entries[-1].terminal_cause == TerminalCause.TIME


def test_post_plant_all_attackers_dead_state_remains_observable_until_objective_resolution():
    # All 5 attackers (team 1) die post-plant; bomb still detonates later.
    kills = [kill(i, killer=6, victim=i, t=10.0 + i) for i in range(1, 6)]
    round_input = make_round(
        outcome="Team A Detonated Win", planted=True, plant_time=10.0, exploded=True, kills=kills
    )
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason is None
    assert result.entries[-1].team1_alive_ids == frozenset()
    assert len(result.duels) == 5


def test_self_environmental_casualty_changes_state_but_not_duel_counts():
    kills = [kill(1, killer=1, victim=1, t=5.0)]  # self-kill
    round_input = make_round(outcome="Team B Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert result.duels == []
    assert result.entries[-1].team1_alive_ids == frozenset({2, 3, 4, 5})
    assert is_valid_state_transition(kills[0]) is True
    assert is_valid_resolved_duel(kills[0], {1: Team.TEAM_1}) is False


def test_opponent_duel_changes_state_and_creates_one_duel_observation():
    kills = [kill(1, killer=1, victim=6, t=5.0)]
    round_input = make_round(outcome="Team A Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert len(result.duels) == 1
    duel = result.duels[0]
    assert duel.killer_match_player_id == 1
    assert duel.victim_match_player_id == 6
    assert duel.team2_alive_before == TEAM2


def test_equal_time_ambiguity_excludes_round():
    kills = [
        kill(1, killer=1, victim=6, t=5.0),
        kill(2, killer=2, victim=7, t=5.0),
    ]
    round_input = make_round(outcome="Team A Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason == "equal_time_ambiguity"
    assert diagnostics.equal_time_ambiguities == 1
    assert result.entries == []
    assert result.duels == []


def test_repeated_state_label_after_explicit_revive_creates_second_entry():
    kills = [
        kill(1, killer=6, victim=1, t=5.0),
        KillEventInput(id=2, killer_match_player_id=None, death_match_player_id=None, event_time_seconds=8.0, revived_match_player_id=1),
        kill(3, killer=6, victim=1, t=12.0),
    ]
    round_input = make_round(outcome="Team B Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason is None
    # initial + death + revive + death again = 4 entries, two of them "4v5".
    labels = [(len(e.team1_alive_ids), len(e.team2_alive_ids)) for e in result.entries]
    assert labels.count((4, 5)) == 2
    assert len(result.duels) == 2


def test_ambiguous_lifecycle_round_excluded_when_revive_events_unavailable():
    kills = [
        kill(1, killer=6, victim=1, t=5.0),
        kill(2, killer=7, victim=1, t=8.0),  # victim already dead, no revive marker
    ]
    round_input = make_round(outcome="Team B Elimination Win", kills=kills)
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason == "ambiguous_lifecycle"
    assert diagnostics.ambiguous_lifecycle_rounds == 1
    assert result.entries == []
    assert result.duels == []


def test_explicit_surrender_placeholder_excluded_while_valid_no_kill_round_retained():
    surrendered = make_round(round_id=1, outcome="Team A Surrendered Win", kills=[])
    result, _ = replay(surrendered)
    assert result.exclusion_reason == "surrendered"

    valid_no_kill = make_round(round_id=2, outcome="Team A Elimination Win", kills=[])
    result2, _ = replay(valid_no_kill)
    assert result2.exclusion_reason is None
    assert len(result2.entries) == 1


def test_overtime_without_side_data_excluded():
    round_input = make_round(round_number=25, outcome="Team A Elimination Win", kills=[])
    result, diagnostics = replay(round_input)
    assert result.exclusion_reason == "overtime_unknown_side"
    assert diagnostics.excluded_rounds_by_reason["overtime_unknown_side"] == 1

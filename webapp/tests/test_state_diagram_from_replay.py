"""Step 8 (docs/player_page_render_speed.txt): accumulate_state_stats_from_
replay derives the round-win/kill-order diamonds' aggregates from state_
replay's ALREADY-COMPUTED entries/duels, adopting its stricter semantics
(terminal-time truncation, whole-round exclusion for ambiguous lifecycle /
equal-time ambiguity / surrender / overtime / unknown winner) instead of the
old accumulate_match_state_stats' ad hoc walk. These tests build real
RoundInput/KillEventInput fixtures and run them through the real
replay_round (mirroring tests/test_state_replay.py's own fixture style) so
the aggregation is checked against the REAL replay engine's output, not a
hand-rolled stand-in.
"""

from app.models.match import Team
from app.services.player_graphs import accumulate_state_stats_from_replay, build_state_aggregates_from_replays
from app.services.state_replay import KillEventInput, ReplayDiagnostics, RoundInput, replay_round

TEAM1 = frozenset({1, 2, 3, 4, 5})
TEAM2 = frozenset({6, 7, 8, 9, 10})


def make_round(round_id=1, round_number=1, outcome="Team A Elimination Win", kills=()):
    return RoundInput(
        round_id=round_id, round_number=round_number, outcome=outcome,
        planted=False, plant_time=None, exploded=False, defused=False, defuse_time=None,
        kill_events=tuple(kills),
    )


def kill(id, killer, victim, t):
    return KillEventInput(id=id, killer_match_player_id=killer, death_match_player_id=victim, event_time_seconds=t)


def replay_one_round(round_input, team1=TEAM1, team2=TEAM2):
    diagnostics = ReplayDiagnostics()
    result = replay_round(
        match_id=1, round_input=round_input, team1_player_ids=team1, team2_player_ids=team2, diagnostics=diagnostics
    )
    return result.entries, result.duels


def _accumulate(entries, duels, target_team=Team.TEAM_1, target_id=1):
    win_stats, kill_order_weights = {}, {}
    accumulate_state_stats_from_replay(entries, duels, target_team, target_id, win_stats, kill_order_weights)
    return win_stats, kill_order_weights


def test_target_gets_a_kill_records_win_stats_and_a_positive_kill_order_weight():
    round_input = make_round(kills=[kill(1, killer=1, victim=6, t=1.0)])
    entries, duels = replay_one_round(round_input)

    win_stats, kill_order_weights = _accumulate(entries, duels)

    assert kill_order_weights[("5v5", "5v4")] == 1
    assert win_stats["5v5"] == {"win": 1, "total": 1}   # target alive at 5v5, team A (team1) wins
    assert win_stats["5v4"] == {"win": 1, "total": 1}   # target alive at 5v4 too -- they're the killer, not the victim


def test_target_dies_to_enemy_records_negative_kill_order_weight():
    round_input = make_round(outcome="Team B Elimination Win", kills=[kill(1, killer=6, victim=1, t=1.0)])
    entries, duels = replay_one_round(round_input)

    win_stats, kill_order_weights = _accumulate(entries, duels)

    assert kill_order_weights[("5v5", "4v5")] == -1
    assert win_stats["5v5"] == {"win": 0, "total": 1}  # target alive at 5v5, but Team B wins, not team1
    assert "4v5" not in win_stats  # target no longer alive at the 4v5 entry -- excluded from win_stats


def test_environmental_death_with_no_killer_still_counts_as_kill_order_loss():
    round_input = make_round(outcome="Team B Elimination Win", kills=[kill(1, killer=None, victim=1, t=1.0)])
    entries, duels = replay_one_round(round_input)

    assert duels == []  # no killer -- not a resolved duel

    win_stats, kill_order_weights = _accumulate(entries, duels)
    assert kill_order_weights[("5v5", "4v5")] == -1


def test_self_kill_still_counts_as_kill_order_loss_but_not_a_duel():
    round_input = make_round(outcome="Team B Elimination Win", kills=[kill(1, killer=1, victim=1, t=1.0)])
    entries, duels = replay_one_round(round_input)

    assert duels == []  # killer == victim -- not a resolved duel

    win_stats, kill_order_weights = _accumulate(entries, duels)
    assert kill_order_weights[("5v5", "4v5")] == -1


def test_target_already_dead_ignores_later_transitions_in_the_round():
    kills = [
        kill(1, killer=6, victim=1, t=1.0),  # target (1) dies first
        kill(2, killer=2, victim=7, t=2.0),  # a teammate then gets a kill -- not the target's
    ]
    round_input = make_round(kills=kills)
    entries, duels = replay_one_round(round_input)

    win_stats, kill_order_weights = _accumulate(entries, duels)

    assert kill_order_weights == {("5v5", "4v5"): -1}
    assert win_stats == {"5v5": {"win": 1, "total": 1}}


def test_surrendered_round_produces_no_aggregates():
    round_input = make_round(outcome="Team A Surrendered Win", kills=[kill(1, killer=1, victim=6, t=1.0)])
    entries, duels = replay_one_round(round_input)
    assert entries == [] and duels == []  # confirms state_replay itself excludes surrendered rounds

    win_stats, kill_order_weights = _accumulate(entries, duels)
    assert win_stats == {} and kill_order_weights == {}


def test_overtime_round_produces_no_aggregates():
    round_input = make_round(round_number=25, kills=[kill(1, killer=1, victim=6, t=1.0)])
    entries, duels = replay_one_round(round_input)
    assert entries == [] and duels == []  # round_number > 24 -- no recoverable attack/defense side

    win_stats, kill_order_weights = _accumulate(entries, duels)
    assert win_stats == {} and kill_order_weights == {}


def test_unresolved_winner_excludes_the_round_from_both_products():
    """Divergence from the old accumulate_match_state_stats, which only
    skipped win_stats for an unresolved winner while still recording the
    kill in kill_order_weights -- state_replay excludes the WHOLE round."""
    round_input = make_round(outcome=None, kills=[kill(1, killer=1, victim=6, t=1.0)])
    entries, duels = replay_one_round(round_input)
    assert entries == [] and duels == []

    win_stats, kill_order_weights = _accumulate(entries, duels)
    assert win_stats == {} and kill_order_weights == {}


def test_two_rounds_accumulate_into_the_same_dicts():
    round1 = make_round(round_id=1, round_number=1, kills=[kill(1, killer=1, victim=6, t=1.0)])
    round2 = make_round(round_id=2, round_number=2, kills=[kill(2, killer=1, victim=6, t=1.0)])
    entries1, duels1 = replay_one_round(round1)
    entries2, duels2 = replay_one_round(round2)

    win_stats, kill_order_weights = _accumulate(entries1 + entries2, duels1 + duels2)

    assert kill_order_weights[("5v5", "5v4")] == 2
    assert win_stats["5v5"] == {"win": 2, "total": 2}


def test_target_on_team2_uses_the_correct_own_opponent_perspective():
    round_input = make_round(outcome="Team B Elimination Win", kills=[kill(1, killer=6, victim=1, t=1.0)])
    entries, duels = replay_one_round(round_input)

    win_stats, kill_order_weights = _accumulate(entries, duels, target_team=Team.TEAM_2, target_id=6)

    assert kill_order_weights[("5v5", "5v4")] == 1  # from team2's own perspective, this is THEIR win transition
    assert win_stats["5v5"] == {"win": 1, "total": 1}  # Team B (team2) wins the round


def test_build_state_aggregates_from_replays_folds_multiple_matches():
    round_input = make_round(kills=[kill(1, killer=1, victim=6, t=1.0)])
    entries, duels = replay_one_round(round_input)

    class _FakeMatchPlayer:
        def __init__(self, id_, team):
            self.id = id_
            self.team = team

    replays = [
        (_FakeMatchPlayer(1, Team.TEAM_1), entries, duels),
        (_FakeMatchPlayer(1, Team.TEAM_1), entries, duels),  # a second "match" with the same shape
    ]

    win_stats, kill_order_weights = build_state_aggregates_from_replays(replays)

    assert kill_order_weights[("5v5", "5v4")] == 2
    assert win_stats["5v5"] == {"win": 2, "total": 2}

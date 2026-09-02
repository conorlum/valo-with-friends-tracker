"""Observation extraction: one differential row per round, team A minus
team B. Fixtures are plain ORM construction with no session, following
tests/test_player_profile_types.py."""

import numpy as np
import pytest

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.scoring.impact import CalculatedImpact
from app.services.impact_eval import RoundObservation, build_observations_for_match


def _match_with_two_rounds():
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED, map_name="Bind",
        team1_rounds_won=13, team2_rounds_won=7,
    )
    a = MatchPlayer(id=1, match_id=1, player_id=10, agent="Jett", team=Team.TEAM_1)
    b = MatchPlayer(id=2, match_id=1, player_id=20, agent="Sova", team=Team.TEAM_2)
    a.player = Player(id=10, display_name="A#1")
    b.player = Player(id=20, display_name="B#2")
    match.match_players = [a, b]

    r1 = Round(id=101, match_id=1, round_number=1, outcome="Team A Wins")
    r1.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=2, deaths=0, assists=0, loadout=800),
        RoundPlayerStat(match_player_id=2, kills=0, deaths=2, assists=0, loadout=800),
    ]
    r2 = Round(id=102, match_id=1, round_number=2, outcome="Team B Wins")
    r2.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=0, deaths=1, assists=0, loadout=4500),
        RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000),
    ]
    match.rounds = [r1, r2]
    return match


def _calculated():
    return [
        CalculatedImpact(101, 1, 300, 0, 300, 100, 60, 30, 20, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(101, 2, 0, 250, -250, 10, -40, -20, -10, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(102, 1, 0, 200, -200, 20, -30, -10, -5, 0, 0, 0, 0, 0, 0, 0, 0, None),
        CalculatedImpact(102, 2, 280, 0, 280, 90, 50, 25, 15, 0, 0, 0, 0, 0, 0, 0, 0, None),
    ]


def test_one_observation_per_round():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert [o.round_number for o in obs] == [1, 2]


def test_features_are_team_a_minus_team_b():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].damage == 100 - 10
    assert obs[0].econ_impact == 60 - (-40)
    assert obs[0].swing_impact == 20 - (-10)


def test_kill_diff_is_team_kill_differential():
    """kills_A - kills_B, not kills-minus-deaths: deaths_A == kills_B in
    99.1% of real rounds, so the latter is the same column doubled."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].kill_diff == 2 - 0
    assert obs[1].kill_diff == 0 - 1


def test_score_differential_excludes_the_current_round():
    """Round 1's control must be 0-0, not 1-0: the round's own result is a
    separate control and must never leak into pre-round score."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].score_diff_before == 0
    assert obs[1].score_diff_before == 1


def test_economy_controls_are_start_of_round():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].loadout_diff == 800 - 800
    assert obs[1].loadout_diff == 4500 - 2000
    assert obs[1].full_buy_count_diff == 1 - 0


def test_round_and_match_outcomes():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].round_won_by_team_a is True
    assert obs[1].round_won_by_team_a is False
    assert all(o.match_won_by_team_a is True for o in obs)


def test_first_half_is_always_attack_first_for_team_a():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert all(o.attacking_is_team_a for o in obs)


def test_last_round_is_terminal():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[-1].is_terminal
    assert not obs[0].is_terminal


def test_surrender_rounds_are_dropped():
    match = _match_with_two_rounds()
    match.rounds[1].outcome = "Team A Surrendered Win"
    obs = build_observations_for_match(match, _calculated())
    assert [o.round_number for o in obs] == [1]


def test_impact_diff_is_the_exact_stored_differential():
    """Not reconstructed from components: round 1 is 300 - (-250) = 550."""
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert obs[0].impact_diff == 300 - (-250)
    assert obs[1].impact_diff == -200 - 280


def test_economy_control_is_a_team_average_not_a_sum():
    """A sum would encode how many player-stat rows a round happens to have."""
    match = _match_with_two_rounds()
    # Drop one of team B's stat rows from round 2; the average must not move.
    match.rounds[1].player_stats = [
        s for s in match.rounds[1].player_stats if s.match_player_id == 1
    ] + [RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=2000)]
    obs = build_observations_for_match(match, _calculated())
    assert obs[1].loadout_diff == 4500 - 2000


def test_missing_impact_rows_raise_rather_than_becoming_zero():
    from app.services.impact_eval import MissingImpactRows

    with pytest.raises(MissingImpactRows, match="round 2"):
        build_observations_for_match(
            _match_with_two_rounds(), [r for r in _calculated() if r.round_id == 101]
        )


def test_tied_match_has_no_match_label():
    match = _match_with_two_rounds()
    match.team1_rounds_won = match.team2_rounds_won = 12
    obs = build_observations_for_match(match, _calculated())
    assert all(o.match_won_by_team_a is None for o in obs)


from app.services.impact_eval import (
    CONTROLS_CONTEXT,
    CONTROLS_RESULT,
    FEATURE_COMPONENTS,
    FIRST_HALF_ROUNDS,
    _feature_value,
    _half_of,
    assign_folds,
    group_by_match,
)


def test_every_match_gets_exactly_one_fold():
    folds = assign_folds([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], n_folds=5, seed=0)
    assert set(folds) == set(range(1, 11))
    assert set(folds.values()) <= {0, 1, 2, 3, 4}


def test_folds_are_balanced():
    folds = assign_folds(list(range(100)), n_folds=5, seed=0)
    counts = [sum(1 for f in folds.values() if f == k) for k in range(5)]
    assert max(counts) - min(counts) <= 1


def test_fold_assignment_is_seed_deterministic():
    assert assign_folds(list(range(50)), seed=11) == assign_folds(list(range(50)), seed=11)


def test_grouping_keeps_a_match_together():
    grouped = group_by_match(build_observations_for_match(_match_with_two_rounds(), _calculated()))
    assert list(grouped) == [1]
    assert len(grouped[1]) == 2


def test_half_boundaries_match_the_scorer_convention():
    """impact.py:309 already encodes rounds 12/24 as the economy resets."""
    assert _half_of(1) == _half_of(FIRST_HALF_ROUNDS) == 1
    assert _half_of(13) == _half_of(24) == 2
    assert _half_of(25) == 3


def test_round_result_control_is_signed_and_separate():
    obs = build_observations_for_match(_match_with_two_rounds(), _calculated())
    assert _feature_value(obs[0], "round_result") == 1.0
    assert _feature_value(obs[1], "round_result") == -1.0
    assert "round_result" in CONTROLS_RESULT
    assert "round_result" not in CONTROLS_CONTEXT
    assert "round_result" not in FEATURE_COMPONENTS

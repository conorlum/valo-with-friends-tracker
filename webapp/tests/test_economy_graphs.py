import pytest

from app.models import Match, MatchPlayer, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.services.economy_graphs import (
    EconSample,
    build_favor_outcome_matrix,
    build_pistol_stats,
    build_tier_matrix,
    match_econ_rounds,
)


def _match_player(id_, team):
    return MatchPlayer(id=id_, player_id=id_, agent="Jett", team=team)


def _stat(match_player_id, loadout):
    return RoundPlayerStat(match_player_id=match_player_id, loadout=loadout)


def test_match_econ_rounds_tiers_use_average_per_player_not_team_total():
    match = Match(id=1, external_id="x", source=MatchSource.SCRAPED)
    match.match_players = [_match_player(i, Team.TEAM_1) for i in range(1, 6)] + [
        _match_player(i, Team.TEAM_2) for i in range(6, 11)
    ]
    # Round 3 -- not a pistol round -- so the buy-tier comes from loadout.
    round_row = Round(id=1, match_id=1, round_number=3, outcome="Team A Wins")
    # Team 1 buys $2000/player (ECO tier). Summed across 5 players that's
    # $10,000 -- well past the $4200 FULL_BUY threshold -- so if tiering used
    # the team total instead of the per-player average it would wrongly read
    # as FULL_BUY.
    round_row.player_stats = [_stat(i, 2000) for i in range(1, 6)] + [_stat(i, 4500) for i in range(6, 11)]
    match.rounds = [round_row]

    rounds = match_econ_rounds(match)

    assert rounds[3].team1_tier_label == "Eco"
    assert rounds[3].team2_tier_label == "Full Buy"


def test_match_econ_rounds_round_one_and_thirteen_are_pistol_regardless_of_loadout():
    match = Match(id=1, external_id="x", source=MatchSource.SCRAPED)
    match.match_players = [_match_player(i, Team.TEAM_1) for i in range(1, 6)] + [
        _match_player(i, Team.TEAM_2) for i in range(6, 11)
    ]
    round1 = Round(id=1, match_id=1, round_number=1, outcome="Team A Wins")
    round1.player_stats = [_stat(i, 900) for i in range(1, 6)] + [_stat(i, 950) for i in range(6, 11)]
    round13 = Round(id=2, match_id=1, round_number=13, outcome="Team B Wins")
    round13.player_stats = [_stat(i, 800) for i in range(1, 6)] + [_stat(i, 4500) for i in range(6, 11)]
    match.rounds = [round1, round13]

    rounds = match_econ_rounds(match)

    assert rounds[1].team1_tier_label == "Pistol"
    assert rounds[1].team2_tier_label == "Pistol"
    # Team 2 full-buys round 13 but it's still labeled Pistol -- the round
    # number overrides the loadout-derived tier.
    assert rounds[13].team1_tier_label == "Pistol"
    assert rounds[13].team2_tier_label == "Pistol"


def test_tier_matrix_computes_win_pct_per_matchup():
    samples = [
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True, own_loadout=4500, enemy_loadout=2000),
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True, own_loadout=4500, enemy_loadout=2000),
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=False, own_loadout=4500, enemy_loadout=2000),
        EconSample(own_tier="ECO", enemy_tier="FULL_BUY", own_won=False, own_loadout=500, enemy_loadout=4500),
    ]
    matrix = build_tier_matrix(samples)

    cell = matrix.cells[("FULL_BUY", "ECO")]
    assert cell.wins == 2
    assert cell.total == 3
    assert cell.win_pct == 2 / 3
    assert cell.avg_loadout_ratio == pytest.approx(4500 / (4500 + 2000))

    other = matrix.cells[("ECO", "FULL_BUY")]
    assert other.wins == 0
    assert other.total == 1
    assert other.win_pct == 0.0
    assert other.avg_loadout_ratio == 500 / (500 + 4500)

    # Every buy-tier pair is present even with zero samples.
    empty = matrix.cells[("ECO", "FORCE")]
    assert empty.total == 0
    assert empty.win_pct is None
    assert empty.avg_loadout_ratio is None
    assert matrix.total_rounds == 4
    # Only the ranked buy tiers form the grid -- no PISTOL row/column.
    assert matrix.tiers == ["ECO", "FORCE", "FULL_BUY"]
    assert ("PISTOL", "PISTOL") not in matrix.cells


def test_tier_matrix_excludes_pistol_samples_from_totals():
    samples = [
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True, own_loadout=4500, enemy_loadout=2000),
        EconSample(own_tier="PISTOL", enemy_tier="PISTOL", own_won=True, own_loadout=900, enemy_loadout=950),
    ]
    matrix = build_tier_matrix(samples)
    assert matrix.total_rounds == 1


def test_build_pistol_stats_computes_win_pct_and_loadout_share():
    samples = [
        EconSample(own_tier="PISTOL", enemy_tier="PISTOL", own_won=True, own_loadout=900, enemy_loadout=950),
        EconSample(own_tier="PISTOL", enemy_tier="PISTOL", own_won=False, own_loadout=800, enemy_loadout=1000),
        # Non-pistol samples are ignored.
        EconSample(own_tier="ECO", enemy_tier="FULL_BUY", own_won=False, own_loadout=1500, enemy_loadout=4500),
    ]
    stats = build_pistol_stats(samples)
    assert stats.wins == 1
    assert stats.losses == 1
    assert stats.total == 2
    assert stats.win_pct == 0.5
    assert stats.avg_loadout_ratio == pytest.approx((900 / 1850 + 800 / 1800) / 2)


def test_build_pistol_stats_with_no_pistol_rounds_has_no_win_pct():
    stats = build_pistol_stats(
        [EconSample(own_tier="ECO", enemy_tier="FULL_BUY", own_won=False, own_loadout=1500, enemy_loadout=4500)]
    )
    assert stats.total == 0
    assert stats.win_pct is None
    assert stats.avg_loadout_ratio is None


def test_favor_outcome_matrix_buckets_by_relative_tier_not_absolute():
    samples = [
        # Favored: bought a higher tier than the enemy.
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True, own_loadout=4500, enemy_loadout=2000),
        EconSample(own_tier="FORCE", enemy_tier="ECO", own_won=False, own_loadout=3500, enemy_loadout=1500),
        # Even: same tier both sides, regardless of which tier.
        EconSample(own_tier="ECO", enemy_tier="ECO", own_won=False, own_loadout=1500, enemy_loadout=1500),
        EconSample(own_tier="FULL_BUY", enemy_tier="FULL_BUY", own_won=True, own_loadout=4500, enemy_loadout=4500),
        # Unfavored: bought a lower tier than the enemy.
        EconSample(own_tier="ECO", enemy_tier="FULL_BUY", own_won=False, own_loadout=1500, enemy_loadout=4500),
    ]
    matrix = build_favor_outcome_matrix(samples)
    by_key = {row.key: row for row in matrix.rows}

    assert by_key["favored"].total == 2
    assert by_key["favored"].wins == 1
    assert by_key["even"].total == 2
    assert by_key["even"].wins == 1
    assert by_key["unfavored"].total == 1
    assert by_key["unfavored"].wins == 0
    assert matrix.total_rounds == 5

    # Own team's average share of the two teams' combined loadout.
    assert by_key["even"].avg_loadout_ratio == 0.5
    assert by_key["unfavored"].avg_loadout_ratio == 1500 / (1500 + 4500)


def test_favor_outcome_matrix_row_with_no_samples_has_no_win_pct():
    matrix = build_favor_outcome_matrix(
        [EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True, own_loadout=4500, enemy_loadout=2000)]
    )
    by_key = {row.key: row for row in matrix.rows}
    assert by_key["unfavored"].total == 0
    assert by_key["unfavored"].win_pct is None
    assert by_key["unfavored"].avg_loadout_ratio is None

from app.models import Match, MatchPlayer, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.services.economy_graphs import EconSample, build_favor_outcome_matrix, build_tier_matrix, match_econ_rounds


def _match_player(id_, team):
    return MatchPlayer(id=id_, player_id=id_, agent="Jett", team=team)


def _stat(match_player_id, loadout):
    return RoundPlayerStat(match_player_id=match_player_id, loadout=loadout)


def test_match_econ_rounds_tiers_use_average_per_player_not_team_total():
    match = Match(id=1, external_id="x", source=MatchSource.SCRAPED)
    match.match_players = [_match_player(i, Team.TEAM_1) for i in range(1, 6)] + [
        _match_player(i, Team.TEAM_2) for i in range(6, 11)
    ]
    round_row = Round(id=1, match_id=1, round_number=1, outcome="Team A Wins")
    # Team 1 buys $2000/player (ECO tier). Summed across 5 players that's
    # $10,000 -- well past the $4200 FULL_BUY threshold -- so if tiering used
    # the team total instead of the per-player average it would wrongly read
    # as FULL_BUY.
    round_row.player_stats = [_stat(i, 2000) for i in range(1, 6)] + [_stat(i, 4500) for i in range(6, 11)]
    match.rounds = [round_row]

    rounds = match_econ_rounds(match)

    assert rounds[1].team1_tier_label == "Eco"
    assert rounds[1].team2_tier_label == "Full Buy"


def test_tier_matrix_computes_win_pct_per_matchup():
    samples = [
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True),
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True),
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=False),
        EconSample(own_tier="SAVE", enemy_tier="FULL_BUY", own_won=False),
    ]
    matrix = build_tier_matrix(samples)

    cell = matrix.cells[("FULL_BUY", "ECO")]
    assert cell.wins == 2
    assert cell.total == 3
    assert cell.win_pct == 2 / 3

    other = matrix.cells[("SAVE", "FULL_BUY")]
    assert other.wins == 0
    assert other.total == 1
    assert other.win_pct == 0.0

    # Every tier pair is present even with zero samples.
    empty = matrix.cells[("ECO", "FORCE")]
    assert empty.total == 0
    assert empty.win_pct is None
    assert matrix.total_rounds == 4


def test_favor_outcome_matrix_buckets_by_relative_tier_not_absolute():
    samples = [
        # Favored: bought a higher tier than the enemy.
        EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True),
        EconSample(own_tier="FORCE", enemy_tier="SAVE", own_won=False),
        # Even: same tier both sides, regardless of which tier.
        EconSample(own_tier="SAVE", enemy_tier="SAVE", own_won=False),
        EconSample(own_tier="FULL_BUY", enemy_tier="FULL_BUY", own_won=True),
        # Unfavored: bought a lower tier than the enemy.
        EconSample(own_tier="ECO", enemy_tier="FULL_BUY", own_won=False),
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


def test_favor_outcome_matrix_row_with_no_samples_has_no_win_pct():
    matrix = build_favor_outcome_matrix([EconSample(own_tier="FULL_BUY", enemy_tier="ECO", own_won=True)])
    by_key = {row.key: row for row in matrix.rows}
    assert by_key["unfavored"].total == 0
    assert by_key["unfavored"].win_pct is None

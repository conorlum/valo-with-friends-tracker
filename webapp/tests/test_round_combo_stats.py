from app.models import Match, MatchPlayer, Round
from app.models.match import MatchSource, Team
from app.services.round_combo_stats import build_round_combo_stats, compute_round_combo_stats


def _mp(mp_id: int, team: Team, player_id: int) -> MatchPlayer:
    return MatchPlayer(id=mp_id, match_id=1, player_id=player_id, agent="Jett", team=team)


def _round(round_number: int, outcome: str | None) -> Round:
    return Round(round_number=round_number, outcome=outcome, player_stats=[])


def _match(
    round_outcomes: dict[int, str | None],
    team1_rounds_won: int, team2_rounds_won: int,
    team1_player_id: int = 100, team2_player_id: int = 200,
) -> Match:
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED,
        team1_rounds_won=team1_rounds_won, team2_rounds_won=team2_rounds_won,
    )
    match.match_players = [_mp(1, Team.TEAM_1, team1_player_id), _mp(2, Team.TEAM_2, team2_player_id)]
    match.rounds = [_round(rn, outcome) for rn, outcome in round_outcomes.items()]
    return match


# ---------------------------------------------------------------------------
# compute_round_combo_stats
# ---------------------------------------------------------------------------

def test_team1_perspective_and_team2_perspective_are_complementary():
    """Team 1 wins the pistol and round 2 (WW) and the match; team 2's
    mirror sample should be LL, also correctly scored against the match
    result (team 2 lost)."""
    match = _match(
        {1: "Team A Wins", 2: "Team A Wins", 13: "Team A Wins", 14: "Team A Wins"},
        team1_rounds_won=13, team2_rounds_won=3,
    )
    result = compute_round_combo_stats([match], roster_player_ids=set())

    first_half = result["all"]["first_half"]
    assert first_half["WW"] == {"total": 1, "win": 1}  # team 1: won both, won match
    assert first_half["LL"] == {"total": 1, "win": 0}  # team 2: lost both, lost match

    full = result["all"]["full"]
    assert full["WWWW"] == {"total": 1, "win": 1}
    assert full["LLLL"] == {"total": 1, "win": 0}


def test_pistol_round_with_no_decisive_outcome_excludes_the_match():
    match = _match({1: None, 2: "Team A Wins"}, team1_rounds_won=13, team2_rounds_won=3)
    result = compute_round_combo_stats([match], roster_player_ids=set())
    assert result["all"]["first_half"] == {}


def test_missing_round_excludes_the_match_from_that_granularity_only():
    """Round 13/14 never happened (match decided in the first half), so
    first_half still gets a sample but full does not."""
    match = _match({1: "Team A Wins", 2: "Team A Wins"}, team1_rounds_won=13, team2_rounds_won=0)
    result = compute_round_combo_stats([match], roster_player_ids=set())
    assert result["all"]["first_half"]["WW"] == {"total": 1, "win": 1}
    assert result["all"]["full"] == {}


def test_tied_match_excludes_both_teams():
    match = _match({1: "Team A Wins", 2: "Team A Wins"}, team1_rounds_won=12, team2_rounds_won=12)
    result = compute_round_combo_stats([match], roster_player_ids=set())
    assert result["all"]["first_half"] == {}


def test_friends_only_counts_samples_for_the_roster_players_own_team():
    match = _match(
        {1: "Team A Wins", 2: "Team A Wins"}, team1_rounds_won=13, team2_rounds_won=3,
        team1_player_id=100, team2_player_id=200,
    )
    friends_of_team1 = compute_round_combo_stats([match], roster_player_ids={100})
    assert friends_of_team1["friends"]["first_half"] == {"WW": {"total": 1, "win": 1}}

    friends_of_team2 = compute_round_combo_stats([match], roster_player_ids={200})
    assert friends_of_team2["friends"]["first_half"] == {"LL": {"total": 1, "win": 0}}
    # "all" is unaffected by roster membership either way
    assert friends_of_team2["all"]["first_half"] == {"WW": {"total": 1, "win": 1}, "LL": {"total": 1, "win": 0}}


# ---------------------------------------------------------------------------
# build_round_combo_stats
# ---------------------------------------------------------------------------

def test_build_stats_computes_win_pct_and_skips_unseen_combos():
    buckets = {"WW": {"total": 10, "win": 8}, "LL": {"total": 5, "win": 1}}
    stats = build_round_combo_stats(buckets, "first_half")
    assert stats.total_samples == 15
    assert len(stats.rows) == 2
    ww_row = next(r for r in stats.rows if r.outcomes == ["W", "W"])
    assert ww_row.total == 10
    assert ww_row.win_pct == 0.8


def test_build_stats_sorts_by_rounds_won_descending():
    buckets = {"LW": {"total": 1, "win": 1}, "WW": {"total": 1, "win": 1}, "LL": {"total": 1, "win": 0}}
    stats = build_round_combo_stats(buckets, "first_half")
    assert [r.outcomes for r in stats.rows] == [["W", "W"], ["L", "W"], ["L", "L"]]


def test_build_stats_empty_buckets_yields_no_rows():
    stats = build_round_combo_stats({}, "full")
    assert stats.rows == []
    assert stats.total_samples == 0
    assert stats.round_labels == ["Round 1 (Pistol)", "Round 2", "Round 13 (Pistol)", "Round 14"]

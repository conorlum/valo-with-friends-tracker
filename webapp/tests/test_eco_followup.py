from app.models import Match, MatchPlayer, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.services.eco_followup import (
    ECO_BUCKET_WIDTH,
    ECO_NUM_BUCKETS,
    ECO_TAIL_HIGH,
    ECO_TAIL_LOW,
    build_eco_followup_stats_from_aggregates,
    compute_pistol_win_followup_eco,
    eco_bucket_index,
)


def _mp(mp_id: int, team: Team, player_id: int) -> MatchPlayer:
    return MatchPlayer(id=mp_id, match_id=1, player_id=player_id, agent="Jett", team=team)


def _stat(match_player_id: int, loadout: int, kills: int) -> RoundPlayerStat:
    return RoundPlayerStat(match_player_id=match_player_id, loadout=loadout, kills=kills, deaths=0, assists=0)


def _round(
    round_number: int, outcome: str | None,
    team1_loadout: int | None = None, team1_kills: int = 0,
    team2_loadout: int | None = None, team2_kills: int = 0,
) -> Round:
    """team1/team2's whole-team loadout/kills for this round, each folded
    into a SINGLE RoundPlayerStat row (compute_pistol_win_followup_eco only
    ever sums across "players on this team", so one row carrying the team
    total is equivalent to five rows that sum to it)."""
    stats = []
    if team1_loadout is not None:
        stats.append(_stat(1, team1_loadout, team1_kills))
    if team2_loadout is not None:
        stats.append(_stat(2, team2_loadout, team2_kills))
    return Round(round_number=round_number, outcome=outcome, player_stats=stats)


def _match(
    rounds: list[Round], team1_player_id: int = 100, team2_player_id: int = 200,
    team1_rounds_won: int = 0, team2_rounds_won: int = 0,
) -> Match:
    """team1_rounds_won/team2_rounds_won default to a 0-0 tie (i.e.
    match_win returns None, so match_total stays 0) -- tests that care about
    the match-win outcome pass a decisive score explicitly."""
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED,
        team1_rounds_won=team1_rounds_won, team2_rounds_won=team2_rounds_won,
    )
    match.match_players = [_mp(1, Team.TEAM_1, team1_player_id), _mp(2, Team.TEAM_2, team2_player_id)]
    match.rounds = rounds
    return match


# ---------------------------------------------------------------------------
# eco_bucket_index
# ---------------------------------------------------------------------------

def test_eco_bucket_index_maps_below_tail_low_to_the_under_bucket():
    assert eco_bucket_index(0) == 0
    assert eco_bucket_index(ECO_TAIL_LOW - 1) == 0


def test_eco_bucket_index_maps_middle_range_to_1000_wide_buckets():
    assert eco_bucket_index(ECO_TAIL_LOW) == 1
    assert eco_bucket_index(ECO_TAIL_LOW + ECO_BUCKET_WIDTH - 1) == 1
    assert eco_bucket_index(ECO_TAIL_LOW + ECO_BUCKET_WIDTH) == 2


def test_eco_bucket_index_maps_at_or_above_tail_high_to_the_above_bucket():
    assert eco_bucket_index(ECO_TAIL_HIGH) == ECO_NUM_BUCKETS - 1
    assert eco_bucket_index(ECO_TAIL_HIGH + 5000) == ECO_NUM_BUCKETS - 1


# ---------------------------------------------------------------------------
# compute_pistol_win_followup_eco
# ---------------------------------------------------------------------------

def test_pistol_win_and_followup_round_win_both_recorded():
    match = _match([
        _round(1, "Team A Wins"),
        _round(2, "Team A Wins", team1_loadout=13500, team1_kills=3),
        _round(3, "Team A Wins", team1_loadout=1000, team1_kills=2),
        _round(4, "Team B Wins", team1_loadout=1000, team1_kills=1),
        _round(5, "Team A Wins", team1_loadout=1000, team1_kills=4),
    ])

    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())

    assert result["friends"]["buckets"] == []
    idx = eco_bucket_index(13500)
    [[bucket_idx, total, win, wins_ratio_sum_2, wins_ratio_sum_4, match_total, match_win_count]] = result["all"][
        "buckets"
    ]
    assert bucket_idx == idx
    assert total == 1
    assert win == 1  # round 2 itself was won
    assert wins_ratio_sum_2 == 2 / 2  # rounds 2, 3 both won
    assert wins_ratio_sum_4 == 3 / 4  # rounds 2, 3, 5 won out of 4
    assert match_total == 0  # match is a 0-0 tie by default -- excluded, not a loss
    assert match_win_count == 0


def test_sample_only_counted_for_friends_when_winning_team_has_a_roster_player():
    match = _match([_round(1, "Team A Wins"), _round(2, "Team A Wins", team1_loadout=13500, team1_kills=1)])

    friends_of_winner = compute_pistol_win_followup_eco([match], roster_player_ids={100})
    assert len(friends_of_winner["friends"]["buckets"]) == 1
    assert len(friends_of_winner["all"]["buckets"]) == 1

    friends_of_loser = compute_pistol_win_followup_eco([match], roster_player_ids={200})
    assert friends_of_loser["friends"]["buckets"] == []
    assert len(friends_of_loser["all"]["buckets"]) == 1


def test_pistol_round_with_no_outcome_is_excluded():
    match = _match([_round(1, None), _round(2, "Team A Wins", team1_loadout=13500, team1_kills=1)])
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    assert result["all"]["buckets"] == []


def test_followup_round_with_no_recorded_loadout_is_excluded():
    match = _match([_round(1, "Team A Wins"), _round(2, "Team A Wins")])  # no loadout on round 2
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    assert result["all"]["buckets"] == []


def test_kills_and_wins_rate_normalize_over_a_partial_window():
    """Match ends after round 3 (e.g. 13-3) -- only 2 of the next-4 rounds
    (2, 3) exist. The rate should divide by 2, not by 4."""
    match = _match([
        _round(1, "Team A Wins"),
        _round(2, "Team A Wins", team1_loadout=13500, team1_kills=4),
        _round(3, "Team B Wins", team1_loadout=1000, team1_kills=2),
    ])
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    [[_, total, win, wins_ratio_sum_2, wins_ratio_sum_4, _, _]] = result["all"]["buckets"]
    assert total == 1
    assert wins_ratio_sum_2 == 1 / 2  # only round 2 of the 2 available was won
    assert wins_ratio_sum_4 == 1 / 2  # only round 2 of the 2 available was won


def test_both_pistol_rounds_contribute_independent_samples():
    match = _match([
        _round(1, "Team A Wins"),
        _round(2, "Team A Wins", team1_loadout=12000, team1_kills=1),
        _round(13, "Team B Wins"),
        _round(14, "Team B Wins", team2_loadout=16000, team2_kills=1),
    ])
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    assert len(result["all"]["buckets"]) == 2


def test_match_win_counted_when_the_pistol_winning_team_wins_the_match():
    match = _match(
        [_round(1, "Team A Wins"), _round(2, "Team A Wins", team1_loadout=13500, team1_kills=1)],
        team1_rounds_won=13, team2_rounds_won=5,
    )
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    [[_, total, _, _, _, match_total, match_win_count]] = result["all"]["buckets"]
    assert total == 1
    assert match_total == 1
    assert match_win_count == 1


def test_match_win_counted_as_loss_when_the_pistol_winning_team_loses_the_match():
    match = _match(
        [_round(1, "Team A Wins"), _round(2, "Team A Wins", team1_loadout=13500, team1_kills=1)],
        team1_rounds_won=11, team2_rounds_won=13,
    )
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    [[_, total, _, _, _, match_total, match_win_count]] = result["all"]["buckets"]
    assert total == 1
    assert match_total == 1
    assert match_win_count == 0


def test_match_win_excluded_from_match_total_when_the_match_is_tied():
    match = _match(
        [_round(1, "Team A Wins"), _round(2, "Team A Wins", team1_loadout=13500, team1_kills=1)],
        team1_rounds_won=12, team2_rounds_won=12,
    )
    result = compute_pistol_win_followup_eco([match], roster_player_ids=set())
    [[_, total, _, _, _, match_total, match_win_count]] = result["all"]["buckets"]
    assert total == 1  # the sample still counts for the round-level columns
    assert match_total == 0
    assert match_win_count == 0


# ---------------------------------------------------------------------------
# build_eco_followup_stats_from_aggregates
# ---------------------------------------------------------------------------

def test_build_stats_computes_rates_and_label():
    variant = {
        "buckets": [
            [0, 10, 4, 25.0, 6.0, 8, 5],
            [1, 10, 4, 25.0, 6.0, 8, 5],
            [ECO_NUM_BUCKETS - 1, 10, 4, 25.0, 6.0, 8, 5],
        ]
    }
    stats = build_eco_followup_stats_from_aggregates(variant)
    under_bucket, first_middle_bucket, above_bucket = stats.buckets

    assert under_bucket.label == f"Under {ECO_TAIL_LOW:,}"
    assert first_middle_bucket.label == f"{ECO_TAIL_LOW:,}-{ECO_TAIL_LOW + ECO_BUCKET_WIDTH:,}"
    assert above_bucket.label == f"{ECO_TAIL_HIGH:,}+"

    assert under_bucket.total == 10
    assert under_bucket.immediate_win_pct == 0.4
    assert under_bucket.avg_win_rate_next2 == 2.5
    assert under_bucket.avg_win_rate_next4 == 0.6
    assert under_bucket.match_total == 8
    assert under_bucket.match_win_pct == 5 / 8
    assert stats.total_samples == 30


def test_match_win_pct_is_none_when_no_sample_in_the_bucket_had_a_decisive_match():
    variant = {"buckets": [[0, 10, 4, 25.0, 6.0, 0, 0]]}
    stats = build_eco_followup_stats_from_aggregates(variant)
    bucket = stats.buckets[0]
    assert bucket.match_total == 0
    assert bucket.match_win_pct is None
    assert bucket.match_fill is None


def test_best_bucket_selection_ignores_buckets_below_min_sample_size():
    """Bucket 0 has a perfect rate but only 3 samples; bucket 1 has a worse
    rate but enough samples to be trusted -- bucket 1 should win."""
    variant = {"buckets": [[0, 3, 3, 30.0, 3.0, 3, 3], [1, 50, 30, 100.0, 30.0, 50, 20]]}
    stats = build_eco_followup_stats_from_aggregates(variant)
    assert stats.best_immediate_win_bucket.total == 50
    assert stats.best_match_win_bucket.total == 50


def test_best_bucket_is_none_when_no_bucket_meets_the_threshold():
    variant = {"buckets": [[0, 3, 3, 3.0, 3.0, 3, 3]]}
    stats = build_eco_followup_stats_from_aggregates(variant)
    assert stats.best_immediate_win_bucket is None
    assert stats.best_win_rate_next2_bucket is None
    assert stats.best_win_rate_next4_bucket is None
    assert stats.best_match_win_bucket is None


def test_empty_buckets_total_zero_are_skipped():
    variant = {"buckets": [[0, 0, 0, 0.0, 0.0, 0, 0], [1, 5, 2, 10.0, 3.0, 5, 2]]}
    stats = build_eco_followup_stats_from_aggregates(variant)
    assert len(stats.buckets) == 1
    assert stats.buckets[0].total == 5

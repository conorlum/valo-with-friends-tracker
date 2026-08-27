from app.models import Match, MatchPlayer, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.services.enemy_at_11_response import (
    ECO_LOADOUT_MAX,
    FORCE_SPEND_RATIO,
    FULL_BUY_LOADOUT_MIN,
    MIN_SAMPLES_FOR_BEST,
    build_enemy_at_11_response_stats,
    classify_buy,
    compute_enemy_at_11_response_stats,
)


def _mp(mp_id: int, team: Team, player_id: int) -> MatchPlayer:
    return MatchPlayer(id=mp_id, match_id=1, player_id=player_id, agent="Jett", team=team)


def _stat(match_player_id: int, loadout: int, remaining: int = 0) -> RoundPlayerStat:
    return RoundPlayerStat(match_player_id=match_player_id, loadout=loadout, remaining=remaining, kills=0, deaths=0, assists=0)


def _round(
    round_number: int, outcome: str | None,
    team1_loadout: int | None = None, team1_remaining: int = 0,
    team2_loadout: int | None = None, team2_remaining: int = 0,
) -> Round:
    """Both teams' whole-team loadout/remaining folded into a single
    RoundPlayerStat row per team -- the module only ever sums across
    "players on this team", so one row carrying the team total is
    equivalent to five rows that sum to it."""
    stats = []
    if team1_loadout is not None:
        stats.append(_stat(1, team1_loadout, team1_remaining))
    if team2_loadout is not None:
        stats.append(_stat(2, team2_loadout, team2_remaining))
    return Round(round_number=round_number, outcome=outcome, player_stats=stats)


def _match(
    rounds: list[Round], team1_player_id: int = 100, team2_player_id: int = 200,
    team1_rounds_won: int = 0, team2_rounds_won: int = 0,
) -> Match:
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED,
        team1_rounds_won=team1_rounds_won, team2_rounds_won=team2_rounds_won,
    )
    match.match_players = [_mp(1, Team.TEAM_1, team1_player_id), _mp(2, Team.TEAM_2, team2_player_id)]
    match.rounds = rounds
    return match


def _rounds_to(n: int, winner_outcome: str) -> list[Round]:
    """n rounds, all won by the same side (e.g. all "Team A Wins"), so the
    winner's cumulative round-win count reaches n at round n."""
    return [_round(rn, winner_outcome) for rn in range(1, n + 1)]


# ---------------------------------------------------------------------------
# classify_buy
# ---------------------------------------------------------------------------

def test_classify_buy_below_eco_max_is_eco_regardless_of_ratio():
    assert classify_buy(ECO_LOADOUT_MAX - 1, spend_ratio=1.0) == "eco"


def test_classify_buy_at_or_above_full_buy_min_is_full_regardless_of_ratio():
    assert classify_buy(FULL_BUY_LOADOUT_MIN, spend_ratio=0.01) == "full"


def test_classify_buy_midrange_high_ratio_is_force():
    assert classify_buy(ECO_LOADOUT_MAX, spend_ratio=FORCE_SPEND_RATIO) == "force"


def test_classify_buy_midrange_low_ratio_is_half():
    assert classify_buy(ECO_LOADOUT_MAX, spend_ratio=FORCE_SPEND_RATIO - 0.01) == "half"


# ---------------------------------------------------------------------------
# compute_enemy_at_11_response_stats
# ---------------------------------------------------------------------------

def test_response_round_right_after_enemy_reaches_11_is_sampled():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=15000, team2_remaining=3000)
    ]
    match = _match(rounds)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())

    # total_loadout=15000 is mid-range; spend_ratio=15000/18000=0.833 < FORCE_SPEND_RATIO -> "half"
    assert result["all"]["half"]["immediate"] == {"total": 1, "win": 1}
    assert result["all"]["force"]["immediate"] == {"total": 0, "win": 0}


def test_trigger_requires_the_response_round_to_exist():
    rounds = _rounds_to(11, "Team A Wins")  # no round 12 recorded
    match = _match(rounds)
    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    for tier in result["all"].values():
        assert tier["immediate"]["total"] == 0


def test_sample_excluded_when_no_recorded_loadout_stats():
    rounds = _rounds_to(11, "Team A Wins") + [_round(12, "Team B Wins")]  # no loadout on round 12
    match = _match(rounds)
    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    for tier in result["all"].values():
        assert tier["immediate"]["total"] == 0


def test_sample_only_counted_for_friends_when_responding_team_has_a_roster_player():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=22000, team2_remaining=1000)
    ]
    match = _match(rounds)

    friends_of_responder = compute_enemy_at_11_response_stats([match], roster_player_ids={200})
    assert friends_of_responder["friends"]["full"]["immediate"] == {"total": 1, "win": 1}

    friends_of_enemy = compute_enemy_at_11_response_stats([match], roster_player_ids={100})
    assert friends_of_enemy["friends"]["full"]["immediate"] == {"total": 0, "win": 0}
    assert friends_of_enemy["all"]["full"]["immediate"] == {"total": 1, "win": 1}


def test_next_round_and_match_win_recorded_alongside_immediate():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=22000, team2_remaining=1000),
        _round(13, "Team B Wins", team2_loadout=22000, team2_remaining=1000),
    ]
    match = _match(rounds, team1_rounds_won=11, team2_rounds_won=13)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())

    full = result["all"]["full"]
    assert full["immediate"] == {"total": 1, "win": 1}
    assert full["next"] == {"total": 1, "win": 1}
    assert full["match"] == {"total": 1, "win": 1}  # team-2 won both the response round and the match


def test_next_round_missing_is_excluded_from_next_but_not_immediate():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=22000, team2_remaining=1000)
    ]  # match ends here, no round 13
    match = _match(rounds)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    full = result["all"]["full"]
    assert full["immediate"] == {"total": 1, "win": 1}
    assert full["next"] == {"total": 0, "win": 0}


def test_both_teams_reaching_11_contribute_independent_samples():
    """A close match (final 13-11) has BOTH teams pass through 11 wins, so
    each team gets its own response sample."""
    rounds = (
        _rounds_to(11, "Team A Wins")
        + [_round(12, "Team B Wins", team2_loadout=22000, team2_remaining=1000)]
        + [_round(rn, "Team B Wins") for rn in range(13, 23)]  # team-2 climbs to 11 wins by round 22
        + [_round(23, "Team A Wins", team1_loadout=8000, team1_remaining=0)]  # team-1's response, eco
    )
    match = _match(rounds, team1_rounds_won=12, team2_rounds_won=11)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    assert result["all"]["full"]["immediate"]["total"] == 1  # team-2's response to team-1 hitting 11
    assert result["all"]["eco"]["immediate"]["total"] == 1  # team-1's response to team-2 hitting 11


# ---------------------------------------------------------------------------
# build_enemy_at_11_response_stats
# ---------------------------------------------------------------------------

def _tier(total: int, win: int, next_total: int = 0, next_win: int = 0, match_total: int = 0, match_win_count: int = 0) -> dict:
    return {
        "immediate": {"total": total, "win": win},
        "next": {"total": next_total, "win": next_win},
        "match": {"total": match_total, "win": match_win_count},
    }


def test_build_stats_computes_rates_and_labels():
    variant = {
        "eco": _tier(10, 4, 8, 5, 6, 2),
        "half": _tier(0, 0),
        "force": _tier(0, 0),
        "full": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    [row] = stats.rows
    assert row.label == "Eco / Save"
    assert row.total == 10
    assert row.immediate_win_pct == 0.4
    assert row.next_win_pct == 5 / 8
    assert row.match_win_pct == 2 / 6
    assert stats.total_samples == 10


def test_build_stats_range_labels_show_the_actual_thresholds():
    variant = {
        "eco": _tier(10, 4),
        "half": _tier(10, 4),
        "force": _tier(10, 4),
        "full": _tier(10, 4),
    }
    stats = build_enemy_at_11_response_stats(variant)
    by_category = {row.category: row.range_label for row in stats.rows}
    assert by_category["eco"] == f"under {ECO_LOADOUT_MAX:,} credits"
    assert by_category["half"] == f"{ECO_LOADOUT_MAX:,}-{FULL_BUY_LOADOUT_MIN:,} credits, under 85% spent"
    assert by_category["force"] == f"{ECO_LOADOUT_MAX:,}-{FULL_BUY_LOADOUT_MIN:,} credits, 85%+ spent"
    assert by_category["full"] == f"{FULL_BUY_LOADOUT_MIN:,}+ credits"


def test_build_stats_skips_empty_tiers():
    variant = {
        "eco": _tier(0, 0),
        "half": _tier(5, 2),
        "force": _tier(0, 0),
        "full": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    assert len(stats.rows) == 1
    assert stats.rows[0].label == "Half Buy"


def test_best_row_selection_ignores_tiers_below_min_sample_size():
    variant = {
        "eco": _tier(3, 3, match_total=3, match_win_count=3),  # perfect but too few samples
        "half": _tier(MIN_SAMPLES_FOR_BEST, MIN_SAMPLES_FOR_BEST - 5, match_total=MIN_SAMPLES_FOR_BEST, match_win_count=MIN_SAMPLES_FOR_BEST - 10),
        "force": _tier(0, 0),
        "full": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    assert stats.best_immediate_row.label == "Half Buy"
    assert stats.best_match_row.label == "Half Buy"


def test_best_row_is_none_when_no_tier_meets_the_threshold():
    variant = {
        "eco": _tier(3, 3),
        "half": _tier(0, 0),
        "force": _tier(0, 0),
        "full": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    assert stats.best_immediate_row is None
    assert stats.best_next_row is None
    assert stats.best_match_row is None


def test_next_and_match_win_pct_are_none_when_no_sample_has_that_outcome():
    variant = {
        "eco": _tier(10, 4),
        "half": _tier(0, 0),
        "force": _tier(0, 0),
        "full": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    row = stats.rows[0]
    assert row.next_win_pct is None
    assert row.next_fill is None
    assert row.match_win_pct is None
    assert row.match_fill is None

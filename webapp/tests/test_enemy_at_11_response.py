from app.models import Match, MatchPlayer, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.services.enemy_at_11_response import (
    FORCE_SPEND_RATIO,
    FULL_BUY_LOADOUT_MIN,
    FULL_SAVE_CEILING_TOTAL,
    FULL_SAVE_LOADOUT_MAX,
    MIN_SAMPLES_FOR_BEST,
    SAVE_TARGET_TOTAL,
    build_enemy_at_11_response_stats,
    classify_response,
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
# classify_response
# ---------------------------------------------------------------------------

def test_classify_response_full_buy_loadout_is_excluded_regardless_of_everything():
    assert classify_response(FULL_BUY_LOADOUT_MIN, total_remaining=0, next_round_bonus_total=99999) is None


def test_classify_response_high_spend_ratio_is_force_buy():
    loadout = 8000
    # remaining chosen so loadout / (loadout + remaining) == FORCE_SPEND_RATIO exactly
    remaining = int(loadout / FORCE_SPEND_RATIO - loadout)
    assert classify_response(loadout, remaining, next_round_bonus_total=0) == "force_buy"


def test_classify_response_low_spend_ratio_and_enough_projected_is_full_save():
    total_remaining = SAVE_TARGET_TOTAL  # already at target with zero bonus needed
    assert classify_response(1000, total_remaining, next_round_bonus_total=0) == "full_save"


def test_classify_response_projection_includes_the_real_bonus():
    """Banked alone falls short of the target, but banked + the real
    next-round bonus clears it."""
    total_remaining = SAVE_TARGET_TOTAL - 1000
    assert classify_response(1000, total_remaining, next_round_bonus_total=1000) == "full_save"
    assert classify_response(1000, total_remaining, next_round_bonus_total=999) is None


def test_classify_response_excluded_when_neither_condition_holds():
    """Low spend ratio, but not enough banked+bonus to hit the save target --
    just broke, not force-buying and not genuinely saving."""
    assert classify_response(100, total_remaining=1000, next_round_bonus_total=0) is None


def test_classify_response_ceiling_excludes_a_team_that_could_afford_both():
    """Remaining alone (no bonus needed) already covers a rifle for
    everyone AND still clears the save target afterward -- not a forced
    save, just spare wealth."""
    total_remaining = FULL_SAVE_CEILING_TOTAL
    assert classify_response(0, total_remaining, next_round_bonus_total=0) is None


def test_classify_response_loadout_cap_excludes_carried_over_gear():
    """Loadout reflects what's carried this round, including a free weapon
    kept from surviving the previous round -- a sample at/above
    FULL_SAVE_LOADOUT_MAX isn't a real save even if the bank/bonus math
    would otherwise qualify it, since real guns are still on the field."""
    assert classify_response(FULL_SAVE_LOADOUT_MAX, SAVE_TARGET_TOTAL, next_round_bonus_total=0) is None


def test_classify_response_force_buy_takes_precedence_over_full_save():
    """High spend ratio wins even if the leftover money would also have
    projected a full_save."""
    loadout = 8000
    remaining = int(loadout / FORCE_SPEND_RATIO - loadout)
    assert classify_response(loadout, remaining, next_round_bonus_total=SAVE_TARGET_TOTAL) == "force_buy"


# ---------------------------------------------------------------------------
# compute_enemy_at_11_response_stats
# ---------------------------------------------------------------------------

def test_response_round_right_after_enemy_reaches_11_is_sampled():
    rounds = _rounds_to(11, "Team A Wins") + [_round(12, "Team B Wins", team2_loadout=9000, team2_remaining=100)]
    match = _match(rounds)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())

    assert result["all"]["force_buy"]["immediate"] == {"total": 1, "win": 1}
    assert result["all"]["full_save"]["immediate"] == {"total": 0, "win": 0}


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


def test_sample_excluded_when_this_round_was_already_a_full_buy():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=FULL_BUY_LOADOUT_MIN, team2_remaining=0)
    ]
    match = _match(rounds)
    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    for tier in result["all"].values():
        assert tier["immediate"]["total"] == 0


def test_full_save_uses_the_real_win_bonus_when_response_round_is_won():
    """Team wins the response round -> next round's real bonus is the flat
    WIN_BONUS (3000/player = 15000 team total). Banked $8500 + 15000 clears
    the $23500 save target; a naive flat-loss-bonus guess would not."""
    rounds = _rounds_to(11, "Team A Wins") + [_round(12, "Team B Wins", team2_loadout=500, team2_remaining=8500)]
    match = _match(rounds)
    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    assert result["all"]["full_save"]["immediate"] == {"total": 1, "win": 1}


def test_sample_only_counted_for_friends_when_responding_team_has_a_roster_player():
    rounds = _rounds_to(11, "Team A Wins") + [_round(12, "Team B Wins", team2_loadout=9000, team2_remaining=100)]
    match = _match(rounds)

    friends_of_responder = compute_enemy_at_11_response_stats([match], roster_player_ids={200})
    assert friends_of_responder["friends"]["force_buy"]["immediate"] == {"total": 1, "win": 1}

    friends_of_enemy = compute_enemy_at_11_response_stats([match], roster_player_ids={100})
    assert friends_of_enemy["friends"]["force_buy"]["immediate"] == {"total": 0, "win": 0}
    assert friends_of_enemy["all"]["force_buy"]["immediate"] == {"total": 1, "win": 1}


def test_next_round_and_match_win_recorded_alongside_immediate():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=9000, team2_remaining=100),
        _round(13, "Team B Wins"),
    ]
    match = _match(rounds, team1_rounds_won=11, team2_rounds_won=13)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())

    force_buy = result["all"]["force_buy"]
    assert force_buy["immediate"] == {"total": 1, "win": 1}
    assert force_buy["next"] == {"total": 1, "win": 1}
    assert force_buy["match"] == {"total": 1, "win": 1}  # team-2 won both the response round and the match


def test_next_round_missing_is_excluded_from_next_but_not_immediate():
    rounds = _rounds_to(11, "Team A Wins") + [
        _round(12, "Team B Wins", team2_loadout=9000, team2_remaining=100)
    ]  # match ends here, no round 13
    match = _match(rounds)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    force_buy = result["all"]["force_buy"]
    assert force_buy["immediate"] == {"total": 1, "win": 1}
    assert force_buy["next"] == {"total": 0, "win": 0}


def test_both_teams_reaching_11_contribute_independent_samples():
    """A close match (final 13-11) has BOTH teams pass through 11 wins, so
    each team gets its own response sample."""
    rounds = (
        _rounds_to(11, "Team A Wins")
        + [_round(12, "Team B Wins", team2_loadout=9000, team2_remaining=100)]
        + [_round(rn, "Team B Wins") for rn in range(13, 23)]  # team-2 climbs to 11 wins by round 22
        + [_round(23, "Team A Wins", team1_loadout=1000, team1_remaining=SAVE_TARGET_TOTAL)]  # team-1's response, full save
    )
    match = _match(rounds, team1_rounds_won=12, team2_rounds_won=11)

    result = compute_enemy_at_11_response_stats([match], roster_player_ids=set())
    assert result["all"]["force_buy"]["immediate"]["total"] == 1  # team-2's response to team-1 hitting 11
    assert result["all"]["full_save"]["immediate"]["total"] == 1  # team-1's response to team-2 hitting 11


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
        "force_buy": _tier(10, 4, 8, 5, 6, 2),
        "full_save": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    [row] = stats.rows
    assert row.label == "Force Buy"
    assert row.total == 10
    assert row.immediate_win_pct == 0.4
    assert row.next_win_pct == 5 / 8
    assert row.match_win_pct == 2 / 6
    assert stats.total_samples == 10


def test_build_stats_range_labels_show_the_actual_thresholds():
    variant = {
        "force_buy": _tier(10, 4),
        "full_save": _tier(10, 4),
    }
    stats = build_enemy_at_11_response_stats(variant)
    by_category = {row.category: row.range_label for row in stats.rows}
    force_pct = int(round(FORCE_SPEND_RATIO * 100))
    assert by_category["force_buy"] == f"{force_pct}%+ of available money spent this round"
    assert by_category["full_save"] == (
        f"under {FULL_SAVE_LOADOUT_MAX:,} loadout this round, banked + next round's real credit bonus "
        f"projects to {SAVE_TARGET_TOTAL:,}+ (~4,700/player)"
    )


def test_build_stats_skips_empty_tiers():
    variant = {
        "force_buy": _tier(0, 0),
        "full_save": _tier(5, 2),
    }
    stats = build_enemy_at_11_response_stats(variant)
    assert len(stats.rows) == 1
    assert stats.rows[0].label == "Full Save"


def test_best_row_selection_ignores_tiers_below_min_sample_size():
    variant = {
        "force_buy": _tier(3, 3, match_total=3, match_win_count=3),  # perfect but too few samples
        "full_save": _tier(
            MIN_SAMPLES_FOR_BEST, MIN_SAMPLES_FOR_BEST - 5,
            match_total=MIN_SAMPLES_FOR_BEST, match_win_count=MIN_SAMPLES_FOR_BEST - 10,
        ),
    }
    stats = build_enemy_at_11_response_stats(variant)
    assert stats.best_immediate_row.label == "Full Save"
    assert stats.best_match_row.label == "Full Save"


def test_best_row_is_none_when_no_tier_meets_the_threshold():
    variant = {
        "force_buy": _tier(3, 3),
        "full_save": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    assert stats.best_immediate_row is None
    assert stats.best_next_row is None
    assert stats.best_match_row is None


def test_next_and_match_win_pct_are_none_when_no_sample_has_that_outcome():
    variant = {
        "force_buy": _tier(10, 4),
        "full_save": _tier(0, 0),
    }
    stats = build_enemy_at_11_response_stats(variant)
    row = stats.rows[0]
    assert row.next_win_pct is None
    assert row.next_fill is None
    assert row.match_win_pct is None
    assert row.match_fill is None

"""Correctness tests for the Step 2b write-path builders: given
load_player_match_data's already-hydrated relationships (Round.player_stats,
Match.match_players.player) instead of get_player_profile's own three extra
queries, build_player_profile_from_match_data must derive the SAME
KDA/round-outcome/teammate-name inputs. Fixtures follow
tests/test_economy_graphs.py's existing pattern: plain ORM model
construction with relationships assigned by hand, no DB/session involved.
"""

from app.models import ImpactScore, Match, MatchPlayer, Player, Round
from app.models.impact_score import _SCALAR_KEYS
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.services.economy_graphs import compute_econ_aggregates, econ_samples_from_data
from app.services.player_profile_types import build_player_profile_from_match_data, compute_pistol_match_stats


def _build_one_match_two_players():
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED, map_name="Bind",
        team1_rounds_won=13, team2_rounds_won=7,
    )
    me = Player(id=100, display_name="Me#456")
    ally = Player(id=200, display_name="Ally#123")

    mp_me = MatchPlayer(id=1, match_id=1, player_id=100, agent="Jett", team=Team.TEAM_1)
    mp_me.player = me
    mp_ally = MatchPlayer(id=2, match_id=1, player_id=200, agent="Sova", team=Team.TEAM_1)
    mp_ally.player = ally
    match.match_players = [mp_me, mp_ally]

    round1 = Round(id=1, match_id=1, round_number=1, outcome="Team A Wins")
    round1.player_stats = [
        RoundPlayerStat(match_player_id=1, kills=3, deaths=1, assists=2, loadout=4000),
        RoundPlayerStat(match_player_id=2, kills=1, deaths=0, assists=0, loadout=4000),
    ]
    match.rounds = [round1]
    mp_me.match = match
    mp_ally.match = match

    return match, me, mp_me, mp_ally


def _impact_score(**overrides):
    """An ImpactScore with every breakdown column zeroed.

    The breakdown scalars used to live in one JSON column that tests could
    pass as `breakdown={...}`; they are separate columns now, and SQLAlchemy
    applies column defaults at INSERT time rather than at construction, so an
    in-memory instance needs them set explicitly.
    """
    fields = {key: 0 for key in _SCALAR_KEYS}
    fields.update(
        round_id=1, match_player_id=1, kill_impact=10, death_impact=-2, impact=8
    )
    fields.update(overrides)
    return ImpactScore(**fields)


def test_kda_is_summed_from_round_player_stats_not_a_separate_query():
    match, me, mp_me, _ = _build_one_match_two_players()
    score = _impact_score()

    profile = build_player_profile_from_match_data(me, [mp_me], {1: [score]})

    assert len(profile.matches) == 1
    m = profile.matches[0]
    assert (m.kills, m.deaths, m.assists) == (3, 1, 2)


def test_round_outcome_is_read_from_hydrated_rounds_not_a_separate_query():
    match, me, mp_me, _ = _build_one_match_two_players()
    # mp_me is TEAM_1 ("Team A Wins" in the outcome string) -- winning side.
    score = _impact_score()

    profile = build_player_profile_from_match_data(me, [mp_me], {1: [score]})

    assert profile.matches[0].win is True


def test_traded_teammate_name_is_read_from_hydrated_match_players():
    match, me, mp_me, mp_ally = _build_one_match_two_players()
    score = _impact_score(trade_detail={"t": {str(mp_ally.id): 1}})

    profile = build_player_profile_from_match_data(me, [mp_me], {1: [score]})

    assert profile.top_traded_teammate == [("Ally#123", 1)]


def test_matches_with_no_impact_score_are_excluded():
    match, me, mp_me, _ = _build_one_match_two_players()

    profile = build_player_profile_from_match_data(me, [mp_me], {})

    assert profile.matches == []


def test_output_matches_get_player_profile_shaped_fields():
    """Sanity check that the from-data path and the live-query path
    (get_player_profile) agree on what a MatchBreakdown looks like for
    identical underlying data -- not a full behavioral-equivalence proof
    (that would need a DB), but guards the field-by-field shape."""
    match, me, mp_me, _ = _build_one_match_two_players()
    score = _impact_score(econ_kill=2, clutch_kill=1)

    profile = build_player_profile_from_match_data(me, [mp_me], {1: [score]})

    assert profile.overall_average_impact == 8.0
    assert profile.avg_econ_kill == 2.0
    assert profile.avg_clutch_kill == 1.0
    assert profile.agent_counts["Jett"] == 1
    assert profile.matches[0].match.external_id == "ext-1"
    assert profile.matches[0].agent == "Jett"
    assert profile.matches[0].team == "team-1"


# ---------------------------------------------------------------------------
# econ_samples_from_data / compute_econ_aggregates (Step 2b/2a write path)
# ---------------------------------------------------------------------------

def test_econ_samples_from_data_matches_player_econ_samples_shape():
    match, me, mp_me, _ = _build_one_match_two_players()
    # Round 1 isn't a pistol round in this fixture's numbering (round_number=1
    # IS actually a pistol round per PISTOL_ROUNDS -- use round_number=3 to
    # exercise a normal buy-tier round instead).
    match.rounds[0].round_number = 3

    samples = econ_samples_from_data([mp_me])

    assert len(samples) == 1
    assert samples[0].own_won is True  # Team A won, mp_me is team-1


def test_compute_econ_aggregates_buckets_by_tier_pair_and_pistol():
    match, me, mp_me, _ = _build_one_match_two_players()
    match.rounds[0].round_number = 3  # non-pistol
    samples = econ_samples_from_data([mp_me])

    aggregates = compute_econ_aggregates(samples)

    assert set(aggregates.keys()) == {"tier_pairs", "pistol", "loadout_buckets"}
    total_tier_rounds = sum(b["total"] for b in aggregates["tier_pairs"].values())
    assert total_tier_rounds == 1
    assert aggregates["pistol"]["total"] == 0  # round 3 isn't a pistol round


# ---------------------------------------------------------------------------
# compute_pistol_match_stats
# ---------------------------------------------------------------------------

def _match_player_with_pistol_rounds(
    *, round1_won: bool | None, round13_won: bool | None, team1_rounds_won: int, team2_rounds_won: int,
) -> MatchPlayer:
    """A single team-1 MatchPlayer in a match with a round 1 and a round 13
    whose outcomes are controlled independently -- None means "no outcome
    recorded" (excluded from the pistol sample), matching _winner_side's own
    contract. Only what compute_pistol_match_stats reads (match.rounds'
    round_number/outcome, match.team1_rounds_won/team2_rounds_won,
    match_player.team) is populated."""
    match = Match(
        id=1, external_id="ext-1", source=MatchSource.SCRAPED,
        team1_rounds_won=team1_rounds_won, team2_rounds_won=team2_rounds_won,
    )

    def _outcome(won: bool | None) -> str | None:
        if won is None:
            return None
        return "Team A Wins" if won else "Team B Wins"

    match.rounds = [
        Round(id=1, match_id=1, round_number=1, outcome=_outcome(round1_won)),
        Round(id=2, match_id=1, round_number=13, outcome=_outcome(round13_won)),
    ]
    mp = MatchPlayer(id=1, match_id=1, player_id=100, agent="Jett", team=Team.TEAM_1)
    mp.match = match
    return mp


_EMPTY_PISTOL_MATCH_STATS = {
    "lost_both_total": 0, "lost_both_wins": 0,
    "won_one_total": 0, "won_one_wins": 0,
    "won_both_total": 0, "won_both_wins": 0,
}


def test_pistol_match_stats_buckets_a_lost_both_win_into_lost_both():
    mp = _match_player_with_pistol_rounds(
        round1_won=False, round13_won=False, team1_rounds_won=13, team2_rounds_won=7,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == {**_EMPTY_PISTOL_MATCH_STATS, "lost_both_total": 1, "lost_both_wins": 1}


def test_pistol_match_stats_buckets_a_single_pistol_win_into_won_one():
    mp = _match_player_with_pistol_rounds(
        round1_won=True, round13_won=False, team1_rounds_won=13, team2_rounds_won=7,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == {**_EMPTY_PISTOL_MATCH_STATS, "won_one_total": 1, "won_one_wins": 1}


def test_pistol_match_stats_buckets_the_other_single_pistol_win_into_won_one_too():
    """round13_won=True, round1_won=False should land in the SAME bucket as
    round1_won=True, round13_won=False -- won_one only counts HOW MANY
    pistols were won, not which one."""
    mp = _match_player_with_pistol_rounds(
        round1_won=False, round13_won=True, team1_rounds_won=13, team2_rounds_won=7,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == {**_EMPTY_PISTOL_MATCH_STATS, "won_one_total": 1, "won_one_wins": 1}


def test_pistol_match_stats_buckets_a_double_pistol_win_into_won_both():
    mp = _match_player_with_pistol_rounds(
        round1_won=True, round13_won=True, team1_rounds_won=13, team2_rounds_won=7,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == {**_EMPTY_PISTOL_MATCH_STATS, "won_both_total": 1, "won_both_wins": 1}


def test_pistol_match_stats_counts_a_pistol_win_followed_by_a_match_loss():
    mp = _match_player_with_pistol_rounds(
        round1_won=True, round13_won=False, team1_rounds_won=7, team2_rounds_won=13,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == {**_EMPTY_PISTOL_MATCH_STATS, "won_one_total": 1, "won_one_wins": 0}


def test_pistol_match_stats_counts_a_lost_both_pistols_match_loss():
    mp = _match_player_with_pistol_rounds(
        round1_won=False, round13_won=False, team1_rounds_won=7, team2_rounds_won=13,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == {**_EMPTY_PISTOL_MATCH_STATS, "lost_both_total": 1, "lost_both_wins": 0}


def test_pistol_match_stats_excludes_a_tied_match():
    mp = _match_player_with_pistol_rounds(
        round1_won=True, round13_won=True, team1_rounds_won=12, team2_rounds_won=12,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == _EMPTY_PISTOL_MATCH_STATS


def test_pistol_match_stats_excludes_a_match_with_one_pistol_round_missing_its_outcome():
    """With only two rounds to place, a missing outcome makes the bucket
    AMBIGUOUS (could be 1 or 2 pistols won), not just incomplete -- so the
    whole match is excluded rather than guessed at."""
    mp = _match_player_with_pistol_rounds(
        round1_won=True, round13_won=None, team1_rounds_won=13, team2_rounds_won=7,
    )

    stats = compute_pistol_match_stats([mp])

    assert stats == _EMPTY_PISTOL_MATCH_STATS

"""Correctness tests for the Step 2b write-path builders: given
load_player_match_data's already-hydrated relationships (Round.player_stats,
Match.match_players.player) instead of get_player_profile's own three extra
queries, build_player_profile_from_match_data must derive the SAME
KDA/round-outcome/teammate-name inputs. Fixtures follow
tests/test_economy_graphs.py's existing pattern: plain ORM model
construction with relationships assigned by hand, no DB/session involved.
"""

from app.models import ImpactScore, Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.models.round import RoundPlayerStat
from app.services.economy_graphs import compute_econ_aggregates, econ_samples_from_data
from app.services.player_profile_types import build_player_profile_from_match_data


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


def test_kda_is_summed_from_round_player_stats_not_a_separate_query():
    match, me, mp_me, _ = _build_one_match_two_players()
    score = ImpactScore(round_id=1, match_player_id=1, kill_impact=10.0, death_impact=-2.0, impact=8.0, breakdown={})

    profile = build_player_profile_from_match_data(me, [mp_me], {1: [score]})

    assert len(profile.matches) == 1
    m = profile.matches[0]
    assert (m.kills, m.deaths, m.assists) == (3, 1, 2)


def test_round_outcome_is_read_from_hydrated_rounds_not_a_separate_query():
    match, me, mp_me, _ = _build_one_match_two_players()
    # mp_me is TEAM_1 ("Team A Wins" in the outcome string) -- winning side.
    score = ImpactScore(round_id=1, match_player_id=1, kill_impact=10.0, death_impact=-2.0, impact=8.0, breakdown={})

    profile = build_player_profile_from_match_data(me, [mp_me], {1: [score]})

    assert profile.matches[0].win is True


def test_traded_teammate_name_is_read_from_hydrated_match_players():
    match, me, mp_me, mp_ally = _build_one_match_two_players()
    score = ImpactScore(
        round_id=1, match_player_id=1, kill_impact=10.0, death_impact=-2.0, impact=8.0,
        breakdown={"traded_teammate_targets": {str(mp_ally.id): 1}},
    )

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
    score = ImpactScore(
        round_id=1, match_player_id=1, kill_impact=10.0, death_impact=-2.0, impact=8.0,
        breakdown={"econ_kill": 2, "clutch_kill": 1},
    )

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

"""Sessions' replay stats split sides by team: a teammate who isn't in the session roster (a random in a duo or a
4-stack) is on our side, not the enemy's. Squad's equivalent is in test_squad_overview_db.py."""

from types import SimpleNamespace as NS

from app.models.match import Team
from app.services.session_stats import _build_replay_stats

# Match players: 1 viewer, 2 friend, 3 random teammate (team 1); 4, 5 enemies (team 2).
TEAM_OF = {1: Team.TEAM_1, 2: Team.TEAM_1, 3: Team.TEAM_1, 4: Team.TEAM_2, 5: Team.TEAM_2}


def kill(event_id, killer, victim, t):
    return NS(id=event_id, killer_match_player_id=killer, death_match_player_id=victim, event_time_seconds=t)


def test_sessions_count_a_random_teammate_on_our_side():
    # Round 1, won: the viewer kills both enemies and nobody of ours dies -- a 3v1, never a clutch.
    # Round 2, won: the random and the friend die, the viewer wins 1v2 -- a clutch.
    round_kills = {
        1: [kill(1, 1, 4, 10.0), kill(2, 1, 5, 20.0)],
        2: [kill(3, 4, 3, 10.0), kill(4, 5, 2, 15.0), kill(5, 1, 4, 20.0), kill(6, 1, 5, 25.0)],
    }
    rounds = [NS(round_number=n, outcome="Team A wins", kill_events=kills, player_stats=[])
              for n, kills in round_kills.items()]
    match_players = [NS(id=mp_id, team=team) for mp_id, team in TEAM_OF.items()]
    session = NS(matches=[NS(id=1, match_players=match_players, rounds=rounds)],
                 team_by_match={1: Team.TEAM_1.value})
    our_mp_to_player = {1: 10, 2: 20}  # viewer and friend; the random isn't in the session roster

    (_streak, _top, _bottom, clutch_counts, xvx, changer, _first, _wipe) = _build_replay_stats(
        None, session, our_mp_to_player, {10: "viewer", 20: "friend"}
    )

    assert clutch_counts == {10: 1}
    # The viewer's kills: round 1 at 3v2 and 3v1 (neither), round 2 at 1v2 (a round changer) and 1v1 (XvX).
    # With the random as an enemy, round 1's looked like 2v3 and 2v2 and counted too.
    assert xvx == {10: 1}
    assert changer == {10: 1}

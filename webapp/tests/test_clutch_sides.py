"""Sessions' replay stats split sides by team: a teammate who isn't in the session roster (a random in a duo or a
4-stack) is on our side, not the enemy's. Squad's equivalent is in test_squad_overview_db.py."""

from types import SimpleNamespace as NS

import pytest

from app.models.match import Team
from app.services.session_stats import _build_replay_stats

# Match players: 1 viewer, 2 friend, 3 random teammate (team 1); 4, 5 enemies (team 2).
TEAM_OF = {1: Team.TEAM_1, 2: Team.TEAM_1, 3: Team.TEAM_1, 4: Team.TEAM_2, 5: Team.TEAM_2}
ROSTER = {1: 10, 2: 20}  # viewer and friend; the random isn't in the session roster


def kill(event_id, killer, victim, t):
    return NS(id=event_id, killer_match_player_id=killer, death_match_player_id=victim, event_time_seconds=t)


def replay_stats(round_kills, team_of=TEAM_OF, roster=ROSTER, our_side=Team.TEAM_1.value, kills_by_mp=None,
                 outcome="Team A wins"):
    player_stats = [NS(match_player_id=mp_id, kills=k) for mp_id, k in (kills_by_mp or {}).items()]
    rounds = [NS(round_number=n, outcome=outcome, kill_events=kills, player_stats=player_stats if n == 1 else [])
              for n, kills in round_kills.items()]
    match_players = [NS(id=mp_id, team=team) for mp_id, team in team_of.items()]
    session = NS(matches=[NS(id=1, match_players=match_players, rounds=rounds)],
                 team_by_match={1: our_side} if our_side else {})
    names = {player_id: f"p{player_id}" for player_id in roster.values()}
    (streak, top, bottom, clutches, xvx, changer, first_death, wipe) = _build_replay_stats(None, session, roster, names)
    return NS(top=top, bottom=bottom, clutches=clutches, xvx=xvx, changer=changer, wipe=wipe)


# Round 1, won: the viewer kills both enemies and nobody of ours dies -- a 3v1, never a clutch.
# Round 2, won: the random and the friend die, the viewer wins 1v2 -- a clutch.
NO_CLUTCH_THEN_CLUTCH = {
    1: [kill(1, 1, 4, 10.0), kill(2, 1, 5, 20.0)],
    2: [kill(3, 4, 3, 10.0), kill(4, 5, 2, 15.0), kill(5, 1, 4, 20.0), kill(6, 1, 5, 25.0)],
}


def test_sessions_count_a_random_teammate_on_our_side():
    stats = replay_stats(NO_CLUTCH_THEN_CLUTCH)

    assert stats.clutches == {10: 1}
    # The viewer's kills: round 1 at 3v2 and 3v1 (neither), round 2 at 1v2 (a round changer) and 1v1 (XvX).
    # With the random as an enemy, round 1's looked like 2v3 and 2v2 and counted too.
    assert stats.xvx == {10: 1}
    assert stats.changer == {10: 1}


def test_a_random_teammates_clutch_credits_nobody_in_the_roster():
    # The viewer and the friend die, the random wins 1v2.
    stats = replay_stats({1: [kill(1, 4, 1, 10.0), kill(2, 5, 2, 15.0), kill(3, 3, 4, 20.0), kill(4, 3, 5, 25.0)]})

    assert stats.clutches == {}


@pytest.mark.parametrize("random_kills", [30, 0], ids=["random-top-fragger", "random-bottom-fragger"])
def test_enemy_top_and_bottom_fraggers_come_from_the_enemy_team(random_kills):
    # Enemy 4 tops the enemy team, enemy 5 bottoms it; the random is above or below both. The viewer kills enemy 4,
    # and enemy 5 kills the friend.
    stats = replay_stats({1: [kill(1, 1, 4, 10.0), kill(2, 5, 2, 15.0), kill(3, 1, 5, 20.0)]},
                         kills_by_mp={1: 5, 2: 5, 3: random_kills, 4: 10, 5: 1})

    assert stats.top == {10: 1}
    assert stats.bottom == {20: 1}


def test_an_unknown_side_keeps_the_roster_split_and_counts_no_clutches():
    stats = replay_stats(NO_CLUTCH_THEN_CLUTCH, our_side=None)

    assert stats.clutches == {}
    # Without a known side the random counts as an enemy, as before: round 1's kills at 2v3 and 2v2 count.
    assert stats.xvx == {10: 2}
    assert stats.changer == {10: 2}


def wipe_kills(team_ids, enemy=6):
    return {1: [kill(i, enemy, mp_id, 10.0 + i) for i, mp_id in enumerate(team_ids)]}


def test_only_a_five_stack_wipe_counts_the_last_alive():
    five_stack = {mp_id: Team.TEAM_1 for mp_id in range(1, 6)} | {6: Team.TEAM_2}
    stats = replay_stats(wipe_kills(range(1, 6)), team_of=five_stack,
                         roster={mp_id: mp_id * 10 for mp_id in range(1, 6)}, outcome="Team B wins")
    assert stats.wipe == {50: 1}

    # A duo whose whole team is wiped, the viewer last: not a 5-stack, so not counted.
    duo_team = {mp_id: Team.TEAM_1 for mp_id in range(1, 6)} | {6: Team.TEAM_2}
    stats = replay_stats(wipe_kills([2, 3, 4, 5, 1]), team_of=duo_team, outcome="Team B wins")
    assert stats.wipe == {}

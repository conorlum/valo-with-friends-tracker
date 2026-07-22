from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.sessions import SessionMatchPlayer, group_matches_into_sessions

BASE_TIME = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)


def make_match(id, played_at, team1_rounds_won=13, team2_rounds_won=5):
    return SimpleNamespace(
        id=id,
        played_at=played_at,
        team1_rounds_won=team1_rounds_won,
        team2_rounds_won=team2_rounds_won,
    )


def make_roster(pairs):
    """pairs: list of (player_id, team, display_name)"""
    return [
        SessionMatchPlayer(player_id=pid, team=team, display_name=name) for pid, team, name in pairs
    ]


def test_two_matches_close_together_with_shared_roster_merge():
    match1 = make_match(1, BASE_TIME, team1_rounds_won=13, team2_rounds_won=5)
    match2 = make_match(2, BASE_TIME + timedelta(hours=2), team1_rounds_won=13, team2_rounds_won=8)

    roster1 = make_roster(
        [
            (1, "team-1", "Alice"),
            (2, "team-1", "Bob"),
            (3, "team-1", "Cara"),
            (4, "team-2", "Dan"),
            (5, "team-2", "Eve"),
        ]
    )
    # 4 of 5 players carry over (1,2,3 team-1 + 5 team-2); 4 replaces the old team-2 slot.
    roster2 = make_roster(
        [
            (1, "team-1", "Alice"),
            (2, "team-1", "Bob"),
            (3, "team-1", "Cara"),
            (5, "team-2", "Eve"),
            (6, "team-2", "Frank"),
        ]
    )

    sessions = group_matches_into_sessions(
        [match1, match2], {1: roster1, 2: roster2}
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert [m.id for m in session.matches] == [1, 2]
    assert session.is_multi_match is True
    assert session.core_player_ids == {1, 2, 3, 5}
    assert session.roster_player_ids == {1, 2, 3, 4, 5, 6}
    # Core roster (1,2,3 on team-1, 5 on team-2) overlaps more with team-1 in both
    # matches, and team-1 won both.
    assert session.wins == 2
    assert session.losses == 0
    assert session.ambiguous_match_ids == []


def test_large_gap_does_not_merge_even_with_full_overlap():
    match1 = make_match(1, BASE_TIME)
    match2 = make_match(2, BASE_TIME + timedelta(hours=8))

    roster = make_roster(
        [
            (1, "team-1", "Alice"),
            (2, "team-1", "Bob"),
            (3, "team-1", "Cara"),
            (4, "team-2", "Dan"),
            (5, "team-2", "Eve"),
        ]
    )

    sessions = group_matches_into_sessions([match1, match2], {1: roster, 2: roster})

    assert len(sessions) == 2
    assert all(not s.is_multi_match for s in sessions)


def test_insufficient_roster_overlap_does_not_merge():
    match1 = make_match(1, BASE_TIME)
    match2 = make_match(2, BASE_TIME + timedelta(hours=1))

    roster1 = make_roster(
        [
            (1, "team-1", "Alice"),
            (2, "team-1", "Bob"),
            (3, "team-1", "Cara"),
            (4, "team-2", "Dan"),
            (5, "team-2", "Eve"),
        ]
    )
    # Only 2 shared players (1, 2) — below the default threshold of 3.
    roster2 = make_roster(
        [
            (1, "team-1", "Alice"),
            (2, "team-1", "Bob"),
            (6, "team-1", "Grace"),
            (7, "team-2", "Hank"),
            (8, "team-2", "Ivy"),
        ]
    )

    sessions = group_matches_into_sessions([match1, match2], {1: roster1, 2: roster2})

    assert len(sessions) == 2
    assert all(not s.is_multi_match for s in sessions)


def test_match_with_no_played_at_is_excluded():
    match1 = make_match(1, BASE_TIME)
    match2 = make_match(2, None)

    roster = make_roster([(1, "team-1", "Alice")])

    sessions = group_matches_into_sessions([match1, match2], {1: roster, 2: roster})

    assert len(sessions) == 1
    assert [m.id for m in sessions[0].matches] == [1]

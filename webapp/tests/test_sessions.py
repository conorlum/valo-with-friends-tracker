from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.sessions import (
    SessionMatchPlayer,
    find_session_index_for_matches,
    group_matches_into_sessions,
)

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


FULL_LOBBY = [
    (1, "team-1", "Alice"),
    (2, "team-1", "Bob"),
    (3, "team-1", "Cara"),
    (4, "team-1", "Dee"),
    (5, "team-1", "Eve"),
    (6, "team-2", "Finn"),
    (7, "team-2", "Gil"),
    (8, "team-2", "Hana"),
    (9, "team-2", "Ivan"),
    (10, "team-2", "Jo"),
]


def make_session(index, match_ids):
    """A SessionSummary stand-in: find_session_index_for_matches only reads
    .index and .matches[].id."""
    return SimpleNamespace(index=index, matches=[SimpleNamespace(id=i) for i in match_ids])


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
    # Provisional cross-match core (1,2,3 on team-1, 5 on team-2) overlaps more
    # with team-1 in both matches, so team-1 is "our" team: roster and core are
    # restricted to team-1's players (1,2,3), excluding opponents 4/5/6 even
    # though 5 persisted across both matches on the other side.
    assert session.core_player_ids == {1, 2, 3}
    assert session.roster_player_ids == {1, 2, 3}
    # Team-1 won both.
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


def test_single_match_session_uses_anchor_to_pick_our_team():
    """A lone match has no second match to intersect rosters against, so which
    side is "ours" can only come from the scope the session was built for --
    which is exactly what the "just mine" view of a session narrows down to."""
    match = make_match(1, BASE_TIME, team1_rounds_won=13, team2_rounds_won=9)

    sessions = group_matches_into_sessions(
        [match], {1: make_roster(FULL_LOBBY)}, anchor_player_ids={1}
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert session.is_multi_match is False
    assert session.team_by_match == {1: "team-1"}
    assert session.roster_player_ids == {1, 2, 3, 4, 5}
    assert session.core_player_ids == {1, 2, 3, 4, 5}
    assert session.wins == 1
    assert session.losses == 0
    assert session.ambiguous_match_ids == []


def test_single_match_session_without_anchor_keeps_whole_lobby():
    """No anchor (the whole-DB grouping used by the analysis scripts) leaves the
    side unresolved, so everyone is kept rather than half the lobby guessed at."""
    match = make_match(1, BASE_TIME, team1_rounds_won=13, team2_rounds_won=9)

    sessions = group_matches_into_sessions([match], {1: make_roster(FULL_LOBBY)})

    session = sessions[0]
    assert session.team_by_match == {}
    assert session.roster_player_ids == set(range(1, 11))
    assert session.wins == 0
    assert session.losses == 0
    assert session.ambiguous_match_ids == [1]


def test_single_match_session_anchor_wins_over_uneven_team_sizes():
    """Guards the reason a single match skips the cross-match core entirely: with
    a 5v4 lobby the "core" is the whole lobby, so team sizes alone would hand the
    session to team-1 even when the viewer is on team-2."""
    match = make_match(1, BASE_TIME, team1_rounds_won=8, team2_rounds_won=13)
    roster = make_roster([row for row in FULL_LOBBY if row[0] != 10])

    sessions = group_matches_into_sessions([match], {1: roster}, anchor_player_ids={6, 7})

    session = sessions[0]
    assert session.team_by_match == {1: "team-2"}
    assert session.roster_player_ids == {6, 7, 8, 9}
    assert session.wins == 1
    assert session.losses == 0


def test_anchor_breaks_a_multi_match_tie_the_core_cannot():
    """Identical rosters across both matches make the cross-match core the whole
    lobby, which splits 5-5 and decides nothing; the anchor settles it."""
    match1 = make_match(1, BASE_TIME, team1_rounds_won=13, team2_rounds_won=7)
    match2 = make_match(2, BASE_TIME + timedelta(hours=1), team1_rounds_won=5, team2_rounds_won=13)
    roster = make_roster(FULL_LOBBY)

    sessions = group_matches_into_sessions(
        [match1, match2], {1: roster, 2: roster}, anchor_player_ids={2}
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert session.is_multi_match is True
    assert session.team_by_match == {1: "team-1", 2: "team-1"}
    assert session.roster_player_ids == {1, 2, 3, 4, 5}
    assert session.wins == 1
    assert session.losses == 1
    assert session.ambiguous_match_ids == []

    without_anchor = group_matches_into_sessions([match1, match2], {1: roster, 2: roster})[0]
    assert without_anchor.team_by_match == {}
    assert without_anchor.ambiguous_match_ids == [1, 2]


def test_find_session_index_for_matches_ignores_a_missing_first_match():
    """The regression this exists for: the viewer sat out the group session's
    first match, so looking that one match up under "just mine" found nothing
    and the toggle fell back to the session list."""
    sessions = [make_session(0, [1, 2]), make_session(1, [11, 12])]

    assert find_session_index_for_matches(sessions, [10, 11, 12]) == 1


def test_find_session_index_for_matches_picks_the_biggest_overlap():
    sessions = [make_session(0, [1, 2]), make_session(1, [3, 4, 5])]

    assert find_session_index_for_matches(sessions, [2, 3, 4, 5]) == 1


def test_find_session_index_for_matches_returns_none_without_overlap():
    sessions = [make_session(0, [1, 2])]

    assert find_session_index_for_matches(sessions, [9]) is None
    assert find_session_index_for_matches(sessions, []) is None

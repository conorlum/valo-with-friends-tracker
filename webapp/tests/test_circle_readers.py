"""Viewer-side friend readers use the circle (own accounts, friends from any
own account, friends' alts), not the per-account friend list."""

import inspect
from types import SimpleNamespace

import pytest

from app.models import Friendship
from app.routers import sessions as sessions_router
from app.services import gap_patterns, matches, session_stats, site_stats, squad
from app.services.account_groups import link_account
from app.services.matches import MatchSummary, PlayerSummary, get_viewer_team
from tests.test_account_groups import _players, db  # noqa: F401  (db is a fixture)


@pytest.fixture
def world(db):
    """viewer + own alt; friend (friended from the alt) + friend's alt; a stranger."""
    viewer, alt, friend, friend_alt, stranger = _players(db, "v#1", "va#2", "f#3", "fa#4", "s#5")
    link_account(db, viewer.id, alt.id)
    link_account(db, friend.id, friend_alt.id)
    db.add(Friendship(owner_player_id=alt.id, friend_player_id=friend.id))
    db.commit()
    return SimpleNamespace(viewer=viewer, alt=alt, friend=friend, friend_alt=friend_alt, stranger=stranger)


def _circle(w):
    return {w.viewer.id, w.alt.id, w.friend.id, w.friend_alt.id}


def _summary(players):
    return MatchSummary(players=players, round_numbers=[], round_outcomes={}, team_summaries=[])


def _ps(player_id, team):
    return PlayerSummary(
        match_player_id=player_id * 10, display_name=f"p{player_id}", agent="Jett", team=team,
        average_impact=0.0, average_kill_impact=0.0, average_death_impact=0.0,
        average_round_win_impact=0.0, impact_by_round={}, kill_impact_by_round={},
        death_impact_by_round={}, player_id=player_id,
    )


def test_site_stats_group_is_the_circle(db, world):
    assert site_stats.viewer_group_player_ids(db, world.viewer.id) == _circle(world)
    assert site_stats.viewer_group_player_ids(db, world.stranger.id) == {world.stranger.id}


def test_gap_patterns_group_is_the_circle(db, world):
    assert gap_patterns.group_player_ids(db, world.viewer.id) == _circle(world)
    assert gap_patterns.group_player_ids(db, None) == set()


def test_viewer_team_says_you_when_an_own_alt_played(db, world):
    summary = _summary([_ps(world.alt.id, "team-2"), _ps(world.stranger.id, "team-1")])
    assert get_viewer_team(db, summary, world.viewer.id) == ("team-2", "You")


def test_viewer_team_counts_a_friends_alt_as_a_friend(db, world):
    summary = _summary([_ps(world.friend_alt.id, "team-1"), _ps(world.stranger.id, "team-2")])
    assert get_viewer_team(db, summary, world.viewer.id) == ("team-1", "Your friends")


def test_scoped_player_ids_both_modes(db, world):
    friends = sessions_router._scoped_player_ids(db, world.viewer.id, True)
    assert friends[0] == world.viewer.id
    assert set(friends) == _circle(world) and len(friends) == len(set(friends))
    mine = sessions_router._scoped_player_ids(db, world.viewer.id, False)
    assert mine[0] == world.viewer.id
    assert set(mine) == {world.viewer.id, world.alt.id} and len(mine) == 2
    # starting from the alt, the alt leads
    assert sessions_router._scoped_player_ids(db, world.alt.id, False)[0] == world.alt.id


class _Sentinel(Exception):
    pass


def test_squad_overview_asks_for_the_circle_friends(db, monkeypatch):
    seen = []

    def fake(db_, viewer):
        seen.append(viewer)
        raise _Sentinel

    monkeypatch.setattr(squad, "circle_friend_ids", fake)
    with pytest.raises(_Sentinel):
        squad.get_squad_overview(db, 7, None)
    assert seen == [7]


@pytest.mark.parametrize("module,fn,name", [
    (session_stats, session_stats.get_session_stats, "circle_ids"),
    (matches, matches.get_match_shoutouts, "circle_ids"),
])
def test_deep_readers_use_circle_ids(module, fn, name):
    # The id set sits deep inside a big function; pin the wiring at source level.
    src = inspect.getsource(fn)
    assert f"{name}(db, viewer_player_id)" in src
    assert "list_friend_ids" not in src
    assert getattr(module, name) is not None


# --- W11b: page-side readers ---------------------------------------------------


def test_roster_pool_has_a_friends_alt_but_not_an_own_alt(db, world):
    from app.services.player_views import _roster_player_ids

    pool = _roster_player_ids(db, world.viewer, None)
    assert pool == {world.friend.id, world.friend_alt.id}
    assert _roster_player_ids(db, world.alt, None) == pool


def test_roster_pool_for_a_group_is_the_same_from_any_member(db, world):
    from app.services.player_views import _roster_player_ids

    ids = frozenset({world.viewer.id, world.alt.id})
    pool = _roster_player_ids(db, world.viewer, ids)
    assert pool == {world.friend.id, world.friend_alt.id}
    assert _roster_player_ids(db, world.alt, ids) == pool


def test_fight_ev_loader_takes_its_pool_from_the_circle(db, world, monkeypatch):
    from app.services import fight_ev

    seen = []
    monkeypatch.setattr(fight_ev, "circle_teammate_ids", lambda d, pid: seen.append(pid) or set())
    fight_ev.load_match_fight_ev_blocks_from_data(db, [], world.viewer)
    assert seen == [world.viewer.id]


def test_recent_rows_count_a_friends_alt_as_a_friend(db, world):
    from app.models import MatchPlayer
    from app.models.match import Team
    from app.services.recent_match_rows import build_recent_match_rows, recent_form_entries
    from tests.test_account_groups import _play, _round_with

    mp = _play(db, world.viewer, 5, "m1")
    _round_with(db, mp, kills=3)
    db.add(MatchPlayer(match_id=mp.match_id, player_id=world.friend_alt.id, agent="Sage", team=Team.TEAM_1))
    db.add(MatchPlayer(match_id=mp.match_id, player_id=world.alt.id, agent="Sage", team=Team.TEAM_1))
    db.commit()
    ids = frozenset({world.viewer.id, world.alt.id})
    entries = recent_form_entries(db, world.viewer.id, player_ids=ids)
    rows = build_recent_match_rows(db, world.viewer.id, entries, ids)
    details = [b.title for r in rows for b in r.badges if b.kind == "party"]
    assert len(details) == 1
    assert details[0].startswith("Queued with ")
    assert "fa" in details[0]          # the friend's alt
    assert "va" not in details[0]      # an own alt is not a friend


def test_fingerprint_changes_when_a_friend_links_an_alt(db, world):
    from app.services.account_groups import group_fingerprint

    ids = frozenset({world.viewer.id, world.alt.id})
    before = group_fingerprint(db, ids)
    new_alt = _players(db, "fa2#9")[0]
    link_account(db, world.friend.id, new_alt.id)
    db.commit()
    mid = group_fingerprint(db, ids)
    assert mid != before
    db.add(Friendship(owner_player_id=world.alt.id, friend_player_id=world.stranger.id))
    db.commit()
    assert group_fingerprint(db, ids) != mid

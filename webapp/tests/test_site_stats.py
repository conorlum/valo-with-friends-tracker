"""The /stats page's two populations: All Players (one site-wide row) and
Friends (the logged-in viewer plus their own friendships, one row per viewer).

Routes are called directly rather than through starlette's TestClient (see
test_site_modes.py for why).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Match, MatchPlayer, Round, ViewerSiteStatsCache
from app.models.match import MatchSource, Team
from app.routers import friends as friends_router
from app.routers import site_stats as site_stats_router
from app.services import site_stats, viewer_site_stats_cache
from app.services.player_data import RECENT_MATCH_LIMIT
from app.services.site_stats import compute_group_pistol_match_stats
from app.services.site_stats_cache import STAT_VARIANT_VALIDATORS
from app.services.viewer_site_stats_cache import (
    _validate_viewer_blob,
    friend_set_hash,
    get_viewer_site_stats_cache,
    viewer_cache_version,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _match(match_id: int, team1: list[int], team2: list[int], *, team1_pistols: int = 2,
           team1_won: bool = True, played_at: datetime | None = _T0) -> Match:
    """A decided match whose team-1 won `team1_pistols` of rounds 1 and 13."""
    match = Match(id=match_id, external_id=f"ext-{match_id}", source=MatchSource.SCRAPED,
                  team1_rounds_won=13 if team1_won else 5, team2_rounds_won=5 if team1_won else 13,
                  played_at=played_at)
    match.match_players = [
        MatchPlayer(id=match_id * 100 + i, match_id=match_id, player_id=pid, agent="Jett", team=team)
        for i, (pid, team) in enumerate([(p, Team.TEAM_1) for p in team1] + [(p, Team.TEAM_2) for p in team2])
    ]
    outcomes = ["Team A win" if i < team1_pistols else "Team B win" for i in range(2)]
    match.rounds = [Round(round_number=n, outcome=o, player_stats=[]) for n, o in zip((1, 13), outcomes)]
    return match


# --- per-viewer pistol stat ---------------------------------------------------


def test_group_pistol_stats_count_each_member_once_per_match_and_skip_outsiders():
    """Two group members as teammates both count (a per-player personal stat,
    as before); a non-member on the other team doesn't."""
    matches = [_match(1, team1=[1, 2], team2=[3], team1_pistols=2, team1_won=True)]

    stats = compute_group_pistol_match_stats(matches, {1, 2}, "career")

    assert stats["won_both_total"] == 2 and stats["won_both_wins"] == 2
    assert stats["lost_both_total"] == 0


def test_group_pistol_stats_recent_keeps_each_members_newest_matches():
    """"recent" is each member's newest RECENT_MATCH_LIMIT matches, newest by
    played_at with an unknown date counting as newest -- the player page's
    own Recent window."""
    old = [_match(i, [1], [9], team1_pistols=0, played_at=_T0 - timedelta(days=i)) for i in range(1, 11)]
    new = [_match(100 + i, [1], [9], team1_pistols=2, played_at=_T0 + timedelta(days=i))
           for i in range(RECENT_MATCH_LIMIT - 1)]
    undated = _match(999, [1], [9], team1_pistols=1, played_at=None)
    matches = old + new + [undated]

    recent = compute_group_pistol_match_stats(matches, {1}, "recent")
    career = compute_group_pistol_match_stats(matches, {1}, "career")

    assert recent["won_both_total"] == RECENT_MATCH_LIMIT - 1
    assert recent["won_one_total"] == 1      # the undated match is inside the window
    assert recent["lost_both_total"] == 0    # every older match falls outside it
    assert career["lost_both_total"] == 10


def test_group_variants_come_from_the_group_not_the_whole_load():
    """The Friends blob keeps each stat's "group" variant: a match the viewer's
    group didn't play in contributes nothing even if it was loaded."""
    matches = [_match(1, [1], [2], played_at=_T0), _match(2, [8], [9], played_at=_T0)]

    group_side = site_stats.GROUP_STAT_COMPUTERS["map_side_stats"](matches, {1})["group"]

    assert sum(bucket["matches"] for bucket in group_side.values()) == 1


# --- the viewer's group --------------------------------------------------------


def test_viewer_group_is_the_viewer_plus_their_own_friends(monkeypatch):
    monkeypatch.setattr(site_stats, "list_friend_ids", lambda db, owner: {11, 12} if owner == 5 else set())
    assert site_stats.viewer_group_player_ids(None, 5) == {5, 11, 12}
    assert site_stats.viewer_group_player_ids(None, 6) == {6}


def test_friend_set_hash_is_order_independent_and_changes_with_membership():
    assert friend_set_hash({1, 2, 3}) == friend_set_hash({3, 2, 1})
    assert friend_set_hash({1, 2, 3}) != friend_set_hash({1, 2})


# --- viewer cache validation and staleness -------------------------------------


def _valid_viewer_blob() -> dict:
    matches = [_match(1, [1], [2])]
    return {
        "pistol_match_stats": {scope: compute_group_pistol_match_stats(matches, {1}, scope)
                               for scope in ("recent", "career")},
        **{key: compute(matches, {1})["group"] for key, compute in site_stats.GROUP_STAT_COMPUTERS.items()},
    }


def test_computed_viewer_blob_validates():
    blob = _valid_viewer_blob()
    assert set(blob) == set(STAT_VARIANT_VALIDATORS)
    assert _validate_viewer_blob(blob)


def test_viewer_blob_rejects_pistol_stats_without_both_scopes():
    blob = _valid_viewer_blob()
    blob["pistol_match_stats"] = blob["pistol_match_stats"]["career"]
    assert not _validate_viewer_blob(blob)


class _OneRowDb:
    def __init__(self, row):
        self.row = row

    def get(self, _model, _key):
        return self.row


def _row(**overrides) -> ViewerSiteStatsCache:
    fields = {"viewer_player_id": 5, "data": _valid_viewer_blob(), "version": viewer_cache_version(),
              "friend_set_hash": friend_set_hash({5, 11})}
    return ViewerSiteStatsCache(**{**fields, **overrides})


def test_viewer_cache_hit():
    row = _row()
    assert get_viewer_site_stats_cache(_OneRowDb(row), 5, friend_set_hash({5, 11})) is row.data


def test_viewer_cache_miss_on_no_row():
    assert get_viewer_site_stats_cache(_OneRowDb(None), 5, friend_set_hash({5})) is None


def test_viewer_cache_stale_after_a_friend_set_change():
    """A row written before a friend was added reads as a miss even if the
    delete that should have removed it never ran."""
    assert get_viewer_site_stats_cache(_OneRowDb(_row()), 5, friend_set_hash({5, 11, 12})) is None


def test_viewer_cache_stale_after_a_version_bump(monkeypatch):
    row = _row()
    monkeypatch.setattr(viewer_site_stats_cache, "SITE_STATS_CACHE_SCHEMA_VERSION",
                        viewer_site_stats_cache.SITE_STATS_CACHE_SCHEMA_VERSION + 1)
    assert get_viewer_site_stats_cache(_OneRowDb(row), 5, friend_set_hash({5, 11})) is None


def test_viewer_cache_miss_on_a_corrupt_blob():
    assert get_viewer_site_stats_cache(_OneRowDb(_row(data={"nope": 1})), 5, friend_set_hash({5, 11})) is None


# --- get_viewer_site_stats: hit / miss / write-through failure -------------------


class _RollbackDb:
    rolled_back = False

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def viewer_group(monkeypatch):
    monkeypatch.setattr(site_stats, "viewer_group_player_ids", lambda db, viewer: {viewer, 11})


def test_get_viewer_site_stats_serves_a_hit_without_computing(monkeypatch, viewer_group):
    monkeypatch.setattr(site_stats, "get_viewer_site_stats_cache", lambda db, viewer, h: {"cached": True})
    monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda *a: pytest.fail("recomputed on a hit"))
    assert site_stats.get_viewer_site_stats(None, 5) == {"cached": True}


def test_get_viewer_site_stats_computes_and_stores_on_a_miss(monkeypatch, viewer_group):
    stored = []
    monkeypatch.setattr(site_stats, "get_viewer_site_stats_cache", lambda db, viewer, h: None)
    monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda db, group: {"group": sorted(group)})
    monkeypatch.setattr(site_stats, "store_viewer_site_stats_cache", lambda db, *args: stored.append(args))

    assert site_stats.get_viewer_site_stats(None, 5) == {"group": [5, 11]}
    assert stored == [(5, friend_set_hash({5, 11}), {"group": [5, 11]})]


def test_get_viewer_site_stats_still_renders_when_the_write_fails(monkeypatch, viewer_group):
    def fail(*_):
        raise RuntimeError("table missing")

    db = _RollbackDb()
    monkeypatch.setattr(site_stats, "get_viewer_site_stats_cache", lambda db, viewer, h: None)
    monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda db, group: {"live": True})
    monkeypatch.setattr(site_stats, "store_viewer_site_stats_cache", fail)

    assert site_stats.get_viewer_site_stats(db, 5) == {"live": True}
    assert db.rolled_back


def test_refresh_site_stats_retires_every_viewer_row_in_the_same_commit(monkeypatch):
    calls = []
    monkeypatch.setattr(site_stats, "_compute_site_stats", lambda db: {"all": 1})
    monkeypatch.setattr(site_stats, "invalidate_all_viewer_site_stats", lambda db: calls.append("invalidate"))
    monkeypatch.setattr(site_stats, "store_site_stats_cache", lambda db, data: calls.append("store+commit"))

    site_stats.refresh_site_stats(None)

    assert calls == ["invalidate", "store+commit"]


# --- routes ------------------------------------------------------------------


def test_logged_out_stats_goes_to_all_players(monkeypatch):
    monkeypatch.setattr(site_stats_router, "get_current_player", lambda request, db: None)
    monkeypatch.setattr(site_stats_router, "get_viewer_site_stats", lambda *a: pytest.fail("computed Friends"))

    response = site_stats_router.stats_page(request=None, db=None)

    assert response.status_code == 303
    assert response.headers["location"] == "/stats/all"


def test_logged_out_career_fragment_is_a_login_note(monkeypatch):
    monkeypatch.setattr(site_stats_router, "get_current_player", lambda request, db: None)
    monkeypatch.setattr(site_stats_router, "get_viewer_site_stats", lambda *a: pytest.fail("computed Friends"))

    response = site_stats_router.friends_career_fragment(request=None, db=None)

    assert site_stats_router.FRIENDS_LOGIN_NOTE.encode() in response.body


def test_friends_context_uses_the_viewer_and_the_requested_scope(monkeypatch):
    seen = []
    blob = _valid_viewer_blob()
    blob["pistol_match_stats"]["recent"] = dict(blob["pistol_match_stats"]["recent"], lost_both_total=7)

    def fake(db, viewer):
        seen.append(viewer)
        return blob

    monkeypatch.setattr(site_stats_router, "get_viewer_site_stats", fake)
    context = site_stats_router._friends_context(None, 5, "recent")

    assert seen == [5]
    assert context["subject_label"] == "you and your friends"
    assert context["scope"] == "recent"


@pytest.mark.parametrize("route, extra", [(friends_router.add_friend_route, {"next": None}),
                                          (friends_router.remove_friend_route, {})])
def test_a_friendship_change_invalidates_that_viewers_row(monkeypatch, route, extra):
    class _Viewer:
        id = 5

    class _Friend:
        id = 11

    class _Db:
        def commit(self):
            pass

    invalidated = []
    monkeypatch.setattr(friends_router.settings, "demo_mode", False)
    monkeypatch.setattr(friends_router, "get_current_player", lambda request, db: _Viewer())
    monkeypatch.setattr(friends_router, "get_player_or_404", lambda db, name: _Friend())
    monkeypatch.setattr(friends_router, "add_friend", lambda db, a, b: True)
    monkeypatch.setattr(friends_router, "remove_friend", lambda db, a, b: True)
    monkeypatch.setattr(friends_router, "invalidate_player_cache", lambda db, ids: None)
    monkeypatch.setattr(friends_router, "invalidate_viewer_site_stats", lambda db, v: invalidated.append(v))

    route(request=None, display_name="Friend#NA1", db=_Db(), **extra)

    assert invalidated == [5]


def test_viewer_cache_read_failure_is_a_miss_not_an_error():
    from sqlalchemy.exc import ProgrammingError

    class _BrokenDb(_RollbackDb):
        def get(self, *_):
            raise ProgrammingError("SELECT", {}, Exception('relation "viewer_site_stats_cache" does not exist'))

    db = _BrokenDb()
    assert get_viewer_site_stats_cache(db, 5, friend_set_hash({5})) is None
    assert db.rolled_back

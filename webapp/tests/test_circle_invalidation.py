"""Friend and link changes clear every affected circle's cached pages (R4, P5)."""

from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.models import Friendship, PlayerViewCache
from app.models.viewer_site_stats_cache import ViewerSiteStatsCache
from app.routers import accounts as accounts_router
from app.routers import friends as friends_router
from app.services import site_stats
from app.services.account_groups import link_account
from app.services.auth import SESSION_KEY
from app.services.player_view_cache import store_player_views
from app.services.site_stats import viewer_group_player_ids
from app.services.viewer_site_stats_cache import friend_set_hash
from tests.test_account_groups import _players, db  # noqa: F401  (db is a fixture)
from tests.test_player_view_cache import _sample_views


def _request(player):
    return Request({
        "type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": [],
        "scheme": "http", "server": ("testserver", 80), "session": {SESSION_KEY: player.id},
    })


def _store(db, player):
    views = _sample_views(player.id)
    store_player_views(db, player.id, {"recent": views, "career": views})


def _cached(db):
    """Read through a fresh session: the route's own session sees uncommitted deletes."""
    with sessionmaker(bind=db.get_bind())() as fresh:
        return {pid for (pid,) in fresh.query(PlayerViewCache.player_id).distinct().all()}


def _friend(db, owner, friend):
    db.add(Friendship(owner_player_id=owner.id, friend_player_id=friend.id))
    db.commit()


def test_a_friend_linking_an_alt_clears_the_viewers_rows(db):
    viewer, friend, alt = _players(db, "viewer#1", "friend#2", "alt#3")
    _friend(db, viewer, friend)
    _store(db, viewer)
    assert _cached(db) == {viewer.id}

    accounts_router.add_account_route(_request(friend), display_name="alt#3", db=db)

    assert viewer.id not in _cached(db)


def test_friending_from_an_alt_clears_the_mains_rows(db):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    assert link_account(db, main.id, alt.id) is None
    db.commit()
    _store(db, main)
    assert main.id in _cached(db)

    friends_router.add_friend_route(_request(alt), display_name="other#3", next=None, db=db)

    assert main.id not in _cached(db)


def test_unfriending_from_an_alt_clears_the_mains_rows(db):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    assert link_account(db, main.id, alt.id) is None
    _friend(db, alt, other)
    _store(db, main)
    assert main.id in _cached(db)

    friends_router.remove_friend_route(_request(alt), display_name="other#3", db=db)

    assert main.id not in _cached(db)


def test_unlinking_clears_the_viewers_rows(db):
    viewer, friend, alt = _players(db, "viewer#1", "friend#2", "alt#3")
    assert link_account(db, friend.id, alt.id) is None
    _friend(db, viewer, friend)
    _store(db, viewer)
    assert viewer.id in _cached(db)

    accounts_router.remove_account_route(_request(friend), display_name="alt#3", db=db)

    assert viewer.id not in _cached(db)


def test_a_sub_leaving_clears_the_rows_of_whoever_friends_its_main(db):
    viewer, friend, alt = _players(db, "viewer#1", "friend#2", "alt#3")
    assert link_account(db, friend.id, alt.id) is None
    _friend(db, viewer, friend)
    _store(db, viewer)
    assert viewer.id in _cached(db)

    accounts_router.remove_account_route(_request(alt), display_name="alt#3", db=db)

    assert viewer.id not in _cached(db)


def test_stats_friends_row_misses_after_a_friend_links_an_alt(db):
    viewer, friend, alt = _players(db, "viewer#1", "friend#2", "alt#3")
    _friend(db, viewer, friend)
    stored = friend_set_hash(viewer_group_player_ids(db, viewer.id))
    db.add(ViewerSiteStatsCache(viewer_player_id=viewer.id, friend_set_hash=stored, data={}, version=1))
    db.commit()

    accounts_router.add_account_route(_request(friend), display_name="alt#3", db=db)

    with sessionmaker(bind=db.get_bind())() as fresh:
        assert fresh.query(ViewerSiteStatsCache).count() == 0


def test_friending_from_an_alt_clears_the_mains_stats_row(db):
    main, alt, other = _players(db, "main#1", "alt#2", "other#3")
    assert link_account(db, main.id, alt.id) is None
    db.add(ViewerSiteStatsCache(viewer_player_id=main.id, friend_set_hash="x", data={}, version=1))
    db.commit()

    friends_router.add_friend_route(_request(alt), display_name="other#3", next=None, db=db)

    with sessionmaker(bind=db.get_bind())() as fresh:
        assert fresh.query(ViewerSiteStatsCache).count() == 0


def test_prewarm_covers_a_friendless_sub_of_a_group_with_a_friendship(db, monkeypatch):
    main, sub, friend, loner = _players(db, "main#1", "sub#2", "friend#3", "loner#4")
    assert link_account(db, main.id, sub.id) is None
    _friend(db, main, friend)
    seen = []
    monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda db_, group: {})
    monkeypatch.setattr(site_stats, "store_viewer_site_stats_cache",
                        lambda db_, viewer, h, data: seen.append(viewer))

    assert site_stats.prewarm_viewer_site_stats(db) == 2

    assert seen == sorted([main.id, sub.id])

"""Page cache failures remain best-effort and reach site health."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import Player
from app.models.site_events import SiteEvent, SiteEventResolution
from app.routers import players
from app.services import account_groups, player_view_cache as cache, site_health
from app.services import site_stats, site_stats_cache, viewer_site_stats_cache
from tests.test_player_view_cache import _sample_views


@pytest.fixture(autouse=True)
def friends_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, tables=[SiteEvent.__table__, SiteEventResolution.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine)


def _row(**overrides):
    return SimpleNamespace(**{
        "player_id": 17, "scope": "recent", "version": cache.cache_version(),
        "data": {"bad": 1}, **overrides,
    })


def _assert_counter(key, items):
    assert set(site_health._counters) == {("cache_fallback", key)}
    counter = site_health._counters[("cache_fallback", key)]
    assert counter["n"] == len(items)
    assert sorted(counter["items"]) == sorted(item for item in items if item is not None)


def test_invalid_cache_row():
    assert cache.decode_cache_row(_row(), Player(id=23)) is None
    _assert_counter("player_view_cache:invalid", [17])


@pytest.mark.parametrize("blob_only", [False, True])
def test_decode_crash(monkeypatch, blob_only):
    monkeypatch.setattr(cache, "_validate_blob", lambda data: True)
    monkeypatch.setattr(cache, "_decode", Mock(side_effect=RuntimeError("decode failed")))
    player = Player(id=23)
    if blob_only:
        result = cache.decode_player_views_blob({}, cache.cache_version(), player)
    else:
        result = cache.decode_cache_row(_row(), player)
    assert result is None
    _assert_counter("player_view_cache:decode", [23 if blob_only else 17])


def test_normal_misses_count_nothing():
    player = Player(id=23)
    assert cache.decode_cache_row(None, player) is None
    assert cache.decode_cache_row(_row(version=-1), player) is None
    assert cache.decode_player_views_blob({"bad": 1}, -1, player) is None
    assert not site_health._counters


def test_invalid_player_views_blob():
    assert cache.decode_player_views_blob({"bad": 1}, cache.cache_version(), Player(id=23)) is None
    _assert_counter("player_view_cache:invalid", [23])


def test_prewarm_failure_counts_each_player(monkeypatch):
    recompute = Mock(side_effect=RuntimeError("prewarm failed"))
    group_prewarm = Mock()
    monkeypatch.setattr(cache, "recompute_player_views", recompute)
    monkeypatch.setattr(account_groups, "prewarm_group_caches", group_prewarm)
    db = SimpleNamespace(rollback=Mock())
    assert cache.prewarm_player_cache(db, {17, 23}) is None
    assert db.rollback.call_count == 2
    group_prewarm.assert_called_once_with(db, {17, 23})
    _assert_counter("player_view_cache:prewarm", [17, 23])


def test_write_through_failure_keeps_page_context(monkeypatch):
    views = _sample_views()
    monkeypatch.setattr(players, "compute_player_views", lambda *a, **kw: views)
    store = Mock(side_effect=RuntimeError("write failed"))
    monkeypatch.setattr(players, "store_player_views", store)
    # Exercise the real store callback without opening a database session.
    monkeypatch.setattr(players, "_run_in_own_session", lambda fn, *a, **kw: fn(None, *a))
    context, extra = players._build_profile_context(
        None, Player(id=17), 30, "recent", cached=None,
    )
    assert context["profile"] is views.profile
    assert extra is None
    store.assert_called_once()
    _assert_counter("player_page:write_through", [17])


def test_flush_aggregates_fallbacks(factory):
    for _ in range(2):
        assert cache.decode_cache_row(_row(), Player(id=17)) is None
    site_health.flush(session_factory=factory)
    with factory() as session:
        row = session.scalars(select(SiteEvent)).one()
        assert (row.kind, row.key, row.count) == ("cache_fallback", "player_view_cache:invalid", 2)
        assert row.context == [17, 17]
    assert not site_health._counters


def test_demo_mode_counts_nothing(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert cache.decode_cache_row(_row(), Player(id=17)) is None
    assert not site_health._counters


def test_invalid_site_stats_cache_row():
    row = SimpleNamespace(version=site_stats_cache.SITE_STATS_CACHE_SCHEMA_VERSION, data={"bad": 1})
    db = Mock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = row
    assert site_stats_cache.get_site_stats_cache(db) is None
    _assert_counter("site_stats:invalid", [None])


@pytest.mark.parametrize("viewer", [False, True])
def test_stats_write_through_failure_keeps_live_result(monkeypatch, viewer):
    data = {"live": True}
    db = SimpleNamespace(rollback=Mock())
    store = Mock(side_effect=RuntimeError("write failed"))
    if viewer:
        monkeypatch.setattr(site_stats, "viewer_group_player_ids", lambda *a: {17})
        monkeypatch.setattr(site_stats, "get_viewer_site_stats_cache", lambda *a: None)
        monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda *a: data)
        monkeypatch.setattr(site_stats, "store_viewer_site_stats_cache", store)
        assert site_stats.get_viewer_site_stats(db, 17) is data
        _assert_counter("viewer_site_stats:write_through", [17])
    else:
        monkeypatch.setattr(site_stats, "get_site_stats_cache", lambda *a: None)
        monkeypatch.setattr(site_stats, "_compute_site_stats", lambda *a: data)
        monkeypatch.setattr(site_stats, "store_site_stats_cache", store)
        assert site_stats.get_site_stats(db) is data
        _assert_counter("site_stats:write_through", [None])
    store.assert_called_once()
    db.rollback.assert_called_once_with()


def test_viewer_stats_cache_read_failure():
    db = SimpleNamespace(get=Mock(side_effect=SQLAlchemyError("read failed")), rollback=Mock())
    assert viewer_site_stats_cache.get_viewer_site_stats_cache(db, 17, "expected") is None
    db.rollback.assert_called_once_with()
    _assert_counter("viewer_site_stats:read", [17])


def test_invalid_viewer_stats_cache_row():
    row = SimpleNamespace(
        version=viewer_site_stats_cache.viewer_cache_version(), friend_set_hash="expected", data={"bad": 1},
    )
    db = SimpleNamespace(get=Mock(return_value=row))
    assert viewer_site_stats_cache.get_viewer_site_stats_cache(db, 17, "expected") is None
    _assert_counter("viewer_site_stats:invalid", [17])


@pytest.mark.parametrize("miss", ["no_row", "version", "friend_set"])
def test_normal_viewer_stats_cache_misses_count_nothing(miss):
    row = SimpleNamespace(
        version=-1 if miss == "version" else viewer_site_stats_cache.viewer_cache_version(),
        friend_set_hash="stale" if miss == "friend_set" else "expected", data={"bad": 1},
    )
    db = SimpleNamespace(get=Mock(return_value=None if miss == "no_row" else row))
    assert viewer_site_stats_cache.get_viewer_site_stats_cache(db, 17, "expected") is None
    assert not site_health._counters


def test_viewer_stats_prewarm_failure_continues(monkeypatch):
    db = Mock()
    db.query.return_value.distinct.return_value.all.return_value = [(17,), (23,)]
    monkeypatch.setattr(site_stats, "own_account_ids", lambda db, pid: {pid})
    monkeypatch.setattr(site_stats, "viewer_group_player_ids", lambda db, pid: {pid})
    monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda *a: {"live": True})
    store = Mock(side_effect=[RuntimeError("prewarm failed"), None])
    monkeypatch.setattr(site_stats, "store_viewer_site_stats_cache", store)
    assert site_stats.prewarm_viewer_site_stats(db) == 1
    assert store.call_count == 2
    db.rollback.assert_called_once_with()
    _assert_counter("viewer_site_stats:prewarm", [17])


def test_account_group_cache_read_failure():
    db = SimpleNamespace(get=Mock(side_effect=RuntimeError("read failed")), rollback=Mock())
    assert account_groups.get_cached_group_views(db, 31, "recent", "expected", Player(id=17)) is None
    db.rollback.assert_not_called()
    _assert_counter("account_group_cache:read", [31])


@pytest.mark.parametrize("lookup_failure", [True, False])
def test_account_group_cache_prewarm_failure(lookup_failure):
    db = Mock()
    failure = RuntimeError("group lookup failed")
    if lookup_failure:
        db.query.side_effect = failure
    else:
        groups = SimpleNamespace(filter=Mock(return_value=[(31,)]))
        db.query.side_effect = [groups, failure]
    assert account_groups.prewarm_group_caches(db, {17}) is None
    db.rollback.assert_called_once_with()
    _assert_counter("account_group_cache:prewarm", [None if lookup_failure else 31])


def test_stats_cache_fallback_in_demo_counts_nothing(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    row = SimpleNamespace(
        version=site_stats_cache.SITE_STATS_CACHE_SCHEMA_VERSION, data={"bad": 1},
    )
    db = Mock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = row
    assert site_stats_cache.get_site_stats_cache(db) is None
    assert not site_health._counters


def test_cache_fallback_literal_keys_are_registered():
    pattern = re.compile(r'\bcount\(\s*"cache_fallback"\s*,\s*"([^"]+)"')
    hits = [
        key
        for path in (Path(__file__).resolve().parents[1] / "app").rglob("*.py")
        for key in pattern.findall(path.read_text(encoding="utf-8"))
    ]
    assert len(hits) >= 4
    assert set(hits) == site_health.CACHE_FALLBACK_KEYS
    assert site_health.CACHE_FALLBACK_KEYS == frozenset({
        "player_view_cache:invalid", "player_view_cache:decode",
        "player_view_cache:prewarm", "player_page:write_through",
        "site_stats:invalid", "site_stats:write_through",
        "viewer_site_stats:read", "viewer_site_stats:invalid",
        "viewer_site_stats:write_through", "viewer_site_stats:prewarm",
        "account_group_cache:read", "account_group_cache:prewarm",
    })



# B4 / B13: the pre-warms run in command-line processes, which have no flush thread: they flush at their end.
def _stored(factory):
    from sqlalchemy import select

    with factory() as s:
        return {(r.kind, r.key): r for r in s.scalars(select(SiteEvent))}


def test_a_player_prewarm_failure_reaches_the_table_without_a_flush_thread(factory, monkeypatch):
    monkeypatch.setattr(cache, "recompute_player_views", Mock(side_effect=RuntimeError("prewarm failed")))
    monkeypatch.setattr(account_groups, "prewarm_group_caches", Mock())
    with factory() as db:
        cache.prewarm_player_cache(db, {17})
    row = _stored(factory)[("cache_fallback", "player_view_cache:prewarm")]
    assert row.count == 1 and row.context == [17]
    assert not site_health._counters


def test_a_viewer_stats_prewarm_failure_reaches_the_table_without_a_flush_thread(engine, factory, monkeypatch):
    from app.models.friendship import Friendship

    Base.metadata.create_all(engine, tables=[Friendship.__table__])
    with factory() as s:
        s.add(Friendship(owner_player_id=17, friend_player_id=23))
        s.commit()
    monkeypatch.setattr(site_stats, "own_account_ids", lambda db, pid: {pid})
    monkeypatch.setattr(site_stats, "viewer_group_player_ids", lambda db, pid: {pid})
    monkeypatch.setattr(site_stats, "compute_viewer_site_stats", lambda *a: {"live": True})
    monkeypatch.setattr(site_stats, "store_viewer_site_stats_cache", Mock(side_effect=RuntimeError("prewarm failed")))
    with factory() as db:
        assert site_stats.prewarm_viewer_site_stats(db) == 0
    row = _stored(factory)[("cache_fallback", "viewer_site_stats:prewarm")]
    assert row.count == 1 and row.context == [17]

"""Player-page cache failures remain best-effort and reach site health."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import Player
from app.models.site_events import SiteEvent, SiteEventResolution
from app.routers import players
from app.services import account_groups, player_view_cache as cache, site_health
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
    assert sorted(counter["items"]) == sorted(items)


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
    })

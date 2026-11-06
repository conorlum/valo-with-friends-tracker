from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.site_events import SiteEvent
from app.services import site_health
from tests.support.postgres import scratch_database, migrated_database, pg_factory  # noqa: F401

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)


def get(factory, kind, key):
    with factory() as s:
        return s.get(SiteEvent, (kind, key))


def test_two_records_count_two(pg_factory):
    site_health.record("k", "a", "one", session_factory=pg_factory, at=T0)
    site_health.record("k", "a", "two", session_factory=pg_factory, at=T0)
    r = get(pg_factory, "k", "a")
    assert r.count == 2 and r.error == "two"


def test_nested_context_roundtrips(pg_factory):
    ctx = {"a": {"b": [1, 2, {"c": "d"}]}, "e": None}
    site_health.record("k", "a", "e", ctx, session_factory=pg_factory, at=T0)
    assert get(pg_factory, "k", "a").context == ctx


def test_flush_counted(pg_factory):
    site_health.record("k", "c", "e", session_factory=pg_factory, at=T0)
    for i in range(3):
        site_health.count("k", "c", {"i": i}, at=T0)
    site_health.enqueue("k", "q", "e", at=T0)
    site_health.flush(session_factory=pg_factory)
    assert get(pg_factory, "k", "c").count == 4
    assert get(pg_factory, "k", "c").context == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert get(pg_factory, "k", "q").count == 1


def test_apply_resolved_twice_deletes_and_does_not_raise(pg_factory):
    from datetime import timedelta
    from app.models.site_events import SiteEventResolution
    e = [{"kind": "page_error", "key": "/a", "fixed_by": "x"}]
    site_health.record("page_error", "/a", "boom", session_factory=pg_factory, at=T0)
    site_health.record("page_error", "/b", "boom", session_factory=pg_factory, at=T0)
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0)
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0)  # a second process
    assert get(pg_factory, "page_error", "/a") is not None
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert get(pg_factory, "page_error", "/a") is None and get(pg_factory, "page_error", "/b") is not None
    with pg_factory() as s:
        rows = s.scalars(select(SiteEventResolution)).all()
    assert len(rows) == 1 and rows[0].applied_at is not None

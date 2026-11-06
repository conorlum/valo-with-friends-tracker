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

"""Live site-health checks: the Critical Util build's staleness. SQLite only; the PostgreSQL lock is in
tests/test_site_health_pg.py."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models.replay import ReplayRoundUtil, UtilModel
from app.models.site_events import SiteEvent, SiteEventResolution
from app.services import replay_gaps, site_health_checks

T = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[UtilModel.__table__, ReplayRoundUtil.__table__,
                                             SiteEvent.__table__, SiteEventResolution.__table__])
    with sessionmaker(bind=engine)() as session:
        yield session


def build(db, at):
    db.add(UtilModel(name="values", version=1, key="k1", data={}, computed_at=at))
    db.commit()


def util_row(db, at, n=1, status="ok", revision=None):
    db.add(ReplayRoundUtil(replay_id=1, round_number=n, status=status, fingerprint="f",
                           gaps_revision=replay_gaps.GAPS_REVISION if revision is None else revision,
                           computed_at=at))
    db.commit()


def test_a_build_older_than_a_newer_util_row_by_over_three_hours_is_stale(db):
    build(db, T)
    util_row(db, T + timedelta(hours=1))
    [item] = site_health_checks.util_build_stale(db, T + timedelta(hours=5))
    assert item["key"] == "values"
    assert item["first_seen"] == T + timedelta(hours=1)
    assert "older than util rows computed since" in item["error"]
    assert item["context"]["build_key"] == "k1"
    assert item["context"]["build_computed_at"] and item["context"]["oldest_newer_row"]


def test_two_hours_is_not_stale(db):
    build(db, T)
    util_row(db, T + timedelta(hours=1))
    assert site_health_checks.util_build_stale(db, T + timedelta(hours=3)) == []


def test_no_build_and_an_old_util_row_is_stale_with_none(db):
    util_row(db, T)
    [item] = site_health_checks.util_build_stale(db, T + timedelta(hours=4))
    assert "none" in item["error"] and item["context"]["build_computed_at"] is None


def test_rows_that_failed_or_have_an_old_revision_do_not_count(db):
    util_row(db, T, n=1, status="failed")
    util_row(db, T, n=2, revision=replay_gaps.GAPS_REVISION - 1)
    assert site_health_checks.util_build_stale(db, T + timedelta(hours=9)) == []


def test_rows_older_than_the_build_do_not_count(db):
    util_row(db, T)
    build(db, T + timedelta(hours=1))
    assert site_health_checks.util_build_stale(db, T + timedelta(hours=9)) == []


def test_a_failing_query_returns_nothing(db):
    ReplayRoundUtil.__table__.drop(db.get_bind())
    assert site_health_checks.util_build_stale(db, T) == []

"""Live site-health checks: the Critical Util build's staleness. SQLite only; the PostgreSQL lock is in
tests/test_site_health_pg.py."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models.impact_score import ImpactScore
from app.models.map_pool import MapPoolPatchNotes
from app.models.match import Match, MatchSource
from app.models.replay import Replay, ReplayRoundUtil, UtilModel
from app.models.round import Round
from app.models.site_events import SiteEvent, SiteEventMark, SiteEventResolution
from app.services import replay_gaps, site_health_checks

@compiles(JSONB, "sqlite")
def _jsonb(type_, compiler, **kw): return "JSON"


T = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[UtilModel.__table__, ReplayRoundUtil.__table__,
                                             SiteEvent.__table__, SiteEventResolution.__table__, SiteEventMark.__table__,
                                             Match.__table__, Round.__table__, ImpactScore.__table__,
                                             Replay.__table__, MapPoolPatchNotes.__table__])
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


def add_match(db, external_id, created, scored=False):
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Ascent", created_at=created)
    db.add(match)
    db.flush()
    rnd = Round(match_id=match.id, round_number=1)
    db.add(rnd)
    db.flush()
    if scored:
        db.add(ImpactScore(round_id=rnd.id, match_player_id=1, kill_impact=0, death_impact=0, impact=0))
    db.commit()
    return match


def add_replay(db, match, status="linked", kill_impact=None):
    replay = Replay(match_uuid=str(uuid4()), match_id=match.id if match else None, map_name="Ascent", round_count=1,
                    format_version=1, recipe="r", game_branch="b", source_sha256="s", source="upload",
                    link_status=status, link_inputs={}, kill_impact=kill_impact, linked_at=T)
    db.add(replay)
    db.commit()
    return replay


def test_a_match_without_impact_rows_after_an_hour_shows(db):
    add_match(db, "old-unscored", T - timedelta(hours=2))
    add_match(db, "new-unscored", T - timedelta(minutes=30))
    add_match(db, "scored", T - timedelta(hours=2), scored=True)
    [item] = site_health_checks.unscored_matches(db, T)
    assert item["key"] == "old-unscored"
    assert item["first_seen"] == T - timedelta(hours=2)
    assert item["link"].endswith("/matches/old-unscored")


def test_unscored_matches_are_capped(db, monkeypatch):
    monkeypatch.setattr(site_health_checks, "UNSCORED_CAP", 2)
    for n in range(4):
        add_match(db, f"m{n}", T - timedelta(hours=2, minutes=n))
    assert len(site_health_checks.unscored_matches(db, T)) == 3, "one past the cap: the card says it was cut"


def test_a_linked_replay_without_kill_impact_on_a_scored_match_shows(db):
    scored = add_match(db, "s", T, scored=True)
    unscored = add_match(db, "u", T)
    shown = add_replay(db, scored)
    add_replay(db, unscored)
    done = add_match(db, "d", T, scored=True)
    add_replay(db, done, kill_impact={"fingerprint": "x"})
    other = add_match(db, "o", T, scored=True)
    add_replay(db, other, status="unlinked")
    [item] = site_health_checks.kill_impact_null(db)
    assert item["key"] == str(shown.id)
    assert item["context"] == {"match_id": scored.id, "map_name": "Ascent"}
    assert item["reproduce"].startswith("read-only: SELECT") and str(shown.match_uuid) in item["reproduce"]
    assert "--refresh-impact" in item["reproduce"] and "EVERY linked replay" in item["reproduce"]
    assert f"--uuid {shown.match_uuid} --refresh-impact" not in item["reproduce"]
    assert item["first_seen"] == T


def test_map_pool_notes_with_an_error_show(db):
    db.add_all([
        MapPoolPatchNotes(url="https://example.test/a", patch="11.01", published_at=T, first_seen_at=T,
                          miss_count=3, last_error="no map section"),
        MapPoolPatchNotes(url="https://example.test/b", patch="11.02", published_at=T, first_seen_at=T, miss_count=0)])
    db.commit()
    [item] = site_health_checks.map_pool_notes(db)
    assert (item["key"], item["error"], item["link"]) == ("https://example.test/a", "no map section",
                                                           "https://example.test/a")
    assert item["context"]["miss_count"] == 3 and item["context"]["patch"] == "11.01"
    assert item["first_seen"] == T


def test_the_new_checks_return_nothing_when_the_tables_are_missing(db):
    for table in (MapPoolPatchNotes.__table__, ImpactScore.__table__):
        table.drop(db.get_bind())
    assert site_health_checks.map_pool_notes(db) == []
    assert site_health_checks.unscored_matches(db, T) == []
    assert site_health_checks.kill_impact_null(db) == []

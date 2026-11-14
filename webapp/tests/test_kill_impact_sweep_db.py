"""SQLite selection and upload-store/link-to-sweep coverage using synthetic replays."""

import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import KillEvent, Round
from app.models.impact_score import ImpactScore
from app.models.replay import Replay
from app.replays import db as replay_db
from app.replays.store import store_replay
from app.services import kill_impact_sweep, replay_impact, site_health_checks
from tests.replays.test_replay_store import TABLES, add_match as add_linkable_match
from tests.replays.test_replay_store import condensed  # noqa: F401 -- shared synthetic fixture
from tests.test_site_health_checks import T, add_match, add_replay


@pytest.fixture
def factory(monkeypatch):
    # test_site_health_checks registers the JSONB-as-JSON SQLite compiler.
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[*TABLES, ImpactScore.__table__])
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


@pytest.fixture
def candidates(factory):
    with factory() as db:
        a = add_replay(db, add_match(db, "a", T, scored=True))
        add_replay(db, add_match(db, "b", T, scored=True),
                   kill_impact={"v": 1, "fingerprint": "old", "kills": {}})
        add_replay(db, add_match(db, "c", T, scored=True), status="unlinked")
        add_replay(db, add_match(db, "d", T))
        e = add_replay(db, add_match(db, "e", T, scored=True))
        add_replay(db, None)  # A linked status alone is insufficient.
        return a.id, e.id


def test_selection_is_newest_first_skips_cooldowns_and_respects_limit(factory, candidates):
    a_id, e_id = candidates
    with factory() as db:
        assert kill_impact_sweep.pending_replay_ids(db, 5, set()) == [e_id, a_id]
        assert kill_impact_sweep.pending_replay_ids(db, 5, {e_id}) == [a_id]
        assert kill_impact_sweep.pending_replay_ids(db, 1, set()) == [e_id]


def test_selection_matches_the_missing_split_health_check(factory, candidates):
    a_id, e_id = candidates
    with factory() as db:
        health_ids = {int(row["key"]) for row in site_health_checks.kill_impact_null(db)}
        assert health_ids == {a_id, e_id}
        assert set(kill_impact_sweep.pending_replay_ids(db, 5, set())) == health_ids


@pytest.fixture
def linked_upload(factory, condensed):
    """Store and link a condensed synthetic upload through the unchanged production path."""
    with factory() as db:
        match, _ = add_linkable_match(db)
        rnd = db.scalars(select(Round).where(Round.match_id == match.id).order_by(Round.id)).first()
        kill = db.scalars(select(KillEvent).where(KillEvent.round_id == rnd.id).order_by(KillEvent.id)).first()
        db.add(ImpactScore(round_id=rnd.id, match_player_id=kill.killer_match_player_id,
                           kill_impact=0, death_impact=0, impact=0))
        db.commit()
        result = store_replay(db, condensed, source="upload")
        assert (result.action, result.link_status) == ("stored", "linked")
        replay = db.get(Replay, result.replay_id)
        assert replay.match_id == match.id and replay.kill_impact is None
        assert {int(row["key"]) for row in site_health_checks.kill_impact_null(db)} == {replay.id}
        return replay.id, match.id, kill.id


def test_a_stored_linked_upload_gets_a_current_split_and_leaves_health(factory, linked_upload, monkeypatch):
    """Exercise store_replay(source='upload'), its real link, refresh and a fresh page read."""
    replay_id, match_id, kill_id = linked_upload
    computed_matches = []

    def compute_split(session, current_match_id):
        computed_matches.append(current_match_id)
        return {"v": 1, "fingerprint": replay_db.impact_fingerprint(session, current_match_id),
                "kills": {str(kill_id): [1.0, 2.0]}}

    monkeypatch.setattr(replay_impact, "compute_split", compute_split)
    failed_until = {}
    counts = kill_impact_sweep.run_pass(factory, threading.Event(), failed_until, now=0.0,
                                        paused=lambda: False, pause_s=0)
    assert counts == {"stored": 1}
    assert computed_matches == [match_id] and failed_until == {}
    with factory() as db:
        replay = db.get(Replay, replay_id)
        assert replay_db.kill_impact_for_page(db, replay) == {kill_id: [1.0, 2.0]}
        assert site_health_checks.kill_impact_null(db) == []


def test_a_refused_upload_stays_missing_and_cools_down(factory, linked_upload, monkeypatch, caplog):
    replay_id, _, _ = linked_upload

    def refuse(session, match_id):
        raise replay_impact.SplitRefused("x")

    monkeypatch.setattr(replay_impact, "compute_split", refuse)
    failed_until = {}
    counts = kill_impact_sweep.run_pass(factory, threading.Event(), failed_until, now=0.0,
                                        paused=lambda: False, pause_s=0)
    assert counts == {"failed": 1}
    assert failed_until == {replay_id: 6 * 3600}
    assert "per-kill Impact not stored: x" in caplog.text
    with factory() as db:
        replay = db.get(Replay, replay_id)
        assert replay.kill_impact is None
        assert {int(row["key"]) for row in site_health_checks.kill_impact_null(db)} == {replay_id}
    assert kill_impact_sweep.run_pass(factory, threading.Event(), failed_until, now=1.0,
                                      paused=lambda: False, pause_s=0) == {}

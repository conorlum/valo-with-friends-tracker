"""Drain ownership, expiry, clock accounting and the shared offline deploy contract."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import threading

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models.deploy_drain import DeployDrain
from app.services import deploy_drain as service


NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
SHA = "a" * 40
FIXTURES = Path(__file__).parent / "fixtures" / "deploy"


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[DeployDrain.__table__])
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def site():
    return service.SiteFacts(cycle_at=NOW.timestamp() + 1, control_in_flight=0,
                             heights_open=0, util_phase="idle", util_child_running=False,
                             uploads_receiving=0, uploads_last_end_age_s=None)


def start(db, **kwargs):
    return service.request(db, "1", SHA, NOW, **kwargs)["drain"]


def test_new_drain_and_takeover_preserve_start_and_require_a_new_cycle(db):
    assert service.active(db, NOW) is None
    response = service.request(db, "1", SHA, NOW)
    row = response["drain"]
    assert response["result"] == "started" and response["superseded"] is None
    assert row.phase == "draining" and row.outcome is None
    assert utc(row.expires_at) == NOW + timedelta(hours=2)
    assert utc(row.started_at) == utc(row.requested_at) == NOW
    later = NOW + timedelta(minutes=30)
    response = service.request(db, "2", "b" * 40, later)
    row = response["drain"]
    assert response["result"] == "took_over" and response["superseded"] == "1"
    assert (row.run_id, row.sha, row.superseded) == ("2", "b" * 40, "1")
    assert utc(row.started_at) == NOW and utc(row.requested_at) == later
    assert utc(row.expires_at) == later + service.EXPIRY
    assert len(db.scalars(select(DeployDrain)).all()) == 1


@pytest.mark.parametrize("phase", ["draining", "deploying"])
def test_same_owner_request_is_idempotent_and_only_renews_expiry(db, phase):
    start(db)
    taken_at = NOW + timedelta(minutes=10)
    row = service.request(db, "2", "b" * 40, taken_at)["drain"]
    service.owner_poll(db, "2", taken_at, waiting_total=7)
    if phase == "deploying":
        service.mark_deploying(db, "2", taken_at)
    before = service.to_json(row)
    assert before["superseded"] == "1"
    retried_at = NOW + timedelta(minutes=30)
    response = service.request(db, "2", "c" * 40, retried_at, skip_drain=phase == "draining")
    assert response["result"] == "started" and response["superseded"] is None
    db.expire_all()
    stored = db.scalars(select(DeployDrain)).one()
    expected = {**before, "expires_at": (retried_at + service.EXPIRY).isoformat()}
    assert service.to_json(response["drain"]) == service.to_json(stored) == expected


def test_deploying_is_busy_without_changing_any_field(db):
    start(db)
    service.mark_deploying(db, "1", NOW)
    before = service.to_json(service.active(db, NOW))
    response = service.request(db, "2", "b" * 40, NOW + timedelta(minutes=10))
    assert response["result"] == "busy" and response["superseded"] is None
    assert service.to_json(response["drain"]) == before


def test_expired_row_is_closed_at_expiry_before_request(db):
    old = start(db)
    expiry = NOW + service.EXPIRY
    later = expiry + timedelta(hours=5)
    response = service.request(db, "2", "b" * 40, later)
    assert response["result"] == "started" and response["drain"].id != old.id
    db.refresh(old)
    assert old.outcome == "expired" and utc(old.ended_at) == expiry
    assert service.drained(db, NOW, later, later) == service.EXPIRY


def test_database_rejects_two_open_rows_but_accepts_history(db):
    start(db)
    db.add(DeployDrain(run_id="2", sha=SHA, phase="draining", started_at=NOW,
                       requested_at=NOW, expires_at=NOW + service.EXPIRY, skip_drain=False))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    service.finish(db, "1", "ok", NOW)
    assert service.request(db, "2", SHA, NOW)["result"] == "started"


def test_owner_poll_renews_expiry_without_moving_start(db):
    start(db)
    for minutes in (30, 60, 90, 120, 150, 180):
        at = NOW + timedelta(minutes=minutes)
        row = service.owner_poll(db, "1", at, waiting_total=7)
        assert utc(row.expires_at) == at + service.EXPIRY
        assert utc(row.started_at) == NOW and row.waiting_total == 7
        assert service.active(db, at) is not None
    assert service.active(db, NOW + timedelta(hours=5)) is None
    row = db.scalars(select(DeployDrain)).one()
    assert row.outcome == "expired" and utc(row.ended_at) == NOW + timedelta(hours=5)


@pytest.mark.parametrize("operation", ["poll", "deploying", "finish", "forced"])
def test_non_owner_cannot_write(db, operation):
    at = datetime.now(timezone.utc)
    service.request(db, "1", SHA, at)
    before = service.to_json(service.active(db, at))
    with pytest.raises(service.NotOwner):
        if operation == "poll":
            service.owner_poll(db, "other", at, 0)
        elif operation == "deploying":
            service.mark_deploying(db, "other", at)
        elif operation == "forced":
            service.mark_forced(db, "other")
        else:
            service.finish(db, "other", "ok", at)
    assert service.to_json(service.active(db, at)) == before


@pytest.mark.parametrize("operation", ["poll", "deploying", "finish", "forced"])
def test_expired_owner_is_refused_and_expiry_is_persisted(db, operation):
    at = datetime.now(timezone.utc)
    service.request(db, "1", SHA, at - timedelta(hours=3))
    with pytest.raises(service.NotOwner):
        if operation == "poll":
            service.owner_poll(db, "1", at, 0)
        elif operation == "deploying":
            service.mark_deploying(db, "1", at)
        elif operation == "forced":
            service.mark_forced(db, "1")
        else:
            service.finish(db, "1", "failed", at)
    db.expire_all()
    row = db.scalars(select(DeployDrain)).one()
    assert row.outcome == "expired" and utc(row.ended_at) == at - timedelta(hours=1)


@pytest.mark.parametrize("outcome", ["ok", "failed"])
def test_last_outcome_skips_dry_runs_and_expired_rows(db, outcome):
    start(db)
    row = service.finish(db, "1", outcome, NOW + timedelta(minutes=1))
    assert row.commit == SHA and row.outcome == outcome
    target_id = row.id
    service.request(db, "2", "b" * 40, NOW + timedelta(minutes=2))
    dry = service.finish(db, "2", "dry_run", NOW + timedelta(minutes=3))
    assert dry.commit is None
    service.request(db, "3", "c" * 40, NOW + timedelta(minutes=4))
    service.active(db, NOW + timedelta(hours=4))
    assert service.last_outcome(db).id == target_id


def test_skip_drain_and_force(db):
    at = datetime.now(timezone.utc)
    row = service.request(db, "1", SHA, at, skip_drain=True)["drain"]
    assert row.phase == "deploying" and row.skip_drain and not row.forced
    service.mark_forced(db, "1")
    db.refresh(row)
    assert row.forced


def test_takeover_can_skip_drain(db):
    start(db)
    row = service.request(db, "2", SHA, NOW, skip_drain=True)["drain"]
    assert row.phase == "deploying" and row.skip_drain


@pytest.mark.parametrize("offset,minutes", [(0, 30), (15, 15), (45, 0)])
def test_drained_counts_only_interval_overlap(db, offset, minutes):
    start(db)
    service.finish(db, "1", "ok", NOW + timedelta(minutes=30))
    assert service.drained(db, NOW + timedelta(minutes=offset), NOW + timedelta(hours=1),
                           NOW + timedelta(hours=1)) == timedelta(minutes=minutes)


def test_drained_caps_active_at_now_and_expiry_and_sums_history(db):
    assert service.drained(db, NOW, NOW, NOW) == timedelta(0)
    start(db)
    assert service.drained(db, NOW, NOW + timedelta(hours=4), NOW + timedelta(minutes=20)) == timedelta(minutes=20)
    assert service.drained(db, NOW, NOW + timedelta(hours=6), NOW + timedelta(hours=5)) == service.EXPIRY
    service.request(db, "2", SHA, NOW + timedelta(hours=5))
    service.finish(db, "2", "ok", NOW + timedelta(hours=5, minutes=30))
    assert service.drained(db, NOW, NOW + timedelta(hours=6), NOW + timedelta(hours=6)) == timedelta(hours=2, minutes=30)


def test_paused_reads_fresh_and_fails_open_with_a_log(db, caplog):
    assert not service.paused(db, NOW)
    start(db)
    assert service.paused(db, NOW)
    service.finish(db, "1", "ok", NOW)
    assert not service.paused(db, NOW)

    class Broken:
        def get_bind(self):
            raise RuntimeError("database unavailable")

    assert service.paused(Broken(), NOW) is False
    assert "drain" in caplog.text.lower()


def test_paused_observes_another_sessions_finish(db):
    row = start(db)
    assert service.paused(db, NOW)
    with Session(db.get_bind()) as other:
        other.execute(update(DeployDrain).where(DeployDrain.id == row.id)
                      .values(ended_at=NOW, outcome="ok", commit=SHA))
        other.commit()
    assert not service.paused(db, NOW)


def test_postgres_lock_uses_transaction_advisory_lock():
    from types import SimpleNamespace

    class Connection:
        calls = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

    connection = Connection()
    service._lock(connection)
    assert connection.calls == [("SELECT pg_advisory_xact_lock(:key)", {"key": service.DRAIN_LOCK})]
    assert service.DRAIN_LOCK != 7_346_120_117


def test_busy_fixture_lists_every_unmet_item(db, site):
    row = start(db)
    busy_site = replace(site, cycle_at=NOW.timestamp() - 1, control_in_flight=2,
                        heights_open=1, util_phase="collecting", util_child_running=True,
                        uploads_receiving=1, uploads_last_end_age_s=30)
    status = service.evaluate(fixture("worker_health_busy.json"), busy_site, row, NOW)
    assert status == {
        "quiet": False, "uploads_quiet": False,
        "waiting": {"site_cycle": 1, "site_control_in_flight": 2, "site_heights_open": 1,
                    "site_util": 1, "site_util_child": 1, "worker_control_queued": 3,
                    "worker_control_running": 2, "worker_warm_builds": 1,
                    "worker_heights_collecting": 1, "worker_heights_queued": 1,
                    "worker_heights_running": 1, "worker_util_building": 1,
                    "worker_parses_running": 1, "worker_cancelling": 2,
                    "site_uploads_receiving": 1, "worker_uploads_receiving": 1,
                    "site_uploads_recent": 1, "worker_uploads_recent": 1},
        "waiting_total": 23,
    }


def test_quiet_fixture_and_ignored_queued_parses(db, site):
    row = start(db)
    health = fixture("worker_health_quiet.json")
    assert service.evaluate(health, site, row, NOW) == {
        "quiet": True, "uploads_quiet": True, "waiting": {}, "waiting_total": 0}
    health["drain"]["parses"]["queued"] = 4
    health["drain"]["runs"]["active"] = 4
    assert service.evaluate(health, site, row, NOW)["quiet"]


@pytest.mark.parametrize("change,key,count", [
    ("cancelling", "worker_cancelling", 2), ("hold", "worker_hold", 1),
    ("cycle", "site_cycle", 1), ("unreachable", "worker_unreachable", 1),
])
def test_readiness_barriers(db, site, change, key, count):
    row = start(db)
    health = fixture("worker_health_quiet.json")
    if change == "cancelling":
        health["drain"]["cancelling"] = 2
    elif change == "hold":
        health["drain"]["hold"] = False
    elif change == "cycle":
        site = replace(site, cycle_at=NOW.timestamp())
    else:
        health = None
    status = service.evaluate(health, site, row, NOW)
    assert not status["quiet"] and status["waiting"][key] == count


@pytest.mark.parametrize("side", ["site", "worker"])
@pytest.mark.parametrize("receiving,age,quiet", [(1, None, False), (0, 30, False),
                                              (0, 60, True), (0, None, True)])
def test_upload_quiet_window(db, site, side, receiving, age, quiet):
    row = start(db)
    health = fixture("worker_health_quiet.json")
    if side == "site":
        site = replace(site, uploads_receiving=receiving, uploads_last_end_age_s=age)
    else:
        health["drain"]["uploads"] = {"receiving": receiving, "last_end_age_s": age}
    status = service.evaluate(health, site, row, NOW)
    assert status["uploads_quiet"] is quiet and status["quiet"] is quiet


def test_route_contract_matches_serialized_row_and_status(db, site):
    row = start(db)
    examples = fixture("routes.json")
    assert set(examples) == {"drain", "poll", "status", "deploying", "force", "resume"}
    assert examples["drain"]["request"] == {"run_id": "1", "sha": SHA, "skip_drain": False}
    assert examples["drain"]["response"] == {
        "result": "started", "drain": service.to_json(row), "superseded": None}
    polled_at = NOW + timedelta(minutes=1)
    status = service.evaluate(fixture("worker_health_quiet.json"), site, row, polled_at)
    row = service.owner_poll(db, "1", polled_at, status["waiting_total"])
    for route in ("poll", "status"):
        response = examples[route]["response"]
        assert response["drain"] == service.to_json(row) and response["status"] == status
        assert response["limit_s"] == service.LIMIT_S and response["now"] == polled_at.timestamp()
        assert response["commits"] == {"worker": "b" * 40, "tasks": None}
    service.mark_deploying(db, "1", polled_at)
    assert examples["deploying"]["response"] == {"drain": service.to_json(row)}
    assert examples["force"]["response"] == {"cancelled": [{"kind": "control", "key": "sample-control"}]}
    row = service.finish(db, "1", "ok", NOW + timedelta(minutes=10))
    assert examples["resume"]["response"] == {"drain": service.to_json(row)}


def test_migration_and_model_both_enforce_the_open_index():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.schema import CreateIndex
    from sqlalchemy.dialects import postgresql

    path = Path(__file__).parents[1] / "alembic" / "versions" / "0034_deploy_drains.py"
    spec = importlib.util.spec_from_file_location("deploy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0033"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        data = dict(run_id="1", sha=SHA, phase="draining", started_at=NOW,
                    requested_at=NOW, expires_at=NOW + service.EXPIRY, skip_drain=False)
        conn.execute(DeployDrain.__table__.insert(), data)
        with pytest.raises(IntegrityError):
            conn.execute(DeployDrain.__table__.insert(), {**data, "run_id": "2"})
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
    index = next(i for i in DeployDrain.__table__.indexes if i.name == "ux_deploy_drains_open")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE INDEX ux_deploy_drains_open" in ddl and "WHERE ended_at IS NULL" in ddl
    engine.dispose()


@pytest.mark.postgres
def test_concurrent_requests_serialize_on_migrated_scratch_database(scratch_database, pg_factory):
    from _postgres import postgres_url_or_skip

    postgres_url_or_skip(writes=True)
    barrier = threading.Barrier(2)
    results, errors = [], []

    def request(run_id):
        try:
            with pg_factory() as session:
                barrier.wait(timeout=10)
                results.append(service.request(session, run_id, SHA, NOW)["result"])
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=request, args=(str(i),)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads) and not errors
    assert sorted(results) == ["started", "took_over"]
    with pg_factory() as session:
        assert len(session.scalars(select(DeployDrain).where(DeployDrain.ended_at.is_(None))).all()) == 1

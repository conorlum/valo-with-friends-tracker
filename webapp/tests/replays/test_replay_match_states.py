"""The per-match state table (app/services/replay_match_states.py, migration 0023): `refresh` writes exactly what
`replay_progress.compute` says per match (R4), and hands that result to the panel's cache. SQLite fixtures, plus
PostgreSQL tests that skip without VALO_TEST_DATABASE_URL."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_gaps_task import _gap_run  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.models.replay import Replay, ReplayMatchState, ReplayRound  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services import replay_match_states as states  # noqa: E402
from app.services import replay_progress as progress  # noqa: E402

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
KEYS = {"written", "deleted", "unchanged", "locked_out", "seconds"}


def rows(db) -> dict:
    db.expire_all()
    return {str(r.match_uuid).lower(): r for r in db.query(ReplayMatchState)}


def naive(moment):
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


def assert_rows_match_compute(db):
    body = progress.compute(db)
    found = rows(db)
    assert found.keys() == body["matches"].keys() and found
    for uuid, match in body["matches"].items():
        row = found[uuid]
        assert (row.done, row.total, row.first_step) == (match["done"], match["total"], match["first"])
        assert (row.recipe, row.control_revision, row.gaps_revision) == (
            body["judged_against"]["recipe"], body["judged_against"]["control_revision"],
            body["judged_against"]["gaps_revision"])


def test_the_constants():
    assert states.REFRESH_S == 60 and states.STALE_S == 5 * states.REFRESH_S
    assert states.LOCK_ID != remote.LOCK_ID


def test_nothing_computed(db, linked):
    out = states.refresh(db, now=NOW)
    assert set(out) == KEYS and out["written"] == 1 and out["locked_out"] == 0
    assert_rows_match_compute(db)
    assert next(iter(rows(db).values())).first_step == "control_missing"


def test_partly_computed_with_gap_runs_a_failed_row_and_an_older_recipe(factory, db, linked):
    n = linked.round_count
    for r in range(1, n + 1):
        put_row(db, linked, r)
    put_row(db, linked, 3, status="failed")
    _gap_run(factory, linked, 1)
    _gap_run(factory, linked, 2, status="failed")
    db.expire_all()
    states.refresh(db, now=NOW)
    assert_rows_match_compute(db)
    row = next(iter(rows(db).values()))
    assert row.done == 1 and row.first_step == "control_failed"
    linked.recipe = "000000000000.c9.f1.000000000"
    db.commit()
    out = states.refresh(db, now=NOW)
    assert out["written"] == 1
    assert_rows_match_compute(db)
    assert next(iter(rows(db).values())).first_step == "reparse_local"


def test_a_second_refresh_writes_nothing_and_moves_checked_at(db, linked):
    states.refresh(db, now=NOW)
    later = NOW + timedelta(seconds=60)
    out = states.refresh(db, now=later)
    assert out["written"] == 0 and out["unchanged"] == 1 and out["deleted"] == 0
    assert naive(next(iter(rows(db).values())).checked_at) == naive(later)


def test_a_deleted_replay_loses_its_row(db, linked):
    states.refresh(db, now=NOW)
    assert len(rows(db)) == 1
    db.query(ReplayRound).delete()      # SQLite here has no foreign-key cascade
    db.delete(db.get(Replay, linked.id))
    db.commit()
    out = states.refresh(db, now=NOW)
    assert out["deleted"] == 1 and rows(db) == {}


def test_demo_mode_writes_nothing(db, linked, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    out = states.refresh(db, now=NOW)
    assert out == {"written": 0, "deleted": 0, "unchanged": 0, "locked_out": 0, "seconds": 0}
    assert rows(db) == {}


def test_seed_hands_the_refreshs_result_to_the_panel_cache(db, linked, monkeypatch):
    monkeypatch.setattr(progress, "_cached", None)
    started = 5000.0
    monkeypatch.setattr(states.time, "monotonic", lambda: started)
    states.refresh(db, now=NOW)
    monkeypatch.setattr(progress, "compute", lambda _db: pytest.fail("recomputed"))
    body = progress.cached(db, now=started + progress.TTL_S - 1)
    assert set(body["matches"]) == set(rows(db))
    recomputed = {"matches": {}}
    monkeypatch.setattr(progress, "compute", lambda _db: recomputed)
    assert progress.cached(db, now=started + progress.TTL_S) is recomputed


def test_pg_a_held_lock_locks_the_refresh_out():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from _postgres import postgres_url_or_skip

    engine = create_engine(postgres_url_or_skip())
    make = sessionmaker(bind=engine)
    holder, other = make(), make()
    try:
        holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": states.LOCK_ID})
        out = states.refresh(other, now=NOW)
        assert out["locked_out"] == 1 and out["written"] == 0
    finally:
        other.close()
        holder.rollback()
        holder.close()
        engine.dispose()


def test_pg_migration_0023_creates_and_drops_the_table():
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from _postgres import postgres_url_or_skip

    path = HERE.parents[1] / "alembic" / "versions" / "0023_replay_match_states.py"
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0023", "0022")

    engine = create_engine(postgres_url_or_skip(writes=True))
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                if "replay_match_states" in inspect(connection).get_table_names():
                    migration.downgrade()
                migration.upgrade()
                assert "replay_match_states" in inspect(connection).get_table_names()
                columns = {c["name"] for c in inspect(connection).get_columns("replay_match_states")}
                assert columns == {"match_uuid", "done", "total", "first_step", "checked_at", "recipe",
                                   "control_revision", "gaps_revision"}
                migration.downgrade()
                assert "replay_match_states" not in inspect(connection).get_table_names()
                migration.upgrade()
        finally:
            transaction.rollback()
    engine.dispose()

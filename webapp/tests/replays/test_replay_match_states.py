"""The per-match state table (app/services/replay_match_states.py, migration 0024): `refresh` writes exactly what
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


def test_a_refresh_is_committed(db, linked):
    states.refresh(db, now=NOW)
    db.rollback()                       # an uncommitted write would vanish here
    assert len(rows(db)) == 1
    later = NOW + timedelta(seconds=60)
    states.refresh(db, now=later)
    db.rollback()
    assert naive(next(iter(rows(db).values())).checked_at) == naive(later)


def test_a_failure_after_the_flush_rolls_the_pass_back(db, linked, monkeypatch):
    states.refresh(db, now=NOW)
    linked.recipe = "000000000000.c9.f1.000000000"
    db.commit()

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected after the flush")

    monkeypatch.setattr(states, "update", fail)
    with pytest.raises(RuntimeError):
        states.refresh(db, now=NOW + timedelta(seconds=60))
    row = next(iter(rows(db).values()))
    assert row.first_step == "control_missing" and naive(row.checked_at) == naive(NOW)


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


def test_pg_migration_0024_creates_and_drops_the_table():
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from _postgres import postgres_url_or_skip

    path = HERE.parents[1] / "alembic" / "versions" / "0024_replay_match_states.py"
    spec = importlib.util.spec_from_file_location("migration_0024", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0024", "0023")

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


# --- S3: the refresher thread -------------------------------------------------------------------------------------

class _Session:
    def __init__(self):
        self.closed = False
        self.rolled_back = 0

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


class _Stop:
    """A stop event whose wait() answers from a list, then True."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.waited = []

    def wait(self, timeout=None):
        self.waited.append(timeout)
        return self.answers.pop(0) if self.answers else True


def _factory(made):
    def make():
        made.append(_Session())
        return made[-1]
    return make


def test_run_forever_one_pass_when_stop_is_already_set(monkeypatch, caplog):
    import threading
    calls = []
    monkeypatch.setattr(states, "refresh", lambda db: calls.append(db) or states._counts(written=2, deleted=1, unchanged=3))
    stop, made = threading.Event(), []
    stop.set()
    with caplog.at_level("INFO", logger=states.log.name):
        states.run_forever(_factory(made), stop, every_s=0)
    assert len(calls) == 1 and made[0].closed
    assert "replay states: written 2, deleted 1, unchanged 3 in" in caplog.text


def test_run_forever_survives_a_failing_refresh(monkeypatch, caplog):
    calls = []

    def refresh(db):
        calls.append(db)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return states._counts()

    monkeypatch.setattr(states, "refresh", refresh)
    made = []
    stop = _Stop(False, True)
    with caplog.at_level("INFO", logger=states.log.name):
        states.run_forever(_factory(made), stop, every_s=7)
    assert len(calls) == 2 and all(s.closed for s in made) and made[0].rolled_back == 1
    assert "boom" in caplog.text and stop.waited == [7, 7]


def test_run_forever_logs_a_locked_out_pass_without_error(monkeypatch, caplog):
    monkeypatch.setattr(states, "refresh", lambda db: states._counts(locked_out=1))
    made = []
    with caplog.at_level("INFO", logger=states.log.name):
        states.run_forever(_factory(made), _Stop(True), every_s=0)
    assert "written 0, deleted 0, unchanged 0" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"] and made[0].closed


def test_start_in_demo_mode_starts_nothing(monkeypatch):
    import threading
    monkeypatch.setattr(settings, "demo_mode", True)
    before = {t.name for t in threading.enumerate()}
    assert states.start(lambda: None) is None
    assert {t.name for t in threading.enumerate()} == before


def test_start_otherwise_starts_a_daemon_thread(monkeypatch):
    import threading
    import time
    monkeypatch.setattr(settings, "demo_mode", False)
    seen = []
    monkeypatch.setattr(states, "run_forever", lambda *a, **k: seen.append(threading.current_thread()))
    stop = states.start(lambda: None)
    assert isinstance(stop, threading.Event)
    for _ in range(100):
        if seen:
            break
        time.sleep(0.01)
    assert seen and seen[0].name == "replay-states" and seen[0].daemon


def test_lifespan_starts_the_refresher(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    from app import main
    from app.db import SessionLocal
    from app.services import replay_archive_sync

    monkeypatch.setattr(remote, "start", lambda factory: None)
    monkeypatch.setattr(replay_archive_sync, "start", lambda factory: None)
    mock = MagicMock(return_value=None)
    monkeypatch.setattr(states, "start", mock)

    async def go():
        async with main.lifespan(main.app):
            pass

    asyncio.run(go())
    mock.assert_called_once_with(SessionLocal)


# ---- S4: the pages read the table --------------------------------------------------------------------------


def _forbid(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("match_states must not plan or compute")
    monkeypatch.setattr(progress, "compute", boom)
    monkeypatch.setattr(progress, "cached", boom)
    from app.services import replay_control, replay_gaps
    monkeypatch.setattr(replay_control, "plan", boom)
    monkeypatch.setattr(replay_gaps, "plan_gaps", boom)


def test_match_states_reads_the_stored_rows_without_planning(db, linked, monkeypatch):
    uuid = str(linked.match_uuid).lower()
    states.refresh(db)
    _forbid(monkeypatch)
    out = progress.match_states(db, [uuid.upper()])
    assert out == {uuid: {"done": 0, "total": linked.round_count, "label": "Map control never computed",
                          "automatic": True}}


def test_match_states_empty_table_and_demo_mode(db, linked, monkeypatch):
    uuid = str(linked.match_uuid).lower()
    _forbid(monkeypatch)
    assert progress.match_states(db, [uuid]) == {}
    assert progress.match_states(db, []) == {}
    db.add(ReplayMatchState(match_uuid=uuid, done=0, total=1, first_step=None, checked_at=datetime.now(timezone.utc),
                            recipe=progress.uploads.site_recipe(), control_revision=progress.cf.CONTROL_REVISION,
                            gaps_revision=progress.replay_gaps.GAPS_REVISION))
    db.commit()
    assert uuid in progress.match_states(db, [uuid])
    monkeypatch.setattr(settings, "demo_mode", True)
    assert progress.match_states(db, [uuid]) == {}


def test_match_states_survives_a_missing_table(db, linked):
    uuid = str(linked.match_uuid).lower()
    ReplayMatchState.__table__.drop(db.get_bind())
    assert progress.match_states(db, [uuid]) == {}


def test_match_states_ignores_a_non_uuid_id(db, linked):
    uuid = str(linked.match_uuid).lower()
    states.refresh(db)
    assert list(progress.match_states(db, ["not-a-uuid", uuid, "", None])) == [uuid]


@pytest.mark.parametrize("change", [
    {"recipe": "other"}, {"control_revision": -1}, {"gaps_revision": -1}, {"first_step": "no_such_step"},
    {"checked_at": datetime.now(timezone.utc) - timedelta(seconds=states.STALE_S + 30)},
])
def test_match_states_leaves_out_an_unusable_row(db, linked, change):
    uuid = str(linked.match_uuid).lower()
    states.refresh(db)
    assert uuid in progress.match_states(db, [uuid])
    row = db.query(ReplayMatchState).one()
    for k, v in change.items():
        setattr(row, k, v)
    db.commit()
    assert progress.match_states(db, [uuid]) == {}


def test_the_cell_says_not_checked_yet_only_for_a_watchable_match():
    from types import SimpleNamespace as NS

    from app.templates import templates
    uuid = "0f452716-1e90-4782-afba-29229fdab922"
    match = NS(external_id=uuid, team1_rounds_won=13, team2_rounds_won=7, map_name="Sunset", played_at=None)

    def page(watchable):
        return templates.env.get_template("matches/list.html").render(
            matches=[match], showing_own_matches=False, current_player=None, watchable=watchable, replay_states={})

    assert 'title="Not checked yet"' in page({uuid})
    assert 'title="Not checked yet"' not in page({"x"})
    assert page({"x"}).count("No replay for this match") == 2


def test_sessions_use_the_same_function():
    from app.routers import sessions
    assert sessions.match_states is progress.match_states

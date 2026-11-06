"""The background Critical Util build: one child per process at a time, never raising into a request."""

import sys

from app.services import util_rebuild

REAL_SPAWN = util_rebuild._spawn        # before the suite's guard (tests/conftest.py) replaces it per test


class Child:
    def __init__(self, done=False):
        self.pid, self.done = 1234, done

    def poll(self):
        return 0 if self.done else None


def test_one_child_at_a_time_then_another_once_it_has_finished(monkeypatch):
    spawned = []
    monkeypatch.setattr(util_rebuild, "_spawn", lambda: spawned.append(Child()) or spawned[-1])
    assert util_rebuild.request() is True and util_rebuild.running()
    assert util_rebuild.request() is False and len(spawned) == 1, "the first is still building"
    spawned[0].done = True
    assert not util_rebuild.running()
    assert util_rebuild.request() is True and len(spawned) == 2


def test_a_child_that_cannot_start_is_logged_not_raised(monkeypatch):
    def fail():
        raise OSError("no fork")

    monkeypatch.setattr(util_rebuild, "_spawn", fail)
    assert util_rebuild.request() is False and not util_rebuild.running()


def test_the_child_runs_this_module_from_the_webapp_folder(monkeypatch):
    seen = {}
    monkeypatch.setattr(util_rebuild.subprocess, "Popen", lambda args, **kw: seen.update(args=args, **kw) or Child())
    REAL_SPAWN()
    assert seen["args"] == [sys.executable, "-m", "app.services.util_rebuild"]
    assert seen["cwd"] == util_rebuild.WEBAPP and (util_rebuild.WEBAPP / "app" / "main.py").is_file()


def test_main_builds_through_ensure_values(monkeypatch, capsys):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.db
    from app.replays import map_feature_sources
    from app.services import replay_util

    monkeypatch.setattr(map_feature_sources, "_accel", None)       # main() registers it: undone after the test
    engine = create_engine("sqlite://")      # never the configured DATABASE_URL
    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(replay_util, "ensure_values", lambda db: {"rows": [1, 2, 3]})
    assert util_rebuild.main() == 0
    assert "stored 3 valued casts" in capsys.readouterr().out
    assert map_feature_sources._accel is not None, "the child decodes masks with numpy"


# ---- failures are recorded (R4) -------------------------------------------------------------------------------

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models.site_events import SiteEvent, SiteEventResolution
from app.services import site_health


@pytest.fixture
def health(monkeypatch):
    import app.db
    from app.replays import map_feature_sources

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(map_feature_sources, "_accel", None)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SiteEvent.__table__, SiteEventResolution.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(app.db, "SessionLocal", factory)
    return factory


def stored(factory):
    with factory() as s:
        return {(r.kind, r.key): r for r in s.scalars(select(SiteEvent))}


def test_a_build_that_raises_is_recorded_and_exits_3(health, monkeypatch):
    from app.services import replay_util

    def boom(db):
        raise RuntimeError("the corpus broke")

    monkeypatch.setattr(replay_util, "ensure_values", boom)
    assert util_rebuild.EXIT_RECORDED == 3
    assert util_rebuild.main() == 3
    row = stored(health)[("util_build_failed", "build")]
    assert row.error.startswith("RuntimeError: the corpus broke")
    assert row.context["traceback"] and len(row.context["traceback"]) <= 15


def test_a_good_build_clears_a_stored_failure(health, monkeypatch):
    from app.services import replay_util

    site_health.record("util_build_failed", "build", "RuntimeError: old", session_factory=health)
    assert ("util_build_failed", "build") in stored(health)
    monkeypatch.setattr(replay_util, "ensure_values", lambda db: {"rows": []})
    assert util_rebuild.main() == 0
    assert stored(health) == {}


def test_the_lock_held_exit_records_and_resolves_nothing(health, monkeypatch, capsys):
    import app.db

    site_health.record("util_build_failed", "build", "RuntimeError: old", session_factory=health)

    class Result:
        def scalar(self):
            return False

    class Holder:
        def execute(self, *a, **k):
            return Result()

        def commit(self):
            pass

        def close(self):
            pass

    class Dialect:
        name = "postgresql"

    class FakeEngine:
        dialect = Dialect()

        def connect(self):
            return Holder()

    monkeypatch.setattr(app.db, "engine", FakeEngine())
    assert util_rebuild.main() == 0
    assert "another process is building" in capsys.readouterr().out
    assert ("util_build_failed", "build") in stored(health), "the failure stays until a build succeeds"


class Done:
    pid = 77

    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


@pytest.mark.parametrize("code, queued", [(9, True), (-9, True), (3, False), (0, False)])
def test_a_finished_child_with_a_bad_exit_code_is_queued_once(monkeypatch, code, queued):
    monkeypatch.setattr(settings, "demo_mode", False)
    spawned = [Done(code), Child(done=False)]
    monkeypatch.setattr(util_rebuild, "_spawn", lambda: spawned.pop(0))
    assert util_rebuild.request() is True            # the child that will finish
    assert util_rebuild.request() is True            # reads its exit code, starts the next
    entries = [e for e in site_health._queue if e["kind"] == "util_build_failed"]
    if queued:
        [entry] = entries
        assert entry["key"] == "build" and entry["context"]["exit_code"] == code
        assert entry["context"]["pid"] == 77 and "ran_s" in entry["context"]
        assert str(code) in entry["error"]
    else:
        assert entries == []
    assert util_rebuild.request() is False           # the second child is still running: nothing more
    assert len([e for e in site_health._queue if e["kind"] == "util_build_failed"]) == len(entries)


def test_a_finished_child_is_read_once(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    spawned = [Done(9), Done(9), Done(9)]
    monkeypatch.setattr(util_rebuild, "_spawn", lambda: spawned.pop(0))
    util_rebuild.request()
    util_rebuild.request()      # reads child 1 (one entry), starts child 2
    assert len(site_health._queue) == 1
    util_rebuild.request()      # reads child 2 (a second entry), starts child 3; child 1 is not read again
    assert len(site_health._queue) == 2

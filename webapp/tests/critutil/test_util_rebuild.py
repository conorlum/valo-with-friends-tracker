"""The background Critical Util build: one child per process at a time, never raising into a request."""

import json
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
    monkeypatch.setattr(util_rebuild, "_spawn", lambda previous=None: spawned.append(Child()) or spawned[-1])
    assert util_rebuild.request() is True and util_rebuild.running()
    assert util_rebuild.request() is False and len(spawned) == 1, "the first is still building"
    spawned[0].done = True
    assert not util_rebuild.running()
    assert util_rebuild.request() is True and len(spawned) == 2


def test_a_child_that_cannot_start_is_logged_not_raised(monkeypatch):
    def fail(previous=None):
        raise OSError("no fork")

    monkeypatch.setattr(util_rebuild, "_spawn", fail)
    assert util_rebuild.request() is False and not util_rebuild.running()


def test_the_child_runs_this_module_from_the_webapp_folder(monkeypatch):
    seen = {}
    monkeypatch.setattr(util_rebuild.subprocess, "Popen", lambda args, **kw: seen.update(args=args, **kw) or Child())
    REAL_SPAWN()
    assert seen["args"] == [sys.executable, "-m", "app.services.util_rebuild"]
    REAL_SPAWN([{"error": "e", "context": None, "at": "2026-11-06T00:00:00+00:00"}])
    assert seen["args"][:4] == [sys.executable, "-m", "app.services.util_rebuild", "--previous"]
    assert json.loads(seen["args"][4])[0]["error"] == "e"
    assert seen["cwd"] == util_rebuild.WEBAPP and (util_rebuild.WEBAPP / "app" / "main.py").is_file()


def test_main_builds_through_ensure_values(monkeypatch, capsys):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.db
    from app.replays import map_feature_sources
    from app.services import replay_util

    monkeypatch.setattr(map_feature_sources, "_accel", None)       # main() registers it: undone after the test
    engine = create_engine("sqlite://")      # never the configured DATABASE_URL
    from app.db import Base
    from app.models.site_events import SiteEvent, SiteEventMark
    Base.metadata.create_all(engine, tables=[SiteEvent.__table__, SiteEventMark.__table__])
    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(replay_util, "ensure_values", lambda db: {"rows": [1, 2, 3]})
    assert util_rebuild.main([]) == 0
    assert "stored 3 valued casts" in capsys.readouterr().out
    assert map_feature_sources._accel is not None, "the child decodes masks with numpy"


# ---- failures are recorded (R4) -------------------------------------------------------------------------------

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models.site_events import SiteEvent, SiteEventMark, SiteEventResolution
from app.services import site_health


@pytest.fixture
def health(monkeypatch):
    import app.db
    from app.replays import map_feature_sources

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(map_feature_sources, "_accel", None)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SiteEvent.__table__, SiteEventResolution.__table__, SiteEventMark.__table__])
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
    assert util_rebuild.main([]) == 3
    row = stored(health)[("util_build_failed", "build")]
    assert row.error.startswith("RuntimeError: the corpus broke")
    assert row.context["traceback"] and len(row.context["traceback"]) <= 15


def test_a_handed_failure_is_written_before_the_build_and_its_success_clears_it(health, monkeypatch):
    # R4-4: the failure and the clear come from one process, in order
    from app.services import replay_util

    order = []
    real = site_health.record_now
    monkeypatch.setattr(site_health, "record_now", lambda *a, **k: order.append("record") or real(*a, **k))
    monkeypatch.setattr(replay_util, "ensure_values",
                        lambda db: order.append(("build", ("util_build_failed", "build") in stored(health)))
                        or {"rows": []})
    previous = {"error": "the build exited with code -9", "context": {"exit_code": -9},
                "at": "2026-11-06T00:00:00+00:00"}
    assert util_rebuild.main(["--previous", json.dumps(previous)]) == 0
    assert order == ["record", ("build", True)]
    assert stored(health) == {}


def test_a_handed_failure_stays_when_another_process_is_building(health, monkeypatch):
    import app.db

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
    previous = {"error": "the build exited with code 9", "context": {"exit_code": 9},
                "at": "2026-11-06T00:00:00+00:00"}
    assert util_rebuild.main(["--previous", json.dumps(previous)]) == 0
    assert stored(health)[("util_build_failed", "build")].error == "the build exited with code 9"


def test_a_failure_the_child_could_not_store_exits_unrecorded_for_the_parent(health, monkeypatch):
    # R4-4: EXIT_RECORDED only after a write that committed
    from app.services import replay_util

    def boom(db):
        raise RuntimeError("the corpus broke")

    monkeypatch.setattr(replay_util, "ensure_values", boom)
    monkeypatch.setattr(site_health, "record_now", lambda *a, **k: False)
    assert util_rebuild.main([]) == util_rebuild.EXIT_UNRECORDED


def test_a_good_build_clears_a_stored_failure(health, monkeypatch):
    from app.services import replay_util

    site_health.record_now("util_build_failed", "build", "RuntimeError: old", session_factory=health)
    assert ("util_build_failed", "build") in stored(health)
    monkeypatch.setattr(replay_util, "ensure_values", lambda db: {"rows": []})
    assert util_rebuild.main([]) == 0
    assert stored(health) == {}


def test_the_lock_held_exit_records_and_resolves_nothing(health, monkeypatch, capsys):
    import app.db

    site_health.record_now("util_build_failed", "build", "RuntimeError: old", session_factory=health)

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
    assert util_rebuild.main([]) == 0
    assert "another process is building" in capsys.readouterr().out
    assert ("util_build_failed", "build") in stored(health), "the failure stays until a build succeeds"


class Done:
    pid = 77

    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


@pytest.mark.parametrize("code, handed", [(9, True), (-9, True), (1, True), (6, True), (3, False), (0, False),
                                         (4, False), (5, False)])
def test_a_finished_child_with_a_bad_exit_code_is_handed_to_the_next_child(monkeypatch, code, handed):
    # R4-4 / R3-3: the next child writes it first, so its own success's clear comes after it; this process
    # queues nothing (a queued failure could land after that clear).
    monkeypatch.setattr(settings, "demo_mode", False)
    spawned, given = [Done(code), Child(done=False)], []
    monkeypatch.setattr(util_rebuild, "_spawn", lambda previous=None: given.append(previous) or spawned.pop(0))
    assert util_rebuild.request() is True            # the child that will finish
    assert util_rebuild.request() is True            # reads its exit code, starts the next with it
    assert given[0] is None
    if handed:
        [previous] = given[1]
        assert previous["id"] and previous["context"]["exit_code"] == code and previous["context"]["pid"] == 77
        assert "ran_s" in previous["context"] and str(code) in previous["error"] and previous["at"]
    else:
        assert given[1] is None
    assert not [e for e in site_health._queue if e["kind"] == "util_build_failed" and e["op"] == "record"]
    assert util_rebuild.request() is False           # the second child is still running: nothing more
    assert len(given) == 2


def test_a_failure_with_no_child_to_take_it_is_queued_here(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    spawned = [Done(9)]

    def spawn(previous=None):
        if not spawned:
            raise OSError("no fork")
        return spawned.pop(0)

    monkeypatch.setattr(util_rebuild, "_spawn", spawn)
    assert util_rebuild.request() is True
    assert util_rebuild.request() is False
    [entry] = [e for e in site_health._queue if e["kind"] == "util_build_failed"]
    assert entry["context"]["exit_code"] == 9


def test_a_finished_child_is_read_once(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    spawned, given = [Done(9), Done(9), Done(9)], []
    monkeypatch.setattr(util_rebuild, "_spawn", lambda previous=None: given.append(previous) or spawned.pop(0))
    util_rebuild.request()
    util_rebuild.request()      # reads child 1, hands it to child 2
    util_rebuild.request()      # reads child 2, hands both to child 3 (child 2 never said it stored child 1's)
    assert given[0] is None and len(given[1]) == 1 and len(given[2]) == 2
    assert given[2][0] == given[1][0], "the same failure, with the same id, so a second write is skipped"


class Exited(Done):
    def __init__(self, code, exited=None):
        super().__init__(code)
        if exited is not None:
            self.exited = exited


def test_handed_failures_are_kept_until_a_child_says_it_stored_them(monkeypatch):
    # R5-A3 / R6-A2: a child that couldn't store them (4), or was killed before saying so, hands them on again
    monkeypatch.setattr(settings, "demo_mode", False)
    spawned, given = [Done(9), Done(4), Done(-9), Done(0), Child()], []
    monkeypatch.setattr(util_rebuild, "_spawn", lambda previous=None: given.append(previous) or spawned.pop(0))
    for _ in range(5):
        util_rebuild.request()
    first = given[1][0]
    assert given[2] == [first], "exit 4: kept, and the child adds nothing of its own"
    assert given[3][0] == first and given[3][1]["context"]["exit_code"] == -9
    assert given[4] is None, "exit 0: stored, dropped"


def test_a_child_that_cannot_store_handed_failures_builds_nothing(health, monkeypatch):
    from app.services import replay_util

    built = []
    monkeypatch.setattr(replay_util, "ensure_values", lambda db: built.append(1))
    monkeypatch.setattr(site_health, "record_now", lambda *a, **k: False)
    previous = [{"id": "f1", "error": "e", "context": {}, "at": "2026-11-06T00:00:00+00:00"}]
    assert util_rebuild.main(["--previous", json.dumps(previous)]) == util_rebuild.EXIT_PREVIOUS_UNSTORED
    assert built == []


def test_a_failure_handed_twice_is_stored_once(health, monkeypatch):
    from app.services import replay_util

    monkeypatch.setattr(replay_util, "ensure_values", lambda db: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(site_health, "record_now",
                        lambda *a, **k: True if a[2].startswith("RuntimeError") else REAL_RECORD_NOW(*a, **k))
    previous = [{"id": "f1", "error": "the build exited with code -9", "context": {"exit_code": -9},
                 "at": "2026-11-06T00:00:00+00:00"}]
    for _ in range(2):      # the first child stored it and was killed before its exit code said so
        util_rebuild.main(["--previous", json.dumps(previous)])
    assert stored(health)[("util_build_failed", "build")].count == 1


REAL_RECORD_NOW = site_health.record_now


def test_a_clear_the_child_could_not_store_is_queued_by_the_parent(health, monkeypatch):
    # R6-B3
    from datetime import datetime, timezone

    from app.services import replay_util

    monkeypatch.setattr(replay_util, "ensure_values", lambda db: {"rows": []})
    monkeypatch.setattr(site_health, "resolve_now", lambda *a, **k: False)
    assert util_rebuild.main([]) == util_rebuild.EXIT_UNCLEARED
    when = datetime(2026, 11, 6, tzinfo=timezone.utc)
    spawned = [Exited(util_rebuild.EXIT_UNCLEARED, (0.0, when)), Child()]
    monkeypatch.setattr(util_rebuild, "_spawn", lambda previous=None: spawned.pop(0))
    util_rebuild.request()
    util_rebuild.request()
    [entry] = [e for e in site_health._queue if e["kind"] == "util_build_failed"]
    assert entry["op"] == "resolve" and entry["at"] == when


def test_a_failure_is_timed_when_its_child_exited(monkeypatch):
    # R5-B2: not when the parent next looked
    from datetime import datetime, timezone

    monkeypatch.setattr(settings, "demo_mode", False)
    when = datetime(2026, 11, 6, 1, 2, 3, tzinfo=timezone.utc)
    started = {}
    monkeypatch.setattr(util_rebuild.time, "monotonic", lambda: started.setdefault("t", 100.0))
    spawned, given = [Exited(9, (130.0, when)), Child()], []
    monkeypatch.setattr(util_rebuild, "_spawn", lambda previous=None: given.append(previous) or spawned.pop(0))
    util_rebuild.request()
    util_rebuild.request()
    [failure] = given[1]
    assert failure["at"] == when.isoformat() and failure["context"]["ran_s"] == 30


def test_the_watcher_notes_the_exit_time():
    import threading

    class Waits:
        def __init__(self):
            self.go = threading.Event()

        def wait(self):
            self.go.wait(5)

    child = Waits()
    util_rebuild._watch(child)
    assert not hasattr(child, "exited")
    child.go.set()
    for _ in range(100):
        if hasattr(child, "exited"):
            break
        threading.Event().wait(0.01)
    assert child.exited[1].tzinfo is not None

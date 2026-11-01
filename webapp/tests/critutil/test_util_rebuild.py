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
    from app.services import replay_util

    engine = create_engine("sqlite://")      # never the configured DATABASE_URL
    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(replay_util, "ensure_values", lambda db: {"rows": [1, 2, 3]})
    assert util_rebuild.main() == 0
    assert "stored 3 valued casts" in capsys.readouterr().out

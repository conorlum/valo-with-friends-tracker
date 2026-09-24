"""with_demo_db.py must never run a command against any database but the demo one."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import with_demo_db  # noqa: E402


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env.demo-remote"
    path.write_text("# demo\nDATABASE_URL=postgresql://u:p@host/valomaths_demo\n")
    monkeypatch.setattr(with_demo_db, "ENV_FILE", path)
    return path


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(with_demo_db.subprocess, "call", lambda cmd, **kw: calls.append((cmd, kw)) or 0)
    return calls


def test_refuses_a_database_that_is_not_the_demo_one(env_file, ran, monkeypatch):
    monkeypatch.setattr(with_demo_db, "connected_database", lambda url: "valorant_friends")
    assert with_demo_db.main(["scripts/recompute_impact.py"]) == 3
    assert ran == []


def test_runs_the_command_with_the_demo_url(env_file, ran, monkeypatch):
    monkeypatch.setattr(with_demo_db, "connected_database", lambda url: "valomaths_demo")
    assert with_demo_db.main(["--", "-m", "alembic", "upgrade", "head"]) == 0
    [(cmd, kw)] = ran
    assert cmd == [sys.executable, "-m", "alembic", "upgrade", "head"]
    assert kw["env"]["DATABASE_URL"] == "postgresql://u:p@host/valomaths_demo"
    assert kw["cwd"] == with_demo_db.WEBAPP_ROOT


def test_a_leading_module_flag_is_the_command_not_an_option(env_file, ran, monkeypatch):
    monkeypatch.setattr(with_demo_db, "connected_database", lambda url: "other_db")
    assert with_demo_db.main(["--expect-database", "other_db", "-m", "alembic", "current"]) == 0
    assert ran[0][0] == [sys.executable, "-m", "alembic", "current"]


def test_a_missing_env_file_stops_before_connecting(tmp_path, ran, monkeypatch):
    monkeypatch.setattr(with_demo_db, "ENV_FILE", tmp_path / ".env.demo-remote")
    monkeypatch.setattr(with_demo_db, "connected_database", lambda url: pytest.fail("connected"))
    with pytest.raises(SystemExit):
        with_demo_db.main(["scripts/recompute_impact.py"])
    assert ran == []


def test_an_env_file_without_a_url_stops(env_file, ran):
    env_file.write_text("OTHER=1\n")
    with pytest.raises(SystemExit):
        with_demo_db.main(["scripts/recompute_impact.py"])
    assert ran == []

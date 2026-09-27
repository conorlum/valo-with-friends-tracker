"""with_friends_db.py must never run a command against any database but the named friends one."""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import with_friends_db  # noqa: E402

FRIENDS = "valowithfriendsdb"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env.remote"
    path.write_text("# friends\nDATABASE_URL=postgresql://u:p@host/valowithfriendsdb?sslmode=require\n")
    monkeypatch.setattr(with_friends_db, "ENV_FILE", path)
    return path


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(with_friends_db.subprocess, "call", lambda cmd, **kw: calls.append((cmd, kw)) or 0)
    return calls


@pytest.fixture
def connected(monkeypatch):
    """A fake connector: records the URL it was given and reports a chosen database name."""
    seen = []

    def use(name):
        monkeypatch.setattr(with_friends_db, "connected_database", lambda url: seen.append(url) or name)
        return seen

    return use


def test_refuses_a_database_that_is_not_the_expected_one(env_file, ran, connected):
    connected("some_other_db")
    assert with_friends_db.main(["--expect-database", FRIENDS, "scripts/ingest_replay.py"]) == 3
    assert ran == []


def test_refuses_the_demo_database_even_when_it_is_expected(env_file, ran, connected):
    seen = connected("valomaths_demo")
    assert with_friends_db.main(["--expect-database", "valomaths_demo", "scripts/x.py"]) == 3
    assert ran == [] and seen == []


def test_refuses_when_the_url_turns_out_to_be_the_demo(env_file, ran, connected):
    connected("valomaths_demo")
    assert with_friends_db.main(["--expect-database", FRIENDS, "scripts/x.py"]) == 3
    assert ran == []


def test_expect_database_is_required(env_file, ran, connected):
    connected(FRIENDS)
    with pytest.raises(SystemExit):
        with_friends_db.main(["scripts/x.py"])
    assert ran == []


def test_runs_the_command_under_venv313_with_the_url_in_the_child_only(env_file, ran, connected, monkeypatch):
    connected(FRIENDS)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert with_friends_db.main(["--expect-database", FRIENDS, "scripts/ingest_replay.py", "--export-dir", "d"]) == 0
    [(cmd, kw)] = ran
    assert cmd == [str(with_friends_db.INGEST_PYTHON), "scripts/ingest_replay.py", "--export-dir", "d"]
    assert "venv313" in cmd[0]
    assert kw["env"]["DATABASE_URL"] == "postgresql://u:p@host/valowithfriendsdb?sslmode=require"
    assert kw["cwd"] == with_friends_db.WEBAPP_ROOT
    import os
    assert "DATABASE_URL" not in os.environ


def test_read_only_sets_the_session_option_for_the_check_and_the_command(env_file, ran, connected):
    seen = connected(FRIENDS)
    assert with_friends_db.main(["--expect-database", FRIENDS, "--read-only", "-m", "alembic", "current"]) == 0
    [(cmd, kw)] = ran
    assert cmd[1:] == ["-m", "alembic", "current"]
    for url in (seen[0], kw["env"]["DATABASE_URL"]):
        query = parse_qs(urlsplit(url).query)
        assert query["options"] == ["-c default_transaction_read_only=on"]
        assert query["sslmode"] == ["require"]


def test_read_only_keeps_an_existing_options_value():
    url = with_friends_db.with_read_only("postgresql://u:p@h/db?options=-c%20statement_timeout%3D5000")
    assert parse_qs(urlsplit(url).query)["options"] == [
        "-c statement_timeout=5000 -c default_transaction_read_only=on"]


def test_a_non_python_command_is_run_as_given(env_file, ran, connected):
    connected(FRIENDS)
    assert with_friends_db.main(["--expect-database", FRIENDS, "--", "psql", "-c", "select 1"]) == 0
    assert ran[0][0] == ["psql", "-c", "select 1"]


def test_a_missing_env_file_stops_before_connecting(tmp_path, ran, monkeypatch):
    monkeypatch.setattr(with_friends_db, "ENV_FILE", tmp_path / ".env.remote")
    monkeypatch.setattr(with_friends_db, "connected_database", lambda url: pytest.fail("connected"))
    with pytest.raises(SystemExit):
        with_friends_db.main(["--expect-database", FRIENDS, "scripts/x.py"])
    assert ran == []


def test_an_env_file_without_a_url_stops(env_file, ran):
    env_file.write_text("OTHER=1\n")
    with pytest.raises(SystemExit):
        with_friends_db.main(["--expect-database", FRIENDS, "scripts/x.py"])
    assert ran == []


def test_the_url_is_never_printed(env_file, ran, connected, capsys):
    connected("wrong_db")
    with_friends_db.main(["--expect-database", FRIENDS, "scripts/x.py"])
    connected(FRIENDS)
    with_friends_db.main(["--expect-database", FRIENDS, "scripts/x.py"])
    out = capsys.readouterr()
    assert "u:p@host" not in out.out + out.err


def test_read_only_encodes_spaces_for_libpq():
    url = with_friends_db.with_read_only("postgresql://u:p@h/db")
    assert "options=-c%20default_transaction_read_only%3Don" in url
    assert "+" not in url


def test_a_worktree_without_a_venv_uses_the_main_checkouts(tmp_path):
    main = tmp_path / "repo"
    (main / "webapp" / ".venv313" / "Scripts").mkdir(parents=True)
    (main / "webapp" / ".venv313" / "Scripts" / "python.exe").write_text("")
    worktree_webapp = main / ".worktrees" / "afk-x" / "webapp"
    worktree_webapp.mkdir(parents=True)
    assert with_friends_db.find_ingest_python(worktree_webapp) == main / "webapp" / ".venv313" / "Scripts" / "python.exe"
    (worktree_webapp / ".venv313" / "Scripts").mkdir(parents=True)
    (worktree_webapp / ".venv313" / "Scripts" / "python.exe").write_text("")
    assert with_friends_db.find_ingest_python(worktree_webapp) == worktree_webapp / ".venv313" / "Scripts" / "python.exe"

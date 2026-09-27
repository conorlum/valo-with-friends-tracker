"""Runs one command against the friends site's database, and nothing else.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\ingest_replay.py --export-dir <dir>
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\ingest_replay.py --export-dir <dir> --dry-run

`app.config` only reads `.env`, which points at a different database, so every
friends-DB command goes through here. It reads DATABASE_URL from
`webapp/.env.remote` (gitignored -- this repository is public, so never commit
it; one line, `DATABASE_URL=<the friends DB's EXTERNAL connection string>`),
connects, and refuses (exit 3) unless `current_database()` is the database named
by `--expect-database` and is not the ValoMaths demo database. Only then does it
run the command, with that DATABASE_URL in the child's environment only
(pydantic-settings prefers it over `.env`), from the `webapp/` directory.

`--read-only` adds `options=-c default_transaction_read_only=on` to the URL, so
the server refuses every write for the whole session, both for the check and for
the command.

A command that starts with a `.py` path or `-m` is run with `.venv313`'s
interpreter (the frozen ingest environment), not whichever Python launched this.
The connection string is never printed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, text

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = WEBAPP_ROOT / ".env.remote"
DEMO_DATABASE = "valomaths_demo"
VENV_PYTHON = Path(".venv313") / "Scripts" / "python.exe"


def find_ingest_python(webapp_root: Path = WEBAPP_ROOT) -> Path:
    """`.venv313`'s interpreter: this checkout's own, else (in a git worktree under
    `.worktrees/`, which has no venv) the first `webapp/.venv313` up the tree."""
    own = webapp_root / VENV_PYTHON
    if own.exists():
        return own
    for parent in webapp_root.parents:
        candidate = parent / "webapp" / VENV_PYTHON
        if candidate.exists():
            return candidate
    return own


INGEST_PYTHON = find_ingest_python()
READ_ONLY_OPTION = "-c default_transaction_read_only=on"


def read_database_url(env_file: Path) -> str:
    if not env_file.exists():
        raise SystemExit(f"{env_file.name} not found in {env_file.parent}. Create it with one line:\n"
                         "    DATABASE_URL=<the friends DB's EXTERNAL connection string>")
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "DATABASE_URL" and value.strip():
            return value.strip()
    raise SystemExit(f"{env_file.name} exists but has no DATABASE_URL=... line.")


def with_read_only(url: str) -> str:
    """Adds the read-only session option to a libpq URL, keeping any options already there."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    options = [value for key, value in query if key == "options"]
    rest = [(key, value) for key, value in query if key != "options"]
    merged = " ".join([*options, READ_ONLY_OPTION]) if READ_ONLY_OPTION not in options else options[0]
    # quote, not quote_plus: libpq does not decode "+" as a space.
    return urlunsplit(parts._replace(query=urlencode([*rest, ("options", merged)], quote_via=quote)))


def connected_database(url: str) -> str:
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT current_database()")).scalar()
    finally:
        engine.dispose()


def build_command(command: list[str], python: Path = INGEST_PYTHON) -> list[str]:
    if command[0] == "-m" or command[0].endswith(".py"):
        return [str(python), *command]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect-database", required=True,
                        help="the database the command may touch (the friends DB is valowithfriendsdb); "
                             "any other is refused")
    parser.add_argument("--read-only", action="store_true",
                        help="make the whole session read-only on the server (default_transaction_read_only)")
    parser.add_argument("command", nargs="*", help="the command to run")
    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything from the first argument that isn't this script's own is the
    # command, untouched -- argparse would otherwise claim a leading "-m".
    split = 0
    while split < len(argv) and argv[split] in ("-h", "--help", "--expect-database", "--read-only"):
        split += 2 if argv[split] == "--expect-database" else 1
    own, command = argv[:split], argv[split:]
    if command[:1] == ["--"]:
        command = command[1:]
    args = parser.parse_args(own)
    if not command:
        parser.error("no command given")
    if args.expect_database == DEMO_DATABASE:
        print(f"REFUSED: {DEMO_DATABASE} is the demo database; use with_demo_db.py", file=sys.stderr)
        return 3

    url = read_database_url(ENV_FILE)
    if args.read_only:
        url = with_read_only(url)
    database = connected_database(url)
    if database == DEMO_DATABASE or database != args.expect_database:
        print(f"REFUSED: {ENV_FILE.name} connects to {database}, not {args.expect_database}", file=sys.stderr)
        return 3

    mode = "read-only" if args.read_only else "read-write"
    print(f"[with_friends_db] database {database} ({mode}): {' '.join(command)}", flush=True)
    return subprocess.call(build_command(command), cwd=WEBAPP_ROOT, env={**os.environ, "DATABASE_URL": url})


if __name__ == "__main__":
    sys.exit(main())

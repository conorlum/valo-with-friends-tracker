"""Runs one command against the public ValoMaths demo database, and nothing else.

    python scripts/with_demo_db.py -m alembic upgrade head
    python scripts/with_demo_db.py scripts/recompute_impact.py
    python scripts/with_demo_db.py scripts/recompute_player_views.py

`app.config` only reads `.env`, which points at a different database, so every
demo-DB command goes through here. It reads DATABASE_URL from
`webapp/.env.demo-remote` (gitignored -- this repository is public, so never
commit it; one line, `DATABASE_URL=<the demo DB's EXTERNAL connection string>`),
connects, and refuses (exit 3) unless `current_database()` is the demo database.
Only then does it run the command, with that DATABASE_URL in its environment
(pydantic-settings prefers it over `.env`), from the `webapp/` directory.

A command that starts with a `.py` path or `-m` is run with this interpreter.
The connection string is never printed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = WEBAPP_ROOT / ".env.demo-remote"
DEMO_DATABASE = "valomaths_demo"


def read_database_url(env_file: Path) -> str:
    if not env_file.exists():
        raise SystemExit(f"{env_file.name} not found in {env_file.parent}. Create it with one line:\n"
                         "    DATABASE_URL=<the demo DB's EXTERNAL connection string>")
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "DATABASE_URL" and value.strip():
            return value.strip()
    raise SystemExit(f"{env_file.name} exists but has no DATABASE_URL=... line.")


def connected_database(url: str) -> str:
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT current_database()")).scalar()
    finally:
        engine.dispose()


def build_command(command: list[str]) -> list[str]:
    if command[0] == "-m" or command[0].endswith(".py"):
        return [sys.executable, *command]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect-database", default=DEMO_DATABASE,
                        help=f"the database the command may touch (default {DEMO_DATABASE}); any other is refused")
    parser.add_argument("command", nargs="*", help="the command to run")
    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything from the first argument that isn't this script's own is the
    # command, untouched -- argparse would otherwise claim a leading "-m".
    split = 0
    while split < len(argv) and argv[split] in ("-h", "--help", "--expect-database"):
        split += 2 if argv[split] == "--expect-database" else 1
    own, command = argv[:split], argv[split:]
    if command[:1] == ["--"]:
        command = command[1:]
    args = parser.parse_args(own)
    args.command = command
    if not args.command:
        parser.error("no command given")

    url = read_database_url(ENV_FILE)
    database = connected_database(url)
    if database != args.expect_database:
        print(f"REFUSED: {ENV_FILE.name} connects to {database}, not {args.expect_database}", file=sys.stderr)
        return 3

    command = build_command(args.command)
    print(f"[with_demo_db] database {database}: {' '.join(args.command)}", flush=True)
    return subprocess.call(command, cwd=WEBAPP_ROOT, env={**os.environ, "DATABASE_URL": url})


if __name__ == "__main__":
    sys.exit(main())

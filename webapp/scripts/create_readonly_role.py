"""Creates (or re-keys) the friends DB's read-only login and writes `webapp/.env.readonly`. Owner only, once per
database (re-run it to rotate the password).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\create_readonly_role.py

It must run through `with_friends_db.py` without `--read-only` (it needs the full-access login to create a role).
The role, `probe_ro`, can log in, read every table and sequence in `public` (now and later: default privileges
for the login that runs the migrations), and nothing else. On the role itself: `default_transaction_read_only`
on, a 60 s statement timeout, a 60 s idle-in-transaction timeout and at most 3 connections, so a heavy probe
can't hold the live site up for long.

Then it logs in as `probe_ro`, runs `with_friends_db.WRITE_CHECKS` and stops (exit 3) if the role could write
anything, naming how. Only after that does it write `.env.readonly` (gitignored by `.env.*`) in the working
directory, which `with_friends_db.py` sets to its own `webapp/`. The password is random and never printed.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import with_friends_db  # noqa: E402

ROLE = "probe_ro"
EXPECTED_DATABASE = "valowithfriendsdb"


def role_url(owner_url: str, password: str) -> str:
    """The owner's URL with the login swapped for the read-only role's (host, database and query kept)."""
    parts = urlsplit(owner_url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit(parts._replace(netloc=f"{ROLE}:{quote(password, safe='')}@{host}"))


def setup_sql(database: str) -> list[str]:
    q = '"' + database.replace('"', '""') + '"'
    return [
        # Only LOGIN and the limit: naming NOSUPERUSER/NOREPLICATION/NOBYPASSRLS needs a superuser (Render's login
        # isn't one), and a new role has none of them anyway; WRITE_CHECKS confirms it after logging in as the role.
        f"ALTER ROLE {ROLE} LOGIN CONNECTION LIMIT 3",
        f"ALTER ROLE {ROLE} SET default_transaction_read_only = on",
        f"ALTER ROLE {ROLE} SET statement_timeout = '60s'",
        f"ALTER ROLE {ROLE} SET idle_in_transaction_session_timeout = '60s'",
        f"GRANT CONNECT ON DATABASE {q} TO {ROLE}",
        f"GRANT USAGE ON SCHEMA public TO {ROLE}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}",
        f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO {ROLE}",
    ]


def main() -> int:
    owner_url = os.environ.get("DATABASE_URL", "")
    if not owner_url or not Path(".env.remote").exists():
        print("Run this through scripts/with_friends_db.py (without --read-only), from webapp/.", file=sys.stderr)
        return 2
    password = secrets.token_urlsafe(32)
    engine = create_engine(with_friends_db._sqlalchemy_url(owner_url))
    try:
        with engine.begin() as conn:
            database = conn.execute(text("SELECT current_database()")).scalar()
            if database != EXPECTED_DATABASE:
                print(f"REFUSED: connected to {database}, not {EXPECTED_DATABASE}", file=sys.stderr)
                return 3
            exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}).scalar()
            if not exists:
                conn.exec_driver_sql(f"CREATE ROLE {ROLE} LOGIN")
            conn.exec_driver_sql(f"ALTER ROLE {ROLE} PASSWORD %s", (password,))
            for sql in setup_sql(database):
                conn.exec_driver_sql(sql)
    finally:
        engine.dispose()
    print(f"role {ROLE} {'re-keyed' if exists else 'created'} on {database}")

    url = role_url(owner_url, password)
    problems = with_friends_db.write_privileges(url)
    if problems:
        print(f"STOPPED: {ROLE} still {', '.join(problems)}. .env.readonly was not written.", file=sys.stderr)
        if "can create in schema public" in problems:
            print("  (PUBLIC has CREATE on schema public: `REVOKE CREATE ON SCHEMA public FROM PUBLIC` fixes it.)",
                  file=sys.stderr)
        return 3
    Path(".env.readonly").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    print(f"checked: {ROLE} can't write. Wrote {Path('.env.readonly').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

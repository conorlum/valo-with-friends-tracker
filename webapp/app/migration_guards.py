"""Bounds for schema migrations, applied at connection time.

A migration that cannot take its lock must fail fast rather than queue: while
ALTER TABLE waits for ACCESS EXCLUSIVE, every later reader queues behind it and
the live site stalls. `statement_timeout` bounds the migration itself too,
because waiting for a lock is not the only way to run long.

These are passed as libpq connection options rather than executed as `SET`
statements, and that is not a style choice. Issuing any statement on the
connection before Alembic opens its own transaction makes SQLAlchemy 2.0
autobegin one, and Alembic's `begin_transaction()` then does not commit it: the
migrations run, the log looks perfect, and the whole thing is rolled back when
the connection closes. That failure was observed on 2026-09-16 against an empty
scratch database, which reported every "Running upgrade" line and ended with no
alembic_version table at all.
"""

from __future__ import annotations

#: A lock wait longer than this means someone is reading; fail and retry later.
MIGRATION_LOCK_TIMEOUT = "10s"
#: A migration that runs longer than this is doing something unplanned.
MIGRATION_STATEMENT_TIMEOUT = "15min"


def migration_connect_args(url: str) -> dict:
    """libpq options for a migration connection, or {} for non-Postgres URLs."""
    if not url.startswith("postgresql"):
        return {}
    return {"options": f"-c lock_timeout={MIGRATION_LOCK_TIMEOUT} "
                       f"-c statement_timeout={MIGRATION_STATEMENT_TIMEOUT}"}

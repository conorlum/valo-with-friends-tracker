"""Opt-in PostgreSQL for tests that need real server behaviour.

Triggers, DDL and lock semantics cannot be exercised on sqlite, so a few tests
need a real server. They ask for it HERE, and only here, with two rules:

1. The URL comes from VALO_TEST_DATABASE_URL and from nowhere else. A fixture
   that quietly falls back to `.env` is how a test suite ends up writing to a
   database someone cares about -- and this suite contains tests that call the
   scorer's committing wrapper.
2. The database must be named as disposable (`*_test` or `*_rehearsal`). A URL
   that points anywhere else FAILS the test rather than skipping it, because a
   silent skip is how the rule stops being noticed.
3. A test that CHANGES anything says so (`writes=True`, or `empties_tables=True`
   for the stronger case) and is then held to `*_test` alone. Read-only tests
   may run against a `*_rehearsal` restore, which is how the corpus-measuring
   tests get real data to measure; a writing one never may, because the damage
   there is invisible -- the scorer's wrapper updates rows in place, so a
   rescored match changes no row count and nothing looks wrong afterwards.

Set it up with the scratch database the rc3 plan's Stage 3.0 creates:

    VALO_TEST_DATABASE_URL="postgresql://.../valo_rc3_test" pytest tests/...
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, pool
from sqlalchemy.orm import sessionmaker

TEST_URL_ENV = "VALO_TEST_DATABASE_URL"
DISPOSABLE_SUFFIXES = ("_test", "_rehearsal")
#: Tests that change anything need a database holding nothing worth keeping.
#: A `_rehearsal` database is a restore of production that the release is
#: rehearsed on, and writing to it would quietly invalidate the rehearsal.
WRITABLE_SUFFIX = "_test"


def database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


def postgres_url_or_skip(*, empties_tables: bool = False, writes: bool = False) -> str:
    url = os.environ.get(TEST_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_URL_ENV} is not set: this test needs a disposable PostgreSQL database")
    name = database_name(url)
    if not name.endswith(DISPOSABLE_SUFFIXES):
        pytest.fail(f"refusing to test against database {name!r}: "
                    f"{TEST_URL_ENV} must name a database ending in {DISPOSABLE_SUFFIXES}")
    if (empties_tables or writes) and not name.endswith(WRITABLE_SUFFIX):
        did = ("deletes every row of the tables it uses" if empties_tables
               else "commits changes to the tables it uses")
        pytest.fail(f"refusing to write to database {name!r}: this test {did}, "
                    f"so it needs a database ending in {WRITABLE_SUFFIX!r}")
    if not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def postgres_session_or_skip(*, empties_tables: bool = False, writes: bool = False):
    """A session on its own connection, so a session-level setting one test
    makes (the write gate's identity) cannot leak into the next one."""
    url = postgres_url_or_skip(empties_tables=empties_tables, writes=writes)
    engine = create_engine(url, poolclass=pool.NullPool)
    return sessionmaker(bind=engine)()

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


def database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


def postgres_url_or_skip() -> str:
    url = os.environ.get(TEST_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_URL_ENV} is not set: this test needs a disposable PostgreSQL database")
    name = database_name(url)
    if not name.endswith(DISPOSABLE_SUFFIXES):
        pytest.fail(f"refusing to test against database {name!r}: "
                    f"{TEST_URL_ENV} must name a database ending in {DISPOSABLE_SUFFIXES}")
    if not url.startswith("postgresql+"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def postgres_session_or_skip():
    """A session on its own connection, so a session-level setting one test
    makes (the write gate's identity) cannot leak into the next one."""
    engine = create_engine(postgres_url_or_skip(), poolclass=pool.NullPool)
    return sessionmaker(bind=engine)()

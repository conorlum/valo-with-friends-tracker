"""Postgres migration lifecycle; requires a disposable scratch database."""
import pytest
from sqlalchemy import create_engine, inspect

from tests.support.postgres import command

pytestmark = pytest.mark.postgres


def test_postplant_migration_upgrades_downgrades_and_reupgrades(scratch_database):
    command(scratch_database, "-m", "alembic", "upgrade", "0037")
    engine = create_engine(scratch_database)
    tables = {"replay_round_postplant", "postplant_stats_cache"}
    try:
        with engine.connect() as conn:
            assert not tables.intersection(inspect(conn).get_table_names())
        command(scratch_database, "-m", "alembic", "upgrade", "0038")
        with engine.connect() as conn:
            assert tables <= set(inspect(conn).get_table_names())
        command(scratch_database, "-m", "alembic", "downgrade", "0037")
        with engine.connect() as conn:
            assert not tables.intersection(inspect(conn).get_table_names())
        command(scratch_database, "-m", "alembic", "upgrade", "0038")
        with engine.connect() as conn:
            assert tables <= set(inspect(conn).get_table_names())
    finally:
        engine.dispose()

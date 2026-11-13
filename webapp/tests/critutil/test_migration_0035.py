import pytest
from sqlalchemy import create_engine, inspect, text

from tests.support.postgres import command

pytestmark = pytest.mark.postgres


def test_util_model_blob_migration_upgrades_downgrades_and_reupgrades(scratch_database):
    command(scratch_database, "-m", "alembic", "upgrade", "0034")
    engine = create_engine(scratch_database)

    def columns(conn):
        return {column["name"]: column for column in inspect(conn).get_columns("util_model")}

    try:
        with engine.begin() as conn:
            assert "blob" not in columns(conn) and not columns(conn)["data"]["nullable"]
            conn.execute(text(
                "INSERT INTO util_model (name, version, key, data, computed_at) VALUES "
                "('values', 1, 'k', CAST('{\"rows\": []}' AS JSONB), now()), "
                "('win', 1, 'w', CAST('{\"a\": 1}' AS JSONB), now())"))
            before = conn.execute(text("SELECT name, version, key, data FROM util_model ORDER BY name")).all()

        command(scratch_database, "-m", "alembic", "upgrade", "0035")
        with engine.begin() as conn:
            cols = columns(conn)
            assert "blob" in cols and cols["data"]["nullable"]
            assert conn.execute(text("SELECT name, version, key, data FROM util_model ORDER BY name")).all() == before
            assert conn.execute(text("SELECT blob FROM util_model")).scalars().all() == [None, None]
            conn.execute(text("UPDATE util_model SET data = NULL, blob = '\\x00'::bytea WHERE name = 'values'"))

        command(scratch_database, "-m", "alembic", "downgrade", "0034")
        with engine.connect() as conn:
            cols = columns(conn)
            assert "blob" not in cols and not cols["data"]["nullable"]
            rows = conn.execute(text("SELECT name, version, key, data FROM util_model ORDER BY name")).all()
            assert rows == [row for row in before if row[0] == "win"]

        command(scratch_database, "-m", "alembic", "upgrade", "0035")
        with engine.connect() as conn:
            cols = columns(conn)
            assert "blob" in cols and cols["data"]["nullable"]
    finally:
        engine.dispose()

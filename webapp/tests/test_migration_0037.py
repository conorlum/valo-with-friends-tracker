import pytest
from sqlalchemy import create_engine, inspect, text

from tests.support.postgres import command

pytestmark = pytest.mark.postgres


def _unique_on_hash(conn):
    inspector = inspect(conn)
    indexes = [i for i in inspector.get_indexes("upload_tokens") if i["column_names"] == ["token_hash"] and i["unique"]]
    constraints = [c for c in inspector.get_unique_constraints("upload_tokens") if c["column_names"] == ["token_hash"]]
    return bool(indexes or constraints)


def test_upload_tokens_migration_upgrades_downgrades_and_reupgrades(scratch_database):
    command(scratch_database, "-m", "alembic", "upgrade", "0036")
    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            assert "upload_tokens" not in inspect(conn).get_table_names()

        command(scratch_database, "-m", "alembic", "upgrade", "0037")
        with engine.begin() as conn:
            assert "upload_tokens" in inspect(conn).get_table_names()
            assert _unique_on_hash(conn)
            conn.execute(text("INSERT INTO upload_tokens (token_hash, label) VALUES (:h, 'laptop')"), {"h": "a" * 64})
            row = conn.execute(text("SELECT created_at, last_used_at, revoked_at, player_id FROM upload_tokens")).one()
            assert row[0] is not None and row[1:] == (None, None, None)

        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO upload_tokens (token_hash, label) VALUES (:h, 'dup')"), {"h": "a" * 64})

        command(scratch_database, "-m", "alembic", "downgrade", "0036")
        with engine.connect() as conn:
            assert "upload_tokens" not in inspect(conn).get_table_names()

        command(scratch_database, "-m", "alembic", "upgrade", "0037")
        with engine.connect() as conn:
            assert "upload_tokens" in inspect(conn).get_table_names()
            assert _unique_on_hash(conn)
    finally:
        engine.dispose()

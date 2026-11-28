"""Migration 0039 (replay_moment_index): the revision chain and an up/down round trip on SQLite."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.search.match import ROW_KEYS

WEBAPP = Path(__file__).resolve().parents[2]


def load_migration():
    path = WEBAPP / "alembic" / "versions" / "0039_replay_moment_index.py"
    spec = importlib.util.spec_from_file_location("migration_0039", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_chain():
    migration = load_migration()
    assert (migration.revision, migration.down_revision) == ("0039", "0038")


def test_up_and_down_on_sqlite():
    migration = load_migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            inspector = inspect(connection)
            assert {"replay_moment_index", "replay_moment_index_state"} <= set(inspector.get_table_names())
            columns = [c["name"] for c in inspector.get_columns("replay_moment_index")]
            assert sorted(columns) == sorted(ROW_KEYS)
            assert inspector.get_pk_constraint("replay_moment_index")["constrained_columns"] == [
                "replay_id", "round", "seq"]
            assert {tuple(i["column_names"]) for i in inspector.get_indexes("replay_moment_index")} == {
                ("kind", "map"), ("player_id", "kind"), ("agent", "kind"), ("replay_id",)}
            assert [c["name"] for c in inspector.get_columns("replay_moment_index_state")] == [
                "replay_id", "key", "revision", "row_count", "error", "built_at"]
            migration.downgrade()
            assert not {"replay_moment_index", "replay_moment_index_state"} & set(
                inspect(connection).get_table_names())

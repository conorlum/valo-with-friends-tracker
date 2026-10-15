"""Migration 0023 (map pool history): revision chain, the frozen seed, and an up/down/up round trip on a real server."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parent
SEED_JSON = WEBAPP / "app" / "data" / "map_pool_seed.json"


def load_migration():
    path = WEBAPP / "alembic" / "versions" / "0023_map_pool.py"
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def deduplicated_json_counts():
    data = json.loads(SEED_JSON.read_text(encoding="utf-8"))
    changes = {(c["map"], c["effective_at"], c["in_pool"]) for c in data["changes"]}
    return len(changes), len({p["url"] for p in data["patch_notes"]})


def test_revision_chain():
    migration = load_migration()
    assert (migration.revision, migration.down_revision) == ("0023", "0022")


def test_frozen_seed_matches_the_json():
    migration = load_migration()
    changes, pages = deduplicated_json_counts()
    assert len({(c["map"], c["effective_at"], c["in_pool"]) for c in migration.SEED_AT_0023["changes"]}) == changes
    assert len({p["url"] for p in migration.SEED_AT_0023["patch_notes"]}) == pages
    for page in migration.SEED_AT_0023["patch_notes"]:
        assert page["change_count"] == sum(1 for c in migration.SEED_AT_0023["changes"] if c["patch"] == page["patch"])


def test_pg_migration_0023_up_down_up():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    from _postgres import postgres_url_or_skip

    migration = load_migration()
    changes, pages = deduplicated_json_counts()
    engine = create_engine(postgres_url_or_skip(writes=True))

    def counts(connection):
        return (connection.execute(text("SELECT count(*) FROM map_pool_changes")).scalar_one(),
                connection.execute(text("SELECT count(*) FROM map_pool_patch_notes")).scalar_one())

    def exists(connection):
        return connection.execute(text("SELECT to_regclass('map_pool_changes') IS NOT NULL")).scalar_one()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                if exists(connection):      # a database already at 0023
                    migration.downgrade()
                assert not exists(connection)
                migration.upgrade()
                assert counts(connection) == (changes, pages)
                assert connection.execute(text(
                    "SELECT count(*) FROM map_pool_patch_notes WHERE read_at IS NULL OR read_at <> published_at "
                    "OR change_count IS NULL")).scalar_one() == 0
                assert connection.execute(text(
                    "SELECT count(*) FROM map_pool_changes WHERE source <> 'seed'")).scalar_one() == 0
                migration.downgrade()
                assert not exists(connection)
                migration.upgrade()
                assert counts(connection) == (changes, pages)
        finally:
            transaction.rollback()
    engine.dispose()

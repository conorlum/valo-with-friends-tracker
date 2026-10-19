"""Migration 0028 (filtered view-cache scopes): revision chain, an up/down/up round trip on a real server,
the model-level CHECKs on sqlite, and the cache_scope_key helper."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

WEBAPP = Path(__file__).resolve().parent.parent


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):
    return "JSON"


def load_migration():
    path = WEBAPP / "alembic" / "versions" / "0028_view_filter_scope.py"
    spec = importlib.util.spec_from_file_location("migration_0028", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_chain():
    migration = load_migration()
    assert (migration.revision, migration.down_revision) == ("0028", "0027")


def test_pg_migration_0028_up_down_up():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    from _postgres import postgres_url_or_skip

    migration = load_migration()
    engine = create_engine(postgres_url_or_skip(writes=True))

    def widened(connection):
        return connection.execute(text(
            "SELECT pg_get_constraintdef(oid) LIKE '%LIKE%' FROM pg_constraint "
            "WHERE conname = 'ck_player_view_cache_scope'")).scalar_one()

    def put(connection, table, key_column, key, scope):
        extra_columns, extra_values = ("fingerprint, ", "'fp', ") if table == "account_group_view_cache" else ("", "")
        connection.execute(text(
            f"INSERT INTO {table} ({key_column}, scope, {extra_columns}data, version) "
            f"VALUES (:k, :s, {extra_values}'{{}}', 1)"), {"k": key, "s": scope})

    def refused(connection, table, key_column, key, scope):
        nested = connection.begin_nested()
        try:
            put(connection, table, key_column, key, scope)
        except (IntegrityError, DataError):
            nested.rollback()
            return True
        nested.commit()
        return False

    def count(connection, table):
        return connection.execute(text(f"SELECT count(*) FROM {table} WHERE scope LIKE '%|%'")).scalar_one()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                if widened(connection):     # a database already at 0028
                    migration.downgrade()
                assert not widened(connection)
                player = connection.execute(text(
                    "INSERT INTO players (display_name, created_at) VALUES ('w15', now()) RETURNING id")).scalar_one()
                group = connection.execute(text(
                    "INSERT INTO account_groups (main_player_id, created_at) VALUES (:p, now()) RETURNING id"),
                    {"p": player}).scalar_one()
                pvc, agvc = ("player_view_cache", "player_id", player), ("account_group_view_cache", "group_id", group)

                migration.upgrade()
                for table, column, key in (pvc, agvc):
                    put(connection, table, column, key, "career|role:duelist")
                    assert refused(connection, table, column, key, "bogus")
                    assert refused(connection, table, column, key, "career|" + "x" * 60)
                    assert count(connection, table) == 1

                # an unfiltered row inserted before the downgrade survives it
                put(connection, "player_view_cache", "player_id", player, "recent")
                put(connection, "account_group_view_cache", "group_id", group, "recent")
                migration.downgrade()
                for table, column, key in (pvc, agvc):
                    assert count(connection, table) == 0
                    assert connection.execute(text(
                        f"SELECT count(*) FROM {table} WHERE scope = 'recent'")).scalar_one() == 1
                    assert refused(connection, table, column, key, "career|x")

                migration.upgrade()
                for table, column, key in (pvc, agvc):
                    put(connection, table, column, key, "recent|agent:KAY/O")
                    assert count(connection, table) == 1
        finally:
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def db(tmp_path):
    from app.db import Base
    from app.models import AccountGroup, AccountGroupViewCache, Player, PlayerViewCache

    eng = create_engine("sqlite:///" + str(tmp_path / "scopes.sqlite"))
    Base.metadata.create_all(eng, tables=[t.__table__ for t in (Player, PlayerViewCache, AccountGroup, AccountGroupViewCache)])
    with sessionmaker(bind=eng)() as session:
        yield session
    eng.dispose()


def test_models_round_trip_filtered_scope_and_refuse_bogus(db):
    from app.models import AccountGroup, AccountGroupViewCache, Player, PlayerViewCache

    player = Player(display_name="w15")
    db.add(player)
    db.flush()
    group = AccountGroup(main_player_id=player.id)
    db.add(group)
    db.flush()
    db.add(PlayerViewCache(player_id=player.id, scope="career|role:duelist", data={}, version=1))
    db.add(AccountGroupViewCache(group_id=group.id, scope="recent|agent:KAY/O", fingerprint="f", data={}, version=1))
    db.commit()
    assert db.query(PlayerViewCache).one().scope == "career|role:duelist"
    assert db.query(AccountGroupViewCache).one().scope == "recent|agent:KAY/O"

    db.add(PlayerViewCache(player_id=player.id, scope="bogus", data={}, version=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(AccountGroupViewCache(group_id=group.id, scope="bogus", fingerprint="f", data={}, version=1))
    with pytest.raises(IntegrityError):
        db.commit()


def test_cache_scope_key():
    from app.services.player_view_cache import cache_scope_key

    assert cache_scope_key("career") == "career"
    assert cache_scope_key("career", "role:duelist") == "career|role:duelist"
    assert cache_scope_key("recent", "agent:KAY/O") == "recent|agent:KAY/O"
    with pytest.raises(ValueError):
        cache_scope_key("bogus", "role:duelist")
    with pytest.raises(ValueError):
        cache_scope_key("career", "x" * 48)
    assert len(cache_scope_key("career", "x" * 41)) == 48

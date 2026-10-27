"""T03/T09: run the deployment commands in fresh processes, then reconnect."""
import json
import os
import time
import subprocess
import sys
import pytest
from sqlalchemy import create_engine, inspect, text
from alembic.config import Config
from alembic.script import ScriptDirectory
from tests.support.postgres import command, WEBAPP, scratch_database, migrated_database  # noqa: F401


def test_empty_database_upgrade_persists_at_head_and_is_idempotent(scratch_database):
    command(scratch_database, '-m', 'alembic', 'upgrade', 'head')
    engine = create_engine(scratch_database)
    try:
        with engine.begin() as conn:
            expected_heads = set(ScriptDirectory.from_config(Config('alembic.ini')).get_heads())
            assert set(conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) == expected_heads
            tables = set(inspect(conn).get_table_names())
            assert {'players', 'impact_scores', 'replays', 'coach_feedback', 'account_group_view_cache',
                    'map_pool_changes', 'replay_round_moments', 'replay_round_util'} <= tables
            assert 'riot_subject' in {c['name'] for c in inspect(conn).get_columns('players')}
            assert 'scoring_version' in {c['name'] for c in inspect(conn).get_columns('impact_scores')}
            conn.execute(text("INSERT INTO players (display_name, created_at) VALUES ('migration-sentinel', now())"))
        command(scratch_database, '-m', 'alembic', 'upgrade', 'head')
        result = command(scratch_database, '-c',
            "from app.db import engine; from sqlalchemy import text; "
            "c=engine.connect(); print(c.execute(text('SELECT display_name FROM players')).scalar_one())")
        assert result.stdout.strip() == 'migration-sentinel'
    finally: engine.dispose()


def test_upgrade_downgrade_upgrade_preserves_unfiltered_rows(scratch_database):
    command(scratch_database, '-m', 'alembic', 'upgrade', '0027')
    engine = create_engine(scratch_database)
    try:
        with engine.begin() as conn:
            pid = conn.execute(text("INSERT INTO players (display_name, created_at) VALUES ('migration-player', now()) RETURNING id")).scalar_one()
            group = conn.execute(text('INSERT INTO account_groups (main_player_id, created_at) VALUES (:p, now()) RETURNING id'), {'p': pid}).scalar_one()
            for table, column, key in [('player_view_cache', 'player_id', pid), ('account_group_view_cache', 'group_id', group)]:
                extra = ", fingerprint" if column == 'group_id' else ''
                val = ", 'fp'" if extra else ''
                conn.execute(text(f"INSERT INTO {table} ({column},scope,data,version{extra}) VALUES (:k,'recent','{{}}',1{val})"), {'k': key})
        command(scratch_database, '-m', 'alembic', 'upgrade', 'head')
        with engine.begin() as conn:
            for table, column, key in [('player_view_cache', 'player_id', pid), ('account_group_view_cache', 'group_id', group)]:
                extra = ", fingerprint" if column == 'group_id' else ''
                val = ", 'fp'" if extra else ''
                conn.execute(text(f"INSERT INTO {table} ({column},scope,data,version{extra}) VALUES (:k,'career|agent:Jett','{{}}',1{val})"), {'k': key})
        command(scratch_database, '-m', 'alembic', 'downgrade', '0027')
        command(scratch_database, '-m', 'alembic', 'upgrade', 'head')
        with engine.connect() as conn:
            for table in ('player_view_cache', 'account_group_view_cache'):
                assert conn.execute(text(f'SELECT scope FROM {table}')).scalars().all() == ['recent']
            assert conn.execute(text('SELECT display_name FROM players')).scalar_one() == 'migration-player'
    finally: engine.dispose()


def test_real_migration_lock_timeout_leaves_revision_and_data_unchanged(scratch_database):
    command(scratch_database, '-m', 'alembic', 'upgrade', '0027')
    engine = create_engine(scratch_database)
    try:
        with engine.begin() as held:
            held.exec_driver_sql('LOCK TABLE player_view_cache IN ACCESS SHARE MODE')
            script = ("import sys; from app import migration_guards as g; g.MIGRATION_LOCK_TIMEOUT='250ms'; "
                      "from alembic.config import Config; from alembic import command; command.upgrade(Config('alembic.ini'),'head')")
            before = time.monotonic()
            result = subprocess.run([sys.executable, '-c', script], cwd=WEBAPP,
                env={**os.environ, 'DATABASE_URL': scratch_database}, capture_output=True, text=True, timeout=15)
            assert result.returncode != 0 and 'lock timeout' in result.stderr
            assert time.monotonic() - before < 15
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0027'
        command(scratch_database, '-m', 'alembic', 'upgrade', 'head')
    finally: engine.dispose()


def test_connection_options_bound_statements_and_session_recovers_after_timeout(scratch_database, monkeypatch):
    from app import migration_guards as guards
    from sqlalchemy.exc import DBAPIError
    monkeypatch.setattr(guards,'MIGRATION_STATEMENT_TIMEOUT','50ms')
    engine = create_engine(scratch_database, connect_args=guards.migration_connect_args(scratch_database))
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql('SHOW lock_timeout').scalar_one() == '10s'
            assert conn.exec_driver_sql('SHOW statement_timeout').scalar_one() == '50ms'
            with pytest.raises(DBAPIError, match='statement timeout'): conn.exec_driver_sql('SELECT pg_sleep(1)')
            conn.rollback()
            assert conn.exec_driver_sql('SELECT 1').scalar_one() == 1
    finally: engine.dispose()


def test_non_postgres_migration_connection_has_no_libpq_options():
    from app.migration_guards import migration_connect_args
    assert migration_connect_args('sqlite://') == {}


def test_committed_sample_seed_loads_twice_at_head_without_replay_features(migrated_database, monkeypatch):
    # This is a newly-created scratch DB, not the deployed demo database.
    command(migrated_database, 'scripts/load_seed_data.py')
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            before = {name: conn.execute(text(f'SELECT count(*) FROM {name}')).scalar_one()
                      for name in ('players', 'matches', 'rounds', 'impact_scores', 'friendships')}
        assert before['matches'] == 6 and before['impact_scores'] > 0 and before['friendships'] > 0
        command(migrated_database, 'scripts/load_seed_data.py')
        with engine.connect() as conn:
            assert {name: conn.execute(text(f'SELECT count(*) FROM {name}')).scalar_one() for name in before} == before
            for table in ('replays', 'control_heights', 'coach_reports'):
                assert conn.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 0
            name = conn.execute(text('SELECT display_name FROM players ORDER BY id LIMIT 1')).scalar_one()
            external = conn.execute(text('SELECT external_id FROM matches ORDER BY id LIMIT 1')).scalar_one()
        from sqlalchemy.orm import sessionmaker
        from urllib.parse import quote
        from app.config import settings
        from app.routers import players
        from tests.support.site import bind_site
        monkeypatch.setattr(players, 'PAGE_BOOTSTRAP_DRAWS', 10)
        with bind_site(sessionmaker(bind=engine), monkeypatch) as (client, _):
            monkeypatch.setattr(settings,'demo_mode',True)
            assert client.get('/stats').headers['location'] == '/stats/all'
            for path in ('/players/'+quote(name,safe=''), '/matches/'+quote(external,safe=''), '/stats/all'):
                assert client.get(path).status_code == 200, path
    finally: engine.dispose()

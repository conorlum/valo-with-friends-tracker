"""Fresh, Alembic-created databases; never reset an existing database's schema."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
import pytest

WEBAPP = Path(__file__).resolve().parents[2]


def command(url, *args, **extra_env):
    env = {**os.environ, 'DATABASE_URL': url, 'DEMO_MODE': 'false', 'MAP_POOL_AUTO': 'false',
           'REPLAY_CONTROL_REMOTE': 'false', 'REPLAY_REPARSE_AUTO': 'false', **extra_env}
    result = subprocess.run([sys.executable, *args], cwd=WEBAPP, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture
def scratch_database():
    from sqlalchemy import create_engine
    from psycopg2 import sql
    from tests._postgres import postgres_url_or_skip
    parent = create_engine(postgres_url_or_skip(writes=True), isolation_level='AUTOCOMMIT')
    name = 'coverage_' + uuid.uuid4().hex + '_test'
    with parent.connect() as conn:
        assert conn.exec_driver_sql('SELECT current_database()').scalar_one() == parent.url.database
        raw = conn.connection.driver_connection
        with raw.cursor() as cursor: cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    child = parent.url.set(database=name)
    try: yield child.render_as_string(hide_password=False)
    finally:
        with parent.connect() as conn:
            with conn.connection.driver_connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        parent.dispose()


@pytest.fixture
def migrated_database(scratch_database):
    command(scratch_database, '-m', 'alembic', 'upgrade', 'head')
    return scratch_database


@pytest.fixture
def pg_factory(migrated_database):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(migrated_database)
    try: yield sessionmaker(bind=engine)
    finally: engine.dispose()

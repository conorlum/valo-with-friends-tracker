"""A complete app with a scratch database and fresh sessions for every request/thread."""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from app.db import Base, get_db
from app.config import settings
from tests.support.http import Client


@compiles(JSONB, "sqlite")
def _jsonb(type_, compiler, **kw): return "JSON"


@pytest.fixture
def site(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path/"site.sqlite"), connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE players ADD COLUMN riot_subject VARCHAR(36)")
    factory = sessionmaker(bind=engine)
    try:
        with bind_site(factory, monkeypatch) as bound: yield bound
    finally: engine.dispose()


@contextmanager
def bind_site(factory, monkeypatch):
    from app import db as database, main, templates as template_module
    from app.routers import players
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(players, "SessionLocal", factory)
    monkeypatch.setattr(template_module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "map_pool_auto", False)
    monkeypatch.setattr(settings, "replay_control_remote", False)
    monkeypatch.setattr(settings, "replay_worker_url", None)
    monkeypatch.delenv("MAINTENANCE_MODE", raising=False)
    monkeypatch.delenv("PLAYER_PAGE_TRACE_LOG", raising=False)
    def session_dependency():
        with factory() as session: yield session
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_db] = session_dependency
    try: yield Client(main.app), factory
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.fixture
def pg_site(pg_factory, monkeypatch):
    with bind_site(pg_factory, monkeypatch) as bound: yield bound

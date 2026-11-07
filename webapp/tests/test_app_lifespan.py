"""T12: every service started by the app gets its shutdown signal, on failures too."""
import asyncio
import threading
import pytest
from app import main
from app.config import settings
from app.db import SessionLocal
from app.services import map_pool_refresh, replay_archive_sync, replay_control_remote, replay_match_states, site_health

SERVICES = (replay_control_remote, replay_archive_sync, map_pool_refresh, replay_match_states)


@pytest.mark.parametrize("failure", [None, "request", 0, 1, 2, 3])
def test_lifespan_cleans_up_normal_exit_runtime_error_and_partial_startup(monkeypatch, failure):
    calls, events = [], []
    for i, service in enumerate(SERVICES):
        def start(factory, i=i):
            assert factory is SessionLocal
            calls.append(i)
            if failure == i: raise RuntimeError("startup failed")
            stop = None if i == 1 else threading.Event()
            events.append(stop)
            return stop
        monkeypatch.setattr(service, "start", start)
    monkeypatch.setattr(settings, "demo_mode", False)
    health = {}
    health_stop = threading.Event()
    def health_start(factory, started_at=None):
        assert factory is SessionLocal and started_at is not None
        health["started"] = True
        return health_stop
    def apply_forbidden(**kw): raise AssertionError("startup waited on the database to apply the resolved file")
    monkeypatch.setattr(site_health, "apply_resolved", apply_forbidden)
    monkeypatch.setattr(site_health, "start", health_start)

    def health_stop_call(event):
        # the loops are told to stop first, so what they record as they stop is in the final flush
        health["loops_stopped_first"] = all(e.is_set() for e in events if e)
        if event is not None:
            event.set()
    monkeypatch.setattr(site_health, "stop", health_stop_call)
    async def run():
        async with main.lifespan(main.app):
            assert calls == [0, 1, 2, 3]
            assert not any(e.is_set() for e in events if e)
            if failure == "request": raise RuntimeError("request failed")
    if failure is None: asyncio.run(run())
    else:
        with pytest.raises(RuntimeError, match="failed"): asyncio.run(run())
    assert all(e.is_set() for e in events if e)
    assert calls == list(range(failure + 1)) if isinstance(failure, int) else calls == [0, 1, 2, 3]
    if isinstance(failure, int):
        assert "started" not in health  # the four services did not all start, so health did not start either
    else:
        assert health["started"] and health_stop.is_set() and health["loops_stopped_first"]


def test_shutdown_waits_for_the_final_flush(monkeypatch):
    # B3/B12: what was queued just before a deploy's shutdown reaches the table.
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.db
    from app.db import Base
    from app.models.site_events import SiteEvent, SiteEventResolution

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[SiteEvent.__table__, SiteEventResolution.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(app.db, "SessionLocal", factory)
    monkeypatch.setattr(site_health, "FLUSH_S", 3600)
    for service in SERVICES:
        monkeypatch.setattr(service, "start", lambda factory: None)

    async def run():
        async with main.lifespan(main.app):
            site_health.enqueue("page_error", "GET /x", "boom")
    asyncio.run(run())
    with factory() as s:
        assert [(r.kind, r.key) for r in s.scalars(select(SiteEvent))] == [("page_error", "GET /x")]
    assert not any(t.name == "site-health-flush" for t in threading.enumerate())


def test_what_a_loop_records_as_it_stops_reaches_the_final_flush(monkeypatch):
    # R4-11 / R3-6: shutdown waits for the loops' threads before the final flush.
    import time

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.db
    from app.db import Base
    from app.models.site_events import SiteEvent, SiteEventResolution

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[SiteEvent.__table__, SiteEventResolution.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(app.db, "SessionLocal", factory)
    monkeypatch.setattr(site_health, "FLUSH_S", 3600)

    def loop_start(session_factory):
        stop, registered = threading.Event(), threading.Event()

        def run():
            site_health.register("test-loop", 60)
            registered.set()
            try:
                stop.wait()
                time.sleep(0.3)              # the pass in progress ends a little after the stop signal
                site_health.loop_failed("test-loop", RuntimeError("failed as it stopped"))
            finally:
                site_health.unregister("test-loop")
        threading.Thread(target=run, daemon=True).start()
        assert registered.wait(5)
        return stop

    for service in SERVICES:
        monkeypatch.setattr(service, "start", lambda factory: None)
    monkeypatch.setattr(SERVICES[0], "start", loop_start)

    async def run():
        async with main.lifespan(main.app):
            pass
    asyncio.run(run())
    with factory() as s:
        assert [(r.kind, r.key) for r in s.scalars(select(SiteEvent))] == [("loop_failed", "test-loop")]


def test_lifespan_yields_when_the_database_is_dead(monkeypatch):
    import app.db
    monkeypatch.setattr(settings, "demo_mode", False)
    def dead():
        raise RuntimeError("db down")
    monkeypatch.setattr(app.db, "SessionLocal", dead)
    for service in SERVICES:
        monkeypatch.setattr(service, "start", lambda factory: None)
    monkeypatch.setattr(site_health, "start", lambda factory, started_at=None: None)
    reached = []
    async def run():
        async with main.lifespan(main.app):
            reached.append(True)
    asyncio.run(run())
    assert reached


def test_demo_starts_no_threads_even_when_all_switches_are_on(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    for name in ('map_pool_auto', 'replay_control_remote', 'replay_reparse_auto', 'replay_heights_auto'):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, 'replay_worker_url', 'http://worker.invalid')
    monkeypatch.setattr(settings, 'replay_upload_code', 'test-code')
    def forbidden(*args, **kw): raise AssertionError("demo started a background thread")
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    def apply_forbidden(**kw): raise AssertionError("demo called apply_resolved")
    monkeypatch.setattr(site_health, "apply_resolved", apply_forbidden)
    async def run():
        async with main.lifespan(main.app): pass
    asyncio.run(run())
    assert not any(t.name == "site-health-flush" for t in threading.enumerate())

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
    monkeypatch.setattr(site_health, "apply_resolved", lambda **kw: health.setdefault("applied", kw))
    monkeypatch.setattr(site_health, "start", health_start)
    monkeypatch.setattr(site_health, "stop", lambda event: event.set())
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
        assert not health  # the four services did not all start, so health did not start either
    else:
        assert health["applied"]["session_factory"] is SessionLocal and health["started"]
        assert health_stop.is_set()


def test_lifespan_yields_when_apply_resolved_meets_a_dead_database(monkeypatch):
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

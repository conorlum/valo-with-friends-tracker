"""T12: every service started by the app gets its shutdown signal, on failures too."""
import asyncio
import threading
import pytest
from app import main
from app.config import settings
from app.db import SessionLocal
from app.services import map_pool_refresh, replay_archive_sync, replay_control_remote, replay_match_states

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


def test_demo_starts_no_threads_even_when_all_switches_are_on(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    for name in ('map_pool_auto', 'replay_control_remote', 'replay_reparse_auto', 'replay_heights_auto'):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, 'replay_worker_url', 'http://worker.invalid')
    monkeypatch.setattr(settings, 'replay_upload_code', 'test-code')
    def forbidden(*args, **kw): raise AssertionError("demo started a background thread")
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    async def run():
        async with main.lifespan(main.app): pass
    asyncio.run(run())

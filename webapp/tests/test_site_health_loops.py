"""W9: the background loops record failures (loop_failed) and show when quiet (loop_quiet). SQLite, no network."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models.map_pool import MapPoolPatchNotes
from app.models.replay import Replay
from app.models.site_events import SiteEvent, SiteEventResolution
from app.services import map_pool_refresh, replay_archive_sync, replay_match_states, site_health, replay_upload
from app.services import replay_control_remote as remote


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, tables=[SiteEvent.__table__, SiteEventResolution.__table__, Replay.__table__,
                                         MapPoolPatchNotes.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine)


def keys(factory, kind="loop_failed"):
    with factory() as s:
        return {r.key: r for r in s.scalars(select(SiteEvent).where(SiteEvent.kind == kind))}


class Boom(Exception):
    pass


def drive(factory, name, run, patch, outcomes):
    """Run a loop; pass i raises Boom if outcomes[i] is False. Returns the open loop_failed keys seen at each
    pass's start. The last pass sets the stop event."""
    stop = threading.Event()
    seen = []
    calls = iter(outcomes)

    def fake(*args, **kwargs):
        seen.append(set(keys(factory)))
        ok = next(calls)
        if len(seen) == len(outcomes):
            stop.set()
        if not ok:
            raise Boom("synthetic failure")
        return {"written": 0, "deleted": 0, "unchanged": 0, "seconds": 0.0, "errors": 0}

    patch(fake)
    run(stop)
    return seen


def loops_under_test(monkeypatch, factory):
    monkeypatch.setattr(map_pool_refresh, "_last_check", None)
    monkeypatch.setattr(map_pool_refresh, "MIN_GAP_S", 0)
    return {
        "control": ("control-dispatch",
                    lambda stop: remote.run_forever(factory, None, stop, every_s=0),
                    lambda f: monkeypatch.setattr(remote, "cycle", f)),
        "archive": ("archive-sync",
                    lambda stop: replay_archive_sync.run_forever(factory, None, stop, every_s=0),
                    lambda f: monkeypatch.setattr(replay_archive_sync, "cycle", f)),
        "states": ("replay-states",
                   lambda stop: replay_match_states.run_forever(factory, stop, every_s=0),
                   lambda f: monkeypatch.setattr(replay_match_states, "refresh", f)),
        "pool": ("map-pool-refresh",
                 lambda stop: map_pool_refresh.run_forever(factory, None, stop, every_s=0, first_delay_s=0),
                 lambda f: monkeypatch.setattr(map_pool_refresh, "check", f)),
    }


@pytest.mark.parametrize("which", ["control", "archive", "states", "pool"])
def test_failed_pass_recorded_and_next_clean_pass_resolves(which, factory, monkeypatch):
    name, run, patch = loops_under_test(monkeypatch, factory)[which]
    seen = drive(factory, name, run, patch, [False, True])
    assert seen[0] == set() and seen[1] == {name}
    assert keys(factory) == {}


@pytest.mark.parametrize("which", ["control", "archive", "states", "pool"])
def test_failed_pass_stays_while_failing_and_has_traceback(which, factory, monkeypatch):
    name, run, patch = loops_under_test(monkeypatch, factory)[which]
    drive(factory, name, run, patch, [False, False])
    row = keys(factory)[name]
    assert row.count == 2 and row.error == "Boom: synthetic failure"
    assert 0 < len(row.context["traceback"]) <= 15


@pytest.mark.parametrize("which", ["control", "archive", "states", "pool"])
def test_registered_while_running_and_unregistered_on_exit(which, factory, monkeypatch):
    name, run, patch = loops_under_test(monkeypatch, factory)[which]
    during = []

    def probe(f):
        def wrapped(*a, **k):
            during.append(name in site_health.loops())
            return f(*a, **k)
        return wrapped

    drive(factory, name, run, lambda f: patch(probe(f)), [True, True])
    assert during == [True, True]
    assert name not in site_health.loops()


class BadStop:
    def wait(self, *_):
        raise RuntimeError("escapes")

    def is_set(self):
        return False


@pytest.mark.parametrize("which", ["control", "archive", "states", "pool"])
def test_unregistered_when_an_exception_escapes(which, factory, monkeypatch):
    name, _, patch = loops_under_test(monkeypatch, factory)[which]
    patch(lambda *a, **k: {"written": 0, "deleted": 0, "unchanged": 0, "seconds": 0.0, "errors": 0})
    runs = {
        "control": lambda: remote.run_forever(factory, None, BadStop(), every_s=0),
        "archive": lambda: replay_archive_sync.run_forever(factory, None, BadStop(), every_s=0),
        "states": lambda: replay_match_states.run_forever(factory, BadStop(), every_s=0),
        "pool": lambda: map_pool_refresh.run_forever(factory, None, BadStop(), every_s=0, first_delay_s=0),
    }
    with pytest.raises(RuntimeError):
        runs[which]()
    assert name not in site_health.loops()


# ------------------------------------------------------------------------------------------- inner keys
def run_cycle(factory, worker):
    state = remote.State()
    remote.cycle(factory, None, state, now=1000.0, worker=worker)


@pytest.fixture
def cycle_env(factory, monkeypatch):
    monkeypatch.setattr(remote, "_collect", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_submit", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(remote, "LIVE", None)
    monkeypatch.setattr(remote.replay_control, "prepare_discovered", lambda *a, **k: None)
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: False)
    return factory


def test_reparse_pass_key(cycle_env, factory, monkeypatch):
    def boom(*a, **k):
        raise Boom("reparse")

    monkeypatch.setattr(remote.reparse_auto, "step", boom)
    run_cycle(factory, object())
    assert set(keys(factory)) == {"reparse_pass"}
    run_cycle(factory, object())
    assert keys(factory)["reparse_pass"].count == 2
    run_cycle(factory, None)   # the step did not run: not a success of that step
    assert set(keys(factory)) == {"reparse_pass"}
    monkeypatch.setattr(remote.reparse_auto, "step", lambda *a, **k: frozenset())
    run_cycle(factory, object())
    assert keys(factory) == {}


def test_height_rebuild_step_key(cycle_env, factory, monkeypatch):
    monkeypatch.setattr(remote.reparse_auto, "step", lambda *a, **k: frozenset())
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: True)

    def boom(*a, **k):
        raise Boom("heights")

    monkeypatch.setattr(remote.replay_heights_remote, "step", boom)
    run_cycle(factory, None)
    run_cycle(factory, None)
    assert keys(factory)["height_rebuild_step"].count == 2
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: False)
    run_cycle(factory, None)   # the heights step was off: still open
    assert set(keys(factory)) == {"height_rebuild_step"}
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: True)
    monkeypatch.setattr(remote.replay_heights_remote, "step", lambda *a, **k: frozenset())
    run_cycle(factory, None)
    assert keys(factory) == {}


def test_archive_worker_needs_three_failing_cycles(factory, monkeypatch):
    class Client:
        fail = True

        def health(self):
            if self.fail:
                raise replay_upload.WorkerError("down")
            return {"archive": {}}

    client = Client()
    monkeypatch.setattr(replay_upload, "collect_unfinished", lambda *a, **k: 0)
    stop = threading.Event()
    n = {"i": 0}
    real_cycle = replay_archive_sync.cycle
    after = []

    def counting(*a, **k):
        if n["i"]:
            after.append(set(keys(factory)))
        n["i"] += 1
        if n["i"] == 5:
            client.fail = False
        if n["i"] == 6:
            stop.set()
        return real_cycle(*a, **k)

    monkeypatch.setattr(replay_archive_sync, "cycle", counting)
    replay_archive_sync.run_forever(factory, client, stop, every_s=0)
    # state seen at the start of cycles 2..6: after 1 fail, 2 fails, 3 fails, 4 fails, then a clean cycle 5
    assert after == [set(), set(), {"archive_sync:worker"}, {"archive_sync:worker"}, set()]
    assert keys(factory) == {}


def test_map_pool_check_key(factory, monkeypatch):
    def bad_fetch(url):
        raise OSError("listing down")

    counts = map_pool_refresh.check(factory, bad_fetch)
    assert counts["errors"] == 1
    assert set(keys(factory)) == {"map_pool:check"}
    assert "listing down" in keys(factory)["map_pool:check"].error
    counts = map_pool_refresh.check(factory, lambda url: "")
    assert counts["errors"] == 0
    assert keys(factory) == {}


def test_map_pool_page_errors_record(factory, monkeypatch):
    from app.services.patch_notes import ListingItem

    item = ListingItem(url="https://x/valorant-patch-notes-9-9", title="Patch Notes 9.9",
                       published_at=datetime(2999, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(map_pool_refresh, "parse_listing", lambda html: [item])
    monkeypatch.setattr(map_pool_refresh.map_pool, "seed_newest_published_at", lambda: None)

    def fetch(url):
        if url == map_pool_refresh.LISTING_URL:
            return "listing"
        raise OSError("page down")

    counts = map_pool_refresh.check(factory, fetch)
    assert counts["errors"] == 1 and set(keys(factory)) == {"map_pool:check"}


# --------------------------------------------------------------------------------------- quiet_loops
T0 = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(site_health, "_now", lambda: now["t"])
    return now


def test_quiet_after_three_cycles_plus_slack(clock):
    site_health.register("q", 100)
    limit = 3 * 100 + 120
    assert site_health.quiet_loops(T0 + timedelta(seconds=limit - 1)) == []
    [item] = site_health.quiet_loops(T0 + timedelta(seconds=limit + 1))
    assert item["name"] == "q" and item["last_end"] is None and item["limit_s"] == limit
    assert item["in_pass_since"] is None


def test_end_beat_clears_and_in_pass_reported(clock):
    site_health.register("q", 100)
    clock["t"] = T0 + timedelta(seconds=1000)
    site_health.beat("q", "start")
    [item] = site_health.quiet_loops(T0 + timedelta(seconds=1100))
    assert item["in_pass_since"] == T0 + timedelta(seconds=1000)
    site_health.beat("q", "end")
    assert site_health.quiet_loops(T0 + timedelta(seconds=1100)) == []
    [item] = site_health.quiet_loops(T0 + timedelta(seconds=1000 + 421))
    assert item["last_end"] == T0 + timedelta(seconds=1000) and item["in_pass_since"] is None


def test_allowed_s_overrides_and_unregistered_never_shows(clock):
    site_health.register("pool", 86400, 26 * 3600)
    assert site_health.quiet_loops(T0 + timedelta(hours=25)) == []
    assert [i["name"] for i in site_health.quiet_loops(T0 + timedelta(hours=27))] == ["pool"]
    site_health.unregister("pool")
    assert site_health.quiet_loops(T0 + timedelta(days=9)) == []

import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models.site_events import SiteEvent, SiteEventResolution
from app.services import site_health

# Recent, so the 30-day retention in every flush never deletes the rows under test.
T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)


def naive(d):
    return d.replace(tzinfo=None) if d.tzinfo else d


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, tables=[SiteEvent.__table__, SiteEventResolution.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine)


def rows(factory):
    with factory() as s:
        return {(r.kind, r.key): r for r in s.scalars(select(SiteEvent))}


def never_factory():
    raise AssertionError("a session was opened")


def test_record_upserts(factory):
    site_health.record("k", "a", "first", {"x": 1}, session_factory=factory, at=T0)
    site_health.record("k", "a", "second", {"x": 2}, session_factory=factory, at=T0 + timedelta(hours=1))
    r = rows(factory)[("k", "a")]
    assert r.count == 2 and r.error == "second" and r.context == {"x": 2}
    assert naive(r.first_seen) == naive(T0)
    assert naive(r.last_seen) == naive(T0 + timedelta(hours=1))


def test_scrub():
    s = site_health.scrub
    assert "secret" not in s("postgresql://u:secret@host/db")
    assert s("postgresql://u:secret@host/db") == "postgresql://u:***@host/db"
    out = s("a password=x&token=y; ok")
    assert "x&" not in out and "=y" not in out and "password=***" in out and "token=***" in out
    assert s("key=abc rest") == "key=*** rest"
    assert s("PASSWORD=Hunter2 Token=zz") == "PASSWORD=*** Token=***"
    assert s("pw='abc'") == "pw='abc'"
    assert s('key="abc" x') == 'key="***" x'


def test_scrub_context_and_cut(factory):
    site_health.record("k", "a", "e" * 5000,
                       {"u": "postgresql://u:secret@h/d", "n": [{"t": "token=abc"}, 3], "i": 4},
                       session_factory=factory, at=T0)
    r = rows(factory)[("k", "a")]
    assert len(r.error) == 2000
    assert r.context == {"u": "postgresql://u:***@h/d", "n": [{"t": "token=***"}, 3], "i": 4}


def test_record_never_raises(caplog, engine):
    def bad():
        raise RuntimeError("db down")
    with caplog.at_level(logging.WARNING):
        site_health.record("k", "a", "e", session_factory=bad)
    assert caplog.records


def test_enqueue_and_count_open_no_session():
    site_health.enqueue("k", "a", "e", at=T0)
    site_health.count("k", "b", {"i": 1}, at=T0)
    assert site_health._open >= {("k", "a"), ("k", "b")}


def test_flush_writes_with_occurrence_time(factory):
    site_health.enqueue("k", "q", "queued", {"a": 1}, at=T0)
    site_health.count("k", "c", {"i": 1}, at=T0)
    site_health.count("k", "c", {"i": 2}, at=T0 + timedelta(minutes=5))
    site_health.flush(session_factory=factory)
    r = rows(factory)
    assert r[("k", "q")].count == 1 and naive(r[("k", "q")].last_seen) == naive(T0)
    c = r[("k", "c")]
    assert c.count == 2 and naive(c.last_seen) == naive(T0 + timedelta(minutes=5))
    assert naive(c.first_seen) == naive(T0)
    assert c.context == [{"i": 1}, {"i": 2}]
    # counters were drained: a second flush adds nothing
    site_health.flush(session_factory=factory)
    assert rows(factory)[("k", "c")].count == 2


def test_flush_adds_to_existing(factory):
    site_health.record("k", "c", "old", session_factory=factory, at=T0)
    for _ in range(3):
        site_health.count("k", "c", {"i": 1}, at=T0 + timedelta(days=1))
    site_health.flush(session_factory=factory)
    assert rows(factory)[("k", "c")].count == 4


def test_counter_keeps_last_five():
    for i in range(8):
        site_health.count("k", "c", {"i": i}, at=T0)
    assert site_health._counters[("k", "c")]["items"] == [{"i": i} for i in range(3, 8)]


def test_failed_flush_keeps_entries(factory):
    site_health.enqueue("k", "q", "e", at=T0)
    site_health.count("k", "c", {"i": 1}, at=T0)

    def bad():
        raise RuntimeError("down")
    site_health.flush(session_factory=bad)
    site_health.flush(session_factory=factory)
    site_health.flush(session_factory=factory)
    r = rows(factory)
    assert r[("k", "q")].count == 1 and r[("k", "c")].count == 1


def test_queue_bound():
    for i in range(1001):
        site_health.enqueue("k", str(i), "e", at=T0)
    assert len(site_health._queue) == 1000
    assert site_health._dropped == 1
    assert site_health._queue[0]["key"] == "1"


def test_count_merge():
    def merge(old, item):
        return item if old is None or item["ms"] > old["ms"] else old
    for ms in (5, 90, 7):
        site_health.count("slow", "r", {"ms": ms}, merge=merge, at=T0)
    assert site_health._counters[("slow", "r")]["context"] == {"ms": 90}


def test_merge_context_is_stored(factory):
    site_health.count("slow", "r", {"ms": 5}, merge=lambda o, i: i, at=T0)
    site_health.flush(session_factory=factory)
    assert rows(factory)[("slow", "r")].context == {"ms": 5}


def test_retention_age(factory):
    now = datetime.now(timezone.utc)
    site_health.record("k", "old", "e", session_factory=factory, at=now - timedelta(days=31))
    site_health.record("k", "new", "e", session_factory=factory, at=now - timedelta(days=29))
    site_health.flush(session_factory=factory)
    assert set(rows(factory)) == {("k", "new")}


def test_retention_per_kind(factory):
    now = datetime.now(timezone.utc)
    with factory() as s:
        for i in range(501):
            s.add(SiteEvent(kind="k", key=f"{i:04d}", error="", count=1,
                            first_seen=now - timedelta(hours=600 - i), last_seen=now - timedelta(hours=600 - i)))
        s.add(SiteEvent(kind="other", key="x", error="", count=1, first_seen=now, last_seen=now))
        s.commit()
    site_health.flush(session_factory=factory)
    r = rows(factory)
    assert len([k for k in r if k[0] == "k"]) == 500
    assert ("k", "0000") not in r and ("k", "0500") in r and ("other", "x") in r


def test_resolve(factory, engine):
    site_health.record("k", "a", "e", session_factory=factory, at=T0)
    site_health.resolve("k", "a", session_factory=factory)
    assert rows(factory) == {}
    seen = []
    event.listen(engine, "before_cursor_execute", lambda *a: seen.append(a[2]))
    site_health.resolve("k", "a", session_factory=factory)
    assert seen == []


def test_resolve_loads_kind_once_then_deletes_stored(factory, engine):
    with factory() as s:
        s.add(SiteEvent(kind="k", key="a", error="", count=1, first_seen=T0, last_seen=T0))
        s.commit()
    site_health.resolve("k", "a", session_factory=factory)
    assert rows(factory) == {}


def test_resolve_drops_queued_and_counted(factory):
    site_health.enqueue("k", "a", "e", at=T0)
    site_health.count("k", "b", {"i": 1}, at=T0)
    site_health.resolve("k", "a", session_factory=factory)
    site_health.resolve("k", "b", session_factory=factory)
    site_health.flush(session_factory=factory)
    assert rows(factory) == {}


def test_resolve_prefix(factory):
    for key in ("m1:a", "m1:b", "m2:a"):
        site_health.record("k", key, "e", session_factory=factory, at=T0)
    site_health.enqueue("k", "m1:q", "e", at=T0)
    site_health.resolve_prefix("k", "m1:", session_factory=factory)
    site_health.flush(session_factory=factory)
    assert set(rows(factory)) == {("k", "m2:a")}


def test_demo_mode_writes_nothing(factory, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    site_health.record("k", "a", "e", session_factory=factory)
    site_health.enqueue("k", "b", "e")
    site_health.count("k", "c")
    site_health.flush(session_factory=factory)
    site_health.resolve("k", "a", session_factory=never_factory)
    assert rows(factory) == {}
    assert site_health.start(factory) is None
    assert not [t for t in threading.enumerate() if t.name == "site-health-flush"]


def test_start_stop(factory, monkeypatch):
    monkeypatch.setattr(site_health, "FLUSH_S", 0.05)
    stop = site_health.start(factory)
    site_health.enqueue("k", "a", "e", at=T0)
    site_health.stop(stop)
    assert ("k", "a") in rows(factory)
    assert not [t for t in threading.enumerate() if t.name == "site-health-flush"]


def test_stop_flushes_once_more(factory, monkeypatch):
    monkeypatch.setattr(site_health, "FLUSH_S", 3600)
    stop = site_health.start(factory)
    site_health.count("k", "a", at=T0)
    site_health.stop(stop)
    assert rows(factory)[("k", "a")].count == 1


def test_loops_registry():
    site_health.register("x", 10, 30)
    site_health.beat("x", "start")
    snap = site_health.loops()
    assert snap["x"]["cycle_s"] == 10 and snap["x"]["allowed_s"] == 30 and "start" in snap["x"]["beats"]
    snap["x"]["cycle_s"] = 99
    assert site_health.loops()["x"]["cycle_s"] == 10
    site_health.unregister("x")
    assert "x" not in site_health.loops()


# ---------------------------------------------------------------------------------- clearing by PR (W6)
def entry(kind="page_error", key="/a", fixed_by="x"):
    return {"kind": kind, "key": key, "fixed_by": fixed_by}


def resolutions(factory):
    with factory() as s:
        return {r.entry: r for r in s.scalars(select(SiteEventResolution))}


def test_two_starts_apply_once(factory):
    e = [entry()]
    site_health.record("page_error", "/a", "boom", session_factory=factory, at=T0 - timedelta(hours=1))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0)
    assert ("page_error", "/a") in rows(factory)  # still inside the grace
    (res,) = resolutions(factory).values()
    assert naive(res.cutoff) == naive(T0 + timedelta(seconds=600)) and res.applied_at is None
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert ("page_error", "/a") not in rows(factory)
    (res,) = resolutions(factory).values()
    assert naive(res.applied_at) == naive(T0 + timedelta(seconds=601))
    site_health.record("page_error", "/a", "again", session_factory=factory, at=T0 + timedelta(seconds=700))
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=1000), entries=e,
                               now=T0 + timedelta(seconds=2000))
    assert ("page_error", "/a") in rows(factory)
    # an applied entry is skipped entirely: even a late-flushed row dated before the cutoff stays
    site_health.record("page_error", "/a", "late", session_factory=factory, at=T0 + timedelta(seconds=100))
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=3000), entries=e,
                               now=T0 + timedelta(seconds=4000))
    assert ("page_error", "/a") in rows(factory)


def test_apply_spares_other_keys_and_rows_after_the_cutoff(factory):
    site_health.record("page_error", "/a", "boom", session_factory=factory, at=T0)
    site_health.record("page_error", "/b", "other", session_factory=factory, at=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    # the old process records /a again after the cutoff, before this process's next apply runs
    site_health.record("page_error", "/a", "again", session_factory=factory, at=T0 + timedelta(seconds=650))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=700))
    left = rows(factory)
    assert ("page_error", "/b") in left
    assert ("page_error", "/a") in left and left[("page_error", "/a")].error == "again"


def test_row_inside_grace_is_deleted(factory):
    site_health.record("page_error", "/a", "boom", session_factory=factory, at=T0 + timedelta(seconds=300))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=601))
    assert rows(factory) == {}


def test_null_key_clears_the_kind_once(factory):
    e = [entry(key=None)]
    for k in ("/a", "/b"):
        site_health.record("page_error", k, "boom", session_factory=factory, at=T0)
    site_health.record("slow_page", "/a", "slow", session_factory=factory, at=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert set(rows(factory)) == {("slow_page", "/a")}
    site_health.record("page_error", "/c", "boom", session_factory=factory, at=T0 + timedelta(seconds=700))
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=1000), entries=e,
                               now=T0 + timedelta(seconds=2000))
    assert ("page_error", "/c") in rows(factory)


def test_editing_fixed_by_does_not_reapply(factory):
    assert site_health.entry_id(entry(fixed_by="x")) == site_health.entry_id(entry(fixed_by="PR 9"))
    assert site_health.entry_id(entry()) != site_health.entry_id(entry(key=None))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=5000),
                               entries=[entry(fixed_by="PR 9")], now=T0 + timedelta(seconds=5000))
    assert len(resolutions(factory)) == 1


def test_unknown_kind_is_skipped_with_a_warning(factory, caplog):
    site_health.record("page_error", "/a", "boom", session_factory=factory, at=T0)
    with caplog.at_level(logging.WARNING, logger=site_health.logger.name):
        site_health.apply_resolved(session_factory=factory, started_at=T0,
                                   entries=[entry(kind="nope"), entry()], now=T0)
    assert any("nope" in r.getMessage() for r in caplog.records)
    assert len(resolutions(factory)) == 1


def test_apply_resolved_never_raises(caplog):
    def broken():
        raise RuntimeError("db down")
    site_health.apply_resolved(session_factory=broken, started_at=T0, entries=[entry()], now=T0)


def test_same_resolution_twice_does_not_raise(factory):
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health._reset_for_tests()
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=1), entries=[entry()],
                               now=T0 + timedelta(seconds=1))
    assert len(resolutions(factory)) == 1


def test_applied_drops_open_memory(factory):
    site_health.record("page_error", "/a", "boom", session_factory=factory, at=T0)
    site_health._open.add(("page_error", "/a"))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=601))
    assert ("page_error", "/a") not in site_health._open


def test_applying_keeps_a_recurrence_not_yet_flushed(factory):
    # A crash queued after the cutoff is a recurrence: applying the entry must not drop it before the flush.
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.enqueue("page_error", "/a", "again", at=T0 + timedelta(seconds=650))
    site_health.count("page_error", "/a", at=T0 + timedelta(seconds=650))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=700))
    assert ("page_error", "/a") in site_health._open
    site_health.flush(session_factory=factory)
    assert rows(factory)[("page_error", "/a")].count == 2


def test_check_resolved():
    check = site_health.check_resolved
    assert check([entry()]) == [] and check([entry(key=None)]) == []
    assert check([entry(kind="nope")])
    assert check([{"kind": "page_error", "key": "/a"}])
    assert check([entry(fixed_by="")])
    assert check([entry(key=5)])
    assert check(["not a dict"])
    assert "given_up" in site_health.KNOWN_KINDS and "unscored_match" in site_health.KNOWN_KINDS


def test_committed_file_passes():
    assert site_health.check_resolved(site_health.load_resolved()) == []


def test_demo_mode_apply_resolved_issues_nothing(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    site_health.apply_resolved(session_factory=never_factory, started_at=T0, entries=[entry()], now=T0)

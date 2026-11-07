import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models.site_events import SiteEvent, SiteEventMark, SiteEventResolution
from app.services import site_health

# Recent, so the 30-day retention in every flush never deletes the rows under test.
T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)


def naive(d):
    return d.replace(tzinfo=None) if d.tzinfo else d


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, tables=[SiteEvent.__table__, SiteEventResolution.__table__, SiteEventMark.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine)


def rows(factory):
    with factory() as s:
        return {(r.kind, r.key): r for r in s.scalars(select(SiteEvent))}


def put(factory, kind, key, error="e", context=None, at=T0):
    """Record one occurrence and flush it."""
    site_health.record(kind, key, error, context, at=at)
    site_health.flush(session_factory=factory)


def stored(factory, kind, key, at=T0):
    """A row another process wrote: not in this process's memory at all."""
    with factory() as s:
        s.add(SiteEvent(kind=kind, key=key, error="", count=1, first_seen=at, last_seen=at))
        s.commit()


def never_factory():
    raise AssertionError("a session was opened")


def new_process():
    """A restart: the resolved file is inserted and checked again."""
    site_health._resolved.update(inserted=False, done=False)


# ------------------------------------------------------------------------------------------ record and flush
def test_record_never_opens_a_session_and_flush_upserts(factory):
    site_health.record("k", "a", "first", {"x": 1}, at=T0)
    site_health.record("k", "a", "second", {"x": 2}, at=T0 + timedelta(hours=1))
    assert rows(factory) == {}
    site_health.flush(session_factory=factory)
    r = rows(factory)[("k", "a")]
    assert r.count == 2 and r.error == "second" and r.context == {"x": 2}
    assert naive(r.first_seen) == naive(T0)
    assert naive(r.last_seen) == naive(T0 + timedelta(hours=1))


def test_a_late_older_occurrence_never_moves_last_seen_back_or_replaces_newer_evidence(factory):
    # B4: the old process flushes an occurrence from before the one already stored.
    put(factory, "page_error", "/a", "new", {"v": "new"}, at=T0 + timedelta(minutes=10))
    put(factory, "page_error", "/a", "old", {"v": "old"}, at=T0)
    r = rows(factory)[("page_error", "/a")]
    assert r.count == 2 and r.error == "new" and r.context == {"v": "new"}
    assert naive(r.first_seen) == naive(T0) and naive(r.last_seen) == naive(T0 + timedelta(minutes=10))


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


@pytest.mark.parametrize("text, secret", [
    ('{"password": "two words here", "user": "u"}', "two words here"),
    ("{'token': 'abc123', 'n': 1}", "abc123"),
    ("secret=s3cr3t next", "s3cr3t"),
    ("api_key: AKIA123 rest", "AKIA123"),
    ("headers={'Authorization': 'Bearer eyJhbGciOi.x.y'}", "eyJhbGciOi"),
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ('password="a \\"quoted\\" one" after', "quoted"),
])
def test_scrub_structured_and_quoted_secrets(text, secret):
    out = site_health.scrub(text)
    assert secret not in out and "***" in out


def test_scrub_leaves_ordinary_errors_alone():
    s = site_health.scrub
    assert s("KeyError: 'abc'") == "KeyError: 'abc'"
    assert s("duplicate key value violates unique constraint") == \
        "duplicate key value violates unique constraint"


def test_scrub_context_and_cut(factory):
    put(factory, "k", "a", "e" * 5000, {"u": "postgresql://u:secret@h/d", "n": [{"t": "token=abc"}, 3], "i": 4})
    r = rows(factory)[("k", "a")]
    assert len(r.error) == 2000
    assert r.context == {"u": "postgresql://u:***@h/d", "n": [{"t": "token=***"}, 3], "i": 4}


def test_nul_and_unencodable_text_is_made_storable(factory):
    put(factory, "page_error", "GET /matches/{external_id}", "bad \x00 byte",
        {"path": "/matches/probe\x00", "s": "lone \udc80 surrogate", "obj": object.__name__})
    r = rows(factory)[("page_error", "GET /matches/{external_id}")]
    assert "\x00" not in r.error and "\\x00" in r.error
    assert r.context["path"] == "/matches/probe\\x00" and "\udc80" not in r.context["s"]


def test_record_and_count_never_raise(monkeypatch, caplog):
    monkeypatch.setattr(site_health, "_now", lambda: (_ for _ in ()).throw(RuntimeError("clock")))
    with caplog.at_level(logging.ERROR):
        site_health.record("k", "a", "e")
        site_health.count("k", "b")
        site_health.resolve("k", "a")
    assert caplog.records


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
    site_health.flush(session_factory=factory)     # counters were drained: a second flush adds nothing
    assert rows(factory)[("k", "c")].count == 2


def test_flush_adds_to_existing_and_keeps_the_last_five_items_across_flushes(factory):
    put(factory, "k", "c", "old")
    for i in range(3):
        site_health.count("k", "c", {"i": i}, at=T0 + timedelta(days=1))
    site_health.flush(session_factory=factory)
    for i in range(3, 6):
        site_health.count("k", "c", {"i": i}, at=T0 + timedelta(days=1))
    site_health.flush(session_factory=factory)
    r = rows(factory)[("k", "c")]
    assert r.count == 7 and r.context == [{"i": i} for i in range(1, 6)]


def test_counter_keeps_last_five():
    for i in range(8):
        site_health.count("k", "c", {"i": i}, at=T0)
    assert site_health._counters[("k", "c")]["items"] == [{"i": i} for i in range(3, 8)]


def test_failed_flush_keeps_entries(factory):
    site_health.enqueue("k", "q", "e", at=T0)
    site_health.count("k", "c", {"i": 1}, at=T0)

    def bad():
        raise OperationalError("connect", {}, Exception("down"))
    site_health.flush(session_factory=bad)
    site_health.flush(session_factory=factory)
    site_health.flush(session_factory=factory)
    r = rows(factory)
    assert r[("k", "q")].count == 1 and r[("k", "c")].count == 1


def test_one_entry_the_database_refuses_is_dropped_and_the_rest_are_written(factory, engine, caplog):
    # B2: a value the database can't store must not roll back, and then block, every other entry.
    def refuse(conn, cursor, statement, params, context, executemany):
        if "poison" in repr(params):
            raise DataError(statement, params, Exception("invalid input"))
    event.listen(engine, "before_cursor_execute", refuse)
    site_health.enqueue("page_error", "/a", "poison", at=T0)
    site_health.enqueue("page_error", "/b", "fine", at=T0)
    site_health.count("slow_page", "/c", {"s": 6}, at=T0)
    with caplog.at_level(logging.ERROR, logger=site_health.logger.name):
        site_health.flush(session_factory=factory)
    assert set(rows(factory)) == {("page_error", "/b"), ("slow_page", "/c")}
    assert any("dropped" in r.getMessage() for r in caplog.records)
    assert not site_health._queue
    site_health.flush(session_factory=factory)     # nothing left to retry
    assert rows(factory)[("page_error", "/b")].count == 1


def test_a_lost_connection_mid_flush_keeps_everything_for_the_next_flush(factory, engine):
    calls = {"n": 0}

    def drop(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("INSERT") and calls["n"] == 0:
            calls["n"] += 1
            raise OperationalError(statement, params, Exception("server closed the connection"))
    event.listen(engine, "before_cursor_execute", drop)
    site_health.enqueue("page_error", "/a", "e", at=T0)
    site_health.flush(session_factory=factory)
    assert rows(factory) == {} and len(site_health._queue) == 1
    site_health.flush(session_factory=factory)
    assert ("page_error", "/a") in rows(factory)


def test_queue_bound():
    for i in range(1001):
        site_health.enqueue("k", str(i), "e", at=T0)
    assert len(site_health._queue) == 1000
    assert site_health._dropped == 1
    assert site_health._queue[0]["key"] == "1"


def test_records_of_one_key_are_one_write(factory, engine):
    for i in range(50):
        site_health.enqueue("page_error", "/a", f"e{i}", at=T0 + timedelta(seconds=i))
    inserts = []
    event.listen(engine, "before_cursor_execute",
                 lambda c, cur, statement, *a: inserts.append(statement) if "INSERT INTO site_events" in statement else None)
    site_health.flush(session_factory=factory)
    assert len(inserts) == 1
    r = rows(factory)[("page_error", "/a")]
    assert r.count == 50 and r.error == "e49"


def test_count_merge():
    def merge(old, item):
        return item if old is None or item["ms"] > old["ms"] else old
    for ms in (5, 90, 7):
        site_health.count("slow", "r", {"ms": ms}, merge=merge, at=T0)
    assert site_health._counters[("slow", "r")]["context"] == {"ms": 90}


def test_merge_context_is_combined_with_the_stored_one(factory):
    def merge(old, item):
        return {"max": max((old or {}).get("max", 0), item), "n": (old or {}).get("n", 0) + 1}

    def combine(a, b):
        return {"max": max(a["max"], b["max"]), "n": a["n"] + b["n"]}
    site_health.count("slow", "r", 40, merge=merge, combine=combine, at=T0)
    site_health.flush(session_factory=factory)
    site_health.count("slow", "r", 6, merge=merge, combine=combine, at=T0 + timedelta(minutes=2))
    site_health.flush(session_factory=factory)
    r = rows(factory)[("slow", "r")]
    assert r.context == {"max": 40, "n": 2} and r.count == 2


def test_retention_age(factory):
    now = datetime.now(timezone.utc)
    site_health.record("k", "old", "e", at=now - timedelta(days=31))
    site_health.record("k", "new", "e", at=now - timedelta(days=29))
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


# ----------------------------------------------------------------------------------------------- resolving
def test_resolve_deletes_on_the_next_flush_and_never_opens_a_session_itself(factory):
    put(factory, "k", "a")
    site_health.resolve("k", "a", at=T0 + timedelta(seconds=1))
    assert ("k", "a") in rows(factory)
    site_health.flush(session_factory=factory)
    assert rows(factory) == {}


def test_resolve_clears_a_row_another_process_wrote(factory):
    # B2/B6: no per-process cache of open keys: a row this process never saw is still cleared.
    stored(factory, "loop_failed", "control_dispatch")
    site_health.resolve("loop_failed", "control_dispatch", at=T0 + timedelta(seconds=1))
    site_health.flush(session_factory=factory)
    assert rows(factory) == {}


def test_order_is_kept_a_failure_after_the_success_stays(factory):
    site_health.record("loop_failed", "x", "before", at=T0)
    site_health.resolve("loop_failed", "x", at=T0 + timedelta(seconds=1))
    site_health.record("loop_failed", "x", "after", at=T0 + timedelta(seconds=2))
    site_health.flush(session_factory=factory)
    r = rows(factory)[("loop_failed", "x")]
    assert r.count == 1 and r.error == "after"


def test_resolve_spares_a_row_last_seen_after_the_success(factory):
    # another process recorded the failure after this one's success
    stored(factory, "loop_failed", "x", at=T0 + timedelta(minutes=5))
    site_health.resolve("loop_failed", "x", at=T0)
    site_health.flush(session_factory=factory)
    assert ("loop_failed", "x") in rows(factory)


def test_a_success_during_an_in_flight_flush_still_clears_what_that_flush_writes(factory):
    # B5: the flush has taken the counter; the success arrives before the flush commits.
    site_health.count("worker_refused", "Ascent:Refused", {"r": 1}, at=T0)

    def racing():
        site_health.resolve_prefix("worker_refused", "Ascent:", at=T0 + timedelta(seconds=1))
        return factory()
    site_health.flush(session_factory=racing)
    assert ("worker_refused", "Ascent:Refused") in rows(factory)
    site_health.flush(session_factory=factory)
    assert rows(factory) == {}


def test_resolve_drops_queued_and_counted(factory):
    site_health.enqueue("k", "a", "e", at=T0)
    site_health.count("k", "b", {"i": 1}, at=T0)
    site_health.resolve("k", "a", at=T0 + timedelta(seconds=1))
    site_health.resolve("k", "b", at=T0 + timedelta(seconds=1))
    site_health.flush(session_factory=factory)
    assert rows(factory) == {}


def test_resolve_prefix(factory):
    for key in ("m1:a", "m1:b", "m2:a", "m1%x"):
        site_health.record("k", key, "e", at=T0)
    site_health.flush(session_factory=factory)
    site_health.enqueue("k", "m1:q", "e", at=T0)
    site_health.resolve_prefix("k", "m1:", at=T0 + timedelta(seconds=1))
    site_health.flush(session_factory=factory)
    assert set(rows(factory)) == {("k", "m2:a"), ("k", "m1%x")}


def test_record_now_and_resolve_now_write_at_once(factory):
    site_health.record_now("util_build_failed", "build", "boom", {"t": 1}, session_factory=factory, at=T0)
    assert rows(factory)[("util_build_failed", "build")].error == "boom"
    site_health.resolve_now("util_build_failed", "build", session_factory=factory)
    assert rows(factory) == {}


def test_record_now_never_raises(caplog):
    def bad():
        raise RuntimeError("db down")
    with caplog.at_level(logging.WARNING):
        site_health.record_now("k", "a", "e", session_factory=bad)
        site_health.resolve_now("k", "a", session_factory=bad)
    assert caplog.records


def test_flush_for_writes_on_a_sessions_engine(factory):
    site_health.count("cache_fallback", "player_view_cache:prewarm", 7, at=T0)
    with factory() as db:
        site_health.flush_for(db)
    assert rows(factory)[("cache_fallback", "player_view_cache:prewarm")].context == [7]


def test_demo_mode_writes_nothing(factory, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    site_health.record("k", "a", "e")
    site_health.enqueue("k", "b", "e")
    site_health.count("k", "c")
    site_health.resolve("k", "a")
    site_health.flush(session_factory=never_factory)
    site_health.record_now("k", "a", "e", session_factory=never_factory)
    site_health.resolve_now("k", "a", session_factory=never_factory)
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
    put(factory, "page_error", "/a", "boom", at=T0 - timedelta(hours=1))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0)
    assert ("page_error", "/a") in rows(factory)  # still inside the grace
    (res,) = resolutions(factory).values()
    assert naive(res.cutoff) == naive(T0 + timedelta(seconds=600)) and res.applied_at is None
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert ("page_error", "/a") not in rows(factory)
    (res,) = resolutions(factory).values()
    assert naive(res.applied_at) == naive(T0 + timedelta(seconds=601))
    put(factory, "page_error", "/a", "again", at=T0 + timedelta(seconds=700))
    new_process()
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=1000), entries=e,
                               now=T0 + timedelta(seconds=2000))
    assert ("page_error", "/a") in rows(factory)


def test_a_late_flush_of_an_occurrence_before_an_applied_cutoff_is_dropped(factory):
    # B7: the old process flushes evidence of the fixed bug after the entry was applied.
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=601))
    site_health.record("page_error", "/a", "late", at=T0 + timedelta(seconds=100))
    site_health.record("page_error", "/b", "other key", at=T0 + timedelta(seconds=100))
    site_health.flush(session_factory=factory)
    assert set(rows(factory)) == {("page_error", "/b")}
    put(factory, "page_error", "/a", "recurrence", at=T0 + timedelta(seconds=650))
    assert rows(factory)[("page_error", "/a")].error == "recurrence"


def test_a_due_entry_is_applied_by_one_process_only(factory, engine):
    # B7: both processes selected the entry as due; the other one applied it (and a recurrence came) before this
    # one's claim, so this one finds it claimed and deletes nothing.
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    put(factory, "page_error", "/a", "boom", at=T0 + timedelta(seconds=100))
    raced = []

    def other_process_applies_first(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE SITE_EVENT_RESOLUTIONS") and not raced:
            raced.append(True)
            cursor.execute("UPDATE site_event_resolutions SET applied_at = '2000-01-01 00:00:00'")
    event.listen(engine, "before_cursor_execute", other_process_applies_first)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=700))
    assert raced and ("page_error", "/a") in rows(factory)


def test_apply_spares_other_keys_and_rows_after_the_cutoff(factory):
    put(factory, "page_error", "/a", "boom")
    put(factory, "page_error", "/b", "other")
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    # the old process records /a again after the cutoff, before this process's next apply runs
    put(factory, "page_error", "/a", "again", at=T0 + timedelta(seconds=650))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=700))
    left = rows(factory)
    assert ("page_error", "/b") in left
    assert ("page_error", "/a") in left and left[("page_error", "/a")].error == "again"


def test_row_inside_grace_is_deleted(factory):
    put(factory, "page_error", "/a", "boom", at=T0 + timedelta(seconds=300))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=601))
    assert rows(factory) == {}


def test_null_key_clears_the_kind_once(factory):
    e = [entry(key=None)]
    for k in ("/a", "/b"):
        put(factory, "page_error", k, "boom")
    put(factory, "slow_page", "/a", "slow")
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert set(rows(factory)) == {("slow_page", "/a")}
    put(factory, "page_error", "/c", "boom", at=T0 + timedelta(seconds=700))
    new_process()
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=1000), entries=e,
                               now=T0 + timedelta(seconds=2000))
    assert ("page_error", "/c") in rows(factory)


def test_a_second_fix_of_the_same_key_applies_again(factory):
    # B1/B8: PR A cleared the key; months later it recurs and PR B adds its own entry.
    first = entry(fixed_by="PR #190")
    put(factory, "page_error", "/a", "first bug")
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[first], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[first],
                               now=T0 + timedelta(seconds=601))
    assert rows(factory) == {}
    later = T0 + timedelta(days=1)
    put(factory, "page_error", "/a", "second bug", at=later)
    second = entry(fixed_by="PR #205")
    assert site_health.entry_id(first) != site_health.entry_id(second)
    new_process()
    site_health.apply_resolved(session_factory=factory, started_at=later + timedelta(hours=1),
                               entries=[first, second], now=later + timedelta(hours=1))
    site_health.apply_resolved(session_factory=factory, started_at=later + timedelta(hours=1),
                               entries=[first, second], now=later + timedelta(hours=1, seconds=601))
    assert rows(factory) == {}
    assert len(resolutions(factory)) == 2


def test_once_every_entry_is_applied_no_more_queries(factory, engine):
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=601))
    seen = []
    event.listen(engine, "before_cursor_execute", lambda *a: seen.append(a[2]))
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()],
                               now=T0 + timedelta(seconds=700))
    assert seen == []


def test_an_entry_naming_no_stored_kind_is_skipped_with_a_warning(factory, caplog):
    put(factory, "page_error", "/a", "boom")
    with caplog.at_level(logging.WARNING, logger=site_health.logger.name):
        site_health.apply_resolved(session_factory=factory, started_at=T0,
                                   entries=[entry(kind="nope"), entry(kind="unscored_match"), entry()], now=T0)
    assert any("nope" in r.getMessage() for r in caplog.records)
    assert any("unscored_match" in r.getMessage() for r in caplog.records)
    assert len(resolutions(factory)) == 1


def test_apply_resolved_never_raises(caplog):
    def broken():
        raise RuntimeError("db down")
    site_health.apply_resolved(session_factory=broken, started_at=T0, entries=[entry()], now=T0)


def test_same_resolution_twice_does_not_raise(factory):
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry()], now=T0)
    new_process()
    site_health.apply_resolved(session_factory=factory, started_at=T0 + timedelta(seconds=1), entries=[entry()],
                               now=T0 + timedelta(seconds=1))
    assert len(resolutions(factory)) == 1


def test_check_resolved():
    check = site_health.check_resolved
    assert check([entry()]) == [] and check([entry(key=None)]) == []
    assert check([entry(), entry(fixed_by="PR #2")]) == []
    assert check([entry(), entry()])                       # the same fix twice
    assert check([entry(kind="nope")])
    assert check([entry(kind="unscored_match")])           # checked live: nothing stored to clear
    assert check([entry(kind="control_failed")])           # a replay kind: nothing stored to clear
    assert check([{"kind": "page_error", "key": "/a"}])
    assert check([entry(fixed_by="")])
    assert check([entry(key=5)])
    assert check(["not a dict"])
    assert site_health.RECORDED_KINDS <= site_health.KNOWN_KINDS
    assert "given_up" in site_health.KNOWN_KINDS and "unscored_match" in site_health.KNOWN_KINDS


def test_committed_file_passes():
    assert site_health.check_resolved(site_health.load_resolved()) == []


def test_demo_mode_apply_resolved_issues_nothing(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    site_health.apply_resolved(session_factory=never_factory, started_at=T0, entries=[entry()], now=T0)


# ------------------------------------------------------------------------------- third and fourth reviews
def test_scrubbing_a_quote_followed_by_backslashes_takes_linear_time():
    # R4-1: both quoted-value alternatives used to match a backslash, so an unmatched quote before a run of
    # backslashes backtracked exponentially (125 ms at 28 backslashes).
    import time

    for text in ('password="' + "\\" * 5000, 'token="' + "\\a" * 3000 + "\\", "key='" + "\\" * 4999 + "x"):
        started = time.perf_counter()
        out = site_health.scrub(text)
        assert time.perf_counter() - started < 0.5
        assert "***" in out and "\\\\\\" not in out


@pytest.mark.parametrize("text, kept", [
    ('worker said password="hunter2 and the rest was cut', 'worker said password="***'),   # R4-2: cut short
    ("conn token='abc", "conn token='***"),
    ('key="ends with a backslash \\', 'key="***'),
    ('password="done" then more', 'password="***" then more'),
])
def test_a_quoted_secret_with_no_closing_quote_is_hidden_to_the_end(text, kept):
    assert site_health.scrub(text) == kept


def test_a_context_field_named_like_a_secret_is_hidden(factory):
    # R4-2 / R3-4: the value alone gives the scrubber nothing to go on
    context = {"token": "abc", "db_password": 5, "X-Api-Key": "k", "map": "Ascent", "times": 2,
               "nested": [{"secret": "s", "path": "/a"}], "key": None}
    site_health.record("page_error", "/a", "e", context, at=T0)
    site_health.flush(session_factory=factory)
    assert rows(factory)[("page_error", "/a")].context == {
        "token": "***", "db_password": "***", "X-Api-Key": "***", "map": "Ascent", "times": 2,
        "nested": [{"secret": "***", "path": "/a"}], "key": None}


def test_a_counter_put_back_by_a_failed_flush_is_dropped_when_a_success_came_meanwhile(factory):
    # R3-1 / R4-3: the flush took the counter, a success arrived, then the flush lost its connection
    site_health.count("worker_refused", "Ascent:Refused", {"r": 1}, at=T0)

    def racing_then_down():
        site_health.resolve_prefix("worker_refused", "Ascent:", at=T0 + timedelta(seconds=1))
        raise OperationalError("connect", {}, Exception("down"))
    site_health.flush(session_factory=racing_then_down)
    assert ("worker_refused", "Ascent:Refused") not in site_health._counters
    site_health.flush(session_factory=factory)
    assert rows(factory) == {}


def test_a_counter_put_back_by_a_failed_flush_stays_when_no_success_came(factory):
    site_health.count("worker_refused", "Ascent:Refused", {"r": 1}, at=T0)

    def other_success_then_down():
        site_health.resolve_prefix("worker_refused", "Bind:", at=T0 + timedelta(seconds=1))
        raise OperationalError("connect", {}, Exception("down"))
    site_health.flush(session_factory=other_success_then_down)
    site_health.flush(session_factory=factory)
    assert rows(factory)[("worker_refused", "Ascent:Refused")].count == 1


def test_repeated_resolves_of_one_key_are_one_queued_entry():
    # R3-2: an idle dispatcher's clears no longer push an outage's failures out of the queue
    for i in range(50):
        site_health.resolve("loop_failed", "control-dispatch", at=T0 + timedelta(seconds=i))
        site_health.resolve_prefix("worker_refused", "Ascent:", at=T0 + timedelta(seconds=i))
    assert [(e["key"], e["at"]) for e in site_health._queue] == [
        ("control-dispatch", T0 + timedelta(seconds=49)), ("Ascent:", T0 + timedelta(seconds=49))]


def test_a_resolve_after_a_record_of_its_key_is_kept_apart(factory):
    site_health.resolve("loop_failed", "a", at=T0)
    site_health.record("loop_failed", "a", "e", at=T0 + timedelta(seconds=1))
    site_health.resolve("loop_failed", "a", at=T0 + timedelta(seconds=2))
    site_health.record("loop_failed", "a", "again", at=T0 + timedelta(seconds=3))
    assert [e["op"] for e in site_health._queue] == ["resolve", "record", "resolve", "record"]
    site_health.flush(session_factory=factory)
    assert rows(factory)[("loop_failed", "a")].error == "again"


def test_dropped_entries_are_logged_once(factory, caplog):
    for i in range(1003):
        site_health.enqueue("k", str(i), "e", at=T0)
    with caplog.at_level(logging.WARNING, logger=site_health.logger.name):
        site_health.flush(session_factory=factory)
        site_health.flush(session_factory=factory)
    assert [r.getMessage() for r in caplog.records if "dropped" in r.getMessage()] == [
        "site health: 3 queued entries were dropped (the queue holds 1000; oldest go first)"]


def test_a_failed_flush_that_overfills_the_queue_drops_the_oldest(factory):
    for i in range(600):
        site_health.enqueue("k", f"old{i}", "e", at=T0)

    def busy_then_down():
        for i in range(600):
            site_health.enqueue("k", f"new{i}", "e", at=T0)
        raise OperationalError("connect", {}, Exception("down"))
    site_health.flush(session_factory=busy_then_down)
    keys = [e["key"] for e in site_health._queue]
    assert len(keys) == 1000 and keys[0] == "old200" and keys[-1] == "new599"


def warm_seen(seen, new):
    from app.services.replay_control_remote import _warm_seen
    return _warm_seen(seen, new)


def test_dedupe_is_decided_against_the_stored_mark_at_flush_time(factory):
    # R4-6: the old and the new site report the same loss; whichever flushes second finds it counted
    for _ in range(2):          # two processes, each flushing its own report
        site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 1, "boot": "b1"}, at=T0, dedupe=warm_seen)
        site_health.flush(session_factory=factory)
    assert rows(factory)[("warm_vanished", "Ascent:1")].count == 1
    site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 2, "boot": "b1"},
                       at=T0 + timedelta(minutes=1), dedupe=warm_seen)
    site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 1, "boot": "b2"},
                       at=T0 + timedelta(minutes=2), dedupe=warm_seen)
    site_health.flush(session_factory=factory)
    r = rows(factory)[("warm_vanished", "Ascent:1")]
    assert r.count == 3 and r.context == {"times": 1, "boot": "b2"}, "two new losses in one write; the new boot last"


def test_a_late_report_of_an_earlier_boot_is_not_counted_again(factory):
    # R6-A3: one process flushed boot A then boot B before the other's report of A arrived
    for boot in ("A", "B", "A"):
        site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 1, "boot": boot}, at=T0, dedupe=warm_seen)
        site_health.flush(session_factory=factory)
    assert rows(factory)[("warm_vanished", "Ascent:1")].count == 2


def test_a_loss_cleared_by_a_pr_is_not_brought_back_by_a_restart_rereading_it(factory):
    # R5-A5 / R6-A3: the mark outlives the row
    site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 1, "boot": "A"}, at=T0, dedupe=warm_seen)
    site_health.flush(session_factory=factory)
    e = [{"kind": "warm_vanished", "key": "Ascent:1", "fixed_by": "x"}]
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert ("warm_vanished", "Ascent:1") not in rows(factory)
    site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 1, "boot": "A"},
                       at=T0 + timedelta(hours=1), dedupe=warm_seen)          # a site restart re-reads the count
    site_health.flush(session_factory=factory)
    assert ("warm_vanished", "Ascent:1") not in rows(factory)
    site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 2, "boot": "A"},
                       at=T0 + timedelta(hours=2), dedupe=warm_seen)          # a new loss still lands
    site_health.flush(session_factory=factory)
    assert rows(factory)[("warm_vanished", "Ascent:1")].count == 1


def test_another_processs_late_failure_from_before_a_success_is_not_stored(factory):
    # R5-B1 / R6-B1: the new process's success flushed first; the old process's earlier failure arrives after
    site_health.resolve("worker_overrun", "Ascent:1:3", at=T0 + timedelta(minutes=5))
    site_health.flush(session_factory=factory)
    site_health._reset_for_tests()                                       # the old process
    site_health.record("worker_overrun", "Ascent:1:3", "late", at=T0)
    site_health.count("worker_overrun", "Ascent:1:3", at=T0 + timedelta(minutes=1))
    site_health.flush(session_factory=factory)
    assert ("worker_overrun", "Ascent:1:3") not in rows(factory)
    site_health.record("worker_overrun", "Ascent:1:3", "after", at=T0 + timedelta(minutes=6))
    site_health.flush(session_factory=factory)
    assert rows(factory)[("worker_overrun", "Ascent:1:3")].error == "after", "a failure after the success stays"


def test_a_prefix_success_leaves_a_mark_for_every_key_under_it(factory):
    site_health.resolve_prefix("worker_refused", "Ascent:", at=T0 + timedelta(minutes=5))
    site_health.flush(session_factory=factory)
    site_health._reset_for_tests()
    site_health.count("worker_refused", "Ascent:Refused", at=T0)
    site_health.count("worker_refused", "Bind:Refused", at=T0)
    site_health.flush(session_factory=factory)
    assert set(rows(factory)) == {("worker_refused", "Bind:Refused")}


def test_resolve_now_leaves_a_mark_and_says_whether_it_stored(factory):
    assert site_health.resolve_now("util_build_failed", "build", session_factory=factory,
                                   at=T0 + timedelta(minutes=5)) is True
    assert site_health.record_now("util_build_failed", "build", "late", session_factory=factory, at=T0) is True
    assert rows(factory) == {}
    assert site_health.resolve_now("util_build_failed", "build", session_factory=never_factory) is False


def test_marks_are_kept_30_days(factory):
    with factory() as s:
        s.add(SiteEventMark(kind="k", key="old", prefix=False, cleared_at=T0, updated_at=T0 - timedelta(days=31)))
        s.add(SiteEventMark(kind="k", key="new", prefix=False, cleared_at=T0, updated_at=T0))
        s.commit()
    put(factory, "k", "x")
    with factory() as s:
        assert [m.key for m in s.scalars(select(SiteEventMark))] == ["new"]


@pytest.mark.parametrize("text, secret", [
    ("db_password=hunter2", "hunter2"),
    ("RIOT_API_KEY=RGAPI-abc", "RGAPI-abc"),
    ("PGPASSWORD=hunter2", "hunter2"),
    ("client_secret=xyz", "xyz"),
    ("access_token=abc", "abc"),
    ("SESSION_SECRET=abc", "abc"),
    ('{\\"password\\": \\"hunter2\\"}', "hunter2"),
    ("{'token': b'raw'}", "raw"),
    ("password='it''s'", "s'"),
])
def test_scrub_hides_prefixed_escaped_bytes_and_doubled_quote_forms(text, secret):
    # R5-A1: a prefix before the name, escaped JSON, bytes and SQL's doubled quote were left in clear
    out = site_health.scrub(text)
    assert secret not in out and "***" in out, out


def test_scrub_keeps_the_text_around_an_escaped_json_secret():
    assert site_health.scrub('{\\"password\\": \\"hunter2\\", \\"user\\": \\"u\\"}') == \
        '{\\"password\\": \\"***\\", \\"user\\": \\"u\\"}'


def test_scrubbing_many_unfinished_urls_takes_linear_time():
    # R5-A2 / R6-B2: the URL password pass looked for an @ to the end of the text from every start
    import time

    for text in ("a://b:" * 50_000, "a://u:p/" * 40_000, "a." * 100_000 + "://"):
        started = time.perf_counter()
        site_health.scrub(text)
        assert time.perf_counter() - started < 1.0, text[:12]


def test_scrub_with_a_limit_cuts_before_and_after(monkeypatch):
    seen = []
    real = site_health._storable
    monkeypatch.setattr(site_health, "_storable", lambda text: seen.append(len(text)) or real(text))
    out = site_health.scrub("token=abc " + "x" * 10_000, 300)
    assert len(out) == 300 and out.startswith("token=*** ")
    assert seen == [300 * site_health.SCRUB_SPAN - len("abc") + len("***")], "only the first 1,200 were scrubbed"


def test_the_queue_holds_an_errors_text_cut_to_what_the_flush_scrubs():
    # R5-B3
    site_health.record("page_error", "/a", "e" * 100_000, at=T0)
    assert len(site_health._queue[-1]["error"]) == site_health.ERROR_MAX * site_health.SCRUB_SPAN


def test_a_new_counter_row_is_inserted_with_its_context(factory):
    site_health.count("slow_page", "/a", {"s": 6}, at=T0)
    site_health.flush(session_factory=factory)
    r = rows(factory)[("slow_page", "/a")]
    assert r.count == 1 and r.context == [{"s": 6}] and r.error == ""


def test_record_now_skips_an_occurrence_an_applied_cutoff_covers(factory):
    # R4-5: the child's direct write honours an applied PR resolution like a flush does
    site_health.apply_resolved(session_factory=factory, started_at=T0, entries=[entry("util_build_failed", "build")],
                               now=T0 + timedelta(seconds=700))
    assert site_health.record_now("util_build_failed", "build", "old", session_factory=factory, at=T0) is True
    assert rows(factory) == {}
    assert site_health.record_now("util_build_failed", "build", "new", session_factory=factory,
                                  at=T0 + timedelta(seconds=601)) is True
    assert ("util_build_failed", "build") in rows(factory)


def test_record_now_says_whether_it_stored():
    def bad():
        raise RuntimeError("db down")
    assert site_health.record_now("k", "a", "e", session_factory=bad) is False


def test_join_loops_waits_for_the_registered_threads_only_up_to_its_timeout():
    import time

    go, registered = threading.Event(), threading.Event()

    def loop(name, sleep_s):
        site_health.register(name, 60)
        registered.set()
        go.wait()
        time.sleep(sleep_s)
        if name == "quick":
            site_health.record("loop_failed", name, "late", at=T0)

    threading.Thread(target=loop, args=("quick", 0.2), daemon=True).start()
    assert registered.wait(5)
    go.set()
    site_health.join_loops(5)
    assert [e["key"] for e in site_health._queue] == ["quick"]
    registered.clear()
    go.clear()
    threading.Thread(target=loop, args=("slow", 10), daemon=True).start()
    assert registered.wait(5)
    go.set()
    started = time.monotonic()
    site_health.join_loops(0.3)
    assert time.monotonic() - started < 2

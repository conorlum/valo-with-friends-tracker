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

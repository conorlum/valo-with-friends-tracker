from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.site_events import SiteEvent
from app.services import site_health
from tests.support.postgres import scratch_database, migrated_database, pg_factory  # noqa: F401

pytestmark = pytest.mark.postgres

# Recent, so the 30-day retention in every flush never prunes the rows under test (B21: a fixed date aged out).
T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)


def get(factory, kind, key):
    with factory() as s:
        return s.get(SiteEvent, (kind, key))


def put(factory, kind, key, error="e", context=None, at=T0):
    site_health.record(kind, key, error, context, at=at)
    site_health.flush(session_factory=factory)


def test_two_records_count_two(pg_factory):
    put(pg_factory, "k", "a", "one")
    put(pg_factory, "k", "a", "two")
    r = get(pg_factory, "k", "a")
    assert r.count == 2 and r.error == "two"


def test_a_late_older_occurrence_never_moves_last_seen_back(pg_factory):
    put(pg_factory, "page_error", "/a", "new", {"v": 2}, at=T0 + timedelta(minutes=10))
    put(pg_factory, "page_error", "/a", "old", {"v": 1}, at=T0)
    r = get(pg_factory, "page_error", "/a")
    assert r.count == 2 and r.error == "new" and r.context == {"v": 2}
    assert r.first_seen == T0 and r.last_seen == T0 + timedelta(minutes=10)


def test_a_nul_from_a_request_path_neither_fails_nor_blocks_the_flush(pg_factory):
    # B2 (second review): PostgreSQL refuses NUL in text and JSONB; one such entry must not hold the rest back.
    site_health.enqueue("page_error", "GET /matches/{external_id}", "bad \x00 path",
                        {"path": "/matches/probe\x00"}, at=T0)
    site_health.enqueue("page_error", "GET /other", "fine", at=T0)
    site_health.count("slow_page", "/x", {"s": 6}, at=T0)
    site_health.flush(session_factory=pg_factory)
    r = get(pg_factory, "page_error", "GET /matches/{external_id}")
    assert r.context == {"path": "/matches/probe\\x00"}
    assert get(pg_factory, "page_error", "GET /other") is not None and get(pg_factory, "slow_page", "/x")
    assert not site_health._queue


def test_an_entry_postgres_refuses_is_dropped_and_the_rest_are_written(pg_factory, monkeypatch):
    # past the cleaning, a value PostgreSQL still refuses (here: a key over the column's 300 characters,
    # bypassing the cut) is dropped alone
    monkeypatch.setattr(site_health, "_clean_key", lambda key: str(key))
    site_health.enqueue("page_error", "k" * 400, "too long", at=T0)
    site_health.enqueue("page_error", "GET /fine", "fine", at=T0)
    site_health.flush(session_factory=pg_factory)
    with pg_factory() as s:
        keys = [r.key for r in s.scalars(select(SiteEvent))]
    assert keys == ["GET /fine"] and not site_health._queue


def test_resolve_deletes_what_another_process_wrote(pg_factory):
    put(pg_factory, "loop_failed", "control-dispatch", "boom")
    site_health._reset_for_tests()                    # this process never saw that row
    site_health.resolve("loop_failed", "control-dispatch", at=T0 + timedelta(seconds=1))
    site_health.flush(session_factory=pg_factory)
    assert get(pg_factory, "loop_failed", "control-dispatch") is None


def test_nested_context_roundtrips(pg_factory):
    ctx = {"a": {"b": [1, 2, {"c": "d"}]}, "e": None}
    put(pg_factory, "k", "a", "e", ctx)
    assert get(pg_factory, "k", "a").context == ctx


def test_flush_counted(pg_factory):
    put(pg_factory, "k", "c", "e")
    for i in range(3):
        site_health.count("k", "c", {"i": i}, at=T0)
    site_health.enqueue("k", "q", "e", at=T0)
    site_health.flush(session_factory=pg_factory)
    assert get(pg_factory, "k", "c").count == 4
    assert get(pg_factory, "k", "c").context == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert get(pg_factory, "k", "q").count == 1


def test_apply_resolved_twice_deletes_and_does_not_raise(pg_factory):
    from datetime import timedelta
    from app.models.site_events import SiteEventResolution
    e = [{"kind": "page_error", "key": "/a", "fixed_by": "x"}]
    put(pg_factory, "page_error", "/a", "boom")
    put(pg_factory, "page_error", "/b", "boom")
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0)
    site_health._reset_for_tests()                    # a second process
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0)
    assert get(pg_factory, "page_error", "/a") is not None
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0 + timedelta(seconds=601))
    assert get(pg_factory, "page_error", "/a") is None and get(pg_factory, "page_error", "/b") is not None
    with pg_factory() as s:
        rows = s.scalars(select(SiteEventResolution)).all()
    assert len(rows) == 1 and rows[0].applied_at is not None


def test_util_build_stale_is_silent_while_the_build_lock_is_held(pg_factory):
    from datetime import timedelta
    from sqlalchemy import text
    from app.models.replay import ReplayRoundUtil, Replay  # noqa: F401
    from app.services import replay_gaps, site_health_checks, util_rebuild

    old = T0 - timedelta(hours=6)
    with pg_factory() as s:
        # the FK chain is replay -> round -> util; the check reads only the util row, so drop the FK for the probe
        s.execute(text("ALTER TABLE replay_round_util DROP CONSTRAINT IF EXISTS fk_replay_round_util_round"))
        s.add(ReplayRoundUtil(replay_id=1, round_number=1, status="ok", fingerprint="f",
                              gaps_revision=replay_gaps.GAPS_REVISION, computed_at=old))
        s.commit()
    with pg_factory() as s:
        assert len(site_health_checks.util_build_stale(s, T0)) == 1
    holder = pg_factory.kw["bind"].connect()
    try:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": util_rebuild.LOCK_KEY})
        holder.commit()
        with pg_factory() as s:
            assert site_health_checks.util_build_stale(s, T0) == []
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": util_rebuild.LOCK_KEY})
        holder.commit()
        holder.close()
    with pg_factory() as s:
        assert len(site_health_checks.util_build_stale(s, T0)) == 1



def test_a_flush_waits_for_a_resolution_being_applied_and_then_honours_its_cutoff(pg_factory):
    # R4-5: the old process's flush must not land an occurrence from before the cutoff after the deletion
    import threading
    import time

    from sqlalchemy import update
    from app.models.site_events import SiteEventResolution

    e = [{"kind": "page_error", "key": "/a", "fixed_by": "x"}]
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0)   # inserted, not due
    applier = pg_factory()
    applier.execute(update(SiteEventResolution).values(applied_at=T0 + timedelta(seconds=700)))   # claimed, open
    site_health.record("page_error", "/a", "from the old process", at=T0 + timedelta(seconds=100))
    flusher = threading.Thread(target=site_health.flush, kwargs={"session_factory": pg_factory})
    flusher.start()
    time.sleep(0.5)
    assert flusher.is_alive(), "the flush waits for the resolution being applied"
    applier.commit()
    applier.close()
    flusher.join(10)
    assert not flusher.is_alive()
    assert get(pg_factory, "page_error", "/a") is None and not site_health._queue


def test_an_apply_waits_for_a_flush_in_progress(pg_factory):
    from sqlalchemy import text, update
    from sqlalchemy.exc import OperationalError
    from app.models.site_events import SiteEventResolution

    e = [{"kind": "page_error", "key": "/a", "fixed_by": "x"}]
    site_health.apply_resolved(session_factory=pg_factory, started_at=T0, entries=e, now=T0)
    flushing = pg_factory()
    site_health._cutoffs(flushing)                      # a flush's transaction, before its writes commit
    with pg_factory() as applier:
        applier.execute(text("SET lock_timeout = '300ms'"))
        with pytest.raises(OperationalError):
            applier.execute(update(SiteEventResolution).values(applied_at=T0 + timedelta(seconds=700)))
    flushing.rollback()
    flushing.close()


def test_two_flushes_of_one_counter_key_take_turns(pg_factory):
    # R4-8: the stored context is read under a row lock, so a second writer waits for the first's commit
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    site_health.count("slow_page", "/a", {"s": 20}, at=T0)
    site_health.flush(session_factory=pg_factory)
    first = pg_factory()
    row = site_health._insert_or_lock(first, "slow_page", "/a", error="", context=None, count=1,
                                      first_seen=T0, last_seen=T0)
    assert row is not site_health._INSERTED and row.context == [{"s": 20}]
    with pg_factory() as second:
        second.execute(text("SET lock_timeout = '300ms'"))
        with pytest.raises(OperationalError):
            site_health._insert_or_lock(second, "slow_page", "/a", error="", context=None, count=1,
                                        first_seen=T0, last_seen=T0)
    first.rollback()
    first.close()


def test_one_warm_loss_reported_by_two_processes_is_stored_once(pg_factory):
    # R4-6, on PostgreSQL's upsert and row lock
    def same(stored, new):
        return (stored or {}).get("boot") == new.get("boot") and new["times"] <= (stored or {}).get("times", 0)

    for _ in range(2):
        site_health.record("warm_vanished", "Ascent:1", "lost", {"times": 1, "boot": "b"}, at=T0, unless_stored=same)
        site_health.flush(session_factory=pg_factory)
    assert get(pg_factory, "warm_vanished", "Ascent:1").count == 1

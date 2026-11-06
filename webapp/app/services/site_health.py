"""Site health core: what background code reports as failed or slow, stored in `site_events` (one row per
(kind, key)) so the upload page's card can read it. Imports nothing heavy, and opens a session only when the
caller hands in a session factory: there is no default database.

Three ways in: `record` (one upsert now), `enqueue` (a queued entry, written by the next flush) and `count`
(an in-memory counter, written by the next flush). `flush` also prunes (30 days, 500 keys per kind). Nothing
here raises into its caller, and nothing writes in demo mode. The loop registry (`register`/`beat`/`loops`) is
in memory only."""

import copy
import hashlib
import json
import logging
import re
import threading
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite

from app.config import settings
from app.models.site_events import SiteEvent, SiteEventResolution

logger = logging.getLogger(__name__)

FLUSH_S = 60  # PROVISIONAL(D1): flush interval
ERROR_MAX = 2000
QUEUE_MAX = 1000
KEEP_DAYS = 30
KEEP_PER_KIND = 500
LAST_ITEMS = 5

_lock = threading.RLock()
_queue: deque = deque()
_dropped = 0
_counters: dict = {}
_open: set = set()
_loaded_kinds: set = set()
_loops: dict = {}
_threads: dict = {}

_URL_PASSWORD = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*:)[^@\s]*@")
_SECRET_VALUE = re.compile(r"(password|token|key)=([\"']?)[^&\s;\"']*", re.IGNORECASE)


def _now():
    return datetime.now(timezone.utc)


def _demo():
    return bool(settings.demo_mode)


def scrub(text):
    """Hide connection-URL passwords and the values of password=, token= and key=."""
    text = _URL_PASSWORD.sub(r"\1***@", text)
    return _SECRET_VALUE.sub(lambda m: f"{m.group(1)}={m.group(2)}***", text)


def _scrub_value(value):
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


def _clean_error(error):
    return scrub(str(error if error is not None else ""))[:ERROR_MAX]


def _upsert(session, kind, key, *, error, context, n, first, last, set_error=True, set_context=True):
    table = SiteEvent.__table__
    dialect = session.get_bind().dialect.name
    insert = (postgresql if dialect == "postgresql" else sqlite).insert
    values = dict(kind=kind, key=key, error=error if error is not None else "", context=context,
                  count=n, first_seen=first, last_seen=last)
    stmt = insert(table).values(**values)
    update = {"count": table.c.count + n, "last_seen": stmt.excluded.last_seen}
    if set_error:
        update["error"] = stmt.excluded.error
    if set_context:
        update["context"] = stmt.excluded.context
    session.execute(stmt.on_conflict_do_update(index_elements=["kind", "key"], set_=update))


def record(kind, key, error, context=None, *, session_factory, at=None):
    """Store one occurrence now, in its own session. Never raises."""
    if _demo():
        return
    try:
        when = at or _now()
        with session_factory() as session:
            _upsert(session, kind, key, error=_clean_error(error), context=_scrub_value(context),
                    n=1, first=when, last=when)
            session.commit()
        with _lock:
            _open.add((kind, key))
    except Exception:
        logger.exception("site health: could not record %s %s", kind, key)


def enqueue(kind, key, error, context=None, *, at=None):
    """Queue one occurrence for the next flush. Never touches the database."""
    global _dropped
    if _demo():
        return
    with _lock:
        _queue.append({"kind": kind, "key": key, "error": error, "context": context, "at": at or _now()})
        while len(_queue) > QUEUE_MAX:
            _queue.popleft()
            _dropped += 1
        _open.add((kind, key))


def count(kind, key, context_item=None, *, merge=None, at=None):
    """Count one occurrence in memory. The stored context is the last 5 items, or `merge(old, item)`."""
    if _demo():
        return
    when = at or _now()
    with _lock:
        c = _counters.get((kind, key))
        if c is None:
            c = _counters[(kind, key)] = {"n": 0, "first": when, "at": when, "items": [], "context": None, "merge": None}
        c["n"] += 1
        if when >= c["at"]:
            c["at"] = when
        if merge is not None:
            c["merge"] = True
            c["context"] = merge(c["context"], context_item)
        elif context_item is not None:
            c["items"] = (c["items"] + [context_item])[-LAST_ITEMS:]
        _open.add((kind, key))


def _counter_context(c):
    return c["context"] if c["merge"] else (list(c["items"]) or None)


def _delete_keys(session, kind, keys):
    for i in range(0, len(keys), 200):
        session.execute(delete(SiteEvent).where(SiteEvent.kind == kind, SiteEvent.key.in_(keys[i:i + 200])))


def _drop_memory(match):
    with _lock:
        for entry in [e for e in _queue if match(e["kind"], e["key"])]:
            _queue.remove(entry)
        for k in [k for k in _counters if match(*k)]:
            del _counters[k]
        for k in [k for k in _open if match(*k)]:
            _open.discard(k)


def _forget_open(match):
    """Forget open keys whose stored rows were deleted, except a key with an occurrence not yet flushed
    (a recurrence after a PR's cutoff must still reach the table)."""
    with _lock:
        pending = {(e["kind"], e["key"]) for e in _queue} | set(_counters)
        for k in [k for k in _open if match(*k) and k not in pending]:
            _open.discard(k)


def _load_kind(session, kind):
    if kind in _loaded_kinds:
        return
    keys = session.scalars(select(SiteEvent.key).where(SiteEvent.kind == kind)).all()
    with _lock:
        _open.update((kind, k) for k in keys)
        _loaded_kinds.add(kind)


def resolve(kind, key, *, session_factory):
    """Delete the stored row for a key that is fine again. A key that is not open costs no statement."""
    if _demo():
        return
    try:
        with session_factory() as session:
            _load_kind(session, kind)
            with _lock:
                was_open = (kind, key) in _open
            if was_open:
                _delete_keys(session, kind, [key])
                session.commit()
        _drop_memory(lambda k, ky: (k, ky) == (kind, key))
    except Exception:
        logger.exception("site health: could not resolve %s %s", kind, key)


def resolve_prefix(kind, prefix, *, session_factory):
    """`resolve` for every open key of the kind that starts with `prefix`."""
    if _demo():
        return
    try:
        with session_factory() as session:
            _load_kind(session, kind)
            with _lock:
                keys = sorted(k for (kd, k) in _open if kd == kind and k.startswith(prefix))
            if keys:
                _delete_keys(session, kind, keys)
                session.commit()
        _drop_memory(lambda k, ky: k == kind and ky.startswith(prefix))
    except Exception:
        logger.exception("site health: could not resolve %s %s*", kind, prefix)


def _retain(session):
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    session.execute(delete(SiteEvent).where(SiteEvent.last_seen < cutoff))
    for kind in session.scalars(select(SiteEvent.kind).distinct()).all():
        extra = session.scalars(select(SiteEvent.key).where(SiteEvent.kind == kind)
                                .order_by(SiteEvent.last_seen.desc(), SiteEvent.key.desc())
                                .offset(KEEP_PER_KIND)).all()
        if extra:
            _delete_keys(session, kind, list(extra))


def flush(*, session_factory):
    """Write the queue and the counters in one transaction, then prune. On failure keep them for next time."""
    if _demo():
        return
    with _lock:
        queued = list(_queue)
        counters = dict(_counters)
        _queue.clear()
        _counters.clear()
    try:
        with session_factory() as session:
            for e in queued:
                _upsert(session, e["kind"], e["key"], error=_clean_error(e["error"]),
                        context=_scrub_value(e["context"]), n=1, first=e["at"], last=e["at"])
            for (kind, key), c in counters.items():
                ctx = _counter_context(c)
                _upsert(session, kind, key, error="", context=_scrub_value(ctx), n=c["n"], first=c["first"],
                        last=c["at"], set_error=False, set_context=ctx is not None)
            _retain(session)
            session.commit()
    except Exception:
        logger.exception("site health: flush failed; entries kept for the next flush")
        with _lock:
            for e in reversed(queued):
                _queue.appendleft(e)
            for k, c in counters.items():
                cur = _counters.get(k)
                if cur is None:
                    _counters[k] = c
                else:
                    cur["n"] += c["n"]
                    cur["at"] = max(cur["at"], c["at"])
                    cur["first"] = min(cur["first"], c["first"])
                    cur["items"] = (c["items"] + cur["items"])[-LAST_ITEMS:]
                    if cur["context"] is None:
                        cur["context"] = c["context"]
                    cur["merge"] = cur["merge"] or c["merge"]


def register(name, cycle_s, allowed_s=None):
    with _lock:
        loop = _loops.setdefault(name, {"beats": {}})
        loop["cycle_s"] = cycle_s
        loop["allowed_s"] = allowed_s
        loop["registered"] = _now()


def unregister(name):
    with _lock:
        _loops.pop(name, None)


def beat(name, phase):
    with _lock:
        loop = _loops.setdefault(name, {"cycle_s": None, "allowed_s": None, "beats": {}})
        loop["beats"][phase] = _now()


def loops():
    with _lock:
        return copy.deepcopy(_loops)


QUIET_FACTOR = 3  # PROVISIONAL(D1): a loop is quiet after this many of its cycles ...
QUIET_SLACK_S = 120  # PROVISIONAL(D1): ... plus this many seconds


def quiet_loops(now=None):
    """Registered loops whose last finished pass (or registration) is older than their limit."""
    now = now or _now()
    out = []
    for name, loop in sorted(loops().items()):
        cycle_s = loop.get("cycle_s")
        limit = loop.get("allowed_s") or (QUIET_FACTOR * cycle_s + QUIET_SLACK_S if cycle_s is not None else None)
        if limit is None:
            continue
        beats = loop.get("beats", {})
        last_end, started = beats.get("end"), beats.get("start")
        since = last_end or loop.get("registered")
        if since is None or (now - since).total_seconds() <= limit:
            continue
        in_pass = started if started is not None and (last_end is None or started > last_end) else None
        out.append({"name": name, "last_end": last_end, "registered": loop.get("registered"),
                    "in_pass_since": in_pass, "limit_s": limit})
    return out


def loop_failed(name, exc, *, session_factory):
    """Record a loop's (or one of its steps') failure under `name`; call from inside the `except`."""
    lines = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).splitlines()[-15:]
    record("loop_failed", name, f"{type(exc).__name__}: {exc}", {"traceback": lines}, session_factory=session_factory)


def loop_ok(name, *, session_factory):
    resolve("loop_failed", name, session_factory=session_factory)


CACHE_FALLBACK_KEYS = frozenset({
    "player_view_cache:invalid", "player_view_cache:decode", "player_view_cache:prewarm",
    "player_page:write_through",
})

KNOWN_KINDS = frozenset({
    # recorded
    "page_error", "loop_failed", "loop_quiet", "util_build_failed", "util_build_stale", "cache_fallback",
    "worker_overrun", "worker_refused", "slow_page", "warm_vanished",
    # checked
    "unscored_match", "kill_impact_missing", "map_pool_note",
    # replay kinds (app/services/replay_errors.py KINDS)
    "box_conflict", "control_failed", "gaps_failed", "moments_failed", "util_failed", "heights_rejected",
    "heights_out_of_tries", "given_up", "held", "upload_failed", "upload_held", "store_failed", "ack_missing",
    "stuck", "unlinked", "needs_a_person", "feature_failed",
})
GRACE_S = 600  # PROVISIONAL(D1): grace after the first start that has an entry
RESOLVED_FILE = Path(__file__).resolve().parents[1] / "data" / "site_health_resolved.json"


def load_resolved():
    return json.loads(RESOLVED_FILE.read_text(encoding="utf-8"))


def check_resolved(entries, known=KNOWN_KINDS):
    """The problems in a resolved-file's entries (empty when it is fine)."""
    if not isinstance(entries, list):
        return ["the file must hold a list"]
    problems = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f"entry {i}: not an object")
            continue
        if e.get("kind") not in known:
            problems.append(f"entry {i}: unknown kind {e.get('kind')!r}")
        if e.get("key") is not None and not isinstance(e.get("key"), str):
            problems.append(f"entry {i}: key must be a string or null")
        if not isinstance(e.get("fixed_by"), str) or not e["fixed_by"].strip():
            problems.append(f"entry {i}: fixed_by must be a non-empty string")
    return problems


def entry_id(entry):
    """Identity of an entry: its kind and key only, so editing fixed_by never re-applies it."""
    blob = json.dumps({"kind": entry["kind"], "key": entry.get("key")}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def apply_resolved(*, session_factory, started_at, entries=None, now=None):
    """Clear the rows the committed file names, once each, GRACE_S after the first start that has the entry
    (rows last seen before that cutoff go). Never raises."""
    if _demo():
        return
    try:
        entries = load_resolved() if entries is None else entries
        now = now or _now()
        known = []
        for e in entries:
            if not isinstance(e, dict) or e.get("kind") not in KNOWN_KINDS:
                logger.warning("site health: skipping resolved entry with unknown kind: %r", e)
                continue
            known.append(e)
        with session_factory() as session:
            insert = (postgresql if session.get_bind().dialect.name == "postgresql" else sqlite).insert
            table = SiteEventResolution.__table__
            for e in known:
                session.execute(insert(table).values(
                    entry=entry_id(e), kind=e["kind"], key=e.get("key"),
                    cutoff=started_at + timedelta(seconds=GRACE_S), applied_at=None,
                ).on_conflict_do_nothing(index_elements=["entry"]))
            due = session.scalars(select(SiteEventResolution).where(
                SiteEventResolution.applied_at.is_(None), SiteEventResolution.cutoff <= now)).all()
            applied = []
            for r in due:
                stmt = delete(SiteEvent).where(SiteEvent.kind == r.kind, SiteEvent.last_seen < r.cutoff)
                if r.key is not None:
                    stmt = stmt.where(SiteEvent.key == r.key)
                session.execute(stmt)
                r.applied_at = now
                applied.append((r.kind, r.key))
            session.commit()
        for kind, key in applied:
            _forget_open(lambda k, kk, kind=kind, key=key: k == kind and (key is None or kk == key))
    except Exception:
        logger.exception("site health: could not apply the resolved file")


def start(session_factory, started_at=None):
    """Flush every FLUSH_S on a daemon thread (applying the resolved file after each flush when `started_at`
    is given). Returns the stop event, or None in demo mode."""
    if _demo():
        return None
    stop_event = threading.Event()

    def run():
        while not stop_event.wait(FLUSH_S):
            flush(session_factory=session_factory)
            if started_at is not None:
                apply_resolved(session_factory=session_factory, started_at=started_at)
        flush(session_factory=session_factory)

    thread = threading.Thread(target=run, name="site-health-flush", daemon=True)
    _threads[stop_event] = thread
    thread.start()
    return stop_event


def stop(event):
    """Stop the flush thread: it flushes once more and exits; wait up to 5 s for it."""
    if event is None:
        return
    event.set()
    thread = _threads.pop(event, None)
    if thread is not None:
        thread.join(timeout=5)


def _reset_for_tests():
    global _dropped
    with _lock:
        _queue.clear()
        _counters.clear()
        _open.clear()
        _loaded_kinds.clear()
        _loops.clear()
        _dropped = 0

"""Site health core: what background code reports as failed or slow, stored in `site_events` (one row per
(kind, key)) so the upload page's card can read it. Imports nothing heavy, and opens a session only when the
caller hands in a session factory: there is no default database.

The jobs being watched never wait on the database for it: `record` (alias `enqueue`) and `resolve` /
`resolve_prefix` queue in the order they happen, `count` adds to an in-memory counter, and the flush thread
writes them every FLUSH_S in one transaction (a resolve deletes rows last seen before it), then prunes (30 days,
500 keys per kind). Only a process about to exit (the Critical Util child) writes at once (`record_now`,
`resolve_now`). Nothing here raises into its caller, and nothing writes in demo mode. The loop registry
(`register`/`beat`/`loops`) is in memory only."""

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

from sqlalchemy import case, delete, func, select, update
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects import postgresql, sqlite

from app.config import settings
from app.models.site_events import SiteEvent, SiteEventResolution

logger = logging.getLogger(__name__)

FLUSH_S = 60  # Flush interval
ERROR_MAX = 2000
QUEUE_MAX = 1000
KEEP_DAYS = 30
KEEP_PER_KIND = 500
LAST_ITEMS = 5

# Every GET APIRoute is classified explicitly. Only first full-page loads are timed.
# tests/test_site_health_slow.py keeps this table in step with the app's routes.
PAGE_ROUTES = {
    "/": "page",
    "//riot.txt": "not_page",
    "/accounts": "page",
    "/accounts/search": "not_page",
    "/admin/replays/archive": "not_page",
    "/admin/replays/reparse/status": "not_page",
    "/admin/replays/uploads/{upload_id}": "not_page",
    "/friends": "page",
    "/gaps": "page",
    "/gaps/{map_name}": "page",
    "/gaps/{map_name}/routes.json": "not_page",
    "/health": "not_page",
    "/login": "page",
    "/matches": "page",
    "/matches/{external_id}": "page",
    "/matches/{external_id}/replay": "page",
    "/matches/{external_id}/rounds/{round_number}": "not_page",
    "/players": "page",
    "/players/{display_name}": "page",
    "/players/{display_name}/career": "not_page",
    "/players/{display_name}/recent-matches": "not_page",
    "/replays": "page",
    "/replays/upload": "page",
    "/replays/upload/errors.json": "not_page",
    "/replays/upload/errors/prompt.txt": "not_page",
    "/replays/upload/guide/{key}": "page",
    "/replays/upload/progress.json": "not_page",
    "/replays/uploads/{upload_id}": "page",
    "/replays/uploads/{upload_id}/status": "not_page",
    "/replays/{match_uuid}": "page",
    "/replays/{match_uuid}/coach": "page",
    "/replays/{match_uuid}/coach/prompt": "not_page",
    "/replays/{match_uuid}/control/heatmap.json": "not_page",
    "/replays/{match_uuid}/control/players.json": "not_page",
    "/replays/{match_uuid}/moments.json": "not_page",
    "/replays/{match_uuid}/util.json": "not_page",
    "/replays/{match_uuid}/{round_number}.json": "not_page",
    "/replays/{match_uuid}/{round_number}/control.bin": "not_page",
    "/replays/{match_uuid}/{round_number}/gaps.json": "not_page",
    "/riot.txt": "not_page",
    "/search": "page",
    "/sessions": "page",
    "/sessions/{session_index}": "page",
    "/squad": "page",
    "/squad/career": "not_page",
    "/stats": "page",
    "/stats/all": "not_page",
    "/stats/friends/career": "not_page",
    "/stats/map-control": "not_page",
    "/util": "page",
}

_lock = threading.RLock()
_queue: deque = deque()     # records and resolves, in the order they happened
_dropped = 0
_dropped_logged = 0
_counters: dict = {}
_loops: dict = {}
_loop_threads: dict = {}    # loop name -> the thread that registered it (join_loops)
_threads: dict = {}
_resolved = {"inserted": False, "done": False}

_URL_PASSWORD = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*:)[^@\s]*@")
_SECRET_NAMES = r"password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|key"
# name, an optional closing quote (JSON/dict keys), = or :, then a quoted value or a bare one. A quoted value runs
# to its closing quote, or to the end of the text when it has none (cut short): every character has one way to
# match (an escape pair, or one character that is neither a backslash nor the quote), so it runs in linear time.
_SECRET_VALUE = re.compile(
    rf"""(?P<head>\b(?:{_SECRET_NAMES})\b["']?\s*[=:]\s*)"""
    r"""(?:(?P<q>["'])(?:\\[\s\S]|(?!(?P=q))[^\\])*(?:(?P<end>(?P=q))|\\?\Z)|[^&\s;,"'}\]]*)""",
    re.IGNORECASE)
# a mapping's field named like a secret (`token`, `db_password`, `X-Api-Key`): its value is hidden whole
_SECRET_FIELD = re.compile(rf"(?:[\w-]*[_-])?(?:{_SECRET_NAMES})", re.IGNORECASE)
_BEARER = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
KEY_MAX = 300


def _now():
    return datetime.now(timezone.utc)


def _demo():
    return bool(settings.demo_mode)


def _storable(text):
    """Text PostgreSQL will take: no NUL (text and JSONB refuse it) and nothing UTF-8 can't encode."""
    return text.replace("\x00", "\\x00").encode("utf-8", "backslashreplace").decode("utf-8")


def scrub(text):
    """Hide connection-URL passwords, Bearer/Basic credentials and the values of password, secret, token, key
    and the like (`name=value`, `name: value`, `"name": "value"`); make the text storable."""
    text = _URL_PASSWORD.sub(r"\1***@", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} ***", text)
    text = _SECRET_VALUE.sub(lambda m: m.group("head") + (m.group("q") or "") + "***" + (m.group("end") or ""), text)
    return _storable(text)


def _scrub_value(value):
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {_storable(str(k)): "***" if v is not None and _SECRET_FIELD.fullmatch(str(k)) else _scrub_value(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub(str(value))     # anything JSON can't hold is stored as its (scrubbed) text


def _clean_error(error):
    return scrub(str(error if error is not None else ""))[:ERROR_MAX]


def _clean_key(key):
    return _storable(str(key))[:KEY_MAX]


def _upsert(session, kind, key, *, error, context, n, first, last, set_error=True, set_context=True,
            context_always=False):
    """Insert or add to a row. A late write of an older occurrence never moves last_seen back or replaces
    newer evidence; first_seen keeps the earliest."""
    table = SiteEvent.__table__
    dialect = session.get_bind().dialect.name
    insert = (postgresql if dialect == "postgresql" else sqlite).insert
    values = dict(kind=kind, key=key, error=error if error is not None else "", context=context,
                  count=n, first_seen=first, last_seen=last)
    stmt = insert(table).values(**values)
    ex = stmt.excluded
    newer = ex.last_seen >= table.c.last_seen
    update = {"count": table.c.count + n,
              "first_seen": case((ex.first_seen < table.c.first_seen, ex.first_seen), else_=table.c.first_seen),
              "last_seen": case((newer, ex.last_seen), else_=table.c.last_seen)}
    if set_error:
        update["error"] = case((newer, ex.error), else_=table.c.error)
    if set_context:
        update["context"] = ex.context if context_always else case((newer, ex.context), else_=table.c.context)
    session.execute(stmt.on_conflict_do_update(index_elements=["kind", "key"], set_=update))


def _merge_resolve(entry):
    """Drop an earlier queued resolve of the same kind, key and prefix when no record it would clear came after
    it: the new one clears everything the old one did. Keeps a long outage's queue to its records. Under _lock."""
    match = _match(entry["kind"], entry["key"], entry["prefix"])
    ident = (entry["kind"], entry["key"], entry["prefix"])
    for i in range(len(_queue) - 1, -1, -1):
        e = _queue[i]
        if e["op"] == "record" and match(e["kind"], e["key"]):
            return
        if e["op"] == "resolve" and (e["kind"], e["key"], e["prefix"]) == ident:
            if e["at"] <= entry["at"]:
                del _queue[i]
            return


def _push(entry):
    global _dropped
    with _lock:
        if entry["op"] == "resolve":
            _merge_resolve(entry)
        _queue.append(entry)
        while len(_queue) > QUEUE_MAX:
            _queue.popleft()
            _dropped += 1


def record(kind, key, error, context=None, *, at=None, unless_stored=None):
    """Queue one occurrence for the next flush. Never touches the database, never raises. `unless_stored(stored
    context, this context)` -> True skips the write: it is decided at flush time, against the stored row locked
    in the flush's transaction, so two processes reporting the same thing store it once."""
    if _demo():
        return
    try:
        _push({"op": "record", "kind": kind, "key": _clean_key(key), "error": error, "context": context,
               "at": at or _now(), "unless_stored": unless_stored})
    except Exception:
        logger.exception("site health: could not queue %s %s", kind, key)


enqueue = record


def count(kind, key, context_item=None, *, merge=None, combine=None, at=None):
    """Count one occurrence in memory. The stored context is the last 5 items, or `merge(old, item)` folded
    over the items; `combine(stored, batch)` joins a flush's context with the stored one."""
    if _demo():
        return
    try:
        when = at or _now()
        key = _clean_key(key)
        with _lock:
            c = _counters.get((kind, key))
            if c is None:
                c = _counters[(kind, key)] = {"n": 0, "first": when, "at": when, "items": [], "context": None,
                                              "merge": None, "combine": None}
            c["n"] += 1
            c["first"] = min(c["first"], when)
            if when >= c["at"]:
                c["at"] = when
            if merge is not None:
                c["merge"], c["combine"] = merge, combine
                c["context"] = merge(c["context"], context_item)
            elif context_item is not None:
                c["items"] = (c["items"] + [context_item])[-LAST_ITEMS:]
    except Exception:
        logger.exception("site health: could not count %s %s", kind, key)


def _match(kind, key, prefix):
    return lambda k, ky: k == kind and (ky.startswith(key) if prefix else ky == key)


def _queue_resolve(kind, key, prefix, at):
    if _demo():
        return
    try:
        at = at or _now()
        match = _match(kind, _clean_key(key), prefix)
        with _lock:
            for k in [k for k in _counters if match(*k)]:   # counted before the success: superseded by it
                del _counters[k]
        _push({"op": "resolve", "kind": kind, "key": _clean_key(key), "prefix": prefix, "at": at})
    except Exception:
        logger.exception("site health: could not queue a resolve of %s %s", kind, key)


def resolve(kind, key, *, at=None):
    """The job behind `key` succeeded: the next flush deletes its row (if last seen before now). Queued in order
    with the records, so a failure that happened later stays, and one that happened earlier goes."""
    _queue_resolve(kind, key, False, at)


def resolve_prefix(kind, prefix, *, at=None):
    """`resolve` for every key of the kind that starts with `prefix`."""
    _queue_resolve(kind, prefix, True, at)


def _delete_stmt(kind, key, prefix, at):
    stmt = delete(SiteEvent).where(SiteEvent.kind == kind, SiteEvent.last_seen <= at)
    return stmt.where(SiteEvent.key.startswith(key, autoescape=True) if prefix else SiteEvent.key == key)


def record_now(kind, key, error, context=None, *, session_factory, at=None):
    """Store one occurrence at once, for a process about to exit (the Critical Util child). Never raises: True
    when the write committed (or an applied PR resolution already covers it), False when it failed."""
    if _demo():
        return False
    try:
        when = at or _now()
        key = _clean_key(key)
        with session_factory() as session:
            if not _covered(_cutoffs(session), kind, key, when):
                _upsert(session, kind, key, error=_clean_error(error), context=_scrub_value(context),
                        n=1, first=when, last=when)
            session.commit()
        return True
    except Exception:
        logger.exception("site health: could not record %s %s", kind, key)
        return False


def resolve_now(kind, key, *, session_factory, at=None):
    """`resolve` at once, for a process about to exit. Never raises."""
    if _demo():
        return
    try:
        with session_factory() as session:
            session.execute(_delete_stmt(kind, _clean_key(key), False, at or _now()))
            session.commit()
    except Exception:
        logger.exception("site health: could not resolve %s %s", kind, key)


def _delete_keys(session, kind, keys):
    for i in range(0, len(keys), 200):
        session.execute(delete(SiteEvent).where(SiteEvent.kind == kind, SiteEvent.key.in_(keys[i:i + 200])))


def _retain(session):
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    session.execute(delete(SiteEvent).where(SiteEvent.last_seen < cutoff))
    for kind in session.scalars(select(SiteEvent.kind).distinct()).all():
        extra = session.scalars(select(SiteEvent.key).where(SiteEvent.kind == kind)
                                .order_by(SiteEvent.last_seen.desc(), SiteEvent.key.desc())
                                .offset(KEEP_PER_KIND)).all()
        if extra:
            _delete_keys(session, kind, list(extra))


def _coalesce(queued):
    """One write per key between two resolves of it: records of the same key merge (count, earliest first,
    latest error and context); a resolve drops the records of its keys queued before it."""
    out, last = [], {}
    for e in queued:
        if e["op"] == "record":
            k = (e["kind"], e["key"])
            if e.get("unless_stored") is not None:      # each is decided against the stored row on its own
                last.pop(k, None)
                out.append({**e, "n": 1, "first": e["at"], "last": e["at"]})
                continue
            i = last.get(k)
            if i is None:
                last[k] = len(out)
                out.append({**e, "n": 1, "first": e["at"], "last": e["at"]})
            else:
                o = out[i]
                o["n"] += 1
                o["first"] = min(o["first"], e["at"])
                if e["at"] >= o["last"]:
                    o["last"], o["error"], o["context"] = e["at"], e["error"], e["context"]
        else:
            match = _match(e["kind"], e["key"], e["prefix"])
            for k in [k for k in last if match(*k)]:
                out[last.pop(k)] = None
            out.append(e)
    return [o for o in out if o is not None]


def _cutoffs(session):
    """The applied PR resolutions' (kind, key, cutoff). Every resolution row is share-locked for the transaction:
    a process applying one waits for this write to commit (and then deletes what it wrote), and this read waits for
    one being applied (and then sees its cutoff), so a late write never lands after the deletion it predates."""
    rows = session.execute(select(SiteEventResolution.kind, SiteEventResolution.key, SiteEventResolution.cutoff,
                                  SiteEventResolution.applied_at).with_for_update(read=True)).all()
    return [(kind, key, _aware(cutoff)) for kind, key, cutoff, applied in rows if applied is not None]


def _covered(cutoffs, kind, key, last):
    """An occurrence from before an applied PR resolution's cutoff (a late flush from the old process)."""
    return any(c_kind == kind and (c_key is None or c_key == key) and last < cutoff
               for c_kind, c_key, cutoff in cutoffs)


_INSERTED = object()


def _insert_or_lock(session, kind, key, **values):
    """Insert the row when there is none (returns _INSERTED), else the stored row's columns, locked for this
    transaction: a write that reads the stored row before writing it is then never interleaved with another
    process's (the insert waits on a concurrent insert of the same key, the lock on a concurrent update). None
    when the row went in between."""
    insert = (postgresql if session.get_bind().dialect.name == "postgresql" else sqlite).insert
    done = session.execute(insert(SiteEvent.__table__).values(kind=kind, key=key, **values)
                           .on_conflict_do_nothing(index_elements=["kind", "key"]))
    if done.rowcount:
        return _INSERTED
    return session.execute(select(SiteEvent.context).where(SiteEvent.kind == kind, SiteEvent.key == key)
                           .with_for_update()).first()


def _joined(c, stored, batch):
    if stored is None:
        return batch
    if c["merge"]:
        return c["combine"](stored, batch) if c["combine"] else batch
    return ((stored if isinstance(stored, list) else []) + batch)[-LAST_ITEMS:]


def _write(session, op):
    if op["op"] == "resolve":
        session.execute(_delete_stmt(op["kind"], op["key"], op["prefix"], op["at"]))
    elif op["op"] == "record":
        error, context = _clean_error(op["error"]), _scrub_value(op["context"])
        if op.get("unless_stored") is not None:
            row = _insert_or_lock(session, op["kind"], op["key"], error=error, context=context, count=op["n"],
                                  first_seen=op["first"], last_seen=op["last"])
            if row is _INSERTED or (row is not None and op["unless_stored"](row.context, op["context"])):
                return
        _upsert(session, op["kind"], op["key"], error=error, context=context, n=op["n"], first=op["first"],
                last=op["last"])
    else:
        kind, key, c = op["kind"], op["key"], op["counter"]
        batch = c["context"] if c["merge"] else (list(c["items"]) or None)
        if batch is None:
            _upsert(session, kind, key, error="", context=None, n=c["n"], first=c["first"], last=c["at"],
                    set_error=False, set_context=False)
            return
        # the stored context is joined with this flush's: read it under a lock so two processes' flushes of
        # the same key both land (the slowest load and the last samples)
        row = _insert_or_lock(session, kind, key, error="", context=_scrub_value(batch), count=c["n"],
                              first_seen=c["first"], last_seen=c["at"])
        if row is _INSERTED:
            return
        ctx = _joined(c, row.context if row is not None else None, batch)
        _upsert(session, kind, key, error="", context=_scrub_value(ctx), n=c["n"], first=c["first"],
                last=c["at"], set_error=False, context_always=True)


def _connection_error(exc):
    return isinstance(exc, (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.DisconnectionError)) or \
        bool(getattr(exc, "connection_invalidated", False))


def _flush_ops(session_factory, ops, *, isolate):
    """Write the ops in order, prune, commit. With `isolate`, each op runs in its own savepoint and one the
    database refuses (a value it can't store) is dropped and logged; a lost connection still fails the whole
    flush. Returns how many were dropped."""
    dropped = 0
    with session_factory() as session:
        cutoffs = _cutoffs(session)
        for op in ops:
            last = op.get("last") or (op["counter"]["at"] if op["op"] == "counter" else None)
            if op["op"] != "resolve" and _covered(cutoffs, op["kind"], op["key"], last):
                continue
            if not isolate:
                _write(session, op)
                continue
            try:
                with session.begin_nested():
                    _write(session, op)
            except Exception as exc:
                if _connection_error(exc):
                    raise
                dropped += 1
                logger.error("site health: dropped an entry the database refused (%s %s): %s",
                             op["kind"], op["key"], exc)
        _retain(session)
        session.commit()
    return dropped


def _aware(when):
    return when.replace(tzinfo=timezone.utc) if when is not None and when.tzinfo is None else when


def flush(*, session_factory):
    """Write the queue (in order) and the counters in one transaction, then prune. If the database refuses
    one entry, the rest are written without it; if it can't be reached, everything waits for the next flush."""
    global _dropped_logged
    if _demo():
        return
    with _lock:
        queued = list(_queue)
        counters = dict(_counters)
        _queue.clear()
        _counters.clear()
        dropped, _dropped_logged = _dropped - _dropped_logged, _dropped
    if dropped > 0:
        logger.warning("site health: %s queued entries were dropped (the queue holds %s; oldest go first)",
                       dropped, QUEUE_MAX)
    if not queued and not counters:
        _flush_prune(session_factory)
        return
    ops = _coalesce(queued) + [{"op": "counter", "kind": k, "key": ky, "counter": c} for (k, ky), c in counters.items()]
    try:
        try:
            _flush_ops(session_factory, ops, isolate=False)
        except Exception as exc:
            if _connection_error(exc):
                raise
            _flush_ops(session_factory, ops, isolate=True)
    except Exception:
        logger.exception("site health: flush failed; entries kept for the next flush")
        _requeue(queued, counters)


def flush_for(db):
    """Flush now on `db`'s engine, in a session of its own: for a command-line process (the cache pre-warms), which
    has no flush thread. Never raises."""
    if _demo():
        return
    try:
        from sqlalchemy.orm import sessionmaker

        bind = db.get_bind()
    except Exception:
        logger.exception("site health: no engine to flush on")
        return
    flush(session_factory=sessionmaker(bind=bind))


def _flush_prune(session_factory):
    try:
        with session_factory() as session:
            _retain(session)
            session.commit()
    except Exception:
        logger.exception("site health: pruning failed")


def _requeue(queued, counters):
    """Put a failed flush's entries back, ahead of what was queued meanwhile. A counter a resolve queued meanwhile
    covers is dropped: every count in it came before that success (a resolve deletes its key's counters)."""
    global _dropped
    with _lock:
        since = [_match(e["kind"], e["key"], e["prefix"]) for e in _queue if e["op"] == "resolve"]
        for e in reversed(queued):
            _queue.appendleft(e)
        while len(_queue) > QUEUE_MAX:
            _queue.popleft()
            _dropped += 1
        for k, c in counters.items():
            if any(match(*k) for match in since):
                continue
            cur = _counters.get(k)
            if cur is None:
                _counters[k] = c
                continue
            cur["n"] += c["n"]
            cur["at"] = max(cur["at"], c["at"])
            cur["first"] = min(cur["first"], c["first"])
            cur["items"] = (c["items"] + cur["items"])[-LAST_ITEMS:]
            if cur["merge"] and c["merge"] and cur["combine"] and c["context"] is not None:
                cur["context"] = cur["combine"](c["context"], cur["context"])
            elif cur["context"] is None:
                cur["context"] = c["context"]
            cur["merge"] = cur["merge"] or c["merge"]
            cur["combine"] = cur["combine"] or c["combine"]


def register(name, cycle_s, allowed_s=None):
    with _lock:
        loop = _loops.setdefault(name, {"beats": {}})
        loop["cycle_s"] = cycle_s
        loop["allowed_s"] = allowed_s
        loop["registered"] = _now()
        _loop_threads[name] = threading.current_thread()


def unregister(name):
    with _lock:
        _loops.pop(name, None)
        _loop_threads.pop(name, None)


def join_loops(timeout):
    """Wait up to `timeout` s in all for the registered loops' threads to end (after their stop events are set),
    so what a pass records as it stops reaches the final flush."""
    import time

    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_loop_threads.values())
    for thread in threads:
        if thread is not threading.current_thread() and thread.is_alive():
            thread.join(max(0.0, deadline - time.monotonic()))


def beat(name, phase):
    with _lock:
        loop = _loops.setdefault(name, {"cycle_s": None, "allowed_s": None, "beats": {}})
        loop["beats"][phase] = _now()


def loops():
    with _lock:
        return copy.deepcopy(_loops)


QUIET_FACTOR = 3  # A loop is quiet after this many of its cycles ...
QUIET_SLACK_S = 120  # ... plus this many seconds


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


def loop_failed(name, exc):
    """Queue a loop's (or one of its steps') failure under `name`; call from inside the `except`."""
    lines = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).splitlines()[-15:]
    record("loop_failed", name, f"{type(exc).__name__}: {exc}", {"traceback": lines})


def loop_ok(name):
    resolve("loop_failed", name)


CACHE_FALLBACK_KEYS = frozenset({
    "player_view_cache:invalid", "player_view_cache:decode", "player_view_cache:prewarm",
    "player_page:write_through",
    "site_stats:invalid", "site_stats:write_through",
    "viewer_site_stats:read", "viewer_site_stats:invalid",
    "viewer_site_stats:write_through", "viewer_site_stats:prewarm",
    "account_group_cache:read", "account_group_cache:prewarm",
})

# Kinds stored in site_events: the only ones a resolved-file entry can clear.
RECORDED_KINDS = frozenset({
    "page_error", "loop_failed", "util_build_failed", "cache_fallback", "worker_overrun", "worker_refused",
    "slow_page", "warm_vanished",
})
KNOWN_KINDS = RECORDED_KINDS | frozenset({
    # checked (read live when the card is computed)
    "loop_quiet", "util_build_stale", "unscored_match", "kill_impact_missing", "map_pool_note",
    # replay kinds (app/services/replay_errors.py KINDS)
    "box_conflict", "control_failed", "gaps_failed", "moments_failed", "util_failed", "heights_rejected",
    "heights_out_of_tries", "given_up", "held", "upload_failed", "upload_held", "store_failed", "ack_missing",
    "stuck", "unlinked", "needs_a_person", "feature_failed",
})
GRACE_S = 600  # Grace after the first start that has an entry
RESOLVED_FILE = Path(__file__).resolve().parents[1] / "data" / "site_health_resolved.json"


def load_resolved():
    return json.loads(RESOLVED_FILE.read_text(encoding="utf-8"))


def check_resolved(entries, known=RECORDED_KINDS):
    """The problems in a resolved-file's entries (empty when it is fine). Only stored kinds can be cleared;
    each fix is its own entry: the same kind, key and fixed_by twice is an error."""
    if not isinstance(entries, list):
        return ["the file must hold a list"]
    problems, seen = [], set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f"entry {i}: not an object")
            continue
        if e.get("kind") not in known:
            problems.append(f"entry {i}: {e.get('kind')!r} is not a stored kind a PR can clear")
        if e.get("key") is not None and not isinstance(e.get("key"), str):
            problems.append(f"entry {i}: key must be a string or null")
        if not isinstance(e.get("fixed_by"), str) or not e["fixed_by"].strip():
            problems.append(f"entry {i}: fixed_by must be a non-empty string")
            continue
        ident = entry_id(e)
        if ident in seen:
            problems.append(f"entry {i}: the same kind, key and fixed_by as an earlier entry")
        seen.add(ident)
    return problems


def entry_id(entry):
    """Identity of an entry: its kind, key and fixed_by. A later fix of the same key is a new entry (a new
    fixed_by), so it applies again; entries are never edited once merged."""
    blob = json.dumps({"kind": entry["kind"], "key": entry.get("key"), "fixed_by": entry.get("fixed_by")},
                      sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def apply_resolved(*, session_factory, started_at, entries=None, now=None):
    """Clear the rows the committed file names, once each, GRACE_S after the first start that has the entry
    (rows last seen before that cutoff go). A due entry is claimed in the same transaction that deletes, so two
    overlapping processes never both apply it. Stops querying once every entry is applied. Never raises."""
    if _demo() or _resolved["done"]:
        return
    try:
        entries = load_resolved() if entries is None else entries
        now = now or _now()
        with session_factory() as session:
            if not _resolved["inserted"]:
                insert = (postgresql if session.get_bind().dialect.name == "postgresql" else sqlite).insert
                table = SiteEventResolution.__table__
                for e in entries:
                    if not isinstance(e, dict) or e.get("kind") not in RECORDED_KINDS:
                        logger.warning("site health: skipping a resolved entry that names no stored kind: %r", e)
                        continue
                    session.execute(insert(table).values(
                        entry=entry_id(e), kind=e["kind"], key=e.get("key"),
                        cutoff=started_at + timedelta(seconds=GRACE_S), applied_at=None,
                    ).on_conflict_do_nothing(index_elements=["entry"]))
            due = session.execute(select(SiteEventResolution.entry, SiteEventResolution.kind,
                                         SiteEventResolution.key, SiteEventResolution.cutoff).where(
                SiteEventResolution.applied_at.is_(None), SiteEventResolution.cutoff <= now)).all()
            for entry, kind, key, cutoff in due:
                claimed = session.execute(update(SiteEventResolution).where(
                    SiteEventResolution.entry == entry, SiteEventResolution.applied_at.is_(None)
                ).values(applied_at=now)).rowcount
                if not claimed:
                    continue        # another process applied it first
                stmt = delete(SiteEvent).where(SiteEvent.kind == kind, SiteEvent.last_seen < cutoff)
                if key is not None:
                    stmt = stmt.where(SiteEvent.key == key)
                session.execute(stmt)
            pending = session.scalar(select(func.count()).select_from(SiteEventResolution)
                                     .where(SiteEventResolution.applied_at.is_(None)))
            session.commit()
        _resolved["inserted"] = True
        _resolved["done"] = not pending
    except Exception:
        logger.exception("site health: could not apply the resolved file")


def start(session_factory, started_at=None):
    """Flush every FLUSH_S on a daemon thread, applying the resolved file after each flush when `started_at`
    is given (the first time one cycle after start: an entry's cutoff is GRACE_S later anyway). Returns the stop
    event, or None in demo mode."""
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


def stop(event, timeout=5):
    """Stop the flush thread: it flushes once more and exits; wait up to `timeout` s for it."""
    if event is None:
        return
    event.set()
    thread = _threads.pop(event, None)
    if thread is not None:
        thread.join(timeout=timeout)


def _reset_for_tests():
    global _dropped, _dropped_logged
    with _lock:
        _queue.clear()
        _counters.clear()
        _loops.clear()
        _loop_threads.clear()
        _dropped = _dropped_logged = 0
        _resolved.update(inserted=False, done=False)

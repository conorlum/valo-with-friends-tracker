"""Site health core: what background code reports as failed or slow, stored in `site_events` (one row per
(kind, key)) so the upload page's card can read it. Imports nothing heavy, and opens a session only when the
caller hands in a session factory: there is no default database.

The jobs being watched never wait on the database for it: `record` (alias `enqueue`) and `resolve` /
`resolve_prefix` queue in the order they happen, `count` adds to an in-memory counter, and the flush thread
writes them every FLUSH_S in one transaction (a resolve deletes rows last seen before it), then prunes (30 days,
500 keys per kind). Only a process about to exit (the Critical Util child) writes at once (`record_now`,
`resolve_now`); any other command-line process that records flushes before it exits (`flush_for`; the cache
pre-warms, `scripts/map_pool.py refresh`). Every write holds the writers' advisory lock, and a clear leaves a mark
(`site_event_marks`) that keeps another process's late write of an earlier failure out. Nothing here raises into its caller, and nothing writes in demo mode. The loop registry
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

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects import postgresql, sqlite

from app.config import settings
from app.models.site_events import SiteEvent, SiteEventMark, SiteEventResolution

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

# Every repeat is bounded (a scheme of 32, a user or password of 256), so a long run that never reaches its `@`
# is given up after a fixed look-ahead: linear in the text (R5-A2: unbounded, "a://b:" * N was quadratic).
_URL_PASSWORD = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]{0,31}://[^:/@\s]{0,256}:)[^@\s]{0,256}@")
_SECRET_NAMES = r"password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|key"
# A name may carry any prefix (`db_password`, `RIOT_API_KEY`, `PGPASSWORD`), starting a word; then an optional
# closing quote (JSON/dict keys, backslash-escaped in a repr or log line), = or :, then the value:
#   an escaped-quoted value (\"...\"), to its escaped closing quote or the end of the text;
#   a quoted value (an optional b for bytes), to its closing quote or the end of the text (cut short); a doubled
#   quote ('it''s') is part of it;
#   a bare value.
# Every character of a value has one way to match, so it runs in linear time (R4-1 found exponential backtracking).
_SECRET_VALUE = re.compile(
    rf"""(?P<head>(?<![\w-])[\w-]*?(?:{_SECRET_NAMES})\b\\?["']?\s*[=:]\s*)"""
    r"""(?:\\(?P<eq>["'])(?:[^\\]|\\(?!(?P=eq))[\s\S])*(?P<eend>\\(?P=eq))?"""
    r"""|(?P<b>b)?(?P<q>["'])(?:\\[\s\S]|(?P=q)(?P=q)|(?!(?P=q))[^\\])*(?:(?P<end>(?P=q))|\\?\Z)"""
    r"""|[^&\s;,"'}\]\\]*)""",
    re.IGNORECASE)
# a mapping's field named like a secret (`token`, `db_password`, `X-Api-Key`, `PGPASSWORD`): its value is hidden
_SECRET_FIELD = re.compile(rf"[\w-]*(?:{_SECRET_NAMES})", re.IGNORECASE)
SCRUB_SPAN = 4      # scrub at most this many times the kept length: the cut comes after, so nothing cut is shown
_BEARER = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
KEY_MAX = 300


def _now():
    return datetime.now(timezone.utc)


def _demo():
    return bool(settings.demo_mode)


def _storable(text):
    """Text PostgreSQL will take: no NUL (text and JSONB refuse it) and nothing UTF-8 can't encode."""
    return text.replace("\x00", "\\x00").encode("utf-8", "backslashreplace").decode("utf-8")


def _hide(m):
    if m.group("eq"):
        return m.group("head") + "\\" + m.group("eq") + "***" + (m.group("eend") or "")
    return m.group("head") + (m.group("b") or "") + (m.group("q") or "") + "***" + (m.group("end") or "")


def scrub(text, limit=None):
    """Hide connection-URL passwords, Bearer/Basic credentials and the values of password, secret, token, key
    and the like (`name=value`, `name: value`, `"name": "value"`); make the text storable. With `limit`, the text
    is cut to SCRUB_SPAN times that before scrubbing (bounded work) and to `limit` after."""
    if limit is not None:
        text = text[:limit * SCRUB_SPAN]
    text = _URL_PASSWORD.sub(r"\1***@", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} ***", text)
    text = _SECRET_VALUE.sub(_hide, text)
    text = _storable(text)
    return text if limit is None else text[:limit]


def _scrub_value(value):
    if isinstance(value, str):
        return scrub(value, ERROR_MAX)
    if isinstance(value, dict):
        return {_storable(str(k)): "***" if v is not None and _SECRET_FIELD.fullmatch(str(k)) else _scrub_value(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub(str(value), ERROR_MAX)     # anything JSON can't hold is stored as its (scrubbed) text


def _clean_error(error):
    return scrub(str(error if error is not None else ""), ERROR_MAX)


def _bounded(error):
    """What the queue holds of an error: its text, cut to what the flush will scrub (R5-B3)."""
    return None if error is None else str(error)[:ERROR_MAX * SCRUB_SPAN]


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


def record(kind, key, error, context=None, *, at=None, dedupe=None, refresh=False):
    """Queue one occurrence for the next flush. Never touches the database, never raises. `dedupe(seen, this
    context)` returns the new `seen` to keep, or None when `seen` already counts this occurrence (it is skipped):
    decided at flush time against the key's stored mark, under the writers' lock, so two processes reporting the
    same thing store it once, and a cleared row's mark still knows what it counted. With `refresh`, a skipped
    occurrence still says the condition stands: an open row's last_seen and the mark move forward (no count, no
    new row), so retention never drops a standing alarm, nor the mark that keeps a cleared one cleared (R2-8)."""
    if _demo():
        return
    try:
        _push({"op": "record", "kind": kind, "key": _clean_key(key), "error": _bounded(error), "context": context,
               "at": at or _now(), "dedupe": dedupe, "refresh": refresh})
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


def record_now(kind, key, error, context=None, *, session_factory, at=None, dedupe=None):
    """Store one occurrence at once, for a process about to exit (the Critical Util child), like a flush would
    (the writers' lock, marks, `dedupe`). Never raises: True when the write committed (or a mark already covers
    it), False when it failed."""
    if _demo():
        return False
    try:
        when = at or _now()
        op = {"op": "record", "kind": kind, "key": _clean_key(key), "error": _bounded(error), "context": context,
              "at": when, "n": 1, "first": when, "last": when, "dedupe": dedupe}
        with session_factory() as session:
            _writers_lock(session)
            marks = _Marks(session, [op])
            if not marks.cleared(kind, op["key"], when):
                _write(session, marks, op)
            session.commit()
        return True
    except Exception:
        logger.exception("site health: could not record %s %s", kind, key)
        return False


def resolve_now(kind, key, *, session_factory, at=None):
    """`resolve` at once, for a process about to exit. Never raises: True when the clear committed."""
    if _demo():
        return False
    try:
        op = {"op": "resolve", "kind": kind, "key": _clean_key(key), "prefix": False, "at": at or _now()}
        with session_factory() as session:
            _writers_lock(session)
            _write(session, _Marks(session, [op]), op)
            session.commit()
        return True
    except Exception:
        logger.exception("site health: could not resolve %s %s", kind, key)
        return False


def _delete_keys(session, kind, keys):
    for i in range(0, len(keys), 200):
        session.execute(delete(SiteEvent).where(SiteEvent.kind == kind, SiteEvent.key.in_(keys[i:i + 200])))


def _retain(session):
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    session.execute(delete(SiteEvent).where(SiteEvent.last_seen < cutoff))
    session.execute(delete(SiteEventMark).where(SiteEventMark.updated_at < cutoff))
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
            if e.get("dedupe") is not None:      # each is decided against the stored mark on its own
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


WRITERS_LOCK = 0x5348_0001          # pg_advisory_xact_lock's key for every site health write ("SH" 1)
LOCK_TIMEOUT = "30s"


def _writers_lock(session):
    """Every health write (a flush, record_now, resolve_now, applying a PR entry) runs under one transaction-level
    advisory lock, so the writes of all processes take turns: none can deadlock another (R5-A4, R6-B4), and each
    reads the marks the last one committed before it writes (R6-A1: a PR clear inserted after a writer's read). A
    write that waits longer than LOCK_TIMEOUT fails like a lost connection: its entries wait for the next flush."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITERS_LOCK})


class _Marks:
    """The marks (`site_event_marks`) a write's ops can meet, read under the writers' lock and kept up to date as
    the ops write. A clear leaves `cleared_at`: an occurrence last seen at or before it is never stored again, by
    any process (R5-B1, R6-B1: the old process's late flush brought back what the new one's success cleared). A
    record with `dedupe` keeps its `seen` there, which outlives the row (R5-A5, R6-A3)."""

    def __init__(self, session, ops):
        self.exact, self.prefixes = {}, {}
        keys = {}
        for op in ops:
            keys.setdefault(op["kind"], set()).add(op["key"])
        m = SiteEventMark
        for kind, ks in sorted(keys.items()):
            rows = list(session.scalars(select(m).where(m.kind == kind, m.prefix.is_(True))))
            ks = sorted(ks)
            for i in range(0, len(ks), 500):
                rows += session.scalars(select(m).where(m.kind == kind, m.prefix.is_(False),
                                                        m.key.in_(ks[i:i + 500])))
            for row in rows:
                if row.prefix:
                    self.prefixes.setdefault(kind, {})[row.key] = _aware(row.cleared_at)
                else:
                    self.exact[(kind, row.key)] = {"cleared_at": _aware(row.cleared_at), "seen": row.seen}

    def cleared(self, kind, key, last):
        """An occurrence last seen at `last` that a stored clear already covers."""
        if last is None:
            return False
        at = (self.exact.get((kind, key)) or {}).get("cleared_at")
        if at is not None and last <= at:
            return True
        return any(c is not None and last <= c and key.startswith(p) for p, c in self.prefixes.get(kind, {}).items())

    def seen(self, kind, key):
        return (self.exact.get((kind, key)) or {}).get("seen")

    def clear(self, session, kind, key, prefix, at):
        cur = self.prefixes.get(kind, {}).get(key) if prefix else (self.exact.get((kind, key)) or {}).get("cleared_at")
        at = max(cur, at) if cur is not None else at
        self._put(session, kind, key, prefix, cleared_at=at)
        if prefix:
            self.prefixes.setdefault(kind, {})[key] = at
        else:
            self.exact.setdefault((kind, key), {"cleared_at": None, "seen": None})["cleared_at"] = at

    def set_seen(self, session, kind, key, seen):
        self._put(session, kind, key, False, seen=seen)
        self.exact.setdefault((kind, key), {"cleared_at": None, "seen": None})["seen"] = seen

    @staticmethod
    def _put(session, kind, key, prefix, **values):
        insert = (postgresql if session.get_bind().dialect.name == "postgresql" else sqlite).insert
        now = _now()
        stmt = insert(SiteEventMark.__table__).values(kind=kind, key=key, prefix=prefix, updated_at=now,
                                                      cleared_at=values.get("cleared_at"), seen=values.get("seen"))
        session.execute(stmt.on_conflict_do_update(index_elements=["kind", "key", "prefix"],
                                                   set_={**values, "updated_at": now}))


def _joined(c, stored, batch):
    if stored is None:
        return batch
    if c["merge"]:
        return c["combine"](stored, batch) if c["combine"] else batch
    return ((stored if isinstance(stored, list) else []) + batch)[-LAST_ITEMS:]


def _write(session, marks, op):
    if op["op"] == "resolve":
        session.execute(_delete_stmt(op["kind"], op["key"], op["prefix"], op["at"]))
        marks.clear(session, op["kind"], op["key"], op["prefix"], op["at"])
    elif op["op"] == "record":
        if op.get("dedupe") is not None:
            stored_seen = marks.seen(op["kind"], op["key"])
            seen = op["dedupe"](stored_seen, op["context"])
            if seen is None:
                if op.get("refresh"):
                    marks.set_seen(session, op["kind"], op["key"], stored_seen)
                    t = SiteEvent.__table__
                    session.execute(t.update().where(t.c.kind == op["kind"], t.c.key == op["key"],
                                                     t.c.last_seen < op["last"]).values(last_seen=op["last"]))
                return
            marks.set_seen(session, op["kind"], op["key"], seen)
        _upsert(session, op["kind"], op["key"], error=_clean_error(op["error"]), context=_scrub_value(op["context"]),
                n=op["n"], first=op["first"], last=op["last"])
    else:
        kind, key, c = op["kind"], op["key"], op["counter"]
        batch = c["context"] if c["merge"] else (list(c["items"]) or None)
        if batch is None:
            _upsert(session, kind, key, error="", context=None, n=c["n"], first=c["first"], last=c["at"],
                    set_error=False, set_context=False)
            return
        # the stored context is joined with this flush's (the slowest load, the last samples): read under the
        # writers' lock, so another process's flush of the same key can't land in between
        stored = session.execute(select(SiteEvent.context).where(SiteEvent.kind == kind, SiteEvent.key == key)).first()
        ctx = _joined(c, stored.context if stored is not None else None, batch)
        _upsert(session, kind, key, error="", context=_scrub_value(ctx), n=c["n"], first=c["first"],
                last=c["at"], set_error=False, context_always=True)


def _connection_error(exc):
    return isinstance(exc, (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.DisconnectionError)) or \
        bool(getattr(exc, "connection_invalidated", False))


def _flush_ops(session_factory, ops, *, isolate):
    """Write the ops in order, prune, commit, under the writers' lock. With `isolate`, each op runs in its own
    savepoint and one the database refuses (a value it can't store) is dropped and logged; a lost connection (or
    the lock not given in time) still fails the whole flush. Returns how many were dropped."""
    dropped = 0
    with session_factory() as session:
        _writers_lock(session)
        marks = _Marks(session, ops)
        for op in ops:
            last = op.get("last") or (op["counter"]["at"] if op["op"] == "counter" else None)
            if op["op"] != "resolve" and marks.cleared(op["kind"], op["key"], last):
                continue
            if not isolate:
                _write(session, marks, op)
                continue
            try:
                with session.begin_nested():
                    _write(session, marks, op)
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
            _writers_lock(session)
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
    "slow_page", "warm_vanished", "map_layout_walls",
})
KNOWN_KINDS = RECORDED_KINDS | frozenset({
    # checked (read live when the card is computed)
    "loop_quiet", "util_build_stale", "unscored_match", "kill_impact_missing", "map_pool_note",
    # replay kinds (app/services/replay_errors.py KINDS)
    "box_conflict", "control_unsupported", "control_failed", "gaps_failed", "moments_failed", "util_failed", "heights_rejected",
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
    overlapping processes never both apply it, under the writers' lock; it leaves a mark at the cutoff, so a late
    write from before it is never stored again. Stops querying once every entry is applied. Never raises."""
    if _demo() or _resolved["done"]:
        return
    try:
        entries = load_resolved() if entries is None else entries
        now = now or _now()
        with session_factory() as session:
            _writers_lock(session)
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
                SiteEventResolution.applied_at.is_(None), SiteEventResolution.cutoff <= now)
                .order_by(SiteEventResolution.entry)).all()
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
                mark = {"kind": kind, "key": key if key is not None else ""}
                _Marks(session, [mark]).clear(session, kind, mark["key"], key is None, _aware(cutoff))
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

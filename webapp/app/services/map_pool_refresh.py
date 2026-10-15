"""Daily check of playvalorant.com's patch notes for Competitive map pool changes.

`check` lists the patch-notes pages newer than the committed seed, reads each unread one (newest first, at most
`MAX_PAGES` per check) and stores its pool changes in `map_pool_changes`; every page looked at gets a
`map_pool_patch_notes` row so a read page is never fetched again. A page that mentions the pool but parses to
nothing, or whose IN and OUT lines don't balance (a "miss"), stores no change, stays unread and is retried, and
is given up on after `GIVE_UP_AFTER` since its publication. Shutdown (`stop`) is checked before each page's
fetch and before its commit, so nothing is written after it.
Off unless MAP_POOL_AUTO is set, and always off in demo mode.
"""

import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select, update

from app.config import settings
from app.models.map_pool import MapPoolChange, MapPoolPatchNotes
from app.services import map_pool
from app.services.patch_notes import parse_listing, parse_patch_notes

log = logging.getLogger(__name__)

LISTING_URL = "https://playvalorant.com/en-us/news/game-updates/"
USER_AGENT = "valo-with-friends-tracker map-pool refresh (+https://valowithfriendstracker.onrender.com)"
MAX_PAGES = 5
GIVE_UP_AFTER = timedelta(days=7)
GIVEN_UP = "given up: parser miss"
MIN_GAP_S = 24 * 3600

_last_check: float | None = None  # time.monotonic() of this process's last check


def enabled() -> bool:
    return bool(settings.map_pool_auto and not settings.demo_mode)


def http_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _patch_label(title: str, url: str) -> str:
    m = re.search(r"patch notes\s+(\d+\.\d+)", title, re.I)
    if m:
        return m.group(1)
    m = re.search(r"valorant-patch-notes-(\d+)-(\d+)", url)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return "unknown"


def _insert(bind):
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


class Stopped(Exception):
    """Shutdown arrived mid-page: nothing of that page is committed."""


def _store_page(session, item, notes, effective: datetime, patch: str, now: datetime,
                stop: threading.Event | None = None) -> tuple[int, bool]:
    """One transaction for one page. Returns (changes stored, was a miss). A page whose IN and OUT lines
    don't balance is a miss too: it was most likely read in part, and storing half a swap would keep a map
    in the pool for good (the safety net only ever adds maps)."""
    insert = _insert(session.get_bind())
    miss = notes.is_miss or not notes.is_balanced
    for ch in () if miss else notes.changes:
        session.execute(
            insert(MapPoolChange)
            .values(map_name=ch.map_name, in_pool=ch.in_pool, effective_at=effective, patch=patch,
                    source="patch_notes", source_url=item.url)
            .on_conflict_do_nothing()
        )
    session.execute(
        insert(MapPoolPatchNotes).values(url=item.url, patch=patch, published_at=effective).on_conflict_do_nothing()
    )
    tbl = MapPoolPatchNotes
    if not miss:
        values = {"read_at": now, "change_count": len(notes.changes), "miss_count": tbl.miss_count,
                  "last_error": None}
    else:
        gave_up = now - effective > GIVE_UP_AFTER
        values = {"miss_count": tbl.miss_count + 1, "last_error": GIVEN_UP if gave_up else "parser miss"}
    session.execute(update(tbl).where(tbl.url == item.url).values(**values))
    if stop is not None and stop.is_set():
        raise Stopped
    session.commit()
    return (0 if miss else len(notes.changes)), miss


def check(session_factory, fetch: Callable[[str], str], now: datetime | None = None,
          stop: threading.Event | None = None) -> dict:
    """`stop` (the thread's shutdown event) is checked before each page's fetch and before its commit."""
    counts = dict(listed=0, new=0, read=0, changes=0, misses=0, given_up=0, errors=0)
    now = _utc(now) if now is not None else datetime.now(timezone.utc)
    try:
        items = parse_listing(fetch(LISTING_URL))
    except Exception:  # noqa: BLE001
        log.exception("map pool refresh: the listing could not be fetched")
        counts["errors"] += 1
        return counts
    counts["listed"] = len(items)
    try:
        seed_newest = map_pool.seed_newest_published_at()
        fresh = [i for i in items if i.published_at is not None
                 and (seed_newest is None or _utc(i.published_at) > seed_newest)]
        session = session_factory()
        try:
            rows = {}
            if fresh:
                rows = {r.url: r for r in session.execute(
                    select(MapPoolPatchNotes).where(MapPoolPatchNotes.url.in_([i.url for i in fresh]))
                ).scalars()}
            todo = []
            for i in fresh:
                row = rows.get(i.url)
                if row is not None and row.read_at is not None:
                    continue
                if row is not None and (row.last_error or "").startswith("given up"):
                    continue
                if row is None:
                    counts["new"] += 1
                todo.append(i)
            todo.sort(key=lambda i: _utc(i.published_at), reverse=True)
            for item in todo[:MAX_PAGES]:
                if stop is not None and stop.is_set():
                    break
                try:
                    notes = parse_patch_notes(fetch(item.url))
                    effective = _utc(notes.published_at or item.published_at)
                    patch = _patch_label(item.title, item.url)
                    stored, miss = _store_page(session, item, notes, effective, patch, now, stop)
                except Stopped:
                    session.rollback()
                    break
                except Exception:  # noqa: BLE001
                    session.rollback()
                    log.exception("map pool refresh: %s failed", item.url)
                    counts["errors"] += 1
                    continue
                if miss:
                    counts["misses"] += 1
                    if now - effective > GIVE_UP_AFTER:
                        counts["given_up"] += 1
                        log.warning("map pool refresh: giving up on %s (parser miss)", item.url)
                    else:
                        log.warning("map pool refresh: parser miss on %s", item.url)
                else:
                    counts["read"] += 1
                    counts["changes"] += stored
                if stored:
                    map_pool.clear_pool_cache()
        finally:
            session.rollback()
            session.close()
    except Exception:  # noqa: BLE001
        log.exception("map pool refresh failed")
        counts["errors"] += 1
    return counts


def run_forever(session_factory, fetch, stop: threading.Event, every_s: float = 86400,
                first_delay_s: float = 60) -> None:
    global _last_check
    if stop.wait(first_delay_s):
        return
    while True:
        if _last_check is not None and time.monotonic() - _last_check < MIN_GAP_S:
            pass
        else:
            _last_check = time.monotonic()
            try:
                counts = check(session_factory, fetch, stop=stop)
                log.info("map pool refresh: %s", ", ".join(f"{k} {v}" for k, v in counts.items()))
            except Exception:  # noqa: BLE001
                log.exception("map pool refresh failed")
        if stop.wait(every_s):
            return


def start(session_factory) -> threading.Event | None:
    """Starts the daily check when enabled; returns its stop event."""
    if not enabled():
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, http_fetch, stop), name="map-pool-refresh",
                     daemon=True).start()
    log.info("map pool refresh on: daily")
    return stop

"""Fill and refresh the replay-search moment index on the site (PLAN D8, R8, R10).

Every 120 s, after a 90 s first delay, take up to 10 replays whose source key changed or that have no
index (newest first), 0.2 s apart. It doubles as the backfill and rebuilds everything after an
`INDEX_REVISION` bump. A failed build cools down in memory for six hours and never fails the pass.
Unlinked replays' rows are dropped once per pass. Deploy drain pauses new work. Never runs in demo mode.
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import settings
from app.services import deploy_drain, replay_search_index, site_health

log = logging.getLogger(__name__)

LOOP_NAME = "search-index"
CYCLE_S = 120
FIRST_DELAY_S = 90
PER_PASS = 10
PAUSE_S = 0.2
RETRY_AFTER_S = 6 * 3600


def run_pass(session_factory, stop, failed_until: dict[int, float], now: float, *,
             select=None, rebuild=None, drop=None, paused=None, limit=PER_PASS, pause_s=PAUSE_S) -> dict[str, int]:
    """Drop unlinked rows and select once, release that session, then rebuild each replay independently."""
    paused = deploy_drain.paused_now if paused is None else paused
    if paused():
        return {"paused": 1}
    select = replay_search_index.pending if select is None else select
    rebuild = replay_search_index.rebuild if rebuild is None else rebuild
    drop = replay_search_index.drop_unlinked if drop is None else drop
    for replay_id, until in list(failed_until.items()):
        if until <= now:
            del failed_until[replay_id]

    db = session_factory()
    try:
        try:
            if drop(db):
                log.info("search-index sweep: dropped rows of unlinked replays")
            db.commit()
        except Exception:  # noqa: BLE001 - a failed drop must not stop the rebuilds
            log.exception("search-index sweep: drop_unlinked failed")
            db.rollback()
        replay_ids = select(db, limit, skip=set(failed_until))
    finally:
        try:
            db.rollback()
        finally:
            db.close()

    counts: dict[str, int] = {}
    for index, replay_id in enumerate(replay_ids):
        if stop.is_set():
            break
        if paused():
            counts["paused"] = 1
            break
        status = rebuild(session_factory, replay_id)
        if status.startswith("failed"):
            failed_until[replay_id] = now + RETRY_AFTER_S
        key = status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        if index < len(replay_ids) - 1 and stop.wait(pause_s):
            break
    return counts


def run_forever(session_factory, stop, every_s=CYCLE_S, first_delay_s=FIRST_DELAY_S):
    """Keep cooldowns across passes; report failures without killing the loop."""
    site_health.register(LOOP_NAME, every_s)
    failed_until: dict[int, float] = {}
    try:
        if stop.wait(first_delay_s):
            return
        while True:
            site_health.beat(LOOP_NAME, "start")
            try:
                counts = run_pass(session_factory, stop, failed_until, time.monotonic())
                if any(counts.values()):
                    log.info("search-index sweep: %s", counts)
            except Exception as exc:  # noqa: BLE001 - a failed pass must never take the site down
                log.exception("search-index sweep failed")
                site_health.loop_failed(LOOP_NAME, exc)
            else:
                site_health.loop_ok(LOOP_NAME)
            site_health.beat(LOOP_NAME, "end")
            if stop.wait(every_s):
                return
    finally:
        if stop.is_set():  # a thread that died on its own stays registered, so it shows as quiet
            site_health.unregister(LOOP_NAME)


def start(session_factory) -> threading.Event | None:
    """Start the site's sweep outside demo mode and return its shutdown signal."""
    if settings.demo_mode:
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, stop), name=LOOP_NAME, daemon=True).start()
    log.info("search-index sweep on: every %ss after %ss", CYCLE_S, FIRST_DELAY_S)
    return stop

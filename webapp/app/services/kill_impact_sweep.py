"""Fill missing per-kill Impact for linked, scored replays on the site.

PLAN D1-D6: the site computes the split because the Workflow worker has no database
code and the scorer reads a session and frozen models. Sweep existing links as well
as new ones, leaving store/upload paths scorer-free. The scorer uses the active
manifest's byte-identical committed mirror when docs are absent on Render. Start
after 60 s, then take up to five newest replays every 30 s with 0.5 s between splits;
failures cool down in memory for six hours. Never run in demo mode.

This is the site's only caller of the scorer, through replay_impact's read-only
split refresh in its own session and advisory lock. Deploy drain pauses new work.
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import select

from app.config import settings
from app.models.replay import Replay
from app.services import deploy_drain, replay_impact, site_health, site_health_checks

log = logging.getLogger(__name__)

LOOP_NAME = "kill-impact"
CYCLE_S = 30
FIRST_DELAY_S = 60
PER_PASS = 5
PAUSE_S = 0.5
RETRY_AFTER_S = 6 * 3600


def pending_replay_ids(db, limit: int, skip: set[int]) -> list[int]:
    """Newest linked, scored replays whose split is missing and not cooling down."""
    query = select(Replay.id).where(
        Replay.link_status == "linked", Replay.match_id.is_not(None),
        Replay.kill_impact.is_(None), site_health_checks._scored(Replay.match_id),
    )
    if skip:
        query = query.where(Replay.id.not_in(skip))
    return list(db.scalars(query.order_by(Replay.id.desc()).limit(limit)))


def run_pass(session_factory, stop, failed_until: dict[int, float], now: float, *,
             select=None, refresh=None, paused=None, limit=PER_PASS, pause_s=PAUSE_S) -> dict[str, int]:
    """Select once, release the selection session, then refresh each split independently."""
    paused = deploy_drain.paused_now if paused is None else paused
    if paused():
        return {"paused": 1}
    select = pending_replay_ids if select is None else select
    refresh = replay_impact.refresh_replay_impact if refresh is None else refresh
    for replay_id, until in list(failed_until.items()):
        if until <= now:
            del failed_until[replay_id]

    db = session_factory()
    try:
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
        status = refresh(session_factory, replay_id)
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
                    log.info("kill-impact sweep: %s", counts)
            except Exception as exc:  # noqa: BLE001 - a failed pass must never take the site down
                log.exception("kill-impact sweep failed")
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
    log.info("kill-impact sweep on: every %ss after %ss", CYCLE_S, FIRST_DELAY_S)
    return stop

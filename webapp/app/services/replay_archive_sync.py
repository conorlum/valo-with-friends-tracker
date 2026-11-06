"""Keeps the worker's .vrf archive in step with the web app (docs/superpowers/specs/2026-10-01-control-heights-
design.md, part 1). The worker holds no secret and never calls the web app, so the web app pushes: a daemon
thread runs `cycle` every CYCLE_S while uploads are enabled.

1. `GET /health`. The worker unreachable: nothing to do.
1a. Collect every finished upload whose page was closed (`replay_upload.collect_unfinished`), whether or not
   the archive is on. The archive off: nothing more to do. An automatic re-parse attempt is collected there
   through its own guards (`replay_upload.collect_auto`): this thread can bind and store an accepted one, and
   never starts, resends or closes one.
2. A `boot_id` this process hasn't synced (a first run, a restart or deploy, a restored disk snapshot): push the
   full `replay_deletions` list. The worker deletes any archived or pending file of a listed match, so a
   restored snapshot can't bring back a deleted recording for longer than one cycle.
3. Re-send every ack still unanswered (`archive_ack` null) for uploads collected within the worker's pending
   TTL, built from what the DB holds now (`replay_upload.send_ack`).

Best effort throughout: a failure is logged and the next cycle tries again.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_

from app.config import settings
from app.models.replay import ReplayDeletion, ReplayUpload
from app.services import replay_upload as uploads
from app.services import site_health

log = logging.getLogger(__name__)

CYCLE_S = 300
LOOP_NAME = "archive-sync"
WORKER_KEY = "archive_sync:worker"
WORKER_FAILS_BEFORE_RECORD = 3  # PROVISIONAL(D1)
RESEND_WITHIN = timedelta(days=2)  # the worker's PENDING_TTL_S: after it, nothing is held to ack


@dataclass
class State:
    synced_boot_id: str | None = None
    worker_error: str | None = None   # the last cycle's worker error, if any
    worker_fails: int = 0             # consecutive cycles that had one


def enabled() -> bool:
    return uploads.upload_enabled()


def cycle(session_factory, client: uploads.WorkerClient, state: State, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    counts = {"collected": 0, "tombstones_pushed": 0, "acks_sent": 0, "acks_unsent": 0}
    state.worker_error = None
    try:
        archive = client.health().get("archive") or {}
    except uploads.WorkerError as exc:
        state.worker_error = f"WorkerError: {exc}"
        return counts
    try:
        counts["collected"] = uploads.collect_unfinished(session_factory, client, now)
    except uploads.WorkerError as exc:
        state.worker_error = f"WorkerError: {exc}"
        log.warning("archive sync: the worker stopped answering while collecting uploads")
    if not archive.get("enabled"):
        return counts
    session = session_factory()
    try:
        if archive.get("boot_id") and archive["boot_id"] != state.synced_boot_id:
            listed = [row.match_uuid for row in session.query(ReplayDeletion)]
            client.tombstones(listed)
            state.synced_boot_id = archive["boot_id"]
            counts["tombstones_pushed"] = len(listed)
        unanswered = (session.query(ReplayUpload)
                      .filter(ReplayUpload.archive_ack.is_(None), ReplayUpload.worker_job_id.isnot(None), or_(
                          and_(ReplayUpload.store_outcome.isnot(None),
                               ReplayUpload.finished_at >= now - RESEND_WITHIN),
                          # A row stored before `store_outcome` was tracked (2026-09-28 and earlier): outside
                          # the resend window by now, but still worth one recovery attempt (replay_upload.ack_body).
                          and_(ReplayUpload.store_outcome.is_(None), ReplayUpload.status == "stored")))
                      .order_by(ReplayUpload.finished_at).all())
        for upload in unanswered:
            if uploads.send_ack(session, upload, client) is None:
                counts["acks_unsent"] += 1
            else:
                counts["acks_sent"] += 1
        return counts
    except uploads.WorkerError as exc:
        state.worker_error = f"WorkerError: {exc}"
        log.warning("archive sync: the worker stopped answering mid-cycle")
        return counts
    finally:
        session.rollback()
        session.close()


def run_forever(session_factory, client, stop: threading.Event, every_s: float = CYCLE_S) -> None:
    state = State()
    site_health.register(LOOP_NAME, every_s)
    try:
        while True:
            site_health.beat(LOOP_NAME, "start")
            try:
                counts = cycle(session_factory, client, state)
                if any(counts.values()):
                    log.info("archive sync: %s", ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if v))
                site_health.loop_ok(LOOP_NAME, session_factory=session_factory)
                _note_worker(session_factory, state)
            except Exception as exc:  # noqa: BLE001 - the sync must never take the site down
                log.exception("archive sync failed")
                site_health.loop_failed(LOOP_NAME, exc, session_factory=session_factory)
            site_health.beat(LOOP_NAME, "end")
            if stop.wait(every_s):
                return
    finally:
        site_health.unregister(LOOP_NAME)


def _note_worker(session_factory, state: State) -> None:
    """A worker that stays away for WORKER_FAILS_BEFORE_RECORD cycles in a row is recorded; one good cycle clears it."""
    if state.worker_error is None:
        state.worker_fails = 0
        site_health.resolve("loop_failed", WORKER_KEY, session_factory=session_factory)
        return
    state.worker_fails += 1
    if state.worker_fails >= WORKER_FAILS_BEFORE_RECORD:
        site_health.record("loop_failed", WORKER_KEY, state.worker_error,
                           {"consecutive_cycles": state.worker_fails}, session_factory=session_factory)


def start(session_factory) -> threading.Event | None:
    """Starts the sync thread when uploads are on; returns its stop event."""
    if not enabled():
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, uploads.WorkerClient(settings.replay_worker_url),
                                               stop), name="archive-sync", daemon=True).start()
    log.info("archive sync on: every %ss", CYCLE_S)
    return stop

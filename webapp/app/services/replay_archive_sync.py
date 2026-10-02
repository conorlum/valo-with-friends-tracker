"""Keeps the worker's .vrf archive in step with the web app (docs/superpowers/specs/2026-10-01-control-heights-
design.md, part 1). The worker holds no secret and never calls the web app, so the web app pushes: a daemon
thread runs `cycle` every CYCLE_S while uploads are enabled.

1. `GET /health`. The worker unreachable: nothing to do.
1a. Collect every finished upload whose page was closed (`replay_upload.collect_unfinished`), whether or not
   the archive is on. The archive off: nothing more to do.
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

from app.config import settings
from app.models.replay import ReplayDeletion, ReplayUpload
from app.services import replay_upload as uploads

log = logging.getLogger(__name__)

CYCLE_S = 300
RESEND_WITHIN = timedelta(days=2)  # the worker's PENDING_TTL_S: after it, nothing is held to ack


@dataclass
class State:
    synced_boot_id: str | None = None


def enabled() -> bool:
    return uploads.upload_enabled()


def cycle(session_factory, client: uploads.WorkerClient, state: State, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    counts = {"collected": 0, "tombstones_pushed": 0, "acks_sent": 0, "acks_unsent": 0}
    try:
        archive = client.health().get("archive") or {}
    except uploads.WorkerError:
        return counts
    try:
        counts["collected"] = uploads.collect_unfinished(session_factory, client, now)
    except uploads.WorkerError:
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
                      .filter(ReplayUpload.archive_ack.is_(None), ReplayUpload.store_outcome.isnot(None),
                              ReplayUpload.worker_job_id.isnot(None),
                              ReplayUpload.finished_at >= now - RESEND_WITHIN)
                      .order_by(ReplayUpload.finished_at).all())
        for upload in unanswered:
            if uploads.send_ack(session, upload, client) is None:
                counts["acks_unsent"] += 1
            else:
                counts["acks_sent"] += 1
        return counts
    except uploads.WorkerError:
        log.warning("archive sync: the worker stopped answering mid-cycle")
        return counts
    finally:
        session.rollback()
        session.close()


def run_forever(session_factory, client, stop: threading.Event, every_s: float = CYCLE_S) -> None:
    state = State()
    while True:
        try:
            counts = cycle(session_factory, client, state)
            if any(counts.values()):
                log.info("archive sync: %s", ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if v))
        except Exception:  # noqa: BLE001 - the sync must never take the site down
            log.exception("archive sync failed")
        if stop.wait(every_s):
            return


def start(session_factory) -> threading.Event | None:
    """Starts the sync thread when uploads are on; returns its stop event."""
    if not enabled():
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, uploads.WorkerClient(settings.replay_worker_url),
                                               stop), name="archive-sync", daemon=True).start()
    log.info("archive sync on: every %ss", CYCLE_S)
    return stop

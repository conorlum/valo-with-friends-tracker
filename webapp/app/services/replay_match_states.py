"""The stored per-match state (table `replay_match_states`, migration 0024): what `replay_progress.compute` says of
each match with a replay, written by `refresh` (R4: the same done, total and first stuck step, from the same
computation, never the dispatcher's own plans).

`refresh` may raise; whoever calls it from a thread or a page must not let that propagate."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text, update

from app.config import settings
from app.models.replay import ReplayMatchState
from app.services import replay_progress

log = logging.getLogger(__name__)

REFRESH_S = 60
STALE_S = 5 * REFRESH_S
LOCK_ID = 7_346_120_231   # distinct from replay_control_remote.LOCK_ID (7_346_120_117)


def _counts(**found) -> dict:
    return {"written": 0, "deleted": 0, "unchanged": 0, "locked_out": 0, "seconds": 0, **found}


def refresh(db, now: datetime | None = None) -> dict:
    """Bring the table to `compute(db)`'s answer in one transaction; then hand that answer to the panel's cache."""
    if settings.demo_mode:
        return _counts()
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    try:
        if db.get_bind().dialect.name == "postgresql":
            if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}).scalar():
                db.rollback()
                return _counts(locked_out=1)
        result = replay_progress.compute(db)
        judged = result["judged_against"]
        existing = {str(r.match_uuid).lower(): r for r in db.query(ReplayMatchState)}
        written = unchanged = 0
        for uuid, match in result["matches"].items():
            values = {"done": match["done"], "total": match["total"], "first_step": match["first"],
                      "recipe": judged["recipe"], "control_revision": judged["control_revision"],
                      "gaps_revision": judged["gaps_revision"]}
            row = existing.get(uuid)
            if row is None:
                db.add(ReplayMatchState(match_uuid=uuid, checked_at=now, **values))
                written += 1
            elif any(getattr(row, k) != v for k, v in values.items()):
                for k, v in values.items():
                    setattr(row, k, v)
                written += 1
            else:
                unchanged += 1
        gone = [row for uuid, row in existing.items() if uuid not in result["matches"]]
        for row in gone:
            db.delete(row)
        db.flush()
        db.execute(update(ReplayMatchState).values(checked_at=now))
        db.commit()
    except Exception:
        db.rollback()
        raise
    replay_progress.seed(result, started)
    return _counts(written=written, deleted=len(gone), unchanged=unchanged,
                   seconds=round(time.monotonic() - started, 2))


def run_forever(session_factory, stop: threading.Event, every_s: float = REFRESH_S) -> None:
    """One pass at once, then one every `every_s` until `stop` is set. A failed pass is logged, never fatal."""
    while True:
        db = session_factory()
        try:
            counts = refresh(db)
            log.info("replay states: written %d, deleted %d, unchanged %d in %.2fs",
                     counts["written"], counts["deleted"], counts["unchanged"], counts["seconds"])
        except Exception:  # noqa: BLE001 - the refresher must never take the site down
            log.exception("replay states refresh failed")
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()
        if stop.wait(every_s):
            return


def start(session_factory) -> threading.Event | None:
    """Starts the refresher thread (not in demo mode; no other switch, R5); returns its stop event."""
    if settings.demo_mode:
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, stop), name="replay-states", daemon=True).start()
    log.info("replay states refresher on: every %ss", REFRESH_S)
    return stop

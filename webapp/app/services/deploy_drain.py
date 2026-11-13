"""Deploy drain transactions and the shared site/worker readiness contract.

Writers own their transaction and commit it before returning (or refusing an
expired owner). Reads use fresh database state; an expired open row is closed
under the same writer lock when active() first notices it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models.deploy_drain import DeployDrain


log = logging.getLogger(__name__)
DRAIN_LOCK = 7_346_120_134
QUIET_UPLOAD_S = 60
EXPIRY = timedelta(hours=2)
LIMIT_S = 3600


class NotOwner(Exception):
    """The run does not own a currently active drain (HTTP 409 at the boundary)."""


def _utc(value: datetime) -> datetime:
    # SQLite drops DateTime(timezone=True)'s zone; persisted times are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DRAIN_LOCK})


def _open(db: Session) -> DeployDrain | None:
    return db.scalars(select(DeployDrain).where(DeployDrain.ended_at.is_(None))
                      .execution_options(populate_existing=True)).one_or_none()


def _expire(db: Session, row: DeployDrain | None, now: datetime) -> DeployDrain | None:
    """Called under DRAIN_LOCK; flush closure before a replacement can be inserted."""
    if row is not None and _utc(row.expires_at) <= _utc(now):
        row.ended_at = row.expires_at
        row.outcome = "expired"
        db.flush()
        return None
    return row


def _owner(db: Session, run_id: str, now: datetime) -> DeployDrain:
    row = _expire(db, _open(db), now)
    if row is None or row.run_id != run_id:
        # Expiry must survive even when the requested owner action is refused.
        db.commit()
        raise NotOwner("run does not own an active deploy drain")
    return row


def request(db: Session, run_id: str, sha: str, now: datetime, *, skip_drain: bool = False) -> dict:
    _lock(db)
    row = _expire(db, _open(db), now)
    superseded = None
    if row is not None and row.run_id == run_id:
        # A timed-out request can be retried in either phase. Never supersede
        # the owner itself or move the dispatcher's required cycle timestamp.
        result = "started"
        row.expires_at = now + EXPIRY
    elif row is not None and row.phase == "deploying":
        result = "busy"
    else:
        if row is None:
            row = DeployDrain(run_id=run_id, sha=sha, started_at=now)
            db.add(row)
            result = "started"
        else:
            superseded = row.run_id
            row.run_id, row.sha, row.superseded = run_id, sha, superseded
            result = "took_over"
        row.phase = "deploying" if skip_drain else "draining"
        row.skip_drain = skip_drain
        row.requested_at = now
        row.expires_at = now + EXPIRY
    db.commit()
    return {"result": result, "drain": row, "superseded": superseded}


def owner_poll(db: Session, run_id: str, now: datetime, waiting_total: int) -> DeployDrain:
    _lock(db)
    row = _owner(db, run_id, now)
    row.expires_at = now + EXPIRY
    row.waiting_total = waiting_total
    db.commit()
    return row


def mark_deploying(db: Session, run_id: str, now: datetime) -> DeployDrain:
    _lock(db)
    row = _owner(db, run_id, now)
    row.phase = "deploying"
    db.commit()
    return row


def mark_forced(db: Session, run_id: str) -> DeployDrain:
    _lock(db)
    row = _owner(db, run_id, datetime.now(timezone.utc))
    row.forced = True
    db.commit()
    return row


def finish(db: Session, run_id: str, outcome: str, now: datetime) -> DeployDrain:
    _lock(db)
    row = _owner(db, run_id, now)
    if outcome not in {"ok", "failed", "dry_run", "expired"}:
        raise ValueError("invalid deploy drain outcome")
    row.outcome = outcome
    row.ended_at = row.expires_at if outcome == "expired" else now
    row.commit = row.sha if outcome in {"ok", "failed"} else None
    db.commit()
    return row


def active(db: Session, now: datetime) -> DeployDrain | None:
    row = _open(db)
    if row is None or _utc(row.expires_at) > _utc(now):
        return row
    _lock(db)
    row = _expire(db, _open(db), now)
    db.commit()
    return row


def paused(db: Session, now: datetime) -> bool:
    try:
        return active(db, now) is not None
    except Exception as error:
        # Do not log connection strings or exception text from a failed DB call.
        log.warning("deploy drain state unavailable (%s)", type(error).__name__)
        return False


def paused_now() -> bool:
    """Uncached admission boundary for callers without a database session."""
    from app.db import SessionLocal

    try:
        with SessionLocal() as db:
            return paused(db, datetime.now(timezone.utc))
    except Exception as error:
        log.warning("deploy drain state unavailable (%s)", type(error).__name__)
        return False


def last_outcome(db: Session) -> DeployDrain | None:
    return db.scalars(select(DeployDrain).where(DeployDrain.ended_at.is_not(None),
                      DeployDrain.outcome.in_(("ok", "failed")))
                      .order_by(DeployDrain.ended_at.desc(), DeployDrain.id.desc())
                      .limit(1).execution_options(populate_existing=True)).first()


def drained(db: Session, start: datetime, end: datetime, now: datetime) -> timedelta:
    start, end, now = _utc(start), _utc(end), _utc(now)
    end = min(end, now)
    if end <= start:
        return timedelta(0)
    rows = db.scalars(select(DeployDrain).where(DeployDrain.started_at < end,
                     or_(DeployDrain.ended_at.is_(None), DeployDrain.ended_at > start))
                     .execution_options(populate_existing=True))
    total = timedelta(0)
    for row in rows:
        stopped = _utc(row.ended_at) if row.ended_at else min(now, _utc(row.expires_at))
        total += max(timedelta(0), min(end, stopped) - max(start, _utc(row.started_at)))
    return total


def to_json(drain: DeployDrain | None) -> dict | None:
    if drain is None:
        return None
    result = {column.name: getattr(drain, column.name) for column in DeployDrain.__table__.columns}
    for key in ("started_at", "requested_at", "expires_at", "ended_at"):
        if result[key] is not None:
            result[key] = _utc(result[key]).isoformat()
    return result


@dataclass(frozen=True)
class SiteFacts:
    cycle_at: float | None
    control_in_flight: int
    heights_open: int
    util_phase: str
    util_child_running: bool
    uploads_receiving: int
    uploads_last_end_age_s: float | None


def _count(value) -> int:
    # Health uses lists for warm/height builds and integers for other counters.
    return len(value) if isinstance(value, (list, dict, tuple, set)) else int(value or 0)


def evaluate(worker_health: dict | None, site: SiteFacts, drain: DeployDrain | None, now: datetime) -> dict:
    """Readiness only: queued parses and aggregate runs are intentionally not barriers (R7)."""
    waiting = {}

    def add(name, value):
        count = _count(value)
        if count:
            waiting[name] = count

    reachable = worker_health is not None
    health = worker_health or {}
    worker = health.get("drain") or {}
    add("worker_unreachable", not reachable)
    if reachable:
        add("worker_hold", not worker.get("hold", False))
    add("site_cycle", drain is None or site.cycle_at is None
        or site.cycle_at <= _utc(drain.requested_at).timestamp())
    add("site_control_in_flight", site.control_in_flight)
    add("site_heights_open", site.heights_open)
    add("site_util", site.util_phase != "idle")
    add("site_util_child", site.util_child_running)
    control = health.get("control") or {}
    add("worker_control_queued", control.get("queued", 0))
    add("worker_control_running", control.get("running", 0))
    add("worker_warm_builds", (control.get("warm_builds") or {}).get("building", []))
    heights = health.get("heights") or {}
    for phase in ("collecting", "queued", "running"):
        add("worker_heights_" + phase, heights.get(phase, []))
    add("worker_util_building", (worker.get("util") or {}).get("building", 0))
    add("worker_parses_running", (worker.get("parses") or {}).get("running", 0))
    add("worker_cancelling", worker.get("cancelling", 0))
    uploads = worker.get("uploads") or {}
    add("site_uploads_receiving", site.uploads_receiving)
    add("worker_uploads_receiving", uploads.get("receiving", 0))
    add("site_uploads_recent", site.uploads_last_end_age_s is not None
        and site.uploads_last_end_age_s < QUIET_UPLOAD_S)
    age = uploads.get("last_end_age_s")
    add("worker_uploads_recent", age is not None and age < QUIET_UPLOAD_S)
    uploads_quiet = reachable and not any(key in waiting for key in (
        "site_uploads_receiving", "worker_uploads_receiving", "site_uploads_recent", "worker_uploads_recent"))
    return {"quiet": not waiting, "uploads_quiet": uploads_quiet,
            "waiting": waiting, "waiting_total": sum(waiting.values())}

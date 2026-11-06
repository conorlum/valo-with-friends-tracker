"""Live checks for the site-health card: conditions read when the card is computed, never stored.

Light imports only (stdlib, SQLAlchemy, models, replay_gaps for its revision, util_rebuild for its lock key): the card
must not pull in the Critical Util engine or numpy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from app.models.replay import ReplayRoundUtil, UtilModel
from app.services import replay_gaps, util_rebuild

log = logging.getLogger(__name__)

UTIL_STALE_S = 3 * 3600  # PROVISIONAL(D1): how old the oldest unbuilt util row may be before the build is out of date


def _aware(moment):
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _iso(moment):
    return moment.isoformat() if moment is not None else "none"


def _build_lock_held(db) -> bool:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return False
    key = util_rebuild.LOCK_KEY
    return db.execute(
        text("SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND classid = :hi AND objid = :lo "
             "AND objsubid = 1 LIMIT 1"),
        {"hi": key >> 32, "lo": key & 0xFFFFFFFF}).first() is not None


def util_build_stale(db, now: datetime) -> list[dict]:
    """The stored Critical Util build is older than util rows that arrived over UTIL_STALE_S ago. 0 or 1 item."""
    try:
        build = db.execute(select(UtilModel.key, UtilModel.version, UtilModel.computed_at)
                           .where(UtilModel.name == "values")).first()
        built_at = _aware(build.computed_at) if build else None
        query = select(func.min(ReplayRoundUtil.computed_at)).where(
            ReplayRoundUtil.status == "ok", ReplayRoundUtil.gaps_revision == replay_gaps.GAPS_REVISION)
        if built_at is not None:
            query = query.where(ReplayRoundUtil.computed_at > build.computed_at)
        oldest = _aware(db.execute(query).scalar())
        if oldest is None or (_aware(now) - oldest).total_seconds() <= UTIL_STALE_S:
            return []
        if _build_lock_held(db):
            return []
        return [{
            "key": "values", "first_seen": oldest,
            "error": f"the stored build (from {_iso(built_at)}) is older than util rows computed since {_iso(oldest)}",
            "context": {"build_computed_at": _iso(built_at) if built_at else None,
                        "build_key": build.key if build else None, "oldest_newer_row": _iso(oldest)},
        }]
    except Exception:
        log.warning("the Critical Util build check failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            log.warning("rollback after the build check failed", exc_info=True)
        return []

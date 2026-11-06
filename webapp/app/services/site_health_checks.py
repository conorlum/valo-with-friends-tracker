"""Live checks for the site-health card: conditions read when the card is computed, never stored.

Light imports only (stdlib, SQLAlchemy, models, replay_gaps for its revision, util_rebuild for its lock key): the card
must not pull in the Critical Util engine or numpy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.models.impact_score import ImpactScore
from app.models.map_pool import MapPoolPatchNotes
from app.models.match import Match
from app.models.replay import Replay, ReplayRoundUtil, UtilModel
from app.models.round import Round
from app.services import replay_gaps, util_rebuild

log = logging.getLogger(__name__)

SITE_URL = "https://valowithfriendstracker.onrender.com"
UNSCORED_AFTER_S = 3600   # a match gets an hour to be scored before it counts
UNSCORED_CAP = 200        # the card shows 50 per kind anyway

UTIL_STALE_S = 3 * 3600  # How old the oldest unbuilt util row may be before the build is out of date


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


def _safely(db, label, body):
    try:
        return body()
    except Exception:
        log.warning("the %s check failed", label, exc_info=True)
        try:
            db.rollback()
        except Exception:
            log.warning("rollback after the %s check failed", label, exc_info=True)
        return []


def _scored(match_id_column):
    """EXISTS: an impact_scores row, through rounds, for the match whose id is `match_id_column`."""
    return (select(ImpactScore.round_id).join(Round, ImpactScore.round_id == Round.id)
            .where(Round.match_id == match_id_column).exists())


def unscored_matches(db, now: datetime) -> list[dict]:
    """Matches stored over an hour ago that have no Impact score row. At most UNSCORED_CAP items."""
    def body():
        cutoff = _aware(now) - timedelta(seconds=UNSCORED_AFTER_S)
        found = db.execute(select(Match.id, Match.external_id, Match.map_name, Match.created_at)
                           .where(Match.created_at < cutoff, ~_scored(Match.id))
                           .order_by(Match.created_at).limit(UNSCORED_CAP)).all()
        return [{
            "key": external_id or str(match_id), "first_seen": _aware(created_at),
            "error": f"stored at {_iso(_aware(created_at))} and still has no Impact scores",
            "context": {"match_id": match_id, "map_name": map_name},
            "link": f"{SITE_URL}/matches/{external_id}" if external_id else None,
        } for match_id, external_id, map_name, created_at in found]
    return _safely(db, "unscored matches", body)


def kill_impact_null(db) -> list[dict]:
    """Linked replays with no per-kill Impact stored, on a match that has Impact scores."""
    def body():
        found = db.execute(select(Replay.id, Replay.match_id, Replay.map_name, Replay.match_uuid, Replay.linked_at,
                                  Replay.created_at)
                           .where(Replay.link_status == "linked", Replay.match_id.is_not(None),
                                  Replay.kill_impact.is_(None), _scored(Replay.match_id))
                           .order_by(Replay.id).limit(UNSCORED_CAP)).all()
        return [{
            "key": str(rid), "first_seen": _aware(linked_at or created_at),
            "error": "linked replay has no stored per-kill Impact",
            "context": {"match_id": match_id, "map_name": map_name},
            "reproduce": f"python scripts/link_replays.py --uuid {match_uuid} --refresh-impact",
        } for rid, match_id, map_name, match_uuid, linked_at, created_at in found]
    return _safely(db, "per-kill Impact", body)


def map_pool_notes(db) -> list[dict]:
    """Patch-notes pages the map-pool refresh could not read."""
    def body():
        found = db.execute(select(MapPoolPatchNotes.url, MapPoolPatchNotes.patch, MapPoolPatchNotes.published_at,
                                  MapPoolPatchNotes.first_seen_at, MapPoolPatchNotes.miss_count,
                                  MapPoolPatchNotes.last_error)
                           .where(MapPoolPatchNotes.last_error.is_not(None)).order_by(MapPoolPatchNotes.url)).all()
        return [{
            "key": url, "first_seen": _aware(first_seen), "error": last_error, "link": url,
            "context": {"patch": patch, "miss_count": miss_count, "published_at": _iso(_aware(published_at))},
        } for url, patch, published_at, first_seen, miss_count, last_error in found]
    return _safely(db, "map-pool notes", body)

"""Read/write/invalidate layer for viewer_site_stats_cache -- the /stats
page's "Friends" tab, one row per logged-in viewer. Same best-effort contract
as app.services.site_stats_cache: a miss, a stale version, a friend-set
mismatch, or a corrupt blob all degrade to a live recompute
(app.services.site_stats.get_viewer_site_stats), never a 500.

The blob holds the same stat keys as the site-wide blob, each mapped to the
viewer's GROUP variant (matches where the viewer or one of their friends was
on the relevant team), with one difference: pistol_match_stats honours the
Recent/Career toggle, so it's stored as {"recent": ..., "career": ...}.

A row is stale when:
  - its version differs from viewer_cache_version() -- which folds in
    SITE_STATS_CACHE_SCHEMA_VERSION, so any change to how a stat is computed
    (which already bumps that constant) also retires every viewer row; or
  - its friend_set_hash differs from the viewer's current player-ID set.

Rows are deleted outright by app.routers.friends on a friendship add/remove
(that viewer's row) and by app.services.site_stats_cache.
invalidate_site_stats_cache / app.services.site_stats.refresh_site_stats on
any ingest (every row).
"""

import hashlib
import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.viewer_site_stats_cache import ViewerSiteStatsCache
from app.services.site_stats_cache import (
    SITE_STATS_CACHE_SCHEMA_VERSION,
    STAT_VARIANT_VALIDATORS,
    _validate_pistol_match_stats,
)

logger = logging.getLogger(__name__)

VIEWER_SITE_STATS_CACHE_SCHEMA_VERSION = 1
# Bump when the viewer blob's OWN shape changes (e.g. the recent/career split
# of pistol_match_stats). A change to how any stat is computed bumps
# SITE_STATS_CACHE_SCHEMA_VERSION instead, which viewer_cache_version()
# already folds in.

PISTOL_SCOPES = ("recent", "career")


def viewer_cache_version() -> int:
    return SITE_STATS_CACHE_SCHEMA_VERSION * 1000 + VIEWER_SITE_STATS_CACHE_SCHEMA_VERSION


def friend_set_hash(player_ids: set[int]) -> str:
    """Order-independent fingerprint of the viewer's group (viewer + friends)."""
    return hashlib.sha256(",".join(str(pid) for pid in sorted(player_ids)).encode()).hexdigest()


def _validate_viewer_blob(data: object) -> bool:
    if not isinstance(data, dict) or set(data.keys()) != set(STAT_VARIANT_VALIDATORS):
        return False
    pistol = data["pistol_match_stats"]
    if not isinstance(pistol, dict) or set(pistol.keys()) != set(PISTOL_SCOPES):
        return False
    if not all(_validate_pistol_match_stats(pistol[scope]) for scope in PISTOL_SCOPES):
        return False
    return all(
        validate(data[key]) for key, validate in STAT_VARIANT_VALIDATORS.items() if key != "pistol_match_stats"
    )


def get_viewer_site_stats_cache(db: Session, viewer_player_id: int, expected_friend_set_hash: str) -> dict | None:
    """None on a miss, a version mismatch, a friend-set mismatch, or a
    corrupt blob -- the caller then computes live and writes back. A read
    that fails outright (e.g. the table isn't there yet mid-deploy) is a miss
    too, with the transaction rolled back so the live recompute can run."""
    try:
        row = db.get(ViewerSiteStatsCache, viewer_player_id)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("viewer_site_stats_cache: read failed for viewer %s", viewer_player_id)
        return None
    if row is None:
        return None
    if row.version != viewer_cache_version() or row.friend_set_hash != expected_friend_set_hash:
        return None
    if not _validate_viewer_blob(row.data):
        logger.warning("viewer_site_stats_cache: invalid blob for viewer %s", viewer_player_id)
        return None
    return row.data


def store_viewer_site_stats_cache(db: Session, viewer_player_id: int, friend_set_hash_: str, data: dict) -> None:
    """Upserts the viewer's row and commits."""
    values = {"data": data, "version": viewer_cache_version(), "friend_set_hash": friend_set_hash_,
              "updated_at": func.now()}
    stmt = (
        pg_insert(ViewerSiteStatsCache.__table__)
        .values(viewer_player_id=viewer_player_id, **values)
        .on_conflict_do_update(index_elements=[ViewerSiteStatsCache.viewer_player_id], set_=values)
    )
    db.execute(stmt)
    db.commit()


def invalidate_viewer_site_stats(db: Session, viewer_player_id: int) -> None:
    """DELETE one viewer's row. Does NOT commit -- the caller commits, so the
    delete lands in the same transaction as the friendship change behind it."""
    db.query(ViewerSiteStatsCache).filter_by(viewer_player_id=viewer_player_id).delete(synchronize_session=False)

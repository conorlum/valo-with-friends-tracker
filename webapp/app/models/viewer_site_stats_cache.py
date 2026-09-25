from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ViewerSiteStatsCache(Base):
    """One row per logged-in viewer: the /stats page's "Friends" tab as that
    viewer sees it (the viewer plus the players on their own Friends page).
    Purely derived from Match/Round/MatchPlayer and friendships rows -- safe
    to truncate at any time; the route recomputes live on a miss, same
    contract as site_stats_cache and player_view_cache.

    friend_set_hash fingerprints the player-ID set the row was computed for,
    so a row outlived by a friendship change reads as stale even if the
    invalidation that should have deleted it never ran."""

    __tablename__ = "viewer_site_stats_cache"

    viewer_player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    friend_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # NOTE: no onupdate=func.now() -- see player_view_cache.py's identical note
    # (SQLAlchemy's onupdate doesn't fire on ON CONFLICT DO UPDATE).

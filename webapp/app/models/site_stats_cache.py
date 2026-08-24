from datetime import datetime

from sqlalchemy import JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SITE_STATS_CACHE_ROW_ID = 1


class SiteStatsCache(Base):
    """Single-row cache (always id=SITE_STATS_CACHE_ROW_ID) of every
    whole-database aggregate stat shown on the "All Players" tab of the
    /stats page -- e.g. pistol_match_stats today, more keys as that tab grows.
    Purely derived from Match/Round/MatchPlayer rows -- safe to truncate at
    any time; the route recomputes live on a miss, same contract as
    player_view_cache."""

    __tablename__ = "site_stats_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # NOTE: no onupdate=func.now() -- see player_view_cache.py's identical note
    # (SQLAlchemy's onupdate doesn't fire on ON CONFLICT DO UPDATE).

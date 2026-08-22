from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlayerViewCache(Base):
    """One pre-computed (state-diagram aggregates + fight-EV) payload per
    (player, scope). Purely derived data -- safe to truncate at any time;
    the player page recomputes live on a miss."""

    __tablename__ = "player_view_cache"
    __table_args__ = (
        UniqueConstraint("player_id", "scope", name="uq_player_view_cache_scope"),
        CheckConstraint("scope IN ('recent', 'career')", name="ck_player_view_cache_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # NOTE: no onupdate=func.now(). SQLAlchemy's onupdate does not fire on
    # PostgreSQL INSERT ... ON CONFLICT DO UPDATE, so _upsert_cache sets
    # updated_at explicitly in its conflict clause.

"""Post-plant evidence and derived statistics, outside the scoring model files."""
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Integer, LargeBinary, SmallInteger, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.replay import JSONType


class ReplayRoundPostplant(Base):
    """One round's extraction result, stored under its gap run's freshness keys."""

    __tablename__ = "replay_round_postplant"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_postplant_status"),
        ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                             ondelete="CASCADE", name="fk_replay_round_postplant_round"),
    )

    replay_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    gaps_revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PostplantStatsCache(Base):
    """Derived zlib-compressed JSON, keyed by the requested population and figure."""

    __tablename__ = "postplant_stats_cache"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

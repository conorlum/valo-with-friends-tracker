from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MapPoolChange(Base):
    """One map entering (in_pool) or leaving the competitive pool at effective_at. map_name is tracker.gg's title
    case, as matches.map_name. patch is "12.08", or "release" for pre-5.0 rows. See migration 0023."""

    __tablename__ = "map_pool_changes"
    __table_args__ = (
        CheckConstraint("source IN ('seed', 'patch_notes')", name="ck_map_pool_changes_source"),
        UniqueConstraint("map_name", "effective_at", "in_pool", name="uq_map_pool_changes_map_time_dir"),
        Index("ix_map_pool_changes_effective_at", "effective_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    map_name: Mapped[str] = mapped_column(Text, nullable=False)
    in_pool: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    patch: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MapPoolPatchNotes(Base):
    """One patch-notes page the refresh has looked at (including pages that changed nothing), so a read page is
    fetched once. read_at is null while unread."""

    __tablename__ = "map_pool_patch_notes"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    patch: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_count: Mapped[int | None] = mapped_column(nullable=True)
    miss_count: Mapped[int] = mapped_column(server_default="0", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

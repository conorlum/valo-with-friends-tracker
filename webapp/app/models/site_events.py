"""Site health events (migration 0031): what went wrong or needs a look, one row per (kind, key), and the
resolutions an owner has applied, and the marks clears and deduplication leave. No foreign keys; not digested by the scoring manifest."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.replay import JSONType


class SiteEvent(Base):
    __tablename__ = "site_events"
    __table_args__ = (Index("ix_site_events_kind_last_seen", "kind", "last_seen"),)

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    context: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SiteEventResolution(Base):
    __tablename__ = "site_event_resolutions"

    entry: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the entry's canonical JSON
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SiteEventMark(Base):
    """What a cleared or deduplicated row leaves behind, so another process's late write can't bring it back:
    `cleared_at` (a success or an applied PR clears every occurrence up to it; `prefix` covers every key starting
    with `key`) and `seen` (what a record already counted, e.g. a warm-cache loss per worker boot)."""
    __tablename__ = "site_event_marks"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    prefix: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seen: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

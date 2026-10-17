"""The AI coach's tables (migration 0021; docs/superpowers/specs/2026-10-12-ai-coach-design.md, "Storage").

No relationship back-references into the scoring models, for the reason app/models/replay.py gives: those files
are scoring HASHED_SOURCES and are never edited. A report is immutable once stored: regenerating makes the next
`version` for the same match, player and kind, and feedback stays attached to the points it was written about."""

from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.replay import JSONType


class CoachReport(Base):
    __tablename__ = "coach_reports"
    __table_args__ = (
        CheckConstraint("kind IN ('match', 'cross_match', 'round')", name="ck_coach_reports_kind"),
        UniqueConstraint("match_id", "player_id", "kind", "version", name="uq_coach_reports_version"),
        Index("ix_coach_reports_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="match")
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    playbook_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    data_gaps: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    dropped_points: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CoachPoint(Base):
    __tablename__ = "coach_points"
    __table_args__ = (CheckConstraint("polarity IN ('good', 'improve')", name="ck_coach_points_polarity"),
                      Index("ix_coach_points_report", "report_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("coach_reports.id", ondelete="CASCADE"), nullable=False)
    polarity: Mapped[str] = mapped_column(String(8), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    theme: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"round": 7, "t": 41.5, "t1": 48.0 (optional), "caption": "...", "evidence": ["q3", "q4"]}, ...]
    instances: Mapped[list] = mapped_column(JSONType, nullable=False)


class CoachFeedback(Base):
    __tablename__ = "coach_feedback"
    __table_args__ = (CheckConstraint("verdict IN ('right', 'wrong', 'correction', 'badly_put')",
                                      name="ck_coach_feedback_verdict"),
                      Index("ix_coach_feedback_point", "point_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("coach_points.id", ondelete="CASCADE"), nullable=False)
    # The moment of the point it is about (its position in the point's `instances`); None: the point as a whole.
    instance_index: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    author_player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(12), nullable=True)      # None: a note alone (migration 0025)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CoachNote(Base):
    """Owner-written material the coach reads that must not be in the public repository (kind 'voice')."""

    __tablename__ = "coach_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

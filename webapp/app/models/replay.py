"""The replay viewer's tables (migration 0012; docs/replay-viewer-plan.md, "Storage").

No relationship back-references into the scoring models: `app/models/{match,round,kill_event,
impact_score,player}.py` are scoring HASHED_SOURCES (`app/scoring/impact_manifest.py`), so they
are never edited for replays. For the same reason `players.riot_subject` (added by 0012) has no
ORM attribute; `app/replays/db.py` reads and writes it with Core SQL.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    REAL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# jsonb on PostgreSQL (the unlink trigger merges into link_report), plain JSON elsewhere (tests);
# Python None is SQL NULL, never JSON null, so `IS NULL` finds a missing split.
JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class Replay(Base):
    __tablename__ = "replays"
    __table_args__ = (
        CheckConstraint("source IN ('local', 'upload')", name="ck_replays_source"),
        CheckConstraint("link_status IN ('linked', 'unlinked', 'refused')", name="ck_replays_link_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_uuid: Mapped[str] = mapped_column(Uuid(as_uuid=False), unique=True, nullable=False)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"), unique=True,
                                                 nullable=True)
    map_name: Mapped[str] = mapped_column(String(64), nullable=False)
    round_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    format_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recipe: Mapped[str] = mapped_column(String(80), nullable=False)
    game_branch: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    link_status: Mapped[str] = mapped_column(String(12), nullable=False)
    link_inputs: Mapped[dict] = mapped_column(JSONType, nullable=False)
    link_report: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    clock_offset: Mapped[float | None] = mapped_column(REAL, nullable=True)
    kill_map: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    db_deaths: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    kill_impact: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReplayRound(Base):
    __tablename__ = "replay_rounds"

    replay_id: Mapped[int] = mapped_column(ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True)
    round_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ReplayRoundControl(Base):
    """Map control for one round (migration 0014; app/replays/control_format.py). Written only by
    scripts/compute_control.py; `data` is served as is, `summary` is read by the heatmaps and tables."""

    __tablename__ = "replay_round_control"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_control_status"),
        CheckConstraint("status <> 'ok' OR (data IS NOT NULL AND summary IS NOT NULL AND data_version IS NOT NULL)",
                        name="ck_replay_round_control_ok_has_data"),
        ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                             ondelete="CASCADE", name="fk_replay_round_control_round"),
    )

    replay_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    data_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # control_format.DATA_VERSION
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    summary: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReplayPlayer(Base):
    """Private: never served. The Subject never leaves the server."""

    __tablename__ = "replay_players"
    __table_args__ = (UniqueConstraint("replay_id", "subject", name="uq_replay_players_subject"),)

    replay_id: Mapped[int] = mapped_column(ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True)
    slot: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    subject: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    side_group: Mapped[str | None] = mapped_column(String(1), nullable=True)
    match_player_id: Mapped[int | None] = mapped_column(ForeignKey("match_players.id", ondelete="SET NULL"),
                                                        nullable=True)


class ReplayUpload(Base):
    __tablename__ = "replay_uploads"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'parsing', 'stored', 'failed')", name="ck_replay_uploads_status"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False,
                                                 index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replay_id: Mapped[int | None] = mapped_column(ForeignKey("replays.id", ondelete="SET NULL"), nullable=True)
    worker_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # migration 0013

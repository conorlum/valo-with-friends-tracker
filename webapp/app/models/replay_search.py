"""The replay search index (migration 0039): one row per moment of a linked replay, columns = `app.search.match.ROW_KEYS`,
and one state row per replay saying which source key and index revision its rows were built from. Written only by
app/services/replay_search_index.py."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.replay import JSONType


class ReplayMomentIndex(Base):
    __tablename__ = "replay_moment_index"
    __table_args__ = (
        Index("ix_replay_moment_index_kind_map", "kind", "map"),
        Index("ix_replay_moment_index_player_id_kind", "player_id", "kind"),
        Index("ix_replay_moment_index_agent_kind", "agent", "kind"),
        Index("ix_replay_moment_index_replay_id", "replay_id"),
    )

    replay_id: Mapped[int] = mapped_column(ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True)
    round: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    map: Mapped[str] = mapped_column(String(64), nullable=False)
    played_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attack_team: Mapped[str | None] = mapped_column(String(8), nullable=True)
    won_team: Mapped[str | None] = mapped_column(String(8), nullable=True)
    buy1: Mapped[str | None] = mapped_column(String(8), nullable=True)
    buy2: Mapped[str | None] = mapped_column(String(8), nullable=True)
    score1: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    score2: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    planted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    site: Mapped[str | None] = mapped_column(String(1), nullable=True)
    plant_t: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    t: Mapped[float] = mapped_column(Float, nullable=False)
    t_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    team: Mapped[str | None] = mapped_column(String(8), nullable=True)
    slot: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    other_slot: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    other_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    other_agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    other_team: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ability: Mapped[str | None] = mapped_column(String(96), nullable=True)
    ability_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ult: Mapped[bool] = mapped_column(Boolean, nullable=False)
    first: Mapped[bool] = mapped_column(Boolean, nullable=False)
    alive1: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    alive2: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    alive_slots: Mapped[list] = mapped_column(JSONType, nullable=False)
    post_plant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tags: Mapped[list] = mapped_column(JSONType, nullable=False)
    weapon: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ReplayMomentIndexState(Base):
    __tablename__ = "replay_moment_index_state"

    replay_id: Mapped[int] = mapped_column(ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

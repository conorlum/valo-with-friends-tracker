from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("match_id", "round_number", name="uq_match_round"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    planted: Mapped[bool] = mapped_column(Boolean, default=False)
    plant_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    exploded: Mapped[bool] = mapped_column(Boolean, default=False)
    defused: Mapped[bool] = mapped_column(Boolean, default=False)
    defuse_time: Mapped[float | None] = mapped_column(Float, nullable=True)

    match: Mapped["Match"] = relationship(back_populates="rounds")
    player_stats: Mapped[list["RoundPlayerStat"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )
    kill_events: Mapped[list["KillEvent"]] = relationship(
        back_populates="round",
        cascade="all, delete-orphan",
        foreign_keys="KillEvent.round_id",
    )


class RoundPlayerStat(Base):
    __tablename__ = "round_player_stats"
    __table_args__ = (
        UniqueConstraint("round_id", "match_player_id", name="uq_round_match_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    match_player_id: Mapped[int] = mapped_column(ForeignKey("match_players.id"), nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    kills: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    loadout: Mapped[int] = mapped_column(Integer, default=0)
    remaining: Mapped[int] = mapped_column(Integer, default=0)

    round: Mapped["Round"] = relationship(back_populates="player_stats")

"""Plant positions and their derived cache, separate from scored model files."""
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer,
    LargeBinary, SmallInteger, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RoundPlant(Base):
    __tablename__ = "round_plants"
    __table_args__ = (
        CheckConstraint("source IN ('tracker', 'replay')", name="ck_round_plants_source"),
    )

    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    asset_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    site: Mapped[str] = mapped_column(String(1))
    u: Mapped[int] = mapped_column(Integer)
    v: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(8))
    round_number: Mapped[int] = mapped_column(SmallInteger)
    plant_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    attackers_alive: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    defenders_alive: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    attackers_won: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    defused: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlantStatsCache(Base):
    """Derived zlib-compressed JSON for one map layout and population."""

    __tablename__ = "plant_stats_cache"

    asset_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    population: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

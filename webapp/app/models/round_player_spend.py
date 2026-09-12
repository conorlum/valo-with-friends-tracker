"""tracker.gg spentCredits per player-round (spec 2026-09-12 section 10).

A separate additive table so app/models/round.py -- shared with the public repo
and hashed by the scoring manifest -- is untouched. No row means UNKNOWN: the
value is never written as 0 when tracker.gg did not supply it. The scorer does
not read this table.
"""
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RoundPlayerSpend(Base):
    __tablename__ = "round_player_spend"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_player_stat_id: Mapped[int] = mapped_column(
        ForeignKey("round_player_stats.id", ondelete="CASCADE"), nullable=False, unique=True)
    spent: Mapped[int] = mapped_column(Integer, nullable=False)

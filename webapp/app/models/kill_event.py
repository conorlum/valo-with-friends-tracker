from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class KillEvent(Base):
    __tablename__ = "kill_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    killer_match_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_players.id"), nullable=True
    )
    death_match_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_players.id"), nullable=True
    )
    weapon: Mapped[str] = mapped_column(String(32), nullable=False)
    event_time_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    # Source-specific extras that don't warrant their own column yet,
    # e.g. the tracker.gg adapter's per-kill assistants list.
    source_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    round: Mapped["Round"] = relationship(
        back_populates="kill_events", foreign_keys=[round_id]
    )

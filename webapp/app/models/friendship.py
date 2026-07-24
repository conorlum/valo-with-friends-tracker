from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Friendship(Base):
    """A one-directional "owner considers friend a friend" edge -- adding
    someone doesn't require their consent or add you to their list, matching
    the app's existing zero-auth login (anyone can log in as any Player).
    """

    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("owner_player_id", "friend_player_id", name="uq_friendship"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    friend_player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    owner: Mapped["Player"] = relationship(foreign_keys=[owner_player_id])
    friend: Mapped["Player"] = relationship(foreign_keys=[friend_player_id])

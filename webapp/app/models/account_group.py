from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AccountGroup(Base):
    """One person's Riot accounts, grouped so the player page can show them
    combined: a main account and its sub accounts. Like Friendship, it is
    zero-auth: whoever is logged in as the main adds and removes subs, and a
    sub can leave (see app.services.account_groups)."""

    __tablename__ = "account_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Also a member. Unique since an account is in at most one group.
    main_player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    members: Mapped[list["AccountGroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True
    )


class AccountGroupMember(Base):
    """player_id is the primary key, so an account belongs to at most one group."""

    __tablename__ = "account_group_members"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("account_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # What the owner calls this account ("Main", "Duelist alt"); None shows the Riot ID.
    label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    group: Mapped[AccountGroup] = relationship(back_populates="members")
    player: Mapped["Player"] = relationship()

    @property
    def is_main(self) -> bool:
        return self.group.main_player_id == self.player_id


class AccountGroupViewCache(Base):
    """The combined player page of a group, one row per scope -- the same
    blob player_view_cache holds for one player. Purely derived data, safe to
    truncate. Instead of being invalidated by every ingest path, a row carries
    a fingerprint of everything it was built from (members, their matches and
    Impact rows, their friendships) and is ignored when that no longer matches
    (app.services.account_groups.group_fingerprint)."""

    __tablename__ = "account_group_view_cache"
    __table_args__ = (
        CheckConstraint("scope IN ('recent', 'career')", name="ck_account_group_view_cache_scope"),
    )

    group_id: Mapped[int] = mapped_column(
        ForeignKey("account_groups.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Same packed cache_version() as player_view_cache.version, hence BigInteger.
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

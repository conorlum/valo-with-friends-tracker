"""Deploy ownership and history, including intervals excluded from stuck-work clocks."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, false, literal_column
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeployDrain(Base):
    __tablename__ = "deploy_drains"
    __table_args__ = (
        CheckConstraint("phase IN ('draining', 'deploying')", name="ck_deploy_drains_phase"),
        CheckConstraint("outcome IN ('ok', 'failed', 'dry_run', 'expired')", name="ck_deploy_drains_outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    superseded: Mapped[str | None] = mapped_column(String(128))
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    commit: Mapped[str | None] = mapped_column(String(64))
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    skip_drain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    waiting_total: Mapped[int | None] = mapped_column(Integer)


# A constant has no column from which SQLAlchemy can infer the owning table.
# Parenthesizing it also makes PostgreSQL treat it as an expression index.
DeployDrain.__table__.append_constraint(Index(
    "ux_deploy_drains_open", literal_column("(true)"), unique=True,
    postgresql_where=DeployDrain.ended_at.is_(None), sqlite_where=DeployDrain.ended_at.is_(None),
))

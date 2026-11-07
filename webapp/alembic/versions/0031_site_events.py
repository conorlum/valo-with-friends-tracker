"""site events

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-05

Site health (docs/superpowers/specs, site health design): `site_events`, one row per (kind, key) holding what went
wrong or needs a look, with a count and first/last seen; and `site_event_resolutions`, the resolutions an owner has
applied (entry = sha256 of the entry's canonical JSON); and `site_event_marks`, what a clear or a
deduplicated record leaves so another process's late write can't bring the row back. Additive: new tables with no foreign keys; the ValoMaths
demo gets empty tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "site_events",
        sa.Column("kind", sa.String(length=32), primary_key=True),
        sa.Column("key", sa.String(length=300), primary_key=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", J, nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_site_events_kind_last_seen", "site_events", ["kind", "last_seen"])
    op.create_table(
        "site_event_resolutions",
        sa.Column("entry", sa.String(length=64), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("key", sa.String(length=300), nullable=True),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "site_event_marks",
        sa.Column("kind", sa.String(length=32), primary_key=True),
        sa.Column("key", sa.String(length=300), primary_key=True),
        sa.Column("prefix", sa.Boolean(), primary_key=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen", J, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("site_event_marks")
    op.drop_table("site_event_resolutions")
    op.drop_index("ix_site_events_kind_last_seen", table_name="site_events")
    op.drop_table("site_events")

"""add round_player_spend

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12

tracker.gg's spentCredits per player-round, stored going forward only
(docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md,
section 10). Additive: no existing table changes and no backfill -- a
missing row means unknown spend, never zero. The scorer does not read it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "round_player_spend",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("round_player_stat_id", sa.Integer(),
                  sa.ForeignKey("round_player_stats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spent", sa.Integer(), nullable=False),
        sa.UniqueConstraint("round_player_stat_id", name="uq_round_player_spend_stat"),
    )


def downgrade() -> None:
    op.drop_table("round_player_spend")

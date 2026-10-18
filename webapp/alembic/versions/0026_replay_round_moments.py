"""replay round moments

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-18

Critical moments: one row per replay round holding the worker's per-fight tags, written with the round's gap run
by app/services/replay_gaps_store.py. References replay_rounds with ON DELETE CASCADE. Additive: the ValoMaths
demo gets an empty table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_round_moments",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("gaps_revision", sa.SmallInteger(), nullable=False),
        sa.Column("fights", J, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_round_moments_round"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_moments_status"),
    )


def downgrade() -> None:
    op.drop_table("replay_round_moments")

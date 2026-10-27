"""critical util

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-21

Critical Util (docs/superpowers/specs/2026-10-21-critical-util-design.md): `replay_round_util`, one row per replay
round holding the worker's casts and their evidence, written with the round's gap run by
app/services/replay_gaps_store.py (references replay_rounds with ON DELETE CASCADE); and `util_model`, the corpus
products the site builds from them (the round-win estimator, each cast's value). Additive: the ValoMaths demo gets
empty tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_round_util",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("gaps_revision", sa.SmallInteger(), nullable=False),
        sa.Column("data", J, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_round_util_round"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_util_status"),
    )
    op.create_table(
        "util_model",
        sa.Column("name", sa.String(length=16), primary_key=True),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("data", J, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("util_model")
    op.drop_table("replay_round_util")

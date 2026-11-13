"""replay round wall shares

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-11

`replay_round_wall_shares`: for each ok control round, how many of its 4 Hz alive player positions fall on
non-walk cells of its layout's walk mask (`inside` of `total`), keyed by the control row's fingerprint it was
computed against. Additive: one new table with no foreign keys (readers join to current ok control rows); the
ValoMaths demo gets an empty table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "replay_round_wall_shares",
        sa.Column("replay_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("asset_key", sa.String(length=64), nullable=False),
        sa.Column("control_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("inside", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("replay_id", "round_number"),
    )


def downgrade() -> None:
    op.drop_table("replay_round_wall_shares")

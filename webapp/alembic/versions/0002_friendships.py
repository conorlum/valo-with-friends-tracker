"""friendships

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "friendships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("friend_player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_player_id", "friend_player_id", name="uq_friendship"),
    )


def downgrade() -> None:
    op.drop_table("friendships")

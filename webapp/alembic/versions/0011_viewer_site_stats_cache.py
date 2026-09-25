"""viewer site stats cache

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24

The /stats page's "Friends" tab is now the logged-in viewer plus that
viewer's own friendships, computed per viewer, so it gets its own cache table
beside the single-row site_stats_cache (which keeps only "All Players").
Purely derived data: the table starts empty and fills on first view.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "viewer_site_stats_cache",
        sa.Column("viewer_player_id", sa.Integer(),
                  sa.ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("friend_set_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("viewer_site_stats_cache")

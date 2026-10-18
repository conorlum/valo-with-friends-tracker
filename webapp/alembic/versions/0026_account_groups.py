"""account groups

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-17

One person's several Riot accounts (a main account and its subs), grouped so
the player page can show them combined or one at a time, plus a cache of the
combined page. Additive: three
new tables, no change to an existing one. All three start empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("main_player_id", sa.Integer(),
                  sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "account_group_members",
        sa.Column("player_id", sa.Integer(),
                  sa.ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("account_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_account_group_members_group_id", "account_group_members", ["group_id"])
    op.create_table(
        "account_group_view_cache",
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("account_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scope", sa.String(length=16), primary_key=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("scope IN ('recent', 'career')", name="ck_account_group_view_cache_scope"),
    )


def downgrade() -> None:
    op.drop_table("account_group_view_cache")
    op.drop_index("ix_account_group_members_group_id", table_name="account_group_members")
    op.drop_table("account_group_members")
    op.drop_table("account_groups")

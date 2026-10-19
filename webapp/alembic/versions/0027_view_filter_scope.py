"""filtered view-cache scopes

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-18

The two view caches (player_view_cache, account_group_view_cache) fold a page
filter into the existing scope column: "career|role:duelist", "recent|agent:KAY/O".
Additive: widens scope from 16 to 48 characters and loosens its CHECK to also
allow 'recent|...' and 'career|...'. No data change; every existing row stays valid.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    ("player_view_cache", "ck_player_view_cache_scope"),
    ("account_group_view_cache", "ck_account_group_view_cache_scope"),
)
OLD_CHECK = "scope IN ('recent', 'career')"
NEW_CHECK = "scope IN ('recent', 'career') OR scope LIKE 'recent|%' OR scope LIKE 'career|%'"


def upgrade() -> None:
    for table, check in TABLES:
        op.drop_constraint(check, table, type_="check")
        op.alter_column(table, "scope", existing_type=sa.String(length=16),
                        type_=sa.String(length=48), existing_nullable=False)
        op.create_check_constraint(check, table, NEW_CHECK)


def downgrade() -> None:
    for table, check in TABLES:
        op.execute(f"DELETE FROM {table} WHERE scope LIKE '%|%'")
        op.drop_constraint(check, table, type_="check")
        op.alter_column(table, "scope", existing_type=sa.String(length=48),
                        type_=sa.String(length=16), existing_nullable=False)
        op.create_check_constraint(check, table, OLD_CHECK)

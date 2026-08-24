"""widen player_view_cache.version to bigint

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-24

cache_version() packs PLAYER_VIEW_CACHE_SCHEMA_VERSION * 1_000_000_000 plus
three sub-versions into one int, which overflows a 4-byte integer (max
~2.1B) once the schema version reaches 3 -- every cache write has been
failing with psycopg2.errors.NumericValueOutOfRange since then.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "player_view_cache", "version",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "player_view_cache", "version",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )

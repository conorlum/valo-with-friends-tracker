"""Compressed Critical Util values storage.

Revision ID: 0035
Revises: 0034
"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("util_model", sa.Column("blob", sa.LargeBinary(), nullable=True))
    op.alter_column("util_model", "data", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM util_model WHERE data IS NULL")
    op.drop_column("util_model", "blob")
    op.alter_column("util_model", "data", nullable=False)

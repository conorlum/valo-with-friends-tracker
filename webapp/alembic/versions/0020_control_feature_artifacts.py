"""Immutable per-map feature archives. Additive; no round changes or backfill.

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('control_feature_artifacts',
        sa.Column('digest', sa.String(64), primary_key=True),
        sa.Column('map_name', sa.String(64), nullable=False),
        sa.Column('height_digest', sa.String(12), nullable=False),
        sa.Column('tags_digest', sa.String(64), nullable=False),
        sa.Column('compiler_version', sa.Integer(), nullable=False),
        sa.Column('manifest', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('inputs', sa.LargeBinary(), nullable=False),
        sa.Column('assets', sa.LargeBinary(), nullable=False),
        sa.Column('code_commit', sa.String(40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('map_name', 'height_digest', 'tags_digest', 'compiler_version', name='uq_control_feature_key'))


def downgrade():
    # Unsafe after live round references exist: retain archives on operational rollback.
    op.drop_table('control_feature_artifacts')

"""coach_feedback can be about one moment of a point (instance_index, the point's `instances` position) and can
be a note with no verdict (a point's overall note). The verdict may also be 'correction'. Existing rows keep
their point-level meaning: instance_index NULL.

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('coach_feedback', sa.Column('instance_index', sa.SmallInteger(), nullable=True))
    op.alter_column('coach_feedback', 'verdict', existing_type=sa.String(12), nullable=True)
    op.drop_constraint('ck_coach_feedback_verdict', 'coach_feedback', type_='check')
    op.create_check_constraint('ck_coach_feedback_verdict', 'coach_feedback',
                               "verdict IN ('right', 'wrong', 'correction', 'badly_put')")


def downgrade():
    op.execute("DELETE FROM coach_feedback WHERE instance_index IS NOT NULL OR verdict IS NULL "
               "OR verdict = 'correction'")
    op.drop_constraint('ck_coach_feedback_verdict', 'coach_feedback', type_='check')
    op.create_check_constraint('ck_coach_feedback_verdict', 'coach_feedback',
                               "verdict IN ('right', 'wrong', 'badly_put')")
    op.alter_column('coach_feedback', 'verdict', existing_type=sa.String(12), nullable=False)
    op.drop_column('coach_feedback', 'instance_index')

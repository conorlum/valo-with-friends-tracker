"""The AI coach's reports, points, feedback and notes. Additive; no existing table changes.

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), 'postgresql')


def upgrade():
    op.create_table('coach_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('match_id', sa.Integer(), sa.ForeignKey('matches.id', ondelete='CASCADE'), nullable=True),
        sa.Column('player_id', sa.Integer(), sa.ForeignKey('players.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('version', sa.SmallInteger(), nullable=False),
        sa.Column('playbook_version', sa.String(32), nullable=False),
        sa.Column('model', sa.String(64), nullable=False),
        sa.Column('data_gaps', JSON, nullable=True),
        sa.Column('dropped_points', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('match', 'cross_match', 'round')", name='ck_coach_reports_kind'),
        sa.UniqueConstraint('match_id', 'player_id', 'kind', 'version', name='uq_coach_reports_version'))
    op.create_index('ix_coach_reports_player', 'coach_reports', ['player_id'])
    op.create_table('coach_points',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('coach_reports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('polarity', sa.String(8), nullable=False),
        sa.Column('ordinal', sa.SmallInteger(), nullable=False),
        sa.Column('theme', sa.String(32), nullable=False),
        sa.Column('title', sa.String(160), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('instances', JSON, nullable=False),
        sa.CheckConstraint("polarity IN ('good', 'improve')", name='ck_coach_points_polarity'))
    op.create_index('ix_coach_points_report', 'coach_points', ['report_id'])
    op.create_table('coach_feedback',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('point_id', sa.Integer(), sa.ForeignKey('coach_points.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_player_id', sa.Integer(), sa.ForeignKey('players.id', ondelete='CASCADE'), nullable=False),
        sa.Column('verdict', sa.String(12), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("verdict IN ('right', 'wrong', 'badly_put')", name='ck_coach_feedback_verdict'))
    op.create_index('ix_coach_feedback_point', 'coach_feedback', ['point_id'])
    op.create_table('coach_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade():
    op.drop_table('coach_notes')
    op.drop_table('coach_feedback')
    op.drop_table('coach_points')
    op.drop_table('coach_reports')

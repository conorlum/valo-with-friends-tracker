"""replay_match_states: what the upload panel's count says of each match with a stored replay, kept so the match
lists read it with one indexed lookup. Additive: a new table, no foreign keys, no change to an existing table.

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'replay_match_states',
        sa.Column('match_uuid', sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column('done', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('first_step', sa.Text(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recipe', sa.Text(), nullable=False),
        sa.Column('control_revision', sa.Integer(), nullable=False),
        sa.Column('gaps_revision', sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table('replay_match_states')

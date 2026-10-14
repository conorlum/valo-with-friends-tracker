"""replay_uploads.status may be 'held': an upload from a game build the pinned parser doesn't support yet, kept by
the worker and parsed once the parser is updated (replay_worker/server.py, "held"). Only the check constraint
changes; no row does.

Revision ID: 0022
Revises: 0021
"""
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_replay_uploads_status', 'replay_uploads', type_='check')
    op.create_check_constraint('ck_replay_uploads_status', 'replay_uploads',
                               "status IN ('queued', 'parsing', 'held', 'stored', 'failed')")


def downgrade():
    op.execute("UPDATE replay_uploads SET status = 'failed', finished_at = now() WHERE status = 'held'")
    op.drop_constraint('ck_replay_uploads_status', 'replay_uploads', type_='check')
    op.create_check_constraint('ck_replay_uploads_status', 'replay_uploads',
                               "status IN ('queued', 'parsing', 'stored', 'failed')")

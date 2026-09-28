"""replay upload worker job

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-27

Stage 3 (friends-only upload): each `replay_uploads` row remembers the worker's own job id, so
the job page's status poll can ask the worker for the result and store it
(app/services/replay_upload.py). A separate revision, not an edit of 0012, because 0012 ships
with Stage 2 and is applied before this one exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("replay_uploads", sa.Column("worker_job_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("replay_uploads", "worker_job_id")

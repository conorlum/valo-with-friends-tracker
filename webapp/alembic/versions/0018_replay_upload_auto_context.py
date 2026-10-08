"""replay upload auto context

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-07

`replay_uploads.auto_context`: on an automatic re-parse attempt, the replay it was selected for as it was at
that moment (its id, recipe and file hash), the recipe it aims for, and how far the attempt got
(docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, task 3). Null on every upload and manual
re-parse, so no existing row is rewritten.

Additive and nullable, and the ValoMaths demo's table is empty.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("replay_uploads", sa.Column("auto_context", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("replay_uploads", "auto_context")

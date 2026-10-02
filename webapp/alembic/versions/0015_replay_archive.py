"""replay archive

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-01

The .vrf archive on the replay worker (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1):

- `replay_uploads.store_outcome`: what store_replay did with the worker's result, so a `kept_existing`
  upload is shown as such and the right ack can be sent again.
- `replay_uploads.archive_ack`: the worker's answer to that ack; null until it answers, and the web app
  re-sends while it is null (app/services/replay_archive_sync.py).
- `replay_deletions`: one row per match deleted on request (scripts/delete_replay_data.py), the tombstone
  store_replay refuses and the worker is pushed.

Additive: no existing row is touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("replay_uploads", sa.Column("store_outcome", sa.String(length=16), nullable=True))
    op.add_column("replay_uploads", sa.Column("archive_ack", sa.String(length=160), nullable=True))
    op.create_table(
        "replay_deletions",
        sa.Column("match_uuid", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("replay_deletions")
    op.drop_column("replay_uploads", "archive_ack")
    op.drop_column("replay_uploads", "store_outcome")

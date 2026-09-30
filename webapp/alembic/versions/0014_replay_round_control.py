"""replay round control

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-30

Map control's storage (docs/replay-map-control-plan.md, "Storage"; Stage 3): one row per replay
round, written only by scripts/compute_control.py. `status` is 'ok' (with `data`, the served
bytes, and `summary`, the heatmap totals and per-player stats) or 'failed' (with `error`), and
`fingerprint` hashes the row's inputs (app/replays/control_format.py) so a stale row is found and
recomputed.

The key references `replay_rounds` with ON DELETE CASCADE, so a row goes with its round, and a
round goes with its replay (store.py's `_delete` also deletes these rows explicitly, before the
rounds). A re-ingest therefore starts every round of that replay without control.

Additive: no existing row is touched, and the ValoMaths demo gets the empty table (its replay
routes 404 in demo mode).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "replay_round_control",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("summary", sa.LargeBinary(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_round_control_round"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_control_status"),
        sa.CheckConstraint("status <> 'ok' OR (data IS NOT NULL AND summary IS NOT NULL)",
                           name="ck_replay_round_control_ok_has_data"),
    )


def downgrade() -> None:
    op.drop_table("replay_round_control")

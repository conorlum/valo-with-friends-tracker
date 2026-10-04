"""replay gaps

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-02

Timing gaps (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 7): one run row per replay round
and one row per predicted gap or back-shot, written only by scripts/compute_control.py. Both reference
replay_rounds with ON DELETE CASCADE. Additive: the ValoMaths demo gets empty tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_round_gap_runs",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("gaps_revision", sa.SmallInteger(), nullable=False),
        sa.Column("chokes_hash", sa.String(length=16), nullable=True),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", J, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_round_gap_runs_round"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_replay_round_gap_runs_status"),
    )
    op.create_table(
        "replay_gaps",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("seq", sa.SmallInteger(), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("map", sa.String(length=64), nullable=False),
        sa.Column("victim_slot", sa.SmallInteger(), nullable=False),
        sa.Column("victim_side", sa.String(length=8), nullable=True),
        sa.Column("t_open", sa.REAL(), nullable=False),
        sa.Column("t_last_exposed", sa.REAL(), nullable=True),
        sa.Column("t_close", sa.REAL(), nullable=True),
        sa.Column("spot_cell", sa.Integer(), nullable=False),
        sa.Column("victim_cell", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.REAL(), nullable=False),
        sa.Column("angle_deg", sa.REAL(), nullable=False),
        sa.Column("qualified_s", sa.REAL(), nullable=True),
        sa.Column("flicker", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cause", sa.String(length=16), nullable=True),
        sa.Column("cause_detail", J, nullable=True),
        sa.Column("choke_seq", J, nullable=True),
        sa.Column("route", J, nullable=False),
        sa.Column("candidate_slots", J, nullable=False),
        sa.Column("candidate_distances", J, nullable=True),
        sa.Column("checked_at", J, nullable=True),
        sa.Column("stood_at", sa.REAL(), nullable=True),
        sa.Column("stood_by", sa.SmallInteger(), nullable=True),
        sa.Column("shot_at", sa.REAL(), nullable=True),
        sa.Column("shot_by", sa.SmallInteger(), nullable=True),
        sa.Column("killed_at", sa.REAL(), nullable=True),
        sa.Column("killed_by", sa.SmallInteger(), nullable=True),
        sa.Column("victim_won_at", sa.REAL(), nullable=True),
        sa.Column("context", J, nullable=True),
        sa.Column("linked_seq", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["replay_id", "round_number"], ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_gaps_round"),
        sa.CheckConstraint("kind IN ('predicted', 'backshot')", name="ck_replay_gaps_kind"),
    )
    op.create_index("ix_replay_gaps_map_kind", "replay_gaps", ["map", "kind"])


def downgrade() -> None:
    op.drop_index("ix_replay_gaps_map_kind", table_name="replay_gaps")
    op.drop_table("replay_gaps")
    op.drop_table("replay_round_gap_runs")

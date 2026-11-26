"""Aim events: one row per fight-start aim event of a replay round.

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

JSONType = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_aim_events",
        sa.Column("replay_id", sa.Integer(), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("idx", sa.SmallInteger(), primary_key=True),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("gaps_revision", sa.SmallInteger(), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("fight_i", sa.SmallInteger(), nullable=False),
        sa.Column("opponent", sa.SmallInteger(), nullable=False),
        sa.Column("t", sa.Float(), nullable=False),
        sa.Column("cls", sa.String(20), nullable=False),
        sa.Column("err", sa.Float(), nullable=True),
        sa.Column("err_125", sa.Float(), nullable=True),
        sa.Column("err_250", sa.Float(), nullable=True),
        sa.Column("d_m", sa.Float(), nullable=True),
        sa.Column("fine", sa.Boolean(), nullable=False),
        sa.Column("hold", sa.Boolean(), nullable=True),
        sa.Column("opener", sa.String(8), nullable=True),
        sa.Column("gun_class", sa.String(16), nullable=True),
        sa.Column("hp", sa.SmallInteger(), nullable=True),
        sa.Column("enemy_hp", sa.SmallInteger(), nullable=True),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("t_end", sa.Float(), nullable=False),
        sa.Column("on_times", JSONType, nullable=True),
        sa.Column("shot", sa.Float(), nullable=True),
        sa.Column("dmg", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["replay_id", "round_number"],
                                ["replay_rounds.replay_id", "replay_rounds.round_number"],
                                ondelete="CASCADE", name="fk_replay_aim_events_round"),
    )
    op.create_index("ix_replay_aim_events_replay_slot", "replay_aim_events", ["replay_id", "slot"])


def downgrade() -> None:
    op.drop_index("ix_replay_aim_events_replay_slot", table_name="replay_aim_events")
    op.drop_table("replay_aim_events")

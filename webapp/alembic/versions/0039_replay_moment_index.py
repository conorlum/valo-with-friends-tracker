"""replay moment index

Revision ID: 0039
Revises: 0038

Replay search (docs/superpowers/specs): `replay_moment_index`, one row per moment of a linked replay (columns are the
row contract's keys), and `replay_moment_index_state`, one row per replay naming the source key and index revision
its rows were built from. Only creates tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "replay_moment_index",
        sa.Column("replay_id", sa.Integer(), sa.ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("round", sa.SmallInteger(), primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("map", sa.String(length=64), nullable=False),
        sa.Column("played_at", sa.String(length=32), nullable=True),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("attack_team", sa.String(length=8), nullable=True),
        sa.Column("won_team", sa.String(length=8), nullable=True),
        sa.Column("buy1", sa.String(length=8), nullable=True),
        sa.Column("buy2", sa.String(length=8), nullable=True),
        sa.Column("score1", sa.SmallInteger(), nullable=False),
        sa.Column("score2", sa.SmallInteger(), nullable=False),
        sa.Column("planted", sa.Boolean(), nullable=False),
        sa.Column("site", sa.String(length=1), nullable=True),
        sa.Column("plant_t", sa.Float(), nullable=True),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("t", sa.Float(), nullable=False),
        sa.Column("t_end", sa.Float(), nullable=True),
        sa.Column("team", sa.String(length=8), nullable=True),
        sa.Column("slot", sa.SmallInteger(), nullable=True),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("agent", sa.String(length=32), nullable=True),
        sa.Column("other_slot", sa.SmallInteger(), nullable=True),
        sa.Column("other_player_id", sa.Integer(), nullable=True),
        sa.Column("other_agent", sa.String(length=32), nullable=True),
        sa.Column("other_team", sa.String(length=8), nullable=True),
        sa.Column("ability", sa.String(length=96), nullable=True),
        sa.Column("ability_key", sa.String(length=16), nullable=True),
        sa.Column("ult", sa.Boolean(), nullable=False),
        sa.Column("first", sa.Boolean(), nullable=False),
        sa.Column("alive1", sa.SmallInteger(), nullable=True),
        sa.Column("alive2", sa.SmallInteger(), nullable=True),
        sa.Column("alive_slots", J, nullable=False),
        sa.Column("post_plant", sa.Boolean(), nullable=False),
        sa.Column("tags", J, nullable=False),
        sa.Column("weapon", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_replay_moment_index_kind_map", "replay_moment_index", ["kind", "map"])
    op.create_index("ix_replay_moment_index_player_id_kind", "replay_moment_index", ["player_id", "kind"])
    op.create_index("ix_replay_moment_index_agent_kind", "replay_moment_index", ["agent", "kind"])
    op.create_index("ix_replay_moment_index_replay_id", "replay_moment_index", ["replay_id"])
    op.create_table(
        "replay_moment_index_state",
        sa.Column("replay_id", sa.Integer(), sa.ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("replay_moment_index_state")
    op.drop_index("ix_replay_moment_index_replay_id", table_name="replay_moment_index")
    op.drop_index("ix_replay_moment_index_agent_kind", table_name="replay_moment_index")
    op.drop_index("ix_replay_moment_index_player_id_kind", table_name="replay_moment_index")
    op.drop_index("ix_replay_moment_index_kind_map", table_name="replay_moment_index")
    op.drop_table("replay_moment_index")

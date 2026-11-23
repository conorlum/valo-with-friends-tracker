"""Round plant positions and compressed plant statistics.

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "round_plants",
        sa.Column("round_id", sa.Integer(), sa.ForeignKey("rounds.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_key", sa.String(64), nullable=True),
        sa.Column("site", sa.String(1), nullable=False),
        sa.Column("u", sa.Integer(), nullable=False),
        sa.Column("v", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("plant_time", sa.Float(), nullable=True),
        sa.Column("attackers_alive", sa.SmallInteger(), nullable=True),
        sa.Column("defenders_alive", sa.SmallInteger(), nullable=True),
        sa.Column("attackers_won", sa.Boolean(), nullable=True),
        sa.Column("defused", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('tracker', 'replay')", name="ck_round_plants_source"),
    )
    op.create_index("ix_round_plants_match_id", "round_plants", ["match_id"])
    op.create_index("ix_round_plants_asset_key", "round_plants", ["asset_key"])
    op.create_table(
        "plant_stats_cache",
        sa.Column("asset_key", sa.String(64), primary_key=True),
        sa.Column("population", sa.String(64), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plant_stats_cache")
    op.drop_index("ix_round_plants_asset_key", table_name="round_plants")
    op.drop_index("ix_round_plants_match_id", table_name="round_plants")
    op.drop_table("round_plants")

"""Area corrections on coach feedback.

Revision ID: 0033
Revises: 0032
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_PLAYER_FK = "fk_coach_feedback_area_player_id"


def upgrade() -> None:
    op.add_column("coach_feedback", sa.Column("area_name", sa.String(64), nullable=True))
    op.add_column("coach_feedback", sa.Column("area_player_id", sa.Integer(), nullable=True))
    op.create_foreign_key(_PLAYER_FK, "coach_feedback", "players", ["area_player_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(_PLAYER_FK, "coach_feedback", type_="foreignkey")
    op.drop_column("coach_feedback", "area_player_id")
    op.drop_column("coach_feedback", "area_name")

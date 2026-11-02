"""index round_player_stats.match_player_id

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-31

round_player_stats' only index on match_player_id is the unique
(round_id, match_player_id) constraint, which leads with round_id, so every
query filtering by match_player_id alone read the whole table (~827k rows,
~1.8 s each). get_match_summary runs one per match, which made a 2-match
session page take ~4 s. Additive; a plain CREATE INDEX blocks writes to the
table for the few seconds it takes to build.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_round_player_stats_match_player_id", "round_player_stats", ["match_player_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_round_player_stats_match_player_id", table_name="round_player_stats")

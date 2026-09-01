"""add missing indexes on kill_events.round_id and impact_scores.match_player_id

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-01

Session-page load times (~11s on a 3-match session) traced to two FK columns
with no usable index, both hit by every session/match-page query that
filters on them instead of the leading PK column:

  - `impact_scores`'s only index is its composite PK (round_id,
    match_player_id), round_id-leading -- so filtering by match_player_id
    alone (get_match_summary, the session leaderboard, trade stats) forces a
    sequential scan of all 652,730+ rows.
  - `kill_events.round_id` has no index at all (Postgres never auto-indexes
    FK columns) -- every KillEvent-based session stat (post-plant, entry/late
    kills, spike deaths, econ upsets) and every selectinload(Round.kill_events)
    (round-win diagrams, the session preload) sequential-scans the whole table.

Plain (non-CONCURRENTLY) CREATE INDEX, consistent with 0006 already taking
ACCESS EXCLUSIVE for a full table rewrite: this only needs a SHARE lock
(blocks writes, not reads) for a build that's far cheaper than a table
rewrite, and nothing writes to either table during a deploy.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_impact_scores_match_player_id", "impact_scores", ["match_player_id"])
    op.create_index("ix_kill_events_round_id", "kill_events", ["round_id"])


def downgrade() -> None:
    op.drop_index("ix_kill_events_round_id", table_name="kill_events")
    op.drop_index("ix_impact_scores_match_player_id", table_name="impact_scores")

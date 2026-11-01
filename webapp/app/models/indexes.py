"""Indexes declared outside the model files the Impact manifest digests.

app/models/round.py (and match.py, kill_event.py, impact_score.py, player.py)
are in impact_manifest.HASHED_SOURCES: an AST change to one of them, even an
index=True, fails the frozen manifest's check and every ingest is refused.
An index changes no value the scorer reads, so performance-only indexes on
those tables are declared here instead, keeping the metadata in step with the
migrations without a re-freeze.
"""
from sqlalchemy import Index

from app.models.round import RoundPlayerStat

# Migration 0030: the unique (round_id, match_player_id) constraint leads with
# round_id, so a lookup by match_player_id alone was a full table scan.
ix_round_player_stats_match_player_id = Index(
    "ix_round_player_stats_match_player_id", RoundPlayerStat.__table__.c.match_player_id
)

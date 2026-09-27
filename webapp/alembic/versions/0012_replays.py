"""replays

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27

The 2D replay viewer's storage (docs/replay-viewer-plan.md, "Storage: migration 0012_replays"):
`replays` (one per match UUID, with the condenser's `link_inputs`, written once, and the link's
mapping), `replay_rounds` (the gzipped JSON v1 blobs), `replay_players` (the private slot
table: Subject, agent, side group, linked match player) and `replay_uploads` (Stage 3's jobs),
plus `players.riot_subject` (decision 1: kept, Stage 1b finding 15).

`players.riot_subject` is added here only. `app/models/player.py` is a scoring HASHED_SOURCE
(`app/scoring/impact_manifest.py`), so the ORM model is not edited; replay code reads and writes
the column with Core SQL.

A row trigger on `replays` unlinks a replay whose match row is deleted: the FK's `ON DELETE SET
NULL` alone would leave `link_status = 'linked'` and stale link data. It fires only when
`match_id` becomes NULL while the row still says linked (the FK action), not on a link write
that sets both itself.

Additive: every existing table's rows are untouched, and the ValoMaths demo gets the empty
tables (its routes 404 in demo mode).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNLINK_FUNCTION = """
CREATE FUNCTION replays_unlink_deleted_match() RETURNS trigger AS $$
BEGIN
    IF NEW.match_id IS NULL AND OLD.match_id IS NOT NULL AND NEW.link_status = 'linked' THEN
        NEW.link_status := 'unlinked';
        NEW.clock_offset := NULL;
        NEW.kill_map := NULL;
        NEW.db_deaths := NULL;
        NEW.kill_impact := NULL;
        NEW.linked_at := NULL;
        NEW.link_report := COALESCE(NEW.link_report, '{}'::jsonb) || '{"unlinked": "match deleted"}'::jsonb;
        UPDATE replay_players SET match_player_id = NULL WHERE replay_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
UNLINK_TRIGGER = """
CREATE TRIGGER replays_unlink_deleted_match BEFORE UPDATE OF match_id ON replays
FOR EACH ROW EXECUTE FUNCTION replays_unlink_deleted_match()
"""


def upgrade() -> None:
    op.create_table(
        "replays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_uuid", postgresql.UUID(as_uuid=False), nullable=False, unique=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="SET NULL"),
                  nullable=True, unique=True),
        sa.Column("map_name", sa.String(length=64), nullable=False),
        sa.Column("round_count", sa.SmallInteger(), nullable=False),
        sa.Column("format_version", sa.SmallInteger(), nullable=False),
        sa.Column("recipe", sa.String(length=80), nullable=False),
        sa.Column("game_branch", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("link_status", sa.String(length=12), nullable=False),
        sa.Column("link_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("link_report", postgresql.JSONB(), nullable=True),
        sa.Column("clock_offset", sa.REAL(), nullable=True),
        sa.Column("kill_map", postgresql.JSONB(), nullable=True),
        sa.Column("db_deaths", postgresql.JSONB(), nullable=True),
        sa.Column("kill_impact", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('local', 'upload')", name="ck_replays_source"),
        sa.CheckConstraint("link_status IN ('linked', 'unlinked', 'refused')", name="ck_replays_link_status"),
    )
    op.create_table(
        "replay_rounds",
        sa.Column("replay_id", sa.Integer(), sa.ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("round_number", sa.SmallInteger(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        "replay_players",
        sa.Column("replay_id", sa.Integer(), sa.ForeignKey("replays.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("slot", sa.SmallInteger(), primary_key=True),
        sa.Column("subject", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("side_group", sa.CHAR(length=1), nullable=True),
        sa.Column("match_player_id", sa.Integer(), sa.ForeignKey("match_players.id", ondelete="SET NULL"),
                  nullable=True),
        sa.UniqueConstraint("replay_id", "subject", name="uq_replay_players_subject"),
    )
    op.create_table(
        "replay_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("session_key", sa.String(length=64), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_id", sa.Integer(), sa.ForeignKey("replays.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'parsing', 'stored', 'failed')", name="ck_replay_uploads_status"),
    )
    op.create_index("ix_replay_uploads_created_at", "replay_uploads", ["created_at"])
    op.add_column("players", sa.Column("riot_subject", postgresql.UUID(as_uuid=False), nullable=True))
    op.create_unique_constraint("uq_players_riot_subject", "players", ["riot_subject"])
    op.execute(UNLINK_FUNCTION)
    op.execute(UNLINK_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS replays_unlink_deleted_match ON replays")
    op.execute("DROP FUNCTION IF EXISTS replays_unlink_deleted_match()")
    op.drop_constraint("uq_players_riot_subject", "players", type_="unique")
    op.drop_column("players", "riot_subject")
    op.drop_index("ix_replay_uploads_created_at", table_name="replay_uploads")
    op.drop_table("replay_uploads")
    op.drop_table("replay_players")
    op.drop_table("replay_rounds")
    op.drop_table("replays")

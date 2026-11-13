"""Deploy drain ownership and pause history.

Revision ID: 0034
Revises: 0033
"""

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deploy_drains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("superseded", sa.String(128), nullable=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("commit", sa.String(64), nullable=True),
        sa.Column("forced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skip_drain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("waiting_total", sa.Integer(), nullable=True),
        sa.CheckConstraint("phase IN ('draining', 'deploying')", name="ck_deploy_drains_phase"),
        sa.CheckConstraint("outcome IN ('ok', 'failed', 'dry_run', 'expired')", name="ck_deploy_drains_outcome"),
    )
    op.create_index("ux_deploy_drains_open", "deploy_drains", [sa.literal_column("(true)")],
                    unique=True, postgresql_where=sa.text("ended_at IS NULL"),
                    sqlite_where=sa.text("ended_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ux_deploy_drains_open", table_name="deploy_drains")
    op.drop_table("deploy_drains")

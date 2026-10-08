"""control heights

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-05

`control_heights`: a map's height assets, built on the replay worker and stored here
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1). One row per build, with the
`.npz` bytes, the build's report, the matches it was built from and the whole input manifest
(app/replays/height_inputs.py) with its digest; at most one `active` row per map, which is the digest every
round of that map is computed with.

Additive, and empty on both sites when it is created: no map has heights yet, and the ValoMaths demo never will.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "control_heights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("map_name", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(12), nullable=False),
        sa.Column("asset", sa.LargeBinary(), nullable=False),
        sa.Column("report", J, nullable=False),
        sa.Column("match_uuids", J, nullable=False),
        sa.Column("rules", J, nullable=False),
        sa.Column("inputs", J, nullable=False),
        sa.Column("inputs_sha", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'rejected', 'superseded')", name="ck_control_heights_status"),
    )
    op.create_index("ix_control_heights_map_built", "control_heights", ["map_name", "built_at"])
    op.create_index("uq_control_heights_active", "control_heights", ["map_name"], unique=True,
                    postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_index("uq_control_heights_active", table_name="control_heights")
    op.drop_index("ix_control_heights_map_built", table_name="control_heights")
    op.drop_table("control_heights")

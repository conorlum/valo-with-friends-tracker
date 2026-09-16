"""add trade_credit and scoring_version to impact_scores

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-16

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md
("rc3: the owner's lock after measurement, and the replays declared before the
freeze"). Both columns are additive and change no score.

`trade_credit` is B * trade_credit_scale * credit as it enters
`leverage_component`, which is itself not stored -- so without this column the
credit is recoverable only by replaying the frozen code under the frozen
interpreter.

`scoring_version` is the IMPACT_CALCULATION_VERSION that wrote the row, and it
is PROVENANCE ONLY. It cannot police who writes: a checkout whose model does
not know the column can update a row that already says 3 and leave the value
untouched. Which build may write is enforced by the release write gate's
triggers (scripts/sql/release_write_gate.sql), installed outside Alembic so a
downgrade cannot lift the freeze.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (column, value for the rows that already exist). Every stored row was written
# by main's v1 scorer with the credit off, so 0 and 1 are the true history, not
# placeholders.
_NEW_COLUMNS = (("trade_credit", "0"), ("scoring_version", "1"))


def upgrade() -> None:
    for column, existing in _NEW_COLUMNS:
        op.add_column(
            "impact_scores",
            sa.Column(column, sa.SmallInteger(), nullable=False, server_default=existing),
        )
        # The server_default backfilled the existing rows; drop it so the ORM's
        # value is the only source for new rows, matching 0008 and every other
        # SmallInteger column on this table.
        op.alter_column("impact_scores", column, server_default=None)


def downgrade() -> None:
    for column, _ in _NEW_COLUMNS:
        op.drop_column("impact_scores", column)

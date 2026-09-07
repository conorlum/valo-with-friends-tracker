"""add impact_scores.kill_order_bonus, econ_component, econ_pickup

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-07

Part of the Impact scoring rework
(docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md).

Three columns, added ALONGSIDE the existing econ_impact and swing_impact
rather than redefining them -- app/services/impact_eval.py reads those two
columns by name, and silently changing their meaning would invalidate every
stored harness comparison. econ_impact/swing_impact are left in place,
written as 0 by the scorer under the new scheme, and are dropped in a later
migration once nothing reads them (econ spec, "Persistence and rollout").

  - kill_order_bonus: the NET kill_order_bonus (no time/econ/swing
    multiplier), analogous to how time_impact is already the net of
    kill_order_bonus_x_time over kills minus deaths. The evaluation harness
    derives time_delta = time_impact - kill_order_bonus from this column by
    subtraction rather than storing time_delta itself (econ spec section
    8c-i). Backfilled from the existing kill/death event data via the same
    per-kill kill_order_bonus/death_order_bonus values compute_impact_for_
    match already uses -- NOT re-derivable from the other stored columns
    (kill_impact/death_impact bake in the 1.25 damage multiplier and the
    FACTOR_WEIGHTS division, which kill_order_bonus must not carry).
  - econ_component, econ_pickup: the new econ component (section 6) and the
    gated weapon-pickup extension (section 11). Both are 0 for every
    existing row -- the component's real computation is a separate change --
    so no backfill is needed here beyond the column default.

Small, additive columns on a table this size (impact_scores) take a SHARE
lock for the ALTER, consistent with 0007's index additions -- no full
rewrite like 0006's column-unpacking migration needed one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = ("kill_order_bonus", "econ_component", "econ_pickup")


def upgrade() -> None:
    for column in _NEW_COLUMNS:
        op.add_column(
            "impact_scores",
            sa.Column(column, sa.SmallInteger(), nullable=False, server_default="0"),
        )
        # server_default did the backfill for existing rows; drop it so the
        # ORM's Python-side default (also 0) is the only source of truth for
        # new rows, matching every sibling SmallInteger column on this table.
        op.alter_column("impact_scores", column, server_default=None)


def downgrade() -> None:
    for column in _NEW_COLUMNS:
        op.drop_column("impact_scores", column)

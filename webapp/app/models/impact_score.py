from sqlalchemy import ForeignKey, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Keys of the old `breakdown` JSON column that are now plain columns, in the
# order compute_impact_for_match built them. Used by the back-compat property
# below so existing readers keep seeing the original dict shape.
_SCALAR_KEYS = (
    "damage",
    "econ_impact",
    "time_impact",
    "swing_impact",
    "econ_kill",
    "econ_death",
    "clutch_kill",
    "clutch_death",
    "post_plant_kill",
    "post_plant_death",
    "traded_teammate",
    "traded_by_teammate",
)


class ImpactScore(Base):
    """One (round, match_player)'s stored Impact.

    Column meanings under Impact v4 (declaration 12; plan 2026-09-21-impact-v4
    section 2.3, no scoring-column migration -- `scoring_version` from 0010
    says which formula wrote a row):

    - `time_impact` stays the UNWEIGHTED net `K*T` including trade credit. Under
      v4's decided-only time factor T is 1 or 0, so it is net kill-order
      leverage over the kills and deaths that could still change the round.
    - `post_plant_kill` / `post_plant_death` stay the weighted sums of `K*T`
      over kills at or after the raw plant time; match pages sum them
      (services/matches.py). Under v4 a decided event contributes 0 to both.
    - `damage` is unchanged: a per-round total with no timestamps, so damage
      and combat-score assist points after the round was decided stay in.
    - the assists term (D x assists) is not a column; under v4 it counts only
      assists on kills made before the round was decided.
    """

    __tablename__ = "impact_scores"

    # Natural key. The old surrogate `id` was referenced nowhere in app/ or
    # scripts/, and its index was never scanned (0 scans, vs 446,891 on the
    # round/match-player unique index) -- so the two collapse into this one
    # composite PK, keeping the round_id-leading order the hot queries use.
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), primary_key=True)
    match_player_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id"), primary_key=True
    )

    # Integer, not Float: compute_impact_for_match round()s all three, and every
    # one of the 652,730 rows in the live DB was verified integral before the
    # narrowing (range -652..3053).
    kill_impact: Mapped[int] = mapped_column(Integer, nullable=False)
    death_impact: Mapped[int] = mapped_column(Integer, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)

    # Promoted out of the old `breakdown` JSON column. That column stored ~14
    # long string keys verbatim on every row (avg_width 313 B, ~200 MB total,
    # ~85% of it repeated key names). Measured ranges across the full table fit
    # int2 with >10x headroom -- the widest, swing_impact, spans -1036..3041.
    #
    # NOTE: the int4 columns above are declared before this int2 block on
    # purpose. Postgres pads for alignment, so interleaving int4 and int2 costs
    # ~12 extra bytes per row (~8 MB here) for identical data.
    damage: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    econ_impact: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    time_impact: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    swing_impact: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    econ_kill: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    econ_death: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    clutch_kill: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    clutch_death: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    post_plant_kill: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    post_plant_death: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    traded_teammate: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    traded_by_teammate: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # Migration 0008 (docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md,
    # section 8c-i / persistence). Added ALONGSIDE econ_impact and
    # swing_impact rather than redefining them -- the evaluation harness
    # reads those by name, and silently changing their meaning would
    # invalidate every stored comparison. econ_impact/swing_impact stay as
    # written by the live scorer until a later migration drops them.
    #
    # kill_order_bonus: the NET kill_order_bonus (no time/econ/swing
    # multiplier) -- the harness derives time_delta = time_impact -
    # kill_order_bonus from this column rather than storing it twice.
    # econ_component / econ_pickup: written as 0 until the econ component
    # (section 6) and the weapon-pickup extension (section 11) are wired in.
    kill_order_bonus: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    econ_component: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    econ_pickup: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # Migration 0010 (declared 2026-09-16 in the ledger). trade_credit is
    # B * trade_credit_scale * credit as it enters leverage_component, which is
    # itself not stored -- without it the credit can only be recovered by
    # replaying the frozen code.
    #
    # scoring_version is the IMPACT_CALCULATION_VERSION that wrote the row (the
    # rows that predate this migration: 1). It is PROVENANCE, not a guard: a
    # checkout whose model lacks the column can update a row that already says 3
    # and leave it saying 3. Which build may write is enforced by the release
    # write gate's triggers, which read the writer's connection, not the row.
    trade_credit: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    scoring_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # The only part of the old breakdown that stays JSON: the two per-teammate
    # maps of match_player_id -> count. Both are empty on 64.1% of rows, which
    # is stored as NULL rather than two empty objects.
    #   {"t": {"<match_player_id>": n}, "s": {"<match_player_id>": n}}
    trade_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    round: Mapped["Round"] = relationship()

    @property
    def breakdown(self) -> dict:
        """The old JSON column's shape, rebuilt from the columns above.

        Kept so readers written against the JSON column (and
        scripts/snapshot_impact_scores.py, which the migration is verified
        with) keep working unchanged. Read-only: the scorer assigns the
        columns directly.
        """
        detail = self.trade_detail or {}
        # `or 0` covers instances that have not been flushed yet: SQLAlchemy
        # column defaults are applied at INSERT time, not at construction, so
        # a freshly built ImpactScore has None in every scalar.
        view = {key: (getattr(self, key) or 0) for key in _SCALAR_KEYS}
        view["traded_teammate_targets"] = detail.get("t", {})
        view["traded_by_teammate_sources"] = detail.get("s", {})
        return view

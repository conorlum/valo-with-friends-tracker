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

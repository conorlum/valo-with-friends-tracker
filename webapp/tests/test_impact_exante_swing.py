"""The evaluator must compute impact components WITHOUT writing them.

This is the regression test for a data-corruption bug: an earlier design
added a use_realized_swing flag directly to compute_impact_for_match,
which commits unconditionally, so an ex-ante evaluation run would have
overwritten every stored score.

Requires a live database; skips cleanly without one.
"""

import pytest

from app.models import ImpactScore, Round
from app.scoring.impact import build_impact_rows_for_match

# EVERY persisted field, not a subset: the spec asks for field-by-field
# equality, and a drift in e.g. clutch_kill or trade_detail would otherwise
# pass unnoticed while silently changing what the tooling reads.
PERSISTED_FIELDS = (
    "kill_impact", "death_impact", "impact", "damage", "econ_impact",
    "time_impact", "swing_impact", "econ_kill", "econ_death", "clutch_kill",
    "clutch_death", "post_plant_kill", "post_plant_death", "traded_teammate",
    "traded_by_teammate", "trade_detail",
)


class _SpyDB:
    """Wraps a real session and records any write attempt."""

    def __init__(self, inner):
        self.inner = inner
        self.added = []
        self.commits = 0

    def query(self, *a, **kw):
        return self.inner.query(*a, **kw)

    def add(self, obj):
        self.added.append(obj)
        return self.inner.add(obj)

    def commit(self):
        self.commits += 1
        return self.inner.commit()


@pytest.fixture
def db_session():
    try:
        from app.db import SessionLocal

        db = SessionLocal()
        db.query(ImpactScore.round_id).limit(1).scalar()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no database available: {exc}")
    yield db
    db.close()


def _representative_match_ids(db, per_kind: int = 3) -> list[int]:
    """A few regulation matches AND a few overtime ones. Overtime exercises
    the round>24 branches of the swing factor and the side rule, which a
    single arbitrary match would never touch."""
    from sqlalchemy import func

    rows = (
        db.query(Round.match_id, func.max(Round.round_number).label("last"))
        .group_by(Round.match_id)
        .all()
    )
    regulation = [m for m, last in rows if last <= 24][:per_kind]
    overtime = [m for m, last in rows if last > 24][:per_kind]
    if not regulation and not overtime:
        pytest.skip("no matches in the database")
    return regulation + overtime


@pytest.fixture
def db_and_match(db_session):
    ids = _representative_match_ids(db_session, per_kind=1)
    return db_session, ids[0]


def test_builder_writes_nothing(db_and_match):
    db, match_id = db_and_match
    spy = _SpyDB(db)
    rows = build_impact_rows_for_match(spy, match_id)
    assert rows, "expected calculated rows"
    assert spy.added == [], "builder must not add ORM objects"
    assert spy.commits == 0, "builder must not commit"


def test_builder_matches_stored_values(db_session):
    """Field-by-field over several regulation AND overtime matches."""
    checked = 0
    for match_id in _representative_match_ids(db_session):
        rows = build_impact_rows_for_match(db_session, match_id, use_realized_swing=True)
        stored = {
            (s.round_id, s.match_player_id): s
            for s in db_session.query(ImpactScore)
            .join(ImpactScore.round)
            .filter_by(match_id=match_id)
            .all()
        }
        if not stored:
            continue
        for row in rows:
            existing = stored[(row.round_id, row.match_player_id)]
            for field in PERSISTED_FIELDS:
                assert getattr(row, field) == getattr(existing, field), (
                    f"{field} drifted for match {match_id} "
                    f"round {row.round_id}/{row.match_player_id}"
                )
            checked += 1
    assert checked, "no stored scores found to compare against"

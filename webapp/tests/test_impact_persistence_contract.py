"""What a stored impact_scores row must carry, and who says so.

Migration 0010, declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md,
adds `trade_credit` (a term the table otherwise never shows) and
`scoring_version` (which build computed the row).

The persisted-field list is checked against the MODEL's own columns rather than
against any consumer of that list. The backfill's acceptance check and the
scorer read the same tuple, so a field missing from it would be missing from
both the write and the check, and the two would agree with each other while the
column silently kept a stale value.
"""

from app.models import ImpactScore
from app.scoring import impact as impact_module
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match, compute_impact_for_match
from app.scoring.impact_config import ImpactScoringConfig
from tests.buy_disruption_fixtures import build_match, session

KEY_COLUMNS = {"round_id", "match_player_id"}
LOCKED = FormulaWeights(damage=1.0, leverage=2.5, econ=2.5, assists=100.0, trade_credit_scale=1.0)
RC3 = ImpactScoringConfig("rc3-test", enable_econ_component=True, weights=LOCKED,
                          enable_trade_credit=True)

# B1 kills A1, then A2 kills B1 half a second later: A1 is traded, so A1 carries
# credit and the row must show it.
TRADE = {2: [("B1", "A1", 10.0), ("A2", "B1", 10.5)]}


def _scored_match():
    db = session(all_tables=True)
    match, players, _events = build_match(db, kills=TRADE, count_stats=True)
    return db, match, players


def test_persisted_fields_are_exactly_the_models_value_columns():
    columns = {c.name for c in ImpactScore.__table__.columns} - KEY_COLUMNS
    assert set(impact_module.PERSISTED_FIELDS) == columns


def test_a_scored_row_records_the_version_that_computed_it():
    db, match, _players = _scored_match()
    rows = build_impact_rows_for_match(db, match.id, **RC3.build_kwargs())
    assert rows, "the fixture should score at least one player-round"
    assert {row.scoring_version for row in rows} == {impact_module.IMPACT_CALCULATION_VERSION}


def test_the_stored_row_keeps_the_credit_and_the_version():
    db, match, players = _scored_match()
    calculated = {(r.round_id, r.match_player_id): r
                  for r in build_impact_rows_for_match(db, match.id, **RC3.build_kwargs())}

    compute_impact_for_match(db, match.id, config=RC3)

    stored = db.query(ImpactScore).all()
    assert len(stored) == len(calculated)
    for row in stored:
        expected = calculated[(row.round_id, row.match_player_id)]
        assert row.trade_credit == expected.trade_credit
        assert row.scoring_version == impact_module.IMPACT_CALCULATION_VERSION

    traded = next(r for r in stored if r.match_player_id == players["A1"].id and r.trade_credit)
    assert traded.trade_credit > 0, "the traded player's stored credit must be visible in SQL"


def test_the_credit_column_is_zero_when_the_credit_is_off():
    db, match, players = _scored_match()
    off = ImpactScoringConfig("rc3-test-off", enable_econ_component=True, weights=LOCKED,
                              enable_trade_credit=False)

    compute_impact_for_match(db, match.id, config=off)

    assert {row.trade_credit for row in db.query(ImpactScore).all()} == {0}

"""The rc3 artifact contract: a keyed, canonical, hash-identified projection.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md. The
chain K1..K5 compares SHA-256s of these files, so every property that could let
two different scorings produce the same bytes -- or the same scoring produce
different bytes -- is pinned here.
"""

import types

import pytest
from sqlalchemy import text

from app.scoring import impact as impact_module
from app.scoring.impact import CalculatedImpact, FormulaWeights, build_impact_rows_for_match
from scripts import export_impact_artifact as export
from tests._postgres import postgres_session_or_skip
from tests.test_impact_alive_counts import SCORED_ROUND, _build

LOCKED = FormulaWeights(damage=1.0, leverage=2.5, econ=2.5, assists=100.0, trade_credit_scale=1.0)


def _row(**overrides):
    """A stand-in scored row carrying every header field."""
    values = {name: 0 for name in export.COMPARISON_HEADER}
    values["round_id"] = 5
    values["match_player_id"] = 7
    values["trade_detail"] = None
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_comparison_header_is_recorded_not_derived():
    """The header is a literal fixed at commit A. Changing it is a deliberate,
    visible act that breaks the chain -- never a side effect of touching
    PERSISTED_FIELDS."""
    assert export.COMPARISON_HEADER == (
        "round_id", "match_player_id", "kill_impact", "death_impact", "impact", "damage",
        "econ_impact", "time_impact", "swing_impact", "econ_kill", "econ_death", "clutch_kill",
        "clutch_death", "post_plant_kill", "post_plant_death", "traded_teammate",
        "traded_by_teammate", "trade_detail", "kill_order_bonus", "econ_component", "econ_pickup",
        "trade_credit", "leverage_component", "assists_component",
    )


def test_header_carries_every_persisted_field_except_declared_provenance():
    """Survives migration 0010 on purpose: it adds trade_credit (already in the
    header) and scoring_version (excluded, because review rows carry 2 and
    activation rows carry 3)."""
    excluded = set(export.EXCLUDED_FROM_COMPARISON)
    assert set(impact_module.PERSISTED_FIELDS) - excluded <= set(export.COMPARISON_HEADER)
    assert set(export.COMPARISON_HEADER) & excluded == set()


def test_every_header_field_exists_on_a_scored_row():
    fields = {f.name for f in CalculatedImpact.__dataclass_fields__.values()}
    assert set(export.COMPARISON_HEADER) <= fields


def test_sql_null_and_json_null_do_not_collide():
    """The empty field means "no row value". A trade_detail whose JSON value is
    null must stay distinguishable from one that is absent."""
    assert export.render_field(None) == ""
    assert export.render_field({"t": None}) == '{"t":null}'


def test_trade_detail_is_canonical_whatever_the_key_order():
    one = export.render_field({"t": {"12": 1}, "s": {"9": 2}})
    other = export.render_field({"s": {"9": 2}, "t": {"12": 1}})
    assert one == other == '{"s":{"9":2},"t":{"12":1}}'


def test_unexpected_value_types_are_refused():
    with pytest.raises(TypeError):
        export.render_field(True)
    with pytest.raises(TypeError):
        export.render_field(1.5)


def test_hash_is_independent_of_input_order(tmp_path):
    rows = [_row(round_id=9, match_player_id=1, impact=10),
            _row(round_id=2, match_player_id=4, impact=20),
            _row(round_id=2, match_player_id=1, impact=30)]
    forward = export.write_artifact(rows, tmp_path / "a.csv")
    backward = export.write_artifact(list(reversed(rows)), tmp_path / "b.csv")
    assert forward["sha256"] == backward["sha256"]
    assert forward["rows"] == 3
    keys = [(r["round_id"], r["match_player_id"]) for r in export.read_artifact(tmp_path / "a.csv")]
    assert keys == [("2", "1"), ("2", "4"), ("9", "1")]


def test_one_changed_value_changes_the_hash(tmp_path):
    """The whole point of the chain: a single differing player-round must not
    hide inside matching totals."""
    rows = [_row(round_id=1, match_player_id=1, impact=100),
            _row(round_id=1, match_player_id=2, impact=-100)]
    before = export.write_artifact(rows, tmp_path / "before.csv")
    rows[1].impact = -99
    after = export.write_artifact(rows, tmp_path / "after.csv")
    assert before["sha256"] != after["sha256"]


def test_trade_detail_survives_the_round_trip(tmp_path):
    rows = [_row(trade_detail={"t": {"3": 1}, "s": {}}), _row(match_player_id=8, trade_detail=None)]
    export.write_artifact(rows, tmp_path / "a.csv")
    parsed = export.read_artifact(tmp_path / "a.csv")
    assert parsed[0]["trade_detail"] == '{"s":{},"t":{"3":1}}'
    assert parsed[1]["trade_detail"] == ""


def test_read_artifact_rejects_a_foreign_header(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("round_id,match_player_id\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        export.read_artifact(path)


def test_credit_off_differs_from_credit_on_in_exactly_one_flag():
    on = export.build_kwargs_for(weights=LOCKED, credit_on=True)
    off = export.build_kwargs_for(weights=LOCKED, credit_on=False)
    assert {k: v for k, v in on.items() if k != "enable_trade_credit"} == \
           {k: v for k, v in off.items() if k != "enable_trade_credit"}
    assert on["enable_trade_credit"] is True
    assert off["enable_trade_credit"] is False


def test_export_rows_are_the_scorer_s_rows(tmp_path):
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.5, "Vandal")]
    db, match, players = _build(kills)
    kwargs = export.build_kwargs_for(weights=LOCKED, credit_on=True)

    exported = export.export_rows(db, [match.id], kwargs)
    direct = build_impact_rows_for_match(db, match.id, **kwargs)
    assert [export.row_fields(r) for r in sorted(exported, key=export.sort_key)] == \
           [export.row_fields(r) for r in sorted(direct, key=export.sort_key)]

    written = export.write_artifact(exported, tmp_path / "fixture.csv")
    parsed = export.read_artifact(tmp_path / "fixture.csv")
    assert written["rows"] == len(exported) == len(parsed)
    credited = [r for r in parsed if r["trade_credit"] not in ("", "0")]
    assert credited, "the fixture's traded player should carry credit in the artifact"
    scored_round_rows = [r for r in parsed if r["impact"] != "0"]
    assert scored_round_rows, f"round {SCORED_ROUND} should produce nonzero impact"


def test_what_postgres_hands_back_for_each_kind_of_null():
    """C7: pinning the driver's real behaviour rather than an assumption about
    it. A temporary table, so no gated table is touched."""
    db = postgres_session_or_skip()
    try:
        db.execute(text("CREATE TEMP TABLE trade_detail_shapes (id int, trade_detail jsonb)"))
        db.execute(text("INSERT INTO trade_detail_shapes VALUES (1, NULL), (2, 'null'::jsonb), "
                        """(3, '{"t": {"5": null}}'::jsonb)"""))
        rendered = {row[0]: export.render_field(row[1]) for row in db.execute(text(
            "SELECT id, trade_detail FROM trade_detail_shapes ORDER BY id"))}
    finally:
        db.rollback()
        db.close()

    assert rendered[1] == "", "SQL NULL: no trade detail"
    assert rendered[2] == "", "a top-level JSON null means the same, and renders the same"
    assert rendered[3] == '{"t":{"5":null}}', "a null INSIDE the detail stays in its JSON text"

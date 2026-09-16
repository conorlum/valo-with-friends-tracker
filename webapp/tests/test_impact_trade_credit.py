"""Trade credit: a traded player gets a share of the trade kill's leverage.

Declared 2026-09-14 in docs/superpowers/2026-09-07-predeclared-values.md
("trade credit for the traded player: declared before scoring"). The share is
timed from the credited player's own death -- 60% in [0,1) seconds down to 30%
in [5,6) in 6-point steps -- added on top of the trader's kill, and split by
max/sum when one trade kill avenges several teammates. Leverage only, behind
enable_trade_credit.
"""

import pytest

from app.scoring import impact_manifest
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match
from app.scoring.impact_config import ImpactScoringConfig
from tests.test_impact_alive_counts import SCORED_ROUND, _build

B = 3.0
WEIGHTS = FormulaWeights(damage=1.0, leverage=B, econ=2.347, assists=100.0)


def _score(kills, enable_trade_credit=True):
    """{player name: row for SCORED_ROUND}, and {kill index: kill_order_bonus_x_time}."""
    return _score_with_weights(kills, WEIGHTS, enable_trade_credit=enable_trade_credit)


def _score_with_weights(kills, weights, enable_trade_credit=True):
    """Same as _score, but with an explicit FormulaWeights (for trade_credit_scale)."""
    db, match, players = _build(kills)
    x_time = {}

    def observer(round_number, kill_index, kill, context):
        if round_number == SCORED_ROUND:
            x_time[kill_index] = kill["kill_order_bonus_x_time"]

    rows = build_impact_rows_for_match(
        db, match.id, enable_econ_component=True, weights=weights,
        enable_trade_credit=enable_trade_credit, kill_observer=observer,
    )
    names = {mp.id: name for name, mp in players.items()}
    round_ids = {r.round_id for r in rows}
    scored = max(round_ids) - (8 - SCORED_ROUND)
    return {names[r.match_player_id]: r for r in rows if r.round_id == scored}, x_time


def test_the_credit_is_off_by_default():
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.5, "Vandal")]
    db, match, _ = _build(kills)
    assert (build_impact_rows_for_match(db, match.id, enable_econ_component=True, weights=WEIGHTS)
            == build_impact_rows_for_match(db, match.id, enable_econ_component=True, weights=WEIGHTS,
                                           enable_trade_credit=False))
    assert all(r.trade_credit == 0 for r in
               build_impact_rows_for_match(db, match.id, enable_econ_component=True, weights=WEIGHTS))


def test_a_player_traded_inside_a_second_gets_60_percent_of_the_trade_kill():
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.5, "Vandal")]
    on, x_time = _score(kills)
    off, _ = _score(kills, enable_trade_credit=False)

    assert on["A1"].trade_credit == round(B * 0.60 * x_time[1])
    assert on["A1"].leverage_component - off["A1"].leverage_component == pytest.approx(on["A1"].trade_credit, abs=1)
    # Added on top: the trader keeps the whole kill.
    assert on["A2"].leverage_component == off["A2"].leverage_component
    assert on["A2"].trade_credit == 0


@pytest.mark.parametrize("gap, share", [
    (0.0, 0.60), (0.99, 0.60), (1.0, 0.54), (2.5, 0.48), (3.0, 0.42), (4.2, 0.36), (5.9, 0.30), (6.0, 0.0),
])
def test_the_share_steps_down_six_points_a_second(gap, share):
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.0 + gap, "Vandal")]
    on, x_time = _score(kills)
    assert on["A1"].trade_credit == round(B * share * x_time[1])


def test_one_trade_avenging_two_teammates_splits_the_fastest_share():
    kills = [
        ("B1", "A1", 10.0, "Vandal"),   # 3s before the trade: 42%
        ("B1", "A3", 12.0, "Vandal"),   # 1s before the trade: 54%
        ("A2", "B1", 13.0, "Vandal"),
    ]
    on, x_time = _score(kills)
    scale = 0.54 / (0.42 + 0.54)
    assert on["A1"].trade_credit == round(B * 0.42 * scale * x_time[2])
    assert on["A3"].trade_credit == round(B * 0.54 * scale * x_time[2])


def test_no_credit_when_the_killer_dies_to_the_environment():
    kills = [("B1", "A1", 10.0, "Vandal"), (None, "B1", 11.0, "Fall")]
    on, _ = _score(kills)
    assert on["A1"].trade_credit == 0


def test_no_credit_for_a_team_kill_victim():
    kills = [("A1", "A3", 10.0, "Vandal"), ("B1", "A1", 11.0, "Vandal")]
    on, _ = _score(kills)
    assert on["A3"].trade_credit == 0


def test_impact_reconciles_to_its_four_terms_with_the_credit_on():
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.5, "Vandal"), ("B2", "A2", 20.0, "Vandal")]
    on, _ = _score(kills)
    assert on["A1"].trade_credit > 0
    for row in on.values():
        assert row.impact == row.damage + row.leverage_component + row.econ_component + row.assists_component


def test_trade_credit_scale_defaults_to_1_and_changes_nothing():
    assert FormulaWeights().trade_credit_scale == 1.0
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.5, "Vandal")]
    default_scale, _ = _score(kills)
    explicit_scale, _ = _score_with_weights(
        kills, FormulaWeights(damage=1.0, leverage=B, econ=2.347, assists=100.0, trade_credit_scale=1.0),
    )
    assert default_scale["A1"].trade_credit == explicit_scale["A1"].trade_credit


def test_trade_credit_scale_multiplies_the_credit_independent_of_leverage():
    kills = [("B1", "A1", 10.0, "Vandal"), ("A2", "B1", 10.5, "Vandal")]
    full, x_time = _score_with_weights(
        kills, FormulaWeights(damage=1.0, leverage=B, econ=2.347, assists=100.0, trade_credit_scale=1.0),
    )
    halved, _ = _score_with_weights(
        kills, FormulaWeights(damage=1.0, leverage=B, econ=2.347, assists=100.0, trade_credit_scale=0.5),
    )
    doubled, _ = _score_with_weights(
        kills, FormulaWeights(damage=1.0, leverage=B, econ=2.347, assists=100.0, trade_credit_scale=2.0),
    )
    assert halved["A1"].trade_credit == round(B * 0.5 * 0.60 * x_time[1])
    assert doubled["A1"].trade_credit == round(B * 2.0 * 0.60 * x_time[1])
    # The trader's own kill (leverage, not credit) is untouched by the scale.
    assert full["A2"].leverage_component == halved["A2"].leverage_component == doubled["A2"].leverage_component


def test_a_frozen_config_records_the_trade_credit_switch():
    config = ImpactScoringConfig("t", enable_econ_component=True, weights=WEIGHTS, enable_trade_credit=True)
    assert config.build_kwargs()["enable_trade_credit"] is True
    recorded = impact_manifest.config_to_dict(config)
    assert recorded["enable_trade_credit"] is True
    assert impact_manifest.config_from_dict(recorded) == config


def test_a_config_frozen_before_the_switch_existed_reads_as_off():
    recorded = impact_manifest.config_to_dict(ImpactScoringConfig("old", enable_econ_component=True))
    del recorded["enable_trade_credit"]
    assert impact_manifest.config_from_dict(recorded).enable_trade_credit is False

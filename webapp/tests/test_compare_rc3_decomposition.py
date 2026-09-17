"""The rc3 decomposition check agrees with correct scoring and catches broken scoring.

A check that imports the function it checks can only ever agree with it, so the
tests that matter here break the SCORER's trade-credit function and require the
decomposition check to disagree.
"""

import math
from dataclasses import replace


from app.models import RoundPlayerStat
from app.models.match import Team
from app.scoring import impact
from app.scoring.impact import FormulaWeights
from app.scoring.impact_manifest import COMPARATORS, RC3, V2_30_80_BONUS
from scripts import compare_rc3_decomposition as decomposition
from tests.buy_disruption_fixtures import build_match, session

RC3_CONFIG = COMPARATORS[RC3]
RC2_CONFIG = COMPARATORS[V2_30_80_BONUS]

# B1 kills A1; A2 kills B1 half a second later (a trade: A1 is credited). A3 has
# assists, so D*assists is live too.
KILLS = {3: [("B1", "A1", 10.0), ("A2", "B1", 10.5), ("B3", "A4", 30.0)]}


def _match():
    db = session(all_tables=True)
    match, players, _ = build_match(db, "decomp", kills=KILLS, count_stats=True)
    db.query(RoundPlayerStat).filter(RoundPlayerStat.match_player_id == players["A3"].id).update({"assists": 3})
    db.flush()
    return db, match, players


def test_correct_scoring_decomposes_exactly():
    db, match, _ = _match()
    checks, mismatches = decomposition.check_match(db, match.id, RC3_CONFIG, RC2_CONFIG)
    assert checks > 0
    assert mismatches == []


def test_the_fixture_really_exercises_the_credit_and_the_assists():
    """Guards the clean result above against passing on a match with nothing to check."""
    db, match, players = _match()
    rows = impact.build_impact_rows_for_match(db, match.id, **RC3_CONFIG.build_kwargs())
    assert any(r.trade_credit for r in rows if r.match_player_id == players["A1"].id)
    assert any(r.assists_component for r in rows if r.match_player_id == players["A3"].id)


def test_a_scorer_that_doubles_the_credit_is_caught(monkeypatch):
    db, match, _ = _match()
    original = impact._trade_credits_for_round

    def doubled(kills, team_of):
        return {player: 2 * credit for player, credit in original(kills, team_of).items()}

    monkeypatch.setattr(impact, "_trade_credits_for_round", doubled)
    _, mismatches = decomposition.check_match(db, match.id, RC3_CONFIG, RC2_CONFIG)
    assert any("trade credit" in m for m in mismatches)
    assert any("B*leverage" in m for m in mismatches)


def test_a_scorer_that_pays_the_trader_instead_is_caught(monkeypatch):
    db, match, players = _match()
    original = impact._trade_credits_for_round
    victim, trader = players["A1"].id, players["A2"].id

    def to_the_trader(kills, team_of):
        credits = dict(original(kills, team_of))
        if victim in credits:
            credits[trader] = credits.pop(victim)
        return credits

    monkeypatch.setattr(impact, "_trade_credits_for_round", to_the_trader)
    _, mismatches = decomposition.check_match(db, match.id, RC3_CONFIG, RC2_CONFIG)
    assert any(f"player {victim} trade credit" in m for m in mismatches)
    assert any(f"player {trader} trade credit" in m for m in mismatches)


def test_the_independent_rule_splits_one_trade_across_three_avenged_players():
    team_of = {1: Team.TEAM_1, 2: Team.TEAM_1, 3: Team.TEAM_1, 4: Team.TEAM_1, 11: Team.TEAM_2}
    kills = [
        {"killer_match_player_id": 11, "death_match_player_id": 1, "event_time_seconds": 10.0,
         "kill_order_bonus_x_time": 0.0},
        {"killer_match_player_id": 11, "death_match_player_id": 2, "event_time_seconds": 11.0,
         "kill_order_bonus_x_time": 0.0},
        {"killer_match_player_id": 11, "death_match_player_id": 3, "event_time_seconds": 12.0,
         "kill_order_bonus_x_time": 0.0},
        {"killer_match_player_id": 4, "death_match_player_id": 11, "event_time_seconds": 15.5,
         "kill_order_bonus_x_time": 170},
    ]
    credits = decomposition.independent_trade_credits(kills, team_of)
    scale = 0.42 / math.fsum([0.3, 0.36, 0.42])
    assert credits[1] == 0.30 * scale * 170
    assert credits[2] == 0.36 * scale * 170
    assert credits[3] == 0.42 * scale * 170
    assert 4 not in credits, "the trader keeps his own kill and is not credited"


def test_a_self_inflicted_trade_pays_no_credit():
    team_of = {1: Team.TEAM_1, 11: Team.TEAM_2}
    kills = [
        {"killer_match_player_id": 11, "death_match_player_id": 1, "event_time_seconds": 10.0,
         "kill_order_bonus_x_time": 0.0},
        {"killer_match_player_id": 11, "death_match_player_id": 11, "event_time_seconds": 11.0,
         "kill_order_bonus_x_time": 90},
    ]
    assert decomposition.independent_trade_credits(kills, team_of) == {}


def test_a_scorer_that_applies_the_weights_to_the_wrong_terms_is_caught(monkeypatch):
    """Declared defect reinstatement: the leverage weight applied to the econ
    term. rc3 ships B = C = 2.5, where swapping them changes nothing anyone can
    see, so this reinstates the defect under unequal weights."""
    db, match, _ = _match()
    unequal = replace(RC3_CONFIG, weights=FormulaWeights(
        damage=1.0, leverage=2.5, econ=3.5, assists=100.0, trade_credit_scale=1.0))
    real = decomposition.build_impact_rows_for_match

    def swaps_the_weights(database, match_id, **kwargs):
        weights = kwargs["weights"]
        kwargs["weights"] = replace(weights, leverage=weights.econ, econ=weights.leverage)
        return real(database, match_id, **kwargs)

    monkeypatch.setattr(decomposition, "build_impact_rows_for_match", swaps_the_weights)
    _, mismatches = decomposition.check_match(db, match.id, unequal, RC2_CONFIG)

    assert any("C*econ" in m for m in mismatches)
    assert any("B*leverage" in m for m in mismatches)


def test_a_scorer_that_drops_the_assists_term_from_impact_is_caught(monkeypatch):
    """Declared defect reinstatement: D left out of impact. The term is still
    computed and still stored, so only the identity notices."""
    db, match, _ = _match()
    real = decomposition.build_impact_rows_for_match

    def forgets_assists(database, match_id, **kwargs):
        rows = real(database, match_id, **kwargs)
        for row in rows:
            row.impact -= row.assists_component
        return rows

    monkeypatch.setattr(decomposition, "build_impact_rows_for_match", forgets_assists)
    _, mismatches = decomposition.check_match(db, match.id, RC3_CONFIG, RC2_CONFIG)

    assert any("identity" in m for m in mismatches)

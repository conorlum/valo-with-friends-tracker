"""A, B, C as actual parameters of the new formula.

    impact = A*damage + B*(kill_order_bonus x time_factor) + C*econ_component

Today none of the three is a knob: A is the literal 1.25 at impact.py, B is an
implicit 1, and C is ECON_SCALE baked inside econ_component. Nothing can fit
or review a weight that does not exist as a parameter.

C is a multiplier ON TOP of ECON_SCALE, so C = 1.0 means "the declared
anchor" rather than "econ counts for one point". The declared candidate is
therefore FormulaWeights() exactly.

The load-bearing test here is reconciliation: `impact` must equal
`damage + leverage_component + econ_component` exactly, under ANY weights,
or the review tool's totals cannot be trusted.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match

_ids: dict[str, int] = {}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    return sessionmaker(bind=engine)()


def _player(db, name):
    if name not in _ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _ids[name] = p.id
    return _ids[name]


def _match(db):
    """Rounds 5 and 6 -- inside the econ spec's LATE regime, so
    econ_component is actually non-zero and C has something to scale."""
    match = Match(external_id="fw1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"),
                         agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"),
                         agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()

    rounds = {}
    # Rounds 1..8 contiguous: _rounds_since_last_win walks backwards from the
    # round being scored and raises KeyError on any gap, and econ_component
    # reads round N+1, so round 7 needs an 8 to exist.
    for number in range(1, 9):
        rnd = Round(match_id=match.id, round_number=number,
                    outcome="Team A Detonate Win", planted=True, plant_time=30.0,
                    exploded=True, defused=False)
        db.add(rnd)
        db.flush()
        rounds[number] = rnd
        for name, mp in players.items():
            db.add(RoundPlayerStat(
                round_id=rnd.id, match_player_id=mp.id, kills=1, deaths=1,
                assists=0, score=200, loadout=3900 if name.startswith("A") else 2400,
                remaining=1500,
            ))
    for number in (5, 6):
        db.add(KillEvent(round_id=rounds[number].id,
                         killer_match_player_id=players["A1"].id,
                         death_match_player_id=players["B1"].id,
                         weapon="Vandal", event_time_seconds=20.0))
        db.add(KillEvent(round_id=rounds[number].id,
                         killer_match_player_id=players["B2"].id,
                         death_match_player_id=players["A2"].id,
                         weapon="Phantom", event_time_seconds=35.0))
    db.commit()
    return match, players


def _rows(db, match_id, weights=None, econ=True):
    return build_impact_rows_for_match(
        db, match_id, use_realized_swing=True,
        enable_econ_component=econ, weights=weights,
    )


def test_default_weights_reproduce_todays_scoring_exactly():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    for econ in (False, True):
        assert _rows(db, match.id, econ=econ) == _rows(db, match.id, FormulaWeights(), econ=econ)


def test_the_declared_candidate_is_the_default():
    assert (FormulaWeights().damage, FormulaWeights().leverage,
            FormulaWeights().econ) == (1.25, 1.0, 1.0)


def test_impact_reconciles_to_its_three_terms_under_default_weights():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    rows = _rows(db, match.id)
    assert rows, "fixture produced no rows"
    for row in rows:
        assert row.impact == row.damage + row.leverage_component + row.econ_component


def test_impact_reconciles_under_non_default_weights():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    rows = _rows(db, match.id, FormulaWeights(damage=0.8, leverage=2.5, econ=0.3))
    for row in rows:
        assert row.impact == row.damage + row.leverage_component + row.econ_component


def test_the_econ_weight_scales_the_econ_term_and_nothing_else():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    base = {(r.round_id, r.match_player_id): r for r in _rows(db, match.id)}
    doubled = {(r.round_id, r.match_player_id): r
               for r in _rows(db, match.id, FormulaWeights(econ=2.0))}

    assert any(r.econ_component for r in base.values()), (
        "fixture produced no econ_component, so scaling it proves nothing"
    )
    for key, row in base.items():
        other = doubled[key]
        assert other.econ_component == pytest.approx(2 * row.econ_component, abs=1)
        assert other.damage == row.damage
        assert other.leverage_component == row.leverage_component


def test_the_leverage_weight_scales_the_leverage_term_and_nothing_else():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    base = {(r.round_id, r.match_player_id): r for r in _rows(db, match.id)}
    doubled = {(r.round_id, r.match_player_id): r
               for r in _rows(db, match.id, FormulaWeights(leverage=2.0))}

    assert any(r.leverage_component for r in base.values()), (
        "fixture produced no leverage, so scaling it proves nothing"
    )
    for key, row in base.items():
        other = doubled[key]
        assert other.leverage_component == pytest.approx(2 * row.leverage_component, abs=1)
        assert other.damage == row.damage
        assert other.econ_component == row.econ_component


def test_the_damage_weight_scales_the_damage_term():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    base = {(r.round_id, r.match_player_id): r for r in _rows(db, match.id)}
    doubled = {(r.round_id, r.match_player_id): r
               for r in _rows(db, match.id, FormulaWeights(damage=2.5))}

    assert any(r.damage for r in base.values()), "fixture produced no damage"
    for key, row in base.items():
        assert doubled[key].damage == pytest.approx(2 * row.damage, abs=1)


def test_zero_weights_remove_their_terms_entirely():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    rows = _rows(db, match.id, FormulaWeights(damage=0.0, leverage=0.0, econ=0.0))
    for row in rows:
        assert (row.damage, row.leverage_component, row.econ_component) == (0, 0, 0)
        assert row.impact == 0


def test_leverage_component_is_the_weighted_net_that_enters_impact():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    rows = _rows(db, match.id, FormulaWeights(leverage=3.0))
    assert any(r.leverage_component for r in rows), "fixture produced no leverage"
    for row in rows:
        assert row.leverage_component == row.impact - row.damage - row.econ_component


def test_kill_and_death_impact_carry_the_leverage_weight_too():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    base = {(r.round_id, r.match_player_id): r for r in _rows(db, match.id)}
    doubled = {(r.round_id, r.match_player_id): r
               for r in _rows(db, match.id, FormulaWeights(leverage=2.0))}
    assert any(r.death_impact for r in base.values()), "fixture produced no deaths"
    for key, row in base.items():
        # kill_impact = damage + B*kill_sum, so only its non-damage half scales.
        assert doubled[key].kill_impact - row.damage == pytest.approx(
            2 * (row.kill_impact - row.damage), abs=1
        )
        assert doubled[key].death_impact == pytest.approx(2 * row.death_impact, abs=1)

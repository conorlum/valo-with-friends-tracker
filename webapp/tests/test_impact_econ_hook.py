"""The econ component wired into impact.py's formula, behind an
off-by-default flag.

Spec: docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md,
sections 8 (top-level structure), 8c (the old columns stay, written as 0) and
10 (boundary behaviour).

    impact = damage + leverage_component + econ_component

replacing damages + mean(econ, time, swing). swing_impact leaves the formula;
its column stays and is written as 0, because the evaluation harness reads it
by name and silently changing its meaning would invalidate every stored
comparison.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring import econ_component
from app.scoring.impact import build_impact_rows_for_match

_ids = {}

# A stand-in for the section 9 anchor. ECON_SCALE ships as a documented
# placeholder of 1.0, which leaves the component on its raw dimensionless
# 0..1.5 scale -- and since econ_component is persisted as an integer number
# of Impact points, every value at that scale rounds to 0. Patching it here to
# a realistic anchor magnitude is what makes the wiring observable at all, and
# mirrors how the Part 3 tests patch preplant_scalar's constants.
ANCHOR_STANDIN = 300.0


@pytest.fixture(autouse=True)
def _anchor(monkeypatch):
    monkeypatch.setattr(econ_component, "ECON_SCALE", ANCHOR_STANDIN)


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _player(db, name):
    if name not in _ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _ids[name] = p.id
    return _ids[name]


def _match(db, external_id="econ1"):
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"{external_id}A{i}"),
                         agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"{external_id}B{i}"),
                         agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()
    return match, players


def _round(db, match, players, number, *, outcome="Team A Eliminated Win",
           loadouts=None, remainings=None, kills=()):
    rnd = Round(match_id=match.id, round_number=number, outcome=outcome,
                planted=False, plant_time=None, exploded=False, defused=False)
    db.add(rnd)
    db.flush()
    loadouts = loadouts or {}
    remainings = remainings or {}
    for name, mp in players.items():
        db.add(RoundPlayerStat(
            round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0, assists=0,
            score=0, loadout=loadouts.get(name, 3900), remaining=remainings.get(name, 1000),
        ))
    for killer, victim, t in kills:
        db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players[killer].id,
                          death_match_player_id=players[victim].id, weapon="Vandal",
                          event_time_seconds=t))
    return rnd


def _scored(db, match, round_number, **kwargs):
    """Rows for ONE round, keyed by match_player_id.

    Keying the whole match by match_player_id alone silently collapses every
    round into the last one -- which is how an earlier version of this file
    ended up asserting round 7's behaviour against round 8's (final-round)
    zero.
    """
    round_id = (
        db.query(Round)
        .filter_by(match_id=match.id, round_number=round_number)
        .one()
        .id
    )
    return {
        r.match_player_id: r
        for r in build_impact_rows_for_match(db, match.id, **kwargs)
        if r.round_id == round_id
    }


def _build_rounds(db, match, players, upto, overrides):
    """Rounds 1..upto, so _econ_swing_risk_factor's walk back through earlier
    outcomes has something to walk -- that is a pre-existing requirement of
    the scorer, not of the econ component. `overrides` supplies loadouts and
    kills for the rounds a test actually cares about."""
    for number in range(1, upto + 1):
        spec = overrides.get(number, {})
        _round(db, match, players, number,
               loadouts=spec.get("loadouts"), kills=spec.get("kills", ()))
    db.commit()


def _late_regime_match(db, *, next_loadouts, external_id="econ1"):
    """Round 7 (late regime) with two kills by A, then round 8 supplying the
    next-round economy the component reads."""
    match, players = _match(db, external_id)
    _build_rounds(db, match, players, 8, {
        7: {"kills": (("A1", "B1", 10.0), ("A2", "B2", 20.0))},
        8: {"loadouts": next_loadouts},
    })
    return match, players


def test_flag_defaults_off_and_leaves_todays_formula_untouched():
    _ids.clear()
    db = _session()
    match, players = _late_regime_match(db, next_loadouts={f"B{i}": 4900 for i in range(1, 6)})

    default = _scored(db, match, 7)
    explicit_off = _scored(db, match, 7, enable_econ_component=False)

    for pid, row in default.items():
        assert row.impact == explicit_off[pid].impact
        assert row.econ_component == 0


def test_enabling_it_writes_econ_component_and_zeroes_the_retired_columns():
    """Section 8c: econ_impact and swing_impact stay as COLUMNS but leave the
    formula, written as 0. time_impact does not die -- it IS the leverage
    component under the new structure."""
    _ids.clear()
    db = _session()
    match, players = _late_regime_match(db, next_loadouts={f"B{i}": 1000 for i in range(1, 6)})

    rows = _scored(db, match, 7, enable_econ_component=True)
    killer = rows[players["A1"].id]

    assert killer.econ_component != 0
    assert killer.econ_impact == 0
    assert killer.swing_impact == 0
    assert killer.time_impact != 0  # the leverage component survives


def test_the_new_top_level_structure_is_damage_plus_leverage_plus_econ():
    _ids.clear()
    db = _session()
    match, players = _late_regime_match(db, next_loadouts={f"B{i}": 1000 for i in range(1, 6)})

    rows = _scored(db, match, 7, enable_econ_component=True)

    for row in rows.values():
        assert row.impact == row.damage + row.time_impact + row.econ_component


def test_the_component_is_zero_sum_across_the_round():
    _ids.clear()
    db = _session()
    match, players = _late_regime_match(db, next_loadouts={f"B{i}": 1000 for i in range(1, 6)})

    rows = _scored(db, match, 7, enable_econ_component=True)

    assert sum(r.econ_component for r in rows.values()) == 0


def test_a_team_that_removed_nothing_still_absorbs_debits():
    """Team A kills nobody and loses two players to team B: A's credit is 0
    and A's two debits are non-zero."""
    _ids.clear()
    db = _session()
    match, players = _match(db, "econ_debit")
    _build_rounds(db, match, players, 8, {
        7: {"kills": (("B1", "A1", 10.0), ("B2", "A2", 20.0))},
        8: {"loadouts": {f"A{i}": 1000 for i in range(1, 6)}},
    })

    rows = _scored(db, match, 7, enable_econ_component=True)

    assert rows[players["A1"].id].econ_component < 0
    assert rows[players["A2"].id].econ_component < 0
    assert rows[players["B1"].id].econ_component > 0


def test_pistol_and_halftime_rounds_produce_exactly_zero():
    _ids.clear()
    db = _session()
    match, players = _match(db, "econ_bounds")
    _build_rounds(db, match, players, 13, {
        1: {"kills": (("A1", "B1", 10.0),)},
        # Round 2's victim team must be BOUGHT IN for the early regime to
        # fire -- g sends a saving team to exactly zero, which is the whole
        # point of the gate -- and must be left below ZERO_AT in round 3, since
        # f reads next-round scarcity rather than round-N commitment.
        2: {"loadouts": {f"B{i}": 3900 for i in range(1, 6)},
            "kills": (("A1", "B1", 10.0),)},
        3: {"loadouts": {f"B{i}": 1000 for i in range(1, 6)}},
        12: {"kills": (("A1", "B1", 10.0),)},
        13: {"loadouts": {f"B{i}": 1000 for i in range(1, 6)}},
    })

    rows = build_impact_rows_for_match(db, match.id, enable_econ_component=True)
    by_round = {}
    for row in rows:
        by_round.setdefault(row.round_id, []).append(row)

    rounds = {r.round_number: r.id for r in db.query(Round).filter_by(match_id=match.id)}
    assert all(r.econ_component == 0 for r in by_round[rounds[1]])   # pistol
    assert all(r.econ_component == 0 for r in by_round[rounds[12]])  # halftime
    assert any(r.econ_component != 0 for r in by_round[rounds[2]])   # early regime fires


def test_final_round_of_the_match_produces_zero():
    """No next round to deny."""
    _ids.clear()
    db = _session()
    match, players = _match(db, "econ_final")
    _build_rounds(db, match, players, 7, {7: {"kills": (("A1", "B1", 10.0),)}})

    rows = _scored(db, match, 7, enable_econ_component=True)

    assert all(r.econ_component == 0 for r in rows.values())


def test_use_realized_false_produces_exactly_zero_econ_for_every_row():
    """The leakage gate, end to end. Must be exact."""
    _ids.clear()
    db = _session()
    match, players = _late_regime_match(db, next_loadouts={f"B{i}": 1000 for i in range(1, 6)},
                                        external_id="econ_exante")

    rows = _scored(db, match, 7, enable_econ_component=True, use_realized_swing=False)

    assert all(r.econ_component == 0 for r in rows.values())


def test_saving_victim_team_pays_the_baseline_late_but_approximately_zero_early():
    """The regime split, end to end: the same saving victim team scores the
    0.5 baseline in round 7 and ~0 in round 3."""
    _ids.clear()
    db = _session()
    late_match, players = _match(db, "econ_late")
    _build_rounds(db, late_match, players, 8, {
        7: {"loadouts": {f"B{i}": 1000 for i in range(1, 6)},
            "kills": (("A1", "B1", 10.0),)},
        8: {"loadouts": {f"B{i}": 4900 for i in range(1, 6)}},
    })

    early_match, early_players = _match(db, "econ_early")
    _build_rounds(db, early_match, early_players, 4, {
        3: {"loadouts": {f"B{i}": 1000 for i in range(1, 6)},
            "kills": (("A1", "B1", 10.0),)},
        4: {"loadouts": {f"B{i}": 4900 for i in range(1, 6)}},
    })

    late = _scored(db, late_match, 7, enable_econ_component=True)
    early = _scored(db, early_match, 3, enable_econ_component=True)

    assert late[players["A1"].id].econ_component > 0     # the 0.5 baseline
    assert early[early_players["A1"].id].econ_component == 0  # g gates it to zero

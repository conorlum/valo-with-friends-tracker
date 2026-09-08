"""The empirical pre-plant timing modifier (docs/superpowers/2026-09-08-
preplant-empirical-factor-candidate.md), wired behind a new, off-by-default
flag exactly like Task 9's planned model-based scalar
(docs/superpowers/plans/2026-09-07-preplant-time-factor-part3.md). Flag
defaults off everywhere; turning it on for a planted round changes the
pre-plant time_impact (and only that) without touching anything else. Same
in-memory sqlite pattern as test_impact_kill_order_bonus_net.py.

NOTE ON SCOPE: app.scoring.preplant_empirical_factor.empirical_preplant_factor
is an explicit review candidate (its own module docstring: "not connected to
impact.py", non-monotone, uncentered). Wiring it here does not claim Part 3's
monotonicity contract is satisfied or that centering has been solved --
IMPACT_CALCULATION_VERSION is not bumped and the flag defaults to False, so
today's scores are unaffected regardless of what this module computes.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import build_impact_rows_for_match
from app.scoring.preplant_empirical_factor import empirical_preplant_factor


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _player(db, name):
    p = Player(display_name=name)
    db.add(p)
    db.flush()
    return p.id


def _match_with_preplant_kill(db, dt_before_plant=14.0, external_id="emp1"):
    """Round 1 (TEAM_1 attacks). Plant at t=40. A1 (attacker) kills B1 at
    t = 40 - dt_before_plant, i.e. seconds_to_plant == dt_before_plant. No
    trade, no self-kill, no other kills this round."""
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"), agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"), agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=40.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, weapon="Classic",
                      event_time_seconds=40.0 - dt_before_plant))
    db.commit()
    return match, players["A1"].id, players["B1"].id


def test_flag_defaults_off_and_matches_todays_flat_behaviour():
    db = _session()
    match, a1_id, _ = _match_with_preplant_kill(db)

    default_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id)}
    explicit_off_rows = {
        r.match_player_id: r
        for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=False)
    }
    assert default_rows[a1_id].time_impact == explicit_off_rows[a1_id].time_impact
    assert default_rows[a1_id].kill_order_bonus == explicit_off_rows[a1_id].kill_order_bonus


def test_flag_on_at_dt16_matches_the_worked_example():
    # dt=16, attacker: fitted difference ~= +0.0637 -> time_factor ~= 1.191,
    # matching this task's own worked example (100 -> ~119.1).
    db = _session()
    match, a1_id, _ = _match_with_preplant_kill(db, dt_before_plant=16.0)

    rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=True)}
    row = rows[a1_id]
    factor = empirical_preplant_factor(16.0, True, strength=3.0)
    assert factor == pytest.approx(1.191, abs=0.001)
    assert row.time_impact == round(row.kill_order_bonus * factor)


def test_flag_on_changes_the_preplant_kill_relative_to_off():
    db = _session()
    match, a1_id, b1_id = _match_with_preplant_kill(db, dt_before_plant=16.0)

    off_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=False)}
    on_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=True)}

    assert off_rows[a1_id].time_impact != on_rows[a1_id].time_impact
    assert off_rows[b1_id].time_impact != on_rows[b1_id].time_impact


def test_flag_on_with_use_realized_false_still_returns_legacy_flat_value():
    # The leakage gate: empirical_preplant_factor(..., use_realized=False) == 1.0
    # exactly, so ex-ante mode must reproduce today's flat pre-plant factor
    # even with the empirical hook enabled.
    db = _session()
    match, a1_id, _ = _match_with_preplant_kill(db, dt_before_plant=16.0)

    rows = {
        r.match_player_id: r
        for r in build_impact_rows_for_match(
            db, match.id, enable_preplant_empirical=True, use_realized_swing=False,
        )
    }
    row = rows[a1_id]
    assert row.time_impact == row.kill_order_bonus


def test_only_time_impact_moves_not_damage_econ_or_swing():
    db = _session()
    match, a1_id, _ = _match_with_preplant_kill(db, dt_before_plant=16.0)

    off_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=False)}
    on_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=True)}
    off_row, on_row = off_rows[a1_id], on_rows[a1_id]

    assert off_row.kill_order_bonus == on_row.kill_order_bonus
    assert off_row.econ_impact == on_row.econ_impact
    assert off_row.swing_impact == on_row.swing_impact
    assert off_row.damage == on_row.damage
    assert off_row.time_impact != on_row.time_impact


def test_never_planted_round_unaffected_by_the_flag():
    db = _session()
    match = Match(external_id="emp2", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"), agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"), agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team B Eliminated", planted=False, plant_time=None)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, weapon="Classic", event_time_seconds=10.0))
    db.commit()

    off_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=False)}
    on_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id, enable_preplant_empirical=True)}
    assert off_rows[players["A1"].id].time_impact == on_rows[players["A1"].id].time_impact

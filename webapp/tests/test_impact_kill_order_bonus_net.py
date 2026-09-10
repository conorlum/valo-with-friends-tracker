"""Migration 0008 / econ spec section 8c-i: build_impact_rows_for_match now
also computes the NET kill_order_bonus (no time/econ/swing multiplier) per
player-round, so the evaluation harness can derive
time_delta = time_impact - kill_order_bonus by subtraction.

Uses a throwaway in-memory sqlite session rather than the Postgres-only DB
fixture the other impact tests skip without (test_impact_exante_swing.py,
test_impact_reconstruction.py) -- this only needs Match/MatchPlayer/Round/
RoundPlayerStat/KillEvent, none of which use a Postgres-only column type,
so it can run everywhere, including without Docker.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import build_impact_rows_for_match


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _single_kill_round(db):
    """One round (the round-1 pistol, so _econ_swing_risk_factor early-
    returns before ever walking earlier rounds' outcomes), one kill: A1
    kills B1 at 5v5. No plant, no trade, no self-kill -- the simplest
    fixture that isolates the net kill_order_bonus arithmetic from every
    other factor (time_factor is exactly 1 for an unplanted round; the
    traded_factor is exactly 1 with no trade)."""
    match = Match(external_id="m1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()

    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "A1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "B1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()

    round_row = Round(
        match_id=match.id, round_number=1, outcome="Team A Elimination Win",
        planted=False, plant_time=None, exploded=False, defused=False, defuse_time=None,
    )
    db.add(round_row)
    db.flush()

    db.add_all(
        [
            RoundPlayerStat(round_id=round_row.id, match_player_id=a1.id, score=200, kills=1, deaths=0, assists=0, loadout=800, remaining=0),
            RoundPlayerStat(round_id=round_row.id, match_player_id=b1.id, score=0, kills=0, deaths=1, assists=0, loadout=800, remaining=0),
            KillEvent(round_id=round_row.id, killer_match_player_id=a1.id, death_match_player_id=b1.id, weapon="Classic", event_time_seconds=1.0),
        ]
    )
    db.commit()
    return match.id, a1.id, b1.id


_player_ids = {}


def _player(db, name):
    if name not in _player_ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _player_ids[name] = p.id
    return _player_ids[name]


def test_net_kill_order_bonus_is_positive_for_the_killer_and_negative_for_the_victim():
    db = _session()
    _player_ids.clear()
    match_id, a1_id, b1_id = _single_kill_round(db)

    rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match_id)}

    # A single kill at 5v5 -> 4v5 (or 5v4, depending on which index the
    # implementation decrements) is a 150-weight edge either way in the
    # kill-order graph -- both "5v5"->"4v5" and "5v5"->"5v4" are 150.
    assert rows[a1_id].kill_order_bonus == 150
    # No trade, so the victim's death_order_bonus equals the same raw
    # kill_order_bonus (traded_factor == 1) -- the net is its exact negation.
    assert rows[b1_id].kill_order_bonus == -150


def test_net_kill_order_bonus_reflects_the_traded_factor_discount():
    """A1 kills B1 at t=1.0 (5v5 -> 4v5, weight 150). B2 kills A1 at t=3.0
    (4v5 -> 4v4, weight 140).

    B1's death_order_bonus is discounted because B1's KILLER (A1) was
    traded back inside the window: trade_time = 3.0 - 1.0 = 2.0, which the
    2026-09-10 schedule charges at 0.17 (the 2-3s bucket), so B1's
    death_order_bonus = 150 * 0.17 = 25.5 -> net kill_order_bonus -26.
    (Before that schedule this was factor 2.0/10 = 0.2 and a net of -30.)
    A1's own death (to B2) is not itself traded (B2 lives past the
    round in this fixture), so A1's death_order_bonus = 140 * 1 = 140,
    giving A1 a net of 150 (as killer) - 140 (as victim) = 10. B2 has no
    death this round: net = 140 (as killer) - 0 = 140."""
    db = _session()
    _player_ids.clear()
    match = Match(external_id="m2", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "A1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "B1"), agent="Sova", team=Team.TEAM_2)
    b2 = MatchPlayer(match_id=match.id, player_id=_player(db, "B2"), agent="Omen", team=Team.TEAM_2)
    db.add_all([a1, b1, b2])
    db.flush()
    round_row = Round(
        match_id=match.id, round_number=1, outcome="Team B Elimination Win",
        planted=False, plant_time=None, exploded=False, defused=False, defuse_time=None,
    )
    db.add(round_row)
    db.flush()
    db.add_all(
        [
            RoundPlayerStat(round_id=round_row.id, match_player_id=a1.id, score=200, kills=1, deaths=1, assists=0, loadout=800, remaining=0),
            RoundPlayerStat(round_id=round_row.id, match_player_id=b1.id, score=0, kills=0, deaths=1, assists=0, loadout=800, remaining=0),
            RoundPlayerStat(round_id=round_row.id, match_player_id=b2.id, score=200, kills=1, deaths=0, assists=0, loadout=800, remaining=0),
            KillEvent(round_id=round_row.id, killer_match_player_id=a1.id, death_match_player_id=b1.id, weapon="Classic", event_time_seconds=1.0),
            KillEvent(round_id=round_row.id, killer_match_player_id=b2.id, death_match_player_id=a1.id, weapon="Classic", event_time_seconds=3.0),
        ]
    )
    db.commit()

    rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id)}

    assert rows[a1.id].kill_order_bonus == 10
    assert rows[b1.id].kill_order_bonus == -26
    assert rows[b2.id].kill_order_bonus == 140

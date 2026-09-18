"""The DISPLAYED trade count uses the same 6s window as the scoring discount.

`traded_teammate` / `traded_by_teammate` are persisted and served on the match
page (routers/matches.py). They were a plain 10-second boolean while the
discount ramped over 10s, so a 9.5s "trade" was shown identically to a 0.2s
one. The project owner's 2026-09-10 ruling closes the window at 6s; this pins
that the displayed count moved with it.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import build_impact_rows_for_match


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    return sessionmaker(bind=engine)()


def _match_with_trade_at(db, gap, external_id):
    """A1 kills B1 at t=1.0; B2 kills A1 at t=1.0+gap, avenging B1."""
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Bind")
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
    db.add_all([
        RoundPlayerStat(round_id=round_row.id, match_player_id=a1.id, score=200, kills=1, deaths=1, assists=0, loadout=800, remaining=0),
        RoundPlayerStat(round_id=round_row.id, match_player_id=b1.id, score=0, kills=0, deaths=1, assists=0, loadout=800, remaining=0),
        RoundPlayerStat(round_id=round_row.id, match_player_id=b2.id, score=200, kills=1, deaths=0, assists=0, loadout=800, remaining=0),
        KillEvent(round_id=round_row.id, killer_match_player_id=a1.id, death_match_player_id=b1.id, weapon="Classic", event_time_seconds=1.0),
        KillEvent(round_id=round_row.id, killer_match_player_id=b2.id, death_match_player_id=a1.id, weapon="Classic", event_time_seconds=1.0 + gap),
    ])
    db.commit()
    return match.id, b1.id, b2.id


_player_ids = {}


def _player(db, name):
    if name not in _player_ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _player_ids[name] = p.id
    return _player_ids[name]


def _counts(gap, external_id):
    db = _session()
    _player_ids.clear()
    match_id, b1_id, b2_id = _match_with_trade_at(db, gap, external_id)
    rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match_id)}
    return rows[b2_id].traded_teammate, rows[b1_id].traded_by_teammate


def test_a_three_second_trade_is_counted():
    traded_teammate, traded_by_teammate = _counts(3.0, "inside")
    assert traded_teammate == 1
    assert traded_by_teammate == 1


def test_a_seven_second_gap_is_not_counted_as_a_trade():
    """Inside the OLD 10s window, outside the declared 6s one."""
    traded_teammate, traded_by_teammate = _counts(7.0, "outside")
    assert traded_teammate == 0
    assert traded_by_teammate == 0

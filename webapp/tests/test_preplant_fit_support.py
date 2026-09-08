"""extract_preplant_observations_with_factors: kill_order_bonus and
traded_factor must exactly match impact.py's own _kill_order_bonus/
_traded_factor, aligned index-for-index with the observation list."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.preplant_fit_support import extract_preplant_observations_with_factors


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


def test_kill_order_bonus_and_trade_match_hand_computation():
    """Round 1 (TEAM_1 attacks), planted far out at t=100 so both kills stay
    pre-plant:
      t=10: A1 kills B1              (5v5 -> 4v5, weight 150; A1 traded at t=12)
      t=12: B3 kills A1 (the trade)  (4v5 -> 4v4, weight 140; untraded)
    """
    db = _session()
    match = Match(external_id="fs1", source=MatchSource.SCRAPED, map_name="Bind")
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
                planted=True, plant_time=100.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, event_time_seconds=10.0, weapon="Vandal"))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["B3"].id,
                      death_match_player_id=players["A1"].id, event_time_seconds=12.0, weapon="Vandal"))
    db.commit()

    obs, kobs, trades = extract_preplant_observations_with_factors(db)

    assert len(obs) == len(kobs) == len(trades) == 2

    kill0 = next(i for i, o in enumerate(obs) if o.dt == 90.0)   # t=10, plant=100
    kill1 = next(i for i, o in enumerate(obs) if o.dt == 88.0)   # t=12, plant=100

    assert obs[kill0].adv == 0
    assert obs[kill0].exact_state == "5v5"
    assert obs[kill0].is_attacker is True
    assert kobs[kill0] == 150.0
    assert trades[kill0] == 0.2  # traded back 2s later, of a 10s window

    assert obs[kill1].adv == -1
    assert obs[kill1].exact_state == "4v5"
    assert obs[kill1].is_attacker is False  # killer B3 is TEAM_2, the defender
    assert kobs[kill1] == 140.0
    assert trades[kill1] == 1.0  # never traded back

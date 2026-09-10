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
    assert trades[kill0] == 0.17  # traded back 2s later -> the 2-3s bucket

    assert obs[kill1].adv == -1
    assert obs[kill1].exact_state == "4v5"
    assert obs[kill1].is_attacker is False  # killer B3 is TEAM_2, the defender
    assert kobs[kill1] == 140.0
    assert trades[kill1] == 1.0  # never traded back


def test_replay_policy_matches_the_scorer_on_selfkills_and_resurrections():
    """The alive-count replay must mirror impact.py exactly.

    This is the case the old code got wrong in BOTH directions:
      - a self-kill left the counts untouched (the scorer decrements -- the
        team really is a player down),
      - a resurrection decremented (the scorer skips it -- the player is back).

    Measured on the full data, 15.06% of rounds contain at least one of the
    two, and once a round diverges every later kill in it is filed under the
    wrong state. `exact_state`/`adv` are the standardization keys for the
    fitted curve, so a wrong state reweights the estimate rather than merely
    mislabelling a row -- which is why this is pinned.

    Round 1 (TEAM_1 attacks), planted at t=100 so every kill stays pre-plant:
      t=10  A1 kills A2   TEAMKILL   -> TEAM_1 drops to 4    (old: stayed 5)
      t=20  A3 kills B1              -> seen at 4v5          (old: 5v5)
      t=30  B2 kills A4   RESURRECTED (A4 kills again later)
                                      -> no decrement        (old: decremented)
      t=40  A4 kills B3              -> seen at 4v4          (old: 4v4 too, but
                                         only because two errors cancelled)
    """
    db = _session()
    match = Match(external_id="fs2", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"), agent="Reyna", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"), agent="Sage", team=Team.TEAM_2)
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
    for killer, victim, t in (("A1", "A2", 10.0), ("A3", "B1", 20.0),
                              ("B2", "A4", 30.0), ("A4", "B3", 40.0)):
        db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players[killer].id,
                         death_match_player_id=players[victim].id,
                         event_time_seconds=t, weapon="Vandal"))
    db.commit()

    obs, _kobs, _trades = extract_preplant_observations_with_factors(db)

    # The teamkill produces no observation; the other three do.
    assert len(obs) == 3
    by_dt = {o.dt: o for o in obs}

    # t=20, after the teamkill cost TEAM_1 a player. Old policy said 5v5 / 0.
    assert by_dt[80.0].exact_state == "4v5"
    assert by_dt[80.0].adv == -1

    # t=30. Old policy said 4v5 / -1 (it had not yet spent the teamkill).
    assert by_dt[70.0].exact_state == "4v4"
    assert by_dt[70.0].adv == 0

    # t=40, after A4's death was recognised as a resurrection and NOT charged.
    assert by_dt[60.0].exact_state == "4v4"
    assert by_dt[60.0].adv == 0

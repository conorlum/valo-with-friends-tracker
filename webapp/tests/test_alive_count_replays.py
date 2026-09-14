"""Every replay that re-walks a round's alive counts uses the scorer's own walk
(impact._alive_before_each_kill), so fits and the refit service see the same
states scoring does. One round exercises the rules those replays each used to
hand-roll differently: an absent player, and a utility kill after death.

Round 1, TEAM_1 attacks, planted at t=50. B5 disconnected (all-zero row).
  t=10  A1 kills B1  Vandal                 TEAM_2 in the round: 4 -> 3
  t=12  B1 kills A2  Showstopper (B1 dead)  not a revive       TEAM_1 5 -> 4
  t=20  A3 kills B2  Vandal       seen at 4v3 (killer's side first)
  t=60  A4 kills B3  Vandal       post-plant: attackers 4, defenders 2
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.postplant_factor import extract_postplant_kills
from app.scoring.postplant_value_table import extract_postplant_round_seconds
from app.scoring.preplant_fit_support import extract_preplant_observations_with_factors
from app.scoring.preplant_time_model import extract_preplant_observations
from app.services.kill_order_leverage import state_visits_for_match


def _round():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    db = sessionmaker(bind=engine)()
    match = Match(external_id="replay", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for side, team in (("A", Team.TEAM_1), ("B", Team.TEAM_2)):
        for i in range(1, 6):
            player = Player(display_name=f"{side}{i}")
            db.add(player)
            db.flush()
            players[f"{side}{i}"] = MatchPlayer(match_id=match.id, player_id=player.id, agent="Raze", team=team)
            db.add(players[f"{side}{i}"])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=50.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for name, mp in players.items():
        stats = (dict(score=0, kills=0, deaths=0, assists=0, loadout=0) if name == "B5"
                 else dict(score=100, kills=0, deaths=0, assists=0, loadout=3900))
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, remaining=0, **stats))
    for killer, victim, seconds, weapon in (("A1", "B1", 10.0, "Vandal"), ("B1", "A2", 12.0, "Showstopper"),
                                            ("A3", "B2", 20.0, "Vandal"), ("A4", "B3", 60.0, "Vandal")):
        db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players[killer].id,
                         death_match_player_id=players[victim].id, weapon=weapon, event_time_seconds=seconds))
    db.commit()
    return db, match


def test_preplant_time_model_sees_the_scorers_states():
    db, _ = _round()
    by_dt = {o.dt: o.exact_state for o in extract_preplant_observations(db)}
    assert by_dt[30.0] == "4v3"


def test_preplant_fit_support_sees_the_scorers_states():
    db, _ = _round()
    observations, _, _ = extract_preplant_observations_with_factors(db)
    assert {o.dt: o.exact_state for o in observations}[30.0] == "4v3"


def test_postplant_factor_sees_the_scorers_states():
    db, _ = _round()
    (kill,) = extract_postplant_kills(db)
    assert (kill.attackers_alive, kill.defenders_alive) == (4, 2)


def test_postplant_value_table_sees_the_scorers_states():
    db, _ = _round()
    rows = {r.t: (r.attackers_alive, r.defenders_alive) for r in extract_postplant_round_seconds(db)}
    assert rows[0] == (4, 2)    # at the plant: B5 absent, B1 and B2 dead
    assert rows[10] == (4, 1)   # after A4 kills B3 at t=60


def test_state_visits_start_from_the_players_in_the_round():
    db, match = _round()
    team_1_view = [(v.own, v.opp) for v in state_visits_for_match(db, match.id) if v.won]
    assert team_1_view == [(5, 4), (5, 3), (4, 3), (4, 2), (4, 1)]

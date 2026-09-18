"""Shared sqlite match builder for the buy-disruption integration tests."""
from collections import defaultdict

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(JSONB, "sqlite")
def _jsonb_as_sqlite_json(element, compiler, **kw):
    # impact_scores.trade_detail is JSONB in Postgres; sqlite stores it as JSON.
    return "JSON"

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team

NAMES = [f"A{i}" for i in range(1, 6)] + [f"B{i}" for i in range(1, 6)]


def session(all_tables=False, url="sqlite:///:memory:"):
    engine = create_engine(url)
    if all_tables:
        Base.metadata.create_all(engine)
    else:
        from app.models import ImpactScore
        Base.metadata.create_all(engine, tables=[
            Player.__table__, Match.__table__, MatchPlayer.__table__, Round.__table__,
            RoundPlayerStat.__table__, KillEvent.__table__, ImpactScore.__table__,
        ])
    return sessionmaker(bind=engine)()


def build_match(db, external_id="bd1", *, upto=8, kills=None, loadouts=None, remainings=None,
                outcomes=None, scores=None, weapons=None, planted=None, count_stats=False):
    """Rounds 1..upto, 5v5, every outcome a Team A win unless overridden.

    kills:       {round: [(killer_name | None, victim_name | None, t), ...]}
    loadouts:    {round: {name: value}}   (default 3900)
    remainings:  {round: {name: value}}   (default 1000)
    weapons:     {round: [weapon per kill, in the order of `kills`]}   (default "Vandal")
    planted:     {round: bool}   (default False)
    count_stats: when True, each stat row's kills/deaths count that round's kill events;
                 the default False keeps the zeros every existing test was derived with.
    Returns (match, players by name, {round: [KillEvent.id, ...]}).
    """
    kills, loadouts, remainings = kills or {}, loadouts or {}, remainings or {}
    outcomes, scores = outcomes or {}, scores or {}
    weapons, planted = weapons or {}, planted or {}
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for name in NAMES:
        player = Player(display_name=f"{external_id}-{name}")
        db.add(player)
        db.flush()
        mp = MatchPlayer(match_id=match.id, player_id=player.id,
                         agent="Jett" if name.startswith("A") else "Sova",
                         team=Team.TEAM_1 if name.startswith("A") else Team.TEAM_2)
        db.add(mp)
        players[name] = mp
    db.flush()
    event_ids = {}
    for number in range(1, upto + 1):
        rnd = Round(match_id=match.id, round_number=number,
                    outcome=outcomes.get(number, "Team A Elimination Win"),
                    planted=planted.get(number, False), plant_time=None, exploded=False, defused=False)
        db.add(rnd)
        db.flush()
        killed, died = defaultdict(int), defaultdict(int)
        for killer, victim, _t in kills.get(number, ()):
            if killer:
                killed[killer] += 1
            if victim:
                died[victim] += 1
        for name, mp in players.items():
            db.add(RoundPlayerStat(
                round_id=rnd.id, match_player_id=mp.id,
                kills=killed[name] if count_stats else 0,
                deaths=died[name] if count_stats else 0, assists=0,
                score=scores.get(number, {}).get(name, 0),
                loadout=loadouts.get(number, {}).get(name, 3900),
                remaining=remainings.get(number, {}).get(name, 1000),
            ))
        ids = []
        round_weapons = weapons.get(number, [])
        for index, (killer, victim, t) in enumerate(kills.get(number, ())):
            weapon = round_weapons[index] if index < len(round_weapons) else "Vandal"
            event = KillEvent(round_id=rnd.id,
                              killer_match_player_id=players[killer].id if killer else None,
                              death_match_player_id=players[victim].id if victim else None,
                              weapon=weapon, event_time_seconds=t)
            db.add(event)
            db.flush()
            ids.append(event.id)
        event_ids[number] = ids
    db.commit()
    return match, players, event_ids


def round_id(db, match, number):
    return db.query(Round).filter_by(match_id=match.id, round_number=number).one().id


def rows_for_round(rows, rid):
    return {r.match_player_id: r for r in rows if r.round_id == rid}


def stat(db, match, players, number, name):
    return (db.query(RoundPlayerStat)
            .filter_by(round_id=round_id(db, match, number), match_player_id=players[name].id).one())

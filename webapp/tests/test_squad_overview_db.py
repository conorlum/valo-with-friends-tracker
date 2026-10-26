"""get_squad_overview's DB load (SQLite): the shared-round numbers it builds, and a query count that doesn't
grow with the number of matches."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (AccountGroup, AccountGroupMember, Friendship, ImpactScore, KillEvent, Match, MatchPlayer,
                        Player, Round, RoundPlayerStat)
from app.models.match import MatchSource, Team
from app.services.squad import get_squad_overview

TABLES = [AccountGroup.__table__, AccountGroupMember.__table__, Player.__table__, Match.__table__,
          MatchPlayer.__table__, Friendship.__table__, Round.__table__, RoundPlayerStat.__table__,
          KillEvent.__table__, ImpactScore.__table__]
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_as_sqlite_json(element, compiler, **kw):
    # impact_scores.trade_detail is JSONB in Postgres; sqlite stores it as JSON.
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    session.info["engine"] = engine
    yield session
    session.close()


def make_people(db):
    viewer, friend, enemy1, enemy2 = (Player(display_name=f"{n}#{uuid.uuid4().hex[:4]}")
                                      for n in ("viewer", "friend", "enemy1", "enemy2"))
    db.add_all([viewer, friend, enemy1, enemy2])
    db.flush()
    db.add(Friendship(owner_player_id=viewer.id, friend_player_id=friend.id))
    db.commit()
    return viewer, friend, enemy1, enemy2


def make_match(db, people, n):
    """Two rounds. Round 1: the friend dies, the viewer wins it alone 1v2 (a clutch). Round 2: lost, no kills."""
    viewer, friend, enemy1, enemy2 = people
    match = Match(external_id=uuid.uuid4().hex, source=MatchSource.SCRAPED, map_name="Ascent",
                  played_at=T0 + timedelta(hours=n), team1_rounds_won=13, team2_rounds_won=5)
    db.add(match)
    db.flush()
    mps = [MatchPlayer(match_id=match.id, player_id=p.id, agent="Jett", team=team)
           for p, team in ((viewer, Team.TEAM_1), (friend, Team.TEAM_1), (enemy1, Team.TEAM_2), (enemy2, Team.TEAM_2))]
    db.add_all(mps)
    db.flush()
    viewer_mp, friend_mp, enemy1_mp, _ = mps
    won = Round(match_id=match.id, round_number=1, outcome="Team A wins")
    lost = Round(match_id=match.id, round_number=2, outcome="Team B wins")
    db.add_all([won, lost])
    db.flush()
    db.add_all([
        KillEvent(round_id=won.id, killer_match_player_id=enemy1_mp.id, death_match_player_id=friend_mp.id,
                  weapon="Vandal", event_time_seconds=10.0),
        KillEvent(round_id=won.id, killer_match_player_id=viewer_mp.id, death_match_player_id=enemy1_mp.id,
                  weapon="Vandal", event_time_seconds=20.0),
    ])
    for round_row, viewer_kills, friend_deaths in ((won, 1, 1), (lost, 0, 0)):
        for mp, kills, deaths in ((viewer_mp, viewer_kills, 0), (friend_mp, 0, friend_deaths)):
            db.add(RoundPlayerStat(round_id=round_row.id, match_player_id=mp.id, kills=kills, deaths=deaths))
            db.add(ImpactScore(round_id=round_row.id, match_player_id=mp.id, kill_impact=10 * kills,
                               death_impact=5 * deaths, impact=10 * kills - 5 * deaths))
    db.commit()


def count_queries(db, fn):
    queries = []
    listener = lambda *args: queries.append(args[2])  # noqa: E731
    event.listen(db.info["engine"], "before_cursor_execute", listener)
    try:
        result = fn()
    finally:
        event.remove(db.info["engine"], "before_cursor_execute", listener)
    return result, len(queries)


def test_shared_rounds_built_from_the_batched_load(db):
    people = make_people(db)
    for n in range(3):
        make_match(db, people, n)

    overview = get_squad_overview(db, people[0].id, match_limit=None)

    [pair] = overview.pairs
    assert pair.friend_player_id == people[1].id
    assert pair.matches_together == 3
    assert pair.rounds_together == 6
    assert pair.win_rate_together == 0.5
    assert pair.clutches_together == 3
    assert pair.kill_differential_together == 3 * (1 - 1)
    # Round 1 per match: viewer 10 kill impact, friend -5 death impact; round 2 is all zeros.
    assert pair.avg_round_win_impact_together == pytest.approx(3 * (10 - 5) / 6)


def test_query_count_does_not_grow_with_matches(db):
    people = make_people(db)
    viewer_id = people[0].id
    make_match(db, people, 0)
    db.expunge_all()
    _, one_match = count_queries(db, lambda: get_squad_overview(db, viewer_id, match_limit=None))

    people = [db.merge(p) for p in people]
    for n in range(1, 6):
        make_match(db, people, n)
    db.expunge_all()
    overview, six_matches = count_queries(db, lambda: get_squad_overview(db, viewer_id, match_limit=None))

    assert overview.pairs[0].matches_together == 6
    assert six_matches == one_match

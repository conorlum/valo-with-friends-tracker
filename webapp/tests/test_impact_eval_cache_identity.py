"""Cache identity must notice an EDIT, not just an added match.

Code review finding 12: the identity hashed match ids plus two counts, so
correcting a kill time, a loadout or a round winner left every field
unchanged and a stale cache stayed 'valid'.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.services.impact_eval_cache import _source_revision


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    return sessionmaker(bind=engine)()


def _seed(db):
    p1, p2 = Player(display_name="A"), Player(display_name="B")
    db.add_all([p1, p2])
    db.flush()
    match = Match(external_id="m", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a = MatchPlayer(match_id=match.id, player_id=p1.id, agent="Jett", team=Team.TEAM_1)
    b = MatchPlayer(match_id=match.id, player_id=p2.id, agent="Sova", team=Team.TEAM_2)
    db.add_all([a, b])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Eliminated Win",
                planted=False)
    db.add(rnd)
    db.flush()
    db.add_all([
        RoundPlayerStat(round_id=rnd.id, match_player_id=a.id, kills=1, deaths=0,
                        assists=0, score=200, loadout=3900, remaining=100),
        RoundPlayerStat(round_id=rnd.id, match_player_id=b.id, kills=0, deaths=1,
                        assists=0, score=0, loadout=3900, remaining=100),
        KillEvent(round_id=rnd.id, killer_match_player_id=a.id,
                  death_match_player_id=b.id, weapon="Vandal", event_time_seconds=20.0),
    ])
    db.commit()
    return rnd


def test_editing_a_kill_time_changes_the_source_revision():
    db = _session()
    _seed(db)
    before = _source_revision(db)

    db.execute(text("UPDATE kill_events SET event_time_seconds = 21.0"))
    db.commit()

    assert _source_revision(db) != before


def test_editing_a_loadout_changes_the_source_revision():
    db = _session()
    _seed(db)
    before = _source_revision(db)

    db.execute(text("UPDATE round_player_stats SET loadout = 1000"))
    db.commit()

    assert _source_revision(db) != before


def test_editing_a_round_winner_changes_the_source_revision():
    """The outcome decides every label, so this is the edit that matters most
    and the one an id-and-count identity was least able to see."""
    db = _session()
    _seed(db)
    before = _source_revision(db)

    db.execute(text("UPDATE rounds SET outcome = 'Team B Eliminated Win'"))
    db.commit()

    assert _source_revision(db) != before


def test_an_untouched_database_keeps_the_same_revision():
    db = _session()
    _seed(db)

    assert _source_revision(db) == _source_revision(db)

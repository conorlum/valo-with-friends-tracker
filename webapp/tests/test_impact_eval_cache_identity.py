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


# --------------------------------------------------------------------------
# Review finding 10 -- the identity must see the rows the replay consumes
# --------------------------------------------------------------------------

from app.services.impact_eval_cache import _database_identity  # noqa: E402


def _identity_session():
    """A richer fixture than _seed: a PLANTED round with a plant_time, and
    two kills at different times, so the two holes finding 10 names can
    actually be exercised."""
    db = _session()
    p1, p2 = Player(display_name="C"), Player(display_name="D")
    db.add_all([p1, p2])
    db.flush()
    match = Match(external_id="rev", source=MatchSource.SCRAPED, map_name="Haven")
    db.add(match)
    db.flush()
    a = MatchPlayer(match_id=match.id, player_id=p1.id, agent="Jett", team=Team.TEAM_1)
    b = MatchPlayer(match_id=match.id, player_id=p2.id, agent="Sova", team=Team.TEAM_2)
    db.add_all([a, b])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=30.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    db.add_all([
        RoundPlayerStat(round_id=rnd.id, match_player_id=a.id, kills=1, deaths=0,
                        assists=0, score=200, loadout=3900, remaining=100),
        RoundPlayerStat(round_id=rnd.id, match_player_id=b.id, kills=0, deaths=1,
                        assists=0, score=0, loadout=2400, remaining=100),
        KillEvent(round_id=rnd.id, killer_match_player_id=a.id,
                  death_match_player_id=b.id, weapon="Vandal", event_time_seconds=20.0),
        KillEvent(round_id=rnd.id, killer_match_player_id=a.id,
                  death_match_player_id=b.id, weapon="Vandal", event_time_seconds=41.0),
    ])
    db.commit()
    return db


def test_editing_a_plant_time_changes_the_source_revision():
    """plant_time appeared NOWHERE in the old digest, and it drives every
    timing score. Changing a planted round's plant_time from 30 to 20 left
    the revision identical while default replay scores moved."""
    db = _identity_session()
    before = _source_revision(db)

    db.execute(text("UPDATE rounds SET plant_time = 20.0"))
    db.commit()

    assert _source_revision(db) != before


def test_a_balanced_loadout_edit_changes_the_source_revision():
    """A SUM cannot see +100 on one row and -100 on another. Per-player
    attribution moves; the aggregate total does not."""
    db = _identity_session()
    before = _source_revision(db)

    rows = db.execute(text(
        "SELECT id, loadout FROM round_player_stats ORDER BY id"
    )).all()
    assert len(rows) == 2
    # Verify the fixture really is balanced: the total is unchanged.
    assert (rows[0][1] + 100) + (rows[1][1] - 100) == rows[0][1] + rows[1][1]
    db.execute(text("UPDATE round_player_stats SET loadout = :v WHERE id = :i"),
               {"v": rows[0][1] + 100, "i": rows[0][0]})
    db.execute(text("UPDATE round_player_stats SET loadout = :v WHERE id = :i"),
               {"v": rows[1][1] - 100, "i": rows[1][0]})
    db.commit()

    assert _source_revision(db) != before


def test_swapping_two_kill_times_changes_the_source_revision():
    """The same hole on the kill side: swapping event_time_seconds between
    two kills leaves COUNT and SUM identical while the kill order, the
    post-plant classification and every timing factor move."""
    db = _identity_session()
    before = _source_revision(db)

    rows = db.execute(text(
        "SELECT id, event_time_seconds FROM kill_events ORDER BY id"
    )).all()
    assert len(rows) == 2 and rows[0][1] != rows[1][1]
    db.execute(text("UPDATE kill_events SET event_time_seconds = :v WHERE id = :i"),
               {"v": rows[1][1], "i": rows[0][0]})
    db.execute(text("UPDATE kill_events SET event_time_seconds = :v WHERE id = :i"),
               {"v": rows[0][1], "i": rows[1][0]})
    db.commit()

    assert _source_revision(db) != before


def test_moving_a_player_to_the_other_team_changes_the_source_revision():
    """match_players.team decides which side every kill transfers value
    between, and was in no aggregate at all."""
    db = _identity_session()
    before = _source_revision(db)

    db.execute(text("UPDATE match_players SET team = 'TEAM_2'"))
    db.commit()

    assert _source_revision(db) != before


def test_the_database_identity_carries_the_port():
    """This repo's Postgres runs on 5433 because the sister repo's runs on
    5432. Two databases on the same host with the same name are exactly the
    confusion this string exists to prevent."""
    from sqlalchemy.engine import make_url

    class _Db:
        def __init__(self, url):
            self._bind = type("B", (), {"url": make_url(url)})()

        def get_bind(self):
            return self._bind

    a = _database_identity(_Db("postgresql://u:p@localhost:5432/valomaths"))
    b = _database_identity(_Db("postgresql://u:p@localhost:5433/valomaths"))

    assert a != b
    assert "5433" in b


def test_the_run_identity_actually_records_the_source_revision():
    """RunIdentity.source_revision was declared, read by matrix_is_comparable,
    and NEVER ASSIGNED -- so it was "" on both sides of every comparison and
    the field protected nothing."""
    from app.services.kill_order_refit import _source_revision_for_identity

    db = _identity_session()

    assert _source_revision_for_identity(None) == ""
    recorded = _source_revision_for_identity(db)
    assert recorded != ""
    assert recorded == _source_revision(db)


# --------------------------------------------------------------------------
# Astra review, claim 2 -- the digest must see the fields that decide LABELS
# --------------------------------------------------------------------------


def test_correcting_a_final_match_score_changes_the_source_revision():
    """team1_rounds_won/team2_rounds_won decide match_won_by_team_a, which is
    T1's entire label and the match-weight half of T2's. Correcting 13-11 to
    11-13 flips every label in the match while touching no round row, so the
    revision stayed identical and a cached replay was accepted with the old
    labels."""
    from app.services.impact_eval import _match_won_by_team_a
    from app.models import Match

    db = _identity_session()
    match = db.query(Match).one()
    match.team1_rounds_won, match.team2_rounds_won = 13, 11
    db.commit()

    before_rev = _source_revision(db)
    before_label = _match_won_by_team_a(db.query(Match).one())

    db.execute(text("UPDATE matches SET team1_rounds_won=11, team2_rounds_won=13"))
    db.commit()
    db.expire_all()

    # Verify the fixture really does flip the label -- otherwise the revision
    # assertion below would pass for the wrong reason.
    assert _match_won_by_team_a(db.query(Match).one()) is not before_label
    assert _source_revision(db) != before_rev


def test_editing_an_agent_changes_the_source_revision():
    """impact.py:536 passes match_players.agent to
    econ_component.committed_value, to back out the free ability credits
    tracker.gg folds into a loadout figure. It was in no query at all."""
    db = _identity_session()
    before = _source_revision(db)

    db.execute(text("UPDATE match_players SET agent='Chamber' WHERE agent='Jett'"))
    db.commit()

    assert _source_revision(db) != before

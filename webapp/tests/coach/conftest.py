import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replays"))

from test_gap_patterns import TABLES, make_replay, players  # noqa: E402  (the replay tests' builders)

from app.db import Base  # noqa: E402
from app.models.coach import CoachFeedback, CoachNote, CoachPoint, CoachReport  # noqa: E402

COACH_TABLES = [CoachReport.__table__, CoachPoint.__table__, CoachFeedback.__table__, CoachNote.__table__]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES + COACH_TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def world(db):
    """A linked, valid replay of a match whose slot i is player ids[i]."""
    ids = players(db)
    return make_replay(db, rounds=3, player_ids=ids), ids

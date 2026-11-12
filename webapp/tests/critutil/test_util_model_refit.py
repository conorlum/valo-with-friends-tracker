"""The portable refit against the original refresh, using only synthetic SQLite data."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db import Base
from app.models.friendship import Friendship
from app.models.kill_event import KillEvent
from app.models.match import Match, MatchPlayer, MatchSource, Team
from app.models.player import Player
from app.models.replay import Replay, UtilModel
from app.models.round import Round
from app.services import util_model


GOLDEN = Path(__file__).with_name("data") / "refit_golden.json"


def synthetic_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[model.__table__ for model in
        (Player, Match, MatchPlayer, Round, KillEvent, Replay, UtilModel, Friendship)])
    db = Session(engine)
    for m in range(1, 7):
        db.add(Match(id=m, external_id=f"synthetic-match-{m}", source=MatchSource.SCRAPED))
        for slot in (1, 2):
            pid = m * 10 + slot
            db.add(Player(id=pid, display_name=f"synthetic-{pid}"))
            db.add(MatchPlayer(id=pid, match_id=m, player_id=pid, agent="Sage",
                               team=Team.TEAM_1 if slot == 1 else Team.TEAM_2))
        for n in (1, 2, 13):
            rid = m * 100 + n
            first_wins = (m + n) % 2 == 0
            db.add(Round(id=rid, match_id=m, round_number=n,
                         outcome="Team A Elimination Win" if first_wins else "Team B Elimination Win",
                         planted=n == 2, plant_time=20.0 if n == 2 else None,
                         exploded=n == 2 and first_wins, defused=n == 2 and not first_wins,
                         defuse_time=36.0 if n == 2 and not first_wins else None))
            db.add(KillEvent(id=rid, round_id=rid, weapon="Vandal",
                             killer_match_player_id=m * 10 + (1 if first_wins else 2),
                             death_match_player_id=m * 10 + (2 if first_wins else 1),
                             event_time_seconds=28.0 if n == 2 else 8.0 + n))
    db.add(Replay(id=6, match_id=6, match_uuid="00000000-0000-0000-0000-000000000006",
                  map_name="Haven", round_count=3, format_version=1, recipe="p1.c22.f1.a1",
                  game_branch="synthetic", source_sha256="0" * 64, source="local",
                  link_status="linked", link_inputs={}, clock_offset=1.25))
    db.commit()
    return db


@pytest.fixture
def db():
    session = synthetic_db()
    try:
        yield session
    finally:
        engine = session.get_bind()
        session.close()
        engine.dispose()


def test_refresh_matches_original_golden(db):
    assert util_model.refresh(db) == json.loads(GOLDEN.read_text())


def test_pure_refit_matches_original_golden(db):
    excluded = util_model.replay_match_ids(db)
    observations = util_model.load_observations(db, excluded, by_match=True)
    assert util_model.refit_from(observations, excluded) == json.loads(GOLDEN.read_text())


@pytest.mark.parametrize("match_ids", [{1, 3}, set()])
def test_selective_observations_filter_all_three_queries_in_sql(db, match_ids):
    full = util_model.load_observations(db, {6}, by_match=True)
    assert full and all(full.values())
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement.lower())
    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        selected = util_model.load_observations(db, {6}, by_match=True, match_ids=match_ids)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert selected == {m: obs for m, obs in full.items() if m in match_ids}
    assert len(queries) == 3
    assert all("where" in q and " in (" in q for q in queries)
    assert "round_id in" in queries[2]
    assert util_model.load_observations(db, {6}, match_ids=match_ids) == [
        o for m, obs in full.items() if m in match_ids for o in obs]


def test_pure_refit_leaves_out_excluded_matches(db):
    all_obs = util_model.load_observations(db, by_match=True)
    assert util_model.refit_from(all_obs, {6}) == json.loads(GOLDEN.read_text())


def test_refresh_ends_read_transaction_before_computing(db, monkeypatch):
    original = util_model.refit_from
    def refit(*args, **kwargs):
        assert not db.in_transaction()
        return original(*args, **kwargs)
    monkeypatch.setattr(util_model, "refit_from", refit)
    assert util_model.refresh(db) == json.loads(GOLDEN.read_text())
    assert not db.in_transaction()

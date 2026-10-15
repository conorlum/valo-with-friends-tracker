"""get_current_map_pool on the real pool (R3, D7): pool from load_pool_history, weights since the
latest official change, each pool map starting at 1 and each map that entered at that change at
NEW_MAP_PRIOR (D7: right after a patch the game mostly gives a map that just entered)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Match, MatchPlayer, Player
from app.models.match import MatchSource, Team
from app.services import map_pool, map_prediction
from app.services.map_pool import Change, PoolHistory

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)
CHANGE = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_cache():
    map_pool.clear_pool_cache()
    yield
    map_pool.clear_pool_cache()


@pytest.fixture
def db(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///" + str(tmp_path / "pred.sqlite"))
    Base.metadata.create_all(eng, tables=[Player.__table__, Match.__table__, MatchPlayer.__table__])
    history = PoolHistory([
        Change("Ascent", True, T0, "1", "seed"),
        Change("Bind", True, T0, "1", "seed"),
        Change("Split", True, T0, "1", "seed"),
        Change("Split", False, CHANGE, "2", "seed"),
        Change("Haven", True, CHANGE, "2", "seed"),
    ])
    monkeypatch.setattr(map_prediction, "load_pool_history", lambda db: history)
    with sessionmaker(bind=eng)() as s:
        yield s
    eng.dispose()


def _matches(db, map_name, n, start):
    for i in range(n):
        db.add(Match(external_id=f"{map_name}-{start:%m%d}-{i}", source=MatchSource.SCRAPED,
                     map_name=map_name, played_at=start + timedelta(hours=i)))
    db.commit()


def test_pool_is_the_real_pool_with_a_head_start_for_new_maps(db):
    after = CHANGE + timedelta(days=1)
    _matches(db, "Ascent", 3, after)
    _matches(db, "Bind", 1, after)
    pool = map_prediction.get_current_map_pool(db)
    assert set(pool) == {"Ascent", "Bind", "Haven"}
    # Haven entered at the latest change: (3+1) / (4+2+5), (1+1) / 11, (0+5) / 11
    assert pool["Ascent"] == pytest.approx(4 / 11)
    assert pool["Bind"] == pytest.approx(2 / 11)
    assert pool["Haven"] == pytest.approx(5 / 11)
    assert sum(pool.values()) == pytest.approx(1.0)


def test_a_map_that_just_entered_leads_before_any_matches(db):
    pool = map_prediction.get_current_map_pool(db)
    assert max(pool, key=pool.get) == "Haven"
    assert pool["Haven"] == pytest.approx(map_prediction.NEW_MAP_PRIOR / (map_prediction.NEW_MAP_PRIOR + 2))


def test_non_pool_map_gets_nothing_even_with_matches(db):
    _matches(db, "Split", 5, CHANGE + timedelta(days=1))
    pool = map_prediction.get_current_map_pool(db)
    assert "Split" not in pool
    assert pool["Ascent"] == pytest.approx(1 / 7)


def test_matches_before_the_latest_change_are_not_counted(db):
    _matches(db, "Ascent", 10, CHANGE - timedelta(days=10))
    pool = map_prediction.get_current_map_pool(db)
    assert pool["Ascent"] == pytest.approx(1 / 7)


def test_safety_net_changes_do_not_start_the_weight_window(db, monkeypatch):
    history = PoolHistory([
        Change("Ascent", True, T0, "1", "seed"),
        Change("Bind", True, CHANGE, "2", "seed"),
        Change("Split", True, CHANGE + timedelta(days=20), map_pool.SAFETY_NET_PATCH, map_pool.SAFETY_NET_SOURCE),
    ])
    monkeypatch.setattr(map_prediction, "load_pool_history", lambda db: history)
    _matches(db, "Ascent", 2, CHANGE + timedelta(days=5))
    pool = map_prediction.get_current_map_pool(db)
    # Bind entered at the latest official change; the safety net's Split is not new: (2+1) / (3+5+1)
    assert pool["Ascent"] == pytest.approx(3 / 9)
    assert pool["Split"] == pytest.approx(1 / 9)


def test_predictions_sum_to_one_over_pool_maps(db):
    player = Player(display_name="A#1")
    db.add(player)
    db.commit()
    _matches(db, "Ascent", 2, CHANGE + timedelta(days=1))
    m = db.query(Match).first()
    db.add(MatchPlayer(match_id=m.id, player_id=player.id, agent="Jett", team=Team.TEAM_1))
    db.commit()
    preds = map_prediction.predict_map_probabilities(db, [player.id])
    assert {p.map_name for p in preds} == {"Ascent", "Bind", "Haven"}
    assert sum(p.probability for p in preds) == pytest.approx(1.0)

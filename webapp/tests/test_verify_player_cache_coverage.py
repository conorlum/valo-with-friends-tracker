"""Prewarm coverage is judged the way a page load judges a cache row."""

import pytest

from app.models import Player, PlayerViewCache
from app.services import player_view_cache
from app.services.player_view_cache import cache_version
from app.services.player_views import compute_player_views_by_scope
from scripts import verify_player_cache_coverage as coverage
from tests.buy_disruption_fixtures import build_match, session


@pytest.fixture
def db():
    database = session(all_tables=True)
    _, players, _ = build_match(database, "cached", kills={3: [("A1", "B1", 10.0)]}, count_stats=True)
    database.info["player_id"] = players["A1"].player_id
    return database


def _player(db):
    return db.get(Player, db.info["player_id"])


def _cache(db, player, scope, *, version, data):
    db.add(PlayerViewCache(player_id=player.id, scope=scope, version=version, data=data))
    db.flush()


def _real_blobs(db, player):
    """What a prewarm writes: views computed from the match, then encoded."""
    return {scope: player_view_cache._encode(views)
            for scope, views in compute_player_views_by_scope(db, player).items()}


def test_missing_rows_are_problems_for_both_scopes(db):
    result = coverage.coverage(db, {_player(db).id})
    assert result["ok"] == 0
    assert len(result["problems"]) == 2
    assert all("no cache row" in p for p in result["problems"])


def test_a_real_row_from_another_version_is_not_coverage(db):
    player = _player(db)
    for scope, blob in _real_blobs(db, player).items():
        _cache(db, player, scope, version=cache_version() - 1, data=blob)
    result = coverage.coverage(db, {player.id})
    assert result["ok"] == 0
    assert all("the running code expects" in p for p in result["problems"])


def test_a_row_whose_blob_does_not_validate_is_not_coverage(db):
    player = _player(db)
    for scope in coverage.SCOPES:
        _cache(db, player, scope, version=cache_version(), data={"not": "a real blob"})
    result = coverage.coverage(db, {player.id})
    assert result["ok"] == 0
    assert all("does not validate" in p for p in result["problems"])


def test_a_real_prewarmed_row_at_the_right_version_counts(db):
    player = _player(db)
    for scope, blob in _real_blobs(db, player).items():
        _cache(db, player, scope, version=cache_version(), data=blob)
    result = coverage.coverage(db, {player.id})
    assert result == {"expected_version": cache_version(), "ok": 2, "problems": [], "requested": 2}


def test_one_scope_missing_is_still_a_problem(db):
    player = _player(db)
    _cache(db, player, "career", version=cache_version(), data=_real_blobs(db, player)["career"])
    result = coverage.coverage(db, {player.id})
    assert result["ok"] == 1
    assert len(result["problems"]) == 1 and "recent: no cache row" in result["problems"][0]


def test_an_unknown_player_is_a_problem_not_a_crash(db):
    result = coverage.coverage(db, {987654})
    assert result["ok"] == 0
    assert result["problems"] == ["player 987654: no such player"]

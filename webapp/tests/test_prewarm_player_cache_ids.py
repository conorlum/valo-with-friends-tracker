"""The prewarm list is captured before the swap and every failure is reported."""

import pytest

from app.models import Player, PlayerViewCache
from app.services.player_view_cache import cache_version
from scripts import prewarm_player_cache_ids as tool
from tests.buy_disruption_fixtures import session


@pytest.fixture
def db():
    database = session(all_tables=True)
    for name in ("Friend#NA1", "Other Friend#TAG", "Stranger#0001", "Viewed#0002"):
        database.add(Player(display_name=name))
    database.flush()
    return database


def _id(db, name):
    return db.query(Player).filter_by(display_name=name).one().id


def _cached(db, name):
    db.add(PlayerViewCache(player_id=_id(db, name), scope="recent", version=cache_version(), data={}))
    db.flush()


def test_capture_takes_the_roster_by_exact_name_and_the_cached_players_beyond_it(db):
    _cached(db, "Friend#NA1")
    _cached(db, "Viewed#0002")

    captured = tool.capture(db, ["Friend#NA1", "Other Friend#TAG", "friend#na1"])

    assert captured["roster"] == {_id(db, "Friend#NA1"), _id(db, "Other Friend#TAG")}
    assert captured["recent"] == {_id(db, "Viewed#0002")}, "the roster is not prewarmed twice"
    assert captured["missing"] == ["friend#na1"], "matching is exact, as ingestion's is"


def test_ids_round_trip_sorted_and_deduplicated(tmp_path):
    path = tmp_path / "ids.txt"
    tool.write_ids(str(path), {30, 4, 12})
    assert path.read_text(encoding="utf-8") == "4\n12\n30\n"
    assert tool.read_ids(str(path)) == [4, 12, 30]


def test_prewarm_reports_each_failure_and_carries_on(db, monkeypatch):
    done, rollbacks = [], []

    def recompute(database, player_id):
        if player_id == 2:
            raise RuntimeError("replay failed")
        done.append(player_id)

    monkeypatch.setattr(tool.player_view_cache, "recompute_player_views", recompute)
    monkeypatch.setattr(db, "rollback", lambda: rollbacks.append(True))

    failures = tool.prewarm(db, [1, 2, 3])

    assert done == [1, 3]
    assert failures == ["player 2: RuntimeError('replay failed')"]
    assert rollbacks == [True], "a failed player's transaction is rolled back before the next"

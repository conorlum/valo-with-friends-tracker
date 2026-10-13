import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replays"))

from test_gap_patterns import make_replay, players  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import Match  # noqa: E402
from app.services import coach  # noqa: E402
from tests.coach.test_coach_store import payload  # noqa: E402


def external_id(db, replay):
    return db.get(Match, replay.match_id).external_id


def test_only_matches_with_a_report_and_a_watchable_replay_are_coached(db, world, monkeypatch):
    replay, ids = world
    other = make_replay(db, rounds=2, player_ids=players(db))
    coach.store_report(db, replay.match_id, ids[0], payload())
    both = [external_id(db, replay), external_id(db, other)]
    monkeypatch.setattr(coach, "watchable_external_ids", lambda db, ids_: {e.lower() for e in ids_})
    assert coach.coached_external_ids(db, both) == {both[0].lower()}
    # the report exists but the replay cannot be watched: no link
    monkeypatch.setattr(coach, "watchable_external_ids", lambda db, ids_: set())
    assert coach.coached_external_ids(db, both) == set()


def test_nothing_is_coached_in_demo_mode(db, world, monkeypatch):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    monkeypatch.setattr(settings, "demo_mode", True)
    assert coach.coached_external_ids(db, [external_id(db, replay)]) == set()

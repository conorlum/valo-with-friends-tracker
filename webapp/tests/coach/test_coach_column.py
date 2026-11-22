import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replays"))

from test_gap_patterns import make_replay, players  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import Match  # noqa: E402
from app.models.coach import CoachFeedback  # noqa: E402
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


def test_feedback_share_counts_points_of_each_players_latest_report(db, world):
    replay, ids = world
    old = coach.store_report(db, replay.match_id, ids[0], payload())
    db.add(CoachFeedback(point_id=coach.points(db, old.id)[0].id, author_player_id=ids[1], verdict="right"))
    latest = coach.store_report(db, replay.match_id, ids[0], payload())        # v2: no feedback yet
    other = coach.store_report(db, replay.match_id, ids[3], payload())
    latest_points, other_points = coach.points(db, latest.id), coach.points(db, other.id)
    # a moment row and an overall row both count as a response; two rows on one point count once
    db.add(CoachFeedback(point_id=latest_points[1].id, instance_index=0, author_player_id=ids[1], verdict="wrong"))
    db.add(CoachFeedback(point_id=other_points[0].id, author_player_id=ids[1], note="ok"))
    db.add(CoachFeedback(point_id=other_points[0].id, author_player_id=ids[2], verdict="right"))
    db.commit()
    eid = external_id(db, replay).lower()
    assert coach.coach_feedback_share(db, {eid}) == {eid: (2, 4)}


def test_feedback_share_is_zero_of_n_with_no_feedback(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    eid = external_id(db, replay).lower()
    assert coach.coach_feedback_share(db, {eid}) == {eid: (0, 2)}
    assert coach.coach_feedback_share(db, set()) == {}


def test_nothing_is_coached_in_demo_mode(db, world, monkeypatch):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    monkeypatch.setattr(settings, "demo_mode", True)
    assert coach.coached_external_ids(db, [external_id(db, replay)]) == set()

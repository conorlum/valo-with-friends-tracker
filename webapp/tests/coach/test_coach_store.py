import pytest

from app.models.coach import CoachFeedback, CoachPoint, CoachReport
from app.services import coach


def test_a_report_its_points_and_feedback_round_trip(db, world):
    replay, ids = world
    report = CoachReport(match_id=replay.match_id, player_id=ids[0], kind="match", version=1,
                         playbook_version="1", model="m", data_gaps={"2": ["control_failed"]}, dropped_points=0)
    db.add(report)
    db.flush()
    point = CoachPoint(report_id=report.id, polarity="improve", ordinal=1, theme="trading", title="T", body="B",
                       instances=[{"round": 2, "t": 41.5, "caption": "c", "evidence": ["q1"]}])
    db.add(point)
    db.flush()
    db.add(CoachFeedback(point_id=point.id, author_player_id=ids[1], verdict="wrong", note="n"))
    db.commit()
    stored = db.query(CoachPoint).one()
    assert stored.instances[0]["t"] == 41.5 and db.query(CoachFeedback).one().handled_at is None
    assert db.query(CoachReport).one().data_gaps == {"2": ["control_failed"]}


def payload(**over):
    point = {"polarity": "improve", "theme": "trading", "title": "Died alone", "body": "Body.",
             "instances": [{"round": 2, "t": 41.5, "caption": "c", "evidence": ["q1"]}]}
    base = {"playbook_version": "1", "model": "m", "data_gaps": {}, "dropped_points": 0,
            "points": [{**point, "polarity": "good", "title": "Good one"}, point]}
    return {**base, **over}


def test_storing_twice_makes_two_versions_and_keeps_the_first(db, world):
    replay, ids = world
    first = coach.store_report(db, replay.match_id, ids[0], payload())
    second = coach.store_report(db, replay.match_id, ids[0], payload(playbook_version="2"))
    assert (first.version, second.version) == (1, 2)
    assert coach.versions(db, replay.match_id, ids[0]) == [1, 2]
    assert coach.get_report(db, replay.match_id, ids[0]).playbook_version == "2"
    assert coach.get_report(db, replay.match_id, ids[0], 1).id == first.id
    assert coach.get_report(db, replay.match_id, ids[0], 99).id == second.id


def test_points_come_back_good_first_in_order(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], payload())
    assert [(p.polarity, p.ordinal, p.title) for p in coach.points(db, report.id)] == [
        ("good", 1, "Good one"), ("improve", 1, "Died alone")]


def test_coached_players_lists_each_player_once(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    coach.store_report(db, replay.match_id, ids[0], payload())
    coach.store_report(db, replay.match_id, ids[3], payload())
    assert sorted(pid for pid, _ in coach.coached_players(db, replay.match_id)) == sorted([ids[0], ids[3]])
    assert coach.get_report(db, replay.match_id, ids[5]) is None


@pytest.mark.parametrize("change, message", [
    ({"points": [{"polarity": "bad", "theme": "t", "title": "x", "body": "b", "instances": []}]}, "polarity"),
    ({"points": [{"polarity": "good", "theme": "t", "title": "", "body": "b", "instances": []}]}, "title"),
    ({"points": [{"polarity": "good", "theme": "t", "title": "x", "body": "b", "instances": [{"t": 1}]}]}, "round"),
    ({"playbook_version": ""}, "playbook_version"),
    ({"points": "nope"}, "points"),
])
def test_a_malformed_payload_is_refused_and_nothing_is_stored(db, world, change, message):
    replay, ids = world
    with pytest.raises(coach.ReportError, match=message):
        coach.store_report(db, replay.match_id, ids[0], payload(**change))
    assert coach.versions(db, replay.match_id, ids[0]) == []

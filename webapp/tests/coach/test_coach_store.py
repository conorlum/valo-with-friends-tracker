from app.models.coach import CoachFeedback, CoachPoint, CoachReport


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

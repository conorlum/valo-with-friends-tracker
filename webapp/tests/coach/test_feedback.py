import pytest

from app.services import coach


def test_voice_notes_are_kept_in_order_and_can_be_switched_off(db):
    first = coach.add_note(db, "voice", "  Stop peeking that dry.  ")
    coach.add_note(db, "voice", "Good trade, do that every round.")
    assert coach.active_notes(db, "voice") == ["Stop peeking that dry.", "Good trade, do that every round."]
    assert coach.set_note_active(db, first.id, False) is True
    assert coach.active_notes(db, "voice") == ["Good trade, do that every round."]
    assert coach.set_note_active(db, 9999, False) is False


def test_an_empty_note_is_refused(db):
    with pytest.raises(coach.ReportError, match="body"):
        coach.add_note(db, "voice", "   ")


from fastapi import HTTPException
from starlette.requests import Request

from app.models.coach import CoachFeedback
from app.routers import coach as routes
from tests.coach.test_coach_store import payload


class Viewer:
    def __init__(self, player_id):
        self.id = player_id


def request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "session": {}, "query_string": b""})


def stored(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], payload())
    return replay, ids, report, coach.points(db, report.id)


def test_feedback_is_stored_against_its_point(db, world):
    replay, ids, report, points = stored(db, world)
    coach.add_feedback(db, replay.match_id, points[1].id, ids[1], "wrong", "  I was there.  ")
    row = db.query(CoachFeedback).one()
    assert (row.point_id, row.author_player_id, row.verdict, row.note, row.handled_at) == (
        points[1].id, ids[1], "wrong", "I was there.", None)


def test_a_bad_verdict_or_an_overlong_note_is_refused(db, world):
    replay, ids, report, points = stored(db, world)
    with pytest.raises(coach.ReportError, match="verdict"):
        coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "meh", None)
    with pytest.raises(coach.ReportError, match="2000"):
        coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "right", "x" * 2001)
    assert db.query(CoachFeedback).count() == 0


def test_a_point_from_another_match_is_not_found(db, world):
    replay, ids, report, points = stored(db, world)
    with pytest.raises(LookupError):
        coach.add_feedback(db, replay.match_id + 999, points[0].id, ids[1], "right", None)


def test_the_route_needs_a_login(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: None)
    with pytest.raises(HTTPException) as refused:
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id, verdict="right", note="", db=db)
    assert refused.value.status_code == 403 and db.query(CoachFeedback).count() == 0


def test_the_route_stores_and_returns_to_the_report(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    response = routes.post_feedback(request(), str(replay.match_uuid), points[0].id, verdict="badly_put", note="n", db=db)
    assert response.status_code == 303
    assert response.headers["location"] == f"/replays/{str(replay.match_uuid).lower()}/coach?player={ids[0]}&version=1"
    assert db.query(CoachFeedback).one().verdict == "badly_put"


def test_the_route_404s_for_a_point_of_another_match_and_400s_for_a_bad_verdict(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    with pytest.raises(HTTPException) as missing:
        routes.post_feedback(request(), str(replay.match_uuid), 99999, verdict="right", note="", db=db)
    with pytest.raises(HTTPException) as bad:
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id, verdict="meh", note="", db=db)
    assert (missing.value.status_code, bad.value.status_code) == (404, 400)


def test_marking_handled_closes_only_that_reports_open_feedback(db, world):
    replay, ids, report, points = stored(db, world)
    other = coach.store_report(db, replay.match_id, ids[3], payload())
    coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "wrong", "a")
    coach.add_feedback(db, replay.match_id, points[1].id, ids[2], "right", None)
    coach.add_feedback(db, replay.match_id, coach.points(db, other.id)[0].id, ids[1], "wrong", "b")
    assert [(row["report_id"], row["open"]) for row in coach.open_feedback(db)] == [(report.id, 2), (other.id, 1)]
    assert coach.mark_handled(db, report.id) == 2 and coach.mark_handled(db, report.id) == 0
    assert [(row["report_id"], row["open"]) for row in coach.open_feedback(db)] == [(other.id, 1)]


def test_the_prompt_route_returns_plain_text_for_a_report_of_that_match(db, world):
    replay, ids, report, points = stored(db, world)
    coach.add_feedback(db, replay.match_id, points[1].id, ids[1], "wrong", "not so")
    response = routes.prompt_out(str(replay.match_uuid), report=str(report.id), db=db)
    assert response.media_type == "text/plain" and b"not so" in response.body
    with pytest.raises(HTTPException) as missing:
        routes.prompt_out(str(replay.match_uuid), report="99999", db=db)
    assert missing.value.status_code == 404


def test_the_prompt_route_404s_in_demo_mode(db, world, monkeypatch):
    from app.config import settings
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as demo:
        routes.prompt_out(str(replay.match_uuid), report=str(report.id), db=db)
    assert demo.value.status_code == 404


def test_the_prompt_route_without_a_report_covers_every_report_of_the_match_with_open_feedback(db, world):
    from app.models import Player
    replay, ids, first, points = stored(db, world)
    coach.add_feedback(db, replay.match_id, points[1].id, ids[1], "wrong", "on the old version")
    coach.store_report(db, replay.match_id, ids[0], payload())                       # a newer version, no feedback
    friend = coach.store_report(db, replay.match_id, ids[3], payload())
    coach.add_feedback(db, replay.match_id, coach.points(db, friend.id)[0].id, ids[1], "right", "spot on")
    done = coach.store_report(db, replay.match_id, ids[2], payload())
    coach.add_feedback(db, replay.match_id, coach.points(db, done.id)[0].id, ids[1], "wrong", "already dealt with")
    coach.mark_handled(db, done.id)
    quiet = coach.store_report(db, replay.match_id, ids[4], payload())

    reports, left_out = coach.match_prompt_data(db, replay)
    assert sorted(r["id"] for r in reports) == sorted([first.id, friend.id])          # the old version is not lost
    assert sorted(left_out) == sorted(db.get(Player, ids[i]).display_name for i in (2, 4))
    body = routes.prompt_out(str(replay.match_uuid), db=db).body.decode()
    assert "on the old version" in body and "spot on" in body and "already dealt with" not in body
    assert f"handled --report" in body and str(quiet.id) not in body.rsplit("handled --report", 1)[1].split()


def test_the_panel_shows_the_form_only_to_a_logged_in_viewer():
    from app.templates import templates
    from tests.coach.test_coach_panel import coach as panel_data

    render = lambda data: templates.env.get_template("replays/_coach_points.html").render(coach=data)
    assert 'action="/replays/abc/coach/points/7/feedback"' in render(panel_data(can_feedback=True))
    assert "Log in to give feedback" in render(panel_data(can_feedback=False))
    assert "<form" not in render(panel_data(can_feedback=False))


# PR 132 review, findings 7 and 12.

def test_a_second_post_by_the_same_author_replaces_their_open_one(db, world):
    replay, ids, report, points = stored(db, world)
    coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "wrong", "first")
    coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "right", "second")
    coach.add_feedback(db, replay.match_id, points[0].id, ids[2], "wrong", "another reader")
    rows = db.query(CoachFeedback).order_by(CoachFeedback.id).all()
    assert [(r.author_player_id, r.verdict, r.note) for r in rows] == [(ids[1], "right", "second"),
                                                                       (ids[2], "wrong", "another reader")]
    coach.mark_handled(db, report.id)                           # handled rows are history: a new post is a new row
    coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "wrong", "after")
    assert db.query(CoachFeedback).count() == 3


def test_the_page_shows_only_the_newest_rows_of_a_point(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(coach, "FEEDBACK_SHOWN", 2)
    for author, note in ((ids[1], "a"), (ids[2], "b"), (ids[3], "c")):
        coach.add_feedback(db, replay.match_id, points[0].id, author, "wrong", note)
    shown = coach._feedback_by_point(db, [points[0].id])[points[0].id]
    assert [f["note"] for f in shown] == ["b", "c"]


def test_the_feedback_route_404s_in_demo_mode(db, world, monkeypatch):
    from app.config import settings
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as demo:
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id, verdict="right", note="", db=db)
    assert demo.value.status_code == 404 and db.query(CoachFeedback).count() == 0

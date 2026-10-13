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


def test_the_panel_shows_the_form_only_to_a_logged_in_viewer():
    from app.templates import templates
    from tests.coach.test_coach_panel import coach as panel_data

    render = lambda data: templates.env.get_template("replays/_coach.html").render(coach=data)
    assert 'action="/replays/abc/coach/points/7/feedback"' in render(panel_data(can_feedback=True))
    assert "Log in to give feedback" in render(panel_data(can_feedback=False))
    assert "<form" not in render(panel_data(can_feedback=False))

import re

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

from app.models import MatchPlayer, Player
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
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id, form={"verdict_0": "right"}, db=db)
    assert refused.value.status_code == 403 and db.query(CoachFeedback).count() == 0


def test_the_route_stores_and_returns_to_the_report(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    response = routes.post_feedback(request(), str(replay.match_uuid), points[0].id, form={"verdict_0": "correction", "note_0": "n"}, db=db)
    assert response.status_code == 303
    assert response.headers["location"] == f"/replays/{str(replay.match_uuid).lower()}/coach?player={ids[0]}&version=1"
    row = db.query(CoachFeedback).one()
    assert (row.verdict, row.note, row.instance_index) == ("correction", "n", 0)


def test_an_area_only_correction_can_be_replaced_or_cleared(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))

    def post(area, player):
        return routes.post_feedback(request(), str(replay.match_uuid), points[0].id,
                                    form={"area_0": area, "area_player_0": str(player)}, db=db)

    assert post(" Market ", ids[5]).status_code == 303
    row = db.query(CoachFeedback).one()
    original_id = row.id
    assert (row.instance_index, row.verdict, row.note, row.area_name, row.area_player_id) == (
        0, None, None, "Market", ids[5])

    post("Courtyard", ids[2])
    row = db.query(CoachFeedback).one()
    assert (row.id, row.area_name, row.area_player_id) == (original_id, "Courtyard", ids[2])

    post("", ids[2])  # the dropdown sends a value even when the area input is blank
    assert db.query(CoachFeedback).count() == 0


@pytest.mark.parametrize("value", ["outside match", "not-an-id", "²"])
def test_the_route_refuses_an_area_player_who_is_not_in_the_match(db, world, monkeypatch, value):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    if value == "outside match":
        outside = Player(display_name="Outside the fixture match")
        db.add(outside)
        db.commit()
        value = str(outside.id)
    with pytest.raises(HTTPException) as bad:
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id,
                             form={"area_0": "", "area_player_0": value}, db=db)
    assert bad.value.status_code == 400 and db.query(CoachFeedback).count() == 0


def test_a_blank_area_clears_its_player_but_keeps_other_feedback(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    form = {"note_0": "Still a useful note", "area_0": "Market", "area_player_0": str(ids[5])}
    routes.post_feedback(request(), str(replay.match_uuid), points[0].id, form=form, db=db)
    routes.post_feedback(request(), str(replay.match_uuid), points[0].id, form={**form, "area_0": "   "}, db=db)
    row = db.query(CoachFeedback).one()
    assert (row.note, row.area_name, row.area_player_id) == ("Still a useful note", None, None)


def test_the_route_404s_for_a_point_of_another_match_and_400s_for_a_bad_verdict(db, world, monkeypatch):
    replay, ids, report, points = stored(db, world)
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    with pytest.raises(HTTPException) as missing:
        routes.post_feedback(request(), str(replay.match_uuid), 99999, form={"verdict_0": "right"}, db=db)
    with pytest.raises(HTTPException) as bad:
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id, form={"verdict_0": "meh"}, db=db)
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
        routes.post_feedback(request(), str(replay.match_uuid), points[0].id, form={"verdict_0": "right"}, db=db)
    assert demo.value.status_code == 404 and db.query(CoachFeedback).count() == 0


# Feedback per moment, and an overall note per point (migration 0025).

def two_moments():
    point = {"polarity": "improve", "theme": "trading", "title": "Died alone", "body": "Body.",
             "instances": [{"round": 2, "t": 41.5, "caption": "first"}, {"round": 3, "t": 12.0, "caption": "second"}]}
    return payload(points=[{**point, "polarity": "good", "title": "Good one"}, point])


def test_the_form_is_read_as_moments_and_an_overall_note():
    moments, overall = routes.parse_feedback_form({"verdict_0": "right", "note_0": "yes", "note_1": "half",
                                                   "area_2": "Market", "area_player_2": "13",
                                                   "note": " all of it ", "verdict_x": "wrong"})
    assert moments == {0: ("right", "yes", "", None), 1: (None, "half", "", None),
                       2: (None, "", "Market", 13)}
    assert overall == " all of it "


def test_one_form_stores_a_row_per_moment_and_one_overall(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], two_moments())
    point = coach.points(db, report.id)[1]
    coach.save_point_feedback(db, replay.match_id, point.id, ids[1],
                              {0: ("wrong", "I was traded", None, None),
                               1: ("correction", "it was 2v1", None, None)}, "Fair overall")
    rows = db.query(CoachFeedback).order_by(CoachFeedback.id).all()
    assert [(r.instance_index, r.verdict, r.note) for r in rows] == [
        (0, "wrong", "I was traded"), (1, "correction", "it was 2v1"), (None, None, "Fair overall")]

    coach.save_point_feedback(db, replay.match_id, point.id, ids[1],
                              {0: (None, "", None, None), 1: ("right", "", None, None)}, "")
    rows = db.query(CoachFeedback).all()                     # a blank moment or overall clears the author's row
    assert [(r.instance_index, r.verdict, r.note) for r in rows] == [(1, "right", None)]


def test_a_moment_that_is_not_in_the_point_or_a_point_level_verdict_is_refused(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], two_moments())
    point = coach.points(db, report.id)[0]
    with pytest.raises(coach.ReportError, match="moment 5"):
        coach.save_point_feedback(db, replay.match_id, point.id, ids[1], {5: ("right", "", None, None)}, "")
    with pytest.raises(coach.ReportError, match="verdict"):
        coach.save_point_feedback(db, replay.match_id, point.id, ids[1], {0: ("badly_put", "", None, None)}, "")
    assert db.query(CoachFeedback).count() == 0


def test_an_old_overall_verdict_survives_the_new_form(db, world):
    replay, ids, report, points = stored(db, world)
    coach.add_feedback(db, replay.match_id, points[0].id, ids[1], "badly_put", "old note")
    coach.save_point_feedback(db, replay.match_id, points[0].id, ids[1], {}, "")
    row = db.query(CoachFeedback).one()
    assert (row.verdict, row.note) == ("badly_put", None)


def test_the_page_puts_feedback_under_its_moment_and_fills_in_the_viewers_own(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], two_moments())
    point = coach.points(db, report.id)[1]
    coach.save_point_feedback(db, replay.match_id, point.id, ids[1], {1: ("correction", "2v1", None, None)}, "overall")
    coach.add_feedback(db, replay.match_id, point.id, ids[2], "wrong", "no", instance=0)
    shown = coach.page_data(db, replay, ids[0], None, viewer_id=ids[1])["report"]["improve"][0]
    assert [[f["note"] for f in i["feedback"]] for i in shown["instances"]] == [["no"], ["2v1"]]
    assert [f["note"] for f in shown["feedback"]] == ["overall"] and shown["disputed"]
    assert shown["mine"][1]["verdict"] == "correction" and shown["mine"][None]["note"] == "overall"
    assert 0 not in shown["mine"]

    from app.templates import templates
    html = templates.env.get_template("replays/_coach_points.html").render(
        coach={"match_uuid": "abc", "can_feedback": True,
               "report": {"good": [], "improve": [shown]}})
    assert 'name="verdict_1" value="correction" checked' in html and 'name="note_1"' in html
    assert 'value="2v1"' in html and ">overall</textarea>" in html
    assert 'name="verdict_0" value="right">' in html and "Correction" in html

    text = routes.prompt_out(str(replay.match_uuid), report=str(report.id), db=db).body.decode()
    assert "- moment 2: round 3 at 0:12 (12.0 s into the round): second" in text
    assert 'moment="2" round="3" t="12.0"' in text and 'verdict="correction"' in text
    assert 'moment="1" round="2" t="41.5"' in text and 'verdict="overall note"' in text


def test_the_area_form_defaults_and_repopulates_with_anonymized_players(db, world, monkeypatch):
    from app.templates import templates

    replay, ids = world
    enemy = db.get(Player, ids[5])
    enemy.display_name = "Hidden opponent fixture"
    db.query(MatchPlayer).filter_by(match_id=replay.match_id, player_id=enemy.id).one().agent = "Neon"
    report = coach.store_report(db, replay.match_id, ids[0], two_moments())
    point = coach.points(db, report.id)[1]
    monkeypatch.setattr(routes, "get_current_player", lambda req, db_: Viewer(ids[1]))
    routes.post_feedback(request(), str(replay.match_uuid), point.id,
                         form={"area_0": "Market", "area_player_0": str(enemy.id)}, db=db)

    data = coach.page_data(db, replay, ids[0], None, viewer_id=ids[1])
    data["can_feedback"] = True
    html = templates.env.get_template("replays/_coach_points.html").render(coach=data)
    shown = html.split(f'data-coach-point="{point.id}"', 1)[1]
    assert "Area should be" in shown and 'name="area_0" maxlength="64" value="Market"' in shown
    for index, selected in ((0, enemy.id), (1, ids[0])):
        options = re.search(rf'<select name="area_player_{index}"[^>]*>(.*?)</select>', shown, re.S).group(1)
        assert options.count("<option") == 10
        assert f'<option value="{selected}" selected>' in options
        assert f'<option value="{enemy.id}"' in options and "the enemy Neon" in options
    assert enemy.display_name not in html

    text = routes.prompt_out(str(replay.match_uuid), report=str(report.id), db=db).body.decode()
    assert "area should be Market for the enemy Neon" in text
    assert enemy.display_name not in text

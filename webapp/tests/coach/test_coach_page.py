import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.models.coach import CoachFeedback
from app.routers import coach as routes
from app.services import coach
from tests.coach.test_coach_store import payload


def request():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "session": {}, "query_string": b""})


@pytest.fixture
def captured(monkeypatch):
    seen = {}
    monkeypatch.setattr(routes.templates, "TemplateResponse",
                        lambda req, name, context, **kw: seen.update(name=name, context=context) or seen)
    monkeypatch.setattr(routes, "viewer_context", lambda req, db, replay: {"replay": {"map_name": replay.map_name}})
    return seen


def test_page_data_shows_the_latest_report_of_the_first_coached_player(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    coach.store_report(db, replay.match_id, ids[0], payload(playbook_version="2"))
    data = coach.page_data(db, replay, None, None)
    assert data["report"]["version"] == 2 and data["report"]["versions"] == [1, 2]
    assert [p["title"] for p in data["report"]["good"]] == ["Good one"]
    assert data["report"]["improve"][0]["instances"] == [{"index": 0, "round": 2, "t": 41.5, "t1": None, "caption": "c", "feedback": []}]
    assert data["players"][0]["selected"] is True and data["team_coming_soon"] is True


def test_a_bad_or_unknown_player_or_version_falls_back(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    for player, version in ((ids[7], None), (None, 99), (-1, -1)):
        assert coach.page_data(db, replay, player, version)["report"]["version"] == 1


def test_a_match_with_no_report_has_an_empty_page(db, world):
    replay, _ = world
    assert coach.page_data(db, replay, None, None) == {"match_uuid": str(replay.match_uuid).lower(),
                                                       "team_coming_soon": True, "players": [], "report": None}


def test_a_point_with_a_wrong_verdict_is_disputed_and_keeps_its_feedback(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], payload())
    point = coach.points(db, report.id)[1]
    db.add(CoachFeedback(point_id=point.id, author_player_id=ids[1], verdict="wrong", note="not what happened"))
    db.commit()
    shown = coach.page_data(db, replay, None, None)["report"]["improve"][0]
    assert shown["disputed"] is True
    assert shown["feedback"][0]["verdict"] == "wrong" and shown["feedback"][0]["note"] == "not what happened"
    assert coach.page_data(db, replay, None, None)["report"]["good"][0]["disputed"] is False


def test_an_instance_without_a_time_or_caption_still_reads(db, world):
    replay, ids = world
    body = payload()
    body["points"][0]["instances"] = [{"round": 3}]
    body["points"][1]["instances"] = []
    coach.store_report(db, replay.match_id, ids[0], body)
    report = coach.page_data(db, replay, None, None)["report"]
    assert report["good"][0]["instances"] == [{"index": 0, "round": 3, "t": None, "t1": None, "caption": "", "feedback": []}]
    assert report["improve"][0]["instances"] == []


def test_the_route_renders_the_viewer_template_with_the_coach_data(db, world, captured):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    routes.coach_page(request(), str(replay.match_uuid), player="x", version="y", db=db)
    assert captured["name"] == "replays/replay.html"
    assert captured["context"]["coach"]["report"]["version"] == 1


def test_the_route_404s_in_demo_mode_and_for_an_unknown_or_unlinked_replay(db, world, captured, monkeypatch):
    replay, _ = world
    with pytest.raises(HTTPException) as missing:
        routes.coach_page(request(), "00000000-0000-4000-8000-000000000000", player=None, version=None, db=db)
    assert missing.value.status_code == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as demo:
        routes.coach_page(request(), str(replay.match_uuid), player=None, version=None, db=db)
    assert demo.value.status_code == 404

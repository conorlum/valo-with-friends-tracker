"""The util.json endpoint, the /util page and the coach's util facts, on SQLite with stored util rows."""

import json
import sys
from pathlib import Path

import pytest

REPLAYS = Path(__file__).resolve().parents[1] / "replays"
sys.path.insert(0, str(REPLAYS))
sys.path.insert(0, str(REPLAYS.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import feature_free_source, put_row  # noqa: E402
from test_gaps_web import page_db, page_linked  # noqa: E402,F401  (fixtures)
from test_replay_routes import request, status_of  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.models import AccountGroupMember  # noqa: E402
from app.models.friendship import Friendship  # noqa: E402
from app.models.replay import ReplayRoundUtil, UtilModel  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_gaps as gaps  # noqa: E402
from app.services import replay_util  # noqa: E402

KILLS = [{"t": 9.2, "killer": 0, "victim": 5, "k_side": "attack", "v_side": "defense", "before": [5, 5]}]


def cast(i=0, slot=0, value_kills=True):
    return {"i": i, "by": slot, "agent": "Breach", "t": 8.0, "t_end": 8.5, "u": 5000, "v": 5000,
            "names": ["4_Flashpoint"], "codes": ["Breach"], "kinds": ["blind"], "side": "attack", "phase": "contact",
            "after_decided": False, "alive": [5, 5], "trigger": None, "enemies": [5],
            "effects": [{"target": 5, "how": "blind", "t0": 8.4, "t1": 10.4, "known": True}],
            "kills": [{"t": 9.2, "killer": 0, "victim": 5, "ours": True, "ev": "affected"}] if value_kills else [],
            "followed": True, "harm": [], "cover": None, "presence": None, "spike": None, "ult_points": None,
            "activation": None}


def data(casts=None):
    return {"v": 1, "sides": {str(s): ("attack" if s < 5 else "defense") for s in range(10)}, "contact": 9.0,
            "plant": None, "defuse": None, "t_decided": 90.0, "kills": KILLS, "casts": casts or [cast()],
            "heals": [], "unused": [{"slot": 0, "t": 30.0, "kind": "held_at_death", "abilities": ["Flashpoint"], "kinds": ["blind"]}],
            "avail": {"heals": True, "causes": True, "charges": True, "ult": True, "friendly_status": False}}


@pytest.fixture
def db(page_db, monkeypatch):
    feature_free_source(monkeypatch)
    bind = page_db.get_bind()
    for table in (Friendship.__table__, AccountGroupMember.__table__, UtilModel.__table__):
        table.create(bind, checkfirst=True)
    return page_db


@pytest.fixture
def linked(page_linked):
    return page_linked


def put_util(db, replay, n, payload=None, status="ok", revision=None):
    db.merge(ReplayRoundUtil(replay_id=replay.id, round_number=n, status=status, fingerprint="f" * 16,
                             gaps_revision=gaps.GAPS_REVISION if revision is None else revision,
                             data=(payload or data()) if status == "ok" else None))
    db.commit()


def call(db, uuid=MATCH_UUID):
    return routes.replay_util_json(request(None), uuid, db)


def body_of(response):
    assert response.status_code == 200, response.body
    return json.loads(response.body)


def test_the_route_is_registered_before_the_round_route():
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": "GET", "path": f"/replays/{MATCH_UUID}/util.json", "root_path": ""}
    first = next(r for r in app.routes if r.matches(scope)[0] == Match.FULL)
    assert first.path == "/replays/{match_uuid}/util.json"
    assert "/util" in [r.path for r in app.routes]


def test_unknown_replays_and_demo_mode_are_plain_404s(db, linked, monkeypatch):
    assert status_of(lambda: call(db, uuid="00000000-0000-4000-8000-000000000000")) == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call(db)) == 404


def test_payload_values_casts_and_counts_stale_and_failed(db, linked):
    put_row(db, linked, 1)
    put_util(db, linked, 1)
    put_util(db, linked, 2, status="failed")
    put_util(db, linked, 3, revision=gaps.GAPS_REVISION - 1)
    body = body_of(call(db))
    (c,) = body["rounds"]["1"]["casts"]
    assert c["slot"] == 0 and c["kind"] == "blind" and c["kills"][0]["ev"] == "affected"
    assert body["rounds"]["1"]["unused"][0]["kind"] == "held_at_death"
    assert body["failed"] == [2] and body["stale"] == linked.round_count - 2
    assert body["estimator"] is False and c["value"] == 0.0, "no estimator yet: kills are worth nothing"
    assert all(set(p) == {"name", "agent", "team"} for p in body["players"].values())


def test_with_an_estimator_an_enabling_flash_is_worth_something(db, linked):
    from app.services import util_model

    rows = [(a, d, False, 10.0, None, i < 20 * a // (a + d)) for a in range(1, 6) for d in range(1, 6) for i in range(20)]
    db.merge(UtilModel(name="win", version=util_model.VERSION, key="k", data=util_model.fit(rows)))
    db.commit()
    put_util(db, linked, 1)
    body = body_of(call(db))
    assert body["rounds"]["1"]["casts"][0]["value"] > 0
    assert body["summary"]["0"]["best"][0]["round"] == 1


def render(db, monkeypatch, viewer=None):
    from app.routers import util_page

    seen = {}
    monkeypatch.setattr(util_page.templates, "TemplateResponse", lambda req, name, ctx, **kw: seen.update(ctx) or ctx)
    monkeypatch.setattr(util_page, "get_current_player", lambda request, db: viewer)
    util_page.util_page(request(None), db)
    return seen


def test_page_needs_a_login_and_404s_in_demo_mode(db, linked, monkeypatch):
    assert render(db, monkeypatch)["login_note"]
    monkeypatch.setattr(settings, "demo_mode", True)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        render(db, monkeypatch)


def test_page_for_a_viewer_shows_only_their_circle(db, linked, monkeypatch):
    from app.models.player import Player

    put_util(db, linked, 1)
    ctx = replay_util._replay_context(db, {linked.id})[linked.id]
    me = db.get(Player, ctx["players"][0]["player_id"])
    seen = render(db, monkeypatch, viewer=me)
    assert seen["login_note"] is None
    assert [p["name"] for p in seen["players"]] == [me.display_name]
    other = db.get(Player, ctx["players"][5]["player_id"])
    assert render(db, monkeypatch, viewer=other)["players"] == []


def test_coach_util_facts_are_the_players_casts_with_ids():
    from app.coach import brief

    round_util = {"casts": [{"slot": 0, "t": 8.0, "ability": "Flashpoint", "kind": "blind", "value": 2.5, "enemies": 1,
                             "kills": [{"ours": True}], "harm": [], "harm_died": 0, "followed": True, "wasted": False,
                             "cover": {"blocked_s": 3.5}},
                            {"slot": 1, "t": 9.0, "ability": "x", "kind": "smoke", "value": 0.0}],
                  "unused": [{"slot": 0, "t": 30.0, "kind": "held_at_death", "kinds": ["blind"]}]}
    facts = brief.util_facts(round_util, 0)
    assert [f["t"] for f in facts] == [8.0, 30.0]
    assert facts[0]["kills"] == 1 and facts[0]["value"] == 2.5 and facts[0]["blocked_s"] == 3.5
    assert brief.util_facts(None, 0) == []

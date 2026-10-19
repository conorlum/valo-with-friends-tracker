"""The moments.json endpoint and its DB loader (app/services/replay_moments.py). SQLite, no engine: moments rows
and control rows are written in their stored shapes."""

import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import feature_free_source, packed, put_row  # noqa: E402
from test_gaps_web import page_db, page_linked  # noqa: E402,F401  (fixtures: the page tables are needed for the names)
from test_replay_routes import request, status_of  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.models.friendship import Friendship  # noqa: E402
from app.models.replay import ReplayRoundControl, ReplayRoundMoments  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_gaps as gaps  # noqa: E402
from app.services import replay_moments  # noqa: E402


@pytest.fixture
def db(page_db, monkeypatch):
    feature_free_source(monkeypatch)      # the fixtures' control inputs, so round fingerprints exist
    Friendship.__table__.create(page_db.get_bind())     # which team is the friends' (enemies are named by agent)
    return page_db


@pytest.fixture
def linked(page_linked):
    return page_linked


def current_fingerprint(db, replay, n):
    control = rc.round_fingerprint(replay, rc.side_groups(db, replay), n,
                                   context=rc.resolve_current_geometry(db, replay.map_name))
    assert control is not None
    return gaps.gap_fingerprint(control, replay.map_name)


def fight(**over):
    f = {"i": 0, "slot": 0, "t0": 10.0, "t1": 14.0, "died": {"t": 13.0}, "kill_detail": []}
    f.update(over)
    return f


def put_moments(db, replay, n, fights=None, fingerprint=None, revision=None, status="ok"):
    db.merge(ReplayRoundMoments(replay_id=replay.id, round_number=n, status=status,
                                fingerprint=fingerprint or current_fingerprint(db, replay, n),
                                gaps_revision=gaps.GAPS_REVISION if revision is None else revision,
                                fights={"v": 1, "sight": "x", "fights": fights if fights is not None else [fight()]}))
    db.commit()


def call(db, headers=None, uuid=MATCH_UUID):
    return routes.replay_moments_json(request(headers), uuid, db)


def body_of(response):
    assert response.status_code == 200, response.body
    return json.loads(response.body)


def test_the_route_is_registered():
    from app.main import app

    assert "/replays/{match_uuid}/moments.json" in [r.path for r in app.routes]


def test_the_path_reaches_the_moments_route():
    """The first route that fully matches the path is the one that answers it: /{round_number}.json declared
    earlier took "moments" as a round number and answered 422 on the live site."""
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": "GET", "path": f"/replays/{MATCH_UUID}/moments.json", "root_path": ""}
    first = next(r for r in app.routes if r.matches(scope)[0] == Match.FULL)
    assert first.path == "/replays/{match_uuid}/moments.json"


def test_the_endpoint_404s_like_the_gaps_route(db, linked, monkeypatch):
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    response = call(db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "no_map"}
    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", False)
    recipe = linked.recipe
    linked.recipe = recipe.replace(f".c{rc.condense_revision(recipe)}.", ".c9.")
    db.commit()
    response = call(db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "old_blob"}
    linked.recipe, linked.link_status = recipe, "unlinked"
    db.commit()
    response = call(db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "unlinked"}


def test_unknown_replays_and_demo_mode_are_plain_404s(db, linked, monkeypatch):
    assert status_of(lambda: call(db, uuid="00000000-0000-4000-8000-000000000000")) == 404
    assert status_of(lambda: call(db, uuid="not-a-uuid")) == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call(db)) == 404


def test_payload_shape_and_stale_count(db, linked):
    put_row(db, linked, 1)
    put_moments(db, linked, 1)
    body = body_of(call(db))
    assert body["rounds"]["1"]["status"] == "ok"
    [f] = body["rounds"]["1"]["fights"]
    assert f["slot"] == 0 and f["died"]["t"] == 13.0 and "control_m2" in f["died"]
    assert body["players"] and all(set(p) == {"name", "agent", "team"} for p in body["players"].values())
    assert isinstance(body["attack_round1"], list) and len(body["attack_round1"]) == 5
    assert set(body["summary"]) == set(body["players"])
    assert body["stale"] == linked.round_count - 1


def test_the_other_team_than_the_friends_is_named_by_agent(db, linked):
    from app.services import replays as replay_service

    page = replay_service.page_context(db, linked)["players"]
    first, *_ = sorted(page.values(), key=lambda p: p["slot"])
    mate = next(p for p in page.values() if p["team"] == first["team"] and p["slot"] != first["slot"])
    db.add(Friendship(owner_player_id=first["player_id"], friend_player_id=mate["player_id"]))
    db.commit()
    players = body_of(call(db))["players"]
    for slot, p in page.items():
        expected = p["name"] if p["team"] == first["team"] else f"the enemy {p['agent']}"
        assert players[slot]["name"] == expected


def test_old_fingerprint_old_revision_and_missing_rows_are_stale(db, linked):
    put_moments(db, linked, 1, fingerprint="0" * 16)
    assert body_of(call(db))["stale"] == linked.round_count
    put_moments(db, linked, 1, revision=gaps.GAPS_REVISION - 1)
    assert body_of(call(db))["stale"] == linked.round_count
    put_moments(db, linked, 1)
    assert body_of(call(db))["stale"] == linked.round_count - 1


def test_a_current_failed_round_is_listed_as_failed_not_stale(db, linked):
    # C5: the gaps ran but the moments raised; the row is current, so it is not stale, and the tab must say so
    put_moments(db, linked, 1, status="failed", fights=[])
    body = body_of(call(db))
    assert body["failed"] == [1] and body["stale"] == linked.round_count - 1
    assert body["rounds"]["1"] == {"status": "failed", "fights": []}
    put_moments(db, linked, 1, status="failed", fights=[], fingerprint="0" * 16)   # an old failure is just stale
    body = body_of(call(db))
    assert body["failed"] == [] and body["stale"] == linked.round_count
    put_moments(db, linked, 1)
    assert body_of(call(db))["failed"] == []


def test_knowledge_events_reach_the_summary(db, linked):
    put_row(db, linked, 1)
    row = db.get(ReplayRoundControl, (linked.id, 1))
    row.data = packed({"knowledge_events": {"attack": [[5.0, 0, "footsteps"], [6.0, 0, "damage"]],
                                            "defend": [[7.0, 1, "footsteps"]]}})
    db.commit()
    put_moments(db, linked, 1, fights=[])
    info = body_of(call(db))["summary"]["0"]["info"]
    assert info["heard"] == [{"round": 1, "t": 5.0}]
    # an unreadable blob is skipped, not fatal
    row.data = b"garbage"
    db.commit()
    assert body_of(call(db))["summary"]["0"]["info"]["heard"] == []


def test_etag_gives_a_304_and_follows_the_moments(db, linked):
    put_moments(db, linked, 1)
    first = call(db)
    again = call(db, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    put_moments(db, linked, 1, fights=[fight(t1=15.0)])
    changed = call(db, headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200 and changed.headers["etag"] != first.headers["etag"]


def test_the_loader_is_quick_on_the_seeded_replay(db, linked):
    put_row(db, linked, 1)
    put_moments(db, linked, 1)
    start = time.perf_counter()
    replay_moments.load_moments_payload(db, linked)
    took = time.perf_counter() - start
    print(f"load_moments_payload: {took * 1000:.1f} ms")
    assert took < 5.0

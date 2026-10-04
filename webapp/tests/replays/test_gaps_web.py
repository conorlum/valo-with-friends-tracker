"""The gaps.json endpoint, the page's Gaps layer and `?t=` (docs/superpowers/plans/2026-10-04-timing-gaps-viewer.md,
section 6, its review amendments and S6). SQLite, no engine and no detector: gap rows are written in the stored
shape, as scripts/compute_control.py would."""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import CONTROL_TABLES, db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_replay_routes import request, status_of  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import ImpactScore, RoundPlayerStat  # noqa: E402
from app.models.replay import Replay, ReplayGap, ReplayRoundGapRun  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.replays import store  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_gaps as gaps  # noqa: E402
from app.services import replay_gaps_view as gaps_view  # noqa: E402
from app.services import replays as service  # noqa: E402


def gap(replay, n, seq, **over):
    row = {"seq": seq, "kind": "predicted", "map": replay.map_name, "victim_slot": 0, "victim_side": "attack",
           "t_open": 10.0 + seq, "t_last_exposed": 12.0, "t_close": 13.0 + seq, "spot_cell": 130, "victim_cell": 0,
           "distance_m": 9.5, "angle_deg": 160.0, "qualified_s": 2.0, "flicker": False, "cause": "open_timing",
           "cause_detail": {}, "choke_seq": [1, 2], "route": [[[8.0, 400, 200], [10.0, 500, 300]]],
           "candidate_slots": [5], "candidate_distances": {"5": 20.0}, "checked_at": None, "stood_at": None,
           "stood_by": None, "shot_at": None, "shot_by": None, "killed_at": None, "killed_by": None,
           "victim_won_at": None, "context": {"t_round": 10.0}, "linked_seq": None}
    row.update(over)
    return ReplayGap(replay_id=replay.id, round_number=n, **row)


def current_fingerprint(db, replay, n):
    return gaps.gap_fingerprint(rc.round_fingerprint(replay, rc.side_groups(db, replay), n), replay.map_name)


def put_run(db, replay, n, status="ok", fingerprint=None, rows=()):
    db.merge(ReplayRoundGapRun(replay_id=replay.id, round_number=n, status=status,
                               fingerprint=fingerprint or current_fingerprint(db, replay, n),
                               gaps_revision=gaps.GAPS_REVISION, gap_count=len(rows),
                               error=None if status == "ok" else "GapError: boom"))
    for row in rows:
        db.merge(row)
    db.commit()


def call(db, n=1, headers=None, uuid=MATCH_UUID):
    return routes.replay_round_gaps(request(headers), uuid, n, db)


def body_of(response):
    assert response.status_code == 200, response.body
    return json.loads(response.body)


# ---------------------------------------------------------------- the four statuses


def test_a_fresh_run_serves_its_rows_in_seq_order_with_the_chokes(db, linked):
    put_run(db, linked, 1, rows=[gap(linked, 1, 1, t_open=5.0), gap(linked, 1, 0, killed_at=11.0, killed_by=5)])
    status, rows = gaps.round_gaps(db, linked, 1)
    assert status == "ok" and [r.seq for r in rows] == [0, 1]
    response = call(db)
    assert response.headers["cache-control"] == "private, no-cache"
    body = body_of(response)
    assert body["status"] == "ok" and body["stale"] is False and body["revision"] == gaps.GAPS_REVISION
    assert [r["seq"] for r in body["rows"]] == [1, 0]                     # the view's order: by t_open
    first = body["rows"][1]
    assert first["used"] == "killed" and first["spot_xy"] == [20, 12] and first["victim_xy"] == [4, 4]
    assert first["route"] == [[[8.0, 400, 200], [10.0, 500, 300]]] and first["candidate_slots"] == [5]
    assert body["chokes"] == gaps_view.choke_points(linked.map_name)


def test_tombstoned_chokes_are_left_out(db, linked):
    put_run(db, linked, 1)
    chokes = body_of(call(db))["chokes"]
    deleted = {str(c.id) for c in choke_assets.load(linked.map_name) or [] if c.deleted}
    assert not deleted & set(chokes)
    assert all(set(c) == {"name", "x", "y"} for c in chokes.values())


def test_a_run_from_older_inputs_is_stale_but_still_served(db, linked, monkeypatch):
    put_run(db, linked, 1, rows=[gap(linked, 1, 0)])
    monkeypatch.setattr(gaps, "GAPS_REVISION", gaps.GAPS_REVISION + 1)       # new gap rules
    body = body_of(call(db))
    assert body["status"] == "stale" and body["stale"] is True and len(body["rows"]) == 1
    monkeypatch.undo()
    put_run(db, linked, 2, fingerprint="0" * 16, rows=[gap(linked, 2, 0)])
    assert body_of(call(db, 2))["status"] == "stale"


def test_a_changed_control_input_stales_the_gaps(db, linked, monkeypatch):
    put_run(db, linked, 1, rows=[gap(linked, 1, 0)])
    monkeypatch.setattr(rc, "geometry_inputs", lambda name: {"sight": "changed", "walk": "", "specials": [], "scale": 1})
    assert gaps.round_gaps(db, linked, 1)[0] == "stale"


def test_a_failed_run_and_a_missing_run_have_no_rows(db, linked):
    put_run(db, linked, 1, status="failed", rows=[gap(linked, 1, 0)])
    assert body_of(call(db)) == {"status": "failed", "stale": False, "revision": gaps.GAPS_REVISION, "rows": [],
                                 "chokes": gaps_view.choke_points(linked.map_name)}
    body = body_of(call(db, 2))
    assert body["status"] == "not_computed" and body["rows"] == [] and body["stale"] is False


# ---------------------------------------------------------------- 404s


def test_the_endpoint_404s_like_the_control_routes(db, linked, monkeypatch):
    put_run(db, linked, 1, rows=[gap(linked, 1, 0)])
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


def test_rounds_out_of_range_unknown_replays_and_demo_mode_are_plain_404s(db, linked, monkeypatch):
    put_run(db, linked, 1)
    assert status_of(lambda: call(db, 0)) == 404
    assert status_of(lambda: call(db, linked.round_count + 1)) == 404
    assert call(db, linked.round_count).status_code == 200
    assert status_of(lambda: call(db, 1, uuid="00000000-0000-4000-8000-000000000000")) == 404
    assert status_of(lambda: call(db, 1, uuid="not-a-uuid")) == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call(db, 1)) == 404


# ---------------------------------------------------------------- the ETag


def test_the_etag_gives_a_304_and_covers_the_whole_body(db, linked, monkeypatch):
    put_run(db, linked, 1, rows=[gap(linked, 1, 0)])
    first = call(db)
    assert first.status_code == 200
    again = call(db, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.body == b""
    # The same rows going stale is a new body, so a new tag.
    monkeypatch.setattr(gaps, "GAPS_REVISION", gaps.GAPS_REVISION + 1)
    stale = call(db, headers={"If-None-Match": first.headers["etag"]})
    assert stale.status_code == 200 and stale.headers["etag"] != first.headers["etag"]
    assert body_of(stale)["rows"] == body_of(first)["rows"]
    monkeypatch.undo()
    put_run(db, linked, 1, rows=[gap(linked, 1, 1)])
    assert call(db, headers={"If-None-Match": first.headers["etag"]}).status_code == 200


def test_the_route_is_registered():
    from app.main import app

    assert "/replays/{match_uuid}/{round_number}/gaps.json" in [r.path for r in app.routes]


# ---------------------------------------------------------------- the page


def test_page_context_offers_gaps_only_on_a_linked_replay_with_control(db, condensed, monkeypatch):
    # Unlinked here (the linked context is in the page test below); `match` is built the same way.
    replay = db.get(Replay, store.store_replay(db, condensed, source="upload").replay_id)
    assert service.page_context(db, replay)["match"]["gaps"] is False


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):   # impact_scores.trade_detail, for the linked page's tables
    return "JSON"


@pytest.fixture
def page_db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=CONTROL_TABLES + [RoundPlayerStat.__table__, ImpactScore.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def page_linked(page_db, condensed):
    from test_replay_store import add_match

    add_match(page_db)
    replay = page_db.get(Replay, store.store_replay(page_db, condensed, source="local").replay_id)
    assert replay.link_status == "linked"
    return replay


def render_page(db, monkeypatch, query=b""):
    from starlette.requests import Request

    from app.templates import templates

    seen = {}
    monkeypatch.setattr(routes.templates, "TemplateResponse", lambda req, name, ctx, **kw: seen.update(ctx))
    page_request = Request({"type": "http", "method": "GET", "path": "/", "query_string": query, "headers": []})
    routes.replay_page(page_request, MATCH_UUID, db)
    return seen, templates.env.get_template("replays/replay.html").render(**seen, request=request())


def test_a_linked_replay_with_control_shows_the_gaps_layer(page_db, page_linked, monkeypatch):
    assert service.page_context(page_db, page_linked)["match"]["gaps"] is True
    seen, html = render_page(page_db, monkeypatch)
    assert seen["gaps"] is True and seen["start_t"] is None
    assert 'data-replay-layer="gaps"' in html and 'data-replay-tab="gaps"' in html
    assert "/static/js/replay_gaps.js" in html and "loadGaps" in html and "/gaps.json" in html
    assert html.index("/static/js/replay_gaps.js") < html.index("/static/js/replay.js")
    assert "window.viewer.seek(" not in html
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    assert service.page_context(page_db, page_linked)["match"]["gaps"] is False
    seen, html = render_page(page_db, monkeypatch)
    assert seen["gaps"] is False
    assert "replay_gaps.js" not in html and 'data-replay-layer="gaps"' not in html and "loadGaps" not in html


def test_an_unlinked_page_has_no_gaps_layer(page_db, page_linked, monkeypatch):
    page_linked.link_status = "unlinked"
    page_db.commit()
    seen, html = render_page(page_db, monkeypatch)
    assert seen["gaps"] is False and "replay_gaps.js" not in html and "loadGaps" not in html


def test_the_start_time_is_parsed_and_sought_after_the_round(page_db, page_linked, monkeypatch):
    seen, html = render_page(page_db, monkeypatch, b"round=2&t=7.5")
    assert seen["start_round"] == 2 and seen["start_t"] == 7.5
    assert "showRound(2).then(" in html and "window.viewer.seek(7.5)" in html
    for bad in (b"t=abc", b"t=-1", b"t=nan", b"t=inf", b"t="):
        seen, html = render_page(page_db, monkeypatch, bad)
        assert seen["start_t"] is None, bad
        assert "window.viewer.seek(" not in html


@pytest.mark.parametrize("value, expected", [("0", 0.0), ("12", 12.0), ("7.25", 7.25), (None, None), ("", None),
                                             ("x", None), ("-0.5", None), ("NaN", None), ("1e999", None)])
def test_start_time_values(value, expected):
    assert routes.start_time(value) == expected

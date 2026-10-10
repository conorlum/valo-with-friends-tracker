"""The upload page's "Show Replay Info" panel (app/services/replay_progress.py): every stored round counted once,
as done or at its first stuck step, by the dispatcher's own freshness tests. SQLite, no engine."""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_gaps_task import _gap_run  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixtures)
from test_replay_upload import request  # noqa: E402

from app.config import settings  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_progress as progress  # noqa: E402


def steps(body) -> dict:
    return {s["key"]: s["rounds"] for s in body["steps"]}


def test_nothing_computed_is_all_control_missing(db, linked):
    body = progress.compute(db)
    n = linked.round_count
    assert body["total_rounds"] == n and body["total_replays"] == 1
    assert body["done"] == 0 and body["needs_work"] == n and body["worker_will_do"] == n
    assert steps(body) == {"control_missing": n}


def test_each_round_is_counted_once_at_its_first_stuck_step(factory, db, linked):
    n = linked.round_count
    for r in range(1, n + 1):
        put_row(db, linked, r)
    put_row(db, linked, 2, fingerprint="0" * 16)          # control out of date
    put_row(db, linked, 3, status="failed")               # control failed under the current inputs
    _gap_run(factory, linked, 1)                          # done
    _gap_run(factory, linked, 4, status="failed")         # gaps failed under the current inputs
    _gap_run(factory, linked, 5, fingerprint="0" * 16)    # gaps out of date
    db.expire_all()
    body = progress.compute(db)
    assert body["done"] == 1
    assert steps(body) == {"control_stale": 1, "control_failed": 1, "gaps_failed": 1, "gaps_stale": 1,
                           "gaps_missing": n - 5}
    assert body["needs_work"] == n - 1
    assert body["needs_a_person"] == 2 and body["worker_will_do"] == n - 3
    assert sum(steps(body).values()) + body["done"] == body["total_rounds"]


def test_an_older_recipe_needs_a_reparse_before_anything_else(factory, db, linked):
    for r in range(1, linked.round_count + 1):
        put_row(db, linked, r)
        _gap_run(factory, linked, r)
    db.expire_all()
    assert progress.compute(db)["done"] == linked.round_count
    linked.recipe = "000000000000.c9.f1.000000000"
    db.commit()
    body = progress.compute(db)
    assert body["done"] == 0 and steps(body) == {"reparse_local": linked.round_count}, "a local file: by hand"
    linked.source = "upload"
    db.commit()
    assert steps(progress.compute(db)) == {"reparse": linked.round_count}


def test_the_count_is_shared_for_ttl(db, linked, monkeypatch):
    calls = []
    monkeypatch.setattr(progress, "_cached", None)
    monkeypatch.setattr(progress, "compute", lambda session: calls.append(1) or {"n": len(calls)})
    assert progress.cached(db, now=100.0) == {"n": 1}
    assert progress.cached(db, now=100.0 + progress.TTL_S - 1) == {"n": 1}
    assert progress.cached(db, now=100.0 + progress.TTL_S) == {"n": 2}


def test_the_route_is_for_sessions_that_entered_the_code(db, linked, monkeypatch):
    monkeypatch.setattr(settings, "replay_upload_code", "letmein")
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    monkeypatch.setattr(progress, "_cached", None)
    with pytest.raises(HTTPException) as raised:
        routes.upload_progress(request({}), db)
    assert raised.value.status_code == 403
    response = routes.upload_progress(request({"replay_upload_ok": True}), db)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert b'"control_missing"' in response.body and b'"checked_at"' in response.body
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as raised:
        routes.upload_progress(request({"replay_upload_ok": True}), db)
    assert raised.value.status_code == 404


def test_the_page_has_the_closed_panel_below_the_uploads(db, monkeypatch):
    monkeypatch.setattr(settings, "replay_upload_code", "letmein")
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    page = routes.upload_form(request({"replay_upload_ok": True}), db).body.decode("utf-8")
    assert '<details class="card replay-progress" data-progress>' in page
    assert "<summary>Show Replay Info</summary>" in page
    assert page.index("data-upload-section") < page.index("data-progress")
    assert "data-progress" not in routes.upload_form(request({}), db).body.decode("utf-8"), "not before the code"

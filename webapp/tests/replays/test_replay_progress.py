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
from app.services import replay_match_states  # noqa: E402
from app.services import replay_progress as progress  # noqa: E402


def steps(body) -> dict:
    return {s["key"]: s["rounds"] for s in body["steps"]}


def test_step_registry_includes_layout_blockers():
    assert [key for key, _, _ in progress.STEPS] == [
        "reparse", "reparse_local", "incomplete", "no_map", "layout_pending", "layout_unknown",
        "old_blob", "features_pending", "source_error", "control_missing", "control_stale", "control_unsupported", "control_failed",
        "gaps_missing", "gaps_stale", "gaps_failed",
    ]
    assert dict((key, (label, automatic)) for key, label, automatic in progress.STEPS)["layout_pending"] == (
        "Map changed in this patch; redraw pending", False)
    assert dict((key, (label, automatic)) for key, label, automatic in progress.STEPS)["layout_unknown"] == (
        "Patch unknown on a map with several layouts", False)


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


@pytest.mark.parametrize("manual_step", ["control_failed", "gaps_failed"])
def test_unsupported_rounds_need_no_person_and_do_not_hide_manual_failures(factory, db, linked, manual_step):
    from app.models.replay import ReplayRoundControl
    from app.templates import templates
    from types import SimpleNamespace as NS

    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
        _gap_run(factory, linked, n)
    put_row(db, linked, 1, status="failed")
    stored = db.get(ReplayRoundControl, (linked.id, 1))
    stored.error = "features_source_unavailable: unverified opening/default state in active feature timeline"
    db.commit()
    body = progress.compute(db)
    assert progress.round_states(db)["states"][linked.id, 1] == "control_unsupported"
    assert steps(body) == {"control_unsupported": 1}
    assert (body["unsupported"], body["needs_a_person"], body["worker_will_do"]) == (1, 0, 0)
    assert [key for key, _, _ in progress.STEPS].index("control_unsupported") < [
        key for key, _, _ in progress.STEPS].index("control_failed")

    def cell():
        replay_match_states.refresh(db)
        return templates.env.get_template("replays/_up_to_date_cell.html").render(
            match=NS(external_id=str(linked.match_uuid)),
            replay_states=progress.match_states(db, [str(linked.match_uuid)]))

    assert "not supported" in cell() and "needs a person" not in cell()
    if manual_step == "control_failed":
        put_row(db, linked, 2, status="failed")
    else:
        _gap_run(factory, linked, 2, status="failed")
    body = progress.compute(db)
    assert progress.round_states(db)["states"][linked.id, 2] == manual_step
    assert (body["unsupported"], body["needs_a_person"]) == (1, 1)
    assert "needs a person" in cell()


def test_a_match_is_up_to_date_only_when_every_round_is_done(factory, db, linked, monkeypatch):
    n, uuid = linked.round_count, str(linked.match_uuid).lower()
    for r in range(1, n + 1):
        put_row(db, linked, r)
        if r > 1:
            _gap_run(factory, linked, r)
    db.expire_all()
    replay_match_states.refresh(db)
    assert progress.match_states(db, [uuid.upper(), "other", None]) == {
        uuid: {"done": n - 1, "total": n, "label": "Timing gaps never computed", "automatic": True}}
    _gap_run(factory, linked, 1)
    db.expire_all()
    replay_match_states.refresh(db)
    assert progress.match_states(db, [uuid]) == {uuid: {"done": n, "total": n, "label": None, "automatic": None}}
    put_row(db, linked, 2, status="failed")   # the earliest stuck step names the match, not the commonest
    linked.recipe = "000000000000.c9.f1.000000000"
    db.commit()
    replay_match_states.refresh(db)
    assert progress.match_states(db, [uuid])[uuid]["label"] == "Needs a re-ingest (older condenser, local file)"
    monkeypatch.setattr(settings, "demo_mode", True)
    assert progress.match_states(db, [uuid]) == {}


def test_the_match_list_has_the_column_beside_watch():
    from app.templates import templates
    from types import SimpleNamespace as NS
    uuid = "0f452716-1e90-4782-afba-29229fdab922"
    match = NS(external_id=uuid, team1_rounds_won=13, team2_rounds_won=7, map_name="Sunset", played_at=None)

    def page(states):
        return templates.env.get_template("matches/list.html").render(
            matches=[match], showing_own_matches=False, current_player=None, watchable={uuid}, replay_states=states)

    assert "&#10003; Yes" in page({uuid: {"done": 20, "total": 20, "label": None, "automatic": None}})
    stuck = page({uuid: {"done": 3, "total": 20, "label": "Map control out of date", "automatic": True}})
    assert "3/20 rounds" in stuck and "Map control out of date" in stuck and "the worker will do it" in stuck
    assert "<th>Watch</th>" in stuck and stuck.index("<th>Watch</th>") < stuck.index("Up to date</th>")


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
    assert 'p.unsupported' in page and 'not supported in their round' in page
    assert 'Nobody: not supported' in page
    assert "data-progress" not in routes.upload_form(request({}), db).body.decode("utf-8"), "not before the code"


def test_progress_panel_renders_unsupported_count_and_nobody_offline(db, linked, monkeypatch):
    import json
    import shutil
    import subprocess
    from app.models.replay import ReplayRoundControl

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed for the offline DOM check")
    monkeypatch.setattr(settings, "replay_upload_code", "letmein")
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    put_row(db, linked, 1, status="failed")
    db.get(ReplayRoundControl, (linked.id, 1)).error = "features_source_unavailable: reset unobserved"
    db.commit()
    result = progress.compute(db)
    page = routes.upload_form(request({"replay_upload_ok": True}), db).body.decode("utf-8")
    script = page[page.index("    function cell("):page.index("    function poll()")]
    harness = r'''
const assert = require("node:assert/strict"), vm = require("node:vm");
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
function element() {
  return {children: [], appendChild(child) { this.children.push(child); },
    set textContent(value) { this.text = value; this.children = []; },
    get textContent() { return this.text; }};
}
const nodes = {}, panel = {querySelector(key) { return nodes[key] ||= element(); }};
const context = {panel, document: {createElement: element}, body: {}, input};
vm.runInNewContext(input.script + "\nrender(input.result);", context);
assert.match(nodes["[data-progress-headline]"].textContent, /1 not supported in their round/);
const rows = nodes["[data-progress-rows]"].children;
assert.equal(rows.find(r => r.children[0].textContent === "Map control: not supported in this round")
  .children[2].textContent, "Nobody: not supported");
input.result.unsupported = 0;
input.result.steps = [];
vm.runInNewContext("render(input.result);", context);
assert.doesNotMatch(nodes["[data-progress-headline]"].textContent, /not supported/);
'''
    completed = subprocess.run([node, "-e", harness], input=json.dumps({"script": script, "result": result}),
                               text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr

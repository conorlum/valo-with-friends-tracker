"""Replay errors are available only to upload-code sessions, outside the demo; no network or DB needed."""

import copy
import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.routers import replays as routes
from app.services import replay_errors


def request(session=None):
    return Request({"type": "http", "method": "GET", "path": "/replays/upload", "query_string": b"",
                    "headers": [], "session": {} if session is None else session})


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", "test-code")
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    # Rendering the upload form asks the worker's health for its batch limit; keep these tests offline.
    monkeypatch.setattr(routes.uploads, "batch_limit", lambda worker: 5)


@pytest.fixture
def result():
    row = {"map": "Summit", "match": "00000000-0000-4000-8000-000000000001", "round": 2, "t": 35.2,
           "agent": "Omen", "figures": {"z": 3.0, "candidates": [[5, [4.0], 0.42]]},
           "link": "/replays/00000000-0000-4000-8000-000000000001?round=2&t=35.2",
           "error": "No single box explains this height", "revision": {"height_rules": 1},
           "reproduce": "Read the stored height metadata"}
    event = {"kind": "page_error", "key": "GET /x", "count": 1, "first_seen": None, "last_seen": None,
             "error": "ValueError: synthetic", "context": {}, "link": "", "reproduce": "Read site_events"}
    return {"checked_at": "2026-10-30T12:00:00+00:00",
            "kinds": [{"key": key, "label": label, "group": "Replays", "shape": "replay", "count": 51, "note": "Stored evidence",
                       "rows": [copy.deepcopy(event if key in replay_errors.EVENT_KINDS else row)
                                for _ in range(51)]} for key, label in replay_errors.KINDS]}


@pytest.mark.parametrize("name", ["upload_errors", "upload_errors_prompt"])
@pytest.mark.parametrize("gate,status", [("session", 403), ("demo", 404), ("code", 404), ("worker", 404)])
def test_errors_require_the_upload_gate(monkeypatch, name, gate, status):
    session = {"replay_upload_ok": True}
    if gate == "session":
        session.clear()
    elif gate == "demo":
        monkeypatch.setattr(settings, "demo_mode", True)
    elif gate == "code":
        monkeypatch.setattr(settings, "replay_upload_code", None)
    else:
        monkeypatch.setattr(settings, "replay_worker_url", None)
    cached = Mock(side_effect=AssertionError("a refused request must not compute errors"))
    monkeypatch.setattr(replay_errors, "cached", cached)
    with pytest.raises(HTTPException) as raised:
        getattr(routes, name)(request(session), Mock())
    assert raised.value.status_code == status
    cached.assert_not_called()


def test_json_keeps_all_kinds_and_counts_but_limits_display_rows(monkeypatch, result):
    cached = Mock(return_value=result)
    monkeypatch.setattr(replay_errors, "cached", cached)
    monkeypatch.setattr(routes, "_deploy_banner", lambda db: None)   # the banner reads deploy_drains; not under test here
    db = Mock()
    response = routes.upload_errors(request({"replay_upload_ok": True}), db)
    body = json.loads(response.body)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert body == {**replay_errors.page(result), "deploy": None}
    assert [entry["key"] for entry in body["kinds"]] == [key for key, _ in replay_errors.KINDS]
    assert all(entry["count"] == 51 and len(entry["rows"]) == 50 for entry in body["kinds"])
    assert all(len(entry["rows"]) == 51 for entry in result["kinds"])
    cached.assert_called_once_with(db)
    db.rollback.assert_called_once_with()


@pytest.mark.parametrize("kind", [None, "box_conflict"])
def test_prompt_uses_the_full_result_and_selected_kind(monkeypatch, result, kind):
    monkeypatch.setattr(replay_errors, "cached", lambda db: result)
    db = Mock()
    response = routes.upload_errors_prompt(request({"replay_upload_ok": True}), db, kind=kind)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.body.decode() == replay_errors.prompt(result, kind)
    db.rollback.assert_called_once_with()


def test_unknown_prompt_kind_is_404(monkeypatch, result):
    monkeypatch.setattr(replay_errors, "cached", lambda db: result)
    db = Mock()
    with pytest.raises(HTTPException) as raised:
        routes.upload_errors_prompt(request({"replay_upload_ok": True}), db, kind="unknown")
    assert raised.value.status_code == 404
    db.rollback.assert_called_once_with()


def test_error_routes_are_registered_before_the_replay_routes():
    registered = {route.path: route for route in routes.router.routes}
    for path in ("/replays/upload/errors.json", "/replays/upload/errors/prompt.txt"):
        assert path in registered and registered[path].methods == {"GET"}
        assert list(registered).index(path) < list(registered).index("/replays/{match_uuid}")


def test_upload_card_is_closed_below_progress_and_requires_the_code(monkeypatch):
    cached = Mock(side_effect=AssertionError("the card computes only when opened"))
    monkeypatch.setattr(replay_errors, "cached", cached)
    page = routes.upload_form(request({"replay_upload_ok": True}), Mock()).body.decode()
    assert '<details class="card replay-errors" data-errors>' in page
    assert "<summary>Show site health</summary>" in page
    assert page.index("data-progress") < page.index("data-errors")
    assert "data-errors" not in routes.upload_form(request(), Mock()).body.decode()
    cached.assert_not_called()


def test_errors_script_uses_text_nodes_and_loads_only_on_first_open():
    page = routes.upload_form(request({"replay_upload_ok": True}), Mock()).body.decode()
    scripts = [script.split("</script>", 1)[0] for script in page.split("<script>")[1:]]
    script = next(script for script in scripts if 'document.querySelector("[data-errors]")' in script)
    assert 'addEventListener("toggle"' in script
    assert "panel.open" in script and "loaded" in script
    assert "/replays/upload/errors.json" in script
    assert "/replays/upload/errors/prompt.txt" in script
    assert "navigator.clipboard.writeText" in script
    assert 'document.createElement("textarea")' in script and "Ctrl+C" in script
    assert "textContent" in script and "innerHTML" not in script
    assert "setInterval" not in script


@pytest.mark.parametrize("clipboard", ["ok", "missing", "reject"])
def test_errors_dom_groups_rows_and_copies_with_manual_fallback(result, clipboard):
    """Run the actual inline script against a small DOM, without a browser or localhost server."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed for the offline DOM check")
    page = routes.upload_form(request({"replay_upload_ok": True}), Mock()).body.decode()
    script = next(script.split("</script>", 1)[0] for script in page.split("<script>")[1:]
                  if 'document.querySelector("[data-errors]")' in script)
    shown = replay_errors.page(result)
    for entry in shown["kinds"][1:]:
        entry.update(count=0, rows=[], note=None)
    unfinished = next(entry for entry in shown["kinds"] if entry["key"] == "feature_failed")
    unfinished["note"] = "checking 0 of 3"
    shown["kinds"][0]["rows"] = shown["kinds"][0]["rows"][:3]
    shown["kinds"][0]["rows"][1]["map"] = "Ascent"
    shown["kinds"][0]["rows"][2]["map"] = "Ascent"
    shown["kinds"][0]["rows"][0]["error"] = '<img src=x onerror="fail()">'
    harness = r'''
const assert = require("node:assert/strict"), vm = require("node:vm");
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this._text = ""; }
  appendChild(child) { this.children.push(child); child.parent = this; return child; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { throw Error("row data must use textContent"); }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
function descendants(parent, tag) {
  return parent.children.flatMap(child => (child.tag === tag ? [child] : []).concat(descendants(child, tag)));
}
const panel = new Element("details"), status = new Element("p"), body = new Element("div"), all = new Element("button");
all.textContent = "Copy prompt"; all.disabled = true; body.hidden = true; panel.open = false;
panel.appendChild(status); panel.appendChild(all); panel.appendChild(body);
panel.querySelector = selector => ({"[data-errors-deploy]": new Element("p"), "[data-errors-status]": status, "[data-errors-body]": body, "[data-errors-copy]": all}[selector]);
const requests = [], copied = [], timers = [];
const navigator = input.clipboard === "missing" ? {} : {clipboard: {writeText(text) {
  if (input.clipboard === "reject") return Promise.reject(Error("denied"));
  copied.push(text); return Promise.resolve();
}}};
vm.runInNewContext(input.script, {
  document: {querySelector: () => panel, createElement: tag => new Element(tag)}, navigator,
  setTimeout: (callback, ms) => timers.push({callback, ms}),
  fetch: (url, options) => {
    requests.push({url, options});
    return Promise.resolve({ok: true, json: () => Promise.resolve(input.result), text: () => Promise.resolve("synthetic prompt")});
  }
});
const settle = () => new Promise(resolve => setImmediate(resolve));
(async function () {
  assert.equal(requests.length, 0);
  panel.events.toggle(); assert.equal(requests.length, 0);
  panel.open = true; panel.events.toggle(); await settle();
  panel.open = false; panel.events.toggle(); panel.open = true; panel.events.toggle(); await settle();
  assert.equal(requests.length, 1); assert.equal(requests[0].url, "/replays/upload/errors.json");
  assert.equal(body.hidden, false); assert.equal(all.disabled, false);
  assert.deepEqual(descendants(body, "h5").map(node => node.textContent), ["Ascent", "Summit"]);
  assert.deepEqual(descendants(body, "tbody").map(node => node.children.length), [2, 1]);
  assert.equal(descendants(body, "h3")[0].textContent, "Replays");
  assert.equal(descendants(body, "h4")[0].textContent, "Box height conflicts (51)");
  assert.match(body.textContent, /Stored evidence/);
  assert.match(body.textContent, /Nothing in: Map control: not supported in this round, Map control failed/);
  // A check still running or unavailable isn't "nothing": it shows its note, and stays out of that list.
  assert.match(body.textContent, /Map features failed to verify: checking 0 of 3/);
  assert.doesNotMatch(body.textContent, /Nothing in:[^]*Map features failed to verify/);
  assert.match(body.textContent, /z: 3/); assert.match(body.textContent, /candidates: \[\[5,\[4\],0.42\]\]/);
  assert.match(body.textContent, /<img src=x onerror="fail\(\)">/); assert.equal(descendants(body, "img").length, 0);
  assert.equal(descendants(body, "a").length, 3);
  assert.ok(descendants(body, "a").every(link => link.textContent === "open" && link.href === input.result.kinds[0].rows[0].link));
  all.events.click(); await settle();
  assert.equal(requests[1].url, "/replays/upload/errors/prompt.txt");
  if (input.clipboard === "ok") {
    assert.deepEqual(copied, ["synthetic prompt"]); assert.equal(all.textContent, "Copied");
    assert.equal(timers[0].ms, 2000); timers[0].callback(); assert.equal(all.textContent, "Copy prompt");
  } else {
    const area = descendants(panel, "textarea")[0];
    assert.equal(area.value, "synthetic prompt"); assert.ok(area.focused && area.selected && area.readOnly);
    assert.match(status.textContent, /press Ctrl\+C/); assert.equal(all.textContent, "Copy prompt");
  }
  descendants(body, "button")[0].events.click(); await settle();
  assert.equal(requests[2].url, "/replays/upload/errors/prompt.txt?kind=box_conflict");
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run([node, "-e", harness], input=json.dumps({"script": script, "result": shown,
                                                                      "clipboard": clipboard}),
                               text=True, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr


def test_errors_dom_shows_group_headings_and_event_tables(result):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed for the offline DOM check")
    page = routes.upload_form(request({"replay_upload_ok": True}), Mock()).body.decode()
    script = next(script.split("</script>", 1)[0] for script in page.split("<script>")[1:]
                  if 'document.querySelector("[data-errors]")' in script)
    shown = replay_errors.page(result)
    for entry in shown["kinds"]:
        entry.update(group="Replays", shape="replay")
    event = {"key": "test_event", "label": "Test events", "group": "Pages", "shape": "event", "count": 7,
             "note": None, "rows": [{"kind": "test_event", "key": "GET /somewhere", "count": 7,
                                     "first_seen": "2026-11-01T10:00:00+00:00",
                                     "last_seen": "2026-11-02T10:00:00+00:00",
                                     "error": "<b>boom</b>", "context": {}, "link": None, "reproduce": None}]}
    shown["kinds"].insert(0, event)
    harness = r'''
const assert = require("node:assert/strict"), vm = require("node:vm");
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this._text = ""; }
  appendChild(child) { this.children.push(child); child.parent = this; return child; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { throw Error("row data must use textContent"); }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  remove() {}
}
function descendants(parent, tag) {
  return parent.children.flatMap(child => (child.tag === tag ? [child] : []).concat(descendants(child, tag)));
}
const panel = new Element("details"), status = new Element("p"), body = new Element("div"), all = new Element("button");
panel.appendChild(status); panel.appendChild(all); panel.appendChild(body);
panel.querySelector = selector => ({"[data-errors-deploy]": new Element("p"), "[data-errors-status]": status, "[data-errors-body]": body, "[data-errors-copy]": all}[selector]);
vm.runInNewContext(input.script, {
  document: {querySelector: () => panel, createElement: tag => new Element(tag)}, navigator: {},
  setTimeout: () => 0,
  fetch: () => Promise.resolve({ok: true, json: () => Promise.resolve(input.result)})
});
(async function () {
  panel.open = true; panel.events.toggle();
  await new Promise(resolve => setImmediate(resolve));
  const headings = descendants(body, "h3").map(node => node.textContent);
  const pages = headings.indexOf("Pages"), replays = headings.indexOf("Replays");
  assert.ok(pages >= 0 && replays > pages, JSON.stringify(headings));
  assert.equal(headings.filter(h => h === "Pages").length, 1);
  assert.match(body.textContent, /GET \/somewhere/); assert.match(body.textContent, /<b>boom<\/b>/);
  const ths = descendants(body, "th").map(node => node.textContent);
  assert.deepEqual(ths.slice(0, 5), ["Key", "Count", "First seen", "Last seen", "Error"]);
  const cells = descendants(descendants(body, "tbody")[0], "td").map(node => node.textContent);
  assert.ok(cells.includes("GET /somewhere") && cells.includes("7"), JSON.stringify(cells));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run([node, "-e", harness], input=json.dumps({"script": script, "result": shown}),
                               text=True, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr


def test_error_routes_are_read_only():
    from app.main import app
    for route in app.routes:
        if getattr(route, "path", "").startswith("/replays/upload/errors"):
            assert not (set(route.methods or ()) & {"POST", "PUT", "PATCH", "DELETE"}), route.path

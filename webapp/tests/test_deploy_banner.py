"""Persisted deploy notices on the upload page and its Site health payload."""

import asyncio
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.config import settings
from app.db import Base, get_db
from app.models.deploy_drain import DeployDrain
from app.models.replay import ReplayUpload
from app.routers import deploy_admin, replays
from app.services import deploy_drain, replay_errors, replay_upload as uploads


T0 = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
UPLOAD = "/replays/upload"
ERRORS = UPLOAD + "/errors.json"
DRAINING = {"kind": "draining", "text": "Deploying: 3 runs finishing, new work paused"}


def call(app, path, body=None):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET" if body is None else "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json"), (b"authorization", b"Bearer t")],
             "client": ("testclient", 1), "server": ("testserver", 80),
             "session": {"replay_upload_ok": True, "replay_upload_sid": "test-session"}}
    messages = []

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message):
        messages.append(message)

    async def run():
        await asyncio.wait_for(app(scope, receive, send), timeout=3)

    asyncio.run(run())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, payload.decode()


@pytest.fixture
def site(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[DeployDrain.__table__, ReplayUpload.__table__])
    factory = sessionmaker(bind=engine)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0

    class Worker:
        base = "fake-banner-worker"

        def health(self, **kwargs):
            return {"runners": {"parse": "local"}}

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", "test-code")
    monkeypatch.setattr(settings, "replay_worker_url", "http://unused.invalid")
    monkeypatch.setattr(settings, "replay_admin_token", "t")
    monkeypatch.setattr(uploads, "client", lambda: Worker())
    monkeypatch.setattr(uploads, "_batch_seen", {})
    monkeypatch.setattr(deploy_admin, "_now", lambda: T0)
    monkeypatch.setattr(deploy_admin, "_worker_call", lambda *a, **kw: {})
    monkeypatch.setattr(deploy_admin, "_status", lambda *a: {"status": {"waiting_total": 3}})
    monkeypatch.setattr(replays, "datetime", Clock)
    monkeypatch.setattr(replay_errors, "cached", lambda db: {})
    monkeypatch.setattr(replay_errors, "page", lambda result: {"kinds": [], "checked_at": T0.isoformat()})
    # Templates' auth context also uses the isolated store, never the configured DB.
    import app.templates as template_module
    monkeypatch.setattr(template_module, "SessionLocal", factory)

    context = SimpleNamespace(engine=engine, factory=factory)

    def database():
        with context.factory() as db:
            yield db

    app = FastAPI()
    app.include_router(replays.router)
    app.include_router(deploy_admin.router)
    app.dependency_overrides[get_db] = database
    context.app = app
    yield context
    engine.dispose()


def request_drain(site, run_id="test-run"):
    status, _ = call(site.app, "/admin/deploy/drain", {"run_id": run_id, "sha": "abcdef123"})
    assert status == 200


def assert_notice(site, expected):
    status, html = call(site.app, UPLOAD)
    assert status == 200
    if expected is None:
        assert "data-deploy-banner" not in html
    else:
        assert expected["text"] in html
        assert 'role="status" data-deploy-banner' in html
        assert html.index("data-deploy-banner") < html.index('<div class="card">')
    status, payload = call(site.app, ERRORS)
    assert status == 200
    assert json.loads(payload)["deploy"] == expected


def test_requested_drain_before_poll_and_after_process_restart(site):
    request_drain(site)
    with site.factory() as db:
        assert db.query(DeployDrain).one().waiting_total == 3
        assert deploy_drain.banner(db, T0) == DRAINING
    assert_notice(site, DRAINING)
    site.factory = sessionmaker(bind=site.engine)
    assert_notice(site, DRAINING)


def test_deploying_shortens_sha(site):
    request_drain(site)
    with site.factory() as db:
        deploy_drain.mark_deploying(db, "test-run", T0)
    assert_notice(site, {"kind": "deploying", "text": "Deploying abcdef1: new work paused"})


def test_failed_history_ignores_dry_run_and_expired_then_ok_clears_it(site):
    request_drain(site)
    with site.factory() as db:
        deploy_drain.finish(db, "test-run", "failed", T0)
        failed = deploy_drain.banner(db, T0)
    assert failed["kind"] == "failed"
    assert failed["text"].startswith("Last deploy failed (abcdef1, ")
    assert "2026-11-13" in failed["text"] and failed["text"].endswith(")")
    assert_notice(site, failed)
    for i, outcome in enumerate(("dry_run", "expired", "ok"), 1):
        with site.factory() as db:
            deploy_drain.request(db, f"next-{i}", "123456789", T0 + timedelta(seconds=i))
            deploy_drain.finish(db, f"next-{i}", outcome, T0 + timedelta(seconds=i + 1))
        assert_notice(site, None if outcome == "ok" else failed)


def test_active_drain_takes_precedence_over_failure(site):
    request_drain(site)
    with site.factory() as db:
        deploy_drain.finish(db, "test-run", "failed", T0)
    request_drain(site, "new-run")
    assert_notice(site, DRAINING)


def test_no_history_has_no_notice(site):
    assert_notice(site, None)


def test_null_waiting_count_and_expiry(site):
    with site.factory() as db:
        deploy_drain.request(db, "test-run", "abcdef123", T0)
        assert deploy_drain.banner(db, T0) == {
            "kind": "draining", "text": "Deploying: 0 runs finishing, new work paused"}
        assert deploy_drain.banner(db, T0 + deploy_drain.EXPIRY) is None


def test_demo_never_reads_banner(site, monkeypatch):
    def forbidden(*args):
        raise AssertionError("demo read deploy state")

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(deploy_drain, "banner", forbidden, raising=False)
    assert call(site.app, UPLOAD)[0] == 404
    assert call(site.app, ERRORS)[0] == 404
    # Also guard the renderer itself, independent of the route's 404 gate.
    with site.factory() as db:
        response = replays._upload_page(Request({"type": "http", "session": {}}), db=db)
    assert response.context.get("deploy_banner") is None


def test_missing_drain_table_does_not_break_either_page(site, caplog):
    DeployDrain.__table__.drop(site.engine)
    with site.factory() as db:
        assert deploy_drain.banner(db, T0) is None
    assert_notice(site, None)
    assert "deploy" in caplog.text and "OperationalError" in caplog.text


def test_failed_banner_does_not_break_pages_or_log_private_details(site, monkeypatch, caplog):
    def failed_read(db, now):
        raise RuntimeError("synthetic private database detail")

    monkeypatch.setattr(deploy_drain, "active", failed_read)
    assert_notice(site, None)
    assert "synthetic private database detail" not in caplog.text


def test_banner_read_failure_rolls_back(site, monkeypatch):
    with site.factory() as db:
        def failed_read(*args):
            raise RuntimeError("synthetic failure")

        rolled_back = []
        monkeypatch.setattr(deploy_drain, "active", failed_read)
        monkeypatch.setattr(db, "rollback", lambda: rolled_back.append(True))
        assert deploy_drain.banner(db, T0) is None
        assert rolled_back == [True]


def test_banner_text_is_html_escaped(site):
    with site.factory() as db:
        deploy_drain.request(db, "test-run", "<svg>&!", T0, skip_drain=True)
    status, html = call(site.app, UPLOAD)
    assert status == 200
    assert "Deploying &lt;svg&gt;&amp;!: new work paused" in html
    assert "Deploying <svg>" not in html


def test_health_card_puts_notice_first_and_renders_text_safely(site):
    status, html = call(site.app, UPLOAD)
    assert status == 200
    assert html.index("data-errors-deploy") < html.index("data-errors-status")
    script = next(script for script in re.findall(r"<script>(.*?)</script>", html, re.S)
                  if 'document.querySelector("[data-errors]")' in script)
    node = shutil.which("node")
    assert node, "Node is required to verify Site health banner rendering"
    harness = r"""
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
class Element {
  constructor() { this.textContent = ''; this.hidden = true; this.children = []; }
  set innerHTML(value) { throw Error('Banner text must never be parsed as HTML'); }
  appendChild(node) { this.children.push(node); }
  setAttribute() {}
  addEventListener(event, handler) { this[event] = handler; }
}
const nodes = Object.fromEntries(['[data-errors-status]', '[data-errors-body]',
  '[data-errors-copy]', '[data-errors-deploy]'].map(key => [key, new Element()]));
const panel = new Element();
panel.open = true;
panel.querySelector = key => nodes[key];
global.document = {querySelector: () => panel, createElement: () => new Element()};
let payload;
global.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(payload)});
(async () => {
  for (const deploy of [{kind: 'failed', text: '<img src=x onerror=bad()>&'}, null]) {
    payload = {deploy, kinds: [], checked_at: '2026-11-13T12:00:00Z'};
    eval(input.script);
    panel.toggle();
    await new Promise(setImmediate);
    const notice = nodes['[data-errors-deploy]'];
    assert.equal(notice.hidden, deploy === null);
    assert.equal(notice.textContent, deploy ? deploy.text : '');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps({"script": script}),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr

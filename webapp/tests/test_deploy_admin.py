"""Deploy API contracts using in-process ASGI, SQLite and a fenced fake worker."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.models.deploy_drain import DeployDrain
from app.models.site_events import SiteEvent, SiteEventMark, SiteEventResolution
from app.routers import deploy_admin
from app.services import deploy_drain, replay_control_remote, replay_heights_remote
from app.services import replay_errors, site_health, upload_activity, util_build_remote, util_rebuild


FIXTURES = Path(__file__).parent / "fixtures" / "deploy"
CONTRACT = json.loads((FIXTURES / "routes.json").read_text())
T0 = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
WORKER_FACTORY = deploy_admin._worker


def call(app, method, path, body=None, token="t"):
    headers = [(b"content-type", b"application/json")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "headers": headers,
             "client": ("testclient", 1), "server": ("testserver", 80)}
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
    return status, json.loads(payload)


class Worker:
    def __init__(self):
        self.health_body = json.loads((FIXTURES / "worker_health_quiet.json").read_text())
        self.ping_result = {"ok": True, "answer": {"status": "ok", "echo": "sample-ping", "commit": "abc"},
                            "seconds": 1.0}
        self.calls = []
        self.holding = None
        self.fail = set()
        self.on_release = None
        self.ping_gate = threading.Event()
        self.ping_gate.set()
        self.ping_entered = threading.Event()

    def health(self):
        if "health" in self.fail:
            raise OSError("synthetic worker failure")
        return self.health_body

    def drain_hold(self, drain_id, until):
        self.calls.append(("hold", drain_id, until))
        if "hold" in self.fail:
            raise OSError("synthetic worker failure")
        self.holding = drain_id
        return {"held": True}

    def drain_cancel(self, drain_id):
        self.calls.append(("cancel", drain_id))
        if "cancel" in self.fail:
            raise OSError("synthetic worker failure")
        return CONTRACT["force"]["response"] if self.holding == drain_id else {"ignored": True}

    def drain_release(self, drain_id):
        self.calls.append(("release", drain_id))
        if self.on_release:
            callback, self.on_release = self.on_release, None
            callback()
        if "release" in self.fail:
            raise OSError("synthetic worker failure")
        if self.holding != drain_id:
            return {"ignored": True}
        self.holding = None
        return {"released": True}

    def ping(self, timeout=330):
        assert timeout == 330
        self.calls.append(("ping",))
        self.ping_entered.set()
        assert self.ping_gate.wait(5), "test did not release ping"
        if "ping" in self.fail:
            raise OSError("synthetic ping failure")
        return self.ping_result


@pytest.fixture
def site(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[DeployDrain.__table__, SiteEvent.__table__,
                                           SiteEventResolution.__table__, SiteEventMark.__table__])
    factory = sessionmaker(bind=engine)
    clock = SimpleNamespace(now=T0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now

    worker = Worker()
    monkeypatch.setattr(settings, "replay_admin_token", "t")
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(deploy_admin, "_session_factory", factory)
    monkeypatch.setattr(deploy_admin, "_worker", lambda: worker)
    monkeypatch.setattr(deploy_admin, "_now", lambda: clock.now)
    monkeypatch.setattr(site_health, "_now", lambda: clock.now)
    monkeypatch.setattr(deploy_drain, "datetime", Clock)
    monkeypatch.setattr(deploy_admin, "_tasks_result", None)
    monkeypatch.setattr(deploy_admin, "_tasks_thread", None)
    monkeypatch.setattr(replay_control_remote, "LIVE", SimpleNamespace(at=T0.timestamp() + 30, in_flight=()))
    monkeypatch.setattr(replay_heights_remote, "open_builds", lambda: 0)
    monkeypatch.setattr(util_build_remote.STATE, "phase", "idle")
    monkeypatch.setattr(util_rebuild, "running", lambda: False)
    monkeypatch.setattr(upload_activity, "receiving", lambda: 0)
    monkeypatch.setattr(upload_activity, "last_end_age_s", lambda: None)

    def database():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(deploy_admin.router)
    app.dependency_overrides[get_db] = database

    def request(name, body=None, token="t"):
        route = CONTRACT[name]
        return call(app, route["method"], route["path"], route["request"] if body is None else body, token)

    context = SimpleNamespace(app=app, factory=factory, worker=worker, clock=clock, request=request,
                              database=database)
    yield context
    worker.ping_gate.set()
    if deploy_admin._tasks_thread is not None:
        deploy_admin._tasks_thread.join(5)
        assert not deploy_admin._tasks_thread.is_alive()
    engine.dispose()


@pytest.mark.parametrize("route", CONTRACT)
@pytest.mark.parametrize("token", [None, "wrong"])
def test_every_route_requires_admin(site, route, token):
    assert site.request(route, token=token)[0] == 404


@pytest.mark.parametrize("route", CONTRACT)
def test_every_route_hidden_in_demo(site, monkeypatch, route):
    monkeypatch.setattr(settings, "demo_mode", True)

    def never_database():
        raise AssertionError("demo admin route opened a database session")

    site.app.dependency_overrides[get_db] = never_database
    assert site.request(route)[0] == 404
    assert site.worker.calls == []


def test_contract_bodies_and_read_only_status(site):
    assert site.request("drain") == (200, CONTRACT["drain"]["response"])
    assert site.worker.calls == [("hold", 1, (T0 + timedelta(hours=2)).timestamp())]
    with site.factory() as db:
        assert db.get(DeployDrain, 1).waiting_total == 0
    site.clock.now += timedelta(minutes=1)
    assert site.request("poll") == (200, CONTRACT["poll"]["response"])
    assert site.request("status") == (200, CONTRACT["status"]["response"])
    site.clock.now += timedelta(minutes=1)
    assert site.request("status")[1]["drain"]["expires_at"] == CONTRACT["status"]["response"]["drain"]["expires_at"]
    assert site.request("deploying") == (200, CONTRACT["deploying"]["response"])
    site.clock.now = T0 + timedelta(minutes=10)
    assert site.request("resume") == (200, CONTRACT["resume"]["response"])
    assert site.worker.calls[-1] == ("release", 1)


def test_takeover_and_nonowner_actions(site):
    site.request("drain")
    body = {**CONTRACT["drain"]["request"], "run_id": "2"}
    code, result = site.request("drain", body)
    assert code == 200 and result["result"] == "took_over" and result["superseded"] == "1"
    for name in ("poll", "deploying", "force", "resume"):
        assert site.request(name)[0] == 409
    assert site.worker.calls == [("hold", 1, (T0 + timedelta(hours=2)).timestamp())] * 2
    site.clock.now += timedelta(minutes=5)
    assert site.request("poll", {"run_id": "2"})[1]["drain"]["expires_at"] == (site.clock.now + timedelta(hours=2)).isoformat()


def test_busy_and_idempotent_request(site):
    site.request("drain")
    site.request("deploying")
    assert site.request("drain", {"run_id": "2", "sha": "c" * 40})[1]["result"] == "busy"
    assert site.request("drain")[1]["result"] == "started"
    assert site.request("status")[1]["drain"]["phase"] == "deploying"


def test_busy_waiting_is_stored_and_live_cycle_is_required(site, monkeypatch):
    site.worker.health_body = json.loads((FIXTURES / "worker_health_busy.json").read_text())
    site.request("drain")
    status = site.request("status")[1]["status"]
    assert status["quiet"] is False and status["waiting"]["worker_control_queued"] == 3
    with site.factory() as db:
        assert db.get(DeployDrain, 1).waiting_total == status["waiting_total"]
    site.worker.health_body = json.loads((FIXTURES / "worker_health_quiet.json").read_text())
    assert site.request("status")[1]["status"]["quiet"] is True
    monkeypatch.setattr(replay_control_remote, "LIVE", None)
    assert site.request("status")[1]["status"]["waiting"] == {"site_cycle": 1}


def test_site_facts_are_all_barriers(site, monkeypatch):
    site.request("drain")
    monkeypatch.setattr(replay_control_remote, "LIVE", SimpleNamespace(at=T0.timestamp(), in_flight=(1, 2)))
    monkeypatch.setattr(replay_heights_remote, "open_builds", lambda: 3)
    monkeypatch.setattr(util_build_remote.STATE, "phase", "running")
    monkeypatch.setattr(util_rebuild, "running", lambda: True)
    monkeypatch.setattr(upload_activity, "receiving", lambda: 2)
    monkeypatch.setattr(upload_activity, "last_end_age_s", lambda: 30)
    status = site.request("status")[1]["status"]
    assert status["waiting"] == {"site_cycle": 1, "site_control_in_flight": 2, "site_heights_open": 3,
                                 "site_util": 1, "site_util_child": 1, "site_uploads_receiving": 2,
                                 "site_uploads_recent": 1}
    assert status["uploads_quiet"] is False


def test_tasks_ping_is_background_single_flight_and_publishes_result(site):
    site.request("drain")
    site.worker.ping_gate.clear()
    try:
        assert site.request("poll", {"run_id": "1", "tasks": True})[1]["commits"]["tasks"] is None
        assert site.worker.ping_entered.wait(1)
        assert site.request("poll", {"run_id": "1", "tasks": True})[1]["commits"]["tasks"] is None
        assert site.worker.calls.count(("ping",)) == 1
    finally:
        site.worker.ping_gate.set()
    deploy_admin._tasks_thread.join(5)
    expected = {"commit": "abc", "started_at": T0.timestamp()}
    assert site.request("poll")[1]["commits"]["tasks"] == expected
    assert site.request("status")[1]["commits"]["tasks"] == expected
    site.worker.fail.add("ping")
    site.clock.now += timedelta(seconds=10)
    assert site.request("poll", {"run_id": "1", "tasks": True})[1]["commits"]["tasks"] == expected
    deploy_admin._tasks_thread.join(5)
    assert site.request("poll")[1]["commits"]["tasks"] == {"commit": None, "started_at": site.clock.now.timestamp()}


@pytest.mark.parametrize("response", [
    {"ok": False, "answer": {"status": "ok", "echo": "sample-ping", "commit": "abc"}, "seconds": 1.0},
    {"ok": True, "answer": None},
    {"ok": True, "answer": "invalid"},
    {"ok": True, "answer": []},
    {"ok": True, "answer": {"status": "ok"}},
])
def test_tasks_ping_without_successful_answer_commit_publishes_null(site, response):
    site.request("drain")
    site.worker.ping_result = response
    site.request("poll", {"run_id": "1", "tasks": True})
    deploy_admin._tasks_thread.join(5)
    assert site.request("poll")[1]["commits"]["tasks"] == {"commit": None, "started_at": T0.timestamp()}


def test_superseded_tasks_poll_does_not_start_ping(site):
    site.request("drain", {"run_id": "2", "sha": "c" * 40})
    assert site.request("poll", {"run_id": "1", "tasks": True})[0] == 409
    assert ("ping",) not in site.worker.calls


def events(site):
    with site.factory() as db:
        return list(db.scalars(select(SiteEvent).where(SiteEvent.kind == "forced_deploy")))


def test_force_records_real_health_event_and_later_success_resolves(site):
    site.request("drain")
    assert site.request("force") == (200, CONTRACT["force"]["response"])
    assert site.worker.calls[-1] == ("cancel", 1)
    [event] = events(site)
    assert event.key == "1" and event.error == "deploy forced after 90 min"
    # Site health's existing context scrubber masks fields named key.
    assert event.context == {"cancelled": [{"kind": "control", "key": "***"}], "waiting": {}}
    with site.factory() as db:
        assert db.get(DeployDrain, 1).forced is True
    assert site.request("resume")[0] == 200
    assert len(events(site)) == 1
    site.request("drain", {"run_id": "2", "sha": "c" * 40})
    assert site.request("resume", {"run_id": "2", "outcome": "failed"})[0] == 200
    assert len(events(site)) == 1
    site.request("drain", {"run_id": "3", "sha": "d" * 40})
    assert site.request("resume", {"run_id": "3", "outcome": "ok"})[0] == 200
    assert events(site) == []
    assert [c for c in site.worker.calls if c[0] == "release"] == [("release", 1), ("release", 2), ("release", 3)]


def test_late_release_keeps_original_id_and_newer_hold(site):
    site.request("drain")
    site.worker.on_release = lambda: site.request("drain", {"run_id": "2", "sha": "c" * 40})
    assert site.request("resume")[0] == 200
    assert ("release", 1) in site.worker.calls and site.worker.holding == 2


def test_earlier_success_does_not_resolve_a_newer_forced_deploy(site):
    site.request("drain")

    def force_next_drain():
        site.clock.now += timedelta(seconds=1)
        site.request("drain", {"run_id": "2", "sha": "c" * 40})
        site.request("force", {"run_id": "2"})

    site.worker.on_release = force_next_drain
    assert site.request("resume")[0] == 200
    assert [event.key for event in events(site)] == ["2"]


def test_no_active_drain_and_skip_drain_dry_run(site):
    answer = site.request("status")[1]
    assert answer["drain"] is None and answer["status"]["quiet"] is False
    assert site.request("poll")[0] == 409
    code, answer = site.request("drain", {**CONTRACT["drain"]["request"], "skip_drain": True})
    assert code == 200 and answer["drain"]["phase"] == "deploying" and answer["drain"]["skip_drain"] is True
    code, answer = site.request("resume", {"run_id": "1", "outcome": "dry_run"})
    assert code == 200 and answer["drain"]["outcome"] == "dry_run" and answer["drain"]["commit"] is None


def test_expired_owner_is_refused_and_status_closes_expiry(site):
    site.request("drain")
    site.clock.now += timedelta(hours=2)
    assert site.request("status")[1]["drain"] is None
    for route in ("poll", "deploying", "force", "resume"):
        assert site.request(route)[0] == 409
    with site.factory() as db:
        assert db.get(DeployDrain, 1).outcome == "expired"


def test_unreachable_worker_is_best_effort_everywhere(site):
    site.worker.fail.update({"health", "hold", "cancel", "release"})
    assert site.request("drain")[0] == 200
    status = site.request("status")[1]
    assert status["commits"]["worker"] is None
    assert status["status"]["waiting"] == {"worker_unreachable": 1}
    assert site.request("poll")[0] == 200
    assert site.request("force") == (200, {"cancelled": []})
    [event] = events(site)
    assert event.context["cancelled"] == [] and event.context["worker_unreachable"] is True
    assert event.context["waiting"] == {"worker_unreachable": 1}
    assert site.request("resume")[0] == 200


def test_unconfigured_worker_and_short_health_timeout(site, monkeypatch):
    monkeypatch.setattr(settings, "replay_worker_url", "")
    with pytest.raises(replay_control_remote.Unreachable):
        WORKER_FACTORY()
    monkeypatch.setattr(settings, "replay_worker_url", "http://worker.invalid")
    worker = WORKER_FACTORY()
    assert isinstance(worker, replay_control_remote.ControlClient)
    assert worker.timeout <= 10


@pytest.mark.parametrize("demo", [False, True])
@pytest.mark.parametrize("commit", [None, "sample-site-commit"])
def test_health_commit_and_main_router_registration(site, monkeypatch, demo, commit):
    from app.main import app

    monkeypatch.setattr(settings, "demo_mode", demo)
    if commit is None:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    else:
        monkeypatch.setenv("RENDER_GIT_COMMIT", commit)
    monkeypatch.setitem(app.dependency_overrides, get_db, site.database)
    assert call(app, "GET", "/health") == (200, {"status": "ok", "commit": commit})
    assert call(app, "GET", "/admin/deploy/status")[0] == (404 if demo else 200)


def test_health_kind_and_page_classification():
    assert ("forced_deploy", "Deploy forced after 90 min") in replay_errors.KINDS
    assert replay_errors.GROUPS["forced_deploy"] == "Background work"
    assert "forced_deploy" in replay_errors.EVENT_KINDS
    assert "forced_deploy" in site_health.RECORDED_KINDS
    assert site_health.PAGE_ROUTES["/admin/deploy/status"] == "not_page"

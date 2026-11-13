"""Drain admission boundaries and clocks, using SQLite and protocol fakes only."""
import json
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "critutil"))
from test_control_store import db, factory, linked  # noqa: F401,E402
from test_replay_store import condensed  # noqa: F401,E402
from test_control_remote import FakeWorker, rows
import test_replay_reparse_auto as reparse_tests
from test_util_build_remote import env as util_env, put_rows
from app.models.deploy_drain import DeployDrain
from app.models.replay import ReplayUpload
from app.services import deploy_drain as drain
from app.services import replay_control_remote as control
from app.services import replay_heights_remote as heights
from app.services import replay_reparse_auto as reparse
from app.services import replay_upload as uploads
from app.services import util_build_remote as util
from app.services import util_rebuild

AT = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
NOW = AT.timestamp()


def start(db, *, start=AT, end=None):
    row = DeployDrain(id=7, run_id="synthetic-run", sha="a" * 40, phase="draining",
                      started_at=start, requested_at=start, expires_at=AT + timedelta(hours=2),
                      ended_at=end, outcome="ok" if end else None)
    db.add(row)
    db.commit()
    return row


@pytest.fixture(autouse=True)
def local_boundaries(monkeypatch, factory):
    def paused():
        with factory() as session:
            return drain.paused(session, AT)
    monkeypatch.setattr(drain, "paused_now", paused)
    monkeypatch.setattr(control.layout_walls, "step", lambda *a, **k: {"skipped": True})
    monkeypatch.setattr(control, "IN_FLIGHT", 8)
    monkeypatch.setattr(control, "MAX_MATCHES", 1000)
    monkeypatch.setattr(control, "in_flight_cap", lambda state: control.IN_FLIGHT)
    monkeypatch.setattr(heights, "enabled", lambda: False)


@pytest.mark.parametrize("paused", [True, False])
def test_submit_pause(factory, db, linked, monkeypatch, paused):
    if paused:
        start(db)
        monkeypatch.setattr(control.replay_control, "plan", lambda *a, **k: pytest.fail("planned while paused"))
    else:
        monkeypatch.setattr(control, "IN_FLIGHT", 1)
    worker = FakeWorker()
    result = control.cycle(factory, worker, control.State(), NOW)
    assert result["sent"] == (0 if paused else 1)
    assert len(worker.tasks) == (0 if paused else 1)


def test_send_stops_mid_batch(factory, db, linked, monkeypatch):
    monkeypatch.setattr(control, "IN_FLIGHT", 3)
    worker = FakeWorker()
    submit = worker.submit
    def send(task):
        answer = submit(task)
        start(db)
        return answer
    worker.submit = send
    result = control.cycle(factory, worker, control.State(), NOW)
    assert result["sent"] == 1 and len(worker.tasks) == 1


def test_collect_during_pause(factory, db, linked, monkeypatch):
    monkeypatch.setattr(control, "IN_FLIGHT", 1)
    worker, state = FakeWorker(), control.State()
    assert control.cycle(factory, worker, state, NOW)["sent"] == 1
    start(db)
    result = control.cycle(factory, worker, state, NOW + 1)
    assert result["stored"] == 1 and result["sent"] == 0
    assert len(rows(db, linked)) == 1 and not state.in_flight


def test_control_drain_failure_is_free(factory, db, linked, monkeypatch):
    monkeypatch.setattr(control, "IN_FLIGHT", 1)
    worker = FakeWorker(answer=lambda task: {"status": "failed", "error_kind": "drain"})
    state = control.State()
    control.cycle(factory, worker, state, NOW)
    start(db)
    before = dict(state.tries)
    control.cycle(factory, worker, state, NOW + 1)
    assert not state.in_flight and state.tries == before


class HoldWorker(FakeWorker):
    def __init__(self):
        super().__init__()
        self.body = json.loads((HERE.parent / "fixtures/deploy/worker_health_quiet.json").read_text())
        self.calls = []

    def health(self):
        if self.health_error:
            raise self.health_error
        return self.body

    def drain_hold(self, drain_id, until):
        self.calls.append(("hold", drain_id, until))

    def drain_release(self, drain_id):
        self.calls.append(("release", drain_id))


@pytest.mark.parametrize("delta,worker_id,expected", [(0, None, True), (59, 7, False), (60, 7, False), (61, 7, True),
                                                       (None, 7, True)])
def test_sync_hold(factory, db, delta, worker_id, expected):
    row = start(db)
    worker = HoldWorker()
    until = NOW + 7200
    worker.body["drain"].update(drain_id=worker_id, hold_until=None if delta is None else until + delta)
    control._sync_hold(worker, factory, NOW)
    assert worker.calls == ([("hold", row.id, until)] if expected else [])


@pytest.mark.parametrize("hold,blocked", [(True, False), (False, True)])
def test_sync_release(factory, hold, blocked):
    worker = HoldWorker()
    worker.body["drain"].update(drain_id=7, hold=hold, starts_blocked=blocked)
    control._sync_hold(worker, factory, NOW)
    assert worker.calls == [("release", 7)]


def test_hold_sync_failure_resolves_next_cycle(factory, monkeypatch):
    worker, state, events = HoldWorker(), control.State(), []
    worker.body["drain"].update(hold=False, starts_blocked=False)
    monkeypatch.setattr(control.site_health, "loop_failed", lambda name, exc: events.append(("failed", name)))
    monkeypatch.setattr(control.site_health, "loop_ok", lambda name: events.append(("ok", name)))
    worker.health_error = control.Unreachable("synthetic")
    control.cycle(factory, worker, state, NOW)
    worker.health_error = None
    control.cycle(factory, worker, state, NOW + 1)
    assert [(kind, name) for kind, name in events if name == "drain_hold_sync"] == [
        ("failed", "drain_hold_sync"), ("ok", "drain_hold_sync")]


def test_hold_sync_failure_keeps_worker_capabilities(factory, db, linked, monkeypatch):
    worker = FakeWorker(gaps=control.GAPS_PROTOCOL)
    worker.runners = {"control": "workflow"}
    monkeypatch.setattr(control, "in_flight_cap", lambda state: 3 if state.remote_control else 1)
    monkeypatch.setattr(control, "_sync_hold", lambda *a: (_ for _ in ()).throw(control.Unreachable("synthetic")))
    state = control.State()
    result = control.cycle(factory, worker, state, NOW)
    assert result["sent"] == 3 and worker.health_reads == 1
    assert state.capable and all("gaps" in task for task in worker.tasks.values())


@pytest.mark.parametrize("method,args,path,body,timeout", [
    ("drain_hold", (7, NOW), "/drain/hold", {"drain_id": 7, "until": NOW}, 30),
    ("drain_release", (7,), "/drain/release", {"drain_id": 7}, 30),
    ("drain_cancel", (7,), "/drain/cancel", {"drain_id": 7}, 30),
    ("ping", (), "/runs/ping", {}, 330),
    ("ping", (45,), "/runs/ping", {}, 45),
])
def test_control_drain_transport(monkeypatch, method, args, path, body, timeout):
    calls = []
    def urlopen(request, timeout):
        calls.append((request.full_url, request.method, json.loads(request.data), timeout))
        return io.BytesIO(b'{}')
    monkeypatch.setattr(control.urllib.request, "urlopen", urlopen)
    getattr(control.ControlClient("https://synthetic.invalid"), method)(*args)
    assert calls == [("https://synthetic.invalid" + path, "POST", body, timeout)]


def test_open_height_build_snapshot(monkeypatch):
    state = control.State()
    state.heights.builds = {"a": SimpleNamespace(job_id="job"), "b": SimpleNamespace(job_id=None)}
    monkeypatch.setattr(control, "_OPEN_HEIGHT_BUILDS", 0)
    assert heights.open_builds() == 0
    live = control._snapshot(state, NOW)
    assert live.at == NOW and heights.open_builds() == 1
    state.heights.builds.clear()
    assert heights.open_builds() == 1
    control._snapshot(state, NOW + 1)
    assert heights.open_builds() == 0


@pytest.fixture
def reparse_on(monkeypatch):
    monkeypatch.setattr(reparse, "off_reason", lambda: None)
    monkeypatch.setattr(uploads, "_site_recipe", reparse_tests.NEW)
    monkeypatch.setattr(reparse, "_builds", reparse_tests.BUILDS)


@pytest.mark.parametrize("mid_pass", [False, True])
def test_reparse_no_reservation(factory, db, reparse_on, mid_pass):
    replay, worker = reparse_tests.stale_one(db)
    if mid_pass:
        health = worker.health
        def health_then_drain():
            start(db)
            return health()
        worker.health = health_then_drain
    else:
        start(db)
    counts = {}
    reparse.step(factory, worker, reparse.Memo(), 0, NOW, counts)
    assert counts["reparse_reserved"] == 0 and worker.accepted == 0


def test_reparse_accepted_settles(factory, db, reparse_on):
    replay, worker = reparse_tests.stale_one(db)
    reparse.step(factory, worker, reparse.Memo(), 0, NOW, {})
    upload = db.query(ReplayUpload).one()
    worker.jobs[upload.worker_job_id] = {"status": "failed", "error": "synthetic parse failure"}
    start(db)
    counts = {}
    reparse.step(factory, worker, reparse.Memo(), 0, NOW + 1, counts)
    db.expire_all()
    assert counts["reparse_finished"] == 1 and upload.status == "failed" and worker.accepted == 1


def test_reparse_reserved_is_not_resent(factory, db, reparse_on):
    replay, worker = reparse_tests.stale_one(db)
    reparse_tests.add_attempt(db, replay, phase="reserved", status="queued")
    start(db)
    reparse.step(factory, worker, reparse.Memo(), 0, NOW, {})
    assert worker.accepted == 0


def test_reparse_recovery_rechecks_after_quiet(factory, db, reparse_on, monkeypatch):
    replay, worker = reparse_tests.stale_one(db)
    reparse_tests.add_attempt(db, replay, phase="reserved", status="queued")
    def quiet(*args):
        start(db)
        return None
    monkeypatch.setattr(reparse, "busy_reason", quiet)
    reparse.step(factory, worker, reparse.Memo(), 0, NOW, {})
    assert worker.accepted == 0


def test_reparse_reservation_rechecks_after_classifying(factory, db, reparse_on, monkeypatch):
    replay, worker = reparse_tests.stale_one(db)
    original = reparse.classify
    def classify(*args):
        answer = original(*args)
        start(db)
        return answer
    monkeypatch.setattr(reparse, "classify", classify)
    counts = {}
    reparse.step(factory, worker, reparse.Memo(), 0, NOW, counts)
    assert counts["reparse_reserved"] == 0 and worker.accepted == 0


@pytest.fixture
def height_plan(monkeypatch):
    manifest = {"key": "synthetic-manifest"}
    due = heights.Due("Haven", manifest, [], "synthetic")
    monkeypatch.setattr(heights.hi, "key", lambda m: m["key"])
    monkeypatch.setattr(heights, "plan_maps", lambda session: heights.Plan(due=[due]))
    return due


@pytest.mark.parametrize("job_id", [None, "synthetic-job"])
def test_heights_pause(factory, db, height_plan, job_id):
    start(db)
    build = heights.Build("Haven", height_plan.manifest["key"], height_plan.manifest, [], NOW, job_id=job_id)
    state = heights.HeightState(builds={build.key: build})
    calls = []
    worker = SimpleNamespace(build=lambda job: calls.append(job) or {"status": "running"},
                             health=lambda: pytest.fail("health while paused"),
                             open_build=lambda *a, **k: pytest.fail("opened while paused"))
    heights.step(factory, db, worker, state, NOW, {})
    assert calls == ([] if job_id is None else [job_id]) and not state.tries


def test_heights_idle_pause(factory, db, height_plan):
    start(db)
    state = heights.HeightState()
    heights.step(factory, db, SimpleNamespace(), state, NOW, {})
    assert not state.builds and not state.tries


@pytest.mark.parametrize("status", ["gone", "drain"])
def test_heights_failure_is_free_and_waits(factory, db, height_plan, status):
    start(db)
    build = heights.Build("Haven", height_plan.manifest["key"], height_plan.manifest, [], NOW, job_id="job")
    state = heights.HeightState(builds={build.key: build})
    def answer(job):
        if status == "gone":
            raise control.WorkerGone("synthetic")
        return {"status": "failed", "error_kind": "drain"}
    worker = SimpleNamespace(build=answer, open_build=lambda *a, **k: pytest.fail("reopened"))
    counts = {}
    heights.step(factory, db, worker, state, NOW, counts)
    heights.step(factory, db, worker, state, NOW + 1, counts)
    assert build.job_id is None and not state.tries and counts["heights_failed"] == 0


def test_heights_health_starts_drain(factory, db, height_plan):
    def health():
        start(db)
        return {"idle": True}
    worker = SimpleNamespace(health=health, open_build=lambda *a, **k: pytest.fail("opened after drain"))
    build = heights.Build("Haven", "synthetic-manifest", height_plan.manifest, [], NOW)
    assert heights._advance(factory, db, worker, build, NOW, {}) is False
    assert build.job_id is None


@pytest.mark.parametrize("phase", ["idle", "pushing", "open"])
def test_util_paused_phases(factory, db, monkeypatch, phase):
    start(db)
    monkeypatch.setattr(util, "enabled", lambda: True)
    state = util.UtilState(phase=phase, key="synthetic-key", tries={"synthetic-key": 1})
    followed = []
    monkeypatch.setattr(util, "_follow", lambda *a: followed.append(True))
    worker = SimpleNamespace(health=lambda: pytest.fail("health while paused"))
    util.step(factory, worker, state, NOW)
    if phase == "open":
        assert followed == [True]
    else:
        expected = 0 if phase == "pushing" else 1
        assert state.phase == "idle" and state.tries[state.key] == expected
        util.step(factory, worker, state, NOW + 1)
        assert state.tries[state.key] == expected


def test_util_drain_failure_is_free():
    state = util.UtilState(phase="open", key="key", build_id="job", tries={"key": 2}, opened_at=NOW)
    worker = SimpleNamespace(util_build=lambda job: {"id": "job", "key": "key", "status": "failed",
                                                      "error_kind": "drain"})
    util._follow(None, worker, state, NOW)
    assert state.phase == "idle" and state.tries["key"] == 1 and state.backoff["key"] == NOW + 60
    assert not state.deploy_mismatch


@pytest.mark.parametrize("boundary", ["prepare", "have", "loading", "batch", "open"])
def test_util_push_boundaries(util_env, monkeypatch, boundary):
    env = util_env
    put_rows(env, count=3)
    paused = [False]
    monkeypatch.setattr(drain, "paused_now", lambda: paused[0])
    if boundary == "prepare":
        original = util.replay_util.friend_players
        def load(*args):
            paused[0] = True
            return original(*args)
        monkeypatch.setattr(util.replay_util, "friend_players", load)
    elif boundary == "have":
        original = util._prepare
        def prepare(*args):
            answer = original(*args)
            paused[0] = True
            return answer
        monkeypatch.setattr(util, "_prepare", prepare)
    elif boundary == "loading":
        original = util._round_entries
        def load(*args):
            answer = original(*args)
            paused[0] = True
            return answer
        monkeypatch.setattr(util, "_round_entries", load)
    else:
        monkeypatch.setattr(util, "BATCH_BYTES", 160)
        original = env.client.util_rounds
        def send(*args, **kwargs):
            answer = original(*args, **kwargs)
            if boundary == "batch" or len(env.client.sent("rounds")) == 3:
                paused[0] = True
            return answer
        env.client.util_rounds = send
    util.push(env.db, env.client, env.state, env.health, NOW)
    assert env.state.phase == "idle" and env.state.tries.get(env.state.key, 0) == 0
    assert not env.client.sent("open")
    assert len(env.client.sent("rounds")) == (1 if boundary == "batch" else 3 if boundary == "open" else 0)
    before = dict(env.state.tries)
    util.push(env.db, env.client, env.state, env.health, NOW + 1)
    assert env.state.tries == before


def test_util_rebuild_paused(db, monkeypatch):
    start(db)
    monkeypatch.setattr(util_rebuild, "_spawn", lambda *a: pytest.fail("spawned"))
    assert util_rebuild.request() is False


@pytest.mark.parametrize("status,age,minutes,failed", [("queued", 70, 30, False), ("queued", 70, 0, True),
    ("parsing", 25, 10, False), ("parsing", 25, 0, True), (None, 25, 10, False), (None, 25, 0, True)])
def test_upload_stuck_clock(factory, db, status, age, minutes, failed):
    created = AT - timedelta(minutes=age)
    if minutes:
        start(db, start=created + timedelta(minutes=2), end=created + timedelta(minutes=2 + minutes))
    upload = ReplayUpload(id="00000000-0000-4000-8000-000000000001", status=status or "queued", source_sha256="b" * 64,
                          created_at=created, worker_job_id="synthetic-job" if status else None)
    db.add(upload)
    db.commit()
    worker = SimpleNamespace(job=lambda job: {"status": status})
    if status is None:
        uploads.collect_unfinished(factory, worker, AT)
    else:
        uploads.refresh_job(db, upload, worker, AT)
    db.expire_all()
    assert (upload.status == "failed") is failed

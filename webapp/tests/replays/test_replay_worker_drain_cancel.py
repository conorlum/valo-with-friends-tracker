"""A forced deploy's cancel-all (R9, worker half) and the start barrier: `POST /drain/cancel`, `Runs.cancel_all`,
the control scheduler's pause, warm-state rows 25-27 (docs/superpowers/specs/2026-11-06-warm-build-states.md), a
Workflow parse handed back to its queue, and the tasks service's commit ping. The fake Render is the one the other
Workflow tests use; a slow or failing Render cancel is the real RenderApi's `cancel` wrapped here. Localhost only."""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import runs, server  # noqa: E402
from test_replay_worker import get  # noqa: E402
from test_replay_worker_control_workflow import (  # noqa: E402,F401  (stub and warm_stub are fixtures)
    round_task, stub, wait_done, warm_and_confirm, warm_starts, warm_stub)
from test_replay_worker_drain import send  # noqa: E402
from test_replay_worker_parse_workflow import Rig as ParseRig  # noqa: E402
from test_replay_worker_parse_workflow import record_of, wait_for, wait_jobs  # noqa: E402


def until(check, timeout=20.0, what="the condition"):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} never held")


class Rig:
    """A ControlRunner in workflow mode with its server and a fake Render. A run of a kind in `hang` waits (until
    `proceed`, set at teardown) and answers nothing; every other run does its task."""

    def __init__(self, tmp_path, warm=False):
        self.render = FakeRender()
        self.hang: set[str] = set()
        self.proceed = threading.Event()
        self.settings = server.Settings(
            temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache", control_runner="workflow",
            control_runs=40, workflow_slug="replay-tasks", render_api_key="k", render_api_base=self.render.url,
            warm_builds=warm)
        self.table = server.make_runs(self.settings)
        self.control = server.ControlRunner(self.settings, runs_table=self.table)
        self.worker = server.Worker(self.settings, on_parse=None)
        self.httpd = server.make_server(self.worker, control=self.control, runs_table=self.table)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.render.runner = self._run

    def _run(self, kind, args):
        if kind in self.hang:
            self.proceed.wait(120)
            return
        tasks.TASKS[kind](args, tasks.WorkerLink(self.base, args["job_id"], args["token"]))

    def hold(self, drain_id=7, seconds=120):
        status, body = send(self.base, "/drain/hold", {"drain_id": drain_id, "until": time.time() + seconds})
        assert status == 200 and body["hold"] is True

    def cancel(self, drain_id=7):
        return send(self.base, "/drain/cancel", {"drain_id": drain_id})

    def release(self, drain_id=7):
        return send(self.base, "/drain/release", {"drain_id": drain_id})

    def health(self):
        status, body = get(self.base, "/health")
        assert status == 200
        return body["drain"]

    def open_kinds(self):
        return sorted(r.kind for r in list(self.table.open_runs.values()))

    def close(self):
        self.proceed.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.render.close()


@pytest.fixture
def rig(tmp_path, stub):  # noqa: F811
    made = Rig(tmp_path)
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def warm_rig(tmp_path, stub, warm_stub):  # noqa: F811
    made = Rig(tmp_path, warm=True)
    try:
        yield made
    finally:
        made.close()


# ------------------------------------------------------------------ the worker's answer and Render

def test_cancel_closes_two_rounds_and_a_warm_build_at_render_and_lists_three(warm_rig):
    rig = warm_rig
    warm_and_confirm(rig.control)                                    # Ascent is warm and confirmed
    before = len(rig.render.started)
    rig.hang = {"control", "warm"}
    for i in range(2):
        rig.control.submit(round_task(f"a{i}"))
    rig.control.submit(round_task("c", map_name="Bind"))              # cold: its warm build is asked first
    until(lambda: rig.open_kinds() == ["control", "control", "warm"], what="three open runs")
    ids = {s["id"] for s in rig.render.started[before:]}
    rig.hold()
    status, body = rig.cancel()
    assert status == 200
    assert sorted(c["kind"] for c in body["cancelled"]) == ["control", "control", "warm"]
    assert {c["key"] for c in body["cancelled"]} == {"a0", "a1", "Bind"}
    until(lambda: set(rig.render.cancelled) == ids, what="the three Render ids cancelled")


def test_a_warm_build_cancelled_by_a_drain_counts_nothing(warm_rig):
    """State row 25."""
    rig = warm_rig
    rig.hang = {"warm"}
    job = rig.control.submit(round_task("c", map_name="Bind"))
    until(lambda: rig.open_kinds() == ["warm"], what="its warm build running")
    rig.table.block_starts()                      # the scheduler idles, so nothing re-checks the tracked name below
    with rig.control.lock:
        # tracked, but its file isn't on the disk
        rig.control.warm_names[job.warm_id] = f"Bind.{'e' * 16}.npz"
    rig.hold()
    assert len(rig.cancel()[1]["cancelled"]) == 1
    until(lambda: rig.control.warm_jobs and rig.control.jobs[rig.control.warm_jobs[job.warm_id]].status == "queued",
          what="the warm build back in the queue")
    counts = rig.control.counts()["warm_builds"]
    assert counts["failures"] == {} and counts["missed"] == {} and counts["vanished"] == {}
    assert job.warm_id not in rig.control.warm_names                 # untracked: its file isn't on the disk
    assert rig.control.pending[0] == rig.control.warm_jobs[job.warm_id]     # at the front
    rig.hang = set()
    assert rig.release()[1] == {"hold": False}
    assert wait_done(rig.control, [job])[0].status == "done"
    assert warm_starts(rig.render) == 2


def test_a_leased_round_cancelled_by_a_drain_goes_back_to_the_front_uncounted(warm_rig):
    """State row 26: the warm build ran; the probe round holds the probe slot when the drain cancels it."""
    rig = warm_rig
    rig.hang = {"control"}
    job = rig.control.submit(round_task("p"))
    until(lambda: rig.open_kinds() == ["control"] and rig.control.warm_probe, what="the probe round running")
    assert job.released_name
    rig.hold()
    assert rig.cancel()[1]["cancelled"] == [{"kind": "control", "key": "p"}]
    until(lambda: job.status == "queued", what="the probe round queued again")
    assert rig.control.pending[0] == job.id and job.released_name is None
    assert rig.control.warm_probe == {}                               # the probe slot is free
    counts = rig.control.counts()["warm_builds"]
    assert counts["missed"] == {} and counts["failures"] == {} and counts["misses"] == 0
    assert job.error is None and not job.finished


def test_a_round_run_unleased_is_requeued_uncounted_too(rig):
    rig.hang = {"control"}
    jobs = [rig.control.submit(round_task(f"u{i}", map_name=f"M{i}")) for i in range(3)]
    until(lambda: rig.open_kinds() == ["control"] * 3, what="three open rounds")
    rig.hold()
    rig.cancel()
    until(lambda: all(j.status == "queued" for j in jobs), what="every round queued again")
    assert all(j.error is None and not j.finished for j in jobs) and set(rig.control.pending) == {j.id for j in jobs}
    assert rig.control.counts()["running"] == 0


def test_nothing_launches_or_is_warmed_while_starts_are_blocked(warm_rig):
    """State row 27."""
    rig = warm_rig
    rig.hang = {"control"}
    first = rig.control.submit(round_task("p"))
    until(lambda: rig.open_kinds() == ["control"] and rig.control.warm_probe, what="the probe round running")
    rig.hold()
    rig.cancel()
    until(lambda: first.status == "queued", what="the probe round queued again")
    started = len(rig.render.started)
    again = rig.control.submit(round_task("p2"))
    cold = rig.control.submit(round_task("c", map_name="Bind"))
    time.sleep(2.5)                                                   # two scheduler passes
    assert len(rig.render.started) == started and rig.control.counts()["running"] == 0
    assert not [i for i in rig.control.warm_jobs if i.startswith("Bind")]          # no warm build asked either
    assert rig.health()["starts_blocked"] is True
    rig.hang = set()
    assert rig.release()[1] == {"hold": False}
    assert [j.status for j in wait_done(rig.control, [first, again, cold])] == ["done"] * 3
    assert rig.health()["starts_blocked"] is False


def test_the_answer_is_immediate_while_render_cancels_slowly_and_health_counts_them(rig):
    rig.hang = {"control"}
    jobs = [rig.control.submit(round_task(f"s{i}", map_name=f"M{i}")) for i in range(3)]
    until(lambda: rig.open_kinds() == ["control"] * 3 and all(r.render_id for r in list(rig.table.open_runs.values())),
          what="three started rounds")
    real = rig.table.api.cancel

    def slow(run_id):
        time.sleep(2.0)
        real(run_id)
    rig.table.api.cancel = slow
    rig.hold()
    begun = time.monotonic()
    status, body = rig.cancel()
    assert status == 200 and time.monotonic() - begun < 1.0 and len(body["cancelled"]) == 3
    assert rig.health()["cancelling"] == 3
    again = rig.cancel()[1]                                           # a second cancel meanwhile: the same list
    assert again == body
    until(lambda: rig.health()["cancelling"] == 0, what="the Render cancels done")
    assert len(rig.render.cancelled) == 3 and all(j.status == "queued" for j in jobs)


def test_a_cancel_naming_another_drain_is_ignored(rig):
    rig.hang = {"control"}
    rig.control.submit(round_task("k"))
    until(lambda: rig.open_kinds() == ["control"], what="an open round")
    rig.hold(drain_id=7)
    assert rig.cancel(drain_id=6) == (200, {"ignored": True, "cancelled": []})
    assert rig.cancel(drain_id=8) == (200, {"ignored": True, "cancelled": []})
    assert rig.open_kinds() == ["control"] and not rig.table.starts_blocked() and rig.render.cancelled == []


def test_a_cancel_with_no_hold_is_ignored(rig):
    assert rig.cancel(drain_id=1) == (200, {"ignored": True, "cancelled": []})


def test_a_hold_that_expires_without_a_release_lifts_the_barrier(rig):
    rig.hang = {"control"}
    rig.control.submit(round_task("e"))
    until(lambda: rig.open_kinds() == ["control"], what="an open round")
    rig.hold(seconds=2)
    rig.cancel()
    assert rig.health()["starts_blocked"] is True
    rig.hang = set()
    until(lambda: rig.health()["starts_blocked"] is False, timeout=15, what="the barrier lifted by expiry")
    job = rig.control.submit(round_task("after"))
    assert wait_done(rig.control, [job])[0].status == "done"


def test_a_release_naming_no_id_lifts_a_barrier_that_has_no_hold(rig):
    """The site releases with the worker's own reported drain_id, null when only starts_blocked is true."""
    rig.table.block_starts()
    assert rig.health()["starts_blocked"] is True and rig.health()["hold"] is False
    assert send(rig.base, "/drain/release", {"drain_id": None}) == (200, {"hold": False})
    assert rig.health()["starts_blocked"] is False
    rig.table.block_starts()
    assert send(rig.base, "/drain/release", {"drain_id": 41}) == (200, {"hold": False})
    assert rig.health()["starts_blocked"] is False
    rig.table.block_starts()
    assert send(rig.base, "/drain/release", {})[0] == 200 and rig.health()["starts_blocked"] is False
    rig.hold(drain_id=50)                       # null never releases another drain's hold
    assert send(rig.base, "/drain/release", {"drain_id": None}) == (200, {"ignored": True})
    assert rig.health()["hold"] is True


# ------------------------------------------------------------------ cancel wins settlement

def post_result(base, job_id, token, body):
    data = json.dumps(body).encode()
    request = urllib.request.Request(f"{base}/runs/{job_id}/result", data=data, method="POST",
                                     headers={"X-Run-Token": token, "Content-Length": str(len(data)),
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_while_render_cancel_is_blocked_a_result_is_refused_and_a_reconcile_settles_drain(rig, monkeypatch):
    rig.hang = {"control"}
    rig.control.submit(round_task("w"))
    until(lambda: rig.open_kinds() == ["control"], what="an open round")
    run = next(iter(rig.table.open_runs.values()))
    gate = threading.Event()
    real = rig.table.api.cancel
    rig.table.api.cancel = lambda run_id: (gate.wait(30), real(run_id))
    rig.hold()
    rig.cancel()
    try:
        until(lambda: rig.health()["cancelling"] == 1, what="the cancel blocked")
        assert not run.done.is_set()
        assert post_result(rig.base, run.job_id, run.token, {"status": "ok", "key": "w"}) == 409
        assert not run.done.is_set()
        rig.render.status[run.render_id] = "canceled"
        monkeypatch.setattr(runs, "GRACE_S", 0)
        rig.table.reconcile()
        assert run.done.is_set() and run.result["status"] == "failed" and run.result["error_kind"] == "drain"
    finally:
        gate.set()
    until(lambda: rig.health()["cancelling"] == 0, what="the cancel done")


def test_a_render_error_on_cancel_still_settles_and_lists_the_run(rig):
    rig.hang = {"control"}
    job = rig.control.submit(round_task("x"))
    until(lambda: rig.open_kinds() == ["control"], what="an open round")

    def refuse(run_id):
        raise runs.RenderError("DELETE: HTTP 500")
    rig.table.api.cancel = refuse
    rig.hold()
    status, body = rig.cancel()
    assert status == 200 and body["cancelled"] == [{"kind": "control", "key": "x"}]
    until(lambda: job.status == "queued", what="the round settled as drain and queued again")
    until(lambda: rig.health()["cancelling"] == 0, what="the count back to zero")
    assert "HTTP 500" in (rig.table.last_error or "")


def test_a_ping_run_still_starts_while_starts_are_blocked(rig):
    rig.hold()
    rig.cancel()
    assert rig.table.starts_blocked()
    request = urllib.request.Request(f"{rig.base}/runs/ping", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        answer = json.loads(response.read())
    assert answer["ok"] is True
    # and a round does not
    job = rig.control.submit(round_task("blocked"))
    time.sleep(1.5)
    assert job.status == "queued"


def test_no_runs_table_answers_an_empty_list(tmp_path):
    settings = server.Settings(temp_root=tmp_path / "tmp")
    worker = server.Worker(settings, on_parse=None)
    httpd = server.make_server(worker)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert send(base, "/drain/hold", {"drain_id": 3, "until": time.time() + 60})[0] == 200
        assert send(base, "/drain/cancel", {"drain_id": 3}) == (200, {"cancelled": []})
        status, body = get(base, "/health")
        assert body["drain"]["starts_blocked"] is False and body["drain"]["cancelling"] == 0
        assert send(base, "/drain/cancel", {"drain_id": "x"})[0] == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


# ------------------------------------------------------------------ Runs.start's own cancel checks

class FakeApi:
    def __init__(self):
        self.started, self.cancelled = [], []
        self.entered, self.gate = threading.Event(), threading.Event()
        self.block_start = False

    def start(self, slug, args):
        if self.block_start:
            self.entered.set()
            self.gate.wait(10)
        self.started.append(args["job_id"])
        return {"id": f"r-{args['job_id']}"}

    def cancel(self, run_id):
        self.cancelled.append(run_id)

    def get(self, run_id):
        return {"id": run_id, "status": "running"}

    def page(self, slugs, cursor=None, limit=100):
        return [], None


class GatePacer:
    per_min = 99

    def __init__(self):
        self.entered, self.gate = threading.Event(), threading.Event()

    def take(self, stop=lambda: False):
        self.entered.set()
        self.gate.wait(10)
        return True

    def hold(self, seconds):
        pass


def start_in_thread(table, run):
    thread = threading.Thread(target=table.start, args=(run, {}), daemon=True)
    thread.start()
    return thread


def test_a_start_waiting_in_pacing_never_calls_start_after_the_cancel_even_once_released(tmp_path):
    api, pacer = FakeApi(), GatePacer()
    table = runs.Runs(api, "s", pacer, tmp_path)
    run = table.open("control", "j1", {}, timeout_s=60)
    thread = start_in_thread(table, run)
    assert pacer.entered.wait(5)
    assert table.cancel_all("a deploy") == [{"kind": "control", "job_id": "j1"}]
    table.unblock_starts()                         # the barrier lifts before pacing finishes
    assert not table.starts_blocked()
    pacer.gate.set()
    thread.join(5)
    assert api.started == [] and run.result["error_kind"] == "drain"


def test_a_start_whose_start_returns_after_the_cancel_is_cancelled_at_render_with_its_id(tmp_path):
    api = FakeApi()
    api.block_start = True
    table = runs.Runs(api, "s", runs.Pacer(60), tmp_path)
    run = table.open("control", "j2", {}, timeout_s=60)
    thread = start_in_thread(table, run)
    assert api.entered.wait(5)
    table.cancel_all("a deploy")
    table.unblock_starts()                         # and the barrier has lifted since
    api.gate.set()
    thread.join(5)
    until(lambda: api.cancelled == ["r-j2"], what="its Render id cancelled")
    assert run.render_id == "r-j2" and run.result["error_kind"] == "drain"
    until(lambda: table.cancelling == 0, what="the count back to zero")


def test_a_new_run_is_refused_while_blocked_and_a_ping_is_not(tmp_path):
    api = FakeApi()
    table = runs.Runs(api, "s", runs.Pacer(60), tmp_path)
    table.block_starts()
    blocked = table.open("control", "j3", {}, timeout_s=60)
    table.start(blocked, {})
    assert api.started == [] and blocked.result["error_kind"] == "drain"
    ping = table.open("ping", "j4", {}, timeout_s=60)
    table.start(ping, {})
    assert api.started == ["j4"] and ping.result is None
    assert table.starts_blocked()


def test_cancel_all_with_nothing_open_blocks_and_lists_nothing(tmp_path):
    table = runs.Runs(FakeApi(), "s", runs.Pacer(60), tmp_path)
    assert table.cancel_all("a deploy") == [] and table.starts_blocked() and table.cancelling == 0
    table.unblock_starts()
    assert not table.starts_blocked()


# ------------------------------------------------------------------ a Workflow parse and the tasks ping

def test_a_workflow_parse_cancelled_by_a_drain_is_queued_again_with_its_input_and_parses_after_release(tmp_path):
    rig = ParseRig(tmp_path)
    try:
        rig.gate.clear()
        job_id = rig.plant("queued")
        worker = rig.start()
        wait_for(lambda: len(rig.render.started) == 1)
        status, held = send(rig.base, "/drain/hold", {"drain_id": 9, "until": time.time() + 120})
        assert status == 200 and held["hold"] is True
        status, body = send(rig.base, "/drain/cancel", {"drain_id": 9})
        assert status == 200 and body["cancelled"] == [{"kind": "parse", "key": job_id}]
        wait_for(lambda: worker.get(job_id).status == "queued")
        assert (rig.disk / "jobs" / job_id / "upload.vrf").is_file()            # the input is kept
        record = record_of(rig, job_id)
        assert record["status"] == "queued" and "run_id" not in record and "run_token" not in record
        assert worker.drain_counts()["running"] == 0 and worker.drain_counts()["queued"] == 1
        time.sleep(1.5)
        assert len(rig.render.started) == 1                                      # held: nothing started again
        rig.gate.set()
        assert send(rig.base, "/drain/release", {"drain_id": 9}) == (200, {"hold": False})
        done, = wait_jobs(worker, [job_id])
        assert done.status == "done" and len(rig.render.started) == 2           # no restart: a second run
    finally:
        rig.close()


class PingLink:
    def __init__(self):
        self.result = None

    def fetch_text(self, name):
        return "hello"

    def post_result(self, result):
        self.result = result


def test_the_tasks_ping_answer_carries_the_services_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc")
    link = PingLink()
    tasks.ping({"job_id": "j", "token": "t"}, link)
    assert link.result == {"status": "ok", "echo": "hello", "commit": "abc"}
    monkeypatch.delenv("RENDER_GIT_COMMIT")
    tasks.ping({"job_id": "j", "token": "t"}, link)
    assert link.result["commit"] is None


def test_the_workers_ping_route_passes_the_answer_through_with_its_commit(rig, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc")
    request = urllib.request.Request(f"{rig.base}/runs/ping", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        answer = json.loads(response.read())
    assert answer["ok"] is True and answer["answer"]["commit"] == "abc"

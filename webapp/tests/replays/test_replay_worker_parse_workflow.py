"""Parsing as Workflow runs (spec 2026-10-14, step 3): the parse task, then the worker's parse threads end to end
through a fake Render."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import parse_job, server  # noqa: E402


class FakeLink:
    def __init__(self, files):
        self.files, self.result = files, None

    def fetch(self, name, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_result(self, result):
        self.result = result


def test_parse_refuses_another_recipe(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r-new")
    called = []
    monkeypatch.setattr(parse_job, "parse_file", lambda *a, **k: called.append(1))
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r-old", "sha256": "s"}, link)
    assert link.result["error_kind"] == "deploy" and called == []      # refused before any parse


def test_parse_reports_a_parse_failure_with_detail(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")

    def boom(*a, **k):
        raise parse_job.ParseFailed("parse failed", "Boom: bad chunk", 3.5)
    monkeypatch.setattr(parse_job, "parse_file", boom)
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "failed", "error_kind": "parse", "error": "parse failed",
                           "detail": "Boom: bad chunk", "parse_seconds": 3.5}


def test_parse_posts_the_result(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")
    monkeypatch.setattr(parse_job, "parse_file", lambda *a, **k: ({"match_uuid": "M", "map_name": "Ascent"}, 4.2))
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "ok", "result": {"match_uuid": "M", "map_name": "Ascent"}, "parse_seconds": 4.2}


import collections  # noqa: E402
import uuid as uuidlib  # noqa: E402

VRF = server.VRF_MAGIC + b"\x00" * 64


class Rig:
    """A fake Render, settings with the archive on a temp 'disk', and at most one live worker."""

    def __init__(self, tmp_path, **overrides):
        self.render = FakeRender()
        self.seen, self.gate = [], threading.Event()
        self.gate.set()
        self.disk = tmp_path / "disk"
        self.disk.mkdir()
        self.settings = server.Settings(**{
            "temp_root": tmp_path / "tmp", "parse_runner": "workflow", "parse_parallel": 15, "queue_size": 15,
            "workflow_slug": "replay-tasks", "render_api_key": "k", "render_api_base": self.render.url,
            "archive_dir": self.disk, "archive_require_mount": False, "archive_total_bytes": 500 * 1024 ** 3,
            **overrides})
        self.render.runner = self._answer_ok
        self.httpd = self.worker = None

    def _answer_ok(self, kind, args):
        self.seen.append(args["job_id"])
        self.gate.wait(30)
        self.link(args).post_result({"status": "ok", "parse_seconds": 1.0,
                                     "result": {"match_uuid": f"m-{args['job_id']}", "map_name": "Ascent"}})

    def link(self, args, token=None):
        return tasks.WorkerLink(self.base, args["job_id"], token or args["token"])

    def start(self):
        table = server.make_runs(self.settings)
        self.worker = server.Worker(self.settings, runs_table=table)
        self.httpd = server.make_server(self.worker, runs_table=table)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        return self.worker

    def plant(self, status="parsing", **record):
        """A job record as an earlier worker left it on the disk."""
        job_id = uuidlib.uuid4().hex
        folder = self.disk / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "upload.vrf").write_bytes(VRF)
        body = {"id": job_id, "kind": "upload", "status": status, "error": None, "sha256": "s" * 64,
                "size": len(VRF), "created": time.time(), "finished": None, "parse_seconds": None,
                "match_uuid": None, "map": None, **record}
        (folder / "job.json").write_text(json.dumps(body), encoding="utf-8")
        return job_id

    def close(self):
        self.gate.set()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.render.close()


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    try:
        yield made
    finally:
        made.close()


def wait_jobs(worker, ids, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if all(worker.get(i) is not None and worker.get(i).status in ("done", "failed") for i in ids):
            return [worker.get(i) for i in ids]
        time.sleep(0.05)
    raise AssertionError("jobs didn't finish")


def wait_for(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never held")


def record_of(rig, job_id):
    return json.loads((rig.disk / "jobs" / job_id / "job.json").read_text(encoding="utf-8"))


def test_local_parsing_keeps_todays_queue_and_archive_reserve(tmp_path):
    """render.yaml's REPLAY_QUEUE_SIZE=15 is for Workflow parsing (D15); a local parser keeps the image's 5, so the
    archive's parse reserve doesn't grow by 10 x the upload cap for nothing (spec D2)."""
    local = server.Settings(temp_root=tmp_path, parse_runner="local", queue_size=15)
    server.keep_local_sizing(local)
    assert local.queue_size == server.LOCAL_QUEUE_SIZE == 5
    remote = server.Settings(temp_root=tmp_path, parse_runner="workflow", queue_size=15)
    server.keep_local_sizing(remote)
    assert remote.queue_size == 15
    small = server.Settings(temp_root=tmp_path, parse_runner="local", queue_size=3)
    server.keep_local_sizing(small)
    assert small.queue_size == 3


def test_fifteen_uploads_parse_at_once(rig):
    worker = rig.start()
    rig.gate.clear()
    jobs = [worker.submit(VRF + bytes([i])) for i in range(15)]
    wait_for(lambda: len(rig.seen) == 15)
    assert worker.parse_count == 15 and worker.parsing and not worker.idle()
    rig.gate.set()
    done = wait_jobs(worker, [j.id for j in jobs])
    assert all(j.status == "done" and j.parse_seconds == 1.0 for j in done)
    wait_for(lambda: worker.parse_count == 0)   # a job reads done just before its thread lowers the count
    assert {s["task"] for s in rig.render.started} == {"replay-tasks/parse"}


def test_token_deadline_then_run_id_are_saved_and_kept_until_settlement(rig):
    at_start = []
    rig.render.on_start = lambda run_id, args: at_start.append(record_of(rig, args["job_id"]))
    worker = rig.start()
    rig.gate.clear()
    job = worker.submit(VRF + b"a")
    wait_for(lambda: record_of(rig, job.id).get("run_id") == "trn-1")      # the durable record itself
    # Saved before the start request: a crash before Render answers still finds the token and deadline.
    [before] = at_start
    assert before["run_token"] and before["run_deadline"] > time.time() and "run_id" not in before
    record = record_of(rig, job.id)
    assert record["status"] == "parsing" and record["run_token"] == before["run_token"]
    assert rig.render.started[0]["input"][0]["token"] == record["run_token"]
    rig.gate.set()
    wait_jobs(worker, [job.id])
    wait_for(lambda: record_of(rig, job.id)["status"] == "done")      # the final save follows the in-memory one
    final = record_of(rig, job.id)
    assert "run_id" not in final and "run_token" not in final


def test_a_restart_reconnects_to_a_saved_run_and_starts_nothing(rig):
    rig.render.finish = False
    rig.render.status["trn-9"], rig.render.slug_of["trn-9"] = "running", "replay-tasks/parse"
    job_id = rig.plant(run_id="trn-9", run_token="tok", run_deadline=time.time() + 600)
    worker = rig.start()
    wait_for(lambda: worker.get(job_id) is not None and worker.get(job_id).status == "parsing")
    time.sleep(0.3)
    assert rig.render.started == []                                         # reconnected, not restarted
    rig.link({"job_id": job_id}, token="tok").post_result(
        {"status": "ok", "parse_seconds": 3.0, "result": {"match_uuid": "m-x", "map_name": "Ascent"}})
    [done] = wait_jobs(worker, [job_id])
    assert done.status == "done" and done.parse_seconds == 3.0


def test_a_restart_without_a_run_id_starts_over_with_the_saved_token_and_deadline(rig):
    deadline = time.time() + 600
    job_id = rig.plant(run_token="tok", run_deadline=deadline)
    worker = rig.start()
    [done] = wait_jobs(worker, [job_id])
    assert done.status == "done" and len(rig.render.started) == 1
    assert rig.render.started[0]["input"][0]["token"] == "tok"


def test_recovery_beyond_the_queue_size_fails_nothing(tmp_path):
    rig = Rig(tmp_path, queue_size=2, parse_parallel=1)
    try:
        ids = [rig.plant(status=s) for s in ("parsing", "queued") * 6]          # 12 accepted, a queue of 2
        worker = rig.start()
        done = wait_jobs(worker, ids, timeout=60)
        assert all(j.status == "done" for j in done)
    finally:
        rig.close()


def test_deploy_refusals_never_outlive_the_jobs_deadline(tmp_path, monkeypatch):
    rig = Rig(tmp_path, parse_run_timeout_s=1.5)
    try:
        monkeypatch.setattr(server, "DEPLOY_WAIT_S", 0.4)
        rig.render.runner = lambda kind, args: rig.link(args).post_result(
            {"status": "failed", "error_kind": "deploy", "error": "recipe"})
        worker = rig.start()
        started = time.time()
        [done] = wait_jobs(worker, [worker.submit(VRF + b"d").id])
        assert done.status == "failed" and done.error == "parse timed out"
        assert time.time() - started < 4 and len(rig.render.started) <= 5
    finally:
        rig.close()


def test_a_deploy_refusal_waits_and_starts_a_new_run(rig, monkeypatch):
    monkeypatch.setattr(server, "DEPLOY_WAIT_S", 0.1)
    answers = iter([{"status": "failed", "error_kind": "deploy", "error": "recipe"}])
    rig.render.runner = lambda kind, args: rig.link(args).post_result(next(answers, {
        "status": "ok", "parse_seconds": 2.0, "result": {"match_uuid": "m", "map_name": "Ascent"}}))
    worker = rig.start()
    [done] = wait_jobs(worker, [worker.submit(VRF + b"b").id])
    assert done.status == "done" and len(rig.render.started) == 2


def test_failures_keep_todays_reasons(rig):
    answers = collections.deque([
        {"status": "failed", "error_kind": "parse", "error": "parse failed", "detail": "Boom", "parse_seconds": 2.0},
        {"status": "failed", "error_kind": "parse", "error": "parse timed out", "detail": None},
        {"status": "failed", "error_kind": "contract", "error": "not condensable: x"}])
    rig.render.runner = lambda kind, args: rig.link(args).post_result(answers.popleft())
    worker = rig.start()
    got = []
    for i in range(3):
        [job] = wait_jobs(worker, [worker.submit(VRF + bytes([10 + i])).id])
        got.append((job.status, job.error, job.detail))
    assert got == [("failed", "parse failed", "Boom"), ("failed", "parse timed out", None),
                   ("failed", "not condensable: x", None)]


def test_an_expiring_held_upload_is_never_released(rig, monkeypatch):
    worker = rig.start()
    job = server.Job("h1", rig.disk / "jobs" / "h1", "s" * 64, 10, status="held", game_build="release-99")
    job.folder.mkdir(parents=True)
    (job.folder / "upload.vrf").write_bytes(VRF)
    worker.supported_builds = set()                                 # nothing releasable until the hook says so
    job.created = 0.0                                               # long past HELD_TTL_S
    with worker.lock:
        worker.jobs["h1"] = job
    real_save = worker._save

    def save_then_try_release(saved, strict=False):
        real_save(saved, strict)
        if saved.id == "h1" and saved.status == "expiring":
            worker.supported_builds = {"release-99"}                # now releasable, were it still held
            assert worker._release_held() is None                   # the claim already excludes it
    monkeypatch.setattr(worker, "_save", save_then_try_release)
    with worker.sweep_lock:
        worker._sweep()
    assert worker.get("h1").status == "failed" and not (job.folder / "upload.vrf").exists()


def test_a_failed_save_after_the_run_started_leaves_no_run_open(rig, monkeypatch):
    worker = rig.start()
    real_save = worker._save

    def save(job, strict=False):
        if strict and job.run_id:
            raise OSError("disk full")
        real_save(job, strict)
    monkeypatch.setattr(worker, "_save", save)
    [done] = wait_jobs(worker, [worker.submit(VRF + b"s").id])
    assert done.status == "failed"
    wait_for(lambda: not worker.runs.open_runs)


def test_a_restart_finishes_an_upload_left_expiring(rig):
    job_id = rig.plant(status="expiring", game_build="release-99", created=1.0)
    worker = rig.start()
    wait_for(lambda: worker.get(job_id).status == "failed")
    assert not (rig.disk / "jobs" / job_id / "upload.vrf").exists()


def test_a_reparse_whose_file_is_another_match_fails_and_stores_nothing(rig):
    job_id = rig.plant(status="queued", kind="reparse", match_uuid="aaaa")
    worker = rig.start()
    [done] = wait_jobs(worker, [job_id])
    assert done.status == "failed" and done.error == "the archived file is another match"
    assert not (rig.disk / "jobs" / job_id / "result.json").exists()
    assert done.result is None


def test_a_recovered_job_past_its_deadline_cancels_its_saved_run(rig):
    rig.render.finish = False
    rig.render.status["trn-7"], rig.render.slug_of["trn-7"] = "running", "replay-tasks/parse"
    job_id = rig.plant(run_id="trn-7", run_token="tok", run_deadline=time.time() - 5)
    worker = rig.start()
    [done] = wait_jobs(worker, [job_id])
    assert done.status == "failed" and done.error == "parse timed out"
    assert "trn-7" in rig.render.cancelled and rig.render.started == []


def test_a_deploy_refusal_saves_the_cleared_run_id(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    try:
        monkeypatch.setattr(server, "DEPLOY_WAIT_S", 0.1)
        seen = []
        answers = iter([{"status": "failed", "error_kind": "deploy", "error": "recipe"}])

        def runner(kind, args):
            rig.link(args).post_result(next(answers, {
                "status": "ok", "parse_seconds": 2.0, "result": {"match_uuid": "m", "map_name": "Ascent"}}))
        rig.render.runner = runner
        # What the disk says while each start is in flight: never the new run's id, which isn't known yet.
        rig.render.on_start = lambda run_id, args: seen.append(record_of(rig, args["job_id"]).get("run_id"))
        worker = rig.start()
        wait_jobs(worker, [worker.submit(VRF + b"q").id])
        assert len(rig.render.started) == 2 and seen[1] is None     # the stale id was not left on the disk
    finally:
        rig.close()


def test_one_releasable_held_upload_starts_exactly_one_run(rig):
    worker = rig.start()
    job = server.Job("h2", rig.disk / "jobs" / "h2", "s" * 64, len(VRF), status="held", game_build="release-98")
    job.folder.mkdir(parents=True)
    (job.folder / "upload.vrf").write_bytes(VRF)
    with worker.lock:
        worker.supported_builds = {"release-98"}
        worker.jobs["h2"] = job
    worker.wake.set()
    [done] = wait_jobs(worker, ["h2"])
    time.sleep(0.5)
    assert done.status == "done" and len(rig.render.started) == 1

"""Height builds as Workflow runs (spec 2026-10-14, step 4): the heights task with a stub child, then a build
end to end through HeightBuilds, ControlRunner and a fake Render."""

import base64
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
from replay_worker import control_cache, server  # noqa: E402

STUB = """
import json, os, sys, pathlib, time
task = json.loads(sys.stdin.buffer.read())
time.sleep(float(os.environ.get("HEIGHT_STUB_SLEEP", "0")))
folder = pathlib.Path(task["dir"])
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
rounds = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
prev = (cache / "heights" / f"{task['map']}.{task.get('previous')}.height.npz").is_file()
print(json.dumps({"status": "ok", "key": task["key"], "rounds_seen": rounds, "had_previous": prev,
                  "asset": "YQ=="}))
"""


class FakeLink:
    def __init__(self, files):
        self.files, self.result = files, None

    def fetch(self, name, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        pass

    def post_result(self, result):
        self.result = result


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub_height.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_HEIGHT_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")


def test_heights_task_unpacks_the_rounds_and_passes_previous(tmp_path, stub):
    spool = tmp_path / "spool"
    (spool / "m1").mkdir(parents=True)
    (spool / "m1" / "1.json.gz").write_bytes(b"r")
    tar = control_cache.pack(spool, tmp_path / "rounds.tar").read_bytes()
    task = {"key": "k", "map": "Ascent", "dir": "/worker/path/ignored", "previous": "d0"}
    link = FakeLink({"task": json.dumps(task).encode(), "rounds": tar, "heights/Ascent.d0.height.npz": b"p"})
    tasks.heights({"job_id": "j", "token": "t", "map": "Ascent", "files": ["heights/Ascent.d0.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["rounds_seen"] == ["m1/1.json.gz"] and link.result["had_previous"] is True


def test_heights_task_refuses_a_bad_tar_as_infra(tmp_path, stub):
    link = FakeLink({"task": b'{"key": "k", "map": "Ascent"}', "rounds": b"not a tar"})
    tasks.heights({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"


@pytest.fixture
def remote(tmp_path, stub):
    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               heights_runner="workflow", workflow_slug="replay-tasks", render_api_key="k",
                               render_api_base=render.url)
    table = server.make_runs(settings)
    control = server.ControlRunner(settings, runs_table=table)
    builds = server.HeightBuilds(settings, control)
    worker = server.Worker(settings)
    httpd = server.make_server(worker, control=control, heights=builds, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: tasks.TASKS[kind](args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    try:
        yield control, builds, render, settings, worker, base
    finally:
        httpd.shutdown()
        httpd.server_close()
        render.close()


MATCH = "0123abcd-0000-0000-0000-000000000001"     # HeightBuilds.add accepts hex match ids only (MATCH_ID)


def open_and_fill(builds, map_name="Ascent", key="key-1"):
    manifest = {"map": map_name, "replays": [[MATCH, 1, "sha", 2]], "refinement_history": []}
    build = builds.open(key,map_name, 2, manifest)
    blob = base64.b64encode(b"round").decode()
    builds.add(build["id"], [{"match": MATCH, "n": 1, "blob": blob}, {"match": MATCH, "n": 2, "blob": blob}])
    return build


def wait_build(builds, build_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        body = builds.get(build_id)
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("build didn't finish")


def wait_for(check, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never held")


def test_a_build_runs_as_a_workflow_run(remote):
    control, builds, render, _, _, _ = remote
    build = open_and_fill(builds)
    builds.start(build["id"], None)
    body = wait_build(builds, build["id"])
    assert body["status"] == "done"
    assert body["result"]["rounds_seen"] == [f"{MATCH}/1.json.gz", f"{MATCH}/2.json.gz"]
    assert [s["task"] for s in render.started] == ["replay-tasks/heights"]


def test_a_remote_build_does_not_block_control_or_get_preempted(remote, monkeypatch):
    control, builds, _, settings, _, _ = remote
    monkeypatch.setenv("HEIGHT_STUB_SLEEP", "2")                      # the build stays running for a while
    settings.control_cmd = [sys.executable, "-c",
                            "import sys, json; sys.stdin.read(); print(json.dumps({'status': 'ok'}))"]
    build = open_and_fill(builds)
    builds.start(build["id"], None)
    wait_for(lambda: builds.get(build["id"])["status"] == "running")
    round_job = control.submit({"key": "r1", "map": "Bind", "blob": "", "link": {"sides": {}, "db_deaths": []}})
    wait_for(lambda: control.get(round_job.id).status == "done")     # a local round ran beside the remote build
    assert builds.get(build["id"])["status"] == "running"
    assert control.preempt() == 0 and not control.preempted
    assert wait_build(builds, build["id"])["status"] == "done"


def test_health_reads_idle_during_a_parse_when_builds_are_remote(remote, monkeypatch):
    import urllib.request
    _, _, _, _, worker, base = remote
    monkeypatch.setattr(worker, "idle", lambda: False)          # a parse is running
    with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
        assert json.loads(response.read())["idle"] is True


def test_a_build_whose_rounds_are_gone_fails_as_inputs(remote):
    import shutil
    control, builds, render, _, _, _ = remote
    build = open_and_fill(builds)
    shutil.rmtree(builds.root / build["id"])
    builds.start(build["id"], None)
    body = wait_build(builds, build["id"])
    assert body["status"] == "failed" and not render.started
    job = control.get(builds.builds[build["id"]]["job"])
    assert job.error_kind == "inputs"


def test_the_previous_asset_reaches_the_run_only_when_the_worker_has_it(remote):
    _, builds, _, settings, _, _ = remote
    previous = "0123456789ab"
    heights = settings.control_cache_dir / "heights"
    heights.mkdir(parents=True)
    (heights / f"Ascent.{previous}.height.npz").write_bytes(b"p")
    build = open_and_fill(builds)
    builds.start(build["id"], previous)
    assert wait_build(builds, build["id"])["result"]["had_previous"] is True


def test_no_previous_asset_when_the_worker_lacks_it(remote):
    _, builds, _, _, _, _ = remote
    build = open_and_fill(builds)
    builds.start(build["id"], "0123456789ab")
    assert wait_build(builds, build["id"])["result"]["had_previous"] is False


def test_a_parse_does_not_mark_a_remote_build_when_control_is_local(remote, monkeypatch):
    control, builds, _, settings, _, _ = remote
    monkeypatch.setenv("HEIGHT_STUB_SLEEP", "2")
    assert settings.control_runner != "workflow"                      # control local, heights workflow
    build = open_and_fill(builds)
    builds.start(build["id"], None)
    wait_for(lambda: builds.get(build["id"])["status"] == "running")
    assert control.preempt() == 0 and not control.preempted
    assert wait_build(builds, build["id"])["status"] == "done"


def tars(settings):
    return list((settings.temp_root / "height_runs").glob("*.tar"))


def test_no_packed_tar_is_left_after_a_done_build_or_a_failed_one(remote):
    import shutil
    _, builds, render, settings, _, _ = remote
    build = open_and_fill(builds)
    builds.start(build["id"], None)
    assert wait_build(builds, build["id"])["status"] == "done" and not tars(settings)
    gone = open_and_fill(builds, "Bind", "key-2")
    shutil.rmtree(builds.root / gone["id"])
    builds.start(gone["id"], None)
    assert wait_build(builds, gone["id"])["status"] == "failed" and not tars(settings)


def test_a_tar_a_crash_left_is_removed_when_the_builds_start(remote):
    control, _, _, settings, _, _ = remote
    runs = settings.temp_root / "height_runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "old.tar").write_bytes(b"x")
    server.HeightBuilds(settings, control)
    assert not tars(settings)


def test_a_remote_height_build_does_not_hold_its_map_against_a_cold_round(remote):
    control, _, _, _, _, _ = remote
    build = server.ControlJob("b1", "bk", "Ascent", b"{}", kind="heights")
    cold = server.ControlJob("r1", "rk", "Ascent", b"{}")
    with control.lock:
        control.jobs.update({"b1": build, "r1": cold})
        control.running["b1"] = False
        control.pending.append("r1")
        try:
            assert control._next() == (cold, True)
        finally:
            control.pending.remove("r1")
            control.running.pop("b1")
            del control.jobs["b1"], control.jobs["r1"]


def test_a_full_remote_control_pool_does_not_hold_back_a_local_build(tmp_path):
    """Control on Workflows, heights local: remote rounds take no CPU here, so a waiting local build starts when
    the worker is idle even with every remote slot taken (else the site times it out and spends a try)."""
    render = FakeRender()
    try:
        settings = server.Settings(temp_root=tmp_path, control_runner="workflow", heights_runner="local",
                                   control_runs=2, workflow_slug="w", render_api_key="k",
                                   render_api_base=render.url)
        control = server.ControlRunner(settings, runs_table=server.make_runs(settings))
        control.idle = lambda: True
        with control.lock:
            for i in range(2):
                control.jobs[f"r{i}"] = server.ControlJob(f"r{i}", f"k{i}", "Ascent", b"{}")
                control.running[f"r{i}"] = False
            control.jobs["b"] = server.ControlJob("b", "kb", "Haven", b"{}", kind="heights")
            control.jobs["r9"] = server.ControlJob("r9", "k9", "Ascent", b"{}")
            control.pending = ["r9", "b"]
            assert control._next() == (control.jobs["b"], False)
            control.idle = lambda: False                     # beside a parse the local build still waits
            assert control._next() is None
    finally:
        render.close()


def test_local_heights_keep_todays_spool_off_the_archive_disk(tmp_path):
    disk = tmp_path / "disk"
    local = server.Settings(temp_root=tmp_path / "tmp", archive_dir=disk, heights_runner="local",
                            height_spool_dir=disk / "height_builds", height_spool_bytes=2 * 1024 ** 3)
    server.keep_local_sizing(local)
    assert local.height_spool_dir is None and local.height_spool_bytes == server.LOCAL_SPOOL_BYTES == 512 * 1024 ** 2
    remote = server.Settings(temp_root=tmp_path / "tmp", archive_dir=disk, heights_runner="workflow",
                             height_spool_dir=disk / "height_builds", height_spool_bytes=2 * 1024 ** 3)
    server.keep_local_sizing(remote)
    assert remote.height_spool_dir == disk / "height_builds" and remote.height_spool_bytes == 2 * 1024 ** 3


def test_a_local_build_that_times_out_says_height_build_and_a_round_says_control(tmp_path):
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               height_cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
                               control_cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
                               height_timeout_s=0.3, control_timeout_s=0.3, control_warm_timeout_s=0.3)
    control = server.ControlRunner(settings)
    texts = {}
    for kind in ("heights", "control"):
        job = server.ControlJob(kind, kind, "Ascent", b"{}", kind=kind)
        with control.lock:
            control.jobs[kind] = job
            control.running[kind] = False
        control._run(job, False)
        texts[kind] = job.error
    assert texts["heights"].startswith("height build timed out after")
    assert texts["control"].startswith("control timed out after")

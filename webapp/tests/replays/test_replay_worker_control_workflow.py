"""Map control as Workflow runs (spec 2026-10-14, step 2): the control task against a fake worker link, then
ControlRunner end to end through a fake Render. The child is a stub command, as in test_replay_worker_control."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import runs, server  # noqa: E402

STUB = """
import json, os, sys, pathlib, time
task = json.loads(sys.stdin.buffer.read())
if str(task["link"].get("mode", "")).startswith("sleep"):
    time.sleep(float(task["link"]["mode"].split(":")[1]))
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
had = sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file())
if task["link"].get("build"):
    (cache / f"{task['map']}.{'c' * 16}.npz").write_bytes(b"built")
if task["link"].get("learn"):              # feature rows learned: the same file, rewritten in place
    rows = cache / "feature-rows" / f"{task['map']}.{'d' * 64}.npz"
    rows.write_bytes(rows.read_bytes() + b"+more")
mode = task["link"].get("mode", "ok")
if mode == "engine":
    print(json.dumps({"status": "failed", "error_kind": "engine", "error": "no side"}))
else:
    print(json.dumps({"status": "ok", "data": "ZA==", "summary": "cw==", "key": task["key"], "had": had}))
"""


class FakeLink:
    def __init__(self, files: dict[str, bytes]):
        self.files, self.posted, self.result, self.order = files, {}, None, []

    def fetch(self, name, dest):
        if name not in self.files:
            raise OSError(f"404 {name}")             # as urllib's HTTPError (an OSError) for a pruned file
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        self.order.append(route)
        self.posted[route] = Path(src).read_bytes()

    def post_result(self, result):
        self.order.append("result")
        self.result = result


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")
    return path


def round_task(key="k1", map_name="Ascent", **link):
    return {"key": key, "map": map_name, "blob": "", "link": {"sides": {}, "db_deaths": [], **link}}


def test_control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first(stub):
    link = FakeLink({"task": json.dumps(round_task(build=True)).encode(),
                     "Ascent.0123456789abcdef.npz": b"old", "heights/Ascent.d1.height.npz": b"h"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent",
                   "files": ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["had"] == ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]
    assert link.order == [f"cache/Ascent.{'c' * 16}.npz", "result"]
    assert link.posted[f"cache/Ascent.{'c' * 16}.npz"] == b"built"


def test_control_task_posts_a_cache_rewritten_in_place(stub):
    rows = f"feature-rows/Ascent.{'d' * 64}.npz"
    link = FakeLink({"task": json.dumps(round_task(learn=True)).encode(), rows: b"rows"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": [rows]}, link)
    route = "cache/" + rows.replace("/", "%2F")
    assert link.order == [route, "result"] and link.posted[route] == b"rows+more"


def test_control_task_skips_a_cache_file_it_cannot_fetch(stub):
    link = FakeLink({"task": json.dumps(round_task()).encode()})       # the listed cache was pruned meanwhile
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": ["Ascent.0123456789abcdef.npz"]}, link)
    assert link.result["status"] == "ok" and link.result["had"] == []


def test_control_task_passes_an_engine_failure_through(stub):
    link = FakeLink({"task": json.dumps(round_task(mode="engine")).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result == {"status": "failed", "error_kind": "engine", "error": "no side"}


def test_control_task_reports_a_child_that_prints_nothing_as_infra(tmp_path, monkeypatch):
    silent = tmp_path / "silent.py"
    silent.write_text("", encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(silent)]))
    link = FakeLink({"task": json.dumps(round_task()).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"


@pytest.fixture
def remote(tmp_path, stub):
    """A ControlRunner in workflow mode, its server, and a fake Render that runs tasks.control."""
    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_runs=40, workflow_slug="replay-tasks",
                               render_api_key="k", render_api_base=render.url)
    table = server.make_runs(settings)
    control = server.ControlRunner(settings, runs_table=table)
    worker = server.Worker(settings, on_parse=None)
    httpd = server.make_server(worker, control=control, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: tasks.TASKS[kind](args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    try:
        yield control, render, settings, base
    finally:
        httpd.shutdown()
        httpd.server_close()
        render.close()


def wait_running(control, at_least, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if control.counts()["running"] >= at_least:
            return
        time.sleep(0.02)
    raise AssertionError(f"never {at_least} running at once")


def wait_done(control, jobs, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if all(control.get(j.id).status in ("done", "failed") for j in jobs):
            return [control.get(j.id) for j in jobs]
        time.sleep(0.05)
    raise AssertionError("rounds didn't finish")


def test_rounds_run_as_workflow_runs_and_a_built_cache_lands_on_disk(remote):
    control, render, settings, _ = remote
    first = control.submit(round_task("k0", build=True))
    wait_done(control, [first])
    assert (settings.control_cache_dir / f"Ascent.{'c' * 16}.npz").read_bytes() == b"built"
    rest = [control.submit(round_task(f"k{i}")) for i in range(1, 6)]
    done = wait_done(control, rest)
    assert all(j.status == "done" for j in done)
    assert all(f"Ascent.{'c' * 16}.npz" in j.result["had"] for j in done)    # every later run got the cache
    assert {s["task"] for s in render.started} == {"replay-tasks/control"}


def test_engine_failure_settles_engine(remote):
    control, _, _, _ = remote
    job = control.submit(round_task("bad", mode="engine"))
    assert wait_done(control, [job])[0].error_kind == "engine"


def warm_up(control):
    """A first round that builds Ascent's visibility cache, so later rounds run side by side."""
    wait_done(control, [control.submit(round_task("warm", build=True))])


def test_a_parse_does_not_preempt_workflow_runs(remote):
    control, _, _, _ = remote
    warm_up(control)
    jobs = [control.submit(round_task(f"p{i}", mode="sleep:1.5")) for i in range(3)]
    wait_running(control, 3)
    assert control.preempt() == 0
    assert all(j.status == "done" for j in wait_done(control, jobs))
    assert control.counts()["preempted"] == 0


def test_runs_ignore_the_local_pool_size(remote):
    control, _, settings, _ = remote
    assert settings.control_workers == 2
    warm_up(control)
    control.idle = lambda: False                     # a parse is on: local mode would allow only one
    jobs = [control.submit(round_task(f"w{i}", mode="sleep:1.5")) for i in range(6)]
    wait_running(control, 6)                         # all six at once: more than the local pool of 2
    assert all(j.status == "done" for j in wait_done(control, jobs))


def test_a_local_parse_still_preempts_a_local_height_build(tmp_path, stub):
    """Control on Workflows, heights still local (spec D17): the local build is a victim, as today."""
    import subprocess
    render = FakeRender()
    try:
        settings = server.Settings(temp_root=tmp_path, control_runner="workflow", workflow_slug="w",
                                   render_api_key="k", render_api_base=render.url)
        control = server.ControlRunner(settings, runs_table=server.make_runs(settings))
        build = server.ControlJob("b1", "key", "Ascent", b"{}", kind="heights")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        with control.lock:
            control.jobs["b1"], control.running["b1"], control.procs["b1"] = build, False, process
        assert control.preempt() == 1 and "b1" in control.preempted
        assert process.wait(timeout=10) is not None
    finally:
        render.close()


def test_a_map_is_not_warm_when_its_cache_never_reached_the_worker(remote, monkeypatch):
    from replay_worker import control_cache
    control, _, settings, _ = remote
    monkeypatch.setattr(control_cache, "store", lambda *a, **k: False)     # the worker refuses every cache file
    job = control.submit(round_task("w0", build=True))
    assert wait_done(control, [job])[0].status == "done"
    assert job.warm_key not in control.warm
    assert not control_cache.has_visibility(settings.control_cache_dir, "Ascent")


def test_cache_route_refuses_other_maps_and_paths(remote, tmp_path):
    import urllib.error
    control, _, settings, base = remote
    control.jobs["jid"] = server.ControlJob("jid", "key", "Ascent", b"{}")
    run = control.runs.open("control", "jid", {}, timeout_s=30)
    link = tasks.WorkerLink(base, "jid", run.token)
    src = tmp_path / "x.npz"
    for bad in ("cache/Haven.0123456789abcdef.npz", "cache/..%2F..%2Farchive%2Fx.npz"):
        src.write_bytes(b"x")
        with pytest.raises(urllib.error.HTTPError) as refused:
            link.post_file(bad, src)
        assert refused.value.code == 400
    src.write_bytes(b"x")
    link.post_file("cache/Ascent.0123456789abcdef.npz", src)
    assert (settings.control_cache_dir / "Ascent.0123456789abcdef.npz").read_bytes() == b"x"


def test_local_mode_keeps_the_local_pool_even_with_control_runs_set(tmp_path):
    render = FakeRender()
    child = tmp_path / "child.py"
    child.write_text("import json, sys, time\njson.loads(sys.stdin.buffer.read())\ntime.sleep(0.6)\n"
                     "print(json.dumps({'status': 'ok'}))\n", encoding="utf-8")
    try:
        settings = server.Settings(temp_root=tmp_path / "tmp", control_runner="local", control_runs=40,
                                   control_workers=2, control_memory_mb=0, control_cmd=[sys.executable, str(child)],
                                   workflow_slug="w", render_api_key="k", render_api_base=render.url)
        control = server.ControlRunner(settings, runs_table=server.make_runs(settings))
        assert not control.remote(server.ControlJob("j", "k", "Ascent", b"{}"))
        control.warm.add("Ascent:")                                  # warm, so rounds may overlap
        jobs = [control.submit(round_task(f"l{i}")) for i in range(4)]
        most, end = 0, time.time() + 60
        while time.time() < end and not all(control.get(j.id).status in ("done", "failed") for j in jobs):
            most = max(most, control.counts()["running"])
            time.sleep(0.02)
        assert all(control.get(j.id).status == "done" for j in jobs)
        assert most == 2
    finally:
        render.close()


def test_control_task_names_the_caches_it_could_not_post(stub):
    class Refusing(FakeLink):
        def post_file(self, route, src):
            raise OSError("507")

    link = Refusing({"task": json.dumps(round_task(build=True)).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "ok" and link.result["cache_unsent"] == [f"Ascent.{'c' * 16}.npz"]
    ok = FakeLink({"task": json.dumps(round_task(build=True)).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, ok)
    assert "cache_unsent" not in ok.result


def _answering(render, base, extra):
    """The fake Render's run, adding `extra` to the control task's answer."""
    class Link(tasks.WorkerLink):
        def post_result(self, result):
            return super().post_result({**result, **extra})

    render.runner = lambda kind, args: tasks.TASKS[kind](args, Link(base, args["job_id"], args["token"]))


def test_an_older_height_cache_does_not_make_the_map_warm_when_the_new_one_was_not_sent(remote):
    control, render, settings, base = remote
    settings.control_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.control_cache_dir / f"Ascent.{'a' * 16}.npz").write_bytes(b"older heights")
    _answering(render, base, {"cache_unsent": [f"Ascent.{'c' * 16}.npz"]})
    job = control.submit(round_task("h0", build=True))
    assert wait_done(control, [job])[0].status == "done"
    assert job.warm_key not in control.warm


def test_an_older_height_cache_still_warms_the_map_when_nothing_went_unsent(remote):
    control, _, settings, _ = remote
    settings.control_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.control_cache_dir / f"Ascent.{'a' * 16}.npz").write_bytes(b"older heights")
    job = control.submit(round_task("h1"))
    assert wait_done(control, [job])[0].status == "done"
    assert job.warm_key in control.warm


def test_an_unsent_feature_row_cache_does_not_keep_a_map_cold(remote):
    control, render, settings, base = remote
    settings.control_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.control_cache_dir / f"Ascent.{'a' * 16}.npz").write_bytes(b"visibility")
    _answering(render, base, {"cache_unsent": [f"feature-rows/Ascent.{'d' * 64}.npz"]})
    job = control.submit(round_task("h2"))
    assert wait_done(control, [job])[0].status == "done"
    assert job.warm_key in control.warm


def test_a_feature_row_cache_rewritten_in_place_reaches_the_worker_through_the_route(remote):
    control, _, settings, _ = remote
    rows = settings.control_cache_dir / "feature-rows" / f"Ascent.{'d' * 64}.npz"
    rows.parent.mkdir(parents=True)
    rows.write_bytes(b"rows")
    job = control.submit(round_task("f0", learn=True))
    assert wait_done(control, [job])[0].status == "done"
    assert rows.read_bytes() == b"rows+more"


def test_a_run_that_fails_to_start_is_not_left_open(remote, monkeypatch):
    control, _, _, _ = remote

    def broken(*args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(control.runs, "start", broken)
    job = control.submit(round_task("s0"))
    assert wait_done(control, [job])[0].status == "failed"
    assert control.runs.open_runs == {}

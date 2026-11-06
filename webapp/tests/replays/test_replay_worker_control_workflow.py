"""Map control as Workflow runs (spec 2026-10-14, step 2): the control task against a fake worker link, then
ControlRunner end to end through a fake Render. The child is a stub command, as in test_replay_worker_control."""

import json
import queue
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
source = task["link"].get("source") or ("cache" if any(n.count(".") == 2 and "/" not in n for n in had) else "built")
if mode == "engine":
    print(json.dumps({"status": "failed", "error_kind": "engine", "error": "no side", "visibility_source": source}))
elif mode == "infra":
    print(json.dumps({"status": "failed", "error_kind": "infra", "error": "machine went away"}))
else:
    print(json.dumps({"status": "ok", "data": "ZA==", "summary": "cw==", "key": task["key"], "had": had,
                      "visibility_source": source}))
"""


WARM_STUB = """
import json, os, sys, pathlib
task = json.loads(sys.stdin.buffer.read())
if task["map"] == "Broken":
    print(json.dumps({"status": "failed", "error_kind": "infra", "error": "no geometry", "key": task["key"]}))
    sys.exit(0)
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
name = f"{task['map']}.{(task.get('height_mode') or 'e')[0] * 16}.npz"
source = "cache" if (cache / name).is_file() else "built"
if source == "built":
    (cache / name).write_bytes(b"vis")
print(json.dumps({"status": "ok", "visibility": name, "visibility_source": source, "visibility_s": 0.1,
                  "seconds": 0.2, "key": task["key"]}))
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

    def post_started(self):
        self.order.append("started")


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")
    return path


@pytest.fixture
def warm_stub(tmp_path, monkeypatch):
    path = tmp_path / "warm_stub.py"
    path.write_text(WARM_STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_WARM_CMD", json.dumps([sys.executable, str(path)]))
    return path


def test_warm_task_posts_the_new_cache_then_the_answer_naming_it(warm_stub):
    link = FakeLink({"task": json.dumps({"key": "warm:Ascent", "map": "Ascent"}).encode()})
    tasks.warm({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    name = f"Ascent.{'e' * 16}.npz"
    assert link.order == ["started", f"cache/{name}", "result"]
    assert link.result["visibility"] == name and link.result["visibility_source"] == "built"


def test_warm_task_with_the_cache_already_there_posts_only_the_answer(warm_stub):
    name = f"Ascent.{'e' * 16}.npz"
    link = FakeLink({"task": json.dumps({"key": "w", "map": "Ascent"}).encode(), name: b"vis"})
    tasks.warm({"job_id": "j", "token": "t", "map": "Ascent", "files": [name]}, link)
    assert link.order == ["started", "result"] and link.result["visibility_source"] == "cache"


def test_the_warm_task_is_registered_with_render():
    import ast

    tree = ast.parse((REPO / "replay_tasks" / "main.py").read_text(encoding="utf-8"))
    names = {kw.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
             for kw in node.keywords if kw.arg == "name"}
    assert "warm" in names and "warm" in tasks.TASKS


def round_task(key="k1",map_name="Ascent", **link):
    return {"key": key, "map": map_name, "blob": "", "link": {"sides": {}, "db_deaths": [], **link}}


def test_control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first(stub):
    link = FakeLink({"task": json.dumps(round_task(build=True)).encode(),
                     "Ascent.0123456789abcdef.npz": b"old", "heights/Ascent.d1.height.npz": b"h"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent",
                   "files": ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["had"] == ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]
    assert link.order == ["started", f"cache/Ascent.{'c' * 16}.npz", "result"]
    assert link.posted[f"cache/Ascent.{'c' * 16}.npz"] == b"built"


def test_control_task_posts_a_cache_rewritten_in_place(stub):
    rows = f"feature-rows/Ascent.{'d' * 64}.npz"
    link = FakeLink({"task": json.dumps(round_task(learn=True)).encode(), rows: b"rows"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": [rows]}, link)
    route = "cache/" + rows.replace("/", "%2F")
    assert link.order == ["started", route, "result"] and link.posted[route] == b"rows+more"


def post_started_to(base, job, token):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(f"{base}/runs/{job}/started", data=b"", method="POST",
                                     headers={"X-Run-Token": token, "Content-Length": "0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_started_route_moves_the_deadline_and_refuses_a_wrong_token(remote, monkeypatch):
    control, _, _, base = remote
    table = control.runs
    now = [1000.0]
    monkeypatch.setattr(table, "clock", lambda: now[0])
    run = table.open("control", "manual", {}, timeout_s=50)
    assert post_started_to(base, "manual", "wrong") == 409 and run.deadline == 1050.0
    now[0] = 1010.0
    assert post_started_to(base, "manual", run.token) == 200 and run.deadline == 1060.0
    table.close(run)


def test_an_older_worker_without_the_route_answers_404_and_the_task_carries_on(tmp_path):
    from fake_render import closed_port

    settings = server.Settings(temp_root=tmp_path / "tmp")
    httpd = server.make_server(server.Worker(settings, on_parse=None), runs_table=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert post_started_to(base, "j", "t") == 404
        tasks.WorkerLink(base, "j", "t", timeout_s=5).post_started()                       # 404: no raise
        tasks.WorkerLink(f"http://127.0.0.1:{closed_port()}", "j", "t", timeout_s=1).post_started()   # refused
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_control_task_skips_a_cache_file_it_cannot_fetch(stub):
    link = FakeLink({"task": json.dumps(round_task()).encode()})       # the listed cache was pruned meanwhile
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": ["Ascent.0123456789abcdef.npz"]}, link)
    assert link.result["status"] == "ok" and link.result["had"] == []


def test_control_task_tries_its_leased_cache_again_before_building_its_own(stub, monkeypatch):
    # B2: the leased file can't have been pruned, so a failed fetch is the network's: tried again.
    monkeypatch.setattr(tasks, "REQUIRED_FETCH_WAIT_S", 0)
    name = f"Ascent.{'e' * 16}.npz"
    link = FakeLink({"task": json.dumps(round_task()).encode(), name: b"vis"})
    real, fails = link.fetch, []

    def flaky(n, dest):
        if n == name and len(fails) < tasks.REQUIRED_FETCH_TRIES - 1:
            fails.append(1)
            raise OSError("connection reset")
        return real(n, dest)

    link.fetch = flaky
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": [name], "visibility": name}, link)
    assert link.result["had"] == [name] and link.result["visibility_source"] == "cache"
    link2 = FakeLink({"task": json.dumps(round_task()).encode()})       # never fetchable: it builds its own
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": [name], "visibility": name}, link2)
    assert link2.result["status"] == "ok" and link2.result["had"] == []


def test_control_task_passes_an_engine_failure_through(stub):
    link = FakeLink({"task": json.dumps(round_task(mode="engine")).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result == {"status": "failed", "error_kind": "engine", "error": "no side", "visibility_source": "built"}


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


@pytest.fixture
def warm_remote(tmp_path, stub, warm_stub):
    """`remote` with warm builds on (REPLAY_WARM_BUILDS)."""
    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_runs=40, workflow_slug="replay-tasks",
                               render_api_key="k", render_api_base=render.url, warm_builds=True)
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


def warm_starts(render):
    return [s["task"] for s in render.started].count("replay-tasks/warm")


def warm_and_confirm(control):
    """One warm build, then one probe round that reads its cache: the identity is confirmed."""
    wait_done(control, [control.submit(round_task("probe"))])


def test_a_cold_map_gets_one_warm_build_one_probe_then_every_round_at_once(warm_remote):
    control, render, _, _ = warm_remote
    jobs = [control.submit(round_task(f"r{i}", mode="sleep:1.5")) for i in range(6)]
    done = wait_done(control, jobs)
    assert all(j.status == "done" for j in done)
    assert warm_starts(render) == 1 and render.started[0]["task"] == "replay-tasks/warm"
    assert all(f"Ascent.{'e' * 16}.npz" in j.result["had"] for j in done)
    state = control.counts()["warm_builds"]
    assert state["misses"] == 0 and state["confirmed"] == [jobs[0].warm_id]


def test_after_confirmation_rounds_run_side_by_side(warm_remote):
    control, _, _, _ = warm_remote
    warm_and_confirm(control)
    jobs = [control.submit(round_task(f"s{i}", mode="sleep:1.5")) for i in range(6)]
    wait_running(control, 6)
    assert all(j.status == "done" for j in wait_done(control, jobs))


def test_a_probe_that_built_the_cache_holds_the_rest_and_warms_again(warm_remote):
    control, render, _, _ = warm_remote
    probe = control.submit(round_task("p", source="built", mode="sleep:0.5"))
    rest = [control.submit(round_task(f"q{i}")) for i in range(3)]
    wait_done(control, [probe])
    assert control.counts()["warm_builds"]["misses"] == 1
    assert all(j.status == "done" for j in wait_done(control, rest))
    assert warm_starts(render) == 2


def test_two_height_modes_are_two_identities_with_two_files(warm_remote):
    control, render, _, _ = warm_remote
    a = control.submit(round_task("a"))
    b = control.submit({**round_task("b"), "height_mode": "flat"})
    wait_done(control, [a, b])
    assert a.warm_id != b.warm_id and warm_starts(render) == 2
    assert f"Ascent.{'f' * 16}.npz" in b.result["had"]


def test_a_new_height_digest_gets_its_own_warm_build(warm_remote):
    control, render, _, _ = warm_remote
    warm_and_confirm(control)
    wait_done(control, [control.submit({**round_task("b"), "height": "d2"})])
    assert warm_starts(render) == 2


def test_a_warm_build_that_keeps_failing_falls_back_to_one_warming_round(warm_remote):
    control, render, _, _ = warm_remote
    job = control.submit(round_task("x", map_name="Broken", build=True))
    assert wait_done(control, [job])[0].status == "done"
    assert warm_starts(render) == server.WARM_BUILD_TRIES
    assert control.counts()["warm_builds"]["failures"] == {job.warm_id: server.WARM_BUILD_TRIES}


def test_a_round_whose_file_left_the_disk_waits_for_a_new_warm_build_never_runs_cold(warm_remote):
    control, render, settings, _ = warm_remote
    warm_and_confirm(control)
    (settings.control_cache_dir / f"Ascent.{'e' * 16}.npz").unlink()
    job = control.submit(round_task("b"))
    assert wait_done(control, [job])[0].status == "done"
    assert warm_starts(render) == 2
    assert f"Ascent.{'e' * 16}.npz" in job.result["had"]      # it ran with the cache, not cold
    assert control.counts()["warm_builds"]["vanished"] == {}   # B5: evicted after a probe read it, not a D3 loss


def test_a_failed_warm_build_and_a_probe_miss_together_end_in_the_fallback(warm_remote):
    # B3: a build that posts a file doesn't clear the earlier failure; only a probe that reads the file does.
    control, render, _, base = warm_remote
    inner, warms = render.runner, []

    def fail_the_first_warm_build(kind, args):
        if kind == "warm" and not warms:
            warms.append(1)
            link = tasks.WorkerLink(base, args["job_id"], args["token"])
            return link.post_result({"status": "failed", "error_kind": "infra", "error": "boom"})
        return inner(kind, args)

    render.runner = fail_the_first_warm_build
    probe = control.submit(round_task("p", source="built"))
    other = control.submit(round_task("o"))
    assert all(j.status == "done" for j in wait_done(control, [probe, other]))
    assert warm_starts(render) == server.WARM_BUILD_TRIES          # no third warm build
    state = control.counts()["warm_builds"]
    assert state["failures"] == {probe.warm_id: 1} and state["missed"] == {probe.warm_id: 1}


def test_a_probe_that_reads_the_file_clears_the_identitys_earlier_trouble(warm_remote):
    control, render, _, base = warm_remote
    inner, warms = render.runner, []

    def fail_the_first_warm_build(kind, args):
        if kind == "warm" and not warms:
            warms.append(1)
            link = tasks.WorkerLink(base, args["job_id"], args["token"])
            return link.post_result({"status": "failed", "error_kind": "infra", "error": "boom"})
        return inner(kind, args)

    render.runner = fail_the_first_warm_build
    warm_and_confirm(control)
    assert control.counts()["warm_builds"]["failures"] == {}


def test_after_its_files_are_lost_the_fallback_warms_the_map_alone(warm_remote):
    # B1: the probe left the map's legacy warm flag set; losing the files must clear it, or every fallback round
    # would start at once and each rebuild the cache.
    control, render, settings, _ = warm_remote
    warm_and_confirm(control)
    for path in settings.control_cache_dir.glob("*.npz"):
        path.unlink()                                  # evicted after the probe used it: no D3 loss
    inner = render.runner

    def lose_the_file(kind, args):
        out = inner(kind, args)
        if kind == "warm":
            for path in settings.control_cache_dir.glob("*.npz"):
                path.unlink()
        return out

    render.runner = lose_the_file
    jobs = [control.submit(round_task(f"f{i}", mode="sleep:1")) for i in range(3)]
    peak, end = 0, time.time() + 60
    while time.time() < end and not all(control.get(j.id).status in ("done", "failed") for j in jobs):
        peak = max(peak, sum(control.get(j.id).status == "running" for j in jobs))
        time.sleep(0.02)
    assert all(control.get(j.id).status == "done" for j in jobs)
    assert peak < len(jobs)
    assert control.counts()["warm_builds"]["vanished"] == {jobs[0].warm_id: server.WARM_BUILD_TRIES}


def test_an_identity_whose_file_leaves_the_disk_after_every_warm_build_ends_in_the_fallback(warm_remote):
    control, render, settings, _ = warm_remote
    inner = render.runner

    def runner_then_lose_the_file(kind, args):       # the file is pruned after each warm build settles
        out = inner(kind, args)
        if kind == "warm":
            for path in settings.control_cache_dir.glob("*.npz"):
                path.unlink()
        return out

    render.runner = runner_then_lose_the_file
    jobs = [control.submit(round_task(f"v{i}")) for i in range(3)]
    assert all(j.status == "done" for j in wait_done(control, jobs))
    assert warm_starts(render) == server.WARM_BUILD_TRIES
    assert control.counts()["warm_builds"]["vanished"] == {jobs[0].warm_id: server.WARM_BUILD_TRIES}


def test_warm_builds_take_no_queue_slot_of_the_sites(tmp_path, monkeypatch):
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_queue=2, warm_builds=True,
                               workflow_slug="replay-tasks", render_api_key="k")
    control = server.ControlRunner(settings, runs_table=server.make_runs(settings))
    monkeypatch.setattr(control, "wake", threading.Event())
    control.submit(round_task("q1"))
    warm = server.ControlJob("w1", "warm-key", "Ascent", b"{}", kind="warm")
    with control.lock:
        control.jobs[warm.id] = warm
        control.pending.append(warm.id)
    assert control.counts()["queued"] == 1
    control.submit(round_task("q2"))                       # 1 round + 1 warm build pending: not full
    with pytest.raises(queue.Full):
        control.submit(round_task("q3"))


def test_a_round_whose_lease_fails_is_requeued_uncounted_and_runs_after_a_new_warm_build(warm_remote, monkeypatch):
    from replay_worker import control_cache

    control, render, _, _ = warm_remote
    warm_and_confirm(control)
    real, calls = control_cache.lease, []

    def flaky(cache_dir, name):                    # the file vanishes between scheduling and the run's lease
        calls.append(name)
        return False if len(calls) == 1 else real(cache_dir, name)

    monkeypatch.setattr(control_cache, "lease", flaky)
    job = control.submit(round_task("b"))
    assert wait_done(control, [job])[0].status == "done"
    assert len(calls) == 2 and warm_starts(render) == 2
    assert f"Ascent.{'e' * 16}.npz" in job.result["had"]


def test_a_round_that_fails_for_the_machine_leaves_its_identity_confirmed(warm_remote):
    control, render, _, _ = warm_remote
    warm_and_confirm(control)
    assert wait_done(control, [control.submit(round_task("b", mode="infra"))])[0].error_kind == "infra"
    wait_done(control, [control.submit(round_task("c"))])
    assert warm_starts(render) == 1


def test_health_lists_only_identities_whose_file_is_on_disk(warm_remote):
    control, _, settings, _ = warm_remote
    warm_and_confirm(control)
    assert control.counts()["warm_builds"]["ready"]
    (settings.control_cache_dir / f"Ascent.{'e' * 16}.npz").unlink()
    assert control.counts()["warm_builds"]["ready"] == []
    assert control.counts()["warm_builds"]["recent_seconds"] == [0.2]


def test_warm_builds_off_keeps_todays_rule_and_reports_nothing(remote):
    control, render, _, _ = remote
    wait_done(control, [control.submit(round_task("a", build=True))])
    assert warm_starts(render) == 0 and "warm_builds" not in control.counts()


def test_settings_read_the_warm_build_switch_and_limit():
    on = server.Settings.from_env({"REPLAY_WARM_BUILDS": "true", "REPLAY_WARM_BUILD_TIMEOUT_S": "600"})
    assert on.warm_builds and on.warm_build_timeout_s == 600.0
    assert not server.Settings.from_env({}).warm_builds


def test_a_refused_warm_start_spends_no_try_and_is_asked_again(warm_remote, monkeypatch):
    control, render, _, _ = warm_remote
    monkeypatch.setattr(server, "WARM_START_RETRY_S", 0.5)
    real, refused = control.runs.api.start, []

    def flaky(slug, args):                    # the workflow hasn't registered `warm` yet: Render answers 404
        if slug.endswith("/warm") and len(refused) < 3:
            refused.append(slug)
            raise runs.RenderError("POST /task-runs: HTTP 404")
        return real(slug, args)

    monkeypatch.setattr(control.runs.api, "start", flaky)
    job = control.submit(round_task("a"))
    assert wait_done(control, [job])[0].status == "done"
    assert len(refused) == 3 and control.counts()["warm_builds"]["failures"] == {}
    assert warm_starts(render) == 1                # Render saw only the start it accepted


def test_refused_warm_starts_are_free_only_for_so_long(warm_remote, monkeypatch):
    control, _, _, _ = warm_remote
    monkeypatch.setattr(server, "WARM_START_REFUSED_MAX_S", 0.5)
    monkeypatch.setattr(server, "WARM_START_RETRY_S", 0.2)
    real = control.runs.api.start

    def refusing(slug, args):
        if slug.endswith("/warm"):
            raise runs.RenderError("POST /task-runs: HTTP 404")
        return real(slug, args)

    monkeypatch.setattr(control.runs.api, "start", refusing)
    job = control.submit(round_task("a"))
    assert wait_done(control, [job])[0].status == "done"          # the fallback's warming round
    assert control.counts()["warm_builds"]["failures"][job.warm_id] == server.WARM_BUILD_TRIES


def test_a_warm_build_and_its_rounds_that_keep_disagreeing_end_in_the_fallback(warm_remote):
    control, render, _, _ = warm_remote
    jobs = [control.submit(round_task(f"m{i}", source="built")) for i in range(3)]
    assert all(j.status == "done" for j in wait_done(control, jobs))
    assert warm_starts(render) == server.WARM_BUILD_TRIES
    assert control.counts()["warm_builds"]["missed"] == {jobs[0].warm_id: server.WARM_BUILD_TRIES}


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


def test_health_shows_the_control_cache(remote):
    import urllib.request

    control, _, _, base = remote
    warm_up(control)
    with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
        body = json.loads(response.read())
    assert body["control"]["cache"]["maps"] == {"Ascent": len(b"built")}
    assert body["control"]["cache"]["reserve"] > 0


def test_a_childs_stderr_reaches_the_runs_own_log(tmp_path, monkeypatch, capfd):
    say = tmp_path / "say.py"
    say.write_text("import json, os, sys\n"
                   "sys.stderr.write('control phase: ' + os.environ.get('CONTROL_PHASE_LOG', '-') + '\\n')\n"
                   "print(json.dumps({'status': 'ok'}))\n", encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(say)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")
    answer = tasks._run_child("REPLAY_CONTROL_CMD", [], b"{}", tmp_path)
    assert answer == {"status": "ok"}
    assert "control phase: 1" in capfd.readouterr().err


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


def _mixed(tmp_path, render):
    """Control on Workflows, heights local (the mix spec D17 keeps working during the migration)."""
    settings = server.Settings(temp_root=tmp_path, control_runner="workflow", workflow_slug="w",
                               render_api_key="k", render_api_base=render.url)
    return server.ControlRunner(settings, runs_table=server.make_runs(settings))


def test_a_running_local_build_does_not_hold_back_remote_rounds(tmp_path):
    render = FakeRender()
    try:
        control = _mixed(tmp_path, render)
        build = server.ControlJob("b1", "key", "Ascent", b"{}", kind="heights")
        round_ = server.ControlJob("r1", "key", "Bind", b"{}")
        with control.lock:
            control.jobs.update(b1=build, r1=round_)
            control.running["b1"] = False
            control.pending.append("r1")
            assert control._next() == (round_, True)      # it takes no CPU here, so it doesn't wait an hour
    finally:
        render.close()


def test_a_pending_local_build_does_not_wait_for_remote_rounds_to_drain(tmp_path):
    render = FakeRender()
    try:
        control = _mixed(tmp_path, render)
        build = server.ControlJob("b1", "key", "Ascent", b"{}", kind="heights")
        with control.lock:
            control.jobs["b1"] = build
            for i in range(3):
                control.jobs[f"r{i}"] = server.ControlJob(f"r{i}", "key", "Bind", b"{}")
                control.running[f"r{i}"] = False
            control.pending.append("b1")
            assert control._next() == (build, False)
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


def test_local_control_keeps_its_cache_off_the_archive_disk(tmp_path):
    """Blueprint sets CONTROL_CACHE_DIR on the disk for Workflow runs; while control is local the cache stays in
    the image's own folder, so the archive keeps its whole budget (spec D2: local is today's behaviour)."""
    disk = tmp_path / "disk"
    local = server.Settings(temp_root=tmp_path / "tmp", archive_dir=disk, control_cache_dir=disk / "control_cache",
                            control_runner="local")
    server.keep_local_sizing(local)
    assert local.control_cache_dir == tmp_path / "tmp" / "control_cache"
    remote = server.Settings(temp_root=tmp_path / "tmp", archive_dir=disk, control_cache_dir=disk / "control_cache",
                             control_runner="workflow")
    server.keep_local_sizing(remote)
    assert remote.control_cache_dir == disk / "control_cache"
    assert local.archive_extra_reserve == 0 and remote.archive_extra_reserve == 0     # nothing on the disk yet


def test_a_rollback_to_local_reserves_the_cache_left_on_the_disk(tmp_path):
    """A Workflow spell filled <archive>/control_cache; after the switch back it stays (a switch forward again finds
    the maps warm), so the archive must not grow into it."""
    disk = tmp_path / "disk"
    (disk / "control_cache" / "feature-rows").mkdir(parents=True)
    (disk / "control_cache" / "Ascent.0000000000000001.npz").write_bytes(b"x" * 700)
    (disk / "control_cache" / "feature-rows" / f"Ascent.{'a' * 64}.npz").write_bytes(b"x" * 300)
    local = server.Settings(temp_root=tmp_path / "tmp", archive_dir=disk, control_cache_dir=disk / "control_cache",
                            control_runner="local")
    server.keep_local_sizing(local)
    assert local.control_cache_dir == tmp_path / "tmp" / "control_cache"
    assert local.archive_extra_reserve == 1000


def test_a_local_child_is_told_the_cache_folder_the_worker_uses(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_CACHE_DIR", str(tmp_path / "disk" / "control_cache"))
    child = tmp_path / "child.py"
    child.write_text("import json, os, sys\njson.loads(sys.stdin.buffer.read())\n"
                     "print(json.dumps({'status': 'ok', 'cache': os.environ['CONTROL_CACHE_DIR']}))\n",
                     encoding="utf-8")
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "tmp" / "control_cache",
                               control_memory_mb=0, control_cmd=[sys.executable, str(child)])
    control = server.ControlRunner(settings)
    job = control.submit(round_task("c0"))
    end = time.time() + 30
    while time.time() < end and control.get(job.id).status not in ("done", "failed"):
        time.sleep(0.02)
    assert control.get(job.id).result["cache"] == str(tmp_path / "tmp" / "control_cache")


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


def test_an_engine_failure_whose_new_cache_was_not_sent_leaves_the_map_cold(remote):
    control, render, settings, base = remote
    settings.control_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.control_cache_dir / f"Ascent.{'a' * 16}.npz").write_bytes(b"older heights")
    _answering(render, base, {"cache_unsent": [f"Ascent.{'c' * 16}.npz"]})
    job = control.submit(round_task("e0", mode="engine"))
    assert wait_done(control, [job])[0].error_kind == "engine"
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


def test_workflow_limits_apply_to_runs_and_local_limits_to_children():
    s = server.Settings.from_env({"REPLAY_CONTROL_RUN_TIMEOUT_S": "7200", "REPLAY_HEIGHT_RUN_TIMEOUT_S": "18000"})
    assert (s.control_run_timeout_s, s.height_run_timeout_s) == (7200.0, 18000.0)
    assert (s.control_timeout_s, s.control_warm_timeout_s, s.height_timeout_s) == (1800.0, 1800.0, 3600.0)


def test_a_workflow_round_is_opened_with_the_workflow_limit(remote, monkeypatch):
    control, _, settings, _ = remote
    monkeypatch.setattr(settings, "control_run_timeout_s", 1234.0)
    seen = []
    real = control.runs.open
    monkeypatch.setattr(control.runs, "open", lambda kind, job_id, files, timeout_s, **k:
                        seen.append((kind, timeout_s)) or real(kind, job_id, files, timeout_s, **k))
    wait_done(control, [control.submit(round_task("t", build=True))])
    assert seen == [("control", 1234.0)]


def test_the_cache_reserve_setting_reaches_the_store_and_health(tmp_path, stub):
    import urllib.request

    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_runs=4, workflow_slug="replay-tasks",
                               render_api_key="k", render_api_base=render.url, control_cache_reserve=3)
    table = server.make_runs(settings)
    control = server.ControlRunner(settings, runs_table=table)
    httpd = server.make_server(server.Worker(settings, on_parse=None), control=control, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: tasks.TASKS[kind](args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    try:
        job = control.submit(round_task("big", build=True))     # writes b"built": 5 bytes, past a 3-byte reserve
        wait_done(control, [job])
        assert not (settings.control_cache_dir / f"Ascent.{'c' * 16}.npz").exists()
        with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
            assert json.loads(response.read())["control"]["cache"]["reserve"] == 3
    finally:
        httpd.shutdown()
        httpd.server_close()
        render.close()


def test_the_cache_reserve_is_read_from_the_environment():
    assert server.Settings.from_env({"REPLAY_CONTROL_CACHE_RESERVE_BYTES": "12345"}).control_cache_reserve == 12345
    assert server.Settings.from_env({}).control_cache_reserve == server.control_cache.RESERVE

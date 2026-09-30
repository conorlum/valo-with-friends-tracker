"""Map control on the upload worker (replay_worker/server.py ControlRunner, replay_worker/control_job.py;
docs/map-control-worker-plan.md, step 3), with a stub child command: dedupe, the fixed pool, a map's first
round alone, failure kinds, timeouts and the HTTP answers. Localhost only; no DB, no Docker."""

import base64
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from replay_worker import control_job, server  # noqa: E402

STUB = """
import json, os, sys, time
task = json.loads(sys.stdin.buffer.read())
log = {log!r}   # a folder: one file per event (appends from several processes interleave on Windows)
mode = task["link"].get("mode", "ok")
def event(kind):
    open(os.path.join(log, f"{{time.time():.6f}}_{{kind}}_{{task['map']}}_{{os.getpid()}}"), "w").close()
event("start")
if mode.startswith("sleep"):
    time.sleep(float(mode.split(":")[1]))
if mode == "hang":
    time.sleep(60)
event("end")
if mode == "engine":
    print(json.dumps({{"status": "failed", "error_kind": "engine", "error": "ControlError: no side"}}))
elif mode == "infra":
    print(json.dumps({{"status": "failed", "error_kind": "infra", "error": "MemoryError"}}))
else:
    print(json.dumps({{"status": "ok", "data": "ZA==", "summary": "cw==", "revision": 1, "data_version": 1,
                      "key": task["key"]}}))
"""


@pytest.fixture
def runner(tmp_path):
    log = tmp_path / "events"
    log.mkdir()
    stub = tmp_path / "stub_control.py"
    stub.write_text(STUB.format(log=str(log)), encoding="utf-8")
    made = []

    def make(**overrides):
        settings = server.Settings(control_cmd=[sys.executable, str(stub)], **overrides)
        r = server.ControlRunner(settings)
        made.append(r)
        return r, log
    return make


def task(key, map_name="Ascent", mode="ok"):
    return {"key": key, "map": map_name, "blob": "", "link": {"sides": {}, "db_deaths": [], "mode": mode}}


def wait_all(runner, jobs, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if all(runner.get(j.id).status in ("done", "failed") for j in jobs):
            return
        time.sleep(0.05)
    raise AssertionError("jobs didn't finish")


def overlaps(log: Path):
    """The most children running at once, and the events in order."""
    events = sorted((float(t), kind, name) for t, kind, name, _ in (p.name.split("_") for p in log.iterdir()))
    running = most = 0
    for _, kind, _ in events:
        running += 1 if kind == "start" else -1
        most = max(most, running)
    return most, events


def test_a_key_already_queued_running_or_done_is_the_same_job(runner):
    r, _ = runner()
    first = r.submit(task("37:1:abc"))
    assert r.submit(task("37:1:abc")).id == first.id
    wait_all(r, [first])
    assert r.submit(task("37:1:abc")).id == first.id           # done: the result is still there
    assert r.get(first.id).result["data"] == "ZA=="


def test_a_maps_first_round_runs_alone_then_the_pool_fills_but_never_overflows(runner):
    r, log = runner(control_workers=2)
    jobs = [r.submit(task(f"k{i}", "Ascent", "sleep:0.4")) for i in range(6)]
    wait_all(r, jobs)
    most, events = overlaps(log)
    assert most == 2
    assert [kind for _, kind, _ in events[:2]] == ["start", "end"], "the warming round runs alone"


def test_another_map_warms_beside_a_warm_one(runner):
    r, log = runner(control_workers=2)
    a = [r.submit(task("a0", "Ascent"))]
    wait_all(r, a)
    jobs = [r.submit(task(f"b{i}", "Split", "sleep:0.4")) for i in range(2)] + \
           [r.submit(task(f"a{i}", "Ascent", "sleep:0.4")) for i in range(1, 3)]
    wait_all(r, jobs)
    assert overlaps(log)[0] == 2
    assert set(r.counts()["warm"]) == {"Ascent", "Split"}


def test_failure_kinds_and_a_machine_failure_makes_the_map_cold_again(runner):
    r, log = runner(control_workers=2)
    engine, infra = r.submit(task("e", "Haven", "engine")), None
    wait_all(r, [engine])
    assert r.get(engine.id).status == "failed" and r.get(engine.id).error_kind == "engine"
    assert "Haven" in r.counts()["warm"], "an engine failure got past loading"
    infra = r.submit(task("i", "Haven", "infra"))
    wait_all(r, [infra])
    assert r.get(infra.id).error_kind == "infra" and "Haven" not in r.counts()["warm"]
    # an infra failure can be retried under the same key
    for p in log.iterdir():
        p.unlink()
    again = r.submit(task("i", "Haven"))
    assert again.id != infra.id
    jobs = [again] + [r.submit(task(f"h{i}", "Haven", "sleep:0.3")) for i in range(2)]
    wait_all(r, jobs)
    assert [kind for _, kind, _ in overlaps(log)[1][:2]] == ["start", "end"], "cold again: the next round warms alone"


def test_a_round_past_its_timeout_is_killed_and_is_the_machines_failure(runner):
    r, _ = runner(control_warm_timeout_s=0.5)
    job = r.submit(task("slow", "Abyss", "hang"))
    wait_all(r, [job], timeout=30)
    assert r.get(job.id).status == "failed" and r.get(job.id).error_kind == "infra"
    assert "timed out" in r.get(job.id).error


def test_the_queue_has_a_size(runner):
    r, _ = runner(control_workers=1, control_queue=2)
    r.submit(task("q0", "Sunset", "sleep:2"))
    time.sleep(0.3)                          # q0 is running now
    r.submit(task("q1", "Sunset"))
    r.submit(task("q2", "Sunset"))
    with pytest.raises(server.queue.Full):
        r.submit(task("q3", "Sunset"))


def http(base, path, body=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    request = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def serve(tmp_path, control):
    settings = server.Settings(temp_root=tmp_path)
    httpd = server.make_server(server.Worker(settings), control=control)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def test_the_http_answers(tmp_path, runner):
    r, _ = runner()
    base = serve(tmp_path, r)
    assert http(base, "/control", b"not json")[0] == 400
    assert http(base, "/control", {"key": 1})[0] == 400
    code, body = http(base, "/control", task("h1"))
    assert code == 202 and body["status"] in ("queued", "running")
    wait_all(r, [r.get(body["id"])])
    code, job = http(base, f"/control/{body['id']}")
    assert code == 200 and job["status"] == "done" and job["result"]["summary"] == "cw=="
    assert http(base, "/control/nope")[0] == 404
    health = http(base, "/health")[1]["control"]
    assert health["enabled"] is True and "Ascent" in health["warm"]


def test_control_off_answers_404_and_parsing_is_untouched(tmp_path, runner):
    r, _ = runner(control_enabled=False)
    base = serve(tmp_path, r)
    assert http(base, "/control", task("x"))[0] == 404
    assert http(base, "/health")[1]["control"]["enabled"] is False
    base = serve(tmp_path, None)
    assert http(base, "/control", task("x"))[0] == 404


def test_settings_from_the_environment():
    s = server.Settings.from_env({"REPLAY_CONTROL": "0", "REPLAY_CONTROL_WORKERS": "3",
                                  "REPLAY_CONTROL_CMD": '["/opt/control-venv/bin/python", "-m", "replay_worker.control_job"]',
                                  "REPLAY_CONTROL_TIMEOUT_S": "60", "REPLAY_CONTROL_MEMORY_MB": "1024"})
    assert (s.control_enabled, s.control_workers, s.control_timeout_s, s.control_memory_mb) == (False, 3, 60.0, 1024)
    assert s.control_cmd[0] == "/opt/control-venv/bin/python"
    assert server.Settings.from_env({}).control_workers == 2      # never the core count


def test_the_child_returns_the_tasks_bytes_in_base64(monkeypatch):
    from control_toys import blob, open_hall

    from app.control import task as control_task
    from app.replays import control_format as cf
    from app.replays import format as fmt

    geo = open_hall()
    monkeypatch.setitem(control_task._GEOMETRY, geo.name, geo)
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}, t_end=3.0)
    raw = fmt.encode_blob(data)
    t = {"key": "k", "map": geo.name, "blob": base64.b64encode(raw).decode(),
         "link": {"sides": {"0": "attack", "5": "defense"}, "db_deaths": []}}
    result = control_job.run(t)
    direct = control_task.compute_task({**t, "blob": raw})
    assert result["status"] == "ok" and base64.b64decode(result["data"]) == direct["data"]
    assert base64.b64decode(result["summary"]) == direct["summary"]
    assert (result["revision"], result["data_version"]) == (cf.CONTROL_REVISION, cf.DATA_VERSION)

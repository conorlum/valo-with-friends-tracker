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
elif mode == "garbage":
    print("not json")
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
    assert set(r.counts()["warm"]) == {"Ascent:", "Split:"}


def test_failure_kinds_and_a_machine_failure_makes_the_map_cold_again(runner):
    r, log = runner(control_workers=2)
    engine, infra = r.submit(task("e", "Haven", "engine")), None
    wait_all(r, [engine])
    assert r.get(engine.id).status == "failed" and r.get(engine.id).error_kind == "engine"
    assert "Haven:" in r.counts()["warm"], "an engine failure got past loading"
    infra = r.submit(task("i", "Haven", "infra"))
    wait_all(r, [infra])
    assert r.get(infra.id).error_kind == "infra" and "Haven:" not in r.counts()["warm"]
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


# ---------------------------------------------------------------- one child beside a parse; a parse kills one
# (docs/superpowers/plans/2026-10-05-control-idle-queue.md, D4 as amended 2026-10-07)


def wait_until(predicate, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_one_child_runs_while_the_worker_is_busy_and_two_once_it_is_idle(runner):
    busy = {"on": True}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # it runs though a parse is on: one is allowed
    jobs = [r.submit(task(f"a{i}:1:f", mode="sleep:0.6")) for i in range(3)]
    wait_all(r, jobs)
    assert overlaps(log)[0] == 1, "never two beside a parse"
    for p in log.iterdir():
        p.unlink()
    busy["on"] = False
    jobs = [r.submit(task(f"b{i}:1:f", mode="sleep:0.6")) for i in range(3)]
    wait_all(r, jobs)
    assert overlaps(log)[0] == 2


def test_a_parse_kills_the_child_started_last_and_its_round_goes_back_first_uncounted(runner):
    busy = {"on": False}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # the map is warm: two may run at once
    a = r.submit(task("a:1:f", mode="sleep:6"))
    wait_until(lambda: r.counts()["running"] == 1)
    b = r.submit(task("b:1:f", mode="sleep:30"))
    c = r.submit(task("c:1:f"))
    wait_until(lambda: r.counts()["running"] == 2)
    busy["on"] = True
    assert r.preempt() == 1
    wait_until(lambda: r.counts()["running"] == 1)
    assert r.get(a.id).status == "running", "the child started first is left alone"
    assert r.get(b.id).status == "queued" and r.pending[:2] == [b.id, c.id]
    assert r.get(b.id).error is None and r.get(b.id).task, "uncounted, task kept"
    assert r.counts()["preempted"] == 1 and "Ascent:" in r.counts()["warm"]
    assert r.preempt() == 0, "a second parse finds one child: nothing to kill"
    r.jobs[b.id].task = json.dumps(task("b:1:f")).encode()      # let it finish quickly this time
    wait_all(r, [a, b, c])                                       # still busy: they finish one at a time
    assert r.counts()["preempted"] == 1


def test_a_preempted_warming_round_leaves_its_map_cold(runner):
    busy = {"on": False}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # Ascent is warm
    first = r.submit(task("a:1:f", mode="sleep:6"))
    wait_until(lambda: r.counts()["running"] == 1)
    warming = r.submit(task("s:1:f", "Split", mode="sleep:30"))  # another map warms beside a warm one
    wait_until(lambda: r.counts()["running"] == 2)
    busy["on"] = True
    assert r.preempt() == 1
    wait_until(lambda: r.counts()["running"] == 1)
    assert r.get(warming.id).status == "queued" and "Split:" not in r.counts()["warm"]
    r.jobs[warming.id].task = json.dumps(task("s:1:f", "Split")).encode()
    busy["on"] = False
    wait_all(r, [first, warming])
    assert "Split:" in r.counts()["warm"]


@pytest.mark.parametrize("ending", ["done", "timeout", "garbage"])
def test_preemption_at_settlement_wins_over_every_other_ending(runner, ending):
    mode = {"done": "ok", "timeout": "hang", "garbage": "garbage"}[ending]
    overrides = {"control_timeout_s": 1.0} if ending == "timeout" else {}
    r, log = runner(**overrides)
    wait_all(r, [r.submit(task("w:1:f"))])                       # Ascent is warm
    gate = threading.Event()

    def at_settlement(job):
        # the preempt lands after the child has ended, before settlement; idle goes false first so it can't rerun
        if job.key == "p:1:f" and not gate.is_set():
            r.idle = lambda: False
            r.preempt()
            gate.set()

    r._before_settle = at_settlement
    keep = r.submit(task("k:1:f", "Split", mode="sleep:8"))      # started first, on the warm timeout: it stays
    wait_until(lambda: r.counts()["running"] == 1)
    job = r.submit(task("p:1:f", mode=mode))
    assert gate.wait(30)
    wait_until(lambda: r.get(job.id).status == "queued")
    assert r.get(job.id).error is None and r.get(job.id).task
    assert r.pending == [job.id] and r.counts()["preempted"] == 1
    assert r.get(keep.id).status == "running" and "Ascent:" in r.counts()["warm"], "warmth unchanged"


def test_a_failing_kill_or_seam_never_loses_the_slot(runner, monkeypatch):
    r, log = runner()

    def boom(job):
        raise RuntimeError("seam")
    r._before_settle = boom
    job = r.submit(task("s:1:f"))
    wait_all(r, [job])
    assert r.get(job.id).status == "done" and r.counts()["running"] == 0


def test_the_worker_wires_its_parse_hook_to_control(monkeypatch):
    made = {}

    class Fake:
        server_address = ("x", 1)

        def serve_forever(self):
            pass
    monkeypatch.setattr(server, "make_server", lambda worker, host, port, control: made.update(
        worker=worker, control=control) or Fake())
    monkeypatch.setattr(server.Settings, "from_env", classmethod(lambda cls, env=None: cls(control_cmd=["true"])))
    server.main()
    assert made["worker"].on_parse == made["control"].preempt
    assert made["control"].idle == made["worker"].idle


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
    assert health["enabled"] is True and "Ascent:" in health["warm"]


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


def test_the_image_builds_a_control_venv_with_the_web_apps_pins():
    """No Docker here (the first build is Render's), so the Dockerfile is checked as text."""
    import re

    dockerfile = (REPO / "replay_worker" / "Dockerfile").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^(numpy|scipy|Pillow)==(\S+)$", (REPO / "webapp" / "requirements.txt")
                           .read_text(encoding="utf-8"), re.M))
    assert set(pins) == {"numpy", "scipy", "Pillow"}
    install = re.search(r"/opt/control-venv/bin/pip install ([^\n]+)", dockerfile).group(1)
    assert "--only-binary=:all:" in install
    assert sorted(re.findall(r"(numpy|scipy|Pillow)==(\S+)", install)) == sorted(pins.items())
    assert "python3-venv" in dockerfile and "COPY webapp/app/control /srv/webapp/app/control" in dockerfile
    assert "COPY webapp/app/gaps /srv/webapp/app/gaps" in dockerfile
    smoke = re.search(r'/opt/control-venv/bin/python -c "import ([^"]+)"', dockerfile).group(1)
    assert {"app.gaps.detect", "app.gaps.cache", "app.gaps.rows", "app.gaps.backshots"} <= set(smoke.split(", "))
    assert re.search(r'RUN PYTHONPATH=\S+ /opt/control-venv/bin/python -c "import numpy, scipy, PIL, app.control.engine',
                     dockerfile)
    assert "REPLAY_CONTROL_CMD='[\"/opt/control-venv/bin/python\", \"-m\", \"replay_worker.control_job\"]'" in dockerfile
    assert "CONTROL_CACHE_DIR=/jobs/control_cache" in dockerfile and "chown -R worker /jobs" in dockerfile
    # the server itself still runs on the system python3, without the venv
    assert dockerfile.rstrip().endswith('CMD ["python3", "-m", "replay_worker.server"]')


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


def _gaps_task(monkeypatch, tmp_path, key="e" * 16):
    """A round on the toy map with a gaps block carrying the site's keys; the tick cache goes under tmp_path."""
    from control_toys import blob, open_hall

    from app.control import geometry
    from app.control import task as control_task
    from app.replays import format as fmt

    monkeypatch.setattr(geometry, "cache_dir", lambda: tmp_path)
    geo = open_hall()
    monkeypatch.setitem(control_task._GEOMETRY, geo.name, geo)
    (tmp_path / "gaps").mkdir(exist_ok=True)
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}, t_end=3.0)
    return {"key": "k", "map": geo.name, "blob": base64.b64encode(fmt.encode_blob(data)).decode(),
            "link": {"sides": {"0": "attack", "5": "defense"}, "db_deaths": []},
            "gaps": {"replay_id": 1, "round": 1, "fingerprint": "c" * 16, "gap_fingerprint": "9" * 16,
                     "engine_key": key}}


def test_the_child_returns_its_figures_and_keeps_no_tick_cache(tmp_path, monkeypatch):
    import os

    from app.replays import control_format as cf

    t = _gaps_task(monkeypatch, tmp_path)
    gaps_dir = tmp_path / "gaps"
    old = gaps_dir / "9-r9-dead.ticks.pkl.gz"                    # a killed child's leftover
    old.write_bytes(b"x")
    stale = time.time() - control_job.STALE_CACHE_S - 60
    os.utime(old, (stale, stale))
    recent = gaps_dir / "8-r8-live.ticks.pkl.gz"                 # another child's file, being written now
    recent.write_bytes(b"x")
    result = control_job.run(t)
    assert result["status"] == "ok" and result["figures"] == cf.figures_hash()
    assert result["gaps"]["run"]["status"] == "ok", result["gaps"]["run"].get("error")
    assert result["gaps"]["run"]["fingerprint"] == "9" * 16
    assert sorted(p.name for p in gaps_dir.iterdir()) == [recent.name], "its own file and the old one are gone"
    json.dumps(result)                                           # the whole answer is JSON


def test_two_children_on_one_round_never_delete_each_others_tick_cache(tmp_path, monkeypatch):
    # Two task keys for one replay and round can be live at once (a deploy or a relink between them), and
    # both carry the same engine key: each child works under its own suffix and removes only its own names.
    from app.control import task as control_task

    t = _gaps_task(monkeypatch, tmp_path)
    gaps_dir = tmp_path / "gaps"
    prefix = f"1-r1-{'e' * 16}"
    other = gaps_dir / f"{prefix}-{'0' * 32}.ticks.pkl.gz"       # the other child's file and its half-written one
    other_tmp = gaps_dir / (other.name + ".tmp")
    other.write_bytes(b"x")
    other_tmp.write_bytes(b"x")
    seen = []
    real = control_task.compute_task

    def spy(task):
        result = real(task)
        seen.append((task["gaps"]["engine_key"], sorted(p.name for p in gaps_dir.iterdir())))
        return result

    monkeypatch.setattr(control_task, "compute_task", spy)
    result = control_job.run(t)
    assert result["gaps"]["run"]["status"] == "ok", result["gaps"]["run"].get("error")
    [(used, during)] = seen
    assert used.startswith("e" * 16 + "-") and used != "e" * 16 + "-" + "0" * 32, "its own suffix"
    assert f"1-r1-{used}.ticks.pkl.gz" in during and other.name in during
    assert sorted(p.name for p in gaps_dir.iterdir()) == sorted([other.name, other_tmp.name])
    assert t["gaps"]["engine_key"] == "e" * 16, "the caller's task is not rewritten"


def test_a_task_without_gaps_touches_no_tick_cache(tmp_path, monkeypatch):
    t = _gaps_task(monkeypatch, tmp_path)
    del t["gaps"]
    kept = tmp_path / "gaps" / "8-r8-live.ticks.pkl.gz"
    kept.write_bytes(b"x")
    assert control_job.run(t)["status"] == "ok"
    assert [p.name for p in (tmp_path / "gaps").iterdir()] == [kept.name]


def test_health_advertises_the_gaps_protocol_beside_the_switch(tmp_path, runner):
    # What the web app reads before it sends a gaps block (app/services/replay_control_remote.py).
    assert server.GAPS_PROTOCOL == 1
    on, _ = runner()
    control = http(serve(tmp_path, on), "/health")[1]["control"]
    assert control["enabled"] is True and control["gaps_protocol"] == 1
    off, _ = runner(control_enabled=False)
    control = http(serve(tmp_path, off), "/health")[1]["control"]
    assert control["enabled"] is False and control["gaps_protocol"] == 1, "the switch and the protocol are separate"


# ---------------------------------------------------------------- heights by digest
# (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1)


def height_task(key, digest, map_name="Ascent", mode="ok"):
    return {**task(key, map_name, mode), "height": digest}


def test_a_pushed_asset_is_kept_and_a_round_naming_a_missing_one_is_asked_for(tmp_path, runner):
    r, _ = runner(control_cache_dir=tmp_path / "cache")
    base = serve(tmp_path, r)
    code, body = http(base, "/control", height_task("h:1:f", "a" * 12))
    assert code == 409 and body["height"] == "a" * 12, "the web app pushes it and asks again"
    assert http(base, "/heights", {"map": "Ascent", "digest": "a" * 12, "asset": "bnB6"})[0] == 200
    assert (tmp_path / "cache" / "heights" / f"Ascent.{'a' * 12}.height.npz").read_bytes() == b"npz"
    code, body = http(base, "/control", height_task("h:1:f", "a" * 12))
    assert code == 202
    for bad in ({"map": "../x", "digest": "a" * 12, "asset": "bnB6"}, {"map": "Ascent", "digest": "nope", "asset": "bnB6"},
                {"map": "Ascent", "digest": "a" * 12, "asset": "***"}, {"map": "Ascent"}):
        assert http(base, "/heights", bad)[0] == 400, bad


def test_only_a_few_assets_per_map_are_kept(tmp_path, runner):
    r, _ = runner(control_cache_dir=tmp_path / "cache")
    base = serve(tmp_path, r)
    for i in range(server.HEIGHTS_KEPT + 2):
        assert http(base, "/heights", {"map": "Ascent", "digest": f"{i:012x}", "asset": "bnB6"})[0] == 200
        time.sleep(0.02)
    kept = sorted(p.name for p in (tmp_path / "cache" / "heights").iterdir())
    assert len(kept) == server.HEIGHTS_KEPT and f"Ascent.{server.HEIGHTS_KEPT + 1:012x}.height.npz" in kept


def test_a_new_height_digest_makes_the_map_cold_again(tmp_path, runner):
    # The visibility cache is per heights: the first round under a new digest runs alone, as a map's first does.
    cache = tmp_path / "cache" / "heights"
    cache.mkdir(parents=True)
    for digest in ("a" * 12, "b" * 12):
        (cache / f"Ascent.{digest}.height.npz").write_bytes(b"npz")
    r, log = runner(control_cache_dir=tmp_path / "cache")
    first = r.submit(height_task("w:1:f", "a" * 12))
    wait_all(r, [first])
    assert r.counts()["warm"] == [f"Ascent:{'a' * 12}"]
    jobs = [r.submit(height_task(f"w:{n}:g", "b" * 12, mode="sleep:0.4")) for n in (2, 3, 4)]
    wait_all(r, jobs)
    most, events = overlaps(log)
    starts = [t for t, kind, _ in events if kind == "start"][1:]
    ends = [t for t, kind, _ in events if kind == "end"][1:]
    assert starts[1] >= ends[0], "the first round under the new heights finished before another started"

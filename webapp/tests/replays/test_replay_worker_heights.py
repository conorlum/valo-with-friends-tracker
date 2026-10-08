"""Height builds on the replay worker (replay_worker/server.py HeightBuilds;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 2 and 3), with stub children: the
five states and what each allows, uploads that are immutable and counted once, the caps and the expiry, a build
that runs alone and ahead of control rounds, a parse preempting it, and the HTTP answers. Localhost only; no DB,
no engine."""

import base64
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

from replay_worker import server  # noqa: E402
from test_replay_worker_control import STUB, http, task, wait_all, wait_until  # noqa: E402

BUILD_STUB = """
import json, os, sys, time
task = json.loads(sys.stdin.buffer.read())
files = sorted(os.path.relpath(os.path.join(d, f), task["dir"]).replace(os.sep, "/")
               for d, _, fs in os.walk(task["dir"]) for f in fs)
open(os.path.join({log!r}, f"{{time.time():.6f}}_build_{{task['map']}}_{{os.getpid()}}"), "w").close()
time.sleep({sleep})
if {fail!r}:
    print("not json")
else:
    print(json.dumps({{"status": "ok", "map": task["map"], "digest": "d" * 12, "asset": "bnB6", "rounds": len(files),
                      "report": {{"files": files, "previous": task.get("previous")}}, "rules": {{"version": 2}},
                      "inputs_sha": task["manifest"]["sha"], "key": task["key"]}}))
"""
UUID = "0f452716-1e90-4782-afba-29229fdab922"
MANIFEST = {"sha": "m" * 16}


@pytest.fixture
def worker(tmp_path):
    log = tmp_path / "events"
    log.mkdir()
    control_stub = tmp_path / "stub_control.py"
    control_stub.write_text(STUB.format(log=str(log)), encoding="utf-8")

    def make(sleep=0.0, fail=False, **overrides):
        build_stub = tmp_path / f"stub_build_{sleep}_{fail}.py"
        build_stub.write_text(BUILD_STUB.format(log=str(log), sleep=sleep, fail=fail), encoding="utf-8")
        settings = server.Settings(temp_root=tmp_path, control_cmd=[sys.executable, str(control_stub)],
                                   height_cmd=[sys.executable, str(build_stub)], **overrides)
        control = server.ControlRunner(settings)
        return control, server.HeightBuilds(settings, control), log
    return make


def rounds(*numbers, match=UUID, text="blob"):
    return [{"match": match, "n": n, "blob": base64.b64encode(f"{text}{n}".encode()).decode()} for n in numbers]


def finished(builds, build_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        got = builds.get(build_id)
        if got["status"] in ("done", "failed"):
            return got
        time.sleep(0.05)
    raise AssertionError("the build didn't finish")


def spool(control) -> Path:
    return control.settings.temp_root / "height_builds"


def test_rounds_are_spooled_and_the_build_reads_them(worker):
    control, builds, _ = worker()
    job = builds.open("heights:Sunset:abc", "Sunset", 3, MANIFEST)
    assert job["status"] == "collecting" and builds.open("heights:Sunset:abc", "Sunset", 3, MANIFEST)["id"] == job["id"]
    assert builds.add(job["id"], rounds(1, 2))["received"] == 2
    with pytest.raises(server.HeightConflict) as early:
        builds.start(job["id"], None)
    assert early.value.reason == "rounds are missing" and early.value.facts == {"received": 2, "expected": 3}
    builds.add(job["id"], rounds(3))
    assert builds.start(job["id"], "a" * 12)["status"] in ("queued", "running")
    got = finished(builds, job["id"])
    assert got["status"] == "done" and got["result"]["report"]["previous"] == "a" * 12
    assert got["result"]["report"]["files"] == [f"{UUID}/{n}.json.gz" for n in (1, 2, 3)]
    assert got["result"]["inputs_sha"] == MANIFEST["sha"], "the child was given the manifest"
    builds.sweep()
    assert not any(spool(control).iterdir()), "the spool goes when the build ends; its answer stays"
    assert builds.get(job["id"])["status"] == "done"


def test_an_upload_is_immutable_and_counted_once(worker):
    control, builds, _ = worker()
    job = builds.open("k", "Sunset", 2, MANIFEST)
    assert builds.add(job["id"], rounds(1, 2))["received"] == 2
    used = builds.status()["spool_bytes"]
    for _ in range(3):                                   # the same batch again, as after a lost acknowledgement
        assert builds.add(job["id"], rounds(1, 2))["received"] == 2
    assert builds.status()["spool_bytes"] == used, "an unchanged round is acknowledged and not charged again"
    with pytest.raises(server.HeightConflict) as other:
        builds.add(job["id"], rounds(2, text="other"))
    assert other.value.reason == "other bytes" and other.value.facts == {"match": UUID, "n": 2}
    assert builds.status()["spool_bytes"] == used
    assert (spool(control) / job["id"] / UUID / "2.json.gz").read_bytes() == b"blob2", "the first bytes stay"
    with pytest.raises(ValueError):
        builds.add(job["id"], rounds(3)), "more rounds than the build was opened with"


def test_bad_rounds_are_refused_and_never_written(worker):
    control, builds, _ = worker()
    job = builds.open("k", "Sunset", 1, MANIFEST)
    for bad in ([{"match": "../x", "n": 1, "blob": "YQ=="}], [{"match": UUID, "n": "1; rm", "blob": "YQ=="}],
                [{"match": UUID, "n": 1, "blob": "***"}], [{"match": UUID}], [{"match": UUID, "n": 0, "blob": "YQ=="}]):
        with pytest.raises(ValueError):
            builds.add(job["id"], bad)
    assert builds.get(job["id"])["received"] == 0 and not list((spool(control) / job["id"]).iterdir())
    for key, name, count, manifest in (("k2", "../Sunset", 1, MANIFEST), ("k3", "Sunset", 0, MANIFEST),
                                       ("k4", "Sunset", 1, "not a manifest")):
        with pytest.raises(ValueError):
            builds.open(key, name, count, manifest)
    assert len(list(spool(control).iterdir())) == 1, "a refused open leaves no folder behind"


def test_each_state_allows_only_what_it_should(worker):
    control, builds, _ = worker(sleep=0.8)
    job = builds.open("k", "Sunset", 1, MANIFEST)
    builds.add(job["id"], rounds(1))
    builds.start(job["id"], None)
    wait_until(lambda: builds.get(job["id"])["status"] == "running")
    for attempt in (lambda: builds.add(job["id"], rounds(1)), lambda: builds.cancel(job["id"])):
        with pytest.raises(server.HeightConflict) as refused:
            attempt()
        assert refused.value.reason == "not collecting" and refused.value.facts["status"] == "running"
    assert builds.start(job["id"], None)["status"] == "running", "starting again answers the state: no conflict"
    assert builds.open("k", "Sunset", 1, MANIFEST)["status"] == "running", "and so does opening it again"
    finished(builds, job["id"])
    assert builds.open("k", "Sunset", 1, MANIFEST)["status"] == "done", "a finished build is answered again"
    with pytest.raises(LookupError):
        builds.add("nope", rounds(1))


def test_a_failed_build_is_opened_afresh(worker):
    control, builds, _ = worker(fail=True)
    failed = builds.open("k-fail", "Haven", 1, MANIFEST)
    builds.add(failed["id"], rounds(1))
    builds.start(failed["id"], None)
    assert finished(builds, failed["id"])["status"] == "failed"
    again = builds.open("k-fail", "Haven", 1, MANIFEST)
    assert again["id"] != failed["id"] and again["status"] == "collecting" and again["received"] == 0


def test_the_spool_and_the_collectors_are_bounded_for_all_builds_together(worker):
    control, builds, _ = worker(height_spool_bytes=12)
    a = builds.open("ka", "Sunset", 2, MANIFEST)
    b = builds.open("kb", "Haven", 2, MANIFEST)
    builds.add(a["id"], rounds(1))                        # 5 bytes
    builds.add(b["id"], rounds(1))                        # 5 more: 10 of 12
    with pytest.raises(OverflowError):
        builds.add(a["id"], rounds(2))
    assert builds.get(a["id"])["received"] == 1 and builds.status()["spool_bytes"] == 10
    with pytest.raises(OverflowError):
        builds.open("kc", "Lotus", 1, MANIFEST)           # HEIGHT_COLLECTING_MAX collectors already
    assert builds.cancel(a["id"]) is True and builds.get(a["id"]) is None
    assert builds.status()["spool_bytes"] == 5 and not (spool(control) / a["id"]).exists()
    assert builds.open("kc", "Lotus", 1, MANIFEST)["status"] == "collecting"
    other = builds.open("kd", "Haven", 2, MANIFEST)       # new inputs for a map still collecting: the old one goes
    assert builds.get(b["id"]) is None and other["id"] != b["id"] and builds.status()["spool_bytes"] == 0


def test_a_collector_nobody_feeds_expires_and_only_the_newest_answers_are_kept(worker):
    control, builds, _ = worker(height_collect_ttl_s=100)
    idle = builds.open("idle", "Sunset", 2, MANIFEST)
    builds.add(idle["id"], rounds(1))
    builds.sweep(now=time.time() + 50)
    assert builds.get(idle["id"])["status"] == "collecting"
    builds.sweep(now=time.time() + 101)
    assert builds.get(idle["id"]) is None and not (spool(control) / idle["id"]).exists()
    done = []
    for i in range(server.HEIGHT_BUILDS_KEPT + 2):
        job = builds.open(f"k{i}", "Sunset", 1, MANIFEST)
        builds.add(job["id"], rounds(1))
        builds.start(job["id"], None)
        finished(builds, job["id"])
        done.append(job["id"])
        builds.sweep()
    assert [builds.get(i) is not None for i in done] == [False, False] + [True] * server.HEIGHT_BUILDS_KEPT


def test_a_build_runs_alone_and_ahead_of_queued_rounds(worker):
    control, builds, log = worker(sleep=0.6)
    control.idle = lambda: False                       # nothing starts yet
    waiting = [control.submit(task(f"r:{n}:f", "Ascent", "sleep:0.3")) for n in (1, 2)]
    job = builds.open("k", "Sunset", 1, MANIFEST)
    builds.add(job["id"], rounds(1))
    builds.start(job["id"], None)
    control.idle = lambda: True
    control.wake.set()
    finished(builds, job["id"])
    wait_all(control, waiting)
    events = sorted(p.name.split("_")[:2] for p in log.iterdir())
    assert events[0][1] == "build", "the rebuild starts before any queued round"
    build_end = float(events[0][0]) + 0.6
    assert all(float(t) >= build_end - 0.05 for t, kind in events[1:] if kind == "start"), "and runs alone"


def test_a_parse_preempts_a_build_and_it_starts_again_with_its_rounds(worker):
    control, builds, log = worker(sleep=1.0)
    job = builds.open("k", "Sunset", 1, MANIFEST)
    builds.add(job["id"], rounds(1))
    builds.start(job["id"], None)
    wait_until(lambda: builds.get(job["id"])["status"] == "running")
    time.sleep(0.5)                                    # the child has started and logged; it sleeps a second
    assert control.preempt() == 1
    wait_until(lambda: builds.get(job["id"])["status"] in ("queued", "running", "done"))
    got = finished(builds, job["id"])
    assert got["status"] == "done" and control.counts()["preempted"] == 1
    assert sum(1 for p in log.iterdir() if "_build_" in p.name) == 2, "killed once, then run to the end"


def serve(control, builds):
    httpd = server.make_server(server.Worker(control.settings), control=control, heights=builds)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def test_the_http_answers_each_state_and_health(worker):
    control, builds, _ = worker(sleep=0.5)
    base = serve(control, builds)
    code, job = http(base, "/heights/build", {"key": "k", "map": "Sunset", "rounds": 2, "manifest": MANIFEST})
    assert code == 202 and job["status"] == "collecting" and (job["received"], job["expected"]) == (0, 2)
    valid = {"key": "other", "map": "Sunset", "rounds": 2, "manifest": MANIFEST}
    for missing in ("key", "map", "rounds", "manifest"):
        assert http(base, "/heights/build", {k: v for k, v in valid.items() if k != missing})[0] == 400, missing
    assert http(base, "/heights/build/unknown/start", {})[0] == 404
    path = f"/heights/build/{job['id']}"
    assert http(base, f"{path}/rounds", {"rounds": rounds(1)}) == (200, {"received": 1})
    health = http(base, "/health")[1]
    assert health["heights"]["collecting"] == ["Sunset"] and health["heights"]["spool_bytes"] == 5 and health["idle"] is True
    code, body = http(base, f"{path}/start", {})
    assert code == 409 and body == {"error": "rounds are missing", "received": 1, "expected": 2}
    assert http(base, f"{path}/rounds", {"rounds": [{"match": "x"}]})[0] == 400
    code, body = http(base, f"{path}/rounds", {"rounds": rounds(1, text="other")})
    assert code == 409 and body == {"error": "other bytes", "match": UUID, "n": 1}
    http(base, f"{path}/rounds", {"rounds": rounds(2)})
    assert http(base, f"{path}/start", {})[0] == 202
    code, body = http(base, f"{path}/rounds", {"rounds": rounds(2)})
    assert code == 409 and body["error"] == "not collecting" and body["status"] in ("queued", "running")
    assert http(base, f"{path}/start", {})[0] == 202, "asking to start a started build is not an error"
    code, again = http(base, "/heights/build", {"key": "k", "map": "Sunset", "rounds": 2, "manifest": MANIFEST})
    assert code == 202 and again["id"] == job["id"] and again["status"] in ("queued", "running")
    assert http(base, f"{path}/cancel", {})[0] == 409
    finished(builds, job["id"])
    code, got = http(base, path)
    assert code == 200 and got["status"] == "done" and got["result"]["digest"] == "d" * 12
    for missing in ("/heights/build/nope", "/heights/build/nope/start", "/heights/build/nope/rounds", "/heights/build/nope/cancel"):
        assert http(base, missing, None if missing.endswith("nope") else {"rounds": []})[0] == 404, missing
    code, job = http(base, "/heights/build", {"key": "k2", "map": "Haven", "rounds": 1, "manifest": MANIFEST})
    assert http(base, f"/heights/build/{job['id']}/cancel", {}) == (200, {"cancelled": True})
    assert http(base, f"/heights/build/{job['id']}")[0] == 404

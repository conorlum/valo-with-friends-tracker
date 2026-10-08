"""The upload worker (replay_worker/server.py) with a stub parser command: the queue, the
timeout, temp cleanup, overflow and the limits. Localhost only; no DB, no Docker."""

import base64
import json
import os
import subprocess
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

from replay_synthetic import MATCH_UUID, SyntheticMatch  # noqa: E402

from app.replays.condense import condense_export_dir  # noqa: E402
from replay_worker import server  # noqa: E402

STUB = """
import json, os, sys, time
sys.path.insert(0, {tests!r})
from replay_synthetic import SyntheticMatch
vrf, out, mode = sys.argv[1], sys.argv[2], sys.argv[3]
open(os.path.join(os.path.dirname(vrf), "stub.pid"), "w").write(str(os.getpid()))
if mode == "hang":
    time.sleep(120)
if mode == "fail":
    sys.exit(1)
data = open(vrf, "rb").read()
match = SyntheticMatch(shape="swiftplay", vrf_bytes=data)
if mode == "wrong-hash":
    match.manifest_extra = {{"source_sha256": "0" * 64}}
if mode == "slow":
    time.sleep(3)
match.write(__import__("pathlib").Path(out))
"""


@pytest.fixture
def stub(tmp_path):
    path = tmp_path / "stub_parser.py"
    path.write_text(STUB.format(tests=str(HERE)), encoding="utf-8")
    return path


def start(tmp_path, stub, mode="ok", **overrides):
    settings = server.Settings(parser_cmd=[sys.executable, str(stub), "{vrf}", "{out}", mode],
                               temp_root=tmp_path / "jobs", **overrides)
    settings.temp_root.mkdir(exist_ok=True)
    worker = server.Worker(settings)
    httpd = server.make_server(worker)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    return worker, httpd, base


def post(base, body: bytes):
    request = urllib.request.Request(f"{base}/jobs", data=body, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def get(base, path):
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def wait(base, job_id, until=("done", "failed"), seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        _, body = get(base, f"/jobs/{job_id}")
        if body["status"] in until:
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} still {body['status']}")


def vrf_bytes() -> bytes:
    return SyntheticMatch(shape="swiftplay").vrf_bytes


def alive(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in listing
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_a_job_returns_the_same_blobs_as_the_local_path_and_leaves_no_temp_files(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub)
    try:
        status, body = post(base, vrf_bytes())
        assert status == 202 and body["status"] == "queued"
        done = wait(base, body["id"])
        assert done["status"] == "done", done
        result = done["result"]
        assert result["match_uuid"] == MATCH_UUID and result["round_count"] == 3
        # The local path on the same export gives byte-identical blobs (same code, same recipe).
        match = SyntheticMatch(shape="swiftplay")
        local_dir = match.write(tmp_path / "local")
        local = condense_export_dir(local_dir, source_sha256=match.source_sha256,
                                    vrf_path=match.write_vrf(tmp_path / f"{MATCH_UUID}.vrf"))
        assert {n: base64.b64decode(b) for n, b in result["rounds"].items()} == \
            {str(n): data for n, data in local.encoded_rounds().items()}
        assert result["players"] == local.players and result["recipe"] == local.recipe
        assert list((tmp_path / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


def test_the_match_uuid_comes_from_the_header_not_a_name(tmp_path, stub):
    # The upload has no name at all; the worker never reads one.
    worker, httpd, base = start(tmp_path, stub)
    try:
        done = wait(base, post(base, vrf_bytes())[1]["id"])
        assert done["result"]["match_uuid"] == MATCH_UUID
    finally:
        httpd.shutdown()


def test_the_timeout_kills_a_hung_parse_and_cleans_up(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub, mode="hang", timeout_s=2.0)
    try:
        body = post(base, vrf_bytes())[1]
        pid_file = tmp_path / "jobs" / f"replay-job-{body['id']}" / "stub.pid"
        deadline = time.time() + 30
        pid = None
        while time.time() < deadline and pid is None:
            if pid_file.exists() and pid_file.read_text():
                pid = int(pid_file.read_text())
            time.sleep(0.05)
        done = wait(base, body["id"], seconds=60)
        assert (done["status"], done["error"]) == ("failed", server.REASON_TIMEOUT)
        assert pid is not None and not alive(pid)
        assert list((tmp_path / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("mode, reason", [("fail", server.REASON_PARSE),
                                          ("wrong-hash", "not condensable: source_sha256")])
def test_a_failed_job_says_why_and_cleans_up(tmp_path, stub, mode, reason):
    worker, httpd, base = start(tmp_path, stub, mode=mode)
    try:
        done = wait(base, post(base, vrf_bytes())[1]["id"])
        assert (done["status"], done["error"]) == ("failed", reason)
        assert list((tmp_path / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


def test_a_full_queue_answers_503(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub, mode="slow", queue_size=1)
    try:
        first = post(base, vrf_bytes())[1]
        wait(base, first["id"], until=("parsing",))
        second = post(base, vrf_bytes())
        third = post(base, vrf_bytes())
        assert second[0] == 202 and third[0] == 503
        assert wait(base, first["id"])["status"] == "done" and wait(base, second[1]["id"])["status"] == "done"
        assert list((tmp_path / "jobs").iterdir()) == [], "the refused upload left nothing behind"
    finally:
        httpd.shutdown()


def test_limits_and_errors(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub, max_bytes=1000)
    try:
        # The worker answers 413 without reading an over-cap body (it may be gigabytes), so the
        # client can see the connection closed before it reads that answer (a Windows socket race).
        try:
            assert post(base, b"\xdd\xef\xf4\x43" + b"x" * 2000)[0] == 413
        except (ConnectionAbortedError, ConnectionResetError, urllib.error.URLError) as error:
            assert "10053" in str(error) or "10054" in str(error) or "reset" in str(error).lower(), error
        assert post(base, b"not a replay at all")[0] == 400
        assert get(base, "/jobs/nope")[0] == 404
        status, health = get(base, "/health")
        assert status == 200 and health["limits"]["max_bytes"] == 1000
        assert list((tmp_path / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


def test_the_worker_says_when_it_is_idle_and_calls_the_parse_hook(tmp_path, stub):
    calls = []
    settings = server.Settings(parser_cmd=[sys.executable, str(stub), "{vrf}", "{out}", "slow"],
                               temp_root=tmp_path / "jobs")
    settings.temp_root.mkdir(exist_ok=True)
    worker = server.Worker(settings, on_parse=lambda: calls.append(time.time()))
    assert worker.idle()
    job = worker.submit(vrf_bytes())
    assert calls, "accepting an upload calls the hook at once"
    assert not worker.idle()
    end = time.time() + 60
    while worker.get(job.id).status not in ("done", "failed") and time.time() < end:
        time.sleep(0.05)
    assert worker.get(job.id).status == "done"
    end = time.time() + 5                    # `parsing` clears a few statements after the status reads done
    while not worker.idle() and time.time() < end:
        time.sleep(0.02)
    assert worker.idle() and len(calls) >= 2, "a parse starting calls it again"


def test_a_failing_parse_hook_never_stops_a_parse(tmp_path, stub):
    def boom():
        raise RuntimeError("the kill failed")

    settings = server.Settings(parser_cmd=[sys.executable, str(stub), "{vrf}", "{out}", "ok"],
                               temp_root=tmp_path / "jobs")
    settings.temp_root.mkdir(exist_ok=True)
    worker = server.Worker(settings, on_parse=boom)
    job = worker.submit(vrf_bytes())
    end = time.time() + 60
    while worker.get(job.id).status not in ("done", "failed") and time.time() < end:
        time.sleep(0.05)
    assert worker.get(job.id).status == "done"


def test_health_names_the_recipe_the_condenser_stamps(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub)
    try:
        health = get(base, "/health")[1]
        done = wait(base, post(base, vrf_bytes())[1]["id"])
        assert done["status"] == "done", done
        assert health["recipe"] and health["recipe"] == done["result"]["recipe"]
        assert health["control"]["gaps_protocol"] == 1
    finally:
        httpd.shutdown()


def test_a_recipe_that_cannot_be_computed_is_null_and_parsing_still_works(tmp_path, stub, monkeypatch):
    def boom():
        raise OSError("the pin file is missing")

    monkeypatch.setattr(server, "current_recipe", boom)
    worker, httpd, base = start(tmp_path, stub)
    try:
        assert worker.recipe is None and get(base, "/health")[1]["recipe"] is None
        assert wait(base, post(base, vrf_bytes())[1]["id"])["status"] == "done"
    finally:
        httpd.shutdown()


def test_settings_from_the_environment():
    settings = server.Settings.from_env({"REPLAY_PARSER_CMD": '["x", "{vrf}"]', "REPLAY_TIMEOUT_S": "9",
                                         "REPLAY_QUEUE_SIZE": "2", "REPLAY_MAX_BYTES": "5"})
    assert settings.parser_cmd == ["x", "{vrf}"] and settings.timeout_s == 9 and settings.queue_size == 2
    assert settings.max_bytes == 5 and server.Settings.from_env({}).timeout_s == 180

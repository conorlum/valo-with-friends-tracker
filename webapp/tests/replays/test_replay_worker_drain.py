"""The worker's parse hold (replay_worker/server.py: hold/release/holding, the admission check, the hold file),
its upload counter and the `drain` block of /health. Same stub parser and helpers as test_replay_worker.py.
Localhost only; a temp folder stands in for the disk."""

import hashlib
import http.client
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_worker import get, post, stub, vrf_bytes, wait  # noqa: E402,F401  (stub is a fixture)

from replay_worker import archive as arc  # noqa: E402
from replay_worker import server  # noqa: E402

FIXTURES = HERE.parents[0] / "fixtures" / "deploy"


def send(base, path, body):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{base}{path}", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def make_settings(tmp_path, stub, mode="ok", archive=False, **overrides):
    extra = {}
    if archive:
        disk = tmp_path / "disk"
        disk.mkdir(exist_ok=True)
        extra = dict(archive_dir=disk, archive_require_mount=False, archive_total_bytes=500 * arc.GB)
    settings = server.Settings(parser_cmd=[sys.executable, str(stub), "{vrf}", "{out}", mode],
                               temp_root=tmp_path / "jobs", **extra, **overrides)
    settings.temp_root.mkdir(exist_ok=True)
    return settings


def run(settings, on_parse=None):
    worker = server.Worker(settings, on_parse=on_parse)
    httpd = server.make_server(worker)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return worker, httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(params=["archive-off", "archive-on"])
def live(request, tmp_path, stub):  # noqa: F811
    settings = make_settings(tmp_path, stub, archive=request.param == "archive-on")
    worker, httpd, base = run(settings)
    try:
        yield worker, base, settings
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def plain(tmp_path, stub):  # noqa: F811
    settings = make_settings(tmp_path, stub)
    worker, httpd, base = run(settings)
    try:
        yield worker, base, settings
    finally:
        httpd.shutdown()
        httpd.server_close()


def health(base):
    status, body = get(base, "/health")
    assert status == 200
    return body


def hold_file(settings):
    root = settings.archive_dir if settings.archive_dir is not None else settings.temp_root
    return root / "drain-hold.json"


def test_a_hold_keeps_an_upload_queued_until_released(live):
    worker, base, _ = live
    assert worker.hold(1, time.time() + 60)
    status, body = post(base, vrf_bytes())
    assert status == 202 and body["status"] == "queued"
    time.sleep(2)
    assert get(base, f"/jobs/{body['id']}")[1]["status"] == "queued"
    assert send(base, "/drain/release", {"drain_id": 1}) == (200, {"hold": False})
    assert wait(base, body["id"])["status"] == "done"


def test_a_consumer_already_waiting_does_not_take_an_upload_after_the_hold(live):
    worker, base, _ = live
    time.sleep(0.5)          # the parse thread is blocked waiting for work
    worker.hold(1, time.time() + 60)
    first = post(base, vrf_bytes())[1]["id"]
    second = post(base, vrf_bytes())[1]["id"]
    time.sleep(2)
    assert get(base, f"/jobs/{first}")[1]["status"] == "queued"
    assert get(base, f"/jobs/{second}")[1]["status"] == "queued"
    assert health(base)["drain"]["parses"] == {"running": 0, "queued": 2}
    worker.release(1)
    wait(base, first)
    wait(base, second)
    assert worker.get(first).finished <= worker.get(second).finished     # their order held


def test_an_expired_hold_parses_at_once_and_the_file_is_written(plain):
    worker, base, settings = plain
    assert worker.hold(3, time.time() - 5)
    assert json.loads(hold_file(settings).read_text(encoding="utf-8")) == {
        "drain_id": 3, "until": pytest.approx(time.time() - 5, abs=30), "max_drain_id": 3}
    status, body = post(base, vrf_bytes())
    assert status == 202
    assert wait(base, body["id"])["status"] == "done"


def test_a_new_worker_on_the_same_folder_comes_back_holding(tmp_path, stub):  # noqa: F811
    settings = make_settings(tmp_path, stub)
    first, httpd, _ = run(settings)
    try:
        until = time.time() + 60
        first.hold(4, until)
        second = server.Worker(settings)
        assert second.holding(time.time())
        assert (second.hold_drain_id, second.hold_until, second.max_drain_id) == (4, until, 4)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_corrupt_or_missing_file_is_no_hold(tmp_path, stub):  # noqa: F811
    settings = make_settings(tmp_path, stub)
    assert not server.Worker(settings).holding(time.time())
    hold_file(settings).write_text("{not json", encoding="utf-8")
    worker = server.Worker(settings)
    assert not worker.holding(time.time())
    assert worker.hold(1, time.time() + 60)        # and it is usable afterwards


def test_a_reparse_submitted_while_held_stays_queued(tmp_path, stub):  # noqa: F811
    settings = make_settings(tmp_path, stub, archive=True)
    worker, httpd, base = run(settings)
    try:
        body = vrf_bytes()
        sha = hashlib.sha256(body).hexdigest()
        done = wait(base, post(base, body)[1]["id"])
        assert done["status"] == "done"
        ack = {"match_uuid": MATCH_UUID, "sha256": sha, "outcome": "stored", "replay_id": 1,
               "played_at": "2026-09-30T20:00:00Z"}
        assert send(base, f"/jobs/{done['id']}/ack", ack)[0] == 200
        worker.hold(1, time.time() + 60)
        status, queued = send(base, "/reparse", {"match_uuid": MATCH_UUID})
        assert status == 202, queued
        time.sleep(2)
        assert get(base, f"/jobs/{queued['id']}")[1]["status"] == "queued"
        worker.release(1)
        assert wait(base, queued["id"])["status"] == "done"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_release_naming_another_id_is_ignored_and_a_higher_hold_replaces(plain):
    worker, base, _ = plain
    until = time.time() + 60
    assert send(base, "/drain/hold", {"drain_id": 1, "until": until}) == (
        200, {"hold": True, "drain_id": 1, "until": until})
    assert send(base, "/drain/release", {"drain_id": 2}) == (200, {"ignored": True})
    assert worker.holding(time.time()) and worker.hold_drain_id == 1
    later = until + 30
    assert send(base, "/drain/hold", {"drain_id": 2, "until": later})[1]["drain_id"] == 2
    assert worker.hold_drain_id == 2 and worker.hold_until == later
    assert health(base)["drain"]["drain_id"] == 2


def test_reordered_calls_never_touch_a_newer_drain(tmp_path, stub):  # noqa: F811
    settings = make_settings(tmp_path, stub)
    worker, httpd, _ = run(settings)
    try:
        until = time.time() + 60
        assert worker.hold(2, until)
        assert not worker.hold(1, until + 100)       # a delayed older hold
        assert not worker.release(1)                 # a delayed older release
        assert worker.holding(time.time()) and worker.hold_until == until and worker.hold_drain_id == 2
        assert worker.release(2)
        assert not worker.holding(time.time())
        assert not worker.hold(1, until + 100)       # still ignored after the release
        assert not worker.holding(time.time())
        reloaded = server.Worker(settings)           # and after a restart, from the kept file
        assert reloaded.max_drain_id == 2
        assert not reloaded.hold(1, until + 100)
        assert not reloaded.holding(time.time())
        assert json.loads(hold_file(settings).read_text(encoding="utf-8"))["until"] is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_parse_admitted_before_its_status_update_is_counted_as_running(tmp_path, stub):  # noqa: F811
    entered, proceed = threading.Event(), threading.Event()

    def hook():
        if threading.current_thread().name.startswith("replay-worker"):
            entered.set()
            proceed.wait(30)

    worker, httpd, base = run(make_settings(tmp_path, stub), on_parse=hook)
    try:
        job = post(base, vrf_bytes())[1]["id"]
        assert entered.wait(15)
        assert get(base, f"/jobs/{job}")[1]["status"] == "queued"     # not "parsing" yet
        assert health(base)["drain"]["parses"] == {"running": 1, "queued": 0}
        proceed.set()
        assert wait(base, job)["status"] == "done"
        assert health(base)["drain"]["parses"]["running"] == 0
    finally:
        proceed.set()
        httpd.shutdown()
        httpd.server_close()


def test_an_expired_hold_calls_the_release_hooks_once(plain):
    worker, base, _ = plain
    calls = []
    worker.on_release.append(lambda: calls.append(1))
    worker.hold(1, time.time() + 0.5)
    deadline = time.time() + 10
    while not calls and time.time() < deadline:
        time.sleep(0.1)
    time.sleep(2.5)
    assert calls == [1]
    assert not worker.holding(time.time()) and calls == [1]


def test_an_explicit_release_calls_the_hooks_too(plain):
    worker, _, _ = plain
    calls = []
    worker.on_release.append(lambda: calls.append(1))
    worker.hold(1, time.time() + 60)
    worker.release(1)
    assert calls == [1]


def slow_upload(base, first_part, rest, length):
    conn = http.client.HTTPConnection(*base.replace("http://", "").split(":"), timeout=30)
    conn.putrequest("POST", "/jobs")
    conn.putheader("Content-Length", str(length))
    conn.endheaders()
    conn.send(first_part)
    return conn


def test_the_upload_counter_is_one_while_a_slow_upload_streams(plain):
    worker, base, _ = plain
    assert health(base)["drain"]["uploads"] == {"receiving": 0, "last_end_age_s": None}
    body = vrf_bytes()
    conn = slow_upload(base, body[:100], body[100:], len(body))
    try:
        deadline = time.time() + 10
        while health(base)["drain"]["uploads"]["receiving"] != 1 and time.time() < deadline:
            time.sleep(0.05)
        assert health(base)["drain"]["uploads"]["receiving"] == 1
        conn.send(body[100:])
        response = conn.getresponse()
        assert response.status == 202
        response.read()
    finally:
        conn.close()
    uploads = health(base)["drain"]["uploads"]
    assert uploads["receiving"] == 0 and isinstance(uploads["last_end_age_s"], float)


def test_the_upload_counter_comes_back_to_zero_after_a_refused_upload(plain):
    worker, base, _ = plain
    conn = http.client.HTTPConnection(*base.replace("http://", "").split(":"), timeout=30)
    try:
        conn.putrequest("POST", "/jobs")
        conn.putheader("Content-Length", "100")
        conn.endheaders()
        conn.send(b"x" * 100)
        response = conn.getresponse()
        assert response.status == 400
        response.read()
    finally:
        conn.close()
    uploads = health(base)["drain"]["uploads"]
    assert uploads["receiving"] == 0 and isinstance(uploads["last_end_age_s"], float)


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return "list"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "null" if value is None else type(value).__name__


def compare(expected, actual, path=""):
    """Every key the fixture has is there with the same type (null in the fixture matches anything); a dict the
    fixture fills must have the same keys."""
    problems = []
    if expected == "null":
        return problems
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {actual!r}"]
        if expected and set(expected) != set(actual):
            problems.append(f"{path}: keys differ, {sorted(set(expected) ^ set(actual))}")
        for key in set(expected) & set(actual):
            problems += compare(expected[key], actual[key], f"{path}.{key}")
        return problems
    return [] if expected == actual else [f"{path}: expected {expected}, got {actual}"]


def test_health_matches_the_quiet_fixture_in_keys_and_types(plain, monkeypatch):
    worker, base, _ = plain
    quiet = json.loads((FIXTURES / "worker_health_quiet.json").read_text(encoding="utf-8"))
    worker.hold(1, time.time() + 60)            # the fixture is a held worker
    body = health(base)
    # The blocks that depend on the deployment (control children, Workflow runs, the memory cap) differ between
    # this bare test worker and the fixture's; the top-level keys, `commit` and the whole `drain` block do not.
    assert set(body) == set(quiet)
    problems = compare(shape(quiet["drain"]), shape(body["drain"]), ".drain")
    assert not problems, problems
    assert set(body["drain"]["uploads"]) == set(quiet["drain"]["uploads"])
    assert isinstance(body["commit"], (str, type(None)))


def test_commit_is_the_render_git_commit(plain, monkeypatch):
    _, base, _ = plain
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    assert health(base)["commit"] == "b" * 40
    monkeypatch.delenv("RENDER_GIT_COMMIT")
    assert health(base)["commit"] is None


def test_drain_block_when_idle_and_when_held(plain):
    worker, base, _ = plain
    assert health(base)["drain"] == {
        "hold": False, "hold_until": None, "drain_id": None, "starts_blocked": False, "cancelling": 0,
        "uploads": {"receiving": 0, "last_end_age_s": None}, "parses": {"running": 0, "queued": 0},
        "util": {"building": 0}, "runs": {"active": 0}}
    until = time.time() + 60
    worker.hold(7, until)
    drain = health(base)["drain"]
    assert drain["hold"] is True and drain["hold_until"] == until and drain["drain_id"] == 7


@pytest.mark.parametrize("body", [{"until": "x"}, {"drain_id": 1}, {"drain_id": "1", "until": time.time() + 60},
                                  {"drain_id": 1, "until": 1.0}, {"drain_id": True, "until": time.time() + 60},
                                  {"drain_id": 1, "until": None}])
def test_a_bad_hold_body_is_a_400(plain, body):
    worker, base, _ = plain
    assert send(base, "/drain/hold", body)[0] == 400
    assert not worker.holding(time.time())


def test_a_bad_release_body_is_a_400(plain):
    _, base, _ = plain
    assert send(base, "/drain/release", {"drain_id": "x"})[0] == 400

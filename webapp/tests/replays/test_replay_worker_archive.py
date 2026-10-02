"""The worker with its .vrf archive (replay_worker/server.py + archive.py) over HTTP, with the stub parser of
test_replay_worker.py: archive off is today's behaviour, a parsed upload waits for its ack, restart recovery,
the intake reserve, reparse, deletion and tombstones. Localhost only; a temp folder stands in for the disk."""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_worker import get, post, start, stub, vrf_bytes, wait  # noqa: E402,F401  (fixture)

from replay_worker import archive as arc  # noqa: E402
from replay_worker import server  # noqa: E402


def send(base, path, body: dict):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{base}{path}", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def start_archive(tmp_path, stub, total=500 * arc.GB, mode="ok", **overrides):  # noqa: F811
    disk = tmp_path / "disk"
    disk.mkdir(exist_ok=True)
    return start(tmp_path, stub, mode, archive_dir=disk, archive_require_mount=False, archive_total_bytes=total,
                 **overrides) + (disk,)


def ack_body(sha, outcome="stored", replay_id=1, played_at="2026-09-30T20:00:00Z"):
    return {"match_uuid": MATCH_UUID, "sha256": sha, "outcome": outcome, "replay_id": replay_id,
            "played_at": played_at}


def test_with_the_archive_off_the_worker_is_as_before_and_an_ack_is_a_no_op(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        body = vrf_bytes()
        done = wait(base, post(base, body)[1]["id"])
        assert done["status"] == "done"
        assert list((tmp_path / "jobs").iterdir()) == [], "the temp folder is gone"
        status, answer = send(base, f"/jobs/{done['id']}/ack", ack_body(hashlib.sha256(body).hexdigest()))
        assert (status, answer) == (200, {"archived": False, "reason": "archive off"})
        health = get(base, "/health")[1]
        assert health["archive"] == {"enabled": False, "reason": "REPLAY_ARCHIVE_DIR is not set"}
        assert get(base, "/archive")[0] == 404 and send(base, "/reparse", {"match_uuid": MATCH_UUID})[0] == 404
    finally:
        httpd.shutdown()


def test_a_folder_that_is_not_a_mount_keeps_the_archive_off(tmp_path, stub):  # noqa: F811
    disk = tmp_path / "disk"
    disk.mkdir()
    worker, httpd, base = start(tmp_path, stub, archive_dir=disk)  # require_mount stays on
    try:
        assert worker.archive is None and "not a mounted disk" in get(base, "/health")[1]["archive"]["reason"]
        assert wait(base, post(base, vrf_bytes())[1]["id"])["status"] == "done"
        assert list(disk.iterdir()) == []
    finally:
        httpd.shutdown()


def test_a_parsed_upload_waits_for_its_ack_and_is_then_archived(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        body = vrf_bytes()
        sha = hashlib.sha256(body).hexdigest()
        done = wait(base, post(base, body)[1]["id"])
        assert done["status"] == "done", done
        job_dir = disk / "jobs" / done["id"]
        kept = sorted(p.name for p in job_dir.iterdir() if p.name != "stub.pid")  # the stub parser's own file
        assert kept == ["job.json", "result.json"], "no export, no upload"
        assert (disk / "pending" / f"{done['id']}.vrf").read_bytes() == body
        assert not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        # A wrong sha is refused; the right ack archives; the same ack again gives the same answer.
        assert send(base, f"/jobs/{done['id']}/ack", ack_body("0" * 64))[0] == 409
        status, answer = send(base, f"/jobs/{done['id']}/ack", ack_body(sha))
        assert (status, answer) == (200, {"archived": True, "result": "archived"})
        assert send(base, f"/jobs/{done['id']}/ack", ack_body(sha)) == (status, answer)
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == body
        listed = get(base, "/archive")[1]["files"]
        assert [f["match_uuid"] for f in listed] == [MATCH_UUID] and listed[0]["map"]
        assert get(base, "/health")[1]["archive"]["kept"] == 1
        # An ack for a job the worker never had is a final "nothing held".
        assert send(base, "/jobs/feedface/ack", ack_body(sha))[1]["result"] == "nothing held"
    finally:
        httpd.shutdown()


def test_a_kept_existing_ack_deletes_the_pending_file(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        done = wait(base, post(base, vrf_bytes())[1]["id"])
        answer = send(base, f"/jobs/{done['id']}/ack", ack_body(None, "kept_existing", None))[1]
        assert answer["result"] == "deleted" and list((disk / "pending").iterdir()) == []
        assert not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
    finally:
        httpd.shutdown()


def test_a_restart_requeues_unfinished_jobs_and_reloads_finished_ones(tmp_path, stub):  # noqa: F811
    disk = tmp_path / "disk"
    jobs = disk / "jobs"
    body = vrf_bytes()
    now = time.time()
    # Written by hand, as a killed worker would leave them (one live Worker only: Windows file locks).
    for job_id, status, created in (("aaaa1111", "parsing", now - 20), ("bbbb2222", "queued", now - 10)):
        (jobs / job_id / "export").mkdir(parents=True)
        (jobs / job_id / "export" / "half.bin").write_bytes(b"x" * 100)
        (jobs / job_id / "upload.vrf").write_bytes(body)
        arc.write_json(jobs / job_id / "job.json", {"id": job_id, "kind": "upload", "status": status,
                                                    "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                                    "created": created})
    (jobs / "cccc3333").mkdir()
    arc.write_json(jobs / "cccc3333" / "job.json", {"id": "cccc3333", "status": "done", "sha256": "s", "size": 1,
                                                    "created": now - 30, "finished": now - 25,
                                                    "match_uuid": MATCH_UUID})
    arc.write_json(jobs / "cccc3333" / "result.json", {"match_uuid": MATCH_UUID, "round_count": 3})
    (jobs / "debris").mkdir()
    worker, httpd, base, _ = start_archive(tmp_path, stub)
    try:
        assert get(base, "/jobs/cccc3333")[1] == {"id": "cccc3333", "status": "done",
                                                  "result": {"match_uuid": MATCH_UUID, "round_count": 3}}
        assert wait(base, "aaaa1111")["status"] == "done" and wait(base, "bbbb2222")["status"] == "done"
        assert not (jobs / "debris").exists() and not (jobs / "aaaa1111" / "export").exists()
        assert (disk / "pending" / "aaaa1111.vrf").exists() and (disk / "pending" / "bbbb2222.vrf").exists()
    finally:
        httpd.shutdown()


def test_an_upload_that_would_eat_the_parse_reserve_is_refused(tmp_path, stub):  # noqa: F811
    max_bytes = 10_000_000
    total = arc.ARCHIVE_SLACK + arc.PARSE_RESERVE_FACTOR * max_bytes + 100  # less than one (299-byte) upload
    worker, httpd, base, disk = start_archive(tmp_path, stub, total=total, max_bytes=max_bytes)
    try:
        status, answer = post(base, vrf_bytes())
        assert status == 503 and answer["error"] == "the worker is busy, try again soon"
        assert list((disk / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


def test_a_reparse_runs_from_the_archive_and_leaves_the_file_in_place(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        body = vrf_bytes()
        sha = hashlib.sha256(body).hexdigest()
        first = wait(base, post(base, body)[1]["id"])
        send(base, f"/jobs/{first['id']}/ack", ack_body(sha))
        status, queued = send(base, "/reparse", {"match_uuid": MATCH_UUID.upper()})
        assert status == 202 and queued["kind"] == "reparse" and queued["sha256"] == sha and queued["size"] == len(body)
        done = wait(base, queued["id"])
        assert done["status"] == "done" and done["result"]["match_uuid"] == MATCH_UUID
        assert list((disk / "pending").iterdir()) == [], "a reparse never makes a pending file"
        assert send(base, f"/jobs/{done['id']}/ack", ack_body(sha, "replaced", 2))[1] == {"archived": True,
                                                                                          "result": "kept"}
        assert get(base, "/archive")[1]["files"][0]["replay_id"] == 2
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == body
        assert send(base, "/reparse", {"match_uuid": "11111111-0000-0000-0000-000000000000"})[0] == 404
    finally:
        httpd.shutdown()


def test_a_deletion_removes_the_files_and_a_pushed_list_removes_a_restored_one(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        body = vrf_bytes()
        sha = hashlib.sha256(body).hexdigest()
        first = wait(base, post(base, body)[1]["id"])
        send(base, f"/jobs/{first['id']}/ack", ack_body(sha))
        assert send(base, "/archive/delete", {"match_uuid": MATCH_UUID})[1]["removed"]["archived"] == [MATCH_UUID]
        assert not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        # A new upload of the deleted match: parsed, but never held, and its ack is refused.
        again = wait(base, post(base, body)[1]["id"])
        assert list((disk / "pending").iterdir()) == []
        assert send(base, f"/jobs/{again['id']}/ack", ack_body(sha, replay_id=3))[1]["reason"] == "deleted on request"
        # A "restored" file: the list the web app pushes deletes it.
        worker.archive.set_tombstones([])
        (disk / "archive" / f"{MATCH_UUID}.vrf").write_bytes(body)
        worker.archive.index[MATCH_UUID] = {"sha256": sha, "size": len(body), "accepted_at": "x", "replay_id": 1}
        status, answer = send(base, "/archive/tombstones", {"match_uuids": [MATCH_UUID]})
        assert status == 200 and answer["removed"]["archived"] == [MATCH_UUID]
        assert answer["boot_id"] == get(base, "/health")[1]["archive"]["boot_id"]
        assert not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
    finally:
        httpd.shutdown()


def test_an_ack_for_an_unfinished_job_asks_to_be_sent_again(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="slow")
    try:
        job_id = post(base, vrf_bytes())[1]["id"]
        assert send(base, f"/jobs/{job_id}/ack", ack_body("0" * 64))[0] == 503
        wait(base, job_id)
    finally:
        httpd.shutdown()


def test_settings_read_the_archive_from_the_environment():
    settings = server.Settings.from_env({"REPLAY_ARCHIVE_DIR": "/var/replay", "REPLAY_ARCHIVE_REQUIRE_MOUNT": "0"})
    assert settings.archive_dir == Path("/var/replay") and settings.archive_require_mount is False
    assert server.Settings.from_env({}).archive_dir is None

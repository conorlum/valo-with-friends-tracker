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

import pytest

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


# ---------------------------------------------------------------- re-parse attempts (the automatic queue's protocol)

ATTEMPT = "6f1c2b9e-0d34-4c1a-9b7e-3a5d8e2f4c10"
ATTEMPT_JOB = "auto" + ATTEMPT.replace("-", "")
OTHER_MATCH = "11111111-0000-4000-8000-000000000000"


def plant_archive(tmp_path, body: bytes):
    """The archive as a worker that kept this match left it (no worker running)."""
    disk = tmp_path / "disk"
    (disk / "archive").mkdir(parents=True, exist_ok=True)
    (disk / "archive" / f"{MATCH_UUID}.vrf").write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    arc.write_json(disk / "archive" / "index.json", {"files": {MATCH_UUID: {
        "sha256": sha, "size": len(body), "accepted_at": "2026-10-01T00:00:00+00:00", "replay_id": 1}}})
    return disk, sha


def plant_receipt(disk, state, sha, **extra):
    (disk / "attempts").mkdir(parents=True, exist_ok=True)
    arc.write_json(disk / "attempts" / f"{ATTEMPT.replace('-', '')}.json", {
        "attempt_id": ATTEMPT, "match_uuid": MATCH_UUID, "sha256": sha, "state": state, **extra})


def plant_job(disk, body, sha, status="queued", with_file=True, **extra):
    folder = disk / "jobs" / ATTEMPT_JOB
    folder.mkdir(parents=True)
    if with_file:
        (folder / "upload.vrf").write_bytes(body)
    arc.write_json(folder / "job.json", {"id": ATTEMPT_JOB, "kind": "reparse", "status": status, "sha256": sha,
                                         "size": len(body), "created": time.time() - 5, "match_uuid": MATCH_UUID,
                                         "attempt_id": ATTEMPT, **extra})
    return folder


def attempt(base, sha, attempt_id=ATTEMPT, match_uuid=MATCH_UUID):
    return send(base, "/reparse", {"match_uuid": match_uuid, "attempt_id": attempt_id, "expected_sha256": sha})


def lookup(base, attempt_id=ATTEMPT):
    return get(base, f"/reparse/attempts/{attempt_id}")


def close(base, sha, attempt_id=ATTEMPT, match_uuid=MATCH_UUID):
    return send(base, f"/reparse/attempts/{attempt_id}/close", {"match_uuid": match_uuid, "expected_sha256": sha})


def auto_jobs(disk):
    return sorted(p.name for p in (disk / "jobs").iterdir() if p.name.startswith("auto"))


def test_health_advertises_the_reparse_protocol_only_with_the_archive_on(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        health = get(base, "/health")[1]
        assert health["archive"]["enabled"] is True and health["archive"]["reparse_protocol"] == 1
        assert health["recipe"] == worker.recipe
    finally:
        httpd.shutdown()


def test_an_attempt_is_accepted_once_and_a_repeat_returns_its_one_job(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        status, first = attempt(base, sha, attempt_id=ATTEMPT.upper(), match_uuid=MATCH_UUID.upper())
        assert status == 202 and first["code"] == "accepted" and first["state"] == "accepted"
        assert first["attempt_id"] == ATTEMPT and first["job_id"] == ATTEMPT_JOB
        assert (first["match_uuid"], first["sha256"], first["size"]) == (MATCH_UUID, sha, len(vrf_bytes()))
        assert first["job_status"] in ("queued", "parsing")
        done = wait(base, first["job_id"])
        assert done["status"] == "done" and done["kind"] == "reparse" and done["result"]["match_uuid"] == MATCH_UUID
        status, again = attempt(base, sha)
        assert status == 200 and again == {**first, "job_status": "done"}
        assert lookup(base) == (200, again)
        assert auto_jobs(disk) == [ATTEMPT_JOB] and worker.reparse_queue.empty()
        receipt = arc.read_json(disk / "attempts" / f"{ATTEMPT.replace('-', '')}.json", None)
        assert receipt["state"] == "accepted" and receipt["job_id"] == ATTEMPT_JOB
        # The ack of an automatic job is the ack of any reparse.
        assert send(base, f"/jobs/{ATTEMPT_JOB}/ack", ack_body(sha, "replaced", 2))[1] == {"archived": True,
                                                                                            "result": "kept"}
    finally:
        httpd.shutdown()


def test_a_manual_reparse_answers_exactly_as_before_beside_the_protocol(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        status, queued = send(base, "/reparse", {"match_uuid": MATCH_UUID})
        assert status == 202 and set(queued) == {"id", "status", "kind", "sha256", "size"}
        assert queued["kind"] == "reparse" and queued["status"] == "queued" and not queued["id"].startswith("auto")
        assert wait(base, queued["id"])["status"] == "done"
        assert send(base, "/reparse", {"match_uuid": OTHER_MATCH}) == (404, {"error": "this match has no archived file"})
        assert send(base, "/reparse", {}) == (400, {"error": "match_uuid is required"})
        send(base, "/archive/delete", {"match_uuid": MATCH_UUID})
        assert send(base, "/reparse", {"match_uuid": MATCH_UUID})[0] == 404
        assert not (disk / "attempts").exists() or list((disk / "attempts").iterdir()) == [], "no receipt for a manual one"
    finally:
        httpd.shutdown()


def test_two_clients_sending_one_attempt_at_once_see_one_job(tmp_path, stub):  # noqa: F811
    import threading

    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        answers, gate = [], threading.Barrier(4)

        def client():
            gate.wait()
            answers.append(attempt(base, sha))

        threads = [threading.Thread(target=client) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(status for status, _ in answers) == [200, 200, 200, 202]
        assert {body["job_id"] for _, body in answers} == {ATTEMPT_JOB} and auto_jobs(disk) == [ATTEMPT_JOB]
        assert wait(base, ATTEMPT_JOB)["status"] == "done"
        assert worker.reparse_queue.empty(), "enqueued once"
    finally:
        httpd.shutdown()


def test_an_attempt_id_used_with_another_match_or_file_is_a_conflict(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        assert attempt(base, sha)[0] == 202
        status, answer = attempt(base, "a" * 64)
        assert (status, answer["code"]) == (409, "identity_conflict")
        status, answer = attempt(base, sha, match_uuid=OTHER_MATCH)
        assert (status, answer["code"]) == (409, "identity_conflict")
        assert close(base, "a" * 64)[1]["code"] == "identity_conflict"
        assert auto_jobs(disk) == [ATTEMPT_JOB]
        wait(base, ATTEMPT_JOB)
    finally:
        httpd.shutdown()


def test_a_definite_refusal_accepts_nothing_and_leaves_no_receipt(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        # Another recording is archived than the one the site selected.
        status, answer = attempt(base, "b" * 64)
        assert status == 409 and answer == {"code": "sha_mismatch", "sha256": sha, "error": answer["error"]}
        assert lookup(base) == (404, {"code": "unknown_attempt", "error": "no such attempt"})
        assert auto_jobs(disk) == [] and list((disk / "attempts").iterdir()) == []
        # Nothing archived for the match.
        status, answer = attempt(base, sha, match_uuid=OTHER_MATCH)
        assert (status, answer["code"]) == (404, "no_archived_file") and list((disk / "attempts").iterdir()) == []
        # Not an attempt at all.
        for bad in ({"attempt_id": "not-a-uuid", "expected_sha256": sha},
                    {"attempt_id": ATTEMPT, "expected_sha256": "abc"},
                    {"attempt_id": ATTEMPT}):
            status, answer = send(base, "/reparse", {"match_uuid": MATCH_UUID, **bad})
            assert (status, answer["code"]) == (400, "bad_request"), bad
        assert lookup(base, "nope")[1]["code"] == "bad_request"
        # A refusal consumed nothing: the same id with the right file is accepted.
        assert attempt(base, sha)[0] == 202
        wait(base, ATTEMPT_JOB)
        # Deleted on request: a new id is refused, and that id keeps no receipt either.
        send(base, "/archive/delete", {"match_uuid": MATCH_UUID})
        other = "0a0a0a0a-0000-4000-8000-000000000001"
        status, answer = attempt(base, sha, attempt_id=other)
        assert (status, answer["code"]) == (409, "deleted") and lookup(base, other)[0] == 404
    finally:
        httpd.shutdown()


def test_the_attempt_routes_say_archive_off_without_an_archive(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        assert attempt(base, "c" * 64) == (404, {"code": "archive_off", "error": "the archive is off"})
        assert lookup(base)[1]["code"] == "archive_off" and close(base, "c" * 64)[1]["code"] == "archive_off"
        assert "reparse_protocol" not in get(base, "/health")[1]["archive"]
    finally:
        httpd.shutdown()


def test_a_full_queue_accepts_nothing_and_the_same_id_works_later(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="slow", queue_size=1)
    try:
        running = send(base, "/reparse", {"match_uuid": MATCH_UUID})[1]["id"]
        wait(base, running, until=("parsing",))
        waiting = send(base, "/reparse", {"match_uuid": MATCH_UUID})[1]["id"]   # fills the one waiting place
        status, answer = attempt(base, sha)
        assert (status, answer["code"]) == (503, "queue_full")
        assert lookup(base)[0] == 404 and auto_jobs(disk) == [], "nothing was accepted"
        assert send(base, "/reparse", {"match_uuid": MATCH_UUID})[0] == 503, "the manual route is full too"
        wait(base, waiting)
        status, answer = attempt(base, sha)
        assert (status, answer["code"]) == (202, "accepted")
        assert wait(base, ATTEMPT_JOB)["status"] == "done"
    finally:
        httpd.shutdown()


def test_closing_an_unknown_attempt_fences_out_a_delayed_submission(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        closed = {"code": "closed", "attempt_id": ATTEMPT, "state": "closed"}
        assert close(base, sha) == (200, closed) and close(base, sha) == (200, closed)
        assert attempt(base, sha) == (409, closed), "the delayed request is refused"
        assert lookup(base) == (200, closed)
        assert auto_jobs(disk) == [] and worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


def test_closing_an_accepted_attempt_returns_its_job_and_interrupts_nothing(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="slow")
    try:
        assert attempt(base, sha)[0] == 202
        status, answer = close(base, sha)
        assert status == 200 and answer["code"] == "accepted" and answer["job_id"] == ATTEMPT_JOB
        assert wait(base, ATTEMPT_JOB)["status"] == "done"
        assert lookup(base)[1]["job_status"] == "done"
    finally:
        httpd.shutdown()


# Each crash point's disk is written by hand, as a killed worker would leave it; then one worker starts
# (one live Worker per folder: Windows file locks) and the site retries the same id.

def test_a_crash_after_copying_the_file_finishes_that_same_preparation_once(tmp_path, stub):  # noqa: F811
    body = vrf_bytes()
    disk, sha = plant_archive(tmp_path, body)
    plant_receipt(disk, "preparing", sha)
    (disk / "jobs" / ATTEMPT_JOB).mkdir(parents=True)
    (disk / "jobs" / ATTEMPT_JOB / "upload.vrf").write_bytes(body[:50])   # a half-written copy, no job record
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        assert lookup(base) == (200, {"code": "preparing", "attempt_id": ATTEMPT, "match_uuid": MATCH_UUID,
                                      "sha256": sha, "state": "preparing"})
        assert worker.reparse_queue.empty() and worker.get(ATTEMPT_JOB) is None, "a lookup enqueues nothing"
        status, answer = attempt(base, sha)
        assert (status, answer["code"], answer["job_id"]) == (202, "accepted", ATTEMPT_JOB)
        assert wait(base, ATTEMPT_JOB)["status"] == "done" and auto_jobs(disk) == [ATTEMPT_JOB]
        assert attempt(base, sha)[0] == 200
    finally:
        httpd.shutdown()


def test_a_crash_after_the_job_record_is_recovered_as_that_accepted_job(tmp_path, stub):  # noqa: F811
    body = vrf_bytes()
    disk, sha = plant_archive(tmp_path, body)
    plant_receipt(disk, "preparing", sha)          # the acceptance receipt was never written
    plant_job(disk, body, sha)
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        status, answer = lookup(base)
        assert (status, answer["code"], answer["job_id"]) == (200, "accepted", ATTEMPT_JOB)
        assert arc.read_json(disk / "attempts" / f"{ATTEMPT.replace('-', '')}.json", {})["state"] == "accepted"
        assert attempt(base, sha)[0] == 200, "the retry gets the recovered job, never a second one"
        assert wait(base, ATTEMPT_JOB)["status"] == "done"
        assert auto_jobs(disk) == [ATTEMPT_JOB] and worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


def test_a_crash_after_the_acceptance_receipt_runs_the_job_once(tmp_path, stub):  # noqa: F811
    body = vrf_bytes()
    disk, sha = plant_archive(tmp_path, body)
    plant_receipt(disk, "accepted", sha, job_id=ATTEMPT_JOB, size=len(body))
    plant_job(disk, body, sha)                      # never enqueued: the restart's own recovery queues it
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        assert attempt(base, sha)[0] == 200
        assert wait(base, ATTEMPT_JOB)["status"] == "done"
        time.sleep(0.5)
        assert worker.reparse_queue.empty() and auto_jobs(disk) == [ATTEMPT_JOB], "queued once, by one recovery"
    finally:
        httpd.shutdown()


def test_a_lost_reply_is_answered_with_the_same_job_on_a_retry(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        def lost(answer):
            worker._before_reply = lambda answer: None
            raise ConnectionError("the reply never left")

        worker._before_reply = lost
        try:
            first = attempt(base, sha)
        except (OSError, urllib.error.URLError):
            first = None
        assert first is None or first[0] >= 500, "the first request got no usable answer"
        status, answer = attempt(base, sha)
        assert (status, answer["code"], answer["job_id"]) == (200, "accepted", ATTEMPT_JOB)
        assert wait(base, ATTEMPT_JOB)["status"] == "done" and auto_jobs(disk) == [ATTEMPT_JOB]
        assert worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


def test_an_accepted_attempt_is_answered_after_a_restart(tmp_path, stub):  # noqa: F811
    body = vrf_bytes()
    disk, sha = plant_archive(tmp_path, body)
    plant_receipt(disk, "accepted", sha, job_id=ATTEMPT_JOB, size=len(body))
    folder = plant_job(disk, body, sha, status="done", with_file=False, finished=time.time() - 2)
    arc.write_json(folder / "result.json", {"match_uuid": MATCH_UUID, "round_count": 3})
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        expected = {"code": "accepted", "attempt_id": ATTEMPT, "job_id": ATTEMPT_JOB, "match_uuid": MATCH_UUID,
                    "sha256": sha, "size": len(body), "state": "accepted", "job_status": "done"}
        assert lookup(base) == (200, expected) and attempt(base, sha) == (200, expected)
        assert get(base, f"/jobs/{ATTEMPT_JOB}")[1]["result"] == {"match_uuid": MATCH_UUID, "round_count": 3}
        # The archived file changing or going later does not change what the id means.
        send(base, "/archive/delete", {"match_uuid": MATCH_UUID})
        assert attempt(base, sha) == (200, expected)
    finally:
        httpd.shutdown()


def test_an_accepted_attempt_whose_result_was_swept_is_expired_not_parsed_again(tmp_path, stub):  # noqa: F811
    body = vrf_bytes()
    disk, sha = plant_archive(tmp_path, body)
    plant_receipt(disk, "accepted", sha, job_id=ATTEMPT_JOB, size=len(body))
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        status, answer = lookup(base)
        assert (status, answer["code"], answer["job_status"]) == (200, "accepted", "expired")
        status, answer = attempt(base, sha)
        assert (status, answer["code"], answer["job_status"]) == (200, "accepted", "expired")
        assert auto_jobs(disk) == [] and worker.reparse_queue.empty(), "never a second parse for an accepted id"
    finally:
        httpd.shutdown()


def test_receipts_outlive_the_finished_job_sweep_and_count_as_disk_use(tmp_path, stub):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        assert attempt(base, sha)[0] == 202
        wait(base, ATTEMPT_JOB)
        time.sleep(0.3)                             # the job's last record is written just after it reads done
        worker.get(ATTEMPT_JOB).finished = time.time() - arc.UNCOLLECTED_TTL_S - 10
        before = worker.archive.budget()
        worker._sweep()
        assert auto_jobs(disk) == [] and lookup(base)[1]["job_status"] == "expired"
        assert worker.archive.receipt_bytes() > 0
        assert worker.archive.budget() == before, "the receipt is still counted"
        assert worker.archive.budget() == (worker.archive.total - worker.archive.parse_reserve - arc.ARCHIVE_SLACK
                                           - worker.archive.pending_bytes() - worker.archive.receipt_bytes())
    finally:
        httpd.shutdown()


def test_a_restored_disk_with_another_job_under_an_accepted_id_fails_that_attempt(tmp_path, stub):  # noqa: F811
    body = vrf_bytes()
    disk, sha = plant_archive(tmp_path, body)
    plant_receipt(disk, "accepted", sha, job_id=ATTEMPT_JOB, size=len(body))
    folder = plant_job(disk, body, "d" * 64, status="done", with_file=False, finished=time.time() - 2)
    arc.write_json(folder / "result.json", {"match_uuid": MATCH_UUID, "round_count": 3})
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        status, answer = attempt(base, sha)
        assert (status, answer["code"], answer["job_status"]) == (200, "accepted", "failed")
        assert auto_jobs(disk) == [ATTEMPT_JOB] and worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


def test_an_acceptance_write_failure_still_runs_one_job_without_a_restart(tmp_path, stub, monkeypatch):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    write_receipt = worker.archive.write_receipt
    failures, parsed = [], []
    process = worker._process

    def fail_once(attempt_hex, receipt):
        if receipt['state'] == 'accepted' and not failures:
            failures.append(attempt_hex)
            raise OSError('injected acceptance write failure')
        write_receipt(attempt_hex, receipt)

    def count_parse(job):
        parsed.append(job.id)
        return process(job)

    monkeypatch.setattr(worker.archive, 'write_receipt', fail_once)
    monkeypatch.setattr(worker, '_process', count_parse)
    try:
        assert attempt(base, sha)[1]['code'] == 'accepted'
        assert wait(base, ATTEMPT_JOB)['status'] == 'done'
        for response in (lookup(base), attempt(base, sha), close(base, sha)):
            assert response[1]['job_id'] == ATTEMPT_JOB and response[1]['job_status'] == 'done'
        assert failures == [ATTEMPT.replace('-', '')] and parsed == [ATTEMPT_JOB]
        assert worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


def damage_read(monkeypatch, path, failure):
    """Damage exactly one durable record, leaving unrelated archive and job reads working."""
    if failure == 'unreadable':
        original = Path.read_text

        def read_text(target, *args, **kwargs):
            if target == path:
                raise PermissionError('injected record read failure')
            return original(target, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', read_text)
    elif failure == 'timestamps':
        record = json.loads(path.read_text(encoding='utf-8'))
        record['finished'] = 'not a timestamp'
        path.write_text(json.dumps(record), encoding='utf-8')
    else:
        path.write_text({'json': '{', 'structure': '[]', 'fields': '{"state": "accepted"}'}[failure],
                        encoding='utf-8')


@pytest.mark.parametrize('cleanup', ['forget', 'sweep'])
def test_cleanup_keeps_the_job_until_its_acceptance_receipt_can_be_written(tmp_path, stub, monkeypatch,
                                                                        cleanup):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    original = worker.archive.write_receipt

    def unavailable(attempt_hex, receipt):
        if receipt['state'] == 'accepted':
            raise OSError('the acceptance receipt is still unwritable')
        original(attempt_hex, receipt)

    monkeypatch.setattr(worker.archive, 'write_receipt', unavailable)
    try:
        assert attempt(base, sha)[1]['code'] == 'accepted'
        assert wait(base, ATTEMPT_JOB)['status'] == 'done'
        worker.reparse_queue.join()   # wait until the parse's final job record is written
        if cleanup == 'forget':
            monkeypatch.setattr(server, 'FINISHED_KEPT', 0)
            clean = worker._forget_old
        else:
            worker.get(ATTEMPT_JOB).finished = time.time() - arc.UNCOLLECTED_TTL_S - 10
            clean = worker._sweep
        clean()
        assert worker.get(ATTEMPT_JOB) is not None and (disk / 'jobs' / ATTEMPT_JOB / 'job.json').exists()
        assert lookup(base)[0] == 503, 'the lookup cannot yet promote the receipt, and enqueues nothing'
        monkeypatch.setattr(worker.archive, 'write_receipt', original)
        clean()
        assert worker.get(ATTEMPT_JOB) is None and auto_jobs(disk) == []
        assert attempt(base, sha)[1]['job_status'] == 'expired'
        assert worker.reparse_queue.empty(), 'the old accepted id is never parsed again after cleanup'
    finally:
        httpd.shutdown()


@pytest.mark.parametrize('state', ['closed', 'accepted'])
@pytest.mark.parametrize('failure', ['unreadable', 'json', 'structure', 'fields'])
@pytest.mark.parametrize('route', [lookup, attempt, close])
def test_a_damaged_receipt_never_reopens_or_removes_an_attempt(tmp_path, stub, monkeypatch, state, failure,
                                                            route):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    plant_receipt(disk, state, sha, **({'job_id': ATTEMPT_JOB, 'size': 1} if state == 'accepted' else {}))
    folder = plant_job(disk, vrf_bytes(), sha, status='done', with_file=False, finished=time.time())
    arc.write_json(folder / 'result.json', {'match_uuid': MATCH_UUID})
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    receipt_path = disk / 'attempts' / (ATTEMPT.replace('-', '') + '.json')
    try:
        damage_read(monkeypatch, receipt_path, failure)
        before = {p.relative_to(disk): p.read_bytes() for p in disk.rglob('*') if p.is_file()}
        response = route(base) if route is lookup else route(base, sha)
        assert response[0] == 503 and response[1]['code'] == 'state_unavailable'
        after = {p.relative_to(disk): p.read_bytes() for p in disk.rglob('*') if p.is_file()}
        assert after == before, 'a read failure preserves the fence, result and job record'
        assert worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


@pytest.mark.parametrize('failure', ['unreadable', 'json', 'structure', 'fields'])
@pytest.mark.parametrize('route', [lookup, attempt, close])
def test_a_preparing_attempt_with_a_damaged_job_record_is_left_alone(tmp_path, stub, monkeypatch, failure,
                                                                   route):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    plant_receipt(disk, 'preparing', sha)
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    folder = plant_job(disk, vrf_bytes(), sha)
    try:
        damage_read(monkeypatch, folder / 'job.json', failure)
        before = {p.relative_to(disk): p.read_bytes() for p in disk.rglob('*') if p.is_file()}
        response = route(base) if route is lookup else route(base, sha)
        assert response[0] == 503 and response[1]['code'] == 'state_unavailable'
        assert {p.relative_to(disk): p.read_bytes() for p in disk.rglob('*') if p.is_file()} == before
        assert worker.get(ATTEMPT_JOB) is None and worker.reparse_queue.empty()
    finally:
        httpd.shutdown()


@pytest.mark.parametrize('failure', ['unreadable', 'json', 'structure', 'fields', 'timestamps'])
def test_startup_preserves_a_damaged_automatic_job_record(tmp_path, stub, monkeypatch, failure):  # noqa: F811
    disk, sha = plant_archive(tmp_path, vrf_bytes())
    plant_receipt(disk, 'preparing', sha)
    folder = plant_job(disk, vrf_bytes(), sha)
    damage_read(monkeypatch, folder / 'job.json', failure)
    before = {p.relative_to(disk): p.read_bytes() for p in disk.rglob('*') if p.is_file()}
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        assert lookup(base)[0] == 503
        assert {p.relative_to(disk): p.read_bytes() for p in disk.rglob('*') if p.is_file()} == before
        assert worker.get(ATTEMPT_JOB) is None and worker.reparse_queue.empty()
    finally:
        httpd.shutdown()

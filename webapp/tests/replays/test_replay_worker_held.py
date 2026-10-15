"""Uploads from a game build the pinned parser doesn't support (replay_worker/server.py, "held"): read from the
file's header before any parse, kept on the archive disk under the same job id, and parsed once a parser that
supports the build starts. Also the parser's own last lines on a failed parse (`detail`)."""

import hashlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_worker import get, post, start, stub, vrf_bytes, wait  # noqa: E402,F401  (fixture)
from test_replay_worker_archive import ack_body, send, start_archive  # noqa: E402

from app.replays.header import game_build  # noqa: E402
from replay_worker import archive as arc  # noqa: E402
from replay_worker import server  # noqa: E402

NEW = "++Ares-Core+release-13.09"


def new_patch_bytes() -> bytes:
    """The synthetic replay (the stub parser exports it as the supported 13.06) with a 13.09 branch in it."""
    return vrf_bytes() + b" " + NEW.encode("ascii") + b" "


def test_the_game_build_is_read_from_the_start_of_the_file(tmp_path):
    path = tmp_path / "a.vrf"
    path.write_bytes(new_patch_bytes())
    assert game_build(path) == NEW
    path.write_bytes(vrf_bytes())
    assert game_build(path) is None
    path.write_bytes(b"x" * (70 * 1024) + NEW.encode("ascii"))
    assert game_build(path) is None, "only the header's part of the file is read"


def test_an_unsupported_build_is_held_unparsed_and_other_uploads_still_parse(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        status, body = post(base, new_patch_bytes())
        assert (status, body["status"]) == (202, "held")
        held = get(base, f"/jobs/{body['id']}")[1]
        assert held["status"] == "held" and held["game_build"] == NEW
        assert "13.09" in held["error"] and "kept" in held["error"]
        folder = disk / "jobs" / body["id"]
        assert (folder / "upload.vrf").read_bytes() == new_patch_bytes()
        assert not (folder / "stub.pid").exists(), "never handed to the parser"
        archive = get(base, "/health")[1]["archive"]
        assert (archive["held"], archive["held_bytes"], archive["held_builds"]) == (1, len(new_patch_bytes()), [NEW])
        # A supported upload is parsed as before, and the held one is still held afterwards.
        assert wait(base, post(base, vrf_bytes())[1]["id"])["status"] == "done"
        assert get(base, f"/jobs/{body['id']}")[1]["status"] == "held"
        # Not finished: an ack for it is "ask again later", never "nothing held".
        assert send(base, f"/jobs/{body['id']}/ack", ack_body("0" * 64))[0] == 503
    finally:
        httpd.shutdown()


def test_held_files_come_out_of_the_archive_budget(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        before = worker.archive.budget()
        post(base, new_patch_bytes())
        assert worker.archive.budget() == before - len(new_patch_bytes())
    finally:
        httpd.shutdown()


def test_a_held_upload_is_parsed_once_the_parser_supports_its_build(tmp_path, stub):  # noqa: F811
    disk = tmp_path / "disk"
    folder = disk / "jobs" / "held0001"
    folder.mkdir(parents=True)
    body = new_patch_bytes()
    sha = hashlib.sha256(body).hexdigest()
    (folder / "upload.vrf").write_bytes(body)
    # As the older image left it (written by hand: one live Worker only, for Windows file locks).
    arc.write_json(folder / "job.json", {"id": "held0001", "kind": "upload", "status": "held", "sha256": sha,
                                         "size": len(body), "created": time.time() - 3600, "game_build": NEW,
                                         "error": server.held_reason(NEW)})
    worker, httpd, base, _ = start_archive(tmp_path, stub,
                                           supported_builds=frozenset({"++Ares-Core+release-13.06", NEW}))
    try:
        done = wait(base, "held0001")
        assert done["status"] == "done", done
        assert done["result"]["match_uuid"] == MATCH_UUID and "error" not in done
        # From here it is an ordinary parsed upload: it waits in pending/ for its ack, and the ack archives it.
        assert (disk / "pending" / "held0001.vrf").read_bytes() == body
        status, answer = send(base, "/jobs/held0001/ack", ack_body(sha))
        assert (status, answer["result"]) == (200, "archived")
        assert worker.queue.unfinished_tasks == 0, "a released job is never counted against the upload queue"
    finally:
        httpd.shutdown()


def test_a_held_job_still_unsupported_after_a_restart_stays_held(tmp_path, stub):  # noqa: F811
    disk = tmp_path / "disk"
    folder = disk / "jobs" / "held0002"
    folder.mkdir(parents=True)
    body = new_patch_bytes()
    (folder / "upload.vrf").write_bytes(body)
    # Released by an image that supported it, then the worker restarted mid-parse onto one that doesn't.
    arc.write_json(folder / "job.json", {"id": "held0002", "kind": "upload", "status": "parsing",
                                         "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                         "created": time.time() - 60, "game_build": NEW})
    worker, httpd, base, _ = start_archive(tmp_path, stub)
    try:
        time.sleep(1.5)
        job = get(base, "/jobs/held0002")[1]
        assert job["status"] == "held" and "13.09" in job["error"]
    finally:
        httpd.shutdown()


def test_with_the_archive_off_an_unsupported_build_fails_at_once(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        status, body = post(base, new_patch_bytes())
        assert (status, body["status"]) == (202, "failed")
        job = get(base, f"/jobs/{body['id']}")[1]
        assert "13.09" in job["error"] and "upload it again" in job["error"]
        assert list((tmp_path / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


def test_over_the_cap_an_unsupported_build_fails_and_its_file_goes(tmp_path, stub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "HELD_MAX_BYTES", 0)
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        body = post(base, new_patch_bytes())[1]
        assert body["status"] == "failed"
        assert not (disk / "jobs" / body["id"] / "upload.vrf").exists()
    finally:
        httpd.shutdown()


def test_a_file_held_past_its_ttl_is_deleted_and_the_job_fails(tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        job_id = post(base, new_patch_bytes())[1]["id"]
        monkeypatch.setattr(server, "HELD_TTL_S", 0)
        worker._sweep()
        job = get(base, f"/jobs/{job_id}")[1]
        assert job["status"] == "failed" and "60 days" not in job["error"] and "deleted" in job["error"]
        assert not (disk / "jobs" / job_id / "upload.vrf").exists()
        assert get(base, "/health")[1]["archive"]["held"] == 0
    finally:
        httpd.shutdown()


def test_a_failed_parse_carries_the_parsers_last_lines(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-loud")
    try:
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and job["error"] == "parse failed"
        assert "[ERR] Failed to parse replay." in job["detail"]
        assert "no payload transform" in job["detail"] and " at Replay." not in job["detail"]
        assert not (disk / "jobs" / job["id"] / "parser.log").exists()
    finally:
        httpd.shutdown()


def test_a_silent_failed_parse_has_no_detail(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, mode="fail")
    try:
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and "detail" not in job
    finally:
        httpd.shutdown()

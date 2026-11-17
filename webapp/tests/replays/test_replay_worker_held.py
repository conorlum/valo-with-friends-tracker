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

from app.replays import condense  # noqa: E402
from app.replays.contract import waiting_agent_text  # noqa: E402
from app.replays.header import game_build  # noqa: E402
from replay_worker import archive as arc  # noqa: E402
from replay_worker import server  # noqa: E402

NEW = "++Ares-Core+release-99.99"


def new_patch_bytes() -> bytes:
    """The synthetic replay (the stub parser exports it as the supported 13.06) with the branch of a build no
    parser pin lists in it: 99.99, so a parser bump never breaks these tests."""
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
        assert "99.99" in held["error"] and "kept" in held["error"]
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
        assert job["status"] == "held" and "99.99" in job["error"]
    finally:
        httpd.shutdown()


def test_with_the_archive_off_an_unsupported_build_fails_at_once(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        status, body = post(base, new_patch_bytes())
        assert (status, body["status"]) == (202, "failed")
        job = get(base, f"/jobs/{body['id']}")[1]
        assert "99.99" in job["error"] and "upload it again" in job["error"]
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


def test_a_build_the_pin_already_lists_but_the_parser_still_refuses_is_held_not_failed(tmp_path, stub):  # noqa: F811
    """A build in `supported_builds` still crashes the real parser (an undocumented sub-version gap): held, not
    failed, so the file survives for a retry -- but on a different signal than an unlisted build (next test)."""
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch",
                                              supported_builds=frozenset({NEW}))
    try:
        job_id = post(base, new_patch_bytes())[1]["id"]  # a build IN supported_builds, unlike an unlisted hold
        job = wait(base, job_id, until=("held", "failed"))
        assert job["status"] == "held" and "may not be supported yet" in job["detail"]
        assert job["game_build"] == NEW
        assert (disk / "jobs" / job_id / "upload.vrf").exists(), "the file is kept, like any held upload"
        assert worker.get(job_id).finished is None and worker.get(job_id).parser_pin == worker.recipe
        # Proves the guard, not a side effect: its build is already in supported_builds (that's why the parser
        # even ran on it), so release must be keyed on parser_pin, not on a supported-builds membership check
        # that is already true. Without that guard this job would release (and re-crash) immediately.
        time.sleep(1.5)
        assert get(base, f"/jobs/{job_id}")[1]["status"] == "held"
    finally:
        httpd.shutdown()


def test_that_hold_releases_on_the_next_deploy_with_a_different_recipe(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        job_id = post(base, vrf_bytes())[1]["id"]
        assert wait(base, job_id, until=("held", "failed"))["status"] == "held"
        # A real deploy would ship a parser that no longer crashes on it; simulate both at once.
        worker.recipe = "a different recipe"
        worker.settings.parser_cmd[-1] = "ok"
        done = wait(base, job_id)
        assert done["status"] == "done", done
        # The old refusal doesn't linger on a job that has since succeeded.
        assert "detail" not in done and "game_build" not in done
        job = worker.get(job_id)
        assert job.detail is None and job.parser_pin is None
    finally:
        httpd.shutdown()


def test_over_the_cap_a_parser_refusal_fails_like_any_other_parse_failure(tmp_path, stub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "HELD_MAX_BYTES", 0)
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and "may not be supported yet" in job["detail"]
    finally:
        httpd.shutdown()


def test_with_the_archive_off_a_parser_refusal_fails_at_once(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and "may not be supported yet" in job["detail"]
    finally:
        httpd.shutdown()


def test_a_second_refusal_that_would_overflow_the_cap_fails_instead_of_holding(tmp_path, stub, monkeypatch):  # noqa: F811
    """The capacity check and the hold must be one step: room for exactly one of these two files, so the
    second must fail outright, not be held on a stale (too generous) read of the space left."""
    monkeypatch.setattr(server, "HELD_MAX_BYTES", len(vrf_bytes()))
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        first = wait(base, post(base, vrf_bytes())[1]["id"], until=("held", "failed"))
        assert first["status"] == "held"
        second = wait(base, post(base, vrf_bytes())[1]["id"])
        assert second["status"] == "failed" and "may not be supported yet" in second["detail"]
    finally:
        httpd.shutdown()


def test_an_automatic_reparse_a_parser_refuses_fails_instead_of_blocking_the_reparse_queue(tmp_path, stub):  # noqa: F811
    """A held reparse would look "pending" to the automatic queue forever (replay_upload.collect_auto), blocking
    every other automatic reparse behind it. Only an upload may hold; a reparse always just fails."""
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        body = vrf_bytes()
        uploaded = wait(base, post(base, body)[1]["id"])
        sha = hashlib.sha256(body).hexdigest()
        send(base, f"/jobs/{uploaded['id']}/ack", ack_body(sha))  # archives the file under MATCH_UUID
        worker.settings.parser_cmd[-1] = "fail-unsupported-patch"
        job = worker.submit_reparse(MATCH_UUID)
        done = wait(base, job.id)
        assert done["status"] == "failed" and "may not be supported yet" in done["detail"]
    finally:
        httpd.shutdown()


def test_with_no_recipe_a_parser_refusal_fails_instead_of_looping(tmp_path, stub, monkeypatch):  # noqa: F811
    """`parser_pin` would be None, colliding with the unlisted-build release signal (which also keys off
    parser_pin is None): held under a blank identity would release and re-crash forever."""
    def boom():
        raise OSError("the pin file is missing")

    monkeypatch.setattr(server, "current_recipe", boom)
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        assert worker.recipe is None
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and "may not be supported yet" in job["detail"]
    finally:
        httpd.shutdown()


def test_a_released_parser_refused_job_recovered_mid_parse_after_a_restart_gets_the_right_text(tmp_path, stub, monkeypatch):  # noqa: F811
    """`_recover`'s "released just before the restart" branch used to always use `held_reason` (the unlisted-
    build message); a parser-refused hold (no `game_build`, only `parser_pin`) needs `parser_refused_reason`."""
    monkeypatch.setattr(server, "current_recipe", lambda: "the current recipe")
    disk = tmp_path / "disk"
    folder = disk / "jobs" / "held0003"
    folder.mkdir(parents=True)
    body = vrf_bytes()
    (folder / "upload.vrf").write_bytes(body)
    # Not yet released: same recipe it was held under, so this restarted worker never releases it mid-test.
    arc.write_json(folder / "job.json", {
        "id": "held0003", "kind": "upload", "status": "parsing", "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body), "created": time.time() - 60, "parser_pin": "the current recipe",
        "detail": "this patch may not be supported yet"})
    worker, httpd, base, _ = start_archive(tmp_path, stub)
    try:
        time.sleep(1.5)
        job = get(base, "/jobs/held0003")[1]
        assert job["status"] == "held"
        assert "may not be supported yet" in job["error"] and "doesn't support yet" not in job["error"]
        assert "game_build" not in job
    finally:
        httpd.shutdown()


def test_the_ttl_expiry_message_handles_an_unreadable_game_build(tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        job_id = post(base, vrf_bytes())[1]["id"]  # vrf_bytes() has no readable game_build header
        wait(base, job_id, until=("held", "failed"))
        monkeypatch.setattr(server, "HELD_TTL_S", 0)
        worker._sweep()  # must not raise (the old code called _version(None))
        job = get(base, f"/jobs/{job_id}")[1]
        assert job["status"] == "failed" and "deleted" in job["error"]
    finally:
        httpd.shutdown()


def test_health_lists_held_builds_without_crashing_when_one_is_unknown(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        first = post(base, new_patch_bytes())[1]
        assert first["status"] == "held"  # unlisted build: synchronous, pre-flight (game_build == NEW)
        worker.settings.parser_cmd[-1] = "fail-unsupported-patch"
        second_id = post(base, vrf_bytes())[1]["id"]
        assert wait(base, second_id, until=("held", "failed"))["status"] == "held"  # game_build is unreadable
        archive = get(base, "/health")[1]["archive"]
        assert archive["held"] == 2 and archive["held_builds"] == [NEW]  # must not raise, must not list None
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ holds for an agent the site doesn't know yet

CODE = "NotAnAgent"


def held_for_agent(worker, base, body=None):
    """Uploads a replay whose condense meets the unknown agent; the job id once it is held."""
    job_id = post(base, body or vrf_bytes())[1]["id"]
    assert wait(base, job_id, until=("held", "failed"))["status"] == "held"
    return job_id


def test_an_upload_with_an_unknown_agent_is_held_for_that_agent(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="unknown_agent")
    try:
        job_id = held_for_agent(worker, base)
        job = get(base, f"/jobs/{job_id}")[1]
        assert job["waiting_agent"] == CODE and job["error"] == waiting_agent_text(CODE)
        assert (disk / "jobs" / job_id / "upload.vrf").read_bytes() == vrf_bytes()
        archive = get(base, "/health")[1]["archive"]
        assert archive["held"] == 1 and archive["held_agents"] == [CODE] and archive["held_builds"] == []
        # An ordinary upload after it still parses, and the held one stays held.
        worker.settings.parser_cmd[-1] = "ok"
        assert wait(base, post(base, vrf_bytes() + b"2")[1]["id"])["status"] == "done"
        assert get(base, f"/jobs/{job_id}")[1]["status"] == "held"
    finally:
        httpd.shutdown()


def test_an_agent_hold_is_not_released_by_a_recipe_change_alone(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="unknown_agent")
    try:
        job_id = held_for_agent(worker, base)
        worker.recipe = "another recipe"
        worker.settings.parser_cmd[-1] = "ok"
        time.sleep(1.5)
        job = get(base, f"/jobs/{job_id}")[1]
        assert job["status"] == "held" and job["waiting_agent"] == CODE
    finally:
        httpd.shutdown()


def test_an_agent_hold_is_released_and_parsed_once_the_site_knows_the_agent(tmp_path, stub, monkeypatch):  # noqa: F811
    disk = tmp_path / "disk"
    folder = disk / "jobs" / "agent001"
    folder.mkdir(parents=True)
    body = vrf_bytes()
    (folder / "upload.vrf").write_bytes(body)
    arc.write_json(folder / "job.json", {"id": "agent001", "kind": "upload", "status": "held",
                                         "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                         "created": time.time() - 3600, "waiting_agent": CODE,
                                         "error": waiting_agent_text(CODE)})
    # The restarted worker's agents table now names it, in another case.
    real = condense.load_agents()
    monkeypatch.setattr(condense, "load_agents", lambda *a, **k: {**real, "NOTANAGENT": "Someone"})
    worker, httpd, base, _ = start_archive(tmp_path, stub)
    try:
        done = wait(base, "agent001")
        assert done["status"] == "done", done
        assert worker.get("agent001").waiting_agent is None and "waiting_agent" not in done
        assert (disk / "pending" / "agent001.vrf").read_bytes() == body
    finally:
        httpd.shutdown()


def test_an_agent_hold_that_is_still_unsupported_keeps_status_and_error_over_a_restart(tmp_path, stub):  # noqa: F811
    disk = tmp_path / "disk"
    folder = disk / "jobs" / "agent002"
    folder.mkdir(parents=True)
    body = vrf_bytes()
    (folder / "upload.vrf").write_bytes(body)
    arc.write_json(folder / "job.json", {"id": "agent002", "kind": "upload", "status": "held",
                                         "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                         "created": time.time() - 3600, "waiting_agent": CODE,
                                         "error": waiting_agent_text(CODE)})
    worker, httpd, base, _ = start_archive(tmp_path, stub)
    try:
        time.sleep(1.5)
        job = get(base, "/jobs/agent002")[1]
        assert (job["status"], job["error"], job["waiting_agent"]) == ("held", waiting_agent_text(CODE), CODE)
    finally:
        httpd.shutdown()


def test_a_job_saved_queued_with_an_agent_code_is_held_again_after_a_restart(tmp_path, stub):  # noqa: F811
    disk = tmp_path / "disk"
    folder = disk / "jobs" / "agent003"
    folder.mkdir(parents=True)
    body = vrf_bytes()
    (folder / "upload.vrf").write_bytes(body)
    arc.write_json(folder / "job.json", {"id": "agent003", "kind": "upload", "status": "queued",
                                         "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                         "created": time.time() - 60, "waiting_agent": CODE})
    worker, httpd, base, _ = start_archive(tmp_path, stub)
    try:
        time.sleep(1.5)
        job = get(base, "/jobs/agent003")[1]
        assert (job["status"], job["error"]) == ("held", waiting_agent_text(CODE))
    finally:
        httpd.shutdown()


def test_with_the_archive_off_an_unknown_agent_fails_telling_the_uploader_to_keep_the_file(tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, mode="unknown_agent")
    try:
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and "waiting_agent" not in job
        assert job["error"] == (f"not condensable: unknown_agent (code name {CODE}): keep the file and upload "
                                "it again once the site supports this agent")
    finally:
        httpd.shutdown()


def test_over_the_cap_an_unknown_agent_fails_telling_the_uploader_to_keep_the_file(tmp_path, stub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "HELD_MAX_BYTES", 0)
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="unknown_agent")
    try:
        job = wait(base, post(base, vrf_bytes())[1]["id"])
        assert job["status"] == "failed" and "keep the file and upload it again" in job["error"]
        assert CODE in job["error"]
    finally:
        httpd.shutdown()


def test_a_reparse_that_meets_an_unknown_agent_fails_and_is_never_held(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        body = vrf_bytes()
        uploaded = wait(base, post(base, body)[1]["id"])
        send(base, f"/jobs/{uploaded['id']}/ack", ack_body(hashlib.sha256(body).hexdigest()))
        worker.settings.parser_cmd[-1] = "unknown_agent"
        done = wait(base, worker.submit_reparse(MATCH_UUID).id)
        assert done["status"] == "failed" and done["error"] == "not condensable: unknown_agent"
        assert "waiting_agent" not in done
    finally:
        httpd.shutdown()


def test_an_agent_hold_past_its_ttl_is_deleted_with_the_agent_text(tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="unknown_agent")
    try:
        job_id = held_for_agent(worker, base)
        monkeypatch.setattr(server, "HELD_TTL_S", 0)
        worker._sweep()
        job = get(base, f"/jobs/{job_id}")[1]
        assert job["status"] == "failed"
        assert job["error"] == (f"waiting for agent {CODE}: it still wasn't supported after 0 days, so the kept "
                                "file was deleted")
        assert not (disk / "jobs" / job_id / "upload.vrf").exists()
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ hold kinds are mutually exclusive


def seed_job(tmp_path, job_id, **fields):
    folder = tmp_path / "disk" / "jobs" / job_id
    folder.mkdir(parents=True)
    body = vrf_bytes()
    (folder / "upload.vrf").write_bytes(body)
    arc.write_json(folder / "job.json", {"id": job_id, "kind": "upload", "sha256": hashlib.sha256(body).hexdigest(),
                                         "size": len(body), "created": time.time() - 3600, **fields})
    return folder


def wait_for(predicate, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_released_agent_hold_the_parser_then_refuses_is_only_a_parser_hold(tmp_path, stub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "current_recipe", lambda: "the recipe")
    seed_job(tmp_path, "excl0001", status="held", waiting_agent=CODE, error=waiting_agent_text(CODE))
    real = condense.load_agents()
    monkeypatch.setattr(condense, "load_agents", lambda *a, **k: {**real, "NOTANAGENT": "Someone"})
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        assert wait_for(lambda: worker.get("excl0001").parser_pin is not None)
        job = worker.get("excl0001")
        assert job.status == "held" and job.waiting_agent is None
        time.sleep(1.5)                                    # the agent signal must not release it again
        body = get(base, "/jobs/excl0001")[1]
        assert body["status"] == "held" and "waiting_agent" not in body
        assert "may not be supported yet" in body["error"] and CODE not in body["error"]
        archive = get(base, "/health")[1]["archive"]
        assert archive["held_agents"] == [] and archive["held"] == 1
        assert "waiting_agent" not in arc.read_json(disk / "jobs" / "excl0001" / "job.json", {})
    finally:
        httpd.shutdown()
    # ...and after a restart it is still one parser hold, not released by the (now supported) agent.
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        time.sleep(1.5)
        body = get(base, "/jobs/excl0001")[1]
        assert body["status"] == "held" and "waiting_agent" not in body
        assert "may not be supported yet" in body["error"]
        assert get(base, "/health")[1]["archive"]["held_agents"] == []
    finally:
        httpd.shutdown()


def test_a_saved_job_with_both_hold_markers_recovers_as_a_parser_hold(tmp_path, stub, monkeypatch):  # noqa: F811
    """What an earlier worker wrote after that transition: it must not be released by the agent signal."""
    monkeypatch.setattr(server, "current_recipe", lambda: "the recipe")
    seed_job(tmp_path, "excl0002", status="held", waiting_agent=CODE, parser_pin="the recipe",
             detail="this patch may not be supported yet", error=waiting_agent_text(CODE))
    seed_job(tmp_path, "excl0003", status="queued", waiting_agent=CODE, parser_pin="the recipe",
             detail="this patch may not be supported yet")
    real = condense.load_agents()
    monkeypatch.setattr(condense, "load_agents", lambda *a, **k: {**real, "NOTANAGENT": "Someone"})
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="fail-unsupported-patch")
    try:
        time.sleep(1.5)
        for job_id in ("excl0002", "excl0003"):
            body = get(base, f"/jobs/{job_id}")[1]
            assert body["status"] == "held" and "waiting_agent" not in body, body
            assert "may not be supported yet" in body["error"] and CODE not in body["error"]
        assert get(base, "/health")[1]["archive"]["held_agents"] == []
    finally:
        httpd.shutdown()


def test_an_agent_hold_on_a_job_with_an_earlier_build_or_refusal_hold_clears_those(tmp_path, stub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "current_recipe", lambda: "the new recipe")
    seed_job(tmp_path, "excl0004", status="held", game_build=NEW, parser_pin="an old recipe",
             detail="this patch may not be supported yet", error="the parser refused this file")
    worker, httpd, base, disk = start_archive(tmp_path, stub, mode="unknown_agent")
    try:
        assert wait_for(lambda: worker.get("excl0004").waiting_agent == CODE)
        assert wait_for(lambda: worker.get("excl0004").status == "held")
        job = worker.get("excl0004")
        assert job.game_build is None and job.parser_pin is None and job.error == waiting_agent_text(CODE)
        archive = get(base, "/health")[1]["archive"]
        assert archive["held_agents"] == [CODE] and archive["held_builds"] == []
        saved = arc.read_json(disk / "jobs" / "excl0004" / "job.json", {})
        assert saved["waiting_agent"] == CODE and "game_build" not in saved and "parser_pin" not in saved
    finally:
        httpd.shutdown()


def test_a_build_hold_established_on_a_job_carrying_an_agent_marker_clears_it(tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        folder = seed_job(tmp_path, "excl0005", status="queued")
        job = server.Job("excl0005", folder, "0" * 64, 10)
        job.game_build, job.waiting_agent = NEW, CODE
        worker._hold(job)
        assert job.status == "held" and job.waiting_agent is None and job.parser_pin is None
        archive = get(base, "/health")[1]["archive"]
        assert archive["held_agents"] == [] and archive["held_builds"] == [NEW]
    finally:
        httpd.shutdown()

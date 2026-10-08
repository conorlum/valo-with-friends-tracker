"""The web side of the .vrf archive (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1):
the ack sent after a store, `kept_existing` shown to its uploader as stored, the re-send and tombstone sync, and the admin
routes. Against the real worker with the stub parser and its archive in a temp folder; sqlite store."""

import hashlib
import io
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_store import TABLES  # noqa: E402
from test_replay_worker import start, stub, vrf_bytes  # noqa: E402,F401  (fixture)
from test_replay_worker_archive import start_archive  # noqa: E402

from replay_worker import archive as arc  # noqa: E402
from replay_worker import server  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayUpload  # noqa: E402
from app.routers import replay_admin as admin  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_archive_sync as sync  # noqa: E402
from app.services import replay_upload as uploads  # noqa: E402


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", "letmein")
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool)
    Base.metadata.create_all(engine, tables=[*TABLES, ReplayUpload.__table__])
    return engine


@pytest.fixture
def db(engine):
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def collect(db, client, data: bytes, session_key="sess"):
    upload = uploads.create_upload(db, io.BytesIO(data), session_key, None, client)
    for _ in range(300):
        upload = uploads.refresh_job(db, upload, client)
        if upload.status not in uploads.UNFINISHED:
            return upload
        time.sleep(0.1)
    raise AssertionError("the upload never finished")


def test_a_stored_upload_is_acked_and_its_file_archived(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        upload = collect(db, uploads.WorkerClient(base), vrf_bytes())
        assert (upload.status, upload.store_outcome, upload.archive_ack) == ("stored", "stored", "archived")
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == vrf_bytes()
        entry = worker.archive.entries()[0]
        assert entry["replay_id"] == upload.replay_id and entry["played_at"] is None  # unlinked: no date yet
        assert "kept_existing" not in routes._status_body(db, upload)
    finally:
        httpd.shutdown()


def test_with_the_archive_off_the_ack_is_recorded_as_off(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        upload = collect(db, uploads.WorkerClient(base), vrf_bytes())
        assert (upload.status, upload.archive_ack) == ("stored", "off")
        assert list((tmp_path / "jobs").iterdir()) == []
    finally:
        httpd.shutdown()


def test_a_second_recording_kept_out_looks_stored_to_its_uploader_and_its_file_dropped(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        first = collect(db, client, vrf_bytes())
        other = collect(db, client, vrf_bytes() + b"\0" * 16, session_key="sess2")
        assert (other.status, other.store_outcome, other.archive_ack) == ("stored", "kept_existing", "deleted")
        assert "did not link" in other.error, "the reason is kept for the admin"
        # Friends upload their own recordings of one match: the second uploader sees what the first did.
        assert routes._status_body(db, other) == routes._status_body(db, first)
        assert list((disk / "pending").iterdir()) == []
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == vrf_bytes(), "the first stays"
        assert db.query(Replay).one().id == first.replay_id
    finally:
        httpd.shutdown()


def test_a_deleted_match_is_refused_at_store_and_its_file_dropped(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        db.add(ReplayDeletion(match_uuid=MATCH_UUID, reason="asked"))
        db.commit()
        upload = collect(db, uploads.WorkerClient(base), vrf_bytes())
        assert upload.status == "failed" and "deleted on request" in upload.error
        assert (upload.store_outcome, upload.archive_ack) == ("failed", "deleted")
        assert db.query(Replay).count() == 0 and list((disk / "pending").iterdir()) == []
    finally:
        httpd.shutdown()


def test_an_unreachable_worker_leaves_the_ack_unsent(db, tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        monkeypatch.setattr(client, "ack", lambda *_: (_ for _ in ()).throw(uploads.WorkerError("down")))
        upload = collect(db, client, vrf_bytes())
        assert (upload.status, upload.store_outcome, upload.archive_ack) == ("stored", "stored", None)
        assert len(list((disk / "pending").glob("*.vrf"))) == 1
    finally:
        httpd.shutdown()


def test_the_sync_resends_a_lost_ack(engine, db, tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        lossy = uploads.WorkerClient(base)
        monkeypatch.setattr(lossy, "ack", lambda *_: (_ for _ in ()).throw(uploads.WorkerError("lost")))
        upload = collect(db, lossy, vrf_bytes())
        assert upload.archive_ack is None
        counts = sync.cycle(sessionmaker(bind=engine), uploads.WorkerClient(base), sync.State())
        assert counts["acks_sent"] == 1
        db.expire_all()
        assert db.get(ReplayUpload, upload.id).archive_ack == "archived"
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").exists() and not list((disk / "pending").iterdir())
        # Answered acks aren't sent again.
        assert sync.cycle(sessionmaker(bind=engine), uploads.WorkerClient(base), sync.State())["acks_sent"] == 0
    finally:
        httpd.shutdown()


def test_a_new_worker_boot_gets_the_tombstones_and_a_restored_file_goes(engine, db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        collect(db, client, vrf_bytes())
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        # As after a restored snapshot: the web app's table lists the match, the worker's copy doesn't.
        db.add(ReplayDeletion(match_uuid=MATCH_UUID, reason="asked"))
        db.commit()
        state = sync.State()
        assert sync.cycle(sessionmaker(bind=engine), client, state)["tombstones_pushed"] == 1
        assert not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        assert state.synced_boot_id == worker.archive.boot_id
        assert sync.cycle(sessionmaker(bind=engine), client, state)["tombstones_pushed"] == 0, "once per boot"
    finally:
        httpd.shutdown()


def test_the_upload_page_states_that_the_file_is_kept(db):
    request = Request({"type": "http", "method": "GET", "path": "/replays/upload", "query_string": b"",
                       "headers": [], "session": {"replay_upload_ok": True}, "client": ("10.0.0.1", 1)})
    page = routes.upload_form(request).body.decode("utf-8")
    assert "data-archive-promise" in page and "keeps the <code>.vrf</code> file" in page
    assert "then deleted" not in page and "ask, and its file and its replay are" in page


def admin_request(token: str | None) -> Request:
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": headers})


def test_the_admin_routes_exist_only_with_the_right_token(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_admin_token", None)
    with pytest.raises(HTTPException) as off:
        admin.require_admin(admin_request("anything"))
    assert off.value.status_code == 404
    monkeypatch.setattr(settings, "replay_admin_token", "s3cret")
    for given in (None, "wrong", "letmein"):
        with pytest.raises(HTTPException) as refused:
            admin.require_admin(admin_request(given))
        assert refused.value.status_code == 404
    admin.require_admin(admin_request("s3cret"))
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException):
        admin.require_admin(admin_request("s3cret"))


def test_a_deletion_removes_the_replay_and_the_files_and_blocks_the_match(db, tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    monkeypatch.setattr(settings, "replay_worker_url", base)
    try:
        client = uploads.WorkerClient(base)
        stored = collect(db, client, vrf_bytes())
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        answer = admin.delete_match({"match_uuid": MATCH_UUID.upper(), "reason": "asked"}, db)
        assert answer["replay_deleted"] and answer["worker"]["told"] and not answer["already_tombstoned"]
        assert db.query(Replay).count() == 0 and db.get(ReplayDeletion, MATCH_UUID).reason == "asked"
        assert db.get(ReplayUpload, stored.id).replay_id is None
        assert not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        assert admin.delete_match({"match_uuid": MATCH_UUID}, db)["already_tombstoned"], "idempotent"
        again = collect(db, client, vrf_bytes(), session_key="sess2")
        assert again.status == "failed" and "deleted on request" in again.error
        assert not list((disk / "pending").iterdir()) and not (disk / "archive" / f"{MATCH_UUID}.vrf").exists()
        with pytest.raises(HTTPException) as refused:
            admin.reparse({"match_uuid": MATCH_UUID}, db)
        assert refused.value.status_code == 409
    finally:
        httpd.shutdown()


def test_a_reparse_through_the_admin_routes_stores_and_keeps_the_file(db, tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    monkeypatch.setattr(settings, "replay_worker_url", base)
    try:
        stored = collect(db, uploads.WorkerClient(base), vrf_bytes())
        upload_id = admin.reparse({"match_uuid": MATCH_UUID}, db)["upload_id"]
        for _ in range(300):
            state = admin.reparse_status(upload_id, db)
            if state["status"] not in uploads.UNFINISHED:
                break
            time.sleep(0.1)
        assert state["status"] == "stored", state
        assert (state["store_outcome"], state["archive_ack"], state["replay_id"]) == ("unchanged", "kept", stored.replay_id)
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == vrf_bytes()
        with pytest.raises(HTTPException) as missing:
            admin.reparse({"match_uuid": "11111111-0000-4000-8000-000000000000"}, db)
        assert missing.value.status_code == 404
    finally:
        httpd.shutdown()


def test_the_scripts_select_and_describe_themselves(capsys):
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    import delete_replay_data
    import reparse_archive

    files = [{"match_uuid": "b", "map": "Ascent", "played_at": "2026-09-30T20:00:00+00:00"},
             {"match_uuid": "a", "map": "Haven", "played_at": None, "accepted_at": "2026-10-01T21:00:00+00:00"},
             {"match_uuid": "c", "map": "Ascent", "played_at": "2026-09-01T20:00:00+00:00"}]
    assert [e["match_uuid"] for e in reparse_archive.select(files, "ascent", None, None)] == ["c", "b"]
    assert [e["match_uuid"] for e in reparse_archive.select(files, None, "2026-09-15", None)] == ["b", "a"]
    assert [e["match_uuid"] for e in reparse_archive.select(files, None, None, "A")] == ["a"]
    for module in (delete_replay_data, reparse_archive):
        with pytest.raises(SystemExit) as shown:
            module.main(["--help"])
        assert shown.value.code == 0
    assert "usage" in capsys.readouterr().out


def test_the_sync_collects_a_finished_upload_even_with_the_archive_off(engine, db, tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        upload = uploads.create_upload(db, io.BytesIO(vrf_bytes()), "sess", None, client)
        for _ in range(300):
            if client.job(upload.worker_job_id)["status"] not in uploads.UNFINISHED:
                break
            time.sleep(0.1)
        counts = sync.cycle(sessionmaker(bind=engine), client, sync.State())
        assert counts["collected"] == 1
        db.expire_all()
        assert db.get(ReplayUpload, upload.id).status == "stored"
    finally:
        httpd.shutdown()


def test_the_sync_does_nothing_while_the_archive_is_off(engine, tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub)
    try:
        counts = sync.cycle(sessionmaker(bind=engine), uploads.WorkerClient(base), sync.State())
        assert not any(counts.values())
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- automatic re-parse attempts, the real worker
# docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, task 3. The guards one by one are in
# test_replay_upload.py, against a fake worker.

ATTEMPT = "6f1c2b9e-0d34-4c1a-9b7e-3a5d8e2f4c10"
OTHER_ATTEMPT = "7a2d3c0f-1e45-4d2b-8c8f-4b6e9f305d21"
OTHER_MATCH = "11111111-0000-4000-8000-000000000000"


def test_the_client_reads_every_coded_answer_of_the_attempt_routes(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        sha = db.get(Replay, collect(db, client, vrf_bytes()).replay_id).source_sha256
        # 404, 409 and 400 answers come back as their code, not as an exception.
        assert client.reparse_attempt(ATTEMPT)["code"] == "unknown_attempt"
        assert client.reparse(MATCH_UUID, attempt_id=ATTEMPT, expected_sha256="0" * 64)["code"] == "sha_mismatch"
        assert client.reparse(OTHER_MATCH, attempt_id=ATTEMPT, expected_sha256=sha)["code"] == "no_archived_file"
        assert client.reparse(MATCH_UUID, attempt_id="not-a-uuid", expected_sha256=sha)["code"] == "bad_request"
        assert client.reparse_attempt(ATTEMPT)["code"] == "unknown_attempt", "a definite refusal leaves no receipt"
        accepted = client.reparse(MATCH_UUID, attempt_id=ATTEMPT, expected_sha256=sha)
        assert accepted["code"] == "accepted" and accepted["job_id"] == uploads.auto_job_id(ATTEMPT)
        assert uploads.receipt_matches(accepted, ATTEMPT, {"match_uuid": MATCH_UUID, "source_sha256": sha})
        assert not uploads.receipt_matches(accepted, OTHER_ATTEMPT, {"match_uuid": MATCH_UUID, "source_sha256": sha})
        for again in (client.reparse(MATCH_UUID, attempt_id=ATTEMPT, expected_sha256=sha),
                      client.reparse_attempt(ATTEMPT), client.close_reparse_attempt(ATTEMPT, MATCH_UUID, sha)):
            assert (again["code"], again["job_id"]) == ("accepted", accepted["job_id"])
        assert client.close_reparse_attempt(OTHER_ATTEMPT, MATCH_UUID, sha)["code"] == "closed"
        assert client.reparse(MATCH_UUID, attempt_id=OTHER_ATTEMPT, expected_sha256=sha)["code"] == "closed"
        # Without an attempt id it is the manual re-parse, answered and refused as it always was.
        manual = client.reparse(MATCH_UUID)
        assert set(manual) == {"id", "status", "kind", "sha256", "size"} and not manual["id"].startswith("auto")
        with pytest.raises(uploads.WorkerRefused) as refused:
            client.reparse(OTHER_MATCH)
        assert refused.value.status == 404
    finally:
        httpd.shutdown()
    # No answer says nothing about acceptance: never a code, always an error the caller must not read as a refusal.
    down = uploads.WorkerClient("http://127.0.0.1:9", timeout_s=1)
    for call in (lambda: down.reparse(MATCH_UUID, attempt_id=ATTEMPT, expected_sha256="0" * 64),
                 lambda: down.reparse_attempt(ATTEMPT), lambda: down.close_reparse_attempt(ATTEMPT, MATCH_UUID, "0" * 64)):
        with pytest.raises(uploads.WorkerError) as error:
            call()
        assert not isinstance(error.value, uploads.WorkerRefused)


def start_archive_with_control(tmp_path, stub):  # noqa: F811
    """`start_archive` with map control on, wired as replay_worker/server.py's `main` wires it: what the
    deployed worker's /health says. No control task is sent here, so no child ever starts."""
    disk = tmp_path / "disk"
    disk.mkdir(exist_ok=True)
    config = server.Settings(parser_cmd=[sys.executable, str(stub), "{vrf}", "{out}", "ok"],
                             temp_root=tmp_path / "jobs", archive_dir=disk, archive_require_mount=False,
                             archive_total_bytes=500 * arc.GB)
    config.temp_root.mkdir(exist_ok=True)
    control = server.ControlRunner(config)
    worker = server.Worker(config, on_parse=control.preempt)
    control.idle = worker.idle
    httpd = server.make_server(worker, control=control)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return worker, httpd, f"http://127.0.0.1:{httpd.server_address[1]}", disk


def test_an_automatic_attempt_runs_end_to_end_and_the_sync_collects_it(engine, db, tmp_path, stub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(uploads, "_site_recipe", None)  # the recipe this checkout's code really stamps
    worker, httpd, base, disk = start_archive_with_control(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        replay = db.get(Replay, collect(db, client, vrf_bytes()).replay_id)
        target, sha = replay.recipe, replay.source_sha256
        assert target == worker.recipe == uploads.site_recipe(), "the stub parses to the recipe both sides name"
        assert uploads.worker_off_reason(client.health(), target) is None
        replay.recipe = "old"   # as if stored by an earlier deploy
        db.add(ReplayUpload(id=ATTEMPT, status="queued", source_sha256=sha, session_key=uploads.auto_tag(target),
                            created_at=uploads.datetime.now(uploads.timezone.utc),
                            auto_context=uploads.new_auto_context(replay, target)))
        db.commit()
        factory = sessionmaker(bind=engine)
        # Reserved, not sent: the sync looks the receipt up and starts nothing.
        assert sync.cycle(factory, client, sync.State())["collected"] == 0
        assert client.reparse_attempt(ATTEMPT)["code"] == "unknown_attempt"
        assert not (disk / "jobs" / uploads.auto_job_id(ATTEMPT)).exists()
        assert client.reparse(MATCH_UUID, attempt_id=ATTEMPT, expected_sha256=sha)["code"] == "accepted"
        for _ in range(300):
            if sync.cycle(factory, client, sync.State())["collected"]:
                break
            time.sleep(0.1)
        db.expire_all()
        row = db.get(ReplayUpload, ATTEMPT)
        assert (row.status, row.store_outcome, row.archive_ack, row.error) == ("stored", "replaced", "kept", None)
        assert row.auto_context["phase"] == "accepted" and row.worker_job_id == uploads.auto_job_id(ATTEMPT)
        stored = db.query(Replay).one()
        assert (stored.recipe, stored.source, stored.id) == (target, "upload", row.replay_id)
        assert (disk / "archive" / f"{MATCH_UUID}.vrf").read_bytes() == vrf_bytes(), "the archived file stays"
        assert sync.cycle(factory, client, sync.State())["collected"] == 0
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("receipt_write_fails", [False, True])
def test_the_automatic_step_recovers_a_lost_reply_through_the_real_worker(engine, db, tmp_path, stub, monkeypatch,
                                                                      receipt_write_fails):  # noqa: F811
    """Task 5 of the same plan against the real worker: the step reserves, the worker accepts and its reply
    is lost, and later passes (with no memory of the first) find the same attempt, collect its one job and
    replace the replay."""
    from app.services import replay_reparse_auto as auto

    monkeypatch.setattr(uploads, "_site_recipe", None)
    monkeypatch.setattr(settings, "replay_reparse_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    worker, httpd, base, disk = start_archive_with_control(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        replay = db.get(Replay, collect(db, client, vrf_bytes()).replay_id)
        target, old_id = replay.recipe, replay.id
        monkeypatch.setattr(auto, "_builds", frozenset({replay.game_branch}))
        replay.recipe = "old"   # as if stored by an earlier deploy
        db.commit()
        factory = sessionmaker(bind=engine)
        parsed, failures = [], []
        process, write_receipt = worker._process, worker.archive.write_receipt

        def count_parse(job):
            parsed.append(job.id)
            return process(job)

        def fail_once(attempt_hex, receipt):
            if receipt_write_fails and receipt["state"] == "accepted" and not failures:
                failures.append(attempt_hex)
                raise OSError("injected acceptance receipt failure")
            write_receipt(attempt_hex, receipt)

        monkeypatch.setattr(worker, "_process", count_parse)
        monkeypatch.setattr(worker.archive, "write_receipt", fail_once)

        class LosesTheReply(uploads.WorkerClient):
            def reparse(self, *args, **kwargs):
                super().reparse(*args, **kwargs)
                raise uploads.WorkerError("the replay worker is unreachable")

        counts: dict = {}
        assert auto.step(factory, LosesTheReply(base), auto.Memo(), 0, time.time(), counts) == {old_id}
        assert (counts["reparse_reserved"], counts["reparse_sent"]) == (1, 0)
        row = db.query(ReplayUpload).filter(ReplayUpload.session_key == uploads.auto_tag(target)).one()
        attempt = row.id
        assert row.auto_context["phase"] == "reserved" and row.worker_job_id is None
        assert client.reparse_attempt(attempt)["job_id"] == uploads.auto_job_id(attempt), "the worker did accept it"
        totals: dict = {}
        for _ in range(300):
            counts = {}
            auto.step(factory, client, auto.Memo(), 0, time.time(), counts)
            for name, n in counts.items():
                totals[name] = totals.get(name, 0) + n
            if totals["reparse_finished"]:
                break
            time.sleep(0.1)
        db.expire_all()
        row = db.get(ReplayUpload, attempt)
        assert (row.status, row.store_outcome, row.archive_ack, row.error) == ("stored", "replaced", "kept", None)
        assert (totals["reparse_recovered"], totals["reparse_reserved"], totals["reparse_sent"]) == (1, 0, 0)
        assert db.query(ReplayUpload).filter(ReplayUpload.session_key == uploads.auto_tag(target)).count() == 1
        stored = db.query(Replay).one()
        assert (stored.recipe, stored.source, stored.id) == (target, "upload", row.replay_id)
        again = client.reparse(MATCH_UUID, attempt_id=attempt, expected_sha256=stored.source_sha256)
        assert (again["code"], again["job_id"]) == ("accepted", uploads.auto_job_id(attempt)), "never a second job"
        assert parsed == [uploads.auto_job_id(attempt)]
        assert bool(failures) is receipt_write_fails
        assert auto.unfinished_attempts(db) == []
        # Restoration remains a separate gate. Once the owner deletes the first replay, a different
        # stale uploaded replay can be reserved: this I/O failure did not leave the site-wide gate stuck.
        from test_replay_reparse_auto import add_replay

        db.add(ReplayDeletion(match_uuid=MATCH_UUID, reason="synthetic queue test"))
        waiting = add_replay(db, 2, build=stored.game_branch)
        data = vrf_bytes().replace(MATCH_UUID.upper().encode("utf-16-le"),
                                   waiting.match_uuid.upper().encode("utf-16-le"))
        data = data.replace(MATCH_UUID.encode("ascii"), waiting.match_uuid.encode("ascii"))
        waiting.source_sha256 = hashlib.sha256(data).hexdigest()
        db.commit()
        worker.archive.path_of(waiting.match_uuid).write_bytes(data)
        worker.archive.index[waiting.match_uuid] = {"sha256": waiting.source_sha256, "size": len(data),
                                                   "accepted_at": "2026-10-07T00:00:00+00:00"}
        counts = {}
        auto.step(factory, client, auto.Memo(), 0, time.time() + auto.SETTLE_S + 1, counts)
        assert counts["reparse_reserved"] == counts["reparse_sent"] == 1
        next_attempt = auto.unfinished_attempts(db)[0]
        assert next_attempt.auto_context["selected_replay_id"] == waiting.id
        from test_replay_worker import wait

        assert wait(base, next_attempt.worker_job_id)["status"] == "done"
        assert uploads.collect_auto(db, next_attempt.id, client) == "finished"
        assert db.get(ReplayUpload, next_attempt.id).status == "stored"
        assert parsed == [uploads.auto_job_id(attempt), next_attempt.worker_job_id]
    finally:
        httpd.shutdown()

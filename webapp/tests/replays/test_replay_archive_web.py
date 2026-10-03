"""The web side of the .vrf archive (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1):
the ack sent after a store, `kept_existing` shown to its uploader as stored, the re-send and tombstone sync, and the admin
routes. Against the real worker with the stub parser and its archive in a temp folder; sqlite store."""

import io
import sys
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

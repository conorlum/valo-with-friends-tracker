"""The web side of the .vrf archive (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1):
the ack sent after a store, `kept_existing` shown as such, the re-send and tombstone sync, and the admin
routes. Against the real worker with the stub parser and its archive in a temp folder; sqlite store."""

import io
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_store import TABLES  # noqa: E402
from test_replay_worker import start, stub, vrf_bytes  # noqa: E402,F401  (fixture)
from test_replay_worker_archive import start_archive  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayUpload  # noqa: E402
from app.routers import replays as routes  # noqa: E402
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


def test_a_second_recording_kept_out_is_shown_as_such_and_its_file_dropped(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        first = collect(db, client, vrf_bytes())
        other = collect(db, client, vrf_bytes() + b"\0" * 16, session_key="sess2")
        assert (other.status, other.store_outcome, other.archive_ack) == ("stored", "kept_existing", "deleted")
        body = routes._status_body(db, other)
        assert body["kept_existing"] is True and "did not link" in body["error"]
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

"""Stage 3, the web side of the friends-only upload: the code gate, the limits, the worker client
and store-on-completion, against the real worker with a stub parser (tests/replays/
test_replay_worker.py's) and a throwaway sqlite store. Routes are called directly."""

import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from replay_synthetic import SyntheticMatch  # noqa: E402
from test_replay_store import TABLES  # noqa: E402
from test_replay_worker import start, stub  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.replay import Replay, ReplayUpload  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_upload as uploads  # noqa: E402


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", "letmein")
    monkeypatch.setattr(settings, "replay_worker_url", "http://127.0.0.1:9")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[*TABLES, ReplayUpload.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def vrf_bytes() -> bytes:
    return SyntheticMatch(shape="swiftplay").vrf_bytes


def request(session: dict | None = None, accept: str | None = None) -> Request:
    headers = [(b"accept", accept.encode())] if accept else []
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": headers,
                    "session": {} if session is None else session, "client": ("10.0.0.1", 1234)})


def wait_until_done(client, job_id: str) -> None:
    for _ in range(200):
        if client.job(job_id)["status"] not in uploads.UNFINISHED:
            return
        time.sleep(0.1)
    raise AssertionError("the worker never finished the job")


def status_of(call) -> int:
    with pytest.raises(HTTPException) as raised:
        call()
    return raised.value.status_code


def test_upload_is_off_without_a_code_a_worker_or_outside_the_friends_site(db, monkeypatch):
    assert uploads.upload_enabled()
    monkeypatch.setattr(settings, "demo_mode", True)
    assert not uploads.upload_enabled()
    assert status_of(lambda: routes.upload_form(request())) == 404
    assert status_of(lambda: routes.upload_code(request(), "letmein")) == 404
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_upload_code", None)
    assert status_of(lambda: routes.upload_form(request())) == 404


def test_the_code_is_checked_and_remembered_in_the_session(db):
    session = {}
    assert routes.upload_code(request(session), "wrong").status_code == 403 and not session
    assert routes.upload_code(request(session), "letmein").status_code == 303 and session["replay_upload_ok"]
    assert uploads.code_matches("letmein") and not uploads.code_matches("") and not uploads.code_matches(None)


def test_the_guide_shows_the_code_is_unlisted_and_is_off_with_the_upload(db, monkeypatch):
    assert status_of(lambda: routes.upload_guide(request(), "anything")) == 404, "no key set: no page"
    monkeypatch.setattr(settings, "replay_upload_guide_key", "s3cret-path")
    assert status_of(lambda: routes.upload_guide(request(), "wrong")) == 404
    response = routes.upload_guide(request(), "s3cret-path")
    page = response.body.decode("utf-8")
    assert "<code>letmein</code>" in page and 'href="/replays/upload"' in page
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "upload/guide" not in routes.upload_form(request(), db).body.decode("utf-8"), "nothing links to it"
    for shot in ("1-match-history", "2-address-bar", "3-demos-folder", "4-select-replays"):
        assert f"/static/img/upload_guide/{shot}.png" in page
        assert (Path(routes.__file__).parents[1] / "static" / "img" / "upload_guide" / f"{shot}.png").is_file()
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: routes.upload_guide(request(), "s3cret-path")) == 404


def test_an_upload_without_the_code_is_refused(db):
    class FakeFile:
        file = io.BytesIO(vrf_bytes())

    assert routes.upload_file(request({}), FakeFile(), db).status_code == 403
    assert db.query(ReplayUpload).count() == 0


def test_a_text_file_and_an_oversized_file_are_refused_with_a_reason(db, monkeypatch):
    with pytest.raises(ValueError, match="not a Valorant replay"):
        uploads.create_upload(db, io.BytesIO(b"hello, not a replay"), "s", "ip", uploads.client())
    monkeypatch.setattr(settings, "replay_upload_max_bytes", 10)
    with pytest.raises(ValueError, match="over the"):
        uploads.create_upload(db, io.BytesIO(vrf_bytes()), "s", "ip", uploads.client())
    assert db.query(ReplayUpload).count() == 0


def test_the_eleventh_upload_in_an_hour_and_a_sixth_unfinished_one_are_refused(db):
    now = datetime.now(timezone.utc)
    for i in range(uploads.UPLOADS_PER_HOUR):
        db.add(ReplayUpload(id=f"00000000-0000-4000-8000-{i:012d}", status="stored", session_key="s",
                            client_ip="ip", created_at=now - timedelta(minutes=5)))
    db.commit()
    with pytest.raises(uploads.LimitExceeded, match="an hour"):
        uploads.check_limits(db, "s", "other-ip")
    with pytest.raises(uploads.LimitExceeded, match="an hour"):
        uploads.check_limits(db, "other-session", "ip")  # per IP too
    uploads.check_limits(db, "other-session", "other-ip")
    uploads.check_limits(db, "s", "ip", now=now + timedelta(hours=2))  # an hour later it's fine again
    # A batch: up to MAX_UNFINISHED of one session's uploads may be in progress at once.
    for i in range(uploads.MAX_UNFINISHED - 1):
        db.add(ReplayUpload(id=f"00000000-0000-4000-8000-0000000ff{i:03d}", status="parsing", session_key="t",
                            client_ip=f"x{i}", created_at=now - timedelta(hours=3)))
    db.commit()
    uploads.check_limits(db, "t", "y")
    db.add(ReplayUpload(id="00000000-0000-4000-8000-00000000ffff", status="queued", session_key="t",
                        client_ip="x", created_at=now - timedelta(hours=3)))
    db.commit()
    with pytest.raises(uploads.LimitExceeded, match=f"{uploads.MAX_UNFINISHED} uploads at a time"):
        uploads.check_limits(db, "t", "y")


def test_an_upload_parses_on_the_worker_and_is_stored_unlinked(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, "ok")
    try:
        client = uploads.WorkerClient(base)
        upload = uploads.create_upload(db, io.BytesIO(vrf_bytes()), "sess", "ip", client)
        assert upload.status == "parsing" and upload.worker_job_id
        for _ in range(200):
            upload = uploads.refresh_job(db, upload, client)
            if upload.status not in uploads.UNFINISHED:
                break
            time.sleep(0.1)
        assert upload.status == "stored", upload.error
        replay = db.get(Replay, upload.replay_id)
        assert replay.source == "upload" and replay.link_status == "unlinked"
        assert replay.source_sha256 == upload.source_sha256
        assert not list((tmp_path / "jobs").iterdir()), "the worker's temp folder is empty after the job"
        # The same file again: stored once, the second is a no-op.
        again = uploads.create_upload(db, io.BytesIO(vrf_bytes()), "sess2", "ip2", client)
        for _ in range(200):
            again = uploads.refresh_job(db, again, client)
            if again.status not in uploads.UNFINISHED:
                break
            time.sleep(0.1)
        assert again.status == "stored" and again.replay_id == replay.id and db.query(Replay).count() == 1
    finally:
        httpd.shutdown()


def test_a_failed_parse_and_a_stuck_job_fail_with_a_plain_reason(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, "fail")
    try:
        client = uploads.WorkerClient(base)
        upload = uploads.create_upload(db, io.BytesIO(vrf_bytes()), "sess", "ip", client)
        for _ in range(200):
            upload = uploads.refresh_job(db, upload, client)
            if upload.status not in uploads.UNFINISHED:
                break
            time.sleep(0.1)
        assert upload.status == "failed" and "parse failed" in upload.error
    finally:
        httpd.shutdown()
    stuck = ReplayUpload(id="00000000-0000-4000-8000-00000000eeee", status="parsing", worker_job_id="gone",
                         session_key="z", created_at=datetime.now(timezone.utc) - uploads.STUCK_AFTER - timedelta(minutes=1))
    db.add(stuck)
    db.commit()
    stuck = uploads.refresh_job(db, stuck, uploads.WorkerClient("http://127.0.0.1:9", timeout_s=1))
    assert (stuck.status, stuck.error) == ("failed", "failed: please re-upload")


def test_an_unreachable_worker_fails_the_upload_at_once(db):
    with pytest.raises(uploads.WorkerError):
        uploads.create_upload(db, io.BytesIO(vrf_bytes()), "s", "ip", uploads.WorkerClient("http://127.0.0.1:9",
                                                                                            timeout_s=1))
    assert db.query(ReplayUpload).one().status == "failed"


def test_another_session_cannot_see_an_upload(db):
    db.add(ReplayUpload(id="00000000-0000-4000-8000-00000000abab", status="parsing", session_key="mine",
                        created_at=datetime.now(timezone.utc)))
    db.commit()
    other = request({"replay_upload_sid": "someone-else"})
    assert status_of(lambda: routes.upload_job(other, "00000000-0000-4000-8000-00000000abab", db)) == 404


# ---------------------------------------------------------------- batch upload


def test_a_finished_upload_is_collected_with_no_page_open(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, "ok")
    try:
        client = uploads.WorkerClient(base)
        upload = uploads.create_upload(db, io.BytesIO(vrf_bytes()), "sess", "ip", client)
        wait_until_done(client, upload.worker_job_id)
        factory = sessionmaker(bind=db.get_bind())
        assert uploads.collect_unfinished(factory, client) == 1
        db.expire_all()
        assert db.get(ReplayUpload, upload.id).status == "stored"
        assert db.query(Replay).count() == 1
        assert uploads.collect_unfinished(factory, client) == 0, "a collected upload is not stored twice"
    finally:
        httpd.shutdown()


def test_an_upload_still_streaming_to_the_worker_is_left_alone_until_it_is_stuck(db):
    now = datetime.now(timezone.utc)
    db.add(ReplayUpload(id="00000000-0000-4000-8000-00000000cc01", status="queued", session_key="s",
                        created_at=now - timedelta(minutes=2)))
    db.add(ReplayUpload(id="00000000-0000-4000-8000-00000000cc02", status="queued", session_key="s",
                        created_at=now - uploads.STUCK_AFTER - timedelta(minutes=1)))
    db.commit()
    factory = sessionmaker(bind=db.get_bind())
    assert uploads.collect_unfinished(factory, uploads.WorkerClient("http://127.0.0.1:9", timeout_s=1)) == 1
    db.expire_all()
    assert db.get(ReplayUpload, "00000000-0000-4000-8000-00000000cc01").status == "queued"
    old = db.get(ReplayUpload, "00000000-0000-4000-8000-00000000cc02")
    assert (old.status, old.error) == ("failed", "failed: please re-upload")


def test_a_job_waiting_in_the_worker_queue_gets_longer_before_it_is_stuck(db):
    class QueuedWorker:
        def job(self, job_id):
            return {"id": job_id, "status": "queued"}

    now = datetime.now(timezone.utc)
    waiting = ReplayUpload(id="00000000-0000-4000-8000-00000000dd01", status="parsing", worker_job_id="j1",
                           session_key="s", created_at=now - uploads.STUCK_AFTER - timedelta(minutes=1))
    forgotten = ReplayUpload(id="00000000-0000-4000-8000-00000000dd02", status="parsing", worker_job_id="j2",
                             session_key="s", created_at=now - uploads.QUEUED_STUCK_AFTER - timedelta(minutes=1))
    db.add_all([waiting, forgotten])
    db.commit()
    assert uploads.refresh_job(db, waiting, QueuedWorker()).status == "parsing"
    assert uploads.refresh_job(db, forgotten, QueuedWorker()).status == "failed"


def test_the_upload_post_answers_json_for_the_batch_page(db, tmp_path, stub, monkeypatch):  # noqa: F811
    worker, httpd, base = start(tmp_path, stub, "ok")
    monkeypatch.setattr(settings, "replay_worker_url", base)
    session = {"replay_upload_ok": True}

    class FakeFile:
        def __init__(self, data):
            self.file = io.BytesIO(data)

    try:
        response = routes.upload_file(request(session, "application/json"), FakeFile(vrf_bytes()), db)
        body = json.loads(response.body)
        assert response.status_code == 200 and body["upload_id"]
        assert body["status_url"] == f"/replays/uploads/{body['upload_id']}/status"
        refused = routes.upload_file(request(session, "application/json"), FakeFile(b"not a replay"), db)
        assert refused.status_code == 400 and "not a valorant replay" in json.loads(refused.body)["error"].lower()
        # Without the header the form post still redirects to the job page.
        plain = routes.upload_file(request(session), FakeFile(b"not a replay"), db)
        assert plain.status_code == 400 and plain.media_type == "text/html"
    finally:
        httpd.shutdown()


def test_the_upload_page_lists_this_sessions_recent_uploads(db):
    now = datetime.now(timezone.utc)
    db.add_all([
        ReplayUpload(id="00000000-0000-4000-8000-00000000ee01", status="parsing", session_key="mine",
                     size_bytes=76_000_000, created_at=now - timedelta(minutes=3)),
        ReplayUpload(id="00000000-0000-4000-8000-00000000ee02", status="stored", session_key="mine",
                     created_at=now - timedelta(days=2)),
        ReplayUpload(id="00000000-0000-4000-8000-00000000ee03", status="parsing", session_key="theirs",
                     created_at=now),
    ])
    db.commit()
    response = routes.upload_form(request({"replay_upload_ok": True, "replay_upload_sid": "mine"}), db)
    assert [row["id"] for row in response.context["uploads"]] == ["00000000-0000-4000-8000-00000000ee01"]
    assert b"00000000-0000-4000-8000-00000000ee01" in response.body
    assert routes.upload_form(request({}), db).context["uploads"] == []

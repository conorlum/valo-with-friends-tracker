"""Stage 3, the web side of the friends-only upload: the code gate, the limits, the worker client
and store-on-completion, against the real worker with a stub parser (tests/replays/
test_replay_worker.py's) and a throwaway sqlite store. Routes are called directly."""

import io
import json
import sys
import threading
import time
from dataclasses import replace
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
from test_replay_store import TABLES, add_match, condensed, pg  # noqa: E402,F401  (fixtures)
from test_replay_worker import start, stub  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayUpload  # noqa: E402
from app.replays import store  # noqa: E402
from app.services import replay_archive_sync as sync  # noqa: E402
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


def test_many_finished_uploads_are_fine_but_a_sixth_unfinished_one_is_refused(db):
    now = datetime.now(timezone.utc)
    # No hourly cap: any number of finished uploads from one session in the last hour is fine.
    for i in range(20):
        db.add(ReplayUpload(id=f"00000000-0000-4000-8000-{i:012d}", status="stored", session_key="s",
                            client_ip="ip", created_at=now - timedelta(minutes=5)))
    db.commit()
    uploads.check_limits(db, "s")
    # A batch: up to MAX_UNFINISHED of one session's uploads may be in progress at once.
    for i in range(uploads.MAX_UNFINISHED - 1):
        db.add(ReplayUpload(id=f"00000000-0000-4000-8000-0000000ff{i:03d}", status="parsing", session_key="t",
                            client_ip=f"x{i}", created_at=now - timedelta(hours=3)))
    db.commit()
    uploads.check_limits(db, "t")
    db.add(ReplayUpload(id="00000000-0000-4000-8000-00000000ffff", status="queued", session_key="t",
                        client_ip="x", created_at=now - timedelta(hours=3)))
    db.commit()
    with pytest.raises(uploads.LimitExceeded, match=f"{uploads.MAX_UNFINISHED} uploads at a time"):
        uploads.check_limits(db, "t")


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


# ---------------------------------------------------------------- automatic re-parse attempts
# docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, task 3: the shared guarded collector. A fake
# worker answers here; the real one is in test_replay_archive_web.py.

ATTEMPT = "6f1c2b9e-0d34-4c1a-9b7e-3a5d8e2f4c10"
ATTEMPT_JOB = "auto6f1c2b9e0d344c1a9b7e3a5d8e2f4c10"


def result_of(replay) -> dict:
    """A finished job's result, as replay_worker/server.py builds it."""
    import base64

    return {"match_uuid": replay.match_uuid, "map_name": replay.map_name, "game_branch": replay.game_branch,
            "source_sha256": replay.source_sha256, "recipe": replay.recipe, "hz": replay.hz,
            "round_count": replay.round_count,
            "rounds": {str(n): base64.b64encode(data).decode("ascii") for n, data in replay.encoded_rounds().items()},
            "players": replay.players, "link_inputs": replay.link_inputs, "report": replay.report}


class AutoWorker:
    """What the collector asks a worker: its health, the attempt's receipt, the job, and the ack afterwards."""

    def __init__(self, recipe: str, result: dict | None = None):
        self.health_body = {"ok": True, "recipe": recipe, "archive": {"enabled": True, "reparse_protocol": 1},
                            "control": {"enabled": True, "gaps_protocol": 1}}
        self.receipt = {"code": "unknown_attempt"}
        self.job_body = {"status": "done", "result": result} if result else {"status": "parsing"}
        self.ack_error = None
        self.asked, self.acks = [], []

    def health(self):
        self.asked.append("health")
        if self.health_body is None:
            raise uploads.WorkerError("down")
        return self.health_body

    def reparse_attempt(self, attempt_id):
        self.asked.append("lookup")
        if self.receipt is None:
            raise uploads.WorkerError("down")
        return self.receipt

    def job(self, job_id):
        self.asked.append("job")
        if isinstance(self.job_body, Exception):
            raise self.job_body
        return {"id": job_id, **self.job_body}

    def ack(self, job_id, body):
        if self.ack_error is not None:
            raise self.ack_error
        self.acks.append((job_id, body))
        return {"archived": True, "result": "kept"}


def accepted_receipt(context: dict, job_status: str = "done", **changed) -> dict:
    return {"code": "accepted", "state": "accepted", "attempt_id": ATTEMPT, "job_id": ATTEMPT_JOB,
            "match_uuid": context["match_uuid"], "sha256": context["source_sha256"], "size": 1234,
            "job_status": job_status, **changed}


def reserve(session, replay, target: str, phase: str = "reserved", created: datetime | None = None) -> dict:
    """An attempt row as the automatic step commits it before any worker request; `accepted` is the row
    after the worker's receipt was bound. Returns its context."""
    context = uploads.new_auto_context(replay, target)
    upload = ReplayUpload(id=ATTEMPT, status="queued", source_sha256=replay.source_sha256,
                          session_key=uploads.auto_tag(target), created_at=created or datetime.now(timezone.utc),
                          auto_context=context)
    if phase == "accepted":
        upload.auto_context = {**context, "phase": "accepted"}
        upload.status, upload.worker_job_id = "parsing", ATTEMPT_JOB
    session.add(upload)
    session.commit()
    return context


def make_stale(session, condensed):  # noqa: F811
    """A linked uploaded replay stored under an older recipe than `condensed`'s."""
    add_match(session)
    return session.get(Replay, store.store_replay(session, replace(condensed, recipe="old"), source="upload").replay_id)


@pytest.fixture
def stale(db, condensed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(uploads, "_site_recipe", condensed.recipe)  # this site's code stamps the result's recipe
    return make_stale(db, condensed)


def attempt_row(session) -> ReplayUpload:
    session.expire_all()
    return session.get(ReplayUpload, ATTEMPT)


def replays(session) -> list[tuple]:
    session.expire_all()
    return [(r.id, r.recipe, r.source, r.source_sha256) for r in session.query(Replay).order_by(Replay.id)]


def test_the_tag_the_job_id_and_the_context_helpers():
    assert uploads.auto_recipe(uploads.auto_tag("r1")) == "r1"
    assert uploads.auto_recipe("sess") is None and uploads.auto_recipe(None) is None
    assert uploads.auto_recipe(uploads.AUTO_PREFIX) is None
    assert uploads.auto_job_id(ATTEMPT) == uploads.auto_job_id(ATTEMPT.upper()) == ATTEMPT_JOB
    row = ReplayUpload(id=ATTEMPT, session_key="sess")
    assert not uploads.is_auto(row) and uploads.auto_context(row) is None
    good = {"version": 1, "match_uuid": "m", "selected_replay_id": 3, "source_recipe": "a", "source_sha256": "s",
            "target_recipe": "b", "phase": "reserved"}
    row.auto_context = good
    assert uploads.auto_context(row) == good
    for broken in ({**good, "version": 2}, {**good, "phase": "done"}, {**good, "selected_replay_id": "3"},
                   {**good, "selected_replay_id": True}, {**good, "source_sha256": ""}, "text", None):
        row.auto_context = broken
        assert uploads.auto_context(row) is None


def test_the_worker_check_requires_everything_explicitly():
    good = AutoWorker("r1").health_body
    assert uploads.worker_off_reason(good, "r1") is None
    assert "unreachable" in uploads.worker_off_reason(None, "r1")
    assert "recipe skew" in uploads.worker_off_reason(good, "r2")
    assert "recipe skew" in uploads.worker_off_reason({**good, "recipe": None}, "r1")
    for part, value, said in (("archive", {"enabled": False, "reparse_protocol": 1}, "archive is off"),
                              ("archive", {"enabled": True}, "re-parse protocol"),
                              ("archive", {"enabled": True, "reparse_protocol": 2}, "re-parse protocol"),
                              ("archive", {"enabled": True, "reparse_protocol": True}, "re-parse protocol"),
                              ("archive", None, "archive is off"),
                              ("control", {"enabled": False, "gaps_protocol": 1}, "map control is off"),
                              ("control", {"gaps_protocol": 1}, "map control is off"),
                              ("control", {"enabled": True}, "timing-gaps protocol"),
                              ("control", {"enabled": True, "gaps_protocol": "1"}, "timing-gaps protocol")):
        assert said in uploads.worker_off_reason({**good, part: value}, "r1"), (part, value)


def test_an_accepted_attempt_replaces_the_selected_replay_and_settles_with_it(db, condensed, stale):  # noqa: F811
    context = reserve(db, stale, condensed.recipe)
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    worker.receipt = accepted_receipt(context)
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    assert uploads.collect_auto(db, ATTEMPT, worker, now) == "finished"
    assert worker.asked == ["health", "lookup", "job"], "the receipt is read before the job is polled"
    row = attempt_row(db)
    [(replay_id, recipe, source, _)] = replays(db)
    assert (row.status, row.store_outcome, row.error, row.replay_id) == ("stored", "replaced", None, replay_id)
    assert (recipe, source) == (condensed.recipe, "upload")
    assert (row.worker_job_id, row.size_bytes, row.archive_ack) == (ATTEMPT_JOB, 1234, "kept")
    assert row.finished_at.replace(tzinfo=timezone.utc) == now, "the injected clock stamps the settlement"
    assert row.auto_context == {**context, "phase": "accepted"}, "the snapshot is kept as it was selected"
    [(job_id, ack)] = worker.acks
    assert job_id == ATTEMPT_JOB and (ack["outcome"], ack["replay_id"]) == ("replaced", replay_id)
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished" and len(worker.acks) == 1


@pytest.mark.parametrize("collector", ["poll", "sync"])
def test_a_site_on_a_new_recipe_never_stores_an_old_target_result(db, condensed, stale, monkeypatch, collector):  # noqa: F811
    """The first review's finding 2: an attempt reserved for recipe A finishes after the site (and then the
    worker) moved to B. Whoever collects it, the A result is not written and the replay stays as it was."""
    context = reserve(db, stale, condensed.recipe, phase="accepted")
    monkeypatch.setattr(uploads, "_site_recipe", "B")
    worker = AutoWorker("B", result_of(condensed))
    worker.receipt = accepted_receipt(context)
    before = replays(db)
    if collector == "poll":
        assert uploads.refresh_job(db, db.get(ReplayUpload, ATTEMPT), worker).status == "failed"
    else:
        assert sync.cycle(sessionmaker(bind=db.get_bind()), worker, sync.State())["collected"] == 1
    row = attempt_row(db)
    assert (row.status, row.store_outcome, row.replay_id) == ("failed", "failed", None)
    assert row.error == "could not store it: another recipe"
    assert replays(db) == before and before[0][1] == "old"
    assert worker.acks[0][1]["outcome"] == "failed"


HANDSHAKES = {
    "an older site sees the newer worker": ("A", {"recipe": "B"}, "recipe skew"),
    "the site moved first": ("B", {"recipe": "A"}, "recipe skew"),
    "the worker is unreachable": ("B", None, "unreachable"),
    "the archive is off": ("B", {"archive": {"enabled": False}}, "archive is off"),
    "worker control is off": ("B", {"control": {"enabled": False, "gaps_protocol": 1}}, "map control is off"),
    "no gaps protocol": ("B", {"control": {"enabled": True}}, "timing-gaps protocol"),
    "another re-parse protocol": ("B", {"archive": {"enabled": True, "reparse_protocol": 2}}, "re-parse protocol"),
}


@pytest.mark.parametrize("phase", ["reserved", "accepted"])
@pytest.mark.parametrize("case", sorted(HANDSHAKES))
def test_a_collector_whose_handshake_fails_leaves_the_attempt_exactly_as_it_is(db, condensed, stale, monkeypatch,  # noqa: F811
                                                                              case, phase):
    site, health, said = HANDSHAKES[case]
    context = reserve(db, stale, "B", phase=phase, created=datetime.now(timezone.utc) - timedelta(hours=5))
    monkeypatch.setattr(uploads, "_site_recipe", site)
    worker = AutoWorker("B", result_of(replace(condensed, recipe="B")))
    worker.receipt = accepted_receipt(context)
    worker.health_body = None if health is None else {**worker.health_body, **health}
    before = replays(db)
    answer = uploads.collect_auto(db, ATTEMPT, worker)
    assert answer.startswith("deferred: ") and said in answer
    assert worker.asked == ["health"], "nothing is looked up, polled, closed or stored"
    row = attempt_row(db)
    assert (row.status, row.error, row.finished_at, row.store_outcome) == (
        "queued" if phase == "reserved" else "parsing", None, None, None)
    assert row.auto_context["phase"] == phase and replays(db) == before
    # The archive sync and a status poll are the same collector.
    assert uploads.collect_unfinished(sessionmaker(bind=db.get_bind()), worker) == 0
    assert uploads.refresh_job(db, attempt_row(db), worker).status in uploads.UNFINISHED


@pytest.mark.parametrize("receipt", [{"code": "unknown_attempt"}, None,
                                     {"code": "preparing", "state": "preparing", "attempt_id": ATTEMPT}])
def test_a_reserved_attempt_is_never_polled_as_a_job_or_timed_out(db, condensed, stale, receipt):  # noqa: F811
    long_ago = datetime.now(timezone.utc) - timedelta(hours=6)
    reserve(db, stale, condensed.recipe, created=long_ago)
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    worker.receipt = receipt
    factory = sessionmaker(bind=db.get_bind())
    assert uploads.collect_auto(db, ATTEMPT, worker) == "reserved"
    assert uploads.collect_unfinished(factory, worker) == 0
    assert uploads.refresh_job(db, attempt_row(db), worker).status == "queued"
    assert "job" not in worker.asked and not worker.acks
    row = attempt_row(db)
    assert (row.status, row.error, row.worker_job_id, row.auto_context["phase"]) == ("queued", None, None, "reserved")


@pytest.mark.parametrize("status", ["queued", "parsing"])
def test_an_accepted_job_still_live_is_waited_for_past_the_upload_cutoffs(db, condensed, stale, status):  # noqa: F811
    context = reserve(db, stale, condensed.recipe, phase="accepted",
                      created=datetime.now(timezone.utc) - uploads.QUEUED_STUCK_AFTER - timedelta(hours=3))
    worker = AutoWorker(condensed.recipe)
    worker.receipt, worker.job_body = accepted_receipt(context, status), {"status": status}
    assert uploads.collect_auto(db, ATTEMPT, worker) == "pending"
    assert uploads.collect_unfinished(sessionmaker(bind=db.get_bind()), worker) == 0
    assert attempt_row(db).status == "parsing", "the one-at-a-time gate stays held"


def test_a_closed_receipt_settles_a_refusal_that_used_no_parse(db, condensed, stale):  # noqa: F811
    context = reserve(db, stale, condensed.recipe)
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    worker.receipt = {"code": "closed", "state": "closed", "attempt_id": ATTEMPT}
    before = replays(db)
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert (row.status, row.store_outcome, row.worker_job_id) == ("failed", None, None)
    assert row.auto_context == {**context, "phase": "refused_before_acceptance"}
    assert "job" not in worker.asked and not worker.acks and replays(db) == before


@pytest.mark.parametrize("changed", [{"sha256": "f" * 64}, {"match_uuid": "11111111-0000-4000-8000-000000000000"},
                                     {"job_id": "auto" + "0" * 32}])
def test_a_receipt_that_is_not_this_attempts_is_never_bound(db, condensed, stale, changed):  # noqa: F811
    context = reserve(db, stale, condensed.recipe)
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    worker.receipt = accepted_receipt(context, **changed)
    before = replays(db)
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert row.status == "failed" and "names another file" in row.error
    assert "job" not in worker.asked and not worker.acks and replays(db) == before


@pytest.mark.parametrize("context", [None, {"version": 2}, {"version": 1, "phase": "reserved"}])
def test_an_automatic_row_without_a_valid_context_fails_and_replaces_nothing(db, condensed, stale, context):  # noqa: F811
    db.add(ReplayUpload(id=ATTEMPT, status="parsing", session_key=uploads.auto_tag(condensed.recipe),
                        worker_job_id=ATTEMPT_JOB, created_at=datetime.now(timezone.utc), auto_context=context))
    db.commit()
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    before = replays(db)
    assert uploads.collect_unfinished(sessionmaker(bind=db.get_bind()), worker) == 1
    row = attempt_row(db)
    assert row.status == "failed" and "without a valid context" in row.error
    assert worker.asked == [] and replays(db) == before


@pytest.mark.parametrize("wrong, said", [({"match_uuid": "11111111-0000-4000-8000-000000000000"}, "another match"),
                                         ({"source_sha256": "f" * 64}, "another file"),
                                         ({"recipe": "unasked"}, "another recipe")])
def test_a_result_for_another_match_file_or_recipe_is_not_stored(db, condensed, stale, wrong, said):  # noqa: F811
    context = reserve(db, stale, condensed.recipe, phase="accepted")
    worker = AutoWorker(condensed.recipe, result_of(replace(condensed, **wrong)))
    worker.receipt = accepted_receipt(context)
    before = replays(db)
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert (row.status, row.store_outcome) == ("failed", "failed") and said in row.error
    assert replays(db) == before


def test_an_unreadable_result_fails_the_attempt(db, condensed, stale):  # noqa: F811
    context = reserve(db, stale, condensed.recipe, phase="accepted")
    worker = AutoWorker(condensed.recipe, {"match_uuid": context["match_uuid"]})
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert row.status == "failed" and "unreadable result" in row.error and replays(db)[0][1] == "old"


@pytest.mark.parametrize("moved, status, outcome, said", [
    ("already at the target", "stored", "unchanged", None),
    ("to another recipe", "stored", "kept_existing", "superseded"),
    ("to another recording", "stored", "kept_existing", "superseded"),
    ("gone", "failed", "failed", "gone"),
    ("to a local ingest", "failed", "failed", "local ingest"),
    ("deleted on request", "failed", "failed", "deleted on request"),
])
def test_a_selected_replay_that_moved_on_ends_the_attempt_without_a_replacement(db, condensed, stale, moved,  # noqa: F811
                                                                               status, outcome, said):
    context = reserve(db, stale, condensed.recipe, phase="accepted")
    if moved == "already at the target":   # a manual re-parse got there first
        store.store_replay(db, condensed, source="upload")
    elif moved == "to another recipe":
        stale.recipe = "newer"
    elif moved == "to another recording":
        store.store_replay(db, replace(condensed, recipe="old", source_sha256="f" * 64), source="upload", replace=True)
    elif moved == "to a local ingest":
        stale.source = "local"
    else:
        store._delete(db, stale)
        if moved == "deleted on request":
            db.add(ReplayDeletion(match_uuid=context["match_uuid"], reason="asked"))
    db.commit()
    before = replays(db)
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert (row.status, row.store_outcome) == (status, outcome)
    assert (row.error is None) if said is None else (said in row.error)
    assert replays(db) == before, "never replaced, never recreated"
    assert row.replay_id == (before[0][0] if status == "stored" else None)
    assert worker.acks[0][1]["outcome"] == outcome


def test_a_failure_just_before_the_settlement_commit_rolls_back_the_replacement_too(db, condensed, stale,  # noqa: F811
                                                                                   monkeypatch):
    reserve(db, stale, condensed.recipe, phase="accepted")
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    before = replays(db)

    def crash(upload):
        assert upload.status == "stored" and db.query(Replay).one().recipe == condensed.recipe
        raise RuntimeError("the database went away")

    monkeypatch.setattr(uploads, "_before_settle", crash)
    with pytest.raises(RuntimeError):
        uploads.collect_auto(db, ATTEMPT, worker)
    row = attempt_row(db)
    assert (row.status, row.store_outcome, row.replay_id, row.finished_at) == ("parsing", None, None, None)
    assert replays(db) == before and not worker.acks, "the old replay and the unfinished attempt, together"
    monkeypatch.setattr(uploads, "_before_settle", None)
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    assert attempt_row(db).store_outcome == "replaced" and replays(db)[0][1] == condensed.recipe


def test_a_lost_ack_after_the_commit_is_sent_again_and_never_replaces_again(db, condensed, stale):  # noqa: F811
    reserve(db, stale, condensed.recipe, phase="accepted")
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    worker.ack_error = uploads.WorkerError("lost")
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert (row.status, row.store_outcome, row.archive_ack) == ("stored", "replaced", None)
    stored = replays(db)
    worker.ack_error, polls = None, worker.asked.count("job")
    counts = sync.cycle(sessionmaker(bind=db.get_bind()), worker, sync.State())
    assert (counts["collected"], counts["acks_sent"]) == (0, 1)
    assert attempt_row(db).archive_ack == "kept" and replays(db) == stored
    assert worker.asked.count("job") == polls, "a settled attempt is not collected again"


def test_an_accepted_job_gone_from_the_worker_settles_only_on_an_expired_receipt(db, condensed, stale):  # noqa: F811
    context = reserve(db, stale, condensed.recipe, phase="accepted")
    worker = AutoWorker(condensed.recipe)
    worker.job_body = uploads.WorkerRefused("no such job", 404, {})
    worker.receipt = None
    assert uploads.collect_auto(db, ATTEMPT, worker) == "pending"
    worker.receipt = {"code": "unknown_attempt"}
    assert uploads.collect_auto(db, ATTEMPT, worker) == "pending", "one 404 is not evidence of anything"
    worker.job_body = uploads.WorkerError("timeout")
    assert uploads.collect_auto(db, ATTEMPT, worker) == "pending"
    assert attempt_row(db).status == "parsing"
    worker.job_body = uploads.WorkerRefused("no such job", 404, {})
    worker.receipt = accepted_receipt(context, "expired")
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert row.status == "failed" and "expired" in row.error and replays(db)[0][1] == "old"


def test_an_accepted_parse_failure_fails_the_attempt_with_the_workers_reason(db, condensed, stale):  # noqa: F811
    reserve(db, stale, condensed.recipe, phase="accepted")
    worker = AutoWorker(condensed.recipe)
    worker.job_body = {"status": "failed", "error": "parse failed"}
    assert uploads.collect_auto(db, ATTEMPT, worker) == "finished"
    row = attempt_row(db)
    assert (row.status, row.error) == ("failed", uploads.REASONS["parse failed"]) and replays(db)[0][1] == "old"
    assert row.auto_context["phase"] == "accepted", "an accepted failure counts as a parse attempt"


def test_the_prefix_collects_only_automatic_rows(db, condensed, stale):  # noqa: F811
    now = datetime.now(timezone.utc)
    reserve(db, stale, condensed.recipe, phase="accepted")
    db.add(ReplayUpload(id="00000000-0000-4000-8000-00000000ff01", status="queued", session_key="sess",
                        created_at=now - uploads.STUCK_AFTER - timedelta(minutes=1)))
    db.commit()
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    factory = sessionmaker(bind=db.get_bind())
    assert uploads.collect_unfinished(factory, worker, now, session_prefix=uploads.AUTO_PREFIX) == 1
    db.expire_all()
    assert db.get(ReplayUpload, "00000000-0000-4000-8000-00000000ff01").status == "queued"
    assert attempt_row(db).store_outcome == "replaced"
    assert uploads.collect_unfinished(factory, worker, now) == 1, "without a prefix, every unfinished row"
    db.expire_all()
    assert db.get(ReplayUpload, "00000000-0000-4000-8000-00000000ff01").status == "failed"


def test_a_manual_reparse_row_keeps_the_old_path_and_a_null_context(db):
    class DoneNothing:
        def job(self, job_id):
            return {"id": job_id, "status": "failed", "error": "parse failed"}

    row = ReplayUpload(id="00000000-0000-4000-8000-00000000ff02", status="parsing", worker_job_id="j1",
                       session_key="admin-reparse", created_at=datetime.now(timezone.utc))
    db.add(row)
    db.commit()
    assert uploads.refresh_job(db, row, DoneNothing()).status == "failed"
    assert db.get(ReplayUpload, row.id).auto_context is None


# ---------------------------------------------------------------- PostgreSQL only


@pytest.fixture
def pg_stale(pg, condensed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(uploads, "_site_recipe", condensed.recipe)
    session = pg()
    replay = make_stale(session, condensed)
    context = reserve(session, replay, condensed.recipe, phase="accepted")
    session.close()
    yield pg, context
    cleanup = pg()
    cleanup.query(ReplayDeletion).delete()
    cleanup.commit()
    cleanup.close()


def in_thread(pg, worker, answers: list) -> threading.Thread:  # noqa: F811
    def run():
        session = pg()
        try:
            answers.append(uploads.collect_auto(session, ATTEMPT, worker))
        except Exception as error:  # noqa: BLE001
            answers.append(error)
        finally:
            session.rollback()
            session.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_pg_two_collectors_store_and_settle_one_attempt_once(pg_stale, condensed, monkeypatch):  # noqa: F811
    pg, _ = pg_stale  # noqa: F811
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    holding, go = threading.Event(), threading.Event()

    def pause(upload):  # the first collector stops just before its commit, holding the match lock and the row
        holding.set()
        assert go.wait(30)

    monkeypatch.setattr(uploads, "_before_settle", pause)
    answers: list = []
    first = in_thread(pg, worker, answers)
    assert holding.wait(30)
    second = in_thread(pg, worker, answers)
    second.join(1.0)
    assert second.is_alive(), "the second collector waits for the match lock"
    go.set()
    first.join(30)
    second.join(30)
    assert answers == ["finished", "finished"]
    session = pg()
    [(replay_id, recipe, _, _)] = replays(session)
    row = attempt_row(session)
    assert recipe == condensed.recipe and (row.status, row.store_outcome, row.replay_id) == ("stored", "replaced", replay_id)
    assert len(worker.acks) == 1, "one store, one settlement, one ack"
    session.close()


def test_pg_a_failure_before_the_settlement_commit_keeps_the_old_replay_and_the_attempt(pg_stale, condensed,  # noqa: F811
                                                                                        monkeypatch):
    pg, _ = pg_stale  # noqa: F811
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    monkeypatch.setattr(uploads, "_before_settle", lambda upload: (_ for _ in ()).throw(RuntimeError("boom")))
    answers: list = []
    in_thread(pg, worker, answers).join(30)
    assert isinstance(answers[0], RuntimeError)
    session = pg()
    assert [r[1] for r in replays(session)] == ["old"]
    row = attempt_row(session)
    assert (row.status, row.store_outcome, row.replay_id) == ("parsing", None, None) and not worker.acks
    session.close()


def test_pg_a_deletion_that_held_the_lock_first_wins_over_the_waiting_result(pg_stale, condensed):  # noqa: F811
    pg, context = pg_stale  # noqa: F811
    worker = AutoWorker(condensed.recipe, result_of(condensed))
    holder = pg()
    store.replay_db.advisory_lock(holder, context["match_uuid"])
    store._delete(holder, holder.query(Replay).one())
    holder.add(ReplayDeletion(match_uuid=context["match_uuid"], reason="asked"))
    holder.flush()
    answers: list = []
    collector = in_thread(pg, worker, answers)
    collector.join(1.0)
    assert collector.is_alive(), "the result waits for the deletion"
    holder.commit()
    holder.close()
    collector.join(30)
    assert answers == ["finished"]
    session = pg()
    row = attempt_row(session)
    assert row.status == "failed" and "deleted on request" in row.error and row.replay_id is None
    assert session.query(Replay).count() == 0, "a deleted match is never recreated"
    session.close()


def test_pg_migration_0018_adds_a_nullable_context_and_drops_only_it(pg):  # noqa: F811
    """On the real schema at head: the column is nullable JSON, the downgrade drops it and nothing else, and
    an existing row comes through both ways with a null context. Leaves the database at head."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    path = HERE.parents[1] / "alembic" / "versions" / "0018_replay_upload_auto_context.py"
    spec = importlib.util.spec_from_file_location("migration_0018", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0018", "0017")

    session = pg()
    session.add(ReplayUpload(id=ATTEMPT, status="stored", session_key="sess", created_at=datetime.now(timezone.utc)))
    session.commit()
    engine = session.get_bind()
    session.close()

    def columns() -> dict:
        return {column["name"]: column for column in inspect(engine).get_columns("replay_uploads")}

    def run(step) -> None:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                step()

    before = columns()
    assert before["auto_context"]["nullable"] and "JSON" in str(before["auto_context"]["type"]).upper()
    try:
        run(migration.downgrade)
        assert set(columns()) == set(before) - {"auto_context"}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id::text, status FROM replay_uploads")).fetchall() == [
                (ATTEMPT, "stored")]
    finally:
        if "auto_context" not in columns():
            run(migration.upgrade)
    assert {name: str(c["type"]) for name, c in columns().items()} == {name: str(c["type"]) for name, c in before.items()}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id::text, auto_context FROM replay_uploads")).fetchall() == [
            (ATTEMPT, None)]

"""The web side of a held upload (app/services/replay_upload.py, HELD): an upload from a game build the worker's
parser doesn't support yet is shown as waiting, polled without ever timing out, kept out of the batch limit, and
stored through the ordinary path once the worker parses it. Also the parser's own words on a failed parse."""

import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_replay_store import condensed  # noqa: E402,F401  (fixture)
from test_replay_upload import db, request, result_of  # noqa: E402,F401  (fixture)
from test_replay_worker import stub  # noqa: E402,F401  (fixture)
from test_replay_worker_archive import start_archive  # noqa: E402
from test_replay_worker_held import new_patch_bytes  # noqa: E402

from app.models.replay import Replay, ReplayUpload  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_upload as uploads  # noqa: E402

UPLOAD = "00000000-0000-4000-8000-0000000a1d01"
WEEKS_AGO = timedelta(days=20)


class ScriptedWorker:
    """Answers each job poll with `answer` (a dict, or an exception to raise); acks are accepted."""

    def __init__(self, answer):
        self.answer = answer

    def job(self, job_id):
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"id": job_id, **self.answer}

    def ack(self, job_id, body):
        return {"archived": True, "result": "archived"}


def held_row(db, sha="0" * 64, note="recorded on game version 13.08, which ..."):
    row = ReplayUpload(id=UPLOAD, status="held", error=note, worker_job_id="job1", source_sha256=sha,
                       session_key="mine", created_at=datetime.now(timezone.utc) - WEEKS_AGO)
    db.add(row)
    db.commit()
    return row


def test_an_upload_from_an_unsupported_build_is_held_and_not_counted_in_the_batch(db, tmp_path, stub):  # noqa: F811
    worker, httpd, base, disk = start_archive(tmp_path, stub)
    try:
        client = uploads.WorkerClient(base)
        upload = uploads.create_upload(db, io.BytesIO(new_patch_bytes()), "mine", "ip", client)
        upload = uploads.refresh_job(db, upload, client)
        assert upload.status == "held" and "13.08" in upload.error and upload.finished_at is None
        # Five more held files: none of them is "in progress", so the batch limit never refuses the next upload.
        for _ in range(uploads.MAX_UNFINISHED):
            more = uploads.create_upload(db, io.BytesIO(new_patch_bytes()), "mine", "ip", client)
            uploads.refresh_job(db, more, client)
        uploads.check_limits(db, "mine")
        # What the uploader's pages show.
        session = {"replay_upload_ok": True, "replay_upload_sid": "mine"}
        page = routes.upload_job(request(session), upload.id, db)
        assert b"Waiting for a parser update" in page.body and b"13.08" in page.body
        assert page.context["job"]["status"] == "held"
    finally:
        httpd.shutdown()


def test_a_held_upload_never_times_out_and_is_stored_once_the_worker_parses_it(db, condensed):  # noqa: F811
    upload = held_row(db, sha=condensed.source_sha256)
    # Weeks old and the worker still holds it: held, unchanged.
    upload = uploads.refresh_job(db, upload, ScriptedWorker({"status": "held", "error": upload.error}))
    assert upload.status == "held"
    # The new parser has taken it: still held on this side, never failed as stuck, with a note that says so.
    for status in ("queued", "parsing"):
        upload = uploads.refresh_job(db, upload, ScriptedWorker({"status": status}))
        assert (upload.status, upload.error) == ("held", uploads.RELEASED)
    # An unreachable worker changes nothing.
    upload = uploads.refresh_job(db, upload, ScriptedWorker(uploads.WorkerError("unreachable")))
    assert (upload.status, upload.error) == ("held", uploads.RELEASED)
    upload = uploads.refresh_job(db, upload, ScriptedWorker({"status": "done", "result": result_of(condensed)}))
    assert upload.status == "stored" and upload.error is None and upload.archive_ack == "archived"
    assert db.get(Replay, upload.replay_id).source_sha256 == condensed.source_sha256


def test_a_held_upload_the_worker_no_longer_has_fails_with_a_plain_reason(db):
    upload = held_row(db)
    upload = uploads.refresh_job(db, upload, ScriptedWorker(uploads.WorkerRefused("not found", 404, {})))
    assert (upload.status, upload.error) == ("failed", uploads.GONE_WHILE_HELD)


def test_a_held_upload_the_worker_gave_up_on_fails_with_its_reason(db):
    upload = held_row(db)
    reason = "recorded on game version 13.08: the replay parser still didn't support it after 60 days"
    upload = uploads.refresh_job(db, upload, ScriptedWorker({"status": "failed", "error": reason}))
    assert (upload.status, upload.error) == ("failed", reason)


def test_the_background_collector_polls_held_uploads(db, condensed):  # noqa: F811
    held_row(db, sha=condensed.source_sha256)
    factory = sessionmaker(bind=db.get_bind())
    worker = ScriptedWorker({"status": "done", "result": result_of(condensed)})
    assert uploads.collect_unfinished(factory, worker) == 1
    db.expire_all()
    assert db.get(ReplayUpload, UPLOAD).status == "stored"


def test_pg_migration_0022_allows_held_and_its_downgrade_fails_held_rows():
    """On a real server, in one transaction that is rolled back whatever happens: the upgrade accepts 'held', the
    downgrade turns held rows into failed ones and refuses 'held' again. The database is left as it was."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, exc, text

    from _postgres import postgres_url_or_skip

    path = HERE.parents[1] / "alembic" / "versions" / "0022_upload_held.py"
    spec = importlib.util.spec_from_file_location("migration_0022", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0022", "0021")

    engine = create_engine(postgres_url_or_skip(writes=True))
    insert = text("INSERT INTO replay_uploads (id, status, created_at) VALUES (:id, :status, now())")

    def allowed(connection, status: str) -> bool:
        savepoint = connection.begin_nested()
        try:
            connection.execute(insert, {"id": "00000000-0000-4000-8000-0000000a1dff", "status": status})
        except exc.IntegrityError:
            savepoint.rollback()
            return False
        savepoint.rollback()
        return True

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                if not allowed(connection, "held"):    # a database still at 0021
                    migration.upgrade()
                assert allowed(connection, "held")
                connection.execute(insert, {"id": UPLOAD, "status": "held"})
                migration.downgrade()
                assert not allowed(connection, "held")
                assert connection.execute(text("SELECT status, finished_at IS NOT NULL FROM replay_uploads "
                                               "WHERE id = :id"), {"id": UPLOAD}).one() == ("failed", True)
                migration.upgrade()
                assert allowed(connection, "held") and allowed(connection, "parsing")
                assert not allowed(connection, "nonsense")
        finally:
            transaction.rollback()
    engine.dispose()


def test_a_failed_parse_shows_the_parsers_last_lines():
    assert uploads.failure_text({"error": "parse failed"}) == "parse failed"
    assert uploads.failure_text({"error": "parse failed", "detail": "[ERR] Failed to parse replay."}) == \
        "parse failed (the parser said: [ERR] Failed to parse replay.)"
    assert uploads.failure_text({"error": "parse timed out"}) == uploads.REASONS["parse timed out"]
    assert uploads.failure_text({}) == "parse failed"

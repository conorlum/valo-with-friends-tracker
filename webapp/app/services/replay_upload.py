"""Friends-only replay upload: the web side (docs/replay-viewer-plan.md, "Upload", Stage 3).

- `upload_enabled`: both `REPLAY_UPLOAD_CODE` and `REPLAY_WORKER_URL` set, and not demo mode.
- `code_matches`: a constant-time compare of the invite code (the site's only gate).
- `check_limits`: at most MAX_UNFINISHED unfinished jobs per session (a batch), counted from
  `replay_uploads`. There is no hourly cap: the batch limit already protects the worker.
- `WorkerClient`: the private worker over `urllib` (standard library only): stream the file to
  `POST /jobs`, read `GET /jobs/{id}`.
- `refresh_job`: the job page's status poll. It asks the worker; when the job is done it stores
  the result with the same `store_replay` as local ingest (one locked transaction, the dedupe rule,
  the link), and marks the upload `stored` or `failed` with a plain reason. A job still parsing
  after STUCK_AFTER (or still queued after QUEUED_STUCK_AFTER) becomes `failed: please re-upload`.
- `collect_unfinished`: the same poll for every unfinished upload, from the archive sync thread, so a
  result is stored when its page was closed.
- `collect_auto`: the one collector of an automatic re-parse attempt (a row tagged AUTO_PREFIX), whoever
  asks: it checks this site against the worker's health, reads the attempt's receipt before any job, and
  commits the replacement and the attempt's end together. It never submits or closes an attempt.

- `send_ack`: right after a result is stored (or refused), tells the worker what happened, so it archives
  the file or deletes it (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1). Best
  effort: the worker's answer goes in `archive_ack`, and app/services/replay_archive_sync.py re-sends any
  that is still null. A `kept_existing` result keeps the store's own reason in `error` for the admin, but its
  uploader sees it as stored (app/routers/replays.py, `_status_body`): friends upload their own recordings
  of one match, and whoever is second shouldn't be told so.

Uploaded `.vrf` files are never kept here: the request's spooled file goes to the worker. With its
archive disk on, the worker keeps the accepted recording of each match privately; with it off, it
deletes its copy when the job ends (decision 8). Nothing here runs the scorer: an uploaded replay's
per-kill Impact comes from the next crawl's link pass (run decision D3).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.replay import ReplayUpload
from app.replays import contract
from app.replays import db as replay_db
from app.replays.condense import CondensedReplay
from app.replays.store import ExpectedAuto, StoreRefused, store_replay

VRF_MAGIC = (0x43F4EFDD).to_bytes(4, "little")
# The plan's 10 minutes, doubled: a local job on the smallest competitive file took 179 s (parse
# 52 s + condense), the largest file is 1.5x its size, and Render's CPU may be slower than this PC's.
STUCK_AFTER = timedelta(minutes=20)
# A job the worker still reports as queued is waiting behind others (a batch of MAX_UNFINISHED at the cap is
# several parses), not stuck: it gets longer.
QUEUED_STUCK_AFTER = timedelta(minutes=60)
WORKER_TIMEOUT_S = 30
ACK_TIMEOUT_S = 5  # the ack is sent inside the uploader's status poll; a slow worker is retried later
UNFINISHED = ("queued", "parsing")
MAX_UNFINISHED = 5  # a batch: one session's uploads in progress at once, the worker's own queue size
RECENT_LISTED = timedelta(hours=24)  # the upload page lists this session's uploads from this long ago

REASONS = {
    "parse timed out": "parse timed out: please try again",
    "parse failed": "parse failed (this patch may not be supported yet)",
}

# The automatic re-parse queue (docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md). An attempt is a
# `replay_uploads` row whose `session_key` is this prefix plus the recipe it aims for, whose id is the attempt
# UUID the worker keys its receipt on, and whose `auto_context` holds the replay it was selected for.
AUTO_PREFIX = "auto-reparse:"
AUTO_CONTEXT_VERSION = 1
AUTO_PHASES = ("reserved", "accepted", "refused_before_acceptance")
# What this site's code speaks. The worker must advertise exactly these in /health (`worker_off_reason`).
GAPS_PROTOCOL = 1
REPARSE_PROTOCOL = 1
_before_settle = None  # tests only: called with the attempt row just before the store-and-settle commit


def auto_tag(recipe: str) -> str:
    return f"{AUTO_PREFIX}{recipe}"


def auto_recipe(session_key: str | None) -> str | None:
    """The target recipe an automatic attempt's tag names, or None for any other row."""
    if not session_key or not session_key.startswith(AUTO_PREFIX):
        return None
    return session_key[len(AUTO_PREFIX):] or None


def is_auto(upload: ReplayUpload) -> bool:
    return bool(upload.session_key) and upload.session_key.startswith(AUTO_PREFIX)


def auto_job_id(upload_id: str) -> str:
    """The one worker job an attempt can ever have (replay_worker/server.py derives the same id)."""
    return f"auto{uuid.UUID(str(upload_id)).hex}"


def new_auto_context(replay, target_recipe: str) -> dict:
    """The snapshot a reservation keeps. `replay_uploads.replay_id` can't be it: a replacement sets that
    foreign key null, and an id alone says nothing about which recipe and file it named."""
    return {"version": AUTO_CONTEXT_VERSION, "match_uuid": replay.match_uuid.lower(),
            "selected_replay_id": replay.id, "source_recipe": replay.recipe, "source_sha256": replay.source_sha256,
            "target_recipe": target_recipe, "phase": "reserved"}


def auto_context(upload: ReplayUpload) -> dict | None:
    """The row's context when it is one this code can act on, else None (the row then fails safely)."""
    context = upload.auto_context
    if not isinstance(context, dict) or context.get("version") != AUTO_CONTEXT_VERSION:
        return None
    texts = ("match_uuid", "source_recipe", "source_sha256", "target_recipe")
    if not all(isinstance(context.get(key), str) and context[key] for key in texts):
        return None
    if type(context.get("selected_replay_id")) is not int or context.get("phase") not in AUTO_PHASES:
        return None
    return context


def site_recipe() -> str:
    """The recipe this deploy's code stamps: fixed for the life of the process, so read once."""
    global _site_recipe
    if _site_recipe is None:
        _site_recipe = contract.current_recipe()
    return _site_recipe


_site_recipe: str | None = None


def worker_off_reason(health: dict | None, recipe: str) -> str | None:
    """Why this site must not start, store or close an automatic re-parse against the worker that gave
    `health`; None when it may. Everything is required explicitly: a worker that doesn't say fails closed.
    The collector, the automatic step and the status route all ask here."""
    if not isinstance(health, dict):
        return "the replay worker is unreachable"
    if health.get("recipe") != recipe:
        return f"recipe skew: the site is on {recipe}, the worker on {health.get('recipe') or 'an unknown recipe'}"
    archive, control = health.get("archive") or {}, health.get("control") or {}
    if archive.get("enabled") is not True:
        return "the worker's archive is off"
    if control.get("enabled") is not True:
        return "map control is off on the worker"
    if type(control.get("gaps_protocol")) is not int or control["gaps_protocol"] != GAPS_PROTOCOL:
        return f"the worker's timing-gaps protocol is not {GAPS_PROTOCOL}"
    if type(archive.get("reparse_protocol")) is not int or archive["reparse_protocol"] != REPARSE_PROTOCOL:
        return f"the worker's re-parse protocol is not {REPARSE_PROTOCOL}"
    return None


def upload_enabled() -> bool:
    return bool(settings.replay_upload_code and settings.replay_worker_url and not settings.demo_mode)


def code_matches(given: str | None) -> bool:
    expected = settings.replay_upload_code or ""
    return bool(expected) and hmac.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))


def guide_key_matches(given: str | None) -> bool:
    expected = settings.replay_upload_guide_key or ""
    return bool(expected) and hmac.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))


class LimitExceeded(Exception):
    pass


def check_limits(db, session_key: str) -> None:
    if db.query(ReplayUpload).filter(ReplayUpload.session_key == session_key,
                                     ReplayUpload.status.in_(UNFINISHED)).count() >= MAX_UNFINISHED:
        raise LimitExceeded(f"{MAX_UNFINISHED} uploads at a time: wait for one to finish")


class WorkerError(Exception):
    pass


class WorkerRefused(WorkerError):
    """The worker answered 4xx: a final answer, never worth sending again."""

    def __init__(self, reason: str, status: int, body: dict):
        super().__init__(reason)
        self.status = status
        self.body = body


class WorkerClient:
    def __init__(self, base_url: str, timeout_s: float = WORKER_TIMEOUT_S):
        # render.yaml's `fromService ... property: hostport` gives "host:port" with no scheme.
        self.base = (base_url if "://" in base_url else f"http://{base_url}").rstrip("/")
        self.timeout = timeout_s

    def submit(self, stream, size: int) -> dict:
        request = urllib.request.Request(f"{self.base}/jobs", data=stream, method="POST",
                                         headers={"Content-Type": "application/octet-stream",
                                                  "Content-Length": str(size)})
        return self._call(request)

    def job(self, job_id: str) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/jobs/{job_id}"))

    def health(self) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/health"))

    def archive(self) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/archive"))

    def ack(self, job_id: str, body: dict) -> dict:
        return self._post_json(f"/jobs/{job_id}/ack", body, timeout=ACK_TIMEOUT_S)

    def reparse(self, match_uuid: str, *, attempt_id: str | None = None,
                expected_sha256: str | None = None) -> dict:
        """Without an attempt id, the manual re-parse as it always was. With one, the attempt protocol: the
        worker's coded answer whatever its HTTP status (see `_coded`). Never send an attempt id to a worker
        that doesn't advertise `archive.reparse_protocol`: an older one would read it as a manual re-parse."""
        if attempt_id is None:
            return self._post_json("/reparse", {"match_uuid": match_uuid})
        return self._coded(self._json_request("/reparse", {"match_uuid": match_uuid, "attempt_id": attempt_id,
                                                           "expected_sha256": expected_sha256}))

    def reparse_attempt(self, attempt_id: str) -> dict:
        return self._coded(urllib.request.Request(f"{self.base}/reparse/attempts/{attempt_id}"))

    def close_reparse_attempt(self, attempt_id: str, match_uuid: str, expected_sha256: str) -> dict:
        return self._coded(self._json_request(f"/reparse/attempts/{attempt_id}/close",
                                              {"match_uuid": match_uuid, "expected_sha256": expected_sha256}))

    def delete(self, match_uuid: str) -> dict:
        return self._post_json("/archive/delete", {"match_uuid": match_uuid})

    def tombstones(self, match_uuids: list[str]) -> dict:
        return self._post_json("/archive/tombstones", {"match_uuids": match_uuids})

    def _json_request(self, path: str, body: dict):
        return urllib.request.Request(f"{self.base}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
                                      headers={"Content-Type": "application/json"})

    def _post_json(self, path: str, body: dict, timeout: float | None = None) -> dict:
        return self._call(self._json_request(path, body), timeout)

    def _coded(self, request) -> dict:
        """An attempt route's answer: the JSON body with its `code`, whether the worker said 2xx, 4xx or 503
        (`accepted`, `unknown_attempt`, `sha_mismatch`, `queue_full`, ...). Anything else raises WorkerError:
        a timeout, a dropped connection or an answer without a code says nothing about whether the attempt
        was accepted, and the caller must not read it as a refusal."""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read() or b"{}")
            except (ValueError, OSError):
                body = None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise WorkerError("the replay worker is unreachable") from error
        if not isinstance(body, dict) or not isinstance(body.get("code"), str):
            raise WorkerError("the replay worker gave no answer for this attempt")
        return body

    def _call(self, request, timeout: float | None = None) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read() or b"{}")
            except ValueError:
                body = {}
            reason = body.get("error") or f"worker answered {error.code}"
            if 400 <= error.code < 500:
                raise WorkerRefused(reason, error.code, body) from error
            raise WorkerError(reason) from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise WorkerError("the replay worker is unreachable") from error


def client() -> WorkerClient:
    return WorkerClient(settings.replay_worker_url)


def sha256_of(stream, chunk: int = 1 << 20) -> tuple[str, int, bytes]:
    """(sha256, size, the first 4 bytes) of a seekable stream, which is rewound after."""
    digest, size, head = hashlib.sha256(), 0, b""
    stream.seek(0)
    for block in iter(lambda: stream.read(chunk), b""):
        if not head:
            head = block[:4]
        digest.update(block)
        size += len(block)
    stream.seek(0)
    return digest.hexdigest(), size, head


def create_upload(db, stream, session_key: str, client_ip: str | None, worker: WorkerClient) -> ReplayUpload:
    """Checks, records and hands one upload to the worker. Raises ValueError (a plain reason) for a
    file that isn't a replay or is too big, LimitExceeded, or WorkerError."""
    sha, size, head = sha256_of(stream)
    if size > settings.replay_upload_max_bytes:
        raise ValueError(f"the file is over the {settings.replay_upload_max_bytes // 1_000_000} MB limit")
    if head != VRF_MAGIC:
        raise ValueError("not a Valorant replay")
    check_limits(db, session_key)
    upload = ReplayUpload(id=str(uuid.uuid4()), status="queued", source_sha256=sha, size_bytes=size,
                          session_key=session_key, client_ip=client_ip,
                          created_at=datetime.now(timezone.utc))
    db.add(upload)
    db.commit()
    try:
        job = worker.submit(stream, size)
    except WorkerError as error:
        upload.status, upload.error, upload.finished_at = "failed", str(error), datetime.now(timezone.utc)
        db.commit()
        raise
    upload.worker_job_id = job.get("id")
    upload.status = "parsing"
    db.commit()
    return upload


def condensed_from_result(result: dict) -> CondensedReplay:
    import gzip

    rounds = {int(n): json.loads(gzip.decompress(base64.b64decode(data))) for n, data in result["rounds"].items()}
    return CondensedReplay(
        match_uuid=result["match_uuid"], map_name=result["map_name"], game_branch=result["game_branch"],
        source_sha256=result["source_sha256"], recipe=result["recipe"], hz=result["hz"], rounds=rounds,
        players=result["players"], link_inputs=result["link_inputs"], report=result.get("report") or {})


def refresh_job(db, upload: ReplayUpload, worker: WorkerClient, now: datetime | None = None) -> ReplayUpload:
    """One status poll: ask the worker, store a finished result, fail a stuck job."""
    now = now or datetime.now(timezone.utc)
    if upload.status not in UNFINISHED:
        return upload
    if is_auto(upload):  # never the path below: an automatic attempt is collected only through its guards
        upload_id = upload.id
        collect_auto(db, upload_id, worker, now)
        return db.get(ReplayUpload, upload_id)
    created = upload.created_at if upload.created_at.tzinfo else upload.created_at.replace(tzinfo=timezone.utc)
    try:
        job = worker.job(upload.worker_job_id) if upload.worker_job_id else {"status": "failed"}
    except WorkerError:
        job = {"status": "unknown"}
    status = job.get("status")
    if status == "done":
        match_uuid = None
        try:
            condensed = condensed_from_result(job["result"])
            match_uuid = condensed.match_uuid.lower()
            if condensed.source_sha256 != upload.source_sha256:
                raise ValueError("the worker parsed another file")
            result = store_replay(db, condensed, source="upload")
        except (StoreRefused, ValueError, KeyError) as error:
            db.rollback()
            upload = db.get(ReplayUpload, upload.id)
            upload.store_outcome = "failed"
            upload = _finish(db, upload, "failed", f"could not store it: {error}")
            send_ack(db, upload, worker, match_uuid=match_uuid)
            return upload
        upload = db.get(ReplayUpload, upload.id)
        upload.replay_id = result.replay_id
        upload.store_outcome = result.action
        note = None
        if result.action == "kept_existing":
            note = (result.report or {}).get("reason") or "a linked replay of this match already exists"
        upload = _finish(db, upload, "stored", note)
        send_ack(db, upload, worker)
        return upload
    if status == "failed":
        reason = job.get("error") or "parse failed"
        return _finish(db, upload, "failed", REASONS.get(reason, reason))
    if now - created > (QUEUED_STUCK_AFTER if status == "queued" else STUCK_AFTER):
        return _finish(db, upload, "failed", "failed: please re-upload")
    return upload


def collect_unfinished(session_factory, worker: WorkerClient, now: datetime | None = None,
                       session_prefix: str | None = None) -> int:
    """Stores the result of every finished upload nobody's page is polling (a closed tab), with the same
    `refresh_job` the page uses; returns how many finished. Each upload's row is locked first, so the page
    and this never store one result twice. An upload with no worker job yet is still streaming to the
    worker: it is left alone until it is stuck.

    `session_prefix` narrows it to rows whose `session_key` starts with it (the control cycle passes
    AUTO_PREFIX). An automatic attempt goes through `collect_auto` whoever calls this, with its own lock
    order; it is never read as a stuck or still-streaming upload."""
    now = now or datetime.now(timezone.utc)
    session = session_factory()
    finished = 0
    try:
        rows = [(upload_id, key or "") for upload_id, key in
                session.query(ReplayUpload.id, ReplayUpload.session_key).filter(ReplayUpload.status.in_(UNFINISHED))
                .order_by(ReplayUpload.created_at)]
        session.rollback()
        for upload_id, key in rows:
            if session_prefix is not None and not key.startswith(session_prefix):
                continue
            if key.startswith(AUTO_PREFIX):
                finished += collect_auto(session, upload_id, worker, now) == "finished"
                session.rollback()
                continue
            query = session.query(ReplayUpload).filter(ReplayUpload.id == upload_id).populate_existing()
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update()
            upload = query.one_or_none()
            if upload is None or upload.status not in UNFINISHED:
                session.rollback()
                continue
            if upload.worker_job_id is None:
                created = upload.created_at if upload.created_at.tzinfo else upload.created_at.replace(
                    tzinfo=timezone.utc)
                if now - created > STUCK_AFTER:
                    _finish(session, upload, "failed", "failed: please re-upload")
                    finished += 1
                session.rollback()
                continue
            if refresh_job(session, upload, worker, now).status not in UNFINISHED:
                finished += 1
            session.rollback()  # releases the row lock when nothing changed
        return finished
    finally:
        session.rollback()
        session.close()


def _finish(db, upload: ReplayUpload, status: str, error: str | None, now: datetime | None = None) -> ReplayUpload:
    upload.status, upload.error, upload.finished_at = status, error, now or datetime.now(timezone.utc)
    db.commit()
    return upload


# ---------------------------------------------------------------- automatic re-parse attempts


def _locked_attempt(db, upload_id: str, match_uuid: str | None) -> ReplayUpload | None:
    """A fresh transaction holding the match's advisory lock and then the attempt's row, in that order
    everywhere (the store takes the match lock first too). None, with the transaction ended, when the row
    is gone or already finished: another collector settled it."""
    db.rollback()
    if match_uuid:
        replay_db.advisory_lock(db, match_uuid)
    query = db.query(ReplayUpload).filter(ReplayUpload.id == upload_id).populate_existing()
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    upload = query.one_or_none()
    if upload is None or upload.status not in UNFINISHED:
        db.rollback()
        return None
    return upload


def settle_auto(db, upload_id: str, status: str, error: str | None, *, phase: str | None = None,
                outcome: str | None = None, now: datetime | None = None) -> bool:
    """Ends an attempt without touching any replay. False when it was already finished."""
    seen = db.get(ReplayUpload, upload_id)
    context = auto_context(seen) if seen is not None else None
    upload = _locked_attempt(db, upload_id, context["match_uuid"] if context else None)
    if upload is None:
        return False
    if phase is not None and context is not None:
        upload.auto_context = {**context, "phase": phase}
    if outcome is not None:
        upload.store_outcome = outcome
    _finish(db, upload, status, error, now)
    return True


def receipt_matches(receipt: dict, upload_id: str, context: dict) -> bool:
    """An accepted receipt that is this attempt's: its one job, for the selected match and file."""
    return (receipt.get("job_id") == auto_job_id(upload_id)
            and str(receipt.get("match_uuid") or "").lower() == context["match_uuid"]
            and receipt.get("sha256") == context["source_sha256"])


def bind_accepted(db, upload_id: str, receipt: dict) -> bool:
    """Records that the worker accepted the attempt (its receipt says so). If this commit is lost, the row
    stays `reserved` and the next lookup of the same id binds it again: never a second job."""
    seen = db.get(ReplayUpload, upload_id)
    context = auto_context(seen) if seen is not None else None
    upload = _locked_attempt(db, upload_id, context["match_uuid"] if context else None)
    if upload is None or context is None:
        db.rollback()
        return False
    context = auto_context(upload) or context
    if context["phase"] == "reserved":
        upload.auto_context = {**context, "phase": "accepted"}
        upload.worker_job_id = auto_job_id(upload_id)
        upload.status = "parsing"
        if type(receipt.get("size")) is int:
            upload.size_bytes = receipt["size"]
    db.commit()
    return True


def collect_auto(db, upload_id: str, worker: WorkerClient, now: datetime | None = None) -> str:
    """One look at one automatic attempt, from any collector (the control cycle, the archive sync, a poll).
    Returns `finished`, `pending` (accepted, its job still running or unreadable), `reserved` (not known to
    be accepted: only the automatic step may send, resend or close it) or `deferred: <reason>`.

    Nothing is decided until this site and the worker agree (`worker_off_reason`): during a deploy an older
    site sees a newer worker, or the reverse, and the attempt is left exactly as it is for the collector
    that matches. With a match, the result must be for the selected match and file and carry the recipe that
    the tag, the context and this site all name; otherwise it fails as `another recipe` and nothing is stored.
    """
    now = now or datetime.now(timezone.utc)
    db.rollback()
    upload = db.get(ReplayUpload, upload_id, populate_existing=True)
    if upload is None or upload.status not in UNFINISHED:
        return "finished"
    context = auto_context(upload)
    if context is None or auto_recipe(upload.session_key) is None:
        settle_auto(db, upload_id, "failed", "an automatic re-parse row without a valid context", now=now)
        return "finished"
    tag_recipe = auto_recipe(upload.session_key)
    db.rollback()
    try:
        health = worker.health()
    except WorkerError:
        health = None
    recipe = site_recipe()
    reason = worker_off_reason(health, recipe)
    if reason is not None:
        return f"deferred: {reason}"
    if context["phase"] == "reserved":
        # The receipt first: a reserved row is never polled as a job or timed out as a stuck upload.
        try:
            receipt = worker.reparse_attempt(upload_id)
        except WorkerError:
            return "reserved"
        if receipt.get("code") == "closed":
            settle_auto(db, upload_id, "failed", "closed before the worker accepted it",
                        phase="refused_before_acceptance", now=now)
            return "finished"
        if receipt.get("code") != "accepted":
            return "reserved"
        if not receipt_matches(receipt, upload_id, context):
            settle_auto(db, upload_id, "failed", "the worker's receipt for this attempt names another file",
                        phase="refused_before_acceptance", now=now)
            return "finished"
        if not bind_accepted(db, upload_id, receipt):
            return "finished"
    elif context["phase"] != "accepted":
        settle_auto(db, upload_id, "failed", "refused before the worker accepted it", now=now)
        return "finished"
    try:
        job = worker.job(auto_job_id(upload_id))
    except WorkerRefused:
        # The job is gone from the worker. Only its receipt can say the result expired; anything less waits.
        try:
            receipt = worker.reparse_attempt(upload_id)
        except WorkerError:
            return "pending"
        if receipt.get("code") == "accepted" and receipt.get("job_status") == "expired":
            settle_auto(db, upload_id, "failed", "the result expired on the worker before it was collected", now=now)
            return "finished"
        return "pending"
    except WorkerError:
        return "pending"
    status = job.get("status")
    if status == "failed":
        reason = job.get("error") or "parse failed"
        settle_auto(db, upload_id, "failed", REASONS.get(reason, reason), now=now)
        return "finished"
    if status != "done":
        return "pending"  # queued or parsing: an accepted job is waited for, however long it takes
    problem = None
    try:
        condensed = condensed_from_result(job["result"])
        if condensed.match_uuid.lower() != context["match_uuid"]:
            problem = "the worker parsed another match"
        elif condensed.source_sha256 != context["source_sha256"]:
            problem = "the worker parsed another file"
        elif not (condensed.recipe == context["target_recipe"] == tag_recipe == recipe):
            problem = "another recipe"
    except (ValueError, KeyError, TypeError) as error:
        problem = f"unreadable result ({error})"
    stored = None
    if problem is None:
        try:
            stored = _locked_attempt(db, upload_id, context["match_uuid"])
            if stored is None:
                return "finished"
            result = store_replay(db, condensed, source="upload", commit=False, expected_auto=ExpectedAuto(
                context["selected_replay_id"], context["source_recipe"], context["source_sha256"],
                context["target_recipe"]))
            stored.replay_id = result.replay_id
            stored.store_outcome = result.action
            stored.status, stored.finished_at = "stored", now
            stored.error = (result.report or {}).get("reason") if result.action == "kept_existing" else None
            if _before_settle is not None:
                _before_settle(stored)
            db.commit()  # the replacement and the attempt's end, together or not at all
        except StoreRefused as refused:
            db.rollback()
            stored, problem = None, str(refused)
        except Exception:
            db.rollback()
            raise
    if stored is None:
        if not settle_auto(db, upload_id, "failed", f"could not store it: {problem}", outcome="failed", now=now):
            return "finished"
        stored = db.get(ReplayUpload, upload_id)
    # Only now, with the settlement committed. A lost ack is re-sent by the archive sync; it never re-stores.
    send_ack(db, stored, worker, match_uuid=context["match_uuid"])
    return "finished"


def ack_body(db, upload: ReplayUpload, match_uuid: str | None = None) -> dict | None:
    """The ack for one collected upload, from what the DB holds now (so a re-send carries the current
    replay id and, once the replay links, the match's date). None when there is nothing to ack."""
    from app.models.match import Match
    from app.models.replay import Replay

    outcome = upload.store_outcome
    if not outcome or not upload.worker_job_id:
        return None
    replay = db.get(Replay, upload.replay_id) if upload.replay_id is not None else None
    if outcome in ("stored", "replaced", "unchanged") and replay is None:
        outcome = "failed"  # the replay is gone (deleted on request): the worker drops the file
    body = {"match_uuid": (replay.match_uuid if replay is not None else match_uuid) or "", "sha256": None,
            "outcome": outcome, "replay_id": None, "played_at": None}
    if replay is not None and outcome in ("stored", "replaced", "unchanged"):
        body.update(sha256=replay.source_sha256, replay_id=replay.id)
        match = db.get(Match, replay.match_id) if replay.match_id is not None else None
        if match is not None and match.played_at is not None:
            played = match.played_at if match.played_at.tzinfo else match.played_at.replace(tzinfo=timezone.utc)
            body["played_at"] = played.isoformat()
    return body


def send_ack(db, upload: ReplayUpload, worker: WorkerClient, match_uuid: str | None = None) -> str | None:
    """Best effort: records the worker's answer in `archive_ack`; leaves it null when the worker can't be
    reached (or asks to be asked again), for replay_archive_sync to re-send."""
    body = ack_body(db, upload, match_uuid)
    if body is None:
        return None
    try:
        answer = worker.ack(upload.worker_job_id, body)
        if answer.get("reason") == "archive off":
            recorded = "off"
        else:
            recorded = answer.get("result") or ("archived" if answer.get("archived") else "answered")
            if answer.get("reason"):
                recorded += f": {answer['reason']}"
    except WorkerRefused as refused:
        recorded = f"refused: {refused}"
    except WorkerError:
        db.rollback()
        return None
    upload = db.get(ReplayUpload, upload.id)
    upload.archive_ack = recorded[:160]
    db.commit()
    return recorded

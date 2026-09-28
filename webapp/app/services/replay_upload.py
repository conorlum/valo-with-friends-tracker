"""Friends-only replay upload: the web side (docs/replay-viewer-plan.md, "Upload", Stage 3).

- `upload_enabled`: both `REPLAY_UPLOAD_CODE` and `REPLAY_WORKER_URL` set, and not demo mode.
- `code_matches`: a constant-time compare of the invite code (the site's only gate).
- `check_limits`: at most UPLOADS_PER_HOUR per session and per IP, and one unfinished job per
  session, counted from `replay_uploads`.
- `WorkerClient`: the private worker over `urllib` (standard library only): stream the file to
  `POST /jobs`, read `GET /jobs/{id}`.
- `refresh_job`: the job page's status poll. It asks the worker; when the job is done it stores
  the result with the same `store_replay` as local ingest (one locked transaction, the dedupe rule,
  the link), and marks the upload `stored` or `failed` with a plain reason. A job still parsing
  after STUCK_AFTER becomes `failed: please re-upload`.

Uploaded `.vrf` files are never kept here: the request's spooled file goes to the worker, which
deletes its copy when the job ends (decision 8). Nothing here runs the scorer: an uploaded
replay's per-kill Impact comes from the next crawl's link pass (run decision D3).
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

from sqlalchemy import or_

from app.config import settings
from app.models.replay import ReplayUpload
from app.replays.condense import CondensedReplay
from app.replays.store import StoreRefused, store_replay

VRF_MAGIC = (0x43F4EFDD).to_bytes(4, "little")
# Decision 10's starting limits (approved D10), checked against the six competitive files.
UPLOADS_PER_HOUR = 10
# The plan's 10 minutes, doubled: a local job on the smallest competitive file took 179 s (parse
# 52 s + condense), the largest file is 1.5x its size, and Render's CPU may be slower than this PC's.
STUCK_AFTER = timedelta(minutes=20)
WORKER_TIMEOUT_S = 30
UNFINISHED = ("queued", "parsing")

REASONS = {
    "parse timed out": "parse timed out: please try again",
    "parse failed": "parse failed (this patch may not be supported yet)",
}


def upload_enabled() -> bool:
    return bool(settings.replay_upload_code and settings.replay_worker_url and not settings.demo_mode)


def code_matches(given: str | None) -> bool:
    expected = settings.replay_upload_code or ""
    return bool(expected) and hmac.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))


class LimitExceeded(Exception):
    pass


def check_limits(db, session_key: str, client_ip: str | None, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    recent = db.query(ReplayUpload).filter(ReplayUpload.created_at >= now - timedelta(hours=1))
    who = [ReplayUpload.session_key == session_key]
    if client_ip:
        who.append(ReplayUpload.client_ip == client_ip)
    if recent.filter(or_(*who)).count() >= UPLOADS_PER_HOUR:
        raise LimitExceeded(f"at most {UPLOADS_PER_HOUR} uploads an hour: please try later")
    if db.query(ReplayUpload).filter(ReplayUpload.session_key == session_key,
                                     ReplayUpload.status.in_(UNFINISHED)).count():
        raise LimitExceeded("one upload at a time: wait for the last one to finish")


class WorkerError(Exception):
    pass


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

    def _call(self, request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read() or b"{}")
            except ValueError:
                body = {}
            raise WorkerError(body.get("error") or f"worker answered {error.code}") from error
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
    check_limits(db, session_key, client_ip)
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
    created = upload.created_at if upload.created_at.tzinfo else upload.created_at.replace(tzinfo=timezone.utc)
    try:
        job = worker.job(upload.worker_job_id) if upload.worker_job_id else {"status": "failed"}
    except WorkerError:
        job = {"status": "unknown"}
    status = job.get("status")
    if status == "done":
        try:
            condensed = condensed_from_result(job["result"])
            if condensed.source_sha256 != upload.source_sha256:
                raise ValueError("the worker parsed another file")
            result = store_replay(db, condensed, source="upload")
        except (StoreRefused, ValueError, KeyError) as error:
            db.rollback()
            return _finish(db, upload, "failed", f"could not store it: {error}")
        upload = db.get(ReplayUpload, upload.id)
        upload.replay_id = result.replay_id
        note = None if result.action != "kept_existing" else "a linked replay of this match already exists"
        return _finish(db, upload, "stored", note)
    if status == "failed":
        reason = job.get("error") or "parse failed"
        return _finish(db, upload, "failed", REASONS.get(reason, reason))
    if now - created > STUCK_AFTER:
        return _finish(db, upload, "failed", "failed: please re-upload")
    return upload


def _finish(db, upload: ReplayUpload, status: str, error: str | None) -> ReplayUpload:
    upload.status, upload.error, upload.finished_at = status, error, datetime.now(timezone.utc)
    db.commit()
    return upload

"""The replay upload worker: parses and condenses one `.vrf` at a time (Stage 3, W-c).

Standard library only. It holds no DB credentials and no secrets; it only parses. The friends
web service streams an uploaded file to it over Render's private network and polls for the
result, then stores and links it itself (docs/replay-viewer-plan.md, "Upload").

    POST /jobs        body: the .vrf bytes. 202 {"id", "status": "queued"}; 400 not a replay,
                      411 no length, 413 over the size cap, 503 the queue is full.
    GET  /jobs/{id}   {"id", "status": "queued" | "parsing" | "done" | "failed", "error"?, "result"?}
    GET  /health      {"ok": true, "queued", "limits", "recipe" (what a parse here stamps; null if unknown),
                       "control": {"enabled", "gaps_protocol", "queued", "running", "warm", "preempted"},
                       "archive": {"enabled", ..., "reparse_protocol" (archive on only)}}
    POST /control     body: JSON {key, map, blob (base64), link}: one round's map control
                      (docs/map-control-worker-plan.md). 202 {"id", "status"} (the same job for a key
                      it already has), 400 not a task, 404 control is off, 413 too large, 503 full.
    GET  /control/{id} {"id", "key", "status": "queued" | "running" | "done" | "failed",
                      "error"?, "error_kind"? ("engine" | "infra"), "result"?}

Map control has its own queue and never shares the parse thread: a fixed pool of child processes
(`python -m replay_worker.control_job`, run by the control venv's interpreter; this process never
imports numpy or the engine), niced, time- and memory-capped, one map's first round alone.
It runs on up to `control_workers` children while nothing is parsing or waiting to parse (`Worker.idle`),
and on one beside a parse; a parse arriving kills every running child but the one started first, and their
rounds go back to the front of the queue uncounted (docs/superpowers/plans/2026-10-05-control-idle-queue.md,
D4 as amended 2026-10-07).

One job runs at a time; up to `queue_size` more wait. Each job gets its own folder (the upload and
the parser's export, which is about 65x the file). It runs the parser command with a timeout that
kills the whole process tree and, on Linux, an address-space cap; then condenses with the same code
as local ingest.

The `.vrf` archive (replay_worker/archive.py; docs/superpowers/specs/2026-10-01-control-heights-design.md,
part 1) is on only when REPLAY_ARCHIVE_DIR is a mounted, writable disk. Off, everything is as before: the
folder is a temp folder, deleted when the job ends whatever happened, and the job table lives in memory.
On, job folders live on the disk with a `job.json` each (a restart re-queues or reloads them), the export is
deleted as soon as the condenser returns, and a parsed upload waits in `pending/` until the web app acks
what it stored:

    POST /jobs/{id}/ack        {"match_uuid", "sha256", "outcome", "replay_id", "played_at"}: archive or drop
                               the job's file. Archive off: 200 {"archived": false, "reason": "archive off"}.
    POST /reparse              {"match_uuid"}: parse the archived file again, behind every waiting upload.
    GET  /archive              {"files": [...]}: the archive's index.
    POST /archive/delete       {"match_uuid"}: tombstone a match, delete its pending and archived files.
    POST /archive/tombstones   {"match_uuids": [...]}: the web app's full list replaces the local copy.

Re-parse attempts (the automatic queue; docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, task 1).
The web app reserves an attempt UUID in its database before it asks, and that id is the idempotency key here:
one id is one job, whatever is lost or restarted in between. Every answer is JSON with a "code".

    POST /reparse              {"match_uuid", "attempt_id", "expected_sha256"}: 202 accepted the first time,
                               200 with the same job on a repeat. Refused before acceptance (nothing kept, the
                               id may be sent again): 404 no_archived_file, 409 sha_mismatch, 409 deleted,
                               503 queue_full. 409 identity_conflict: the id was used for another match or
                               file. 409 closed. 400 bad_request. 404 archive_off.
    GET  /reparse/attempts/{id}        the receipt: accepted (with "job_status": queued | parsing | done |
                               failed | expired), preparing, closed, or 404 unknown_attempt. Never enqueues.
    POST /reparse/attempts/{id}/close  {"match_uuid", "expected_sha256"}: the accepted job if there is one;
                               otherwise a durable "closed" receipt, so a delayed POST of the id is refused.
                               Closing never interrupts an accepted parse.
    Any attempt route returns 503 state_unavailable if its durable records cannot safely be read or written;
                               an unreadable receipt is never treated as an unknown id.

The write order of an acceptance is: the receipt as `preparing`, the copied file, the job's `job.json`, the
receipt as `accepted`, the queue, the reply. A restart between any two of them leaves one job at most
(`Worker._recover_attempts`). An accepted id whose job folder has gone answers `expired`; it is never parsed
a second time. If the final receipt write fails, the durable preparation and job record still prove
acceptance and the job is queued once. Cleanup keeps that evidence until the acceptance receipt is durable.

These routes are as unauthenticated as /jobs: the service is private, reachable only from the web service.

Configuration (environment, all optional):
    REPLAY_PARSER_CMD        JSON list, with {vrf} and {out} placeholders
                             (default ["CliReader", "export", "{vrf}", "--output", "{out}"])
    REPLAY_PARSER_BUILD      path of the parser build's BUILD.json, checked against the pin
    REPLAY_WORKER_TMP        where job folders go (default: the system temp folder)
    REPLAY_TIMEOUT_S         parse timeout (default 180)
    REPLAY_MAX_BYTES         upload cap (default 80 MB)
    REPLAY_QUEUE_SIZE        waiting jobs (default 5)
    REPLAY_MEMORY_CAP_MB     parser address-space cap, Linux only (default 0: none)
    REPLAY_WORKER_HOST/PORT  bind address (default 0.0.0.0:8080 in the container)
    REPLAY_CONTROL           "0" turns map control off (default on)
    REPLAY_CONTROL_CMD       JSON list: the control child (default this Python, -m replay_worker.control_job)
    REPLAY_CONTROL_WORKERS   children at once while no parse is on (default 2: the 2-CPU plan's two cores;
                             one beside a parse; never from the core count, which a container reports
                             for the host)
    REPLAY_CONTROL_QUEUE     waiting rounds (default 32)
    REPLAY_CONTROL_TIMEOUT_S / REPLAY_CONTROL_WARM_TIMEOUT_S   per round (900) / a map's first (1800)
    REPLAY_CONTROL_MEMORY_MB child address-space cap, Linux only (default 2048)
    REPLAY_ARCHIVE_DIR       the archive disk's mount point (default unset: no archive)
    REPLAY_ARCHIVE_REQUIRE_MOUNT  "0" skips the mount-point check (tests and local runs only)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

from app.replays.condense import condense_export_dir  # noqa: E402
from app.replays.contract import ContractError, current_recipe  # noqa: E402

try:  # run as `python -m replay_worker.server` (the container) or imported by the tests
    from replay_worker import archive as archive_store  # noqa: E402
except ImportError:  # pragma: no cover - run as a plain script from its folder
    import archive as archive_store  # type: ignore[no-redef]  # noqa: E402

VRF_MAGIC = (0x43F4EFDD).to_bytes(4, "little")
DEFAULT_PARSER_CMD = ["CliReader", "export", "{vrf}", "--output", "{out}"]
FINISHED_KEPT = 50  # finished jobs whose results stay readable (oldest dropped first)

# User-facing reasons (the web service shows them as they are).
REASON_TIMEOUT = "parse timed out"
REASON_PARSE = "parse failed"


@dataclass
class Settings:
    parser_cmd: list[str] = field(default_factory=lambda: list(DEFAULT_PARSER_CMD))
    parser_build: Path | None = None
    temp_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    timeout_s: float = 180.0
    max_bytes: int = 80 * 1024 * 1024
    queue_size: int = 5
    memory_cap_mb: int = 0
    # Map control (docs/map-control-worker-plan.md): its own queue, a fixed pool of child processes.
    control_enabled: bool = True
    control_cmd: list[str] = field(default_factory=lambda: [sys.executable, "-m", "replay_worker.control_job"])
    control_workers: int = 2
    control_queue: int = 32
    control_timeout_s: float = 900.0
    control_warm_timeout_s: float = 1800.0
    control_memory_mb: int = 2048
    control_max_bytes: int = 4 * 1024 * 1024
    # The .vrf archive (replay_worker/archive.py): off unless the directory is a mounted, writable disk.
    archive_dir: Path | None = None
    archive_require_mount: bool = True
    archive_total_bytes: int | None = None  # tests only; the disk's own size otherwise

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        settings = cls()
        if env.get("REPLAY_PARSER_CMD"):
            settings.parser_cmd = json.loads(env["REPLAY_PARSER_CMD"])
        if env.get("REPLAY_PARSER_BUILD"):
            settings.parser_build = Path(env["REPLAY_PARSER_BUILD"])
        if env.get("REPLAY_WORKER_TMP"):
            settings.temp_root = Path(env["REPLAY_WORKER_TMP"])
        settings.timeout_s = float(env.get("REPLAY_TIMEOUT_S", settings.timeout_s))
        settings.max_bytes = int(env.get("REPLAY_MAX_BYTES", settings.max_bytes))
        settings.queue_size = int(env.get("REPLAY_QUEUE_SIZE", settings.queue_size))
        settings.memory_cap_mb = int(env.get("REPLAY_MEMORY_CAP_MB", settings.memory_cap_mb))
        settings.control_enabled = env.get("REPLAY_CONTROL", "1") != "0"
        if env.get("REPLAY_CONTROL_CMD"):
            settings.control_cmd = json.loads(env["REPLAY_CONTROL_CMD"])
        settings.control_workers = max(1, int(env.get("REPLAY_CONTROL_WORKERS", settings.control_workers)))
        settings.control_queue = int(env.get("REPLAY_CONTROL_QUEUE", settings.control_queue))
        settings.control_timeout_s = float(env.get("REPLAY_CONTROL_TIMEOUT_S", settings.control_timeout_s))
        settings.control_warm_timeout_s = float(env.get("REPLAY_CONTROL_WARM_TIMEOUT_S",
                                                        settings.control_warm_timeout_s))
        settings.control_memory_mb = int(env.get("REPLAY_CONTROL_MEMORY_MB", settings.control_memory_mb))
        if env.get("REPLAY_ARCHIVE_DIR"):
            settings.archive_dir = Path(env["REPLAY_ARCHIVE_DIR"])
        settings.archive_require_mount = env.get("REPLAY_ARCHIVE_REQUIRE_MOUNT", "1") != "0"
        return settings


@dataclass
class Job:
    id: str
    folder: Path
    sha256: str
    size: int
    status: str = "queued"
    error: str | None = None
    result: dict | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    parse_seconds: float | None = None
    kind: str = "upload"            # or "reparse" (archive on only)
    match_uuid: str | None = None   # known once parsed (a reparse knows it from the start)
    map: str | None = None
    attempt_id: str | None = None   # a reparse the automatic queue asked for (its receipt names this job)

    def public(self) -> dict:
        body = {"id": self.id, "status": self.status}
        if self.kind != "upload":
            body["kind"] = self.kind
        if self.error:
            body["error"] = self.error
        if self.result is not None:
            body["result"] = self.result
        if self.parse_seconds is not None:
            body["parse_seconds"] = self.parse_seconds
        return body


def kill_tree(process: subprocess.Popen) -> None:
    """Kill a child and everything it started; every wait is bounded. On Linux the child leads its own
    process group (setsid), so the group is killed even when the leader has already exited."""
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
                               check=False, timeout=10)
            except subprocess.TimeoutExpired:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _limit_memory(cap_mb: int):
    """A preexec hook that caps the parser's address space (Linux; `resource` isn't on Windows)."""
    def apply() -> None:
        os.setsid()
        if cap_mb:
            import resource
            limit = cap_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return apply


class NoSpace(Exception):
    """The archive disk can't take another upload without eating into the running parse's reserve."""


class Worker:
    """The queue, one worker thread, and the finished jobs' results. With the archive on, jobs live on
    its disk with a job.json each, and reparse jobs wait in their own queue behind every upload."""

    SWEEP_EVERY_S = 3600

    def __init__(self, settings: Settings, on_parse=None):
        self.settings = settings
        self.on_parse = on_parse or (lambda: None)   # map control's preempt (D4); always called outside self.lock
        self.parsing = False
        self.queue: queue.Queue[Job] = queue.Queue(maxsize=settings.queue_size)
        self.reparse_queue: queue.Queue[Job] = queue.Queue(maxsize=settings.queue_size)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        # Every admission to either queue, and every read or write of an attempt receipt, happens under this
        # one lock: a queue seen with room stays that way until the put, so nothing is accepted and then lost.
        self.admission = threading.Lock()
        self._before_reply = lambda answer: None  # tests only: runs after an attempt is settled, before its reply
        self.wake = threading.Event()
        try:
            self.recipe = current_recipe()
        except Exception:  # noqa: BLE001 - a worker that can't name its recipe still parses uploads
            traceback.print_exc()
            self.recipe = None
        self.archive, self.archive_reason = None, "REPLAY_ARCHIVE_DIR is not set"
        if settings.archive_dir is not None:
            self.archive, self.archive_reason = archive_store.open_archive(
                settings.archive_dir, require_mount=settings.archive_require_mount,
                total_bytes=settings.archive_total_bytes, max_bytes=settings.max_bytes,
                queue_size=settings.queue_size, log=_log)
            if self.archive is None:
                _log(f"archive off: {self.archive_reason}")
        self.last_sweep = 0.0
        if self.archive is not None:
            self._recover()
            self._recover_attempts()
        self.thread = threading.Thread(target=self._run, name="replay-worker", daemon=True)
        self.thread.start()

    @property
    def memory_cap(self) -> str:
        if not self.settings.memory_cap_mb:
            return "none"
        return f"{self.settings.memory_cap_mb} MB" if os.name != "nt" else "unsupported on this OS"

    def archive_status(self) -> dict:
        if self.archive is None:
            return {"enabled": False, "reason": self.archive_reason}
        return self.archive.status()

    def idle(self) -> bool:
        """No parse running and none waiting: map control may use its whole pool; otherwise one child.
        Between a queue's `get` and `parsing = True` this reads True for a few bytecodes; a second child
        started in that gap is killed by the `on_parse()` call at the top of `_run`
        (docs/superpowers/plans/2026-10-05-control-idle-queue.md, Task 2)."""
        return not self.parsing and self.queue.empty() and self.reparse_queue.empty()

    def _parse_coming(self) -> None:
        """Tells map control a parse is waiting or starting. A failing hook must never stop a parse."""
        try:
            self.on_parse()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    # -------------------------------------------------------------- intake

    def submit(self, body: bytes) -> Job:
        job_id = uuid.uuid4().hex
        with self.admission:
            if self.archive is None:
                folder = self.settings.temp_root / f"replay-job-{job_id}"
            else:
                if not self.archive.intake_ok(len(body)):
                    raise NoSpace()
                folder = self.archive.jobs / job_id
            folder.mkdir(parents=True)
            (folder / "upload.vrf").write_bytes(body)
            job = Job(job_id, folder, hashlib.sha256(body).hexdigest(), len(body))
            self._save(job)
            try:
                self.queue.put_nowait(job)
            except queue.Full:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        self._admitted(job)
        return job

    def _admitted(self, job: Job) -> None:
        """A job that is in its queue: tell map control, make it readable, wake the parse thread."""
        self._parse_coming()
        with self.lock:
            self.jobs[job.id] = job
        self.wake.set()

    def _copy_archived(self, match_uuid: str, folder: Path) -> tuple[str, int]:
        """The archived recording copied into a job folder: (its sha256, its size). FileNotFoundError when
        it was evicted or deleted since the caller looked."""
        folder.mkdir(parents=True)
        try:
            with self.archive.lock:
                shutil.copyfile(self.archive.path_of(match_uuid), folder / "upload.vrf")
        except FileNotFoundError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return archive_store.sha256_file(folder / "upload.vrf"), (folder / "upload.vrf").stat().st_size

    def submit_reparse(self, match_uuid: str) -> Job:
        """Raises LookupError (not archived), PermissionError (deleted on request), queue.Full."""
        match_uuid = match_uuid.lower()
        if self.archive is None or not self.archive.has(match_uuid):
            raise LookupError("this match has no archived file")
        if self.archive.is_tombstoned(match_uuid):
            raise PermissionError("this match was deleted on request")
        job_id = uuid.uuid4().hex
        folder = self.archive.jobs / job_id
        with self.admission:
            try:
                sha, size = self._copy_archived(match_uuid, folder)
            except FileNotFoundError:  # evicted or deleted since the check
                raise LookupError("this match has no archived file") from None
            job = Job(job_id, folder, sha, size, kind="reparse", match_uuid=match_uuid)
            self._save(job)
            try:
                self.reparse_queue.put_nowait(job)
            except queue.Full:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        self._admitted(job)
        return job

    # -------------------------------------------------------------- re-parse attempts (see the module docstring)

    @staticmethod
    def _attempt_identity(attempt_id, match_uuid, expected_sha256) -> tuple[str, str, str] | None:
        """(the canonical attempt UUID, the match UUID, the sha), all lower case; None when it isn't one."""
        try:
            attempt = str(uuid.UUID(str(attempt_id)))
        except ValueError:
            return None
        match_uuid, sha = str(match_uuid or "").strip().lower(), str(expected_sha256 or "").strip().lower()
        if not match_uuid or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            return None
        return attempt, match_uuid, sha

    def _attempt_job_status(self, receipt: dict) -> str:
        """The accepted job's status. `expired` once its folder was swept. `failed` when the disk holds
        another job under the id (a restored snapshot): that is never read as "not accepted yet"."""
        job = self.get(receipt["job_id"])
        if job is not None:
            record = {"status": job.status, "sha256": job.sha256, "match_uuid": job.match_uuid,
                      "attempt_id": job.attempt_id}
        else:
            record = self._attempt_record(self.archive.jobs / receipt["job_id"])
        if record is None:
            return "expired"
        same = all(str(record.get(k) or "").lower() == receipt[k] for k in ("attempt_id", "match_uuid", "sha256"))
        return record.get("status") or "failed" if same else "failed"

    def _attempt_answer(self, receipt: dict) -> dict:
        body = {"code": receipt["state"], "attempt_id": receipt["attempt_id"], "state": receipt["state"]}
        if receipt["state"] == "closed":
            return body
        body.update(match_uuid=receipt["match_uuid"], sha256=receipt["sha256"])
        if receipt["state"] == "accepted":
            body.update(job_id=receipt["job_id"], size=receipt.get("size"),
                        job_status=self._attempt_job_status(receipt))
        return body

    def _settled_receipt(self, attempt_hex: str) -> dict | None:
        """The receipt, with a preparation whose job record reached the disk read as the acceptance it is
        (the worker stopped between the two writes). Called under `admission`."""
        receipt = self.archive.receipt(attempt_hex)
        if receipt is not None and receipt.get("state") == "preparing":
            job_id = f"auto{attempt_hex}"
            record = self._attempt_record(self.archive.jobs / job_id)
            if record is not None:
                receipt = {**receipt, "state": "accepted", "job_id": job_id, "size": record.get("size")}
                self.archive.write_receipt(attempt_hex, receipt)
        return receipt

    @staticmethod
    def _attempt_record(folder: Path) -> dict | None:
        """Read an automatic job without confusing damaged state with an unfinished preparation."""
        record = archive_store.read_attempt_json(folder / "job.json")
        if record is None:
            return None
        try:
            valid = (record["id"] == folder.name and record["kind"] == "reparse"
                     and record["status"] in ("queued", "parsing", "done", "failed")
                     and isinstance(record["match_uuid"], str) and bool(record["match_uuid"])
                     and isinstance(record["sha256"], str) and len(record["sha256"]) == 64
                     and all(c in "0123456789abcdef" for c in record["sha256"])
                     and type(record["size"]) is int and record["size"] >= 0
                     and all(record.get(key) is None or type(record[key]) in (int, float)
                             for key in ("created", "finished", "parse_seconds")))
            uuid.UUID(record["attempt_id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise archive_store.AttemptStateError(f"invalid automatic job record {folder.name}")
        return record

    def reparse_attempt(self, attempt_id, match_uuid, expected_sha256) -> tuple[int, dict]:
        """POST /reparse with an attempt id: (HTTP status, answer)."""
        if self.archive is None:
            return HTTPStatus.NOT_FOUND, {"code": "archive_off", "error": "the archive is off"}
        identity = self._attempt_identity(attempt_id, match_uuid, expected_sha256)
        if identity is None:
            return HTTPStatus.BAD_REQUEST, {"code": "bad_request",
                                            "error": "attempt_id must be a UUID and expected_sha256 64 hex digits"}
        attempt, match_uuid, sha = identity
        attempt_hex = attempt.replace("-", "")
        job_id = f"auto{attempt_hex}"
        folder = self.archive.jobs / job_id
        with self.admission:
            receipt = self._settled_receipt(attempt_hex)
            if receipt is not None:
                if (receipt["match_uuid"], receipt["sha256"]) != (match_uuid, sha):
                    return HTTPStatus.CONFLICT, {"code": "identity_conflict",
                                                 "error": "this attempt id was used for another match or file"}
                if receipt["state"] == "closed":
                    return HTTPStatus.CONFLICT, self._attempt_answer(receipt)
                if receipt["state"] == "accepted":
                    return HTTPStatus.OK, self._attempt_answer(receipt)
            # New, or a preparation that never reached its job record: the same preparation, from the top.
            shutil.rmtree(folder, ignore_errors=True)
            if self.archive.is_tombstoned(match_uuid):
                self.archive.drop_receipt(attempt_hex)
                return HTTPStatus.CONFLICT, {"code": "deleted", "error": "this match was deleted on request"}
            if not self.archive.has(match_uuid):
                self.archive.drop_receipt(attempt_hex)
                return HTTPStatus.NOT_FOUND, {"code": "no_archived_file", "error": "this match has no archived file"}
            if self.reparse_queue.full():
                return HTTPStatus.SERVICE_UNAVAILABLE, {"code": "queue_full", "error": "the reparse queue is full"}
            receipt = {"attempt_id": attempt, "match_uuid": match_uuid, "sha256": sha, "state": "preparing",
                       "created": time.time()}
            self.archive.write_receipt(attempt_hex, receipt)
            try:
                actual, size = self._copy_archived(match_uuid, folder)
            except FileNotFoundError:
                self.archive.drop_receipt(attempt_hex)
                return HTTPStatus.NOT_FOUND, {"code": "no_archived_file", "error": "this match has no archived file"}
            if actual != sha:
                shutil.rmtree(folder, ignore_errors=True)
                self.archive.drop_receipt(attempt_hex)
                return HTTPStatus.CONFLICT, {"code": "sha_mismatch", "sha256": actual,
                                             "error": "the archived recording is another file"}
            job = Job(job_id, folder, actual, size, kind="reparse", match_uuid=match_uuid, attempt_id=attempt)
            self._save(job, strict=True)
            receipt = {**receipt, "state": "accepted", "job_id": job_id, "size": size, "accepted": time.time()}
            try:
                self.archive.write_receipt(attempt_hex, receipt)
            except OSError:
                # The preparing receipt and job.json already durably identify this acceptance. A failed
                # final receipt write must not strand that job until restart; lookup can promote it later.
                traceback.print_exc()
            self.reparse_queue.put_nowait(job)   # room was seen under this lock, and only the parse thread takes
        self._admitted(job)
        return HTTPStatus.ACCEPTED, self._attempt_answer(receipt)

    def attempt_status(self, attempt_id) -> tuple[int, dict]:
        """GET /reparse/attempts/{id}. Reads only: nothing is enqueued or prepared here."""
        if self.archive is None:
            return HTTPStatus.NOT_FOUND, {"code": "archive_off", "error": "the archive is off"}
        try:
            attempt_hex = uuid.UUID(str(attempt_id)).hex
        except ValueError:
            return HTTPStatus.BAD_REQUEST, {"code": "bad_request", "error": "attempt_id must be a UUID"}
        with self.admission:
            receipt = self._settled_receipt(attempt_hex)
            if receipt is None:
                return HTTPStatus.NOT_FOUND, {"code": "unknown_attempt", "error": "no such attempt"}
            return HTTPStatus.OK, self._attempt_answer(receipt)

    def close_attempt(self, attempt_id, match_uuid, expected_sha256) -> tuple[int, dict]:
        """POST /reparse/attempts/{id}/close: the accepted job, or a durable fence against a late POST."""
        if self.archive is None:
            return HTTPStatus.NOT_FOUND, {"code": "archive_off", "error": "the archive is off"}
        identity = self._attempt_identity(attempt_id, match_uuid, expected_sha256)
        if identity is None:
            return HTTPStatus.BAD_REQUEST, {"code": "bad_request",
                                            "error": "attempt_id must be a UUID and expected_sha256 64 hex digits"}
        attempt, match_uuid, sha = identity
        attempt_hex = attempt.replace("-", "")
        with self.admission:
            receipt = self._settled_receipt(attempt_hex)
            if receipt is not None and (receipt["match_uuid"], receipt["sha256"]) != (match_uuid, sha):
                return HTTPStatus.CONFLICT, {"code": "identity_conflict",
                                             "error": "this attempt id was used for another match or file"}
            if receipt is None or receipt["state"] == "preparing":
                shutil.rmtree(self.archive.jobs / f"auto{attempt_hex}", ignore_errors=True)
                receipt = {"attempt_id": attempt, "match_uuid": match_uuid, "sha256": sha, "state": "closed",
                           "closed": time.time()}
                self.archive.write_receipt(attempt_hex, receipt)
            return HTTPStatus.OK, self._attempt_answer(receipt)

    def _recover_attempts(self) -> None:
        """After a restart, before anything is served: a preparation whose job record is on the disk becomes
        the acceptance it was about to be. `_recover` has already queued that job (once) or removed the folder
        of a preparation that got no further, which a retry of the id then starts again."""
        rebuilt = 0
        for path in self.archive.attempts.glob("*.json"):
            try:
                before = self.archive.receipt(path.stem)
                after = self._settled_receipt(path.stem)
                rebuilt += bool(before and after and before.get("state") != after.get("state"))
            except Exception:  # noqa: BLE001 - one unreadable receipt never stops the worker starting
                traceback.print_exc()
        if rebuilt:
            _log(f"archive on: {rebuilt} re-parse attempt(s) were accepted just before the restart")

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def ack(self, job_id: str, body: dict) -> tuple[int, dict]:
        if self.archive is None:
            return HTTPStatus.OK, {"archived": False, "reason": "archive off"}
        job = self.get(job_id)
        if job is not None:
            record = {"status": job.status, "match_uuid": job.match_uuid, "sha256": job.sha256, "kind": job.kind,
                      "map": job.map}
        else:  # forgotten from memory (FINISHED_KEPT) or from before a restart: its record on disk
            folder = self.archive.jobs / job_id
            record = archive_store.read_json(folder / "job.json", None) if job_id.isalnum() else None
        if record is not None and record.get("status") in ("queued", "parsing"):
            return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the job isn't finished; ack it again later"}
        meta = None
        if record is not None and record.get("status") == "done":
            meta = {k: record.get(k) for k in ("match_uuid", "sha256", "kind", "map")}
        return self.archive.ack(job_id, body, meta)

    # -------------------------------------------------------------- the job

    def _next(self) -> Job:
        """Uploads first, then reparses. With the archive off, exactly the old blocking get."""
        if self.archive is None:
            job = self.queue.get()
            self.parsing = True
            return job
        while True:
            for source in (self.queue, self.reparse_queue):
                try:
                    job = source.get_nowait()
                    self.parsing = True
                    return job
                except queue.Empty:
                    pass
            if time.time() - self.last_sweep > self.SWEEP_EVERY_S:
                self._sweep()
            self.wake.wait(timeout=1.0)
            self.wake.clear()

    def _run(self) -> None:
        while True:
            job = self._next()
            self._parse_coming()
            status, error, result = "failed", REASON_PARSE, None
            try:
                job.status = "parsing"
                self._save(job)
                if self.archive is not None:
                    if job.kind == "reparse" and self.archive.is_tombstoned(job.match_uuid):
                        raise _JobFailed("this match was deleted on request")
                    self.archive.evict()
                result, status, error = self._process(job), "done", None
            except _JobFailed as failed:
                error = failed.reason
            except ContractError as refused:
                error = f"not condensable: {refused.reason}"
            except Exception:  # noqa: BLE001 - one bad file must not stop the worker
                traceback.print_exc()
            finally:
                if self.archive is None:
                    # The temp folder goes first: a job reads as finished only once it's gone.
                    shutil.rmtree(job.folder, ignore_errors=True)
                else:
                    self._settle(job, status, result)
                job.result, job.error, job.finished = result, error, time.time()
                job.status = status
                self._save(job)
                self._forget_old()
                (self.queue if job.kind == "upload" else self.reparse_queue).task_done()
                self.parsing = False

    def _settle(self, job: Job, status: str, result: dict | None) -> None:
        """Archive on: the export goes, the result is written, a parsed upload waits in pending/ for its
        ack, and anything else of the upload is deleted. Errors here never stop the worker."""
        try:
            shutil.rmtree(job.folder / "export", ignore_errors=True)
            vrf = job.folder / "upload.vrf"
            if status == "done" and result is not None:
                archive_store.write_json(job.folder / "result.json", result)
                if job.kind == "upload":
                    self.archive.hold_pending(job.id, vrf, {"match_uuid": job.match_uuid, "sha256": job.sha256,
                                                            "size": job.size, "map": job.map})
            vrf.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _process(self, job: Job) -> dict:
        vrf = job.folder / "upload.vrf"
        out = job.folder / "export"
        command = [part.replace("{vrf}", str(vrf)).replace("{out}", str(out)) for part in self.settings.parser_cmd]
        started = time.perf_counter()
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": str(job.folder)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["preexec_fn"] = _limit_memory(self.settings.memory_cap_mb)
        process = subprocess.Popen(command, **kwargs)
        try:
            code = process.wait(timeout=self.settings.timeout_s)
        except subprocess.TimeoutExpired:
            kill_tree(process)
            raise _JobFailed(REASON_TIMEOUT)
        job.parse_seconds = round(time.perf_counter() - started, 1)
        if code != 0 or not (out / "manifest.json").exists():
            raise _JobFailed(REASON_PARSE)
        build = None
        if self.settings.parser_build is not None:
            build = json.loads(self.settings.parser_build.read_text(encoding="utf-8-sig"))
        # The match UUID comes from the file's own header, never its name (check_file_name=False).
        replay = condense_export_dir(out, source_sha256=job.sha256, build=build, vrf_path=vrf, check_file_name=False)
        if self.archive is not None:
            shutil.rmtree(out, ignore_errors=True)  # the export is ~65x the file; the result is all we need
            if job.kind == "reparse" and replay.match_uuid.lower() != job.match_uuid:
                raise _JobFailed("the archived file is another match")
        job.match_uuid, job.map = replay.match_uuid.lower(), replay.map_name
        return {
            "match_uuid": replay.match_uuid, "map_name": replay.map_name, "game_branch": replay.game_branch,
            "source_sha256": replay.source_sha256, "recipe": replay.recipe, "hz": replay.hz,
            "round_count": replay.round_count,
            "rounds": {str(n): base64.b64encode(data).decode("ascii") for n, data in replay.encoded_rounds().items()},
            "players": replay.players, "link_inputs": replay.link_inputs,
            # The web service stores this (app/replays/store.py): dropped_final_round goes into
            # link_inputs, and the rest is a small summary for the job page.
            "report": {k: replay.report.get(k) for k in ("rounds", "hz", "kills", "sizes", "lifecycle", "sides",
                                                          "dropped_final_round", "extras")},
        }

    def _forget_old(self) -> None:
        with self.lock:
            finished = sorted((j for j in self.jobs.values() if j.finished), key=lambda j: j.finished)
        discard = finished[:-FINISHED_KEPT] if FINISHED_KEPT else finished
        for job in discard:
            if self.archive is None or self._drop_job_folder(job):
                with self.lock:
                    self.jobs.pop(job.id, None)

    def _drop_job_folder(self, job: Job) -> bool:
        """Keep the acceptance evidence until its unswept receipt is durable, even after repeated I/O errors."""
        with self.admission:
            if job.attempt_id:
                try:
                    receipt = self._settled_receipt(job.attempt_id.replace("-", ""))
                except (archive_store.AttemptStateError, OSError):
                    traceback.print_exc()
                    return False
                if (receipt is None or receipt["state"] != "accepted"
                        or (receipt["job_id"], receipt["match_uuid"], receipt["sha256"])
                        != (job.id, job.match_uuid, job.sha256)):
                    return False
            shutil.rmtree(job.folder, ignore_errors=True)
        return True

    # -------------------------------------------------------------- the archive's job records

    def _save(self, job: Job, strict: bool = False) -> None:
        """`strict` lets the error out: an attempt is accepted only once its job record is on the disk."""
        if self.archive is None:
            return
        record = {"id": job.id, "kind": job.kind, "status": job.status, "error": job.error, "sha256": job.sha256,
                  "size": job.size, "created": job.created, "finished": job.finished,
                  "parse_seconds": job.parse_seconds, "match_uuid": job.match_uuid, "map": job.map}
        if job.attempt_id:
            record["attempt_id"] = job.attempt_id
        try:
            archive_store.write_json(job.folder / "job.json", record)
        except OSError:
            if strict:
                raise
            traceback.print_exc()

    def _recover(self) -> None:
        """After a restart: queued and parsing jobs are queued again in creation order (a parse starts
        over), done and failed ones are readable again. A folder without a job.json is debris."""
        records = []
        for folder in self.archive.jobs.iterdir():
            if folder.name.startswith("auto"):
                try:
                    record = self._attempt_record(folder)
                except archive_store.AttemptStateError:
                    # Leave evidence for the attempt routes to report as unavailable. Deleting it would
                    # turn an accepted attempt into a new preparation on the next retry.
                    traceback.print_exc()
                    continue
            else:
                record = archive_store.read_json(folder / "job.json", None)
            if not folder.is_dir() or record is None:
                shutil.rmtree(folder, ignore_errors=True)
                continue
            records.append((record.get("created") or 0, folder, record))
        for _created, folder, record in sorted(records, key=lambda r: r[0]):
            job = Job(record["id"], folder, record["sha256"], record["size"], status=record["status"],
                      error=record.get("error"), created=record.get("created") or time.time(),
                      finished=record.get("finished"), parse_seconds=record.get("parse_seconds"),
                      kind=record.get("kind") or "upload", match_uuid=record.get("match_uuid"), map=record.get("map"),
                      attempt_id=record.get("attempt_id"))
            shutil.rmtree(folder / "export", ignore_errors=True)
            if job.status in ("queued", "parsing"):
                job.status = "queued"
                target = self.queue if job.kind == "upload" else self.reparse_queue
                try:
                    if not (folder / "upload.vrf").exists():
                        raise queue.Full
                    target.put_nowait(job)
                except queue.Full:
                    job.status, job.error, job.finished = "failed", "the worker restarted: please re-upload", time.time()
                    (folder / "upload.vrf").unlink(missing_ok=True)
                self._save(job)
            elif job.status == "done":
                job.result = archive_store.read_json(folder / "result.json", None)
                if job.result is None:
                    job.status, job.error = "failed", REASON_PARSE
            with self.lock:
                self.jobs[job.id] = job
        _log(f"archive on: recovered {len(records)} job(s); {self.queue.qsize()} upload(s) and "
             f"{self.reparse_queue.qsize()} reparse(s) queued again")
        self._sweep()

    def _sweep(self) -> None:
        """Pending files and ack records past their TTL, and finished job folders nobody collected."""
        self.last_sweep = time.time()
        try:
            self.archive.sweep()
            cutoff = time.time() - archive_store.UNCOLLECTED_TTL_S
            with self.lock:
                stale = [j for j in self.jobs.values() if j.finished and j.finished < cutoff]
            for job in stale:
                if self._drop_job_folder(job):
                    with self.lock:
                        self.jobs.pop(job.id, None)
                    _log(f"job {job.id} was never collected; deleted")
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- map control (docs/map-control-worker-plan.md)

CONTROL_FINISHED_KEPT = 200
# The control child's task and result protocol, told to the web app in /health: 1 means a task's `gaps` block
# is understood (replay_worker/control_job.py; the image ships app/gaps). A plain number here, never read
# from the detector: this process imports none of it.
GAPS_PROTOCOL = 1
CHILD_THREADS = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


@dataclass
class ControlJob:
    id: str
    key: str
    map: str
    task: bytes
    status: str = "queued"
    error: str | None = None
    error_kind: str | None = None
    result: dict | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def public(self) -> dict:
        body = {"id": self.id, "key": self.key, "status": self.status}
        if self.error:
            body.update({"error": self.error, "error_kind": self.error_kind})
        if self.result is not None:
            body["result"] = self.result
        return body


def _child_setup(memory_mb: int):
    """A preexec hook for a control child (Linux): its own process group, nice 10 so a parse wins
    the CPU, and an address-space cap."""
    def apply() -> None:
        os.setsid()
        os.nice(10)
        if memory_mb:
            import resource
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return apply


class ControlRunner:
    """Map control's queue, beside the parse queue and never sharing its thread. At most
    `control_workers` children at once, and one beside a parse; a map's first round runs alone ("warming": it builds the
    map's visibility cache) and the map is warm once one of its rounds gets past loading. A task's
    key (replay, round, fingerprint) is deduped while its job is queued, running or done."""

    def __init__(self, settings: Settings, idle=None):
        self.settings = settings
        self.lock = threading.Lock()
        self.jobs: dict[str, ControlJob] = {}
        self.by_key: dict[str, str] = {}
        self.pending: list[str] = []
        self.running: dict[str, bool] = {}     # job id -> warming
        self.warm: set[str] = set()
        self.idle = idle or (lambda: True)     # the parse side has nothing running or waiting (D4)
        self.procs: dict[str, subprocess.Popen] = {}
        self.preempted: set[str] = set()       # running jobs a parse has killed; settled as requeued (D4)
        self.preempted_total = 0
        self._before_settle = lambda job: None  # tests only: runs after the child ends, before settlement
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._schedule, name="control-scheduler", daemon=True)
        self.thread.start()

    @property
    def enabled(self) -> bool:
        return self.settings.control_enabled

    def submit(self, task: dict) -> ControlJob:
        """Raises queue.Full when the queue is full."""
        key = str(task.get("key"))
        with self.lock:
            existing = self.jobs.get(self.by_key.get(key, ""))
            if existing is not None and existing.status in ("queued", "running", "done"):
                return existing
            if len(self.pending) >= self.settings.control_queue:
                raise queue.Full
            job = ControlJob(uuid.uuid4().hex, key, str(task["map"]), json.dumps(task).encode("utf-8"))
            self.jobs[job.id] = job
            self.by_key[key] = job.id
            self.pending.append(job.id)
        self.wake.set()
        return job

    def get(self, job_id: str) -> ControlJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def counts(self) -> dict:
        with self.lock:
            return {"queued": len(self.pending), "running": len(self.running), "warm": sorted(self.warm),
                    "preempted": self.preempted_total}

    def preempt(self) -> int:
        """A parse is coming: kill every running child but the one started first, so the parse has a CPU and
        control keeps one (D4 as amended 2026-10-07; the child started last has done the least work).
        `self.running` keeps start order. How each run ends is decided at settlement, under the lock, where a
        preempted mark wins over everything else."""
        with self.lock:
            alive = [job_id for job_id in self.running if job_id not in self.preempted]
            victims = [(job_id, self.procs.get(job_id)) for job_id in alive[1:]]
            self.preempted.update(job_id for job_id, _ in victims)
        for _, process in victims:
            if process is not None:
                try:
                    kill_tree(process)
                except Exception:  # noqa: BLE001 - a parse must never fail because a kill did
                    traceback.print_exc()
        return len(victims)

    def _next(self) -> tuple[ControlJob, bool] | None:
        """The next job that may start now, and whether it warms its map (called under the lock)."""
        # Beside a parse (running or waiting) control keeps one child; idle, the whole pool.
        limit = self.settings.control_workers if self.idle() else 1
        if len(self.running) >= limit:
            return None
        busy_maps = {self.jobs[j].map for j in self.running}
        warming_maps = {self.jobs[j].map for j, warming in self.running.items() if warming}
        for job_id in self.pending:
            job = self.jobs[job_id]
            if job.map in self.warm:
                if job.map in warming_maps:
                    continue
                return job, False
            if job.map not in busy_maps:
                return job, True
        return None

    def _schedule(self) -> None:
        while True:
            self.wake.wait(timeout=1.0)
            self.wake.clear()
            while True:
                with self.lock:
                    found = self._next()
                    if found is None:
                        break
                    job, warming = found
                    self.pending.remove(job.id)
                    self.running[job.id] = warming
                    job.status = "running"
                threading.Thread(target=self._run, args=(job, warming), name=f"control-{job.id[:8]}",
                                 daemon=True).start()

    def _run(self, job: ControlJob, warming: bool) -> None:
        settings = self.settings
        timeout = settings.control_warm_timeout_s if warming else settings.control_timeout_s
        env = {**os.environ, **CHILD_THREADS}
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(WEBAPP), str(WEBAPP.parent), env.get("PYTHONPATH")) if p)
        kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env,
                  "cwd": str(WEBAPP.parent)}
        if os.name != "nt":
            kwargs["preexec_fn"] = _child_setup(settings.control_memory_mb)
        status, error, kind, result = "failed", None, "infra", None
        process = None
        try:
            # Started outside the lock (preexec_fn must not run while this thread holds a lock another
            # needs), registered under it, and killed at once if a preempt landed in between.
            process = subprocess.Popen(settings.control_cmd, **kwargs)
            with self.lock:
                self.procs[job.id] = process
                killed_before_start = job.id in self.preempted
            if killed_before_start:
                kill_tree(process)
            stdout, _ = process.communicate(job.task, timeout=timeout)
            answer = json.loads(stdout or b"{}")
            if answer.get("status") == "ok":
                status, kind, result = "done", None, answer
            else:
                error = answer.get("error") or f"control child exited {process.returncode}"
                kind = answer.get("error_kind") or "infra"
        except subprocess.TimeoutExpired:
            error = f"control timed out after {timeout:g} s"
        except (OSError, ValueError) as failure:
            error = f"control child failed: {failure}"
        except Exception:  # noqa: BLE001 - one bad round must not stop the runner
            traceback.print_exc()
            error = "control child failed"
        finally:
            # A timeout leaves the child running: kill it, and close the pipes rather than drain them, so
            # a descendant holding one can't hang this thread.
            if process is not None:
                try:
                    kill_tree(process)                 # bounded; a no-op for a child that exited cleanly
                except Exception:  # noqa: BLE001 - settlement below must always run, or the slot is lost
                    traceback.print_exc()
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        if stream is not None:
                            stream.close()
                    except OSError:
                        pass
        try:
            self._before_settle(job)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        with self.lock:
            self.procs.pop(job.id, None)
            self.running.pop(job.id, None)
            if job.id in self.preempted:
                # D4: whatever the child did, a parse killed this run. Back to the front, uncounted, task
                # kept, warmth untouched.
                self.preempted.discard(job.id)
                self.preempted_total += 1
                job.status = "queued"
                self.pending.insert(0, job.id)
            else:
                job.status, job.error, job.error_kind, job.result = status, error, kind, result
                job.finished = time.time()
                job.task = b""
                # Past loading (ok, or the round's own failure): the map's cache is there. Any machine
                # failure makes the map cold again, so its next round warms alone.
                if status == "done" or kind == "engine":
                    self.warm.add(job.map)
                else:
                    self.warm.discard(job.map)
                finished = sorted((j for j in self.jobs.values() if j.finished), key=lambda j: j.finished)
                for old in finished[:-CONTROL_FINISHED_KEPT]:
                    del self.jobs[old.id]
                    if self.by_key.get(old.key) == old.id:
                        del self.by_key[old.key]
        self.wake.set()


class _JobFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------- HTTP


def make_handler(worker: Worker, control: ControlRunner | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "replay-worker"

        def log_message(self, fmt, *args):  # noqa: A003 - quiet by default; one line per request
            sys.stderr.write("%s %s\n" % (self.command, self.path))

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                body = {"ok": True, "queued": worker.queue.qsize(),
                        "limits": {"timeout_s": worker.settings.timeout_s, "max_bytes": worker.settings.max_bytes,
                                   "queue_size": worker.settings.queue_size, "memory_cap": worker.memory_cap}}
                body["control"] = {"enabled": bool(control and control.enabled), "gaps_protocol": GAPS_PROTOCOL,
                                   **(control.counts() if control else {})}
                body["archive"] = worker.archive_status()
                body["recipe"] = worker.recipe
                return self._send(HTTPStatus.OK, body)
            if self.path.startswith("/reparse/attempts/"):
                try:
                    answer = worker.attempt_status(self.path[len("/reparse/attempts/"):])
                except (archive_store.AttemptStateError, OSError):
                    return self._attempt_unavailable()
                return self._send(*answer)
            if self.path == "/archive":
                if worker.archive is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "the archive is off"})
                return self._send(HTTPStatus.OK, {"files": worker.archive.entries(),
                                                  "status": worker.archive.status()})
            if self.path.startswith("/control/"):
                job = control.get(self.path[len("/control/"):]) if control and control.enabled else None
                if job is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "no such control job"})
                return self._send(HTTPStatus.OK, job.public())
            if self.path.startswith("/jobs/"):
                job = worker.get(self.path[len("/jobs/"):])
                if job is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "no such job"})
                return self._send(HTTPStatus.OK, job.public())
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path == "/control":
                return self._control()
            if self.path.startswith("/jobs/") and self.path.endswith("/ack"):
                return self._ack(self.path[len("/jobs/"):-len("/ack")])
            if self.path.startswith("/reparse/attempts/") and self.path.endswith("/close"):
                return self._attempt_post(self.path[len("/reparse/attempts/"):-len("/close")])
            if self.path in ("/reparse", "/archive/delete", "/archive/tombstones"):
                return self._archive_post()
            if self.path != "/jobs":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            length = self.headers.get("Content-Length")
            if length is None:
                return self._send(HTTPStatus.LENGTH_REQUIRED, {"error": "a Content-Length is required"})
            size = int(length)
            if size > worker.settings.max_bytes:
                self.close_connection = True
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "file too large"})
            body = self.rfile.read(size)
            if len(body) != size or not body.startswith(VRF_MAGIC):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a Valorant replay"})
            try:
                job = worker.submit(body)
            except (queue.Full, NoSpace):
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the worker is busy, try again soon"})
            return self._send(HTTPStatus.ACCEPTED, {"id": job.id, "status": job.status})

        def _json(self, limit: int = 1 << 20) -> dict | None:
            length = self.headers.get("Content-Length")
            if length is None or int(length) > limit:
                self.close_connection = True
                return None
            try:
                body = json.loads(self.rfile.read(int(length)) or b"{}")
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def _ack(self, job_id: str):
            body = self._json()
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not an ack"})
            status, answer = worker.ack(job_id, body)
            return self._send(status, answer)

        def _attempt_post(self, close_id: str | None = None, body: dict | None = None):
            """A re-parse attempt (the body names an attempt id) or the close of one. Every answer has a code."""
            body = self._json() if body is None else body
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"code": "bad_request", "error": "not JSON"})
            try:
                if close_id is not None:
                    status, answer = worker.close_attempt(close_id, body.get("match_uuid"), body.get("expected_sha256"))
                else:
                    status, answer = worker.reparse_attempt(body.get("attempt_id"), body.get("match_uuid"),
                                                            body.get("expected_sha256"))
                    worker._before_reply(answer)
            except (archive_store.AttemptStateError, OSError):
                return self._attempt_unavailable()
            return self._send(status, answer)

        def _attempt_unavailable(self):
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                              {"code": "state_unavailable", "error": "the attempt's durable state is unavailable; retry later"})

        def _archive_post(self):
            body = self._json()
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not JSON"})
            if self.path == "/reparse" and "attempt_id" in body:
                return self._attempt_post(body=body)   # the manual route below is untouched by the protocol
            if worker.archive is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "the archive is off"})
            if self.path == "/archive/tombstones":
                uuids = body.get("match_uuids")
                if not isinstance(uuids, list):
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": "match_uuids must be a list"})
                return self._send(HTTPStatus.OK, {"removed": worker.archive.set_tombstones(uuids),
                                                  "boot_id": worker.archive.boot_id})
            match_uuid = str(body.get("match_uuid") or "").lower()
            if not match_uuid:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "match_uuid is required"})
            if self.path == "/archive/delete":
                return self._send(HTTPStatus.OK, {"removed": worker.archive.delete(match_uuid)})
            try:
                job = worker.submit_reparse(match_uuid)
            except LookupError as missing:
                return self._send(HTTPStatus.NOT_FOUND, {"error": str(missing)})
            except PermissionError as deleted:
                return self._send(HTTPStatus.CONFLICT, {"error": str(deleted)})
            except queue.Full:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the reparse queue is full"})
            return self._send(HTTPStatus.ACCEPTED, {"id": job.id, "status": job.status, "kind": job.kind,
                                                    "sha256": job.sha256, "size": job.size})

        def _control(self):
            if control is None or not control.enabled:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "map control is off on this worker"})
            length = self.headers.get("Content-Length")
            if length is None:
                return self._send(HTTPStatus.LENGTH_REQUIRED, {"error": "a Content-Length is required"})
            size = int(length)
            if size > control.settings.control_max_bytes:
                self.close_connection = True
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "task too large"})
            try:
                task = json.loads(self.rfile.read(size))
                if not all(k in task for k in ("key", "map", "blob", "link")):
                    raise ValueError("missing fields")
            except ValueError:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a control task"})
            try:
                job = control.submit(task)
            except queue.Full:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the control queue is full"})
            return self._send(HTTPStatus.ACCEPTED, {"id": job.id, "status": job.status})

    return Handler


def make_server(worker: Worker, host: str = "127.0.0.1", port: int = 0,
                control: ControlRunner | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(worker, control))


def main() -> None:
    settings = Settings.from_env()
    control = ControlRunner(settings) if settings.control_enabled else None
    worker = Worker(settings, on_parse=control.preempt if control else None)
    if control is not None:
        # Wired after the Worker exists. Until then idle() reads True, but the control queue is empty: the
        # web app hasn't sent anything to a server that isn't listening yet.
        control.idle = worker.idle
    server = make_server(worker, os.environ.get("REPLAY_WORKER_HOST", "0.0.0.0"),
                         int(os.environ.get("REPLAY_WORKER_PORT", "8080")), control)
    print(f"replay worker on {server.server_address}, timeout {settings.timeout_s:g} s, "
          f"queue {settings.queue_size}, cap {settings.max_bytes} bytes", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

"""The replay upload worker: parses and condenses one `.vrf` at a time (Stage 3, W-c).

Standard library only. It holds no DB credentials and no secrets; it only parses. The friends
web service streams an uploaded file to it over Render's private network and polls for the
result, then stores and links it itself (docs/replay-viewer-plan.md, "Upload").

    POST /jobs        body: the .vrf bytes. 202 {"id", "status": "queued"}; 400 not a replay,
                      411 no length, 413 over the size cap, 503 the queue is full.
    GET  /jobs/{id}   {"id", "status": "queued" | "parsing" | "done" | "failed", "error"?, "result"?}
    GET  /health      {"ok": true, "queued", "limits", "control": {"enabled", "queued", "running", "warm"}}
    POST /control     body: JSON {key, map, blob (base64), link}: one round's map control
                      (docs/map-control-worker-plan.md). 202 {"id", "status"} (the same job for a key
                      it already has), 400 not a task, 404 control is off, 413 too large, 503 full.
    GET  /control/{id} {"id", "key", "status": "queued" | "running" | "done" | "failed",
                      "error"?, "error_kind"? ("engine" | "infra"), "result"?}

Map control has its own queue and never shares the parse thread: a fixed pool of child processes
(`python -m replay_worker.control_job`, run by the control venv's interpreter; this process never
imports numpy or the engine), niced, time- and memory-capped, one map's first round alone.

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
    REPLAY_CONTROL_WORKERS   children at once (default 2; never from the core count, which a
                             container reports for the host)
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
from app.replays.contract import ContractError  # noqa: E402

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
    """Kill the parser and everything it started."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
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
        self.wake = threading.Event()
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
        """No parse running and none waiting: map control may run. Between a queue's `get` and
        `parsing = True` this reads True for a few bytecodes; a control child started in that gap is
        killed by the `on_parse()` call at the top of `_run`
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
        self._parse_coming()
        with self.lock:
            self.jobs[job_id] = job
        self.wake.set()
        return job

    def submit_reparse(self, match_uuid: str) -> Job:
        """Raises LookupError (not archived), PermissionError (deleted on request), queue.Full."""
        match_uuid = match_uuid.lower()
        if self.archive is None or not self.archive.has(match_uuid):
            raise LookupError("this match has no archived file")
        if self.archive.is_tombstoned(match_uuid):
            raise PermissionError("this match was deleted on request")
        job_id = uuid.uuid4().hex
        folder = self.archive.jobs / job_id
        folder.mkdir(parents=True)
        try:
            with self.archive.lock:
                shutil.copyfile(self.archive.path_of(match_uuid), folder / "upload.vrf")
        except FileNotFoundError:  # evicted or deleted since the check
            shutil.rmtree(folder, ignore_errors=True)
            raise LookupError("this match has no archived file") from None
        sha = archive_store.sha256_file(folder / "upload.vrf")
        job = Job(job_id, folder, sha, (folder / "upload.vrf").stat().st_size, kind="reparse", match_uuid=match_uuid)
        self._save(job)
        try:
            self.reparse_queue.put_nowait(job)
        except queue.Full:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._parse_coming()
        with self.lock:
            self.jobs[job_id] = job
        self.wake.set()
        return job

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
            for job in finished[:-FINISHED_KEPT]:
                del self.jobs[job.id]
                if self.archive is not None:
                    shutil.rmtree(job.folder, ignore_errors=True)

    # -------------------------------------------------------------- the archive's job records

    def _save(self, job: Job) -> None:
        if self.archive is None:
            return
        try:
            archive_store.write_json(job.folder / "job.json", {
                "id": job.id, "kind": job.kind, "status": job.status, "error": job.error, "sha256": job.sha256,
                "size": job.size, "created": job.created, "finished": job.finished,
                "parse_seconds": job.parse_seconds, "match_uuid": job.match_uuid, "map": job.map})
        except OSError:
            traceback.print_exc()

    def _recover(self) -> None:
        """After a restart: queued and parsing jobs are queued again in creation order (a parse starts
        over), done and failed ones are readable again. A folder without a job.json is debris."""
        records = []
        for folder in self.archive.jobs.iterdir():
            record = archive_store.read_json(folder / "job.json", None)
            if not folder.is_dir() or record is None:
                shutil.rmtree(folder, ignore_errors=True)
                continue
            records.append((record.get("created") or 0, folder, record))
        for _created, folder, record in sorted(records, key=lambda r: r[0]):
            job = Job(record["id"], folder, record["sha256"], record["size"], status=record["status"],
                      error=record.get("error"), created=record.get("created") or time.time(),
                      finished=record.get("finished"), parse_seconds=record.get("parse_seconds"),
                      kind=record.get("kind") or "upload", match_uuid=record.get("match_uuid"), map=record.get("map"))
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
                    del self.jobs[job.id]
            for job in stale:
                shutil.rmtree(job.folder, ignore_errors=True)
                _log(f"job {job.id} was never collected; deleted")
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- map control (docs/map-control-worker-plan.md)

CONTROL_FINISHED_KEPT = 200
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
    `control_workers` children at once; a map's first round runs alone ("warming": it builds the
    map's visibility cache) and the map is warm once one of its rounds gets past loading. A task's
    key (replay, round, fingerprint) is deduped while its job is queued, running or done."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.jobs: dict[str, ControlJob] = {}
        self.by_key: dict[str, str] = {}
        self.pending: list[str] = []
        self.running: dict[str, bool] = {}     # job id -> warming
        self.warm: set[str] = set()
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
            return {"queued": len(self.pending), "running": len(self.running), "warm": sorted(self.warm)}

    def _next(self) -> tuple[ControlJob, bool] | None:
        """The next job that may start now, and whether it warms its map (called under the lock)."""
        if len(self.running) >= self.settings.control_workers:
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
        kwargs = {"input": job.task, "capture_output": True, "timeout": timeout, "env": env,
                  "cwd": str(WEBAPP.parent)}
        if os.name != "nt":
            kwargs["preexec_fn"] = _child_setup(settings.control_memory_mb)
        status, error, kind, result = "failed", None, "infra", None
        try:
            completed = subprocess.run(settings.control_cmd, check=False, **kwargs)
            answer = json.loads(completed.stdout or b"{}")
            if answer.get("status") == "ok":
                status, kind, result = "done", None, answer
            else:
                error = answer.get("error") or f"control child exited {completed.returncode}"
                kind = answer.get("error_kind") or "infra"
        except subprocess.TimeoutExpired:
            error = f"control timed out after {timeout:g} s"
        except (OSError, ValueError) as failure:
            error = f"control child failed: {failure}"
        except Exception:  # noqa: BLE001 - one bad round must not stop the runner
            traceback.print_exc()
            error = "control child failed"
        with self.lock:
            job.status, job.error, job.error_kind, job.result = status, error, kind, result
            job.finished = time.time()
            job.task = b""
            self.running.pop(job.id, None)
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
                body["control"] = {"enabled": bool(control and control.enabled),
                                   **(control.counts() if control else {})}
                body["archive"] = worker.archive_status()
                return self._send(HTTPStatus.OK, body)
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

        def _archive_post(self):
            body = self._json()
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not JSON"})
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
    server = make_server(Worker(settings), os.environ.get("REPLAY_WORKER_HOST", "0.0.0.0"),
                         int(os.environ.get("REPLAY_WORKER_PORT", "8080")), control)
    print(f"replay worker on {server.server_address}, timeout {settings.timeout_s:g} s, "
          f"queue {settings.queue_size}, cap {settings.max_bytes} bytes", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

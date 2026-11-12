"""The replay upload worker: parses and condenses one `.vrf` at a time (Stage 3, W-c).

Standard library only. It holds no DB credentials and no secrets; it only parses. The friends
web service streams an uploaded file to it over Render's private network and polls for the
result, then stores and links it itself (docs/replay-viewer-plan.md, "Upload").

    POST /jobs        body: the .vrf bytes. 202 {"id", "status": "queued" (or "held"/"failed", below)}; 400 not a replay,
                      411 no length, 413 over the size cap, 503 the queue is full.
    GET  /jobs/{id}   {"id", "status": "queued" | "parsing" | "held" | "done" | "failed", "error"?, "result"?,
                      "detail"? (the parser's last lines when a parse failed), "game_build"? (held uploads, may
                      be unknown even when held)}
                      "held": two reasons, told apart by `detail`/the error text, not by `game_build` alone --
                      a game build the pinned parser doesn't list (read from the file's header before any
                      parse; parsed once an image whose parser supports the build starts), or a build the pin
                      file already lists where the parser refuses the file anyway (parsed again once a deploy
                      changes this worker's recipe -- see UNSUPPORTED_PATCH_SIGNATURE). Both: archive on only;
                      archive off, or over HELD_MAX_BYTES, fails at once instead.
    GET  /health      {"ok": true, "queued", "limits", "recipe" (what a parse here stamps; null if unknown),
                       "control": {"enabled", "gaps_protocol", "queued", "running", "warm", "preempted"},
                       "archive": {"enabled", ..., "reparse_protocol" (archive on only)},
                       "idle" (a height build may start: no parse
                       running or waiting, or builds run as Workflow runs),
                       "heights": {"collecting", "queued", "running" (map names), "spool_bytes"}}
    POST /control     body: JSON {key, map, blob (base64), link}: one round's map control
                      (docs/map-control-worker-plan.md). 202 {"id", "status"} (the same job for a key
                      it already has), 400 not a task, 404 control is off, 409 {error, height} when the task
                      names a height digest this worker doesn't have, 413 too large, 503 full.
    GET  /control/{id} {"id", "key", "status": "queued" | "running" | "done" | "failed",
                      "error"?, "error_kind"? ("engine" | "infra"), "result"?}
    POST /heights     body: JSON {map, digest, asset (base64 .npz)}: a map's height asset, kept in the control
                      cache for the rounds that name it. 200 {"stored": true}, 400 not one, 404 control is off,
                      413 too large.

    GET  /runs/{job}/input/{name}   (header X-Run-Token) a Workflow run's input file, streamed; 404 otherwise.
    POST /runs/{job}/result         (header X-Run-Token) the run's JSON answer: 200 taken, 409 refused (wrong
                                    token, unknown or already settled run), 413, 400.
    POST /runs/{job}/started        (header X-Run-Token) an attempt has begun: its limit counts from now. 200; 409.
    POST /runs/{job}/output         (header X-Run-Token, util runs only) the run's output, streamed to disk:
                                    200 {"bytes", "sha256"}, 409 refused, 413 past util_result_max.
    POST /runs/ping                 starts a ping run and waits for it: {"ok", "answer", "seconds"}.

    POST /util/build                {key, digest, rows, obs, refit, context (base64), ref?}: 202 the build
                                    {"id", "key", "values_key", "status" (queued | running | done | failed), "digest",
                                    "version", "refit", "bytes", "sha256", "error"?, "error_kind"?} (the same build
                                    for a key it has, unless it failed); 409 {"missing_rows", "missing_obs"}; 400.
    GET  /util/build/{id}           the build; 404.
    GET  /util/build/{id}/result    exactly the bytes the run streamed; 404 until the build is done.
These exist only when RENDER_API_KEY and REPLAY_WORKFLOW_SLUG are set (replay_worker/runs.py).

Height builds (HeightBuilds; docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): a map's heights
built here from its stored rounds, by one more control child (`python -m replay_worker.height_job`) that runs
alone and ahead of every control round, and that a parse preempts like one. Off with map control.

    POST /heights/build              {key, map, rounds, manifest}: 202 the build {"id", "key", "map", "status",
                                     "received", "expected"} (the same build for a key it already has, unless that
                                     one failed); 400 not a build; 503 too many collecting.
    POST /heights/build/{id}/rounds  {"rounds": [{"match", "n", "blob" (base64)}]}: 200 {"received"};
                                     409 {"error": "not collecting", "status"} or {"error": "other bytes", "match",
                                     "n"}; 413 the spool is full; 400; 404.
    POST /heights/build/{id}/start   {"previous"?}: 202 the build; 409 {"error": "rounds are missing", "received",
                                     "expected"}; 404.
    POST /heights/build/{id}/cancel  200 {"cancelled": true}; 409 {"error": "not collecting", "status"}; 404.
    GET  /heights/build/{id}         the build, with "result" when done and "error" when failed.

A build is always in one of five states, and every answer says which, so the web app picks up wherever it was
after a restart on either side:

    collecting        send rounds, start, cancel. The same round with the same bytes is acknowledged again for
                      free; the same round with other bytes is a conflict.
    queued, running   wait and ask. Sending rounds is a conflict that says the state; starting again answers it.
    done              read the result; it stays readable for the newest HEIGHT_BUILDS_KEPT builds.
    failed            open it again: the same key starts a fresh, empty build.
    unknown (404)     open it again: the worker restarted, or dropped a collector nobody fed.

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
    REPLAY_CONTROL_RUNS      runs at once with REPLAY_CONTROL_RUNNER=workflow (default 40);
                             REPLAY_CONTROL_WORKERS is the local pool
    REPLAY_WARM_BUILDS       "true": with REPLAY_CONTROL_RUNNER=workflow, a cold identity's visibility cache is one
                             warm build (replay_tasks warm) before its rounds, then one probe round, then the rest at
                             once (default off; a restart clears failed-build counts);
    REPLAY_WARM_BUILD_TIMEOUT_S  a warm build's limit (default 21600; Render's task limit is 300 s above it);
    REPLAY_CONTROL_RUN_TIMEOUT_S / REPLAY_HEIGHT_RUN_TIMEOUT_S  a round's / height build's limit on Workflows
                             (defaults 1800 / 3600); local children keep REPLAY_CONTROL_TIMEOUT_S,
                             REPLAY_CONTROL_WARM_TIMEOUT_S, REPLAY_HEIGHT_TIMEOUT_S;
    REPLAY_CONTROL_QUEUE     waiting rounds (default 32)
    REPLAY_CONTROL_TIMEOUT_S / REPLAY_CONTROL_WARM_TIMEOUT_S   per round (1800) / a map's first (1800)
    REPLAY_CONTROL_MEMORY_MB child address-space cap, Linux only (default 2048)
    CONTROL_CACHE_DIR        the control children's cache; pushed height assets go in its `heights` folder
                             (default webapp/.control_cache)
    REPLAY_CONTROL_CACHE_RESERVE_BYTES  bytes the Workflow control cache may hold (default 2 GiB);
                             the archive leaves them free.
    REPLAY_HEIGHT_CMD        JSON list: the height-build child (default this Python, -m replay_worker.height_job)
    REPLAY_HEIGHT_TIMEOUT_S  per build (default 3600)
    REPLAY_UTIL_RUN_TIMEOUT_S  a Critical Util build's limit on Workflows (default 1500)
    REPLAY_ARCHIVE_DIR       the archive disk's mount point (default unset: no archive)
    REPLAY_ARCHIVE_REQUIRE_MOUNT  "0" skips the mount-point check (tests and local runs only)
    REPLAY_PARSE_RUNNER / REPLAY_CONTROL_RUNNER / REPLAY_HEIGHTS_RUNNER   "local" (default) or "workflow"
    REPLAY_WORKFLOW_SLUG     the workflow service's slug (e.g. replay-tasks)
    RENDER_API_KEY           a Render API key (set by hand in the dashboard, never in render.yaml)
    REPLAY_TASK_STARTS_PER_MIN  run starts per minute (default 1000: a safety valve; a 429 waits and retries)
"""

from __future__ import annotations

import base64
import collections
import hashlib
import io
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

from app.critutil import build_digest  # noqa: E402 - standard library only; never app.critutil.corpus (numpy)
from app.replays.condense import condense_export_dir  # noqa: E402
from app.replays.contract import ContractError, current_recipe, load_pin  # noqa: E402
from app.replays.header import game_build  # noqa: E402

try:  # run as `python -m replay_worker.server` (the container) or imported by the tests
    from replay_worker import archive as archive_store  # noqa: E402
    from replay_worker import control_cache  # noqa: E402
    from replay_worker import runs as runs_mod  # noqa: E402
except ImportError:  # pragma: no cover - run as a plain script from its folder
    import archive as archive_store  # type: ignore[no-redef]  # noqa: E402
    import control_cache  # type: ignore[no-redef]  # noqa: E402
    import runs as runs_mod  # type: ignore[no-redef]  # noqa: E402

VRF_MAGIC = (0x43F4EFDD).to_bytes(4, "little")
ARCHIVE_CHUNK_BYTES = 1 << 20
DEFAULT_PARSER_CMD = ["CliReader", "export", "{vrf}", "--output", "{out}"]
FINISHED_KEPT = 50  # finished jobs whose results stay readable (oldest dropped first)

# User-facing reasons (the web service shows them as they are).
try:
    from replay_worker.parse_job import (REASON_PARSE, REASON_TIMEOUT, ParseFailed, kill_tree,  # noqa: E402
                                         limit_memory as _limit_memory, parse_file, parser_detail)
except ImportError:  # pragma: no cover
    from parse_job import (REASON_PARSE, REASON_TIMEOUT, ParseFailed, kill_tree,  # type: ignore  # noqa: E402
                           limit_memory as _limit_memory, parse_file, parser_detail)

# Two situations hold a file on disk under the same job id instead of parsing it, both only with the archive on
# (with it off, both fail at once instead): an upload recorded on a game build the pinned parser doesn't list
# (replay_parser.json), parsed once an image whose parser supports the build starts; or a build the pin file
# already lists, where the parser itself refuses the file anyway (see UNSUPPORTED_PATCH_SIGNATURE below), held
# until a deploy with a different recipe. A held job runs only when no upload or reparse waits. Held files count
# against the archive's budget and are capped here.
HELD_MAX_BYTES = 10 * 1024 ** 3
HELD_TTL_S = 60 * 86400
DEPLOY_WAIT_S = 60      # before a new parse run after a deploy refusal, within the job's deadline (spec D11)
INFRA_WAIT_S = 30       # before a new parse run after a run that failed for want of a machine, within the deadline
# A build the pin file already lists as supported, but the parser itself refuses once actually run on it (an
# undocumented sub-version gap): the parser's own last line says so. Held like an unlisted build (above), but
# released on the next deploy with a different recipe, never on a supported-builds match (it's already "supported").
UNSUPPORTED_PATCH_SIGNATURE = "may not be supported yet"


def _version(build: str) -> str:
    """"++Ares-Core+release-13.08" as people say it: "13.08"."""
    return build.rsplit("release-", 1)[-1]


def held_reason(build: str) -> str:
    return (f"recorded on game version {_version(build)}, which the replay parser doesn't support yet: the file is "
            f"kept and will be parsed when the parser is updated")


def parser_refused_reason(detail: str) -> str:
    return (f"the replay parser refused this file ({detail}): it is kept and will be retried after the parser "
            "is next updated")


@dataclass
class Settings:
    parser_cmd: list[str] = field(default_factory=lambda: list(DEFAULT_PARSER_CMD))
    parser_build: Path | None = None
    temp_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    timeout_s: float = 180.0
    max_bytes: int = 80 * 1024 * 1024
    queue_size: int = 5
    parse_parallel: int = 15                     # parse runs at once with REPLAY_PARSE_RUNNER=workflow (spec D15)
    parse_run_timeout_s: float = 1200.0          # one parse run: download, parse, condense, post
    memory_cap_mb: int = 0
    # Map control (docs/map-control-worker-plan.md): its own queue, a fixed pool of child processes.
    control_enabled: bool = True
    control_cmd: list[str] = field(default_factory=lambda: [sys.executable, "-m", "replay_worker.control_job"])
    control_workers: int = 2
    control_runs: int = 40                  # runs at once with control_runner == "workflow"
    archive_extra_reserve: int = 0          # bytes the archive leaves free for the control cache (main() sets it)
    control_queue: int = 32
    control_timeout_s: float = 1800.0
    control_warm_timeout_s: float = 1800.0
    warm_builds: bool = False               # REPLAY_WARM_BUILDS: a cold identity's visibility cache as its own run
    warm_build_timeout_s: float = 21600.0   # REPLAY_WARM_BUILD_TIMEOUT_S
    control_run_timeout_s: float = 1800.0   # a control round on Workflows (REPLAY_CONTROL_RUN_TIMEOUT_S); local
                                            # children keep control_timeout_s / control_warm_timeout_s
    height_run_timeout_s: float = 3600.0    # a height build on Workflows (REPLAY_HEIGHT_RUN_TIMEOUT_S); local keeps
                                            # height_timeout_s
    control_memory_mb: int = 2048
    control_max_bytes: int = 4 * 1024 * 1024
    # Where control children keep their caches (CONTROL_CACHE_DIR, as app/control/geometry.py reads it); pushed
    # height assets live in its `heights` folder.
    control_cache_dir: Path = field(default_factory=lambda: WEBAPP / ".control_cache")
    control_cache_reserve: int = control_cache.RESERVE   # bytes the cache may hold (REPLAY_CONTROL_CACHE_RESERVE_BYTES)
    # Height builds (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): one more kind of control
    # child, run alone and ahead of control rounds. Their rounds are spooled under `temp_root`.
    height_cmd: list[str] = field(default_factory=lambda: [sys.executable, "-m", "replay_worker.height_job"])
    height_timeout_s: float = 3600.0
    height_spool_bytes: int = 512 * 1024 * 1024      # every build's rounds together
    height_builds_max: int = 2                       # unfinished builds (collecting, queued or running), one per map
    height_spool_dir: Path | None = None             # None keeps `temp_root/height_builds`
    height_collect_ttl_s: float = 3600.0             # a build nobody has sent a round to for this long is dropped
    # The .vrf archive (replay_worker/archive.py): off unless the directory is a mounted, writable disk.
    archive_dir: Path | None = None
    archive_require_mount: bool = True
    archive_total_bytes: int | None = None  # tests only; the disk's own size otherwise
    supported_builds: frozenset | None = None  # tests only; the pin's supported_replay_builds otherwise
    # Render Workflows (docs/superpowers/specs/2026-10-14-render-workflows-design.md): which kind of work runs as
    # a Workflow run instead of a child here. "local" (default) is today's child process.
    parse_runner: str = "local"
    control_runner: str = "local"
    heights_runner: str = "local"
    workflow_slug: str | None = None              # the workflow service's slug, e.g. "replay-tasks"
    render_api_key: str | None = field(default=None, repr=False)      # never in a printed Settings
    render_api_base: str = runs_mod.API
    task_starts_per_min: int = 1000               # a safety valve only (spec D10)
    run_result_max_bytes: int = 256 * 1024 ** 2   # a parse result, decoded in memory once (spec D18)
    # The Critical Util input store (UtilStore): None keeps `<archive_dir>/util`, else `<temp_root>/util`.
    util_dir: Path | None = None                  # REPLAY_UTIL_DIR
    util_reserve: int = 2 * 1024 ** 3             # REPLAY_UTIL_RESERVE: the most the stored rounds + observations hold
    util_run_timeout_s: float = 1500.0            # REPLAY_UTIL_RUN_TIMEOUT_S: one util build on Workflows

    @property
    def util_result_max(self) -> int:
        return self.run_result_max_bytes

    def util_root(self) -> Path:
        if self.util_dir is not None:
            return self.util_dir
        return (self.archive_dir if self.archive_dir is not None else self.temp_root) / "util"

    def workflow_wanted(self) -> bool:
        return "workflow" in (self.parse_runner, self.control_runner, self.heights_runner)

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
        settings.control_runs = max(1, int(env.get("REPLAY_CONTROL_RUNS", settings.control_runs)))
        settings.parse_parallel = max(1, int(env.get("REPLAY_PARSE_PARALLEL", settings.parse_parallel)))
        settings.parse_run_timeout_s = float(env.get("REPLAY_PARSE_RUN_TIMEOUT_S", settings.parse_run_timeout_s))
        settings.control_queue = int(env.get("REPLAY_CONTROL_QUEUE", settings.control_queue))
        settings.control_timeout_s = float(env.get("REPLAY_CONTROL_TIMEOUT_S", settings.control_timeout_s))
        settings.control_warm_timeout_s = float(env.get("REPLAY_CONTROL_WARM_TIMEOUT_S",
                                                        settings.control_warm_timeout_s))
        settings.warm_builds = env.get("REPLAY_WARM_BUILDS", "").strip().lower() in ("1", "true")
        settings.warm_build_timeout_s = float(env.get("REPLAY_WARM_BUILD_TIMEOUT_S", settings.warm_build_timeout_s))
        settings.control_run_timeout_s = float(env.get("REPLAY_CONTROL_RUN_TIMEOUT_S", settings.control_run_timeout_s))
        settings.height_run_timeout_s = float(env.get("REPLAY_HEIGHT_RUN_TIMEOUT_S", settings.height_run_timeout_s))
        settings.control_memory_mb = int(env.get("REPLAY_CONTROL_MEMORY_MB", settings.control_memory_mb))
        settings.control_cache_reserve = int(env.get("REPLAY_CONTROL_CACHE_RESERVE_BYTES",
                                                     settings.control_cache_reserve))
        if env.get("CONTROL_CACHE_DIR"):
            settings.control_cache_dir = Path(env["CONTROL_CACHE_DIR"])
        if env.get("REPLAY_HEIGHT_CMD"):
            settings.height_cmd = json.loads(env["REPLAY_HEIGHT_CMD"])
        settings.height_timeout_s = float(env.get("REPLAY_HEIGHT_TIMEOUT_S", settings.height_timeout_s))
        settings.height_builds_max = max(1, int(env.get("REPLAY_HEIGHT_BUILDS_MAX", settings.height_builds_max)))
        settings.height_spool_bytes = int(env.get("REPLAY_HEIGHT_SPOOL_BYTES", settings.height_spool_bytes))
        if env.get("REPLAY_HEIGHT_SPOOL_DIR"):
            settings.height_spool_dir = Path(env["REPLAY_HEIGHT_SPOOL_DIR"])
        if env.get("REPLAY_ARCHIVE_DIR"):
            settings.archive_dir = Path(env["REPLAY_ARCHIVE_DIR"])
        settings.archive_require_mount = env.get("REPLAY_ARCHIVE_REQUIRE_MOUNT", "1") != "0"
        if env.get("REPLAY_UTIL_DIR"):
            settings.util_dir = Path(env["REPLAY_UTIL_DIR"])
        settings.util_reserve = int(env.get("REPLAY_UTIL_RESERVE", settings.util_reserve))
        settings.util_run_timeout_s = float(env.get("REPLAY_UTIL_RUN_TIMEOUT_S", settings.util_run_timeout_s))
        for name in ("parse", "control", "heights"):
            value = env.get(f"REPLAY_{name.upper()}_RUNNER", "local")
            if value not in ("local", "workflow"):
                raise ValueError(f"REPLAY_{name.upper()}_RUNNER must be local or workflow, not {value!r}")
            setattr(settings, f"{name}_runner", value)
        settings.workflow_slug = env.get("REPLAY_WORKFLOW_SLUG") or None
        settings.render_api_key = env.get("RENDER_API_KEY") or None
        settings.render_api_base = env.get("REPLAY_RENDER_API", settings.render_api_base)
        settings.task_starts_per_min = int(env.get("REPLAY_TASK_STARTS_PER_MIN", settings.task_starts_per_min))
        return settings


def make_runs(settings: Settings):
    """The run table, or None when no Render key and slug are set. A runner set to `workflow` without them stops
    the worker here, at startup, rather than failing every job later."""
    if not (settings.render_api_key and settings.workflow_slug):
        if settings.workflow_wanted():
            raise SystemExit("a REPLAY_*_RUNNER is 'workflow' but RENDER_API_KEY or REPLAY_WORKFLOW_SLUG is not set")
        return None
    api = runs_mod.RenderApi(settings.render_api_key, base=settings.render_api_base)
    return runs_mod.Runs(api, settings.workflow_slug, runs_mod.Pacer(settings.task_starts_per_min),
                         settings.temp_root / "runs")


def reserve_util(settings: Settings, runs_table) -> None:
    """The archive leaves room for the util store's folders (rounds + observations, their packed tars, and results)
    only when the store lives under the archive and Workflows are on, as heights reserve their spool."""
    if runs_table is None or settings.archive_dir is None:
        return
    root, archive = settings.util_root().resolve(), settings.archive_dir.resolve()
    if root.is_relative_to(archive) and root != archive:
        settings.archive_extra_reserve += 2 * settings.util_reserve + 4 * settings.util_result_max


LOCAL_QUEUE_SIZE = 5       # the image's REPLAY_QUEUE_SIZE before Workflows (replay_worker/Dockerfile)
LOCAL_SPOOL_BYTES = 512 * 1024 * 1024      # Settings.height_spool_bytes' default before Workflows


def keep_local_sizing(settings: Settings) -> None:
    """A kind still on its local runner keeps today's sizing (spec D2: local is today's child processes, and
    rollback is one env var). render.yaml sets the Workflow sizing for every deploy, so it only takes effect for a
    kind switched to `workflow`; otherwise the archive would lose the room it reserves for nothing."""
    if settings.control_runner != "workflow" and settings.archive_dir is not None and \
            settings.control_cache_dir.resolve().is_relative_to(settings.archive_dir.resolve()):
        # What a Workflow spell left on the disk stays (a switch back finds the maps warm), so the archive
        # reserves what it holds now; it can only shrink, since nothing local writes there.
        left = settings.control_cache_dir
        settings.archive_extra_reserve += control_cache.tree_bytes(left) if left.is_dir() else 0
        settings.control_cache_dir = settings.temp_root / "control_cache"     # the image's own, as before
    if settings.parse_runner != "workflow":
        # One parse at a time: today's waiting line, and the archive's parse reserve that goes with it.
        settings.queue_size = min(settings.queue_size, LOCAL_QUEUE_SIZE)
    if settings.heights_runner != "workflow":
        # One local build at a time: today's spool in the temp folder, so the archive doesn't reserve 4 GiB for it.
        spool = settings.height_spool_dir
        if spool is not None and settings.archive_dir is not None and \
                spool.resolve().is_relative_to(settings.archive_dir.resolve()) and \
                spool.resolve() != settings.archive_dir.resolve():           # never the archive itself
            # A build in flight when the switch went back to local left its rounds and packed tar on the disk.
            # Builds live in memory only (HeightBuilds forgets them on every start), so nothing will read them
            # again, and nothing reserves their room any more: drop them.
            shutil.rmtree(spool, ignore_errors=True)
            shutil.rmtree(spool.parent / "height_runs", ignore_errors=True)
        settings.height_spool_dir = None
        settings.height_spool_bytes = min(settings.height_spool_bytes, LOCAL_SPOOL_BYTES)


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
    game_build: str | None = None   # set while held (either kind: an unsupported build, or one the parser
                                     # refused anyway -- `parser_pin` tells the two apart, not this alone)
    detail: str | None = None       # the parser's own last lines when a parse failed
    parser_pin: str | None = None   # set only when held after the parser itself refused a pinned build (below):
                                     # this deploy's recipe, so a later deploy with a different one releases it
    released: bool = False          # a held job taken straight to the parse thread, not from a queue (not saved)
    run_id: str | None = None
    run_token: str | None = None
    run_deadline: float | None = None

    def public(self) -> dict:
        body = {"id": self.id, "status": self.status}
        if self.kind != "upload":
            body["kind"] = self.kind
        if self.error:
            body["error"] = self.error
        if self.detail:
            body["detail"] = self.detail
        if self.game_build:
            body["game_build"] = self.game_build
        if self.result is not None:
            body["result"] = self.result
        if self.parse_seconds is not None:
            body["parse_seconds"] = self.parse_seconds
        return body


class NoSpace(Exception):
    """The archive disk can't take another upload without eating into the running parse's reserve."""


class Worker:
    """The queue, one worker thread, and the finished jobs' results. With the archive on, jobs live on
    its disk with a job.json each, and reparse jobs wait in their own queue behind every upload."""

    SWEEP_EVERY_S = 3600

    def __init__(self, settings: Settings, on_parse=None, runs_table=None):
        self.settings = settings
        self.runs = runs_table
        self.on_parse = on_parse or (lambda: None)   # map control's preempt (D4); always called outside self.lock
        self.parse_count = 0
        self.sweep_lock = threading.Lock()
        self.recovered: collections.deque[Job] = collections.deque()
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
        self.supported_builds = settings.supported_builds
        if self.supported_builds is None:
            try:
                self.supported_builds = load_pin().supported_builds
            except Exception:  # noqa: BLE001 - without the list nothing is held: every upload is parsed as before
                traceback.print_exc()
        self.archive, self.archive_reason = None, "REPLAY_ARCHIVE_DIR is not set"
        if settings.archive_dir is not None:
            self.archive, self.archive_reason = archive_store.open_archive(
                settings.archive_dir, require_mount=settings.archive_require_mount,
                total_bytes=settings.archive_total_bytes, max_bytes=settings.max_bytes,
                queue_size=settings.queue_size, log=_log)
            if self.archive is None:
                _log(f"archive off: {self.archive_reason}")
            else:
                self.archive.held_bytes = self.held_bytes
                self.archive.cache_reserve = settings.archive_extra_reserve
        self.last_sweep = 0.0
        if self.archive is not None:
            self._recover()
            self._recover_attempts()
        threads = self.settings.parse_parallel if (self.runs is not None and
                                                   self.settings.parse_runner == "workflow") else 1
        self.threads = [threading.Thread(target=self._run, name=f"replay-worker-{i}", daemon=True)
                        for i in range(threads)]
        for thread in self.threads:
            thread.start()
        self.thread = self.threads[0]

    @property
    def parsing(self) -> bool:
        return self.parse_count > 0

    def _parse_started(self) -> None:
        with self.lock:
            self.parse_count += 1

    def _parse_ended(self) -> None:
        with self.lock:
            self.parse_count -= 1

    @property
    def memory_cap(self) -> str:
        if not self.settings.memory_cap_mb:
            return "none"
        return f"{self.settings.memory_cap_mb} MB" if os.name != "nt" else "unsupported on this OS"

    def archive_status(self) -> dict:
        if self.archive is None:
            return {"enabled": False, "reason": self.archive_reason}
        with self.lock:
            held = [j.game_build for j in self.jobs.values() if j.status == "held"]
        return {**self.archive.status(), "held": len(held), "held_bytes": self.held_bytes(),
                "held_builds": sorted({b for b in held if b})}  # a parser-refused hold may have no known build

    def held_bytes(self) -> int:
        with self.lock:
            return sum(j.size for j in self.jobs.values() if j.status == "held")

    def idle(self) -> bool:
        """No parse running and none waiting: map control may use its whole pool; otherwise one child.
        Between a queue's `get` and `parsing = True` this reads True for a few bytecodes; a second child
        started in that gap is killed by the `on_parse()` call at the top of `_run`
        (docs/superpowers/plans/2026-10-05-control-idle-queue.md, Task 2)."""
        if self.runs is not None and self.settings.parse_runner == "workflow":
            return True     # a Workflow parse takes no CPU here, so it holds back nothing local (spec D17)
        return (not self.parsing and not self.recovered and self.queue.empty()
                and self.reparse_queue.empty())

    def _parse_coming(self) -> None:
        """Tells map control a parse is waiting or starting. A failing hook must never stop a parse."""
        try:
            self.on_parse()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    # -------------------------------------------------------------- intake

    def reserve_folder(self, size: int) -> tuple[str, Path]:
        """A new job folder for an upload of `size` bytes about to stream in. With the archive on, it is under
        jobs/ on the disk, where a folder without job.json is debris to the next recovery."""
        job_id = uuid.uuid4().hex
        with self.admission:
            if self.archive is None:
                folder = self.settings.temp_root / f"replay-job-{job_id}"
            else:
                if not self.archive.intake_ok(size):
                    raise NoSpace()
                folder = self.archive.jobs / job_id
            folder.mkdir(parents=True)
        return job_id, folder

    def submit_streamed(self, job_id: str, folder: Path, sha256: str, size: int) -> Job:
        """Today's submit, with the upload already at `folder/upload.vrf`. Any failure deletes the folder."""
        try:
            with self.admission:
                # intake_ok counts what jobs/ holds, this file included: the disk still has room now it's written.
                if self.archive is not None and not self.archive.intake_ok(0):
                    raise NoSpace()
                job = Job(job_id, folder, sha256, size)
                job.game_build = self._unsupported_build(folder / "upload.vrf")
                if job.game_build is not None:
                    self._hold(job)
                else:
                    # Registered before it is queued: a parse thread can dequeue and start on it the instant
                    # `put_nowait` returns, and a parser-refusal hold's budget check (self.jobs.values()) must
                    # already see it by then, not after.
                    with self.lock:
                        self.jobs[job.id] = job
                    self._save(job)
                    self.queue.put_nowait(job)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if job.status != "queued":
            with self.lock:
                self.jobs[job.id] = job
            return job
        self._parse_coming()
        self.wake.set()
        return job

    def submit(self, body: bytes) -> Job:
        """For tests and callers with the bytes in hand; the upload route streams (submit_streamed)."""
        job_id, folder = self.reserve_folder(len(body))
        try:
            (folder / "upload.vrf").write_bytes(body)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return self.submit_streamed(job_id, folder, hashlib.sha256(body).hexdigest(), len(body))

    def _unsupported_build(self, vrf: Path) -> str | None:
        """The file's game build when the parser is known not to support it, else None (supported, or unknown)."""
        if not self.supported_builds:
            return None
        try:
            build = game_build(vrf)
        except OSError:
            return None
        return build if build is not None and build not in self.supported_builds else None

    def _hold(self, job: Job) -> None:
        """Keeps an upload the parser can't read yet (archive on, under the cap), else fails it without a parse."""
        version = _version(job.game_build)
        held = False
        if self.archive is not None:
            # Shares this budget check-and-reserve with a parser-refusal hold (below): both decide and (for this
            # one) become visible to that sum together, under one lock, so neither overcounts the other's room.
            with self.lock:
                held_bytes = sum(j.size for j in self.jobs.values() if j.status == "held")
                if held_bytes + job.size <= HELD_MAX_BYTES:
                    job.status, job.error, held = "held", held_reason(job.game_build), True
                    self.jobs[job.id] = job
        if held:
            _log(f"job {job.id} held: {job.game_build} is not supported by the pinned parser")
        else:
            job.status, job.finished = "failed", time.time()
            job.error = (f"recorded on game version {version}, which the replay parser doesn't support yet: keep the "
                         f"file and upload it again when the parser is updated")
            if self.archive is None:
                shutil.rmtree(job.folder, ignore_errors=True)
            else:
                (job.folder / "upload.vrf").unlink(missing_ok=True)
        self._save(job)

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
        if record is not None and record.get("status") in ("queued", "parsing", "held"):
            return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the job isn't finished; ack it again later"}
        meta = None
        if record is not None and record.get("status") == "done":
            meta = {k: record.get(k) for k in ("match_uuid", "sha256", "kind", "map")}
        return self.archive.ack(job_id, body, meta)

    # -------------------------------------------------------------- the job

    def _next(self) -> Job:
        """Uploads first, then reparses, then the oldest held upload ready for another try. With the archive off,
        exactly the old blocking get."""
        if self.archive is None:
            job = self.queue.get()
            self._parse_started()
            return job
        while True:
            try:
                job = self.recovered.popleft()
                self._parse_started()
                return job
            except IndexError:
                pass
            for source in (self.queue, self.reparse_queue):
                try:
                    job = source.get_nowait()
                    self._parse_started()
                    return job
                except queue.Empty:
                    pass
            job = self._release_held()
            if job is not None:
                self._parse_started()
                return job
            if time.time() - self.last_sweep > self.SWEEP_EVERY_S and self.sweep_lock.acquire(blocking=False):
                try:
                    self._sweep()
                finally:
                    self.sweep_lock.release()
            self.wake.wait(timeout=1.0)
            self.wake.clear()

    def _release_held(self) -> Job | None:
        """The oldest held upload ready for another try, now queued; None when there is none. Held jobs never sit
        in a queue, so however many there are, new uploads are never turned away for them. Two kinds release on
        different signals: a build the pin file didn't list yet releases once it's added to the supported-builds
        list; a build the pin file already listed, where the parser itself refused the file (`parser_pin` set),
        releases once this deploy's recipe differs from the one it was held under -- whatever changed may have
        fixed it, and there is no narrower signal than "the parser changed"."""
        with self.lock:
            ready = sorted((j for j in self.jobs.values() if j.status == "held" and (
                            (j.parser_pin is None and j.game_build in (self.supported_builds or ()))
                            or (j.parser_pin is not None and self.recipe is not None and j.parser_pin != self.recipe))),
                           key=lambda j: j.created)
            if not ready:
                return None
            job = ready[0]
            was_parser_refused, job.status, job.error, job.released = job.parser_pin is not None, "queued", None, True
        self._save(job)
        reason = "the parser changed since it refused this file" if was_parser_refused \
            else f"the parser now supports {job.game_build}"
        _log(f"job {job.id} released: {reason}")
        return job

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
                # Upload only (a held reparse would look "pending" to the automatic queue forever, blocking
                # it -- a reparse just fails and the next sweep tries again); the worker's own recipe must be
                # known (else this collides with the unlisted-build release signal, see _release_held); and the
                # detail must be an exact parser line, not any message that happens to contain the phrase.
                if (error == REASON_PARSE and job.kind == "upload" and self.recipe is not None
                        and job.detail and UNSUPPORTED_PATCH_SIGNATURE in job.detail.rsplit(" | ", 1)[-1]
                        and self.archive is not None):
                    try:
                        build = game_build(job.folder / "upload.vrf")
                    except OSError:
                        build = None
                    refused_text = parser_refused_reason(job.detail)
                    # One lock, one write: every field a reader could see -- status, game_build, parser_pin,
                    # error, and the run fields a held job doesn't carry -- together, so nobody (in particular
                    # _release_held or a status poll, both on other threads) can ever see "held" with any of
                    # the rest still blank or stale.
                    held_now = False
                    with self.lock:   # _hold (intake) and held_bytes() share this same budget under this lock
                        held_bytes = sum(j.size for j in self.jobs.values() if j.status == "held")
                        if held_bytes + job.size <= HELD_MAX_BYTES:
                            job.status, job.game_build, job.parser_pin = "held", build, self.recipe
                            job.error, job.finished = refused_text, None
                            job.run_id = job.run_token = job.run_deadline = None
                            held_now = True
                    if held_now:
                        status, error = "held", refused_text
                        self._save(job)
                        _log(f"job {job.id} held: the parser refused it ({job.detail})")
            except ContractError as refused:
                error = f"not condensable: {refused.reason}"
            except Exception:  # noqa: BLE001 - one bad file must not stop the worker
                traceback.print_exc()
            finally:
                if status == "held":
                    # The file stays, same as a build the pin file never listed (above). The status write
                    # itself already happened under the lock, above; here, only finish the rest of the held
                    # state the same way, and only if this job is still "held" -- a concurrent _sweep (60-day
                    # TTL) could have claimed and expired it in between, and must not be resurrected to "held"
                    # over an already-deleted file.
                    with self.lock:
                        resurrect = job.status == "held"
                        if resurrect:
                            job.error, job.result, job.finished = error, result, None
                            job.run_id = job.run_token = job.run_deadline = None
                    if resurrect:
                        self._save(job)
                    # else: the claimant (_sweep) owns this job's state and its own save from here.
                else:
                    if self.archive is None:
                        # The temp folder goes first: a job reads as finished only once it's gone.
                        shutil.rmtree(job.folder, ignore_errors=True)
                    else:
                        self._settle(job, status, result)
                    job.run_id = job.run_token = job.run_deadline = None
                    job.result, job.error, job.finished = result, error, time.time()
                    job.status = status
                    if status == "done":
                        # A released parser-refusal hold that now succeeds shouldn't still read as carrying
                        # that old refusal (a fresh failure's own detail is set above and must survive this).
                        job.detail = job.parser_pin = None
                    self._save(job)
                self._forget_old()
                if not job.released:
                    (self.queue if job.kind == "upload" else self.reparse_queue).task_done()
                self._parse_ended()

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
        if self.settings.parse_runner == "workflow" and self.runs is not None:
            return self._process_remote(job)
        try:
            result, job.parse_seconds = parse_file(
                job.folder / "upload.vrf", job.folder, job.sha256, parser_cmd=self.settings.parser_cmd,
                timeout_s=self.settings.timeout_s, memory_cap_mb=self.settings.memory_cap_mb,
                parser_build=self.settings.parser_build)
        except ParseFailed as failed:
            job.detail, job.parse_seconds = failed.detail, failed.seconds
            raise _JobFailed(failed.reason)
        return self._accept_result(job, result)

    def _process_remote(self, job: Job) -> dict:
        """The parse as Workflow runs, under the job's one deadline (spec D8) and today's restart rule (D9):
        the token and deadline are saved before a run starts and the Render id as soon as there is one; a job
        recovered with a saved id reconnects to that run, and one without starts over with its saved token. The
        identity stays in job.json until `_run` settles the job."""
        files = {"vrf": job.folder / "upload.vrf"}
        if job.run_deadline is None or job.run_token is None:
            job.run_deadline = time.time() + self.settings.parse_run_timeout_s
            job.run_token = secrets.token_hex(16)
            self._save(job, strict=True)
        reconnect = job.run_id is not None
        while True:
            if time.time() >= job.run_deadline:
                if reconnect and job.run_id:
                    try:                                # the run we would have reconnected to must not go on
                        self.runs.api.cancel(job.run_id)
                    except Exception:  # noqa: BLE001 - best effort; the job fails either way
                        traceback.print_exc()
                raise _JobFailed(REASON_TIMEOUT)
            run = self.runs.open("parse", job.id, files, 0, token=job.run_token,
                                 render_id=job.run_id if reconnect else None, deadline=job.run_deadline)
            try:
                if not reconnect:
                    def started(render_id, job=job):
                        job.run_id = render_id
                        self._save(job, strict=True)
                    self.runs.start(run, {"recipe": self.recipe, "sha256": job.sha256}, on_started=started)
                answer = self.runs.wait(run)
            except BaseException:
                self.runs.close(run)                    # never leave the run open (close is safe to repeat)
                raise
            kind = answer.get("error_kind")
            if answer.get("status") == "ok":
                job.parse_seconds = answer.get("parse_seconds")
                return self._accept_result(job, answer["result"])
            if kind == "deploy":
                # This run's image has another recipe: nothing was parsed. Wait, then a fresh run, within the
                # deadline (the loop's first check ends it).
                time.sleep(max(0.0, min(DEPLOY_WAIT_S, job.run_deadline - time.time())))
                job.run_id, reconnect = None, False
                self._save(job)
                continue
            if kind == "infra":
                # Nothing says the file can't be parsed: Render refused or couldn't take the start, the run died
                # without an answer (or the one we reconnected to did), or it couldn't download the whole file.
                # Start over, as a restart does today, within the one deadline (the loop's first check ends it
                # as a timeout, which tells the uploader to try again).
                if not reconnect:
                    time.sleep(max(0.0, min(INFRA_WAIT_S, job.run_deadline - time.time())))
                job.run_id, reconnect = None, False
                self._save(job)
                continue
            if kind == "timeout":
                raise _JobFailed(REASON_TIMEOUT)
            if kind == "parse":
                job.detail, job.parse_seconds = answer.get("detail"), answer.get("parse_seconds")
                raise _JobFailed(answer.get("error") or REASON_PARSE)
            if kind == "contract":
                raise _JobFailed(answer.get("error") or REASON_PARSE)
            raise _JobFailed(REASON_PARSE)

    def _accept_result(self, job: Job, result: dict) -> dict:
        """Today's checks on a parse's result, wherever it ran."""
        match_uuid = str(result["match_uuid"]).lower()
        if self.archive is not None and job.kind == "reparse" and match_uuid != job.match_uuid:
            raise _JobFailed("the archived file is another match")
        job.match_uuid, job.map = match_uuid, result["map_name"]
        return result

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
        for key in ("game_build", "detail", "parser_pin", "run_id", "run_token", "run_deadline"):
            if getattr(job, key):
                record[key] = getattr(job, key)
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
                      attempt_id=record.get("attempt_id"), game_build=record.get("game_build"),
                      detail=record.get("detail"), parser_pin=record.get("parser_pin"), run_id=record.get("run_id"),
                      run_token=record.get("run_token"), run_deadline=record.get("run_deadline"))
            shutil.rmtree(folder / "export", ignore_errors=True)
            if job.status == "expiring":
                job.status = "held"                     # a sweep died mid-claim: it expires again on the next one
            if job.status == "held":
                if not (folder / "upload.vrf").exists():
                    job.status, job.finished = "failed", time.time()
                    job.error = "the kept file is gone from the worker: please upload it again"
                    self._save(job)
            elif (job.game_build or job.parser_pin) and job.status in ("queued", "parsing"):
                # A held job released just before the restart: held again, released by _next when its turn comes.
                job.status = "held"
                job.error = (held_reason(job.game_build) if job.parser_pin is None
                             else parser_refused_reason(job.detail or "the parser refused this file"))
                self._save(job)
            elif job.status in ("queued", "parsing"):
                if (folder / "upload.vrf").exists():
                    # Accepted before the restart: worked first, never through the bounded queue, so a restart
                    # can't fail it for want of room (spec D15). `released` marks a job not taken from a queue.
                    job.status, job.released = "queued", True
                    self.recovered.append(job)
                else:
                    job.status, job.error, job.finished = "failed", "the worker restarted: please re-upload", time.time()
                self._save(job)
            elif job.status == "done":
                job.result = archive_store.read_json(folder / "result.json", None)
                if job.result is None:
                    job.status, job.error = "failed", REASON_PARSE
            with self.lock:
                self.jobs[job.id] = job
        with self.lock:
            held = sum(j.status == "held" for j in self.jobs.values())
        _log(f"archive on: recovered {len(records)} job(s); {len(self.recovered)} "
             f"queued again, {held} held (an unsupported game build, or the parser's own refusal)")
        self._sweep()

    def _sweep(self) -> None:
        """Pending files and ack records past their TTL, and finished job folders nobody collected."""
        self.last_sweep = time.time()
        try:
            self.archive.sweep()
            with self.lock:
                expired = [j for j in self.jobs.values() if j.status == "held" and j.created < time.time() - HELD_TTL_S]
                for job in expired:
                    job.status = "expiring"                 # claimed: _release_held skips it
            for job in expired:
                self._save(job)
                (job.folder / "upload.vrf").unlink(missing_ok=True)
                job.status, job.finished = "failed", time.time()
                build_text = f"recorded on game version {_version(job.game_build)}" if job.game_build else \
                    "the parser refused this file"
                job.error = (f"{build_text}: it still wasn't parseable after {HELD_TTL_S // 86400} days, so the "
                             "kept file was deleted")
                self._save(job)
                _log(f"job {job.id} was held for {HELD_TTL_S // 86400} days; its file is deleted")
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
WARM_BUILD_TRIES = 2     # failed warm builds of one identity before its rounds fall back to one warming round
WARM_SECONDS_KEPT = 20   # recent warm-build durations /health shows
WARM_START_RETRY_S = 60  # after Render refused a warm build's start (a deploy gap)
WARM_START_REFUSED_MAX_S = 1800   # refused starts past this long count as failures (rule 4's fallback)
HEIGHTS_KEPT = 3                 # pushed height assets kept per map (the newest); a missing one is pushed again
MAP_NAME = re.compile(r"^[A-Za-z0-9]{1,64}$")
HEIGHT_DIGEST = re.compile(r"^[0-9a-f]{12}$")
# The control child's task and result protocol, told to the web app in /health: 1 means a task's `gaps` block
# is understood (replay_worker/control_job.py; the image ships app/gaps); 2, its result also carries the
# round's critical moments (the image ships app/moments); 3, and its Critical Util evidence (app/critutil). A plain number here, never read
# from the detector: this process imports none of it.
GAPS_PROTOCOL = 3
from app.replays import map_feature_artifacts as feature_artifacts
_FEATURE_VERIFY_SLOTS = threading.BoundedSemaphore(2)
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
    error_code: str | None = None
    result: dict | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    warm_key: str = ""           # what "warm" is tracked by: the map and its heights (their visibility cache)
    kind: str = "control"        # or "heights" (a height build) or "warm" (a warm build)
    warm_id: str = ""            # warm_key plus height mode: what warm builds track (REPLAY_WARM_BUILDS only)
    released_name: str | None = None   # the exact cache file this round was released to read (warm builds)
    warm_epoch: int = 0          # its identity's confirmation number when it was released (only that cycle's answers count)

    def public(self) -> dict:
        body = {"id": self.id, "key": self.key, "status": self.status}
        if self.error:
            body.update({"error": self.error, "error_kind": self.error_kind})
            if self.error_code:
                body['error_code'] = self.error_code
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


def verify_feature_upload(settings, artifact):
    """Bounded heavy correspondence check; this server remains standard library only."""
    height = None
    if artifact.key.height_digest != 'flat':
        path = height_path(settings, artifact.key.map_name, artifact.key.height_digest)
        try:
            height = base64.b64encode(path.read_bytes()).decode('ascii')
        except OSError as exc:
            raise feature_artifacts.FeatureArtifactMissing('upload the exact height first') from exc
    request = json.dumps({'mode': 'verify', 'artifact': json.loads(feature_artifacts.encode_artifact(artifact)),
                          'height': height}).encode('utf-8')
    if len(request) > feature_artifacts.MAX_WIRE_BYTES:
        raise feature_artifacts.FeatureArtifactCorrupt('verification request size limit')
    env = dict(os.environ)
    env.update(CHILD_THREADS)
    env['PYTHONPATH'] = os.pathsep.join((str(WEBAPP), str(WEBAPP.parent)))
    kwargs = {}
    if os.name != 'nt':
        kwargs['preexec_fn'] = _child_setup(settings.control_memory_mb)
    with _FEATURE_VERIFY_SLOTS, tempfile.TemporaryFile() as output:
        try:
            process = subprocess.run([settings.control_cmd[0], '-m', 'app.control.feature_job'], input=request,
                                     stdout=output, stderr=subprocess.DEVNULL, env=env, cwd=WEBAPP,
                                     timeout=120, **kwargs)
            if process.returncode or output.tell() > feature_artifacts.MAX_WIRE_BYTES:
                raise feature_artifacts.FeatureArtifactCorrupt('feature verification child failed')
            output.seek(0)
            reply = json.load(output)
            if not reply.get('ok'):
                cls = feature_artifacts.UnsupportedFeatureCompiler if reply.get('code') == 'features_unsupported' \
                    else feature_artifacts.FeatureArtifactCorrupt
                raise cls(reply.get('reason') or 'feature correspondence failed')
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, feature_artifacts.FeatureArtifactError):
                raise
            raise feature_artifacts.FeatureArtifactCorrupt(str(exc)) from exc


def height_path(settings: Settings, map_name: str, digest: str) -> Path:
    """app/control/geometry.py `height_cache_path`, without importing it (this process has no numpy)."""
    return settings.control_cache_dir / "heights" / f"{map_name}.{digest}.height.npz"


def store_height(settings: Settings, map_name: str, digest: str, data: bytes) -> None:
    """Writes a pushed asset whole (a temp file, then a rename), and keeps the map's newest HEIGHTS_KEPT."""
    path = height_path(settings, map_name, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    partial.write_bytes(data)
    os.replace(partial, path)
    mine = sorted(path.parent.glob(f"{map_name}.*.height.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in mine[HEIGHTS_KEPT:]:
        try:
            old.unlink()
        except OSError:
            pass


class ControlRunner:
    """Map control's queue, beside the parse queue and never sharing its thread. At most
    `control_workers` children at once, and one beside a parse; a map's first round runs alone ("warming": it builds the
    map's visibility cache) and a map is warm, under one height digest, once one of its rounds gets past loading.
    A task's key (replay, round, fingerprint) is deduped while its job is queued, running or done."""

    def __init__(self, settings: Settings, idle=None, runs_table=None):
        self.settings = settings
        self.runs = runs_table
        self.lock = threading.Lock()
        self.jobs: dict[str, ControlJob] = {}
        self.by_key: dict[str, str] = {}
        self.pending: list[str] = []
        self.running: dict[str, bool] = {}     # job id -> warming
        self.warm: set[str] = set()
        # Warm builds (REPLAY_WARM_BUILDS), by warm_id.
        self.warm_names: dict[str, str] = {}     # the cache file its warm build named
        self.warm_confirmed: set[str] = set()    # cleared for parallel rounds: this cycle's probe read that file
        self.warm_read: set[str] = set()         # some round has read the current file (D3: no longer "before use")
        self.warm_epoch: dict[str, int] = {}     # confirmation cycle: each withdrawal starts a new one; a round's
                                                 # answer from an older cycle never confirms or counts a miss
        self.warm_rewarmed: dict[str, int] = {}  # files evicted after use, warmed again (cumulative: cache thrash)
        self.warm_probe: dict[str, str] = {}     # the probe round running now, by job id
        self.warm_jobs: dict[str, str] = {}      # its warm build, queued or running, by job id
        # The fallback's counts (WARM_BUILD_TRIES, added together). A probe round that reads the identity's file
        # clears all three: they count trouble since the identity last worked, never across a success.
        self.warm_failures: dict[str, int] = {}  # failed warm builds
        self.warm_missed: dict[str, int] = {}    # rounds that had to build a cache the identity was warm for
        self.warm_vanished: dict[str, int] = {}  # its file left the disk before any round read it (D3)
        self.warm_vanished_seen: dict[str, int] = {}  # the same losses, never cleared: /health's alarm for the site
        self.warm_ids: set[str] = set()          # identities a fallback round of their own warmed (never a leased one)
        self.boot = uuid.uuid4().hex             # this process, in /health: the site compares counts within one
        self.warm_retry_at: dict[str, float] = {}  # no warm build for this identity before this time (start refused)
        self.warm_refused_since: dict[str, float] = {}   # when its start was first refused, until one is accepted
        self.warm_misses = 0                     # rounds released to a name that still had to build the cache
        self.warm_seconds: list[float] = []      # the last WARM_SECONDS_KEPT warm builds' durations
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

    def remote(self, job: ControlJob) -> bool:
        """This job runs as a Workflow run (spec 2026-10-14) rather than a child here."""
        if self.runs is None:
            return False
        if job.kind == "warm":
            return True                  # a warm build only ever runs on Workflows
        if job.kind == "heights":
            return self.settings.heights_runner == "workflow"
        return job.kind == "control" and self.settings.control_runner == "workflow"

    def uses_warm_builds(self) -> bool:
        return self.settings.warm_builds and self.settings.control_runner == "workflow" and self.runs is not None

    def _ready(self, job: ControlJob) -> bool:
        """The round's identity has a cache name from its warm build, and that file is on this disk (under the lock).
        The name is the exact geometry's, so a cache from other heights or another height mode never counts."""
        name = self.warm_names.get(job.warm_id)
        return name is not None and (self.settings.control_cache_dir / name).is_file()

    def _spent(self, warm_id: str) -> int:
        """The identity's fallback count: failures, misses and vanished files since it last worked (under the lock)."""
        return (self.warm_failures.get(warm_id, 0) + self.warm_missed.get(warm_id, 0)
                + self.warm_vanished.get(warm_id, 0))

    def _withdraw(self, warm_id: str) -> None:
        """No more parallel rounds on this identity until a new probe reads its file (under the lock): a new
        confirmation cycle, so answers from rounds released before now neither confirm nor count again, and the
        fallback's warmth by identity goes too (only a fallback round of its own sets it again)."""
        self.warm_confirmed.discard(warm_id)
        self.warm_epoch[warm_id] = self.warm_epoch.get(warm_id, 0) + 1
        self.warm_ids.discard(warm_id)

    def _note_gone(self, warm_id: str, warm_key: str) -> None:
        """The identity's named cache file is no longer on this disk (under the lock). Before any round read it,
        it is a D3 loss: counted toward the fallback and logged loudly. After a round read it, it is ordinary
        eviction of a used file: the identity just warms again (counted in `rewarmed`, to spot cache thrash).
        Either way the map's legacy warm flag goes too, so a fallback round warms the map alone rather than every
        round rebuilding the cache at once."""
        if warm_id in self.warm_read:
            self.warm_rewarmed[warm_id] = self.warm_rewarmed.get(warm_id, 0) + 1
            _log(f"warm identity {warm_id}: its cache file was evicted after use; warming it again "
                 f"({self.warm_rewarmed[warm_id]} so far)")
        else:
            self.warm_vanished[warm_id] = self.warm_vanished.get(warm_id, 0) + 1
            self.warm_vanished_seen[warm_id] = self.warm_vanished_seen.get(warm_id, 0) + 1
            _log(f"warm identity {warm_id} vanished: its cache file left the disk before a round leased it "
                 f"({self.warm_vanished_seen[warm_id]} so far)")
        self.warm_names.pop(warm_id, None)
        self.warm_read.discard(warm_id)
        self._withdraw(warm_id)
        self.warm.discard(warm_key)

    def _ask_warm(self, job: ControlJob) -> None:
        """One warm build per identity, ahead of every round (under the lock). Its task is the round's own less what
        only a round needs, so it loads exactly the geometry the round will."""
        if job.warm_id in self.warm_jobs:
            return
        task = json.loads(job.task)
        for name in ("blob", "link", "gaps", "gaps_only"):
            task.pop(name, None)
        task["key"] = f"warm:{job.warm_id}"
        warm = ControlJob(uuid.uuid4().hex, task["key"], job.map, json.dumps(task).encode("utf-8"),
                          warm_key=job.warm_key, kind="warm", warm_id=job.warm_id)
        self.jobs[warm.id] = warm
        self.warm_jobs[job.warm_id] = warm.id
        self.pending.insert(0, warm.id)
        self.wake.set()

    def submit(self, task: dict) -> ControlJob:
        """Raises queue.Full when the queue is full."""
        key = str(task.get("key"))
        with self.lock:
            existing = self.jobs.get(self.by_key.get(key, ""))
            if existing is not None and existing.status in ("queued", "running", "done"):
                return existing
            if sum(1 for j in self.pending if self.jobs[j].kind != "warm") >= self.settings.control_queue:
                raise queue.Full
            job = ControlJob(uuid.uuid4().hex, key, str(task["map"]), json.dumps(task).encode("utf-8"),
                             warm_key=f"{task['map']}:{task.get('height') or ''}" +
                             (f":{task['features']}" if task.get('features') else ''))
            job.warm_id = f"{job.warm_key}|{task.get('height_mode') or ''}"
            self.jobs[job.id] = job
            self.by_key[key] = job.id
            self.pending.append(job.id)
        self.wake.set()
        return job

    def submit_build(self, key: str, map_name: str, task: dict) -> ControlJob:
        """A height build: queued ahead of every control round, deduped by its key like one."""
        with self.lock:
            existing = self.jobs.get(self.by_key.get(key, ""))
            if existing is not None and existing.status in ("queued", "running", "done"):
                return existing
            job = ControlJob(uuid.uuid4().hex, key, map_name, json.dumps(task).encode("utf-8"), kind="heights")
            self.jobs[job.id] = job
            self.by_key[key] = job.id
            self.pending.insert(0, job.id)
        self.wake.set()
        return job

    def get(self, job_id: str) -> ControlJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def forget(self, job_id: str) -> None:
        """Drops a finished job and its answer (a height build's, when its build is dropped)."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job.finished:
                del self.jobs[job_id]
                if self.by_key.get(job.key) == job_id:
                    del self.by_key[job.key]

    def counts(self) -> dict:
        with self.lock:
            body = {"queued": sum(1 for j in self.pending if self.jobs[j].kind != "warm"),
                    "running": len(self.running), "warm": sorted(self.warm), "preempted": self.preempted_total}
            if self.uses_warm_builds():
                now = time.time()
                ready = [i for i, n in self.warm_names.items() if (self.settings.control_cache_dir / n).is_file()]
                body["warm_builds"] = {"ready": sorted(ready), "confirmed": sorted(self.warm_confirmed),
                                       "building": sorted(self.warm_jobs),
                                       "failures": dict(sorted(self.warm_failures.items())),
                                       "missed": dict(sorted(self.warm_missed.items())),
                                       # every loss since this process started (never cleared): the site's alarm
                                       "vanished": dict(sorted(self.warm_vanished_seen.items())),
                                       "start_refused": sorted(i for i, t in self.warm_retry_at.items() if t > now),
                                       "misses": self.warm_misses, "recent_seconds": list(self.warm_seconds),
                                       "rewarmed": dict(sorted(self.warm_rewarmed.items())), "boot": self.boot}
            return body

    def preempt(self) -> int:
        """A parse is coming: kill every running child but the one started first, so the parse has a CPU and
        control keeps one (D4 as amended 2026-10-07; the child started last has done the least work).
        `self.running` keeps start order. How each run ends is decided at settlement, under the lock, where a
        preempted mark wins over everything else."""
        with self.lock:
            # Only children on this machine: a Workflow run takes no CPU from a parse (spec D17).
            alive = [job_id for job_id in self.running
                     if job_id not in self.preempted and not self.remote(self.jobs[job_id])]
            # A height build runs alone and is never the child kept: a parse always preempts it (the height
            # rebuild spec, section 2).
            kept = alive[:1] if alive and self.jobs[alive[0]].kind == "control" else []
            victims = [(job_id, self.procs.get(job_id)) for job_id in alive if job_id not in kept]
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
        for job_id in self.pending:
            job = self.jobs[job_id]
            if job.kind == "heights" and self.remote(job):
                return job, False                    # another machine: it neither waits for idle nor runs alone
        # Beside a parse (running or waiting) control keeps one child; idle, the whole pool.
        idle = self.idle()
        if self.settings.control_runner == "workflow" and self.runs is not None:
            limit = self.settings.control_runs
        else:
            limit = self.settings.control_workers if idle else 1
        # Remote work takes no CPU here: a local build neither waits for it nor holds it back, and a full remote
        # pool never holds a local build.
        local_running = [j for j in self.running if not self.remote(self.jobs[j])]
        local_build = any(self.jobs[j].kind == "heights" for j in local_running)
        if not local_build:
            for job_id in self.pending:
                if self.jobs[job_id].kind == "heights":  # a local one: remote builds were returned above
                    # Idle, drain the running local children before starting the build ahead of queued rounds.
                    # Beside a parse, the build waits while the one control slot keeps working.
                    if idle:
                        return (self.jobs[job_id], False) if not local_running else None
                    break
        # A remote height build takes no CPU here and writes nothing a cold round reads: it holds no slot or map.
        held = [j for j in self.running if not (self.jobs[j].kind == "heights" and self.remote(self.jobs[j]))]
        if len(held) >= limit:
            return None
        # A warm build takes a run slot but holds no map: it writes only its own exact-name file, so another
        # identity's warming round of the same map need not wait out its hours.
        busy_maps = {self.jobs[j].map for j in held if self.jobs[j].kind != "warm"}
        warming_maps = {self.jobs[j].map for j in held if self.running[j]}
        for job_id in list(self.pending):            # _ask_warm inserts into self.pending
            job = self.jobs[job_id]
            if job.kind == "heights":
                continue
            if local_build and not self.remote(job):
                continue                             # a local build runs alone among the local children
            if job.kind == "warm":
                return job, False                    # its own run: it neither waits for a map nor holds one
            if self.uses_warm_builds():
                # A tracked file that left the disk is noticed whatever the tries say (a D3 loss after the tries
                # are spent still raises the alarm), once any probe of it has answered.
                if job.warm_id in self.warm_names and not self._ready(job):
                    if job.warm_id in self.warm_probe:
                        continue                     # its probe may have read the file before it went: wait for it
                    self._note_gone(job.warm_id, job.warm_key)
                # Every warm path is checked against the spent tries, the leased one too: a file that is on the
                # disk but that no round can use (a download that keeps failing) must still end in the fallback.
                if self._spent(job.warm_id) < WARM_BUILD_TRIES:
                    if self._ready(job):
                        if job.warm_id not in self.warm_confirmed:
                            if job.warm_id in self.warm_probe:
                                continue             # one probe at a time; the rest wait for its answer (risk 2)
                            self.warm_probe[job.warm_id] = job.id
                        job.released_name = self.warm_names[job.warm_id]
                        job.warm_epoch = self.warm_epoch.get(job.warm_id, 0)
                        return job, False
                    if self.warm_retry_at.get(job.warm_id, 0.0) > time.time():
                        continue                     # rule 9: its start was refused
                    if self._spent(job.warm_id) < WARM_BUILD_TRIES:
                        self._ask_warm(job)          # the round waits for its identity's one warm build
                        continue
                # Warm builds keep failing for this identity: today's rule, one round warms the map alone. Warm
                # here means this identity's own round warmed it (warm_ids), not the map's shared flag: another
                # height mode of the same map and heights sets that flag without this identity's file.
                if job.warm_id in self.warm_ids:
                    if job.map in warming_maps:
                        continue
                    return job, False
                if job.map not in busy_maps:
                    return job, True
                continue
            if job.warm_key in self.warm:
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
        build = job.kind == "heights"
        what = "height build" if build else "warm build" if job.kind == "warm" else "control"
        remote = self.remote(job)
        if job.kind == "warm":
            timeout = settings.warm_build_timeout_s
        elif build:
            timeout = settings.height_run_timeout_s if remote else settings.height_timeout_s
        elif remote:
            timeout = settings.control_run_timeout_s       # warming or not: one Workflow limit
        else:
            timeout = settings.control_warm_timeout_s if warming else settings.control_timeout_s
        env ={**os.environ, **CHILD_THREADS}
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(WEBAPP), str(WEBAPP.parent), env.get("PYTHONPATH")) if p)
        env["CONTROL_CACHE_DIR"] = str(settings.control_cache_dir)     # keep_local_sizing may have moved it
        kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env,
                  "cwd": str(WEBAPP.parent)}
        if os.name != "nt":
            kwargs["preexec_fn"] = _child_setup(settings.control_memory_mb)
        status, error, kind, result = "failed", None, "infra", None
        answer: dict = {}
        error_code = None
        unsent: list = []           # cache files a run couldn't post, whether its round succeeded or not
        process = None
        try:
            # Started outside the lock (preexec_fn must not run while this thread holds a lock another
            # needs), registered under it, and killed at once if a preempt landed in between.
            if self.remote(job):
                answer = self._run_remote(job, timeout)
            else:
                process = subprocess.Popen(settings.height_cmd if build else settings.control_cmd, **kwargs)
                with self.lock:
                    self.procs[job.id] = process
                    killed_before_start = job.id in self.preempted
                if killed_before_start:
                    kill_tree(process)
                stdout, _ = process.communicate(job.task, timeout=timeout)
                answer = json.loads(stdout or b"{}")
            unsent = answer.get("cache_unsent") or []
            if answer.get("status") == "ok":
                status, kind, result = "done", None, answer
            else:
                error = answer.get("error") or (f"control child exited {process.returncode}" if process
                                                else "control run gave no error text")
                kind = answer.get("error_kind") or "infra"
                error_code = answer.get('error_code')
        except subprocess.TimeoutExpired:
            error = f"{what} timed out after {timeout:g} s"
        except (OSError, ValueError) as failure:
            error = f"{what} child failed: {failure}"
        except Exception:  # noqa: BLE001 - one bad round must not stop the runner
            traceback.print_exc()
            error = f"{what} child failed"
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
            elif kind == "rewarm":
                # Its warm cache left the disk before the run could lease it: back to the front, uncounted, and the
                # identity warmed again. It never runs cold (risk 2).
                # Only this cycle's loss, and only if the file is still missing: a replacement can have been
                # built under the same name since (R5-B1).
                if (self.warm_names.get(job.warm_id) == job.released_name
                        and job.warm_epoch == self.warm_epoch.get(job.warm_id, 0)
                        and not (self.settings.control_cache_dir / job.released_name).is_file()):
                    self._note_gone(job.warm_id, job.warm_key)
                if self.warm_probe.get(job.warm_id) == job.id:
                    self.warm_probe.pop(job.warm_id, None)
                job.released_name, job.status = None, "queued"
                self.pending.insert(0, job.id)
            elif kind == "cache_input" and job.released_name:
                # The run couldn't download its leased file (the task tried REQUIRED_FETCH_TRIES times) and never
                # started its child: back to the front, the identity withdrawn so the next round is one probe
                # again, and a miss counted, so a download that keeps failing ends in the fallback. One miss per
                # confirmation cycle: every round released in it may fail on the same outage, which is one event.
                if (self.warm_names.get(job.warm_id) == job.released_name
                        and job.warm_epoch == self.warm_epoch.get(job.warm_id, 0)):
                    self.warm_missed[job.warm_id] = self.warm_missed.get(job.warm_id, 0) + 1
                    self._withdraw(job.warm_id)
                    _log(f"warm identity {job.warm_id}: a round couldn't download its cache; counted as a miss")
                if self.warm_probe.get(job.warm_id) == job.id:
                    self.warm_probe.pop(job.warm_id, None)
                job.released_name, job.status = None, "queued"
                self.pending.insert(0, job.id)
            else:
                job.status, job.error, job.error_kind, job.result = status, error, kind, result
                job.error_code = error_code
                job.finished = time.time()
                job.task = b""
                # R5-B3: cache files the run couldn't read. Its copy was a download; drop this disk's copy too, so
                # the next warm build (or warming round) builds it instead of every run downloading it broken.
                unreadable = [n for n in (answer.get("unreadable") or [])
                              if isinstance(n, str) and control_cache.valid(n, job.map)]
                for name in unreadable:
                    try:
                        (self.settings.control_cache_dir / name).unlink(missing_ok=True)
                        _log(f"cache {name}: a run couldn't read it; removed so it is built again")
                    except OSError:
                        pass                                 # leased and open elsewhere: the next settlement tries
                if job.kind == "warm":
                    self.warm_jobs.pop(job.warm_id, None)
                    name = (result or {}).get("visibility")
                    refused = (error or "").startswith("the run could not start")
                    if not refused:
                        self.warm_refused_since.pop(job.warm_id, None)   # Render accepted a start
                    built = status == "done" and name and control_cache.valid(name, job.map) and name not in unsent
                    if built and self.warm_names.get(job.warm_id) != name:
                        self.warm_read.discard(job.warm_id)   # a new file: nobody has read it yet
                        self._withdraw(job.warm_id)
                    if built and (self.settings.control_cache_dir / name).is_file():
                        self.warm_names[job.warm_id] = name   # its counts clear only once a probe reads it
                    elif built:
                        # Posted, then cleaned off the disk before this answer settled: a D3 loss, not a failure.
                        self._note_gone(job.warm_id, job.warm_key)
                    else:
                        since = self.warm_refused_since.setdefault(job.warm_id, time.time()) if refused else None
                        if refused and time.time() - since <= WARM_START_REFUSED_MAX_S:
                            # Rule 9: Render refused the start (a deploy gap, before the workflow knows `warm`): no
                            # try is spent; the identity's rounds wait and a warm build is asked again after a minute.
                            self.warm_retry_at[job.warm_id] = time.time() + WARM_START_RETRY_S
                        else:
                            self.warm_failures[job.warm_id] = self.warm_failures.get(job.warm_id, 0) + 1
                            _log(f"warm build {job.warm_id} failed: {error or 'its cache is not on this disk'}")
                    if isinstance(answer.get("seconds"), (int, float)):
                        self.warm_seconds = (self.warm_seconds + [round(float(answer["seconds"]), 1)])[-WARM_SECONDS_KEPT:]
                    del self.jobs[job.id]            # nobody asks after a warm build by id
                elif job.kind == "control" and self.uses_warm_builds() and job.released_name:
                    source = answer.get("visibility_source")
                    current = self.warm_names.get(job.warm_id) == job.released_name
                    this_cycle = job.warm_epoch == self.warm_epoch.get(job.warm_id, 0)
                    probe = self.warm_probe.get(job.warm_id) == job.id
                    if probe:
                        self.warm_probe.pop(job.warm_id, None)     # whatever happened, the probe slot is free
                    # The file the run says it read (R5-B5); an older task image doesn't say: the lease's name.
                    read = answer.get("visibility") or job.released_name
                    # A round that had to build the cache, couldn't read the one it was given, or read another
                    # file for its geometry: the leased file didn't do its job, one miss. R5-B2: an older cycle's
                    # answer spends nothing.
                    wrong = source == "built" or job.released_name in unreadable or (
                        source == "cache" and read != job.released_name)
                    if wrong and current and this_cycle:
                        self.warm_misses += 1                      # rule 7
                        self.warm_missed[job.warm_id] = self.warm_missed.get(job.warm_id, 0) + 1
                        self.warm_names.pop(job.warm_id, None)     # later answers no longer match: counted once
                        self.warm_read.discard(job.warm_id)
                        self._withdraw(job.warm_id)
                        why = ("built its own cache" if source == "built" else
                               "couldn't read its cache" if job.released_name in unreadable else
                               f"read another cache ({read})")
                        _log(f"warm identity {job.warm_id} missed: a round {why}")
                    elif source == "cache" and current and not wrong:
                        self.warm_read.add(job.warm_id)            # read: its eviction from now on isn't D3's
                        if probe and this_cycle:
                            # Rule 5, from this cycle's probe only: a round released before a withdrawal can't
                            # reopen parallel rounds or clear the counts while the new probe is still out.
                            self.warm_confirmed.add(job.warm_id)
                            for counts in (self.warm_failures, self.warm_missed, self.warm_vanished):
                                counts.pop(job.warm_id, None)      # it works: earlier trouble is behind it
                elif (job.kind == "control" and self.uses_warm_builds() and answer.get("visibility_source") == "cache"
                        and answer.get("visibility") and answer.get("visibility") == self.warm_names.get(job.warm_id)):
                    # A fallback round with no lease read the identity's tracked file: read, so its eviction from
                    # now on is ordinary cleanup, not a D3 loss (R5-B5).
                    self.warm_read.add(job.warm_id)
                if self.warm_names.get(job.warm_id) in unreadable:
                    # The tracked file was unreadable and is now deleted here, reported by a round whose answer
                    # counted nothing above (a fallback round, or one from an older cycle): stop tracking it, so the
                    # next pass doesn't take its absence for a D3 loss (state table, event 13).
                    self.warm_names.pop(job.warm_id, None)
                    self.warm_read.discard(job.warm_id)
                    self._withdraw(job.warm_id)
                # Past loading (ok, or the round's own failure): the map's cache is there. Any machine
                # failure makes the map cold again, so its next round warms alone.
                if job.kind == "control":
                    # A remote round's map is warm only once its visibility cache is on this disk: a run whose
                    # cache upload failed would otherwise release every other round to rebuild it.
                    # And not when the run says it failed to send a visibility cache: an older one (other
                    # heights) can still be on disk.
                    cached = not self.remote(job) or (
                        control_cache.has_visibility(self.settings.control_cache_dir, job.map)
                        and not any(control_cache.visibility_name(n) for n in unsent))
                    if (status == "done" or kind == "engine") and cached:
                        self.warm.add(job.warm_key)
                        if not job.released_name:
                            self.warm_ids.add(job.warm_id)          # a round of its own warmed it, not a lease
                    else:
                        self.warm.discard(job.warm_key)
                        self.warm_ids.discard(job.warm_id)
                finished = sorted((j for j in self.jobs.values() if j.finished and j.kind == "control"),
                                  key=lambda j: j.finished)
                for old in finished[:-CONTROL_FINISHED_KEPT]:
                    del self.jobs[old.id]
                    if self.by_key.get(old.key) == old.id:
                        del self.by_key[old.key]
        self.wake.set()

    def _run_remote(self, job: ControlJob, timeout: float) -> dict:
        """One round as a Workflow run: the task and the map's cached files are its inputs."""
        task = json.loads(job.task)
        folder = self.settings.temp_root / "runs-in" / job.id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / "task.json").write_bytes(job.task)
            files = {"task": folder / "task.json"}
            if job.kind == "heights":
                # The packed tar goes beside the spool (`<spool>/../height_runs`), not inside a build's folder.
                runs_dir = Path(task["dir"]).parent.parent / "height_runs"
                tar_path = runs_dir / f"{job.id}.tar"
                try:
                    # Only a ValueError from packing (rounds gone or unreadable) is an `inputs` failure; an
                    # OSError (disk, permissions) is infra and falls to `_run`'s handler.
                    files["rounds"] = control_cache.pack(Path(task["dir"]), tar_path)
                except ValueError as missing:
                    return {"status": "failed", "error_kind": "inputs", "error": str(missing), "key": task.get("key")}
                files.update(control_cache.inputs(self.settings.control_cache_dir,
                                                  {"map": job.map, "height": task.get("previous")}))
            else:
                files.update(control_cache.inputs(self.settings.control_cache_dir, task))
            leased = None
            if job.kind == "control" and job.released_name:
                if not control_cache.lease(self.settings.control_cache_dir, job.released_name):
                    return {"status": "failed", "error_kind": "rewarm", "key": task.get("key"),
                            "error": f"{job.released_name} left the disk before the run could read it"}
                leased = job.released_name
                files[leased] = self.settings.control_cache_dir / leased   # by name, whatever `inputs` chose
            try:
                run = self.runs.open(job.kind, job.id, files, timeout)
                try:
                    args = {"map": job.map, "files": sorted(n for n in files if n not in ("task", "rounds"))}
                    if leased:
                        args["visibility"] = leased   # the task retries this one fetch before building its own
                    self.runs.start(run, args)
                    answer = self.runs.wait(run)
                except BaseException:
                    self.runs.close(run)          # wait closes it itself; a failure before that must not leak it
                    raise
            finally:
                if leased:
                    control_cache.unlease(leased)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            if job.kind == "heights":
                try:
                    (Path(task["dir"]).parent.parent / "height_runs" / f"{job.id}.tar").unlink(missing_ok=True)
                except OSError:
                    pass
        if answer.get("error_kind") == "timeout":
            what = "height build" if job.kind == "heights" else "warm build" if job.kind == "warm" else "control"
            answer = {**answer, "error_kind": "infra", "error": f"{what} timed out after {timeout:g} s"}
        return answer


MATCH_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
HEIGHT_BUILDS_KEPT = 8           # ended builds whose answer stays readable (the newest)
ROUNDS_PER_MATCH_MAX = 99


class HeightConflict(Exception):
    """The build is not in a state that allows this, or a round arrived again with other bytes."""

    def __init__(self, reason: str, **facts):
        super().__init__(reason)
        self.reason, self.facts = reason, facts


class HeightBuilds:
    """Height builds as the web app drives them (the module docstring's endpoints): open one, send it the
    map's stored rounds in batches, start it, ask after it. The rounds are spooled to
    `<temp>/height_builds/<id>/<match>/<n>.json.gz`, which is the layout the child reads.

    - **One build per key**, and the key is the build's input manifest: opening a key again answers the build
      there is, in whatever state; only a failed one is replaced by a fresh, empty one.
    - **Uploads are immutable.** A round is kept by its sha256: the same bytes again are acknowledged and cost
      nothing; other bytes for a round already received are a conflict and change nothing.
    - **Bounded.** The spool of all builds together stays under `height_spool_bytes`; at most
      `height_builds_max` builds are unfinished at once, one per map; opening a build for a map drops any other still collecting
      for it (its inputs are out of date); a collector nobody feeds for `height_collect_ttl_s` is dropped; of
      the builds that ended, the newest HEIGHT_BUILDS_KEPT keep their answer.
    - **Nothing is left behind**: a build's folder goes when it ends, is cancelled, expires or is replaced, and
      whatever a restart left when the server starts."""

    def __init__(self, settings: Settings, control: ControlRunner):
        self.settings, self.control = settings, control
        self.root = settings.height_spool_dir or settings.temp_root / "height_builds"
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.root.parent / "height_runs", ignore_errors=True)   # packed tars a crash left (see `_run_remote`)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        # id -> {id, key, map, expected, manifest, received {(match, n): (sha256, size)}, job, touched, ended}
        self.builds: dict[str, dict] = {}
        threading.Thread(target=self._sweep_forever, name="height-builds", daemon=True).start()

    # ---- what the web app asks for

    def open(self, key: str, map_name: str, rounds: int, manifest, *, diagnostic=None) -> dict:
        if not MAP_NAME.match(str(map_name)) or type(rounds) is not int or rounds < 1 or not isinstance(manifest, dict):
            raise ValueError("not a height build")
        with self.lock:
            for build in list(self.builds.values()):
                if build["key"] == key:
                    if self._state(build) != "failed":
                        return self._public(build)       # still going, or done: the same build and its answer
                    self._drop(build)                    # it failed: the web app is trying again, from nothing
            for other in [b for b in self.builds.values() if b["map"] == map_name and b["job"] is None]:
                self._drop(other)                        # other inputs for this map, never started
            unfinished = [b for b in self.builds.values() if self._state(b) not in ("done", "failed")]
            if any(b["map"] == map_name for b in unfinished):
                # Its old build is still queued or running (on Workflows it can't be cancelled after start):
                # one unfinished build per map. 503 to the site: busy, ask again later, no try spent.
                raise OverflowError("a build of this map is still running")
            if len(unfinished) >= self.settings.height_builds_max:
                raise OverflowError("too many unfinished builds")
            build = {"id": uuid.uuid4().hex, "key": key, "map": map_name, "expected": rounds, "manifest": manifest,
                     "received": {}, "job": None, "touched": time.time(), "ended": None}
            if diagnostic is not None:
                from app.replays.map_feature_diagnostics import MAX_DIAGNOSTIC_BYTES, diagnostic_error
                if len(json.dumps(diagnostic).encode()) > MAX_DIAGNOSTIC_BYTES:
                    diagnostic = diagnostic_error(map_name, 'diagnostic snapshot too large', code='diagnostic_unavailable')
                build['diagnostic'] = json.loads(json.dumps(diagnostic))
            (self.root / build["id"]).mkdir()
            self.builds[build["id"]] = build
            return self._public(build)

    def add(self, build_id: str, rounds: list) -> dict:
        """Keeps a batch of rounds, all or none of it."""
        with self.lock:
            build = self._collecting(build_id)
            fresh: dict[tuple, bytes] = {}
            for row in rounds:
                try:
                    match, n = str(row["match"]).lower(), int(row["n"])
                    data = base64.b64decode(row["blob"], validate=True)
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("not a round") from error
                if not MATCH_ID.match(match) or not 1 <= n <= ROUNDS_PER_MATCH_MAX or str(row["n"]) != str(n) or not data:
                    raise ValueError("not a round")
                sha = hashlib.sha256(data).hexdigest()
                had = build["received"].get((match, n))
                if had is not None and had[0] != sha or fresh.get((match, n), data) != data:
                    raise HeightConflict("other bytes", match=match, n=n)
                if had is None:
                    fresh[(match, n)] = data
            if len(build["received"]) + len(fresh) > build["expected"]:
                raise ValueError("more rounds than the build was opened with")
            if self._spooled() + sum(len(d) for d in fresh.values()) > self.settings.height_spool_bytes:
                raise OverflowError("the spool is full")
            for (match, n), data in fresh.items():
                folder = self.root / build["id"] / match
                folder.mkdir(exist_ok=True)
                partial = folder / f"{n}.json.gz.tmp"
                partial.write_bytes(data)
                os.replace(partial, folder / f"{n}.json.gz")
                build["received"][(match, n)] = (hashlib.sha256(data).hexdigest(), len(data))
            build["touched"] = time.time()
            return {"received": len(build["received"])}

    def start(self, build_id: str, previous: str | None) -> dict:
        with self.lock:
            build = self.builds.get(build_id)
            if build is None:
                raise LookupError(build_id)
            if build["job"] is None:
                if len(build["received"]) != build["expected"]:
                    raise HeightConflict("rounds are missing", received=len(build["received"]),
                                         expected=build["expected"])
                task = {"key": build["key"], "map": build["map"], "dir": str(self.root / build["id"]),
                        "manifest": build["manifest"]}
                if build.get('diagnostic') is not None:
                    task['diagnostic'] = build['diagnostic']
                if previous and HEIGHT_DIGEST.match(str(previous)):
                    task["previous"] = str(previous)
                build["job"] = self.control.submit_build(build["key"], build["map"], task).id
            return self._public(build)

    def cancel(self, build_id: str) -> bool:
        with self.lock:
            self._drop(self._collecting(build_id))
            return True

    def get(self, build_id: str) -> dict | None:
        with self.lock:
            build = self.builds.get(build_id)
            return None if build is None else self._public(build)

    def status(self) -> dict:
        with self.lock:
            out = {"collecting": [], "queued": [], "running": []}
            for build in self.builds.values():
                state = self._state(build)
                if state in out:
                    out[state].append(build["map"])
            return {**{k: sorted(v) for k, v in out.items()}, "spool_bytes": self._spooled(), 'diagnostics_protocol': 1}

    # ---- housekeeping

    def sweep(self, now: float | None = None) -> None:
        """Deletes the spool of every build that has ended (its answer stays), drops collectors nobody has fed
        for `height_collect_ttl_s`, and forgets all but the newest HEIGHT_BUILDS_KEPT ended builds."""
        now = time.time() if now is None else now
        with self.lock:
            for build in list(self.builds.values()):
                state = self._state(build)
                if state in ("done", "failed") and build["ended"] is None:
                    build["ended"], build["received"] = now, {}
                    shutil.rmtree(self.root / build["id"], ignore_errors=True)
                elif state == "collecting" and now - build["touched"] > self.settings.height_collect_ttl_s:
                    self._drop(build)
            ended = sorted((b for b in self.builds.values() if b["ended"] is not None), key=lambda b: b["ended"])
            for old in ended[:-HEIGHT_BUILDS_KEPT] if len(ended) > HEIGHT_BUILDS_KEPT else []:
                self._drop(old)

    def _sweep_forever(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 - housekeeping must never stop
                traceback.print_exc()

    # ---- under the lock

    def _collecting(self, build_id: str) -> dict:
        build = self.builds.get(build_id)
        if build is None:
            raise LookupError(build_id)
        if build["job"] is not None:
            raise HeightConflict("not collecting", status=self._state(build))
        return build

    def _spooled(self) -> int:
        return sum(size for b in self.builds.values() for _, size in b["received"].values())

    def _state(self, build: dict) -> str:
        if build["job"] is None:
            return "collecting"
        job = self.control.get(build["job"])
        return "failed" if job is None else job.status

    def _public(self, build: dict) -> dict:
        body = {"id": build["id"], "key": build["key"], "map": build["map"], "status": self._state(build),
                "received": len(build["received"]) if build["ended"] is None else build["expected"],
                "expected": build["expected"]}
        if build.get('diagnostic') is not None:
            body['diagnostic'] = build['diagnostic']
        job = self.control.get(build["job"]) if build["job"] else None
        if build["job"] and job is None:
            body["error"] = "the build was forgotten"
        elif job is not None:
            if job.error:
                body["error"] = job.error
            if job.result is not None:
                body["result"] = job.result
        return body

    def _drop(self, build: dict) -> None:
        self.builds.pop(build["id"], None)
        shutil.rmtree(self.root / build["id"], ignore_errors=True)
        if build["job"]:
            self.control.forget(build["job"])


class _JobFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class UtilKeyError(ValueError):
    """A key (or a body) the util store refuses: a 400."""


class UtilFull(Exception):
    """A put that would pass `util_reserve`: a 507."""


class UtilStore:
    """The Critical Util inputs the site has sent: `rounds/<row_key>.json` and `obs/<obs_key>.json` under
    `settings.util_root()`, so a build reads them from disk and the site sends each only once. `runs/` (packed
    tars) and `results/` are scratch for builds and are cleared at every start, as heights clear their leftovers.

    - A file is written to a temp name and renamed; other bytes under the same key overwrite (the DB is authoritative).
    - A key not matching `build_digest.KEY_RE` is refused before anything is written.
    - `have(rows, obs, prune=True)` first deletes what the new corpus doesn't name, so a corpus that fits once the
      old files are replaced never gets stuck at a 507. Files named by a held build (`hold`, until `release`: one
      still queued or running) are never deleted.
    - The folders together stay under `util_reserve`."""

    KINDS = ("rounds", "obs")

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.util_root()
        self.reserve = settings.util_reserve
        for scratch in ("runs", "results"):
            shutil.rmtree(self.root / scratch, ignore_errors=True)
        for folder in (*self.KINDS, "runs", "results"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        for kind in self.KINDS:                     # a write a crash cut short
            for leftover in (self.root / kind).glob("*.tmp-*"):
                leftover.unlink(missing_ok=True)
        self.lock = threading.Lock()
        self._held: dict[str, tuple[frozenset, frozenset]] = {}
        try:
            self.digest = build_digest.digest(WEBAPP)
        except OSError:                             # a source this image doesn't carry: never dispatched to
            self.digest = None

    # ---- keys

    @staticmethod
    def check_key(key) -> str:
        if not isinstance(key, str) or re.fullmatch(build_digest.KEY_RE, key) is None:
            raise UtilKeyError("not a valid key")
        return key

    def _path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def _keys(self, kind: str) -> list[str]:
        return [p.name[:-len(".json")] for p in (self.root / kind).iterdir() if p.name.endswith(".json")]

    # ---- what the site asks

    def have(self, rows, obs=None, prune: bool = False) -> dict:
        rows = [self.check_key(k) for k in rows]
        obs_keys = None if obs is None else [self.check_key(k) for k in obs]
        with self.lock:
            if prune:
                self._prune(rows, obs_keys)
            return {"missing_rows": self._missing("rounds", rows),
                    "missing_obs": self._missing("obs", obs_keys or [])}

    def _missing(self, kind: str, keys) -> list[str]:
        seen, missing = set(), []
        for key in keys:
            if key not in seen and not self._path(kind, key).is_file():
                missing.append(key)
            seen.add(key)
        return missing

    def put(self, kind: str, items: dict[str, bytes]) -> int:
        """Store the batch, or none of it (a bad key, or past the reserve)."""
        for key in items:
            self.check_key(key)
        with self.lock:
            delta = 0
            for key, data in items.items():
                path = self._path(kind, key)
                delta += len(data) - (path.stat().st_size if path.is_file() else 0)
            if delta > 0 and self._usage() + delta > self.reserve:
                raise UtilFull("the util store is at its reserve")
            for key, data in items.items():
                path = self._path(kind, key)
                temp = path.with_name(f"{key}.json.tmp-{uuid.uuid4().hex}")
                temp.write_bytes(data)
                os.replace(temp, path)
        return len(items)

    def put_round(self, key: str, data: bytes) -> None:
        self.put("rounds", {key: data})

    def put_obs(self, key: str, data: bytes) -> None:
        self.put("obs", {key: data})

    def prune(self, keep_rows, keep_obs=None) -> int:
        """Delete every round file not named and, only when `keep_obs` is given, every observation file not named."""
        with self.lock:
            return self._prune([self.check_key(k) for k in keep_rows],
                               None if keep_obs is None else [self.check_key(k) for k in keep_obs])

    def _prune(self, keep_rows, keep_obs) -> int:
        held_rows = set().union(*(rows for rows, _ in self._held.values())) if self._held else set()
        held_obs = set().union(*(obs for _, obs in self._held.values())) if self._held else set()
        removed = 0
        for kind, keep, held in (("rounds", keep_rows, held_rows), ("obs", keep_obs, held_obs)):
            if keep is None:
                continue
            keep = set(keep) | held
            for key in self._keys(kind):
                if key not in keep:
                    self._path(kind, key).unlink(missing_ok=True)
                    removed += 1
        return removed

    def hold(self, ref: str, rows, obs) -> None:
        """A build is queued or running on these files: no prune deletes them until `release`."""
        with self.lock:
            self._held[ref] = (frozenset(rows), frozenset(obs))

    def release(self, ref: str) -> None:
        with self.lock:
            self._held.pop(ref, None)

    def _usage(self) -> int:
        total = 0
        for kind in self.KINDS:
            for path in (self.root / kind).iterdir():
                if path.name.endswith(".json"):
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        return total

    def usage(self) -> int:
        """Bytes of `rounds` + `obs`."""
        with self.lock:
            return self._usage()


UTIL_BUILDS_KEPT = 4             # ended util builds whose result stays readable (the newest)
UTIL_BUILD_BODY_MAX = 64 * 1024 * 1024   # POST /util/build: the frozen context travels in the open


class UtilMissing(Exception):
    """A build named rows or observations the store doesn't hold: a 409 naming them."""

    def __init__(self, rows: list, obs: list):
        super().__init__("inputs are missing")
        self.rows, self.obs = rows, obs


class UtilBuilds:
    """Critical Util builds as the web app drives them (module docstring): open one, ask after it, read its result.
    Each build is one Workflow run (`util`): the worker packs the stored rounds and observations into a tar, the
    run downloads it, computes, streams its output to `POST /runs/{job}/output` and posts a small envelope to
    `/runs/{job}/result`. The envelope must describe the streamed file (size and sha256) or the build failed.

    - **One build per key** (the key includes the refit state): opening a key again answers the build there is, in
      whatever state; only a failed one is replaced by a fresh one.
    - **One build runs at a time**; the rest wait queued, in the order they were opened.
    - A build **holds** its files (`UtilStore.hold`) from open until it settles, so no prune deletes them; at open
      the store is pruned to the build's rows, and to its observations only when it refits.
    - The newest UTIL_BUILDS_KEPT ended builds keep their result file; older ones go with it."""

    def __init__(self, settings: Settings, util: UtilStore, runs_table):
        self.settings, self.util, self.runs = settings, util, runs_table
        self.lock = threading.Lock()
        # id -> {id, key, ref, digest, refit, rows, obs, context, status, answer, error, error_kind, output, ended}
        self.builds: dict[str, dict] = {}
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, name="util-builds", daemon=True).start()

    # ---- what the web app asks for

    def open(self, key, digest, rows, obs, context, refit, ref=None) -> dict:
        key = self.util.check_key(key)
        if not isinstance(digest, str) or not digest or not isinstance(rows, list) or not isinstance(obs, list) \
                or not isinstance(refit, bool) or not isinstance(context, str):
            raise UtilKeyError("not a util build")
        rows, obs = [self.util.check_key(k) for k in rows], [self.util.check_key(k) for k in obs]
        try:
            context_bytes = base64.b64decode(context, validate=True)
        except ValueError as error:                 # binascii.Error is one
            raise UtilKeyError("the context is not base64") from error
        ref = str(ref)[:64] if ref else None
        with self.lock:
            for old in list(self.builds.values()):
                if old["key"] == key:
                    if old["status"] != "failed":
                        return self._public(old)    # still going, or done: the same build and its answer
                    self._drop(old)                 # it failed: the web app is trying again, from nothing
            have = self.util.have(rows, obs)
            if have["missing_rows"] or have["missing_obs"]:
                raise UtilMissing(have["missing_rows"], have["missing_obs"])
            build = {"id": uuid.uuid4().hex, "key": key, "ref": ref, "digest": digest, "refit": refit,
                     "rows": rows, "obs": obs, "context": context_bytes, "status": "queued", "answer": None,
                     "error": None, "error_kind": None, "output": None, "ended": None}
            self.util.hold(build["id"], rows, obs)
            self.util.prune(rows, obs if refit else None)
            self.builds[build["id"]] = build
            self._log(build)
            made = self._public(build)
        self._queue.put(build["id"])
        return made

    def get(self, build_id: str) -> dict | None:
        with self.lock:
            build = self.builds.get(build_id)
            return None if build is None else self._public(build)

    def result_path(self, build_id: str) -> Path | None:
        """The streamed bytes of a done build; None until then."""
        with self.lock:
            build = self.builds.get(build_id)
            if build is None or build["status"] != "done":
                return None
            return self._result_file(build_id)

    # ---- what a run's output route reports

    def owns(self, build_id: str) -> bool:
        with self.lock:
            build = self.builds.get(build_id)
            return build is not None and build["status"] in ("queued", "running")

    def result_file(self, build_id: str) -> Path:
        return self._result_file(build_id)

    def record_output(self, build_id: str, size: int, sha256: str) -> None:
        with self.lock:
            build = self.builds.get(build_id)
            if build is not None:
                build["output"] = (size, sha256)

    # ---- the worker thread

    def _work(self) -> None:
        while True:
            build_id = self._queue.get()
            with self.lock:
                build = self.builds.get(build_id)
                if build is None or build["status"] != "queued":
                    continue
                build["status"] = "running"
                self._log(build)
            try:
                answer = self._execute(build)
            except Exception as error:  # noqa: BLE001 - one build's trouble never stops the thread
                traceback.print_exc()
                answer = runs_mod.failure("infra", f"the build could not run: {error}")
            self._settle(build, answer)

    def _execute(self, build: dict) -> dict:
        timeout = self.settings.util_run_timeout_s
        tar_path = self.util.root / "runs" / f"{build['id']}.tar"
        try:
            try:
                self._pack(build, tar_path)
            except FileNotFoundError as error:
                return runs_mod.failure("inputs", f"a stored input went missing: {error.filename}")
            run = self.runs.open("util", build["id"], {"inputs": tar_path}, timeout)
            try:
                args = {"key": build["key"], "digest": build["digest"]}
                if build["ref"]:
                    args["ref"] = build["ref"]
                self.runs.start(run, args)
                answer = self.runs.wait(run)
            except BaseException:
                self.runs.close(run)          # wait closes it itself; a failure before that must not leak it
                raise
        finally:
            tar_path.unlink(missing_ok=True)
        if answer.get("error_kind") == "timeout":
            answer = {**answer, "error_kind": "infra", "error": f"util build timed out after {timeout:g} s"}
        return answer

    def _pack(self, build: dict, tar_path: Path) -> None:
        """The tar `corpus.unpack_inputs` reads: `context.json`, `rounds/<key>.json`, `obs/<key>.json`. Written with
        the standard library, member by member, so no round is held in memory beside its tar entry."""
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "w") as archive:
            member = tarfile.TarInfo("context.json")
            member.size = len(build["context"])
            archive.addfile(member, io.BytesIO(build["context"]))
            for folder, kind, keys in (("rounds", "rounds", build["rows"]), ("obs", "obs", build["obs"])):
                for key in dict.fromkeys(keys):
                    source = self.util._path(kind, key)
                    with source.open("rb") as handle:
                        member = tarfile.TarInfo(f"{folder}/{key}.json")
                        member.size = os.fstat(handle.fileno()).st_size
                        archive.addfile(member, handle)

    def _settle(self, build: dict, answer: dict) -> None:
        with self.lock:
            problem = None
            output = build["output"]
            if answer.get("status") in ("ok", "done"):
                path = self._result_file(build["id"])
                if output is None or not path.is_file():
                    problem = "the run posted a result but streamed no output"
                elif answer.get("sha256") != output[1] or answer.get("bytes") != output[0]:
                    problem = "the result's size or sha256 differs from the streamed output"
                if problem is None:
                    build.update(status="done", answer=answer)
            else:
                kind = answer.get("error_kind")
                build["error_kind"] = kind if isinstance(kind, str) and kind else "infra"
                build["error"] = str(answer.get("error") or "the run failed")
            if build["status"] != "done":
                build["status"] = "failed"
                if problem is not None:
                    build["error_kind"], build["error"] = "inputs", problem
                self._result_file(build["id"]).unlink(missing_ok=True)
            build["ended"], build["context"] = time.time(), b""
            self.util.release(build["id"])
            self._log(build)
            ended = sorted((b for b in self.builds.values() if b["ended"] is not None), key=lambda b: b["ended"])
            for old in ended[:-UTIL_BUILDS_KEPT]:
                self._drop(old)

    # ---- under the lock

    def _result_file(self, build_id: str) -> Path:
        return self.util.root / "results" / f"{build_id}.json"

    def _drop(self, build: dict) -> None:
        self.builds.pop(build["id"], None)
        self.util.release(build["id"])
        self._result_file(build["id"]).unlink(missing_ok=True)

    def _log(self, build: dict) -> None:
        extra = f" error_kind={build['error_kind']}" if build["status"] == "failed" else ""
        ref = f" ref={build['ref']}" if build["ref"] else ""
        _log(f"util build {build['status']} key={build['key']} id={build['id']}{ref}{extra}")

    def _public(self, build: dict) -> dict:
        done = build["status"] == "done"
        answer = build["answer"] or {}
        body = {"id": build["id"], "key": build["key"], "values_key": answer.get("values_key") if done else None,
                "status": build["status"], "digest": answer.get("digest", build["digest"]) if done else build["digest"],
                "version": answer.get("version") if done else None,
                "refit": answer.get("refit", build["refit"]) if done else build["refit"],
                "bytes": build["output"][0] if done else None, "sha256": build["output"][1] if done else None}
        if build["status"] == "failed":
            body["error"], body["error_kind"] = build["error"], build["error_kind"]
        return body


# ---------------------------------------------------------------- HTTP

UTIL_BODY_MAX = 4 * 1024 * 1024
UTIL_PROTOCOL = 1


def make_handler(worker: Worker, control: ControlRunner | None = None, heights: HeightBuilds | None = None,
                 runs_table=None, util: "UtilStore | None" = None, util_builds: "UtilBuilds | None" = None):
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

        def _control_off(self):
            # Closing with an unread POST body can reset TCP before the client receives the 404.
            # Drain only an admitted-size body, with bounded reads and an overall time limit.
            limit = feature_artifacts.MAX_WIRE_BYTES if self.path == '/features' else worker.settings.control_max_bytes
            timeout = self.connection.gettimeout()
            try:
                remaining = int(self.headers.get('Content-Length', '0'))
                if 0 <= remaining <= limit:
                    deadline = time.monotonic() + 2
                    while remaining:
                        wait = deadline - time.monotonic()
                        if wait <= 0:
                            break
                        self.connection.settimeout(wait)
                        chunk = self.rfile.read1(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
            except (ValueError, OSError):
                pass
            finally:
                self.connection.settimeout(timeout)
            return self._send(HTTPStatus.NOT_FOUND, {'error': 'map control is off on this worker'})

        def _run_input(self):
            parts = self.path.split("/")          # ["", "runs", job, "input", name]
            name = urllib.parse.unquote(parts[4]) if len(parts) == 5 else ""
            path = runs_table.file(parts[2], name, self.headers.get("X-Run-Token")) if name else None
            try:
                source = path.open("rb") if path is not None else None
            except OSError:
                source = None
            if source is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "no such input"})
            # The size of the file opened, not of the path: a cache file os.replace'd in between keeps the old
            # inode open here, so the body always matches its header.
            with source:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.fstat(source.fileno()).st_size))
                self.end_headers()
                shutil.copyfileobj(source, self.wfile, runs_mod.CHUNK)

        def _archive_file(self):
            archive = worker.archive
            if archive is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "the archive is off", "code": "archive_off"})
            match_uuid = self.path[len("/archive/files/"):].lower()
            if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", match_uuid) is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "invalid match UUID", "code": "bad_uuid"})
            opened = archive.open_file(match_uuid)
            if isinstance(opened, str):
                reasons = {"tombstoned": "deleted on request", "not_in_archive": "not in the archive",
                           "missing_on_disk": "the archived file is missing on disk"}
                return self._send(HTTPStatus.NOT_FOUND, {"error": reasons[opened], "code": opened})
            source, sha256, size = opened
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("X-Archive-Sha256", sha256)
                self.end_headers()
                self.connection.settimeout(30)
                remaining = size
                while remaining:
                    chunk = source.read(min(ARCHIVE_CHUNK_BYTES, remaining))
                    if not chunk:
                        self.close_connection = True
                        return
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                    if remaining and archive.is_tombstoned(match_uuid):
                        self.close_connection = True
                        return
            except OSError:
                self.close_connection = True
            finally:
                source.close()

        def _length(self, low: int, high: int) -> int | None:
            """The request's Content-Length when it is a decimal integer in [low, high]; otherwise sends 411, 400
            or 413, marks the connection to close, and returns None. Nothing has been read or written yet."""
            raw = self.headers.get("Content-Length")
            if raw is None:
                self.close_connection = True
                self._send(HTTPStatus.LENGTH_REQUIRED, {"error": "a Content-Length is required"})
                return None
            if not raw.strip().isdigit():
                self.close_connection = True
                self._send(HTTPStatus.BAD_REQUEST, {"error": "Content-Length is not a positive number"})
                return None
            size = int(raw)
            if size < low:
                self.close_connection = True
                self._send(HTTPStatus.BAD_REQUEST, {"error": "the body is empty or too short"})
                return None
            if size > high:
                self.close_connection = True
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "the body is too large"})
                return None
            return size

        def _run_cache(self):
            parts = self.path.split("/")             # ["", "runs", job, "cache", name]
            name = urllib.parse.unquote(parts[4]) if len(parts) == 5 else ""
            run = runs_table.accepts(parts[2], self.headers.get("X-Run-Token"))
            job = control.get(parts[2]) if control is not None and run is not None else None
            if run is None or job is None or run.kind not in ("control", "heights", "warm") \
                    or not control_cache.valid(name, job.map):
                self.close_connection = True
                # Read a small refused body first (as _run_result does): closing with it unread can reset the
                # client's connection before it sees the answer.
                try:
                    unread = int(self.headers.get("Content-Length") or 0)
                    if 0 < unread <= runs_mod.CHUNK:
                        self.rfile.read(unread)
                except (ValueError, OSError):
                    pass
            if run is None or job is None or run.kind not in ("control", "heights", "warm"):
                return self._send(HTTPStatus.CONFLICT, {"error": "no open control run takes this file"})
            if not control_cache.valid(name, job.map):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a cache file of this map"})
            size = self._length(1, 64 * 1024 * 1024)
            if size is None:
                return None
            incoming = control.settings.control_cache_dir / ".incoming" / f"{uuid.uuid4().hex}.npz"
            incoming.parent.mkdir(parents=True, exist_ok=True)
            try:
                with incoming.open("wb") as sink:
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(runs_mod.CHUNK, remaining))
                        if not chunk:
                            break
                        sink.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": "file cut short"})
                # A file arriving as its run settles is still kept: caches are named by their content's identity.
                if not control_cache.store(control.settings.control_cache_dir, name, incoming,
                                           reserve=control.settings.control_cache_reserve):
                    return self._send(HTTPStatus.INSUFFICIENT_STORAGE, {"error": "the cache is at its reserve"})
                return self._send(HTTPStatus.OK, {"stored": name})
            finally:
                incoming.unlink(missing_ok=True)

        def _util_off(self):
            # Drain an admitted-size body first: closing with it unread can reset the client before the 404.
            try:
                remaining = int(self.headers.get("Content-Length", "0"))
                if 0 <= remaining <= UTIL_BODY_MAX:
                    self.rfile.read(remaining)
                else:
                    self.close_connection = True
            except (ValueError, OSError):
                self.close_connection = True
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _util(self):
            """POST /util/have | /util/rounds | /util/obs. Each logs one line: `util <what> ref=<build_ref> counts`."""
            what = self.path[len("/util/"):]
            if what not in ("have", "rounds", "obs"):
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if util is None or runs_table is None:
                return self._util_off()
            size = self._length(1, UTIL_BODY_MAX)
            if size is None:
                return None
            try:
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("not an object")
            except ValueError:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a JSON object"})
            ref = body.get("ref")
            ref = ref if isinstance(ref, str) and re.fullmatch(r"[0-9A-Za-z._-]{1,64}", ref) else "-"
            try:
                if what == "have":
                    rows, obs = body.get("rows", []), body.get("obs")
                    if not isinstance(rows, list) or not (obs is None or isinstance(obs, list)):
                        raise UtilKeyError("rows and obs are lists of keys")
                    prune = body.get("prune") is True
                    answer = util.have(rows, obs, prune=prune)
                    _log(f"util have ref={ref} rows={len(rows)} obs={len(obs or [])} prune={int(prune)} "
                         f"missing_rows={len(answer['missing_rows'])} missing_obs={len(answer['missing_obs'])}")
                    return self._send(HTTPStatus.OK, answer)
                kind, field_name = ("rounds", "rows") if what == "rounds" else ("obs", "obs")
                entries = body.get(field_name)
                if not isinstance(entries, list):
                    raise UtilKeyError(f"{field_name} is a list of {{key, data}}")
                items = {}
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("data"), str):
                        raise UtilKeyError("an entry is {key, data (base64)}")
                    try:
                        items[util.check_key(entry.get("key"))] = base64.b64decode(entry["data"], validate=True)
                    except ValueError as error:
                        if isinstance(error, UtilKeyError):
                            raise
                        raise UtilKeyError("data is not base64") from None
                stored = util.put(kind, items)
            except UtilKeyError as error:
                _log(f"util {what} ref={ref} refused: {error}")
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except UtilFull as error:
                _log(f"util {what} ref={ref} refused: at the reserve")
                return self._send(HTTPStatus.INSUFFICIENT_STORAGE, {"error": str(error)})
            _log(f"util {what} ref={ref} {field_name}={len(entries)} stored={stored}")
            return self._send(HTTPStatus.OK, {"stored": stored})

        def _util_build(self):
            """POST /util/build: open a build (or answer the one a key already has)."""
            if util is None or util_builds is None or runs_table is None:
                return self._util_off()
            body = self._json(limit=UTIL_BUILD_BODY_MAX)
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not JSON, or too large"})
            try:
                made = util_builds.open(body["key"], body["digest"], body["rows"], body["obs"], body["context"],
                                        body["refit"], body.get("ref"))
            except UtilMissing as missing:
                return self._send(HTTPStatus.CONFLICT, {"missing_rows": missing.rows, "missing_obs": missing.obs})
            except (KeyError, TypeError, ValueError):       # UtilKeyError is a ValueError
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a util build"})
            return self._send(HTTPStatus.ACCEPTED, made)

        def _util_build_get(self):
            """GET /util/build/{id} and /util/build/{id}/result."""
            if util is None or util_builds is None or runs_table is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            parts = self.path[len("/util/build/"):].split("/")
            if len(parts) == 1:
                found = util_builds.get(parts[0])
                if found is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "no such util build"})
                return self._send(HTTPStatus.OK, found)
            path = util_builds.result_path(parts[0]) if len(parts) == 2 and parts[1] == "result" else None
            try:
                source = path.open("rb") if path is not None else None
            except OSError:
                source = None
            if source is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "no result"})
            with source:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(os.fstat(source.fileno()).st_size))
                self.end_headers()
                shutil.copyfileobj(source, self.wfile, runs_mod.CHUNK)

        def _run_output(self):
            """POST /runs/{job}/output: a util run's output, streamed to `results/<build id>.json` (job = build id)."""
            parts = self.path.split("/")          # ["", "runs", job, "output"]
            token = self.headers.get("X-Run-Token")
            run = runs_table.accepts(parts[2], token) if len(parts) == 4 else None
            limit = worker.settings.util_result_max
            try:
                declared = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                declared = 0
            if run is None or run.kind != "util" or util_builds is None or not util_builds.owns(parts[2]) \
                    or declared > limit:
                refused = run is None or run.kind != "util" or util_builds is None or not util_builds.owns(parts[2])
                self.close_connection = True
                # A small refused body is read first: closing with it unread can reset the client's connection
                # before it sees the answer. A large one is not read (the connection is closed on it).
                try:
                    if 0 < declared <= runs_mod.CHUNK:
                        self.rfile.read(declared)
                except OSError:
                    pass
                if refused:
                    return self._send(HTTPStatus.CONFLICT, {"error": "no open util run takes this output"})
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "the output is too large"})
            size = self._length(1, limit)
            if size is None:
                return None
            target = util_builds.result_file(parts[2])
            partial = target.with_name(f"{target.name}.part-{uuid.uuid4().hex}")
            digest, remaining = hashlib.sha256(), size
            try:
                with partial.open("wb") as sink:
                    while remaining:
                        chunk = self.rfile.read(min(runs_mod.CHUNK, remaining))
                        if not chunk:
                            break
                        digest.update(chunk)
                        sink.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    return self._send(HTTPStatus.BAD_REQUEST, {"error": "output cut short"})
                if not util_builds.owns(parts[2]):         # settled while it streamed
                    return self._send(HTTPStatus.CONFLICT, {"error": "no open util run takes this output"})
                os.replace(partial, target)
                util_builds.record_output(parts[2], size, digest.hexdigest())
            finally:
                partial.unlink(missing_ok=True)
            sys.stderr.write(f"run output {parts[2]} util {size} bytes\n")
            return self._send(HTTPStatus.OK, {"bytes": size, "sha256": digest.hexdigest()})

        def _run_started(self):
            parts = self.path.split("/")          # ["", "runs", job, "started"]
            ok = len(parts) == 4 and runs_table.started(parts[2], self.headers.get("X-Run-Token"))
            return self._send(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"ok": ok})

        def _run_result(self):
            parts = self.path.split("/")          # ["", "runs", job, "result"]
            token = self.headers.get("X-Run-Token")
            run = runs_table.accepts(parts[2], token) if len(parts) == 4 else None
            if run is None:
                self.close_connection = True
                # Read a small refused body first: closing with it unread can reset the client's connection
                # before it sees the 409. A large one is not read (the connection is closed on it).
                try:
                    unread = int(self.headers.get("Content-Length") or 0)
                    if 0 < unread <= runs_mod.CHUNK:
                        self.rfile.read(unread)
                except (ValueError, OSError):
                    pass
                return self._send(HTTPStatus.CONFLICT, {"error": "no open run takes this result"})
            size = self._length(1, worker.settings.run_result_max_bytes)
            if size is None:
                return None
            target = run.folder / f"result-{uuid.uuid4().hex}.json"
            with target.open("wb") as sink:
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(runs_mod.CHUNK, remaining))
                    if not chunk:
                        break
                    sink.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                target.unlink(missing_ok=True)
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "result cut short"})
            code = runs_table.deliver(parts[2], token, target)
            target.unlink(missing_ok=True)
            # The sizes step 3 reads before step 5 shrinks the worker (spec D18).
            sys.stderr.write(f"run result {parts[2]} {run.kind} {size} bytes -> {code}\n")
            return self._send(code, {"accepted": code == 200})

        def _run_ping(self):
            text = uuid.uuid4().hex
            job_id = f"ping{text[:12]}"
            source = worker.settings.temp_root / "runs" / f"{job_id}.txt"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(text, encoding="utf-8")
            started = time.monotonic()
            try:
                run = runs_table.open("ping", job_id, {"ping": source}, timeout_s=300)
                runs_table.start(run, {})
                answer = runs_table.wait(run)
            finally:
                source.unlink(missing_ok=True)
            return self._send(HTTPStatus.OK, {"ok": answer.get("echo") == text, "answer": answer,
                                              "seconds": round(time.monotonic() - started, 1)})

        def do_GET(self):  # noqa: N802
            if runs_table is not None and self.path.startswith("/runs/") and "/input/" in self.path:
                return self._run_input()
            if self.path == "/health":
                body = {"ok": True, "queued": worker.queue.qsize(),
                        "limits": {"timeout_s": worker.settings.timeout_s, "max_bytes": worker.settings.max_bytes,
                                   "queue_size": worker.settings.queue_size, "memory_cap": worker.memory_cap}}
                body["control"] = {"enabled": bool(control and control.enabled), "gaps_protocol": GAPS_PROTOCOL,
                                   "features": feature_artifacts.protocol_identity(),
                                   **(control.counts() if control else {})}
                if control is not None:
                    body["control"]["cache"] = {**control_cache.usage(control.settings.control_cache_dir),
                                                "reserve": control.settings.control_cache_reserve}
                body["archive"] = worker.archive_status()
                body["recipe"] = worker.recipe
                # "A height build may start now" (app/services/replay_heights_remote.py). A Workflow build runs on
                # another machine, so a parse here never holds it back.
                remote_builds = runs_table is not None and worker.settings.heights_runner == "workflow"
                body["idle"] = True if remote_builds else worker.idle()
                body["heights"] = heights.status() if heights else {"collecting": [], "queued": [], "running": [],
                                                                    "spool_bytes": 0}
                body["runners"] = {"parse": worker.settings.parse_runner, "control": worker.settings.control_runner,
                                   "heights": worker.settings.heights_runner}
                body["workflow"] = runs_table.status() if runs_table is not None else None
                on = util is not None and runs_table is not None
                body["util"] = {"enabled": on, "digest": util.digest if on else None, "protocol": UTIL_PROTOCOL,
                                "bytes": util.usage() if on else 0, "reserve": worker.settings.util_reserve}
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
            if self.path.startswith("/archive/files/"):
                return self._archive_file()
            if self.path.startswith("/util/build/"):
                return self._util_build_get()
            if self.path.startswith("/heights/build/"):
                found = heights.get(self.path[len("/heights/build/"):]) if heights else None
                if found is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "no such height build"})
                return self._send(HTTPStatus.OK, found)
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
            if self.path == "/util/build":
                return self._util_build()
            if self.path.startswith("/util/"):
                return self._util()
            if runs_table is not None and self.path.startswith("/runs/"):
                if self.path == "/runs/ping":
                    return self._run_ping()
                if self.path.endswith("/output"):
                    return self._run_output()
                if "/cache/" in self.path:
                    return self._run_cache()
                if self.path.endswith("/result"):
                    return self._run_result()
                if self.path.endswith("/started"):
                    return self._run_started()
            if self.path == "/heights/build" or self.path.startswith("/heights/build/"):
                return self._height_build()
            if self.path == "/heights":
                return self._heights()
            if self.path == '/features':
                return self._features()
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
            size = self._length(len(VRF_MAGIC), worker.settings.max_bytes)
            if size is None:
                return None
            try:
                job_id, folder = worker.reserve_folder(size)
            except NoSpace:
                self.close_connection = True
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the worker is busy, try again soon"})
            # The upload streams to disk in CHUNKs, hashed as it goes: a 200 MB file is never held in memory.
            self.connection.settimeout(30)               # a stalled client can't hold this thread for ever
            digest, remaining, first = hashlib.sha256(), size, True
            try:
                with (folder / "upload.vrf").open("wb") as sink:
                    while remaining:
                        chunk = self.rfile.read(min(runs_mod.CHUNK, remaining))
                        if not chunk:
                            break
                        if first:
                            first = False
                            if not chunk.startswith(VRF_MAGIC):
                                break
                        digest.update(chunk)
                        sink.write(chunk)
                        remaining -= len(chunk)
            except OSError:
                remaining = remaining or 1
            if remaining:
                shutil.rmtree(folder, ignore_errors=True)
                self.close_connection = True
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a Valorant replay"})
            try:
                job = worker.submit_streamed(job_id, folder, digest.hexdigest(), size)
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
                return self._control_off()
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
            digest = task.get("height")
            if digest and not (MAP_NAME.match(str(task["map"])) and HEIGHT_DIGEST.match(str(digest))
                               and height_path(control.settings, str(task["map"]), str(digest)).is_file()):
                return self._send(HTTPStatus.CONFLICT, {"error": "height asset missing", "height": digest})
            if task.get('features'):
                try:
                    feature_artifacts.load_cached_artifact(control.settings.control_cache_dir / 'features', task['features'])
                except feature_artifacts.FeatureArtifactError:
                    return self._send(HTTPStatus.CONFLICT, {'code': 'needs_features', 'digest': task['features']})
            try:
                job = control.submit(task)
            except queue.Full:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the control queue is full"})
            # Where this round runs, from the worker itself: the site fixes its running backstop from it, never
            # from a /health read that may have failed around the submission.
            return self._send(HTTPStatus.ACCEPTED, {"id": job.id, "status": job.status,
                                                    "runner": "workflow" if control.remote(job) else "local"})

        def _features(self):
            if control is None or not control.enabled:
                return self._control_off()
            length = self.headers.get('Content-Length')
            if length is not None and int(length) > feature_artifacts.MAX_WIRE_BYTES:
                self.close_connection = True
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {'error': 'feature artifact too large'})
            body = self._json(limit=feature_artifacts.MAX_WIRE_BYTES)
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {'error': 'invalid or oversized feature artifact'})
            try:
                item = feature_artifacts.decode_artifact(json.dumps(body).encode('utf-8'))
                feature_artifacts.store_cached_artifact(control.settings.control_cache_dir / 'features', item,
                                                        lambda artifact: verify_feature_upload(control.settings, artifact))
            except feature_artifacts.FeatureArtifactError as exc:
                return self._send(HTTPStatus.BAD_REQUEST, feature_artifacts.feature_failure(exc))
            return self._send(HTTPStatus.OK, {'stored': True, 'digest': item.digest})

        def _heights(self):
            if control is None or not control.enabled:
                return self._control_off()
            length = self.headers.get("Content-Length")
            if length is not None and int(length) > control.settings.control_max_bytes:
                self.close_connection = True
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "height asset too large"})
            body = self._json(limit=control.settings.control_max_bytes)
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height asset"})
            name, digest = str(body.get("map") or ""), str(body.get("digest") or "")
            try:
                data = base64.b64decode(body.get("asset") or "", validate=True)
            except (ValueError, TypeError):
                data = b""
            if not MAP_NAME.match(name) or not HEIGHT_DIGEST.match(digest) or not data:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height asset"})
            store_height(control.settings, name, digest, data)
            return self._send(HTTPStatus.OK, {"stored": True})

        def _height_build(self):
            if heights is None or control is None or not control.enabled:
                return self._control_off()
            body = self._json(limit=control.settings.control_max_bytes)
            if body is None:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not JSON, or too large"})
            parts = self.path[len("/heights/build"):].strip("/").split("/")
            try:
                if parts == [""]:
                    made = heights.open(str(body["key"]), str(body["map"]), body["rounds"], body.get("manifest"),
                                        diagnostic=body.get('diagnostic'))
                    return self._send(HTTPStatus.ACCEPTED, made)
                if len(parts) == 2 and parts[1] == "rounds":
                    if not isinstance(body.get("rounds"), list):
                        raise ValueError("rounds must be a list")
                    return self._send(HTTPStatus.OK, heights.add(parts[0], body["rounds"]))
                if len(parts) == 2 and parts[1] == "start":
                    return self._send(HTTPStatus.ACCEPTED, heights.start(parts[0], body.get("previous")))
                if len(parts) == 2 and parts[1] == "cancel":
                    return self._send(HTTPStatus.OK, {"cancelled": heights.cancel(parts[0])})
            except KeyError:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height build"})
            except LookupError:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "no such height build"})
            except HeightConflict as conflict:
                return self._send(HTTPStatus.CONFLICT, {"error": conflict.reason, **conflict.facts})
            except OverflowError as full:
                opening = parts == [""]
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE if opening else HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                  {"error": str(full)})
            except (TypeError, ValueError):
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a height build"})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def make_server(worker: Worker, host: str = "127.0.0.1", port: int = 0,
                control: ControlRunner | None = None, heights: HeightBuilds | None = None,
                runs_table=None, util: "UtilStore | None" = None,
                util_builds: "UtilBuilds | None" = None) -> ThreadingHTTPServer:
    if util_builds is None and util is not None and runs_table is not None:
        util_builds = UtilBuilds(worker.settings, util, runs_table)
    httpd = ThreadingHTTPServer((host, port), make_handler(worker, control, heights, runs_table, util, util_builds))
    httpd.util_builds = util_builds
    return httpd


def main() -> None:
    settings = Settings.from_env()
    keep_local_sizing(settings)
    runs_table = make_runs(settings)
    if runs_table is not None:
        threading.Thread(target=runs_table.reconcile_forever, args=(threading.Event(),), name="runs-reconcile",
                         daemon=True).start()
    # The cache's room is reserved before the Worker opens the archive and recovers its jobs.
    if settings.archive_dir is not None and settings.control_cache_dir.resolve().is_relative_to(
            settings.archive_dir.resolve()):
        settings.archive_extra_reserve += settings.control_cache_reserve
    if settings.archive_dir is not None and settings.height_spool_dir is not None and \
            settings.height_spool_dir.resolve().is_relative_to(settings.archive_dir.resolve()):
        settings.archive_extra_reserve += 2 * settings.height_spool_bytes    # the spool, and its tars (D16)
    reserve_util(settings, runs_table)
    control_cache.clear_incoming(settings.control_cache_dir)
    util = UtilStore(settings) if runs_table is not None else None
    control = ControlRunner(settings, runs_table=runs_table) if settings.control_enabled else None
    # Preemption follows where parsing runs (spec D17): a local parse protects the CPU for local children;
    # ControlRunner.preempt itself never touches a Workflow run.
    local_parse = settings.parse_runner == "local" or runs_table is None
    worker = Worker(settings, on_parse=control.preempt if (control is not None and local_parse) else None,
                    runs_table=runs_table)
    if control is not None:
        # Wired after the Worker exists. Until then idle() reads True, but the control queue is empty: the
        # web app hasn't sent anything to a server that isn't listening yet.
        control.idle = worker.idle
    heights = HeightBuilds(settings, control) if control is not None else None
    server = make_server(worker, os.environ.get("REPLAY_WORKER_HOST", "0.0.0.0"),
                         int(os.environ.get("REPLAY_WORKER_PORT", "8080")), control, heights,
                         runs_table=runs_table, **({"util": util} if util is not None else {}))
    print(f"replay worker on {server.server_address}, timeout {settings.timeout_s:g} s, "
          f"queue {settings.queue_size}, cap {settings.max_bytes} bytes", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

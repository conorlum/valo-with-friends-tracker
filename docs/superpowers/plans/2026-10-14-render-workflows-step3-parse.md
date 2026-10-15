# Render Workflows, step 3: parsing on Workflows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With `REPLAY_PARSE_RUNNER=workflow`, each upload and re-parse is parsed and condensed in its own Workflow run, up to 15 at once; a worker restart reconnects to a parse run whose Render id it saved and otherwise starts the parse over, as today.

**Architecture:** The parse-and-condense code in `Worker._process` moves to `replay_worker/parse_job.py`, used both by the local path and by the new `parse` task. The worker keeps its queues, job records, held uploads, archive and receipts; in workflow mode it runs `REPLAY_PARSE_PARALLEL` parse threads. Each saves the job's run token and one deadline in `job.json` before starting a run, saves the Render id as soon as it has it, waits, and maps the answer onto today's job outcomes; the run identity is cleared only with the job's final save.

**Tech Stack:** Python 3.13 standard library, pytest, the step-1 run table.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (D1, D7, D8, D9, D11, D15, D18)

**Depends on:** step 1 merged; step 2 need not be.

## Global Constraints

- The `parse` task: plan `flex` (D7), `Retry(max_retries=0)` (D9), `timeout_seconds=1260`, defined above `main.py`'s `if __name__ == "__main__":` block. The parser keeps its own 240 s timeout (`REPLAY_TIMEOUT_S`) inside the run.
- One deadline per job (D8): `REPLAY_PARSE_RUN_TIMEOUT_S` = 1200 s after the job's first run starts, saved in `job.json` as `run_deadline`, kept across deploy refusals and restarts; every run of the job gets only the time left.
- Restart rule (D9, the owner's call): reconnect when `run_id` was saved; otherwise start over with the saved token and deadline. A rare second execution is accepted; the token makes the first result win.
- `REPLAY_PARSE_PARALLEL` = 15 and `REPLAY_QUEUE_SIZE` = 15 (D15): 15 run and up to 15 more wait. Recovered jobs go to a recovery list worked first, never to the bounded queue. The site's per-uploader `MAX_UNFINISHED` goes from 5 to 10 (Task 4).
- Deploy skew (D11): a run whose `current_recipe()` differs from the worker's answers `error_kind: "deploy"`; the worker waits 60 s (bounded by the time left) and starts a new run.
- The site, the receipts and the archive behave exactly as today; a job's outcomes and user-facing reasons are unchanged (`parse timed out`, `parse failed`, `not condensable: <reason>`, `the archived file is another match`).
- `REPLAY_PARSE_RUNNER` is `sync: false` (step 1) and stays unset (local) at merge.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`. A restart is tested with planted job records and **one** live Worker (as `test_replay_worker_archive.py` does), never two workers on one disk.

## Review Focus

- A restart while a parse run is going must reconnect to that run when its Render id was saved (no new start), and start over otherwise; either way within the job's original deadline.
- Recovering more accepted jobs than `REPLAY_QUEUE_SIZE` must fail none of them.
- Two parse threads must never take the same held upload, and a held upload expiring must never be released at the same moment (`_release_held` against `_sweep`).
- A run's result for a re-parse whose file turns out to be another match must fail the job with today's reason, not store it.
- `Worker.idle()` must read "not idle" while any of the parallel parses runs (local height builds still use it).
- A deploy refusal must neither fail the upload nor extend the job past its deadline.

---

## File structure

| File | Responsibility |
| --- | --- |
| `replay_worker/parse_job.py` (new) | `parse_file()`: run the parser, condense, build today's result dict; `ParseFailed`; `kill_tree`, `limit_memory`, `parser_detail`, the reasons (moved from `server.py`, re-exported there) |
| `replay_worker/server.py` (modify) | `Settings.parse_parallel`, `.parse_run_timeout_s`; `Job.run_id`, `.run_token`; `Worker` parse threads, counter, `_process_remote`, recovery |
| `replay_tasks/tasks.py`, `replay_tasks/main.py` (modify) | the `parse` task |
| tests | `test_replay_worker_parse_job.py` (new), `test_replay_worker_parse_workflow.py` (new) |
| `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md` | settings and the switch-over |

---

### Task 1: `parse_job.py`, shared by the worker and the task

**Files:**
- Create: `replay_worker/parse_job.py`
- Modify: `replay_worker/server.py` (`kill_tree`, `_limit_memory`, `parser_detail`, `REASON_TIMEOUT`, `REASON_PARSE`, `DETAIL_MAX_CHARS` move out; `Worker._process` calls `parse_file`)
- Test: `webapp/tests/replays/test_replay_worker_parse_job.py`

**Interfaces:**
- Produces: `ParseFailed(reason: str, detail: str | None = None, seconds: float | None = None)` (`seconds`: how long the parser ran before it failed, as `job.parse_seconds` records today for a nonzero exit; None on a timeout); `parse_file(vrf: Path, folder: Path, sha256: str, *, parser_cmd: list[str], timeout_s: float, memory_cap_mb: int, parser_build: Path | None) -> tuple[dict, float]` (result dict in today's shape, parse seconds); raises `ParseFailed` or `ContractError`; deletes `folder / "export"` before returning. `server.py` keeps the names `kill_tree`, `_limit_memory`, `parser_detail`, `REASON_TIMEOUT`, `REASON_PARSE` by importing them from `parse_job`.

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/replays/test_replay_worker_parse_job.py
"""replay_worker/parse_job.py: the parse-and-condense step shared by the worker's local path and the parse task."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from replay_worker import parse_job, server  # noqa: E402


def test_server_still_exports_the_moved_names():
    assert server.kill_tree is parse_job.kill_tree
    assert server.parser_detail is parse_job.parser_detail
    assert (server.REASON_TIMEOUT, server.REASON_PARSE) == ("parse timed out", "parse failed")


def test_a_parser_that_exits_nonzero_is_parse_failed_with_its_last_lines(tmp_path):
    script = tmp_path / "bad_parser.py"
    script.write_text("import sys; print('Reading header'); print('Boom: bad chunk'); sys.exit(3)", encoding="utf-8")
    vrf = tmp_path / "upload.vrf"
    vrf.write_bytes(b"x")
    with pytest.raises(parse_job.ParseFailed) as failed:
        parse_job.parse_file(vrf, tmp_path, "sha", parser_cmd=[sys.executable, str(script), "{vrf}", "{out}"],
                             timeout_s=30, memory_cap_mb=0, parser_build=None)
    assert failed.value.reason == "parse failed" and "Boom: bad chunk" in failed.value.detail
    assert failed.value.seconds is not None                     # today's job records the time of a failed parse


def test_a_parser_that_hangs_is_parse_timed_out(tmp_path):
    script = tmp_path / "slow_parser.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    vrf = tmp_path / "upload.vrf"
    vrf.write_bytes(b"x")
    with pytest.raises(parse_job.ParseFailed) as failed:
        parse_job.parse_file(vrf, tmp_path, "sha", parser_cmd=[sys.executable, str(script)], timeout_s=1,
                             memory_cap_mb=0, parser_build=None)
    assert failed.value.reason == "parse timed out"
```

The success path is already covered by `test_replay_worker.py`'s parse tests through `Worker._process`, which now calls `parse_file`; they must stay green unchanged.

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_job.py -v`
Expected: FAIL, `ImportError: cannot import name 'parse_job'`.

- [ ] **Step 3: Create `parse_job.py`**

Create `parse_job.py` as below. Where it says "the moved definitions", **cut** these from `server.py` and paste
them there verbatim (line numbers as of `origin/main` 571e656):
- `REASON_TIMEOUT` and `REASON_PARSE` (`server.py:172-173`),
- `DETAIL_MAX_CHARS` with its comment (`server.py:181`),
- `def parser_detail(...)` (`server.py:194-205`),
- `def kill_tree(...)` (`server.py:311-329`),
- `def _limit_memory(...)` (`server.py:332-340`), renamed `limit_memory`, followed by the alias line
  `_limit_memory = limit_memory`.

`parser_detail` uses `re`, `kill_tree` uses `os`, `signal`, `subprocess`; all are imported below.

```python
"""Parse one `.vrf` and condense it (Stage 3, W-c), for the worker's local path and the Workflow `parse` task
(docs/superpowers/specs/2026-10-14-render-workflows-design.md). Standard library and app/replays only."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

from app.replays.condense import condense_export_dir  # noqa: E402

# <the moved definitions, cut from server.py as listed above>


class ParseFailed(Exception):
    def __init__(self, reason: str, detail: str | None = None, seconds: float | None = None):
        super().__init__(reason)
        self.reason, self.detail, self.seconds = reason, detail, seconds


def parse_file(vrf: Path, folder: Path, sha256: str, *, parser_cmd: list[str], timeout_s: float,
               memory_cap_mb: int, parser_build: Path | None) -> tuple[dict, float]:
    out = folder / "export"
    command = [part.replace("{vrf}", str(vrf)).replace("{out}", str(out)) for part in parser_cmd]
    started = time.perf_counter()
    log = folder / "parser.log"
    try:
        with log.open("wb") as output:
            kwargs = {"stdout": output, "stderr": subprocess.STDOUT, "cwd": str(folder),
                      "env": {**os.environ, "VALO_MAP_AUDIT_RAW": "1"}}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["preexec_fn"] = limit_memory(memory_cap_mb)
            process = subprocess.Popen(command, **kwargs)
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                kill_tree(process)
                raise ParseFailed(REASON_TIMEOUT)
        seconds = round(time.perf_counter() - started, 1)
        if code != 0 or not (out / "manifest.json").exists():
            raise ParseFailed(REASON_PARSE, parser_detail(log), seconds)
    finally:
        log.unlink(missing_ok=True)
    build = json.loads(parser_build.read_text(encoding="utf-8-sig")) if parser_build is not None else None
    try:
        # The match UUID comes from the file's own header, never its name (check_file_name=False).
        replay = condense_export_dir(out, source_sha256=sha256, build=build, vrf_path=vrf, check_file_name=False)
    finally:
        shutil.rmtree(out, ignore_errors=True)     # the export is ~65x the file; the result is all we need
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
    }, seconds
```

The export is now always deleted after condensing. Today that happens only with the archive on; with it off the
whole job folder goes in `_run`'s `finally` a moment later, so nothing a caller sees changes.

- [ ] **Step 4: `server.py` uses it**

Replace the moved definitions with:

```python
try:
    from replay_worker.parse_job import (REASON_PARSE, REASON_TIMEOUT, ParseFailed, kill_tree,  # noqa: E402
                                         limit_memory as _limit_memory, parse_file, parser_detail)
except ImportError:  # pragma: no cover
    from parse_job import (REASON_PARSE, REASON_TIMEOUT, ParseFailed, kill_tree,  # type: ignore  # noqa: E402
                           limit_memory as _limit_memory, parse_file, parser_detail)
```

and `Worker._process` becomes:

```python
    def _process(self, job: Job) -> dict:
        if self.runs is not None and self.settings.parse_runner == "workflow":
            return self._process_remote(job)                     # Task 3
        try:
            result, job.parse_seconds = parse_file(
                job.folder / "upload.vrf", job.folder, job.sha256, parser_cmd=self.settings.parser_cmd,
                timeout_s=self.settings.timeout_s, memory_cap_mb=self.settings.memory_cap_mb,
                parser_build=self.settings.parser_build)
        except ParseFailed as failed:
            job.detail, job.parse_seconds = failed.detail, failed.seconds
            raise _JobFailed(failed.reason)
        return self._accept_result(job, result)

    def _accept_result(self, job: Job, result: dict) -> dict:
        """Today's checks on a parse's result, wherever it ran."""
        match_uuid = str(result["match_uuid"]).lower()
        if self.archive is not None and job.kind == "reparse" and match_uuid != job.match_uuid:
            raise _JobFailed("the archived file is another match")
        job.match_uuid, job.map = match_uuid, result["map_name"]
        return result
```

Until Task 3, write the first two lines of `_process` as a plain local call (no `runs`/`parse_runner` yet); Task 3 adds them.

- [ ] **Step 5: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_job.py tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_worker_held.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add replay_worker/parse_job.py replay_worker/server.py webapp/tests/replays/test_replay_worker_parse_job.py
git commit -m "replay worker: parse and condense in parse_job.py, shared with the parse task"
```

---

### Task 2: The `parse` task

**Files:**
- Modify: `replay_tasks/tasks.py`, `replay_tasks/main.py`
- Test: `webapp/tests/replays/test_replay_worker_parse_workflow.py` (new)

**Interfaces:**
- Consumes: `parse_job.parse_file`, `ParseFailed`; `server.Settings.from_env()` (the image's parser settings); `app.replays.contract.current_recipe`, `ContractError`.
- Produces: `tasks.parse(args, link)`; args `{"job_id", "token", "recipe", "sha256"}`. It posts one of:
  - `{"status": "ok", "result": <today's dict>, "parse_seconds": float}`
  - `{"status": "failed", "error_kind": "deploy", "error": "this run has recipe X, the worker Y"}`
  - `{"status": "failed", "error_kind": "parse", "error": "parse failed" | "parse timed out", "detail": str | None}`
  - `{"status": "failed", "error_kind": "contract", "error": "not condensable: <reason>"}`
  - `{"status": "failed", "error_kind": "infra", "error": "<Type>: <message>"}`

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/replays/test_replay_worker_parse_workflow.py
"""Parsing as Workflow runs (spec 2026-10-14, step 3): the parse task, then the worker's parse threads end to end
through a fake Render."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import parse_job, server  # noqa: E402


class FakeLink:
    def __init__(self, files):
        self.files, self.result = files, None

    def fetch(self, name, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_result(self, result):
        self.result = result


def test_parse_refuses_another_recipe(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r-new")
    called = []
    monkeypatch.setattr(parse_job, "parse_file", lambda *a, **k: called.append(1))
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r-old", "sha256": "s"}, link)
    assert link.result["error_kind"] == "deploy" and called == []      # refused before any parse


def test_parse_reports_a_parse_failure_with_detail(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")

    def boom(*a, **k):
        raise parse_job.ParseFailed("parse failed", "Boom: bad chunk", 3.5)
    monkeypatch.setattr(parse_job, "parse_file", boom)
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "failed", "error_kind": "parse", "error": "parse failed",
                           "detail": "Boom: bad chunk", "parse_seconds": 3.5}


def test_parse_posts_the_result(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")
    monkeypatch.setattr(parse_job, "parse_file", lambda *a, **k: ({"match_uuid": "M", "map_name": "Ascent"}, 4.2))
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "ok", "result": {"match_uuid": "M", "map_name": "Ascent"}, "parse_seconds": 4.2}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_workflow.py -v`
Expected: FAIL, `AttributeError: module 'replay_tasks.tasks' has no attribute 'parse'`.

- [ ] **Step 3: Implement**

In `replay_tasks/tasks.py`:

```python
def _recipe() -> str:
    from app.replays.contract import current_recipe
    return current_recipe()


def parse(args: dict, link: WorkerLink) -> None:
    """One upload or re-parse: the same parse and condense the worker runs locally (replay_worker/parse_job.py)."""
    from app.replays.contract import ContractError
    from replay_worker import parse_job
    from replay_worker.server import Settings

    mine = _recipe()
    if mine != args["recipe"]:
        link.post_result({"status": "failed", "error_kind": "deploy",
                          "error": f"this run has recipe {mine}, the worker {args['recipe']}"})
        return
    settings = Settings.from_env()
    work = Path(tempfile.mkdtemp(prefix="parse-"))
    try:
        vrf = link.fetch("vrf", work / "upload.vrf")
        try:
            result, seconds = parse_job.parse_file(vrf, work, args["sha256"], parser_cmd=settings.parser_cmd,
                                                   timeout_s=settings.timeout_s, memory_cap_mb=settings.memory_cap_mb,
                                                   parser_build=settings.parser_build)
            answer = {"status": "ok", "result": result, "parse_seconds": seconds}
        except parse_job.ParseFailed as failed:
            answer = {"status": "failed", "error_kind": "parse", "error": failed.reason, "detail": failed.detail,
                      "parse_seconds": failed.seconds}
        except ContractError as refused:
            answer = {"status": "failed", "error_kind": "contract", "error": f"not condensable: {refused.reason}"}
        except Exception as error:  # noqa: BLE001 - say what happened rather than die silently
            answer = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}"}
        link.post_result(answer)
    finally:
        shutil.rmtree(work, ignore_errors=True)


TASKS = {"ping": ping, "control": control, "parse": parse}
```

(If step 2 isn't merged yet, `TASKS` has no `control`; keep whatever entries exist and add `parse`.) `tasks.py` must put `webapp/` on `sys.path` before the `app` imports: add at the top of `parse` (and reuse in later tasks) `if str(WEBAPP) not in sys.path: sys.path.insert(0, str(WEBAPP))`, with `WEBAPP` defined as in step 2 (`Path(__file__).resolve().parents[1] / "webapp"`).

In `replay_tasks/main.py`, below the earlier tasks and **above** the `if __name__ == "__main__":` block:

```python
@app.task(name="parse", timeout_seconds=1260, retry=NO_RETRY)
def parse(ctx: TaskContext, args: dict) -> dict:
    tasks.parse(args, tasks.link_for(args))
    return {"ok": True}
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_workflow.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add replay_tasks webapp/tests/replays/test_replay_worker_parse_workflow.py
git commit -m "replay tasks: the parse task"
```

---

### Task 3: The worker parses through runs, in parallel, and reconnects after a restart

**Files:**
- Modify: `replay_worker/server.py` (`Settings`; `Job`; `Worker.__init__`, `idle`, `_next`, `_release_held`, `_run`, `_process`, `_save`, `_recover`; `main`)
- Test: `webapp/tests/replays/test_replay_worker_parse_workflow.py`

**Interfaces:**
- Consumes: `runs.Runs` (step 1), `tasks.parse` (Task 2), `_accept_result` (Task 1).
- Produces: `Settings.parse_parallel: int` (15, `REPLAY_PARSE_PARALLEL`), `Settings.parse_run_timeout_s: float` (1200, `REPLAY_PARSE_RUN_TIMEOUT_S`); `Job.run_id: str | None`, `Job.run_token: str | None`, `Job.run_deadline: float | None` (saved in `job.json` under those names); `Worker(settings, on_parse=None, runs_table=None)`; `Worker.parsing` (a read-only property: any parse running); `Worker.recovered: collections.deque[Job]`; `DEPLOY_WAIT_S = 60`.

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_parse_workflow.py`. These use a stub in place of the real parse task, so they run without the .NET parser. The worker is started by a helper, so restart tests can plant job records on the disk **before** the one live Worker starts:

```python
import collections
import uuid as uuidlib

VRF = server.VRF_MAGIC + b"\x00" * 64


class Rig:
    """A fake Render, settings with the archive on a temp 'disk', and at most one live worker."""

    def __init__(self, tmp_path, **overrides):
        self.render = FakeRender()
        self.seen, self.gate = [], threading.Event()
        self.gate.set()
        self.disk = tmp_path / "disk"
        self.disk.mkdir()
        self.settings = server.Settings(**{
            "temp_root": tmp_path / "tmp", "parse_runner": "workflow", "parse_parallel": 15, "queue_size": 15,
            "workflow_slug": "replay-tasks", "render_api_key": "k", "render_api_base": self.render.url,
            "archive_dir": self.disk, "archive_require_mount": False, "archive_total_bytes": 500 * 1024 ** 3,
            **overrides})
        self.render.runner = self._answer_ok
        self.httpd = self.worker = None

    def _answer_ok(self, kind, args):
        self.seen.append(args["job_id"])
        self.gate.wait(30)
        self.link(args).post_result({"status": "ok", "parse_seconds": 1.0,
                                     "result": {"match_uuid": f"m-{args['job_id']}", "map_name": "Ascent"}})

    def link(self, args, token=None):
        return tasks.WorkerLink(self.base, args["job_id"], token or args["token"])

    def start(self):
        table = server.make_runs(self.settings)
        self.worker = server.Worker(self.settings, runs_table=table)
        self.httpd = server.make_server(self.worker, runs_table=table)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        return self.worker

    def plant(self, status="parsing", **record):
        """A job record as an earlier worker left it on the disk."""
        job_id = uuidlib.uuid4().hex
        folder = self.disk / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "upload.vrf").write_bytes(VRF)
        body = {"id": job_id, "kind": "upload", "status": status, "error": None, "sha256": "s" * 64,
                "size": len(VRF), "created": time.time(), "finished": None, "parse_seconds": None,
                "match_uuid": None, "map": None, **record}
        (folder / "job.json").write_text(json.dumps(body), encoding="utf-8")
        return job_id

    def close(self):
        self.gate.set()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.render.close()


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    try:
        yield made
    finally:
        made.close()


def wait_jobs(worker, ids, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if all(worker.get(i) is not None and worker.get(i).status in ("done", "failed") for i in ids):
            return [worker.get(i) for i in ids]
        time.sleep(0.05)
    raise AssertionError("jobs didn't finish")


def wait_for(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never held")


def record_of(rig, job_id):
    return json.loads((rig.disk / "jobs" / job_id / "job.json").read_text(encoding="utf-8"))


def test_fifteen_uploads_parse_at_once(rig):
    worker = rig.start()
    rig.gate.clear()
    jobs = [worker.submit(VRF + bytes([i])) for i in range(15)]
    wait_for(lambda: len(rig.seen) == 15)
    assert worker.parse_count == 15 and worker.parsing and not worker.idle()
    rig.gate.set()
    done = wait_jobs(worker, [j.id for j in jobs])
    assert all(j.status == "done" and j.parse_seconds == 1.0 for j in done)
    assert worker.parse_count == 0
    assert {s["task"] for s in rig.render.started} == {"replay-tasks/parse"}


def test_token_deadline_then_run_id_are_saved_and_kept_until_settlement(rig):
    worker = rig.start()
    rig.gate.clear()
    job = worker.submit(VRF + b"a")
    wait_for(lambda: record_of(rig, job.id).get("run_id") == "trn-1")      # the durable record itself
    record = record_of(rig, job.id)
    assert record["status"] == "parsing" and record["run_token"] and record["run_deadline"] > time.time()
    assert rig.render.started[0]["input"][0]["token"] == record["run_token"]
    rig.gate.set()
    wait_jobs(worker, [job.id])
    final = record_of(rig, job.id)
    assert final["status"] == "done" and "run_id" not in final and "run_token" not in final


def test_a_restart_reconnects_to_a_saved_run_and_starts_nothing(rig):
    rig.render.finish = False
    rig.render.status["trn-9"], rig.render.slug_of["trn-9"] = "running", "replay-tasks/parse"
    job_id = rig.plant(run_id="trn-9", run_token="tok", run_deadline=time.time() + 600)
    worker = rig.start()
    wait_for(lambda: worker.get(job_id) is not None and worker.get(job_id).status == "parsing")
    time.sleep(0.3)
    assert rig.render.started == []                                         # reconnected, not restarted
    rig.link({"job_id": job_id}, token="tok").post_result(
        {"status": "ok", "parse_seconds": 3.0, "result": {"match_uuid": "m-x", "map_name": "Ascent"}})
    [done] = wait_jobs(worker, [job_id])
    assert done.status == "done" and done.parse_seconds == 3.0


def test_a_restart_without_a_run_id_starts_over_with_the_saved_token_and_deadline(rig):
    deadline = time.time() + 600
    job_id = rig.plant(run_token="tok", run_deadline=deadline)
    worker = rig.start()
    [done] = wait_jobs(worker, [job_id])
    assert done.status == "done" and len(rig.render.started) == 1
    assert rig.render.started[0]["input"][0]["token"] == "tok"


def test_recovery_beyond_the_queue_size_fails_nothing(tmp_path):
    rig = Rig(tmp_path, queue_size=2, parse_parallel=1)
    try:
        ids = [rig.plant(status=s) for s in ("parsing", "queued") * 6]          # 12 accepted, a queue of 2
        worker = rig.start()
        done = wait_jobs(worker, ids, timeout=60)
        assert all(j.status == "done" for j in done)
    finally:
        rig.close()


def test_deploy_refusals_never_outlive_the_jobs_deadline(tmp_path, monkeypatch):
    rig = Rig(tmp_path, parse_run_timeout_s=1.5)
    try:
        monkeypatch.setattr(server, "DEPLOY_WAIT_S", 0.4)
        rig.render.runner = lambda kind, args: rig.link(args).post_result(
            {"status": "failed", "error_kind": "deploy", "error": "recipe"})
        worker = rig.start()
        started = time.time()
        [done] = wait_jobs(worker, [worker.submit(VRF + b"d").id])
        assert done.status == "failed" and done.error == "parse timed out"
        assert time.time() - started < 4 and len(rig.render.started) <= 5
    finally:
        rig.close()


def test_a_deploy_refusal_waits_and_starts_a_new_run(rig, monkeypatch):
    monkeypatch.setattr(server, "DEPLOY_WAIT_S", 0.1)
    answers = iter([{"status": "failed", "error_kind": "deploy", "error": "recipe"}])
    rig.render.runner = lambda kind, args: rig.link(args).post_result(next(answers, {
        "status": "ok", "parse_seconds": 2.0, "result": {"match_uuid": "m", "map_name": "Ascent"}}))
    worker = rig.start()
    [done] = wait_jobs(worker, [worker.submit(VRF + b"b").id])
    assert done.status == "done" and len(rig.render.started) == 2


def test_failures_keep_todays_reasons(rig):
    answers = collections.deque([
        {"status": "failed", "error_kind": "parse", "error": "parse failed", "detail": "Boom", "parse_seconds": 2.0},
        {"status": "failed", "error_kind": "parse", "error": "parse timed out", "detail": None},
        {"status": "failed", "error_kind": "contract", "error": "not condensable: x"}])
    rig.render.runner = lambda kind, args: rig.link(args).post_result(answers.popleft())
    worker = rig.start()
    got = []
    for i in range(3):
        [job] = wait_jobs(worker, [worker.submit(VRF + bytes([10 + i])).id])
        got.append((job.status, job.error, job.detail))
    assert got == [("failed", "parse failed", "Boom"), ("failed", "parse timed out", None),
                   ("failed", "not condensable: x", None)]


def test_an_expiring_held_upload_is_never_released(rig, monkeypatch):
    worker = rig.start()
    job = server.Job("h1", rig.disk / "jobs" / "h1", "s" * 64, 10, status="held", game_build="release-99")
    job.folder.mkdir(parents=True)
    (job.folder / "upload.vrf").write_bytes(VRF)
    worker.supported_builds = {"release-99"}
    job.created = 0.0                                               # long past HELD_TTL_S
    with worker.lock:
        worker.jobs["h1"] = job
    real_save = worker._save

    def save_then_try_release(saved, strict=False):
        real_save(saved, strict)
        if saved.id == "h1" and saved.status == "expiring":
            assert worker._release_held() is None                   # the claim already excludes it
    monkeypatch.setattr(worker, "_save", save_then_try_release)
    with worker.sweep_lock:
        worker._sweep()
    assert worker.get("h1").status == "failed" and not (job.folder / "upload.vrf").exists()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_workflow.py -v`
Expected: the new tests FAIL (`Settings` has no `parse_parallel`; `Worker` takes no `runs_table`).

- [ ] **Step 3: Settings and `Job`**

`Settings`:

```python
    parse_parallel: int = 15                     # parse runs at once with REPLAY_PARSE_RUNNER=workflow (spec D15)
    parse_run_timeout_s: float = 1200.0          # one parse run: download, parse, condense, post
```

`from_env`:

```python
        settings.parse_parallel = max(1, int(env.get("REPLAY_PARSE_PARALLEL", settings.parse_parallel)))
        settings.parse_run_timeout_s = float(env.get("REPLAY_PARSE_RUN_TIMEOUT_S", settings.parse_run_timeout_s))
```

`Job`: add `run_id: str | None = None`, `run_token: str | None = None`, `run_deadline: float | None = None`.
`_save`: the loop `for key in ("game_build", "detail"):` becomes
`for key in ("game_build", "detail", "run_id", "run_token", "run_deadline"):` (a None value is left out, as today).
`_recover`: pass `run_id=record.get("run_id"), run_token=record.get("run_token"),
run_deadline=record.get("run_deadline")` to `Job(...)`.

Module constant beside the `REASON_*` imports:

```python
DEPLOY_WAIT_S = 60      # before a new parse run after a deploy refusal, within the job's deadline (spec D11)
```

- [ ] **Step 3b: Recovered jobs never meet the bounded queue (spec D15)**

`Worker.__init__`: add `self.recovered: collections.deque[Job] = collections.deque()` (import `collections`)
before `_recover` runs. In `_recover`, the branch for queued and parsing jobs,

```python
            elif job.status in ("queued", "parsing"):
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
```

becomes

```python
            elif job.status in ("queued", "parsing"):
                if (folder / "upload.vrf").exists():
                    # Accepted before the restart: worked first, never through the bounded queue, so a restart
                    # can't fail it for want of room (spec D15). `released` marks a job not taken from a queue.
                    job.status, job.released = "queued", True
                    self.recovered.append(job)
                else:
                    job.status, job.error, job.finished = "failed", "the worker restarted: please re-upload", time.time()
                self._save(job)
```

The `_log` line at the end of `_recover` reports `len(self.recovered)` recovered jobs instead of the two queue
sizes. In `_next`'s archive-on loop, take a recovered job before the queues:

```python
        while True:
            try:
                job = self.recovered.popleft()
                self._parse_started()
                return job
            except IndexError:
                pass
            for source in (self.queue, self.reparse_queue):
                ...                                   # unchanged
```

`deque.popleft` is thread-safe, so the parse threads share it without the lock.

- [ ] **Step 4: Parallel parse threads and the counter**

`Worker.__init__(self, settings, on_parse=None, runs_table=None)`: store `self.runs = runs_table`; replace `self.parsing = False` with

```python
        self.parse_count = 0
        self.sweep_lock = threading.Lock()
```

add

```python
    @property
    def parsing(self) -> bool:
        return self.parse_count > 0

    def _parse_started(self) -> None:
        with self.lock:
            self.parse_count += 1

    def _parse_ended(self) -> None:
        with self.lock:
            self.parse_count -= 1
```

In `_next`, replace each `self.parsing = True` with `self._parse_started()`; wrap the periodic `self._sweep()` call so only one thread sweeps:

```python
            if time.time() - self.last_sweep > self.SWEEP_EVERY_S and self.sweep_lock.acquire(blocking=False):
                try:
                    self._sweep()
                finally:
                    self.sweep_lock.release()
```

In `_run`'s `finally`, replace `self.parsing = False` with `self._parse_ended()`.

`_release_held`: make the pick-and-mark atomic, so two threads can't take one job:

```python
    def _release_held(self) -> Job | None:
        with self.lock:
            ready = sorted((j for j in self.jobs.values()
                            if j.status == "held" and j.game_build in (self.supported_builds or ())),
                           key=lambda j: j.created)
            if not ready:
                return None
            job = ready[0]
            job.status, job.error, job.released = "queued", None, True
        self._save(job)
        _log(f"job {job.id} released: the parser now supports {job.game_build}")
        return job
```

`_sweep`: expiring a held upload claims it under the lock first, so no parse thread can release it meanwhile
(`_release_held` only takes `status == "held"`). Its block

```python
            with self.lock:
                expired = [j for j in self.jobs.values() if j.status == "held" and j.created < time.time() - HELD_TTL_S]
            for job in expired:
                (job.folder / "upload.vrf").unlink(missing_ok=True)
                job.status, job.finished = "failed", time.time()
```

becomes

```python
            with self.lock:
                expired = [j for j in self.jobs.values() if j.status == "held" and j.created < time.time() - HELD_TTL_S]
                for job in expired:
                    job.status = "expiring"                 # claimed: _release_held skips it
            for job in expired:
                self._save(job)
                (job.folder / "upload.vrf").unlink(missing_ok=True)
                job.status, job.finished = "failed", time.time()
```

(the lines after it, setting `job.error`, `_save` and `_log`, are unchanged). `held_bytes` and `archive_status`
count `status == "held"` only, so an expiring job leaves their totals at once.

The thread start at the end of `__init__` becomes:

```python
        threads = self.settings.parse_parallel if (self.runs is not None and
                                                   self.settings.parse_runner == "workflow") else 1
        self.threads = [threading.Thread(target=self._run, name=f"replay-worker-{i}", daemon=True)
                        for i in range(threads)]
        for thread in self.threads:
            thread.start()
        self.thread = self.threads[0]
```

- [ ] **Step 5: `_process_remote`**

Make `_process` start with the branch shown in Task 1, and add:

```python
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
                raise _JobFailed(REASON_TIMEOUT)
            run = self.runs.open("parse", job.id, files, 0, token=job.run_token,
                                 render_id=job.run_id if reconnect else None, deadline=job.run_deadline)
            if not reconnect:
                def started(render_id, job=job):
                    job.run_id = render_id
                    self._save(job, strict=True)
                self.runs.start(run, {"recipe": self.recipe, "sha256": job.sha256}, on_started=started)
            answer = self.runs.wait(run)
            kind = answer.get("error_kind")
            if answer.get("status") == "ok":
                job.parse_seconds = answer.get("parse_seconds")
                return self._accept_result(job, answer["result"])
            if kind == "deploy":
                # This run's image has another recipe: nothing was parsed. Wait, then a fresh run, within the
                # deadline (the loop's first check ends it).
                time.sleep(max(0.0, min(DEPLOY_WAIT_S, job.run_deadline - time.time())))
                job.run_id, reconnect = None, False
                continue
            if kind == "infra" and reconnect:
                # The run we reconnected to ended without an answer: start over, as a restart does today.
                job.run_id, reconnect = None, False
                continue
            if kind == "timeout":
                raise _JobFailed(REASON_TIMEOUT)
            if kind == "parse":
                job.detail, job.parse_seconds = answer.get("detail"), answer.get("parse_seconds")
                raise _JobFailed(answer.get("error") or REASON_PARSE)
            if kind == "contract":
                raise _JobFailed(answer.get("error") or REASON_PARSE)
            raise _JobFailed(REASON_PARSE)
```

(`import secrets` at the top of `server.py`.) In `_run`'s `finally`, just before the final
`job.result, job.error, job.finished = ...` / `self._save(job)` lines, clear the identity so it goes out with the
job's settlement in the same save:

```python
                job.run_id = job.run_token = job.run_deadline = None
```

- [ ] **Step 6: `main()`**

Pass `runs_table=runs_table` to `Worker(...)`.

- [ ] **Step 7: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_workflow.py tests/replays -k "worker or archive or held" -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_parse_workflow.py
git commit -m "replay worker: parses as workflow runs, fifteen at once, reconnecting after a restart"
```

---

### Task 4: Ten uploads per uploader, settings on Render, and the switch-over

**Files:**
- Modify: `webapp/app/services/replay_upload.py`, `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`
- Test: `webapp/tests/replays/test_replay_upload.py`, `webapp/tests/replays/test_replay_upload_held.py` (unchanged; they read the constant)

- [ ] **Step 0: Ten uploads per uploader (spec D15)**

In `replay_upload.py`:

```python
MAX_UNFINISHED = 10  # a batch: one session's uploads in progress at once (spec 2026-10-14, D15); the worker admits 15
```

The upload page (`max_files` in `routers/replays.py` and `templates/replays/upload.html`) and the limit's
message both read this constant. The comment above `QUEUED_STUCK_AFTER` says "a batch of MAX_UNFINISHED at the
cap is several parses": it still holds.

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_upload.py tests/replays/test_replay_upload_held.py -q`
Expected: all pass.

```bash
git add webapp/app/services/replay_upload.py
git commit -m "uploads: ten in progress per uploader"
```

- [ ] **Step 1: `render.yaml`** (on `replay-worker`; `REPLAY_PARSE_RUNNER` stays `local`)

```yaml
      - key: REPLAY_PARSE_PARALLEL
        value: "15"
      # Admits 15 waiting/running jobs (spec D15). The archive's parse reserve grows by 10 x the 200 MB cap
      # (replay_worker/archive.py parse_reserve), about 2 GB less archive.
      - key: REPLAY_QUEUE_SIZE
        value: "15"
```

`REPLAY_QUEUE_SIZE` also takes effect while parsing is still `local`: the worker then queues up to 15 and parses
them one at a time, as today, which the site already tolerates (its `QUEUED_STUCK_AFTER` is 60 minutes).

- [ ] **Step 2: Docs**

`RENDER_DEPLOY.md`, Workflows section, add:

```markdown
### Parsing on Workflows (step 3)

1. Note today's `parse_seconds` for three recent uploads (the job page's report, or `/admin` → replays).
2. `replay-worker` → Environment → `REPLAY_PARSE_RUNNER=workflow` → save.
3. Upload 10 replays at once from one browser session (its batch), and a few more from a second one: the
   workflow's Runs page shows up to 15 `parse` runs running together.
4. Restart test: upload one, and while its run is running, `replay-worker` → Manual Deploy → Restart. The upload
   still finishes, and the Runs page shows one `parse` run for it (the worker reconnected). If the restart landed
   in the instant between starting the run and saving its id, a second run appears and the first result wins:
   that is the accepted rule (spec D9).
5. Compare `parse_seconds` with step 1's. If clearly slower, change the `parse` task's plan to `2c-4g` in
   `replay_tasks/main.py` (spec D7) and redeploy the workflow.
6. Result sizes (spec D18): in `replay-worker`'s logs, the `run result <job> parse <bytes>` lines. Note the
   largest. Step 5 shrinks the worker to 2 GB only if 15 of those at once fit comfortably.
7. Roll back: `REPLAY_PARSE_RUNNER=local`.
```

`replay_worker/README.md`: in "Behaviour", add that with `REPLAY_PARSE_RUNNER=workflow` up to `REPLAY_PARSE_PARALLEL` parses run at once as Workflow runs; the job record carries `run_id`, `run_token` and `run_deadline` while one is going; and jobs recovered after a restart are worked first, outside the queue.

- [ ] **Step 3: Full suite and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -q`
Expected: all pass.

```bash
git add render.yaml replay_worker/README.md webapp/RENDER_DEPLOY.md
git commit -m "parsing on workflows: settings and the switch-over notes"
```

- [ ] **Step 4: On Render (the owner)**

Follow "Parsing on Workflows". Done when several uploads parse together, the restart test leaves one run, and the
`parse_seconds` comparison is noted (D7's decision on the plan size).

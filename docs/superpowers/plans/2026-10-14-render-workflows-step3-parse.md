# Render Workflows, step 3: parsing on Workflows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With `REPLAY_PARSE_RUNNER=workflow`, each upload and re-parse is parsed and condensed in its own Workflow run, up to 15 at once, and a worker restart reconnects to a parse that is still running instead of starting another.

**Architecture:** The parse-and-condense code in `Worker._process` moves to `replay_worker/parse_job.py`, used both by the local path and by the new `parse` task. The worker keeps its queues, job records, held uploads, archive and receipts; in workflow mode it runs `REPLAY_PARSE_PARALLEL` parse threads, and each opens a run with the job's `.vrf` as input, records the run's Render id and token in `job.json` before waiting, and maps the run's answer onto today's job outcomes.

**Tech Stack:** Python 3.13 standard library, pytest, the step-1 run table.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (D1, D7, D8, D9, D11)

**Depends on:** step 1 merged; step 2 need not be.

## Global Constraints

- The `parse` task: plan `flex` (D7), `Retry(max_retries=0)` (D9). The parser keeps its own 240 s timeout (`REPLAY_TIMEOUT_S`) inside the run; the run as a whole (download, parse, condense, post) gets `REPLAY_PARSE_RUN_TIMEOUT_S` = 1200 s, the site's `STUCK_AFTER` (20 min). The task's `timeout_seconds` is 1260.
- `REPLAY_PARSE_PARALLEL` = 15 and `REPLAY_QUEUE_SIZE` = 15 (spec D15), so the worker admits and runs 15 at once. The site's per-uploader `MAX_UNFINISHED` (5) is unchanged. With 15 parses and 40 control rounds wanting to start together, the 40/min start limit spreads them over about a minute and a half.
- Deploy skew (D11): a run whose `current_recipe()` differs from the worker's answers `error_kind: "deploy"`; the worker waits 60 s and starts a new run, at most 10 times.
- The site, the receipts and the archive behave exactly as today; a job's outcomes and user-facing reasons are unchanged (`parse timed out`, `parse failed`, `not condensable: <reason>`, `the archived file is another match`).
- `REPLAY_PARSE_RUNNER` stays `local` in `render.yaml` at merge.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`.

## Review Focus

- A restart while a parse run is going must reconnect to that run (same Render id, same token) and never start a second one; if that run then turns out to have died, the job starts over once, as a restart does today.
- Two parse threads must never take the same held upload when its build becomes supported (`_release_held` race).
- A run's result for a re-parse whose file turns out to be another match must fail the job with today's reason, not store it.
- `Worker.idle()` must read "not idle" while any of the parallel parses runs (local height builds still use it).
- A deploy refusal must neither fail the upload nor loop forever.

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
- Modify: `replay_worker/server.py` (`kill_tree`, `_limit_memory`, `parser_detail`, `REASON_*`, `DETAIL_MAX_CHARS` move out; `Worker._process` calls `parse_file`)
- Test: `webapp/tests/replays/test_replay_worker_parse_job.py`

**Interfaces:**
- Produces: `ParseFailed(reason: str, detail: str | None = None)`; `parse_file(vrf: Path, folder: Path, sha256: str, *, parser_cmd: list[str], timeout_s: float, memory_cap_mb: int, parser_build: Path | None) -> tuple[dict, float]` (result dict in today's shape, parse seconds); raises `ParseFailed` or `ContractError`; deletes `folder / "export"` before returning. `server.py` keeps the names `kill_tree`, `_limit_memory`, `parser_detail`, `REASON_TIMEOUT`, `REASON_PARSE` by importing them from `parse_job`.

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

Move, unchanged, from `server.py` into `parse_job.py`: `DETAIL_MAX_CHARS`, `REASON_TIMEOUT`, `REASON_PARSE`, `parser_detail`, `kill_tree`, `_limit_memory` (rename to `limit_memory`, keep `_limit_memory = limit_memory` as an alias). Then add:

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

# ... the moved names here ...


class ParseFailed(Exception):
    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


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
            raise ParseFailed(REASON_PARSE, parser_detail(log))
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

Behaviour change to check: today the export is deleted after condense only when the archive is on (with it off, the whole job folder is deleted anyway in `_run`'s `finally`), so always deleting it is equivalent.

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
            job.detail = failed.detail
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
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r-old", "sha256": "s"}, link)
    assert link.result["error_kind"] == "deploy"


def test_parse_reports_a_parse_failure_with_detail(monkeypatch):
    monkeypatch.setattr(tasks, "_recipe", lambda: "r")

    def boom(*a, **k):
        raise parse_job.ParseFailed("parse failed", "Boom: bad chunk")
    monkeypatch.setattr(parse_job, "parse_file", boom)
    link = FakeLink({"vrf": b"x"})
    tasks.parse({"job_id": "j", "token": "t", "recipe": "r", "sha256": "s"}, link)
    assert link.result == {"status": "failed", "error_kind": "parse", "error": "parse failed",
                           "detail": "Boom: bad chunk"}


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
            answer = {"status": "failed", "error_kind": "parse", "error": failed.reason, "detail": failed.detail}
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

In `replay_tasks/main.py`:

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
- Produces: `Settings.parse_parallel: int` (15, `REPLAY_PARSE_PARALLEL`), `Settings.parse_run_timeout_s: float` (1200, `REPLAY_PARSE_RUN_TIMEOUT_S`); `Job.run_id: str | None`, `Job.run_token: str | None` (saved in `job.json` as `run_id`, `run_token`); `Worker(settings, on_parse=None, runs_table=None)`; `Worker.parsing` (a read-only property: any parse running); `DEPLOY_RETRIES = 10`, `DEPLOY_WAIT_S = 60`.

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_parse_workflow.py`. These use a stub `parse` in place of the real parser, so they run without the .NET parser:

```python
VRF = server.VRF_MAGIC + b"\x00" * 64


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A worker parsing through a fake Render whose `parse` runs a stub that answers like tasks.parse."""
    render = FakeRender()
    seen, gate = [], threading.Event()
    gate.set()

    def stub(args, link):
        seen.append(args["job_id"])
        gate.wait(30)
        link.post_result({"status": "ok", "parse_seconds": 1.0,
                          "result": {"match_uuid": f"m-{args['job_id']}", "map_name": "Ascent"}})
    archive_dir = tmp_path / "disk"
    archive_dir.mkdir()
    settings = server.Settings(temp_root=tmp_path / "tmp", parse_runner="workflow", parse_parallel=5,
                               workflow_slug="replay-tasks", render_api_key="k", render_api_base=render.url,
                               archive_dir=archive_dir, archive_require_mount=False,
                               archive_total_bytes=500 * 1024 ** 3)
    table = server.make_runs(settings)
    worker = server.Worker(settings, runs_table=table)
    httpd = server.make_server(worker, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.base_for_tests = base
    render.runner = lambda kind, args: stub(args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    yield worker, render, seen, gate, settings, table
    gate.set()
    httpd.shutdown()
    render.close()


def wait_jobs(worker, jobs, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if all(worker.get(j.id).status in ("done", "failed") for j in jobs):
            return [worker.get(j.id) for j in jobs]
        time.sleep(0.05)
    raise AssertionError("jobs didn't finish")


def test_five_uploads_parse_at_once(remote):
    worker, render, seen, gate, _, _ = remote
    gate.clear()
    jobs = [worker.submit(VRF + bytes([i])) for i in range(5)]
    end = time.time() + 10
    while len(seen) < 5 and time.time() < end:
        time.sleep(0.05)
    assert len(seen) == 5 and worker.parsing and not worker.idle()
    gate.set()
    done = wait_jobs(worker, jobs)
    assert all(j.status == "done" and j.parse_seconds == 1.0 for j in done)
    assert {s["task"] for s in render.started} == {"replay-tasks/parse"}


def test_the_run_id_is_saved_before_the_result(remote):
    worker, render, seen, gate, _, _ = remote
    gate.clear()
    job = worker.submit(VRF + b"a")
    end = time.time() + 10
    while not seen and time.time() < end:
        time.sleep(0.05)
    record = json.loads((job.folder / "job.json").read_text(encoding="utf-8"))
    assert record["status"] == "parsing" and record["run_id"] == "trn-1" and record["run_token"]
    gate.set()
    wait_jobs(worker, [job])


def test_a_deploy_refusal_waits_and_starts_a_new_run(remote, monkeypatch):
    worker, render, _, _, _, _ = remote
    monkeypatch.setattr(server, "DEPLOY_WAIT_S", 0.1)
    answers = iter([{"status": "failed", "error_kind": "deploy", "error": "recipe"}])

    def flaky(kind, args):
        link = tasks.WorkerLink(render.base_for_tests, args["job_id"], args["token"])
        link.post_result(next(answers, {"status": "ok", "parse_seconds": 2.0,
                                        "result": {"match_uuid": "m", "map_name": "Ascent"}}))
    render.runner = flaky
    [done] = wait_jobs(worker, [worker.submit(VRF + b"b")])
    assert done.status == "done" and len(render.started) == 2


def test_failures_keep_todays_reasons(remote):
    worker, render, _, _, _, _ = remote
    answers = {"parse": {"status": "failed", "error_kind": "parse", "error": "parse failed", "detail": "Boom"},
               "timeout": {"status": "failed", "error_kind": "parse", "error": "parse timed out", "detail": None},
               "contract": {"status": "failed", "error_kind": "contract", "error": "not condensable: x"}}
    order = list(answers)

    def answer(kind, args):
        tasks.WorkerLink(render.base_for_tests, args["job_id"], args["token"]).post_result(answers[order.pop(0)])
    render.runner = answer
    jobs = []
    for i in range(3):
        jobs.append(worker.submit(VRF + bytes([10 + i])))
        wait_jobs(worker, jobs[-1:])
    got = [(j.status, j.error, j.detail) for j in (worker.get(j.id) for j in jobs)]
    assert got == [("failed", "parse failed", "Boom"), ("failed", "parse timed out", None),
                   ("failed", "not condensable: x", None)]


def test_a_restart_reconnects_to_a_running_parse(remote, tmp_path):
    worker, render, seen, gate, settings, table = remote
    gate.clear()
    job = worker.submit(VRF + b"c")
    end = time.time() + 10
    while not seen and time.time() < end:
        time.sleep(0.05)
    # A second worker on the same disk stands for the restarted one; the first stops caring.
    table2 = server.make_runs(settings)
    worker2 = server.Worker(settings, runs_table=table2)
    httpd2 = server.make_server(worker2, runs_table=table2)
    threading.Thread(target=httpd2.serve_forever, daemon=True).start()
    base2 = f"http://127.0.0.1:{httpd2.server_address[1]}"
    time.sleep(0.5)
    assert len(render.started) == 1                      # no second run
    rec = json.loads((job.folder / "job.json").read_text(encoding="utf-8"))
    # The running run posts to the restarted worker with its original token:
    tasks.WorkerLink(base2, job.id, rec["run_token"]).post_result(
        {"status": "ok", "parse_seconds": 3.0, "result": {"match_uuid": "m-c", "map_name": "Ascent"}})
    [done] = wait_jobs(worker2, [job])
    assert done.status == "done" and done.parse_seconds == 3.0
    httpd2.shutdown()
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

`Job`: `run_id: str | None = None` and `run_token: str | None = None`. `_save`: add `"run_id"` and `"run_token"` to the loop `for key in ("game_build", "detail"):` → `for key in ("game_build", "detail", "run_id", "run_token"):`. `_recover`: pass `run_id=record.get("run_id"), run_token=record.get("run_token")` to `Job(...)`. A recovered `parsing` job is queued again exactly as today; `_process_remote` sees its `run_id` and reconnects.

Module constants beside `REASON_*` imports:

```python
DEPLOY_RETRIES = 10     # parse runs refused for another recipe, before the job fails (spec D11)
DEPLOY_WAIT_S = 60
```

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
        """The parse as a Workflow run. The run's Render id and token go into job.json before this waits, so a
        restart reconnects to the run instead of starting another (spec D9); a reconnected run that turns out
        to have died starts over once, as a restart does today."""
        files = {"vrf": job.folder / "upload.vrf"}
        reconnected = False
        for attempt in range(DEPLOY_RETRIES + 1):
            if job.run_id and job.run_token and not reconnected:
                reconnected = True
                run = self.runs.open("parse", job.id, files, self.settings.parse_run_timeout_s,
                                     token=job.run_token, render_id=job.run_id)
            else:
                run = self.runs.open("parse", job.id, files, self.settings.parse_run_timeout_s)
                job.run_token = run.token

                def started(render_id, job=job):
                    job.run_id = render_id
                    self._save(job, strict=True)
                self.runs.start(run, {"recipe": self.recipe, "sha256": job.sha256}, on_started=started)
            answer = self.runs.wait(run)
            job.run_id = job.run_token = None
            self._save(job)
            kind = answer.get("error_kind")
            if answer.get("status") == "ok":
                job.parse_seconds = answer.get("parse_seconds")
                return self._accept_result(job, answer["result"])
            if kind == "deploy" and attempt < DEPLOY_RETRIES:
                time.sleep(DEPLOY_WAIT_S)
                continue
            if kind == "infra" and reconnected and attempt == 0:
                continue                                  # the run we reconnected to had died: start over once
            if kind == "timeout":
                raise _JobFailed(REASON_TIMEOUT)
            if kind == "parse":
                job.detail = answer.get("detail")
                raise _JobFailed(answer.get("error") or REASON_PARSE)
            if kind == "contract":
                raise _JobFailed(answer.get("error") or REASON_PARSE)
            raise _JobFailed(REASON_PARSE)
        raise _JobFailed(REASON_PARSE)
```

- [ ] **Step 6: `main()`**

Pass `runs_table=runs_table` to `Worker(...)`.

- [ ] **Step 7: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_parse_workflow.py tests/replays -k "worker or archive or held" -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_parse_workflow.py
git commit -m "replay worker: parses as workflow runs, five at once, reconnecting after a restart"
```

---

### Task 4: Settings on Render and the switch-over

**Files:**
- Modify: `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`

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
3. Upload several replays at once (up to 15, from more than one browser session: one session's batch is 5): the
   workflow's Runs page shows that many `parse` runs running together.
4. Restart test: upload one, and while its run is running, `replay-worker` → Manual Deploy → Restart. The upload
   still finishes, and the Runs page shows one `parse` run for it, not two.
5. Compare `parse_seconds` with step 1's. If clearly slower, change the `parse` task's plan to `2c-4g` in
   `replay_tasks/main.py` (spec D7) and redeploy the workflow.
6. Roll back: `REPLAY_PARSE_RUNNER=local`.
```

`replay_worker/README.md`: in "Behaviour", add that with `REPLAY_PARSE_RUNNER=workflow` up to `REPLAY_PARSE_PARALLEL` parses run at once as Workflow runs, and the job record carries `run_id`/`run_token` while one is going.

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

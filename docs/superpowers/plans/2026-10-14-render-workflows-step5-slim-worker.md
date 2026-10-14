# Render Workflows, step 5: slim the worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `replay-worker` becomes a file and job service only. Parsing, map control and height builds always run as Workflow runs. The .NET parser leaves its image, uploads stream to disk, and it moves to a 1 CPU / 2 GB plan.

**Architecture:** Two PRs, because the workflow service builds from `replay_worker/Dockerfile` today. **5a** adds `replay_tasks/Dockerfile` (the full image), and the owner points the workflow at it. **5b** removes the `local` paths and the code that only served them (child processes, preemption, idle, the local pool), moves the worker's tests onto a shared fake-Render harness, streams `POST /jobs` to disk, slims the worker's image and changes its plan.

**Tech Stack:** Python 3.13 standard library, pytest, Docker, `render.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (step 5, D14, "Load")

**Depends on:** steps 2-4 running with `workflow` on Render with no rollback needed, and Render's metrics for the worker read during steps 2 and 3.

## Global Constraints

- Feature verification (`verify_feature_upload`, a short synchronous child) stays on the worker, so the worker image keeps the control venv (numpy, scipy, Pillow) and `app/control` (D14).
- The worker image keeps `webapp/replay_parser.json`, the patches and `webapp/app/static/data`: `/health`'s `recipe` and the held-upload check (`supported_builds`) read them.
- The site sees no change: `/health` keeps every key it reads (`idle`, `recipe`, `control.enabled`, `control.gaps_protocol`, `control.features`, `archive.*`). `idle` is always `true`, and `control.preempted` is always `0`.
- The worker's new plan is `standard` (1 CPU / 2 GB) in `render.yaml`.
- Every upload, input and result streams through in 1 MB chunks.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`.

## Review Focus

- A 200 MB upload must never be held whole in memory: `POST /jobs` streams to disk, hashing as it goes.
- An upload whose first 4 bytes aren't the replay magic, or that ends short of its `Content-Length`, must leave no file behind.
- A worker started without `RENDER_API_KEY` or `REPLAY_WORKFLOW_SLUG` must stop at startup with a clear message. Nothing can run without them now.
- The workflow must keep building after 5b merges: it must already point at `replay_tasks/Dockerfile` (5a), or it would build the slim image.
- Feature verification must still work on the slim image (its child uses the control venv's interpreter).

---

## PR 5a

### Task 1: The workflow gets its own Dockerfile

**Files:**
- Create: `replay_tasks/Dockerfile`
- Modify: `webapp/RENDER_DEPLOY.md`

- [ ] **Step 1: Copy the image**

Copy `replay_worker/Dockerfile` to `replay_tasks/Dockerfile` unchanged except:
- the header comment: `# The Render Workflow's image (replay_tasks): the pinned parser, the control venv, the task bodies.`
- the last two lines become (the entrypoint drops root as before; the archive variable is unset there, so it skips the chown):

```dockerfile
ENTRYPOINT ["sh", "/srv/replay_worker/entrypoint.sh"]
CMD ["/opt/control-venv/bin/python", "-m", "replay_tasks.main"]
```

- [ ] **Step 2: Docs**

In `RENDER_DEPLOY.md`'s Workflows section, change the setup step's Dockerfile path to `replay_tasks/Dockerfile`, and add:

```markdown
### Before step 5b: point the workflow at its own Dockerfile

`replay-tasks` → Settings → Dockerfile path: `replay_tasks/Dockerfile` → save; wait for the build; run the ping
(`replay-worker` → Shell → `python3 -m replay_worker.runs ping`). Only then merge 5b, which slims
`replay_worker/Dockerfile`.
```

- [ ] **Step 3: Commit and open PR 5a**

```bash
git add replay_tasks/Dockerfile webapp/RENDER_DEPLOY.md
git commit -m "replay tasks: the workflow's own Dockerfile"
```

The owner merges it, changes the Dockerfile path, and confirms the ping.

---

## PR 5b

### Task 2: A shared fake-Render harness for the worker's tests

**Files:**
- Create: `webapp/tests/replays/workflow_harness.py`
- Test: itself, through the converted files in Task 3

**Interfaces:**
- Consumes: `FakeRender` (step 1), `replay_tasks.tasks.TASKS`, `server.make_runs`, `make_server`.
- Produces: `Harness(tmp_path, **settings_overrides)` with `.settings`, `.render`, `.runs`, `.worker`, `.control`, `.heights`, `.base`, `.close()`. Every run goes through the real task body, and the task's child commands come from the environment as on Render (`REPLAY_PARSER_CMD`, `REPLAY_CONTROL_CMD`, `REPLAY_HEIGHT_CMD`), which the converted tests set to their existing stubs.

- [ ] **Step 1: Write the harness**

```python
# webapp/tests/replays/workflow_harness.py
"""The replay worker as it runs from step 5 on: every parse, control round and height build is a Workflow run.
A fake Render runs the real task bodies (replay_tasks/tasks.py) in threads against this worker's own server."""

import json
import os
import threading

from fake_render import FakeRender
from replay_tasks import tasks
from replay_worker import server

COMMANDS = {"parser_cmd": "REPLAY_PARSER_CMD", "control_cmd": "REPLAY_CONTROL_CMD", "height_cmd": "REPLAY_HEIGHT_CMD"}


class Harness:
    def __init__(self, tmp_path, control=True, heights=True, **overrides):
        self.render = FakeRender()
        self._env = {}
        for name, var in COMMANDS.items():          # the tests' stub commands reach the task bodies
            if name in overrides:
                self._env[var] = os.environ.get(var)
                os.environ[var] = json.dumps(overrides.pop(name))
        self._env.setdefault("REPLAY_CONTROL_MEMORY_MB", os.environ.get("REPLAY_CONTROL_MEMORY_MB"))
        os.environ["REPLAY_CONTROL_MEMORY_MB"] = "0"
        overrides.setdefault("temp_root", tmp_path / "tmp")
        overrides.setdefault("control_cache_dir", tmp_path / "cache")
        self.settings = server.Settings(workflow_slug="replay-tasks", render_api_key="k",
                                        render_api_base=self.render.url, **overrides)
        self.runs = server.make_runs(self.settings)
        self.control = server.ControlRunner(self.settings, runs_table=self.runs) if control else None
        self.heights = server.HeightBuilds(self.settings, self.control) if (control and heights) else None
        self.worker = server.Worker(self.settings, runs_table=self.runs)
        self.httpd = server.make_server(self.worker, control=self.control, heights=self.heights,
                                        runs_table=self.runs)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.render.runner = lambda kind, args: tasks.TASKS[kind](
            args, tasks.WorkerLink(self.base, args["job_id"], args["token"]))

    def close(self):
        self.httpd.shutdown()
        self.render.close()
        for var, value in self._env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
```

`parse_file` reads `REPLAY_TIMEOUT_S` and `REPLAY_MEMORY_CAP_MB` through `Settings.from_env()` in the task. A test that sets `timeout_s=` on the harness must also set `REPLAY_TIMEOUT_S` with `monkeypatch.setenv`. Task 3 notes where.

- [ ] **Step 2: Commit**

```bash
git add webapp/tests/replays/workflow_harness.py
git commit -m "tests: a fake-Render harness for the worker's workflow-only shape"
```

---

### Task 3: Convert the worker's tests

**Files:**
- Modify: `test_replay_worker.py`, `test_replay_worker_archive.py`, `test_replay_worker_held.py`, `test_replay_worker_control.py`, `test_replay_worker_heights.py`, `test_replay_worker_control_workflow.py`, `test_replay_worker_parse_workflow.py`, `test_replay_worker_heights_workflow.py` (all in `webapp/tests/replays/`)

There are 94 tests and fixtures across the first five files. The conversion is mechanical: each file's fixture that builds `server.Settings(...)`, `server.Worker(...)`, `server.ControlRunner(...)` and `make_server(...)` builds a `Harness` instead, passes the same overrides, and closes it at teardown. The test bodies don't change, except for the deletions below.

- [ ] **Step 1: Delete the tests of removed behaviour**

Find them with `rg -n "preempt|idle|control_workers|kill_tree|on_parse|warming.*parse|runner=\"local\"|_runner" tests/replays/test_replay_worker*.py` and delete each test whose subject is:
- a parse preempting control children (`preempt`, `preempted`, `on_parse`)
- the pool shrinking to one child beside a parse, or `control_workers` as the pool size (the pool is now `control_runs`)
- a height build waiting for idle, or running alone (a remote build does neither, as step 4 tests)
- the `local`/`workflow` switch itself (steps 2-4's "rollback" tests)
- the parser's address-space cap reported by `/health` (`memory_cap`), which moves to the task

Keep every test of queues, dedupe, held uploads, the archive, acks, receipts, re-parse attempts, height-build states, tokens and warming (a map's first round still runs alone).

- [ ] **Step 2: Swap the fixtures**

For each file, replace the server/worker construction with the harness. Example for `test_replay_worker_control.py`'s `runner` fixture:

```python
@pytest.fixture
def runner(tmp_path):
    log = tmp_path / "events"
    log.mkdir()
    stub = tmp_path / "stub_control.py"
    stub.write_text(STUB.format(log=str(log)), encoding="utf-8")
    made = []

    def make(**overrides):
        h = Harness(tmp_path / f"h{len(made)}", control_cmd=[sys.executable, str(stub)], **overrides)
        made.append(h)
        return h.control, log
    yield make
    for h in made:
        h.close()
```

Its stub writes events from the control child, which now runs inside the task body on the test machine, so `overlaps(log)` still measures concurrency. Pool-size assertions use `control_runs=` instead of `control_workers=`.

Parse tests (`test_replay_worker.py`, `_archive.py`, `_held.py`): pass the stub parser as `parser_cmd=[...]` to `Harness`. Where a test sets `timeout_s=`, add `monkeypatch.setenv("REPLAY_TIMEOUT_S", "<same value>")`.

The three `*_workflow.py` files from steps 2-4: their fixtures become `Harness(...)`, and their `control_runner=`/`parse_runner=`/`heights_runner=` overrides go.

- [ ] **Step 3: Run them against the current code**

Until Task 4 the runners still default to `local`, so for now `Harness` passes `parse_runner="workflow", control_runner="workflow", heights_runner="workflow"` in its `Settings(...)` (Task 4 removes the three).

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -k "worker" -q`
Expected: all pass. A failure here means the conversion is wrong; fix it before Task 4.

- [ ] **Step 4: Commit**

```bash
git add webapp/tests/replays
git commit -m "tests: the worker's tests run through workflow runs"
```

---

### Task 4: Remove the local paths

**Files:**
- Modify: `replay_worker/server.py`, `replay_worker/runs.py` (no change expected), `replay_tasks/tasks.py` (no change expected)
- Test: the converted suite

**Interfaces:**
- Removes: `Settings.parse_runner`, `.control_runner`, `.heights_runner`, `.control_workers`, `.height_cmd`, `.parser_build` use in the worker (the task reads it), `.memory_cap_mb` use in the worker; `Settings.workflow_wanted`; `Worker.on_parse`, `_parse_coming`, `idle`; `ControlRunner.idle`, `preempt`, `procs`, `preempted`, `preempted_total`, `_before_settle`'s child handling; the `Popen` branch of `ControlRunner._run`; the local branch of `Worker._process`; `kill_tree`/`limit_memory` imports in `server.py`.
- Keeps: `Settings.control_cmd` (its interpreter runs feature verification, D14), `parser_cmd`/`timeout_s`/`memory_cap_mb` in `Settings.from_env` (the task reads them through it), `ControlRunner.remote` (always true now), warming.
- Changes: `make_runs(settings)` raises `SystemExit` whenever the key or slug is missing.

- [ ] **Step 1: `make_runs` always required**

```python
def make_runs(settings: Settings):
    """The run table. Every parse, control round and height build is a Workflow run (spec step 5), so the
    worker can't start without Render's key and the workflow's slug."""
    if not (settings.render_api_key and settings.workflow_slug):
        raise SystemExit("RENDER_API_KEY and REPLAY_WORKFLOW_SLUG must be set: the worker runs all work on Workflows")
    api = runs_mod.RenderApi(settings.render_api_key, base=settings.render_api_base)
    return runs_mod.Runs(api, settings.workflow_slug, runs_mod.Pacer(settings.task_starts_per_min),
                         settings.temp_root / "runs")
```

- [ ] **Step 2: `ControlRunner`**

- `remote(job)` returns `True` (keep the method, so the step 2-4 call sites read the same).
- Delete `preempt`, `self.procs`, `self.preempted`, `self.preempted_total` and the `idle` parameter and attribute.
- `_next`: drop the idle and "a build runs alone" logic. A pending `heights` job starts at once (as step 4's remote branch does). Control rounds are limited by `control_runs`, and the warming rules stay:

```python
    def _next(self) -> tuple[ControlJob, bool] | None:
        for job_id in self.pending:
            if self.jobs[job_id].kind == "heights":
                return self.jobs[job_id], False
        running = [j for j in self.running if self.jobs[j].kind == "control"]
        if len(running) >= self.settings.control_runs:
            return None
        busy_maps = {self.jobs[j].map for j in running}
        warming_maps = {self.jobs[j].map for j, warming in self.running.items() if warming}
        for job_id in self.pending:
            job = self.jobs[job_id]
            if job.warm_key in self.warm:
                if job.map in warming_maps:
                    continue
                return job, False
            if job.map not in busy_maps:
                return job, True
        return None
```

- `_run`: the body's `try` becomes `answer = self._run_remote(job, timeout)` followed by today's answer handling. Delete the `kwargs`/`env` set-up, the `Popen` branch, the `finally` that kills and closes the child, and the preempted branch of the settlement (keep the `else` branch's body as the only path).
- `counts()` returns `"preempted": 0`.

- [ ] **Step 3: `Worker`**

- `__init__(self, settings, runs_table)`: `runs_table` is required, and there's no `on_parse`. Always start `parse_parallel` threads.
- Delete `idle`, `_parse_coming` and its call at the top of `_run`.
- `_process(job)` is `return self._process_remote(job)`. Delete the local `parse_file` call (keep `_accept_result`).
- `/health`: `body["idle"] = True`. Drop `limits.memory_cap` (the task has the cap now). Keep `limits.timeout_s`, `max_bytes`, `queue_size`.

- [ ] **Step 4: Settings and `main()`**

Remove the three `*_RUNNER` variables, `REPLAY_CONTROL_WORKERS` and `REPLAY_HEIGHT_CMD` from `Settings`, `from_env` and the docstring. `main()`:

```python
def main() -> None:
    settings = Settings.from_env()
    runs_table = make_runs(settings)
    threading.Thread(target=runs_table.reconcile_forever, args=(threading.Event(),), name="runs-reconcile",
                     daemon=True).start()
    control = ControlRunner(settings, runs_table=runs_table) if settings.control_enabled else None
    worker = Worker(settings, runs_table=runs_table)
    if worker.archive is not None and settings.control_cache_dir.resolve().is_relative_to(
            worker.archive.root.resolve()):
        worker.archive.cache_reserve = control_cache.RESERVE
    heights = HeightBuilds(settings, control) if control is not None else None
    server = make_server(worker, os.environ.get("REPLAY_WORKER_HOST", "0.0.0.0"),
                         int(os.environ.get("REPLAY_WORKER_PORT", "8080")), control, heights, runs_table)
    print(f"replay worker on {server.server_address}: work runs on Workflows ({settings.workflow_slug}), "
          f"{settings.parse_parallel} parses and {settings.control_runs} control runs at once", flush=True)
    server.serve_forever()
```

Remove the three overrides added to `Harness` in Task 3, Step 3.

- [ ] **Step 5: Run the suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -q`
Expected: all pass. `rg -n "Popen|preempt|idle\(" replay_worker/server.py` finds only `verify_feature_upload`'s `subprocess.run`.

- [ ] **Step 6: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/workflow_harness.py
git commit -m "replay worker: all work runs on Workflows; the local child paths go"
```

---

### Task 5: Uploads stream to disk

**Files:**
- Modify: `replay_worker/server.py` (`Worker.submit`; the `/jobs` branch of `do_POST`)
- Test: `webapp/tests/replays/test_replay_worker.py`

**Interfaces:**
- Produces: `Worker.intake_dir() -> Path` (on the archive disk when the archive is on, else `temp_root / "incoming"`); `Worker.submit_path(path: Path, sha256: str, size: int) -> Job` (moves `path` into the new job folder); `Worker.submit(body: bytes) -> Job` (kept for tests: writes `body` to `intake_dir()` and calls `submit_path`).

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker.py` (use the file's harness-based fixture from Task 3; call it `h` here):

```python
import hashlib
import http.client


def post_raw(base, body_parts, length):
    host, port = base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    conn.putrequest("POST", "/jobs")
    conn.putheader("Content-Length", str(length))
    conn.endheaders()
    for part in body_parts:
        conn.send(part)
    response = conn.getresponse()
    return response.status, response.read()


def test_upload_streams_and_hashes(h):
    body = server.VRF_MAGIC + os.urandom(3 * 1024 * 1024)
    status, answer = post_raw(h.base, [body[i:i + 65536] for i in range(0, len(body), 65536)], len(body))
    assert status == 202
    job = h.worker.get(json.loads(answer)["id"])
    assert job.sha256 == hashlib.sha256(body).hexdigest() and job.size == len(body)


def test_a_bad_magic_leaves_nothing_behind(h):
    status, _ = post_raw(h.base, [b"NOPE" + b"x" * 100], 104)
    assert status == 400
    assert not any(h.worker.intake_dir().iterdir())


def test_a_short_upload_leaves_nothing_behind(h):
    body = server.VRF_MAGIC + b"x" * 100
    try:
        post_raw(h.base, [body], len(body) + 50)            # claims more than it sends, then hangs up
    except (ConnectionError, http.client.HTTPException, OSError):
        pass
    time.sleep(0.5)
    assert not any(h.worker.intake_dir().iterdir())
```

For the short upload, the handler's read returns short when the client closes. Set the request socket timeout to 30 s (`self.connection.settimeout(30)`) so a stalled client can't hold a thread forever.

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker.py -v -k "streams or bad_magic or short_upload"`
Expected: FAIL (`intake_dir` missing).

- [ ] **Step 3: Implement**

`Worker`:

```python
    def intake_dir(self) -> Path:
        """Where an upload streams before it becomes a job: the same disk as the job folders, so the move is a rename."""
        folder = (self.archive.root / "incoming") if self.archive is not None else (self.settings.temp_root / "incoming")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def submit(self, body: bytes) -> Job:
        path = self.intake_dir() / f"{uuid.uuid4().hex}.vrf"
        path.write_bytes(body)
        return self.submit_path(path, hashlib.sha256(body).hexdigest(), len(body))

    def submit_path(self, path: Path, sha256: str, size: int) -> Job:
        """Today's submit, with the upload already on disk at `path` (moved into the job folder, or deleted)."""
        job_id = uuid.uuid4().hex
        try:
            with self.admission:
                if self.archive is None:
                    folder = self.settings.temp_root / f"replay-job-{job_id}"
                else:
                    if not self.archive.intake_ok(size):
                        raise NoSpace()
                    folder = self.archive.jobs / job_id
                folder.mkdir(parents=True)
                os.replace(path, folder / "upload.vrf")
                job = Job(job_id, folder, sha256, size)
                # ... the rest of today's submit body from `job.game_build = ...` unchanged ...
        finally:
            path.unlink(missing_ok=True)
```

(Move today's `submit` body into `submit_path`, replacing `(folder / "upload.vrf").write_bytes(body)` and `hashlib.sha256(body).hexdigest(), len(body)` as shown. The `finally` removes the intake file if anything failed before the rename.) Also, on startup, empty `intake_dir()`: a restart mid-upload leaves a part file. Do this at the end of `Worker.__init__` with `shutil.rmtree(self.intake_dir(), ignore_errors=True)`, then let the next `intake_dir()` call recreate the folder.

The `/jobs` branch of `do_POST`:

```python
            size = int(length)
            if size > worker.settings.max_bytes:
                self.close_connection = True
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "file too large"})
            self.connection.settimeout(30)
            part = worker.intake_dir() / f"{uuid.uuid4().hex}.vrf"
            digest, remaining, first = hashlib.sha256(), size, True
            try:
                with part.open("wb") as sink:
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
                part.unlink(missing_ok=True)
                self.close_connection = True
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "not a Valorant replay"})
            try:
                job = worker.submit_path(part, digest.hexdigest(), size)
            except (queue.Full, NoSpace):
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the worker is busy, try again soon"})
            return self._send(HTTPStatus.ACCEPTED, {"id": job.id, "status": job.status})
```

A first chunk shorter than 4 bytes is read as not a replay. `rfile.read(n)` blocks until it has `n` bytes or EOF, so the first chunk is a full megabyte, or the whole body when the body is smaller.

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -k "worker" -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker.py
git commit -m "replay worker: uploads stream to disk"
```

---

### Task 6: The slim image and the smaller plan

**Files:**
- Modify: `replay_worker/Dockerfile`, `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`

- [ ] **Step 1: The worker image**

Rewrite `replay_worker/Dockerfile` without the parser stages:

```dockerfile
# The replay worker: a file and job service (docs/superpowers/specs/2026-10-14-render-workflows-design.md, step 5).
# Parsing, map control and height builds run on the replay-tasks Workflow (replay_tasks/Dockerfile). The control
# venv stays for feature verification (verify_feature_upload, spec D14).
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends util-linux && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /srv worker
RUN python3 -m venv /opt/control-venv \
 && /opt/control-venv/bin/pip install --no-cache-dir --only-binary=:all: numpy==2.4.6 scipy==1.18.1 Pillow==10.4.0
COPY webapp/app/__init__.py /srv/webapp/app/__init__.py
COPY webapp/app/replays /srv/webapp/app/replays
COPY webapp/app/control /srv/webapp/app/control
COPY webapp/app/gaps /srv/webapp/app/gaps
COPY webapp/app/static/data /srv/webapp/app/static/data
COPY webapp/replay_parser.json /srv/webapp/replay_parser.json
COPY webapp/scripts/ascent_map_messages.patch /srv/webapp/scripts/ascent_map_messages.patch
COPY webapp/scripts/map_pool_messages.patch /srv/webapp/scripts/map_pool_messages.patch
COPY replay_worker /srv/replay_worker
RUN PYTHONPATH=/srv/webapp:/srv /opt/control-venv/bin/python -c "import app.control.feature_job, app.replays.map_feature_artifacts; from app.replays.contract import load_pin, current_recipe; load_pin(); current_recipe()"
RUN PYTHONPATH=/srv/webapp:/srv python3 -c "import replay_worker.server"
RUN mkdir -p /jobs && chown -R worker /jobs && command -v setpriv
WORKDIR /srv
ENV REPLAY_WORKER_TMP=/jobs \
    REPLAY_CONTROL_CMD='["/opt/control-venv/bin/python", "-m", "replay_worker.control_job"]' \
    CONTROL_CACHE_DIR=/var/replay/control_cache \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["sh", "/srv/replay_worker/entrypoint.sh"]
CMD ["python3", "-m", "replay_worker.server"]
```

Copy the remaining `ENV` lines of today's worker Dockerfile that don't name the parser (`REPLAY_MAX_BYTES`, `REPLAY_QUEUE_SIZE` and any other the current file sets). Leave out `REPLAY_PARSER_CMD`, `REPLAY_PARSER_BUILD`, `REPLAY_MEMORY_CAP_MB` and `REPLAY_HEIGHT_CMD`: those now live in `replay_tasks/Dockerfile`, which already sets them.

- [ ] **Step 2: `render.yaml`**

On `replay-worker`: `plan: standard` (1 CPU / 2 GB), with the comment `# A file and job service since step 5 (spec 2026-10-14); work runs on the replay-tasks Workflow.` Remove the `REPLAY_PARSE_RUNNER`, `REPLAY_CONTROL_RUNNER` and `REPLAY_HEIGHTS_RUNNER` entries. Update the plan comment on the web service that names the worker's `2c-4g`.

- [ ] **Step 3: Docs**

`replay_worker/README.md`: rewrite the opening ("parses one uploaded `.vrf` at a time") to say what it is now: it takes uploads, keeps the archive and the job records, and hands parsing, control and height builds to the `replay-tasks` Workflow. Remove the sections on preemption, the local pool and idle. `RENDER_DEPLOY.md`: a short "Step 5" note saying the worker needs `RENDER_API_KEY` and `REPLAY_WORKFLOW_SLUG` to start at all.

- [ ] **Step 4: Full suite and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass.

```bash
git add replay_worker/Dockerfile render.yaml replay_worker/README.md webapp/RENDER_DEPLOY.md
git commit -m "replay worker: slim image (no parser), standard plan"
```

- [ ] **Step 5: On Render (the owner)**

Before merging 5b, check that `replay-tasks` builds from `replay_tasks/Dockerfile` (5a). After merging, the worker redeploys onto `standard`. Check:
- `/health` reads `recipe` and the archive as before.
- One upload parses.
- A new match's control runs.
- The worker's memory graph during five uploads stays under 2 GB.

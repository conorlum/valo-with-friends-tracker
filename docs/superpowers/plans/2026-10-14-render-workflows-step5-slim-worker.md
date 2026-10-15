# Render Workflows, step 5: slim the worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With all three kinds running on Workflows, the .NET parser leaves the worker's image, uploads stream to disk instead of being read whole, and the worker moves to a 1 CPU / 2 GB plan. The local code paths stay as the test/development mode and the rollback for control and heights (spec D14).

**Architecture:** Two PRs. The workflow service builds from `replay_worker/Dockerfile` until now, so **5a** gives it its own `replay_tasks/Dockerfile` (the full image) and the owner points the workflow at it. **5b** removes the parser stages from the worker's Dockerfile, refuses to start with a local parse runner and no parser, streams `POST /jobs` into the job's folder, and changes the plan in `render.yaml`.

**Tech Stack:** Python 3.13 standard library, pytest, Docker, `render.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (step 5, D14, D18)

**Depends on:** steps 2-4 merged and running with `workflow` on Render; step 3's result sizes recorded (D18); Render's memory graph for the worker read during steps 2 and 3.

## Global Constraints

- No local code path is removed (D14): `REPLAY_CONTROL_RUNNER=local` and `REPLAY_HEIGHTS_RUNNER=local` still work on the slim image (it keeps the control venv); `REPLAY_PARSE_RUNNER=local` is refused at startup on an image without the parser.
- The worker image keeps `webapp/replay_parser.json`, the patches and `webapp/app/static/data`: `/health`'s `recipe` and the held-upload check (`supported_builds`) read them.
- The site sees no change.
- `POST /jobs`: `Content-Length` must be an integer in `[4, REPLAY_MAX_BYTES]`; the body streams in 1 MB chunks into the job's own folder, hashed as it goes; any failure leaves no file and no job folder behind.
- The worker's plan becomes `standard` (1 CPU / 2 GB) only after the memory check in Task 4.
- 5a must be live (the workflow building from `replay_tasks/Dockerfile`, ping OK) before 5b merges.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`; the existing worker tests stay unchanged except where Task 2 says.

## Review Focus

- A 200 MB upload must never be held whole in memory: the route must not call `Worker.submit(bytes)`.
- An upload with bad magic, a short body (client hangs up early) or a bad `Content-Length` must leave no file and no job folder.
- The workflow must keep building after 5b: it must already point at `replay_tasks/Dockerfile`.
- Starting the slim worker with `REPLAY_PARSE_RUNNER` unset (local) must stop it with a clear message rather than fail every upload.
- Feature verification and local control must still work on the slim image (the control venv stays).

---

## PR 5a

### Task 1: The workflow gets its own Dockerfile

**Files:**
- Create: `replay_tasks/Dockerfile`
- Modify: `webapp/RENDER_DEPLOY.md`

- [ ] **Step 1: Copy the image**

Copy `replay_worker/Dockerfile` (as step 1 left it: with `render==1.0.1` and `COPY replay_tasks`) to
`replay_tasks/Dockerfile`, then change only:
- the first comment line to `# The Render Workflow's image (replay_tasks): the pinned parser, the control venv, the task bodies.`
- the last line to `CMD ["/opt/control-venv/bin/python", "-m", "replay_tasks.main"]` (the `ENTRYPOINT` stays: without
  `REPLAY_ARCHIVE_DIR` it skips the chown and just drops to the `worker` user).

- [ ] **Step 2: Docs**

In `RENDER_DEPLOY.md`'s Workflows section, change the setup step's Dockerfile path to `replay_tasks/Dockerfile`,
and add:

```markdown
### Before step 5b: point the workflow at its own Dockerfile

`replay-tasks` → Settings → Dockerfile path: `replay_tasks/Dockerfile` → save; wait for the build; run the ping
(`replay-worker` → Shell → `python3 -m replay_worker.runs ping`). Only then merge 5b, which takes the parser out
of `replay_worker/Dockerfile`.
```

- [ ] **Step 3: Commit, open PR 5a**

```bash
git add replay_tasks/Dockerfile webapp/RENDER_DEPLOY.md
git commit -m "replay tasks: the workflow's own Dockerfile"
```

The owner merges it, changes the Dockerfile path, and confirms the ping.

---

## PR 5b

### Task 2: No local parse without a parser

**Files:**
- Modify: `replay_worker/server.py` (`main`; a new `parser_available`)
- Test: `webapp/tests/replays/test_replay_worker_runs.py`

**Interfaces:**
- Produces: `parser_available(settings: Settings) -> bool` (the parser command's program exists: on `PATH`, or as a file).

- [ ] **Step 1: Write the failing tests**

```python
def test_parser_available_finds_a_program_or_a_file(tmp_path):
    assert server.parser_available(server.Settings(parser_cmd=[sys.executable, "-V"]))
    assert not server.parser_available(server.Settings(parser_cmd=[str(tmp_path / "no-such-parser")]))
    assert not server.parser_available(server.Settings(parser_cmd=["no-such-program-anywhere"]))
```

(`sys` is already imported at the top of that file.)

- [ ] **Step 2: Run it to see it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v -k parser_available`
Expected: FAIL, `AttributeError: module 'replay_worker.server' has no attribute 'parser_available'`.

- [ ] **Step 3: Implement**

In `server.py`, beside `make_runs`:

```python
def parser_available(settings: Settings) -> bool:
    """The parser command's program is here (step 5's worker image has none: parsing runs on Workflows)."""
    program = settings.parser_cmd[0] if settings.parser_cmd else ""
    return bool(program) and (shutil.which(program) is not None or Path(program).is_file())
```

In `main()`, right after `settings = Settings.from_env()`:

```python
    if settings.parse_runner == "local" and not parser_available(settings):
        raise SystemExit("REPLAY_PARSE_RUNNER is local but this image has no parser "
                         f"({settings.parser_cmd[:1]}): set REPLAY_PARSE_RUNNER=workflow")
```

- [ ] **Step 4: Run it and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: all pass.

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_runs.py
git commit -m "replay worker: refuse a local parse runner on an image without the parser"
```

---

### Task 3: Uploads stream into the job's folder

**Files:**
- Modify: `replay_worker/server.py` (`Worker.submit`; new `Worker.reserve_folder`, `Worker.submit_streamed`; the `/jobs` branch of `do_POST`)
- Test: `webapp/tests/replays/test_replay_worker.py`

**Interfaces:**
- Consumes: the handler's `_length(low, high)` (step 1).
- Produces: `Worker.reserve_folder(size: int) -> tuple[str, Path]` (job id and a new, empty job folder; raises `NoSpace` when the archive can't take `size`); `Worker.submit_streamed(job_id: str, folder: Path, sha256: str, size: int) -> Job` (today's `submit` from the point the file is on disk; on any exception the folder is deleted); `Worker.submit(body: bytes) -> Job` (kept for tests and callers with bytes in hand: reserve, write, `submit_streamed`).

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker.py` (its `stub` fixture and `start()` helper are at the top of the file):

```python
import hashlib
import http.client
import socket


@pytest.fixture
def live(tmp_path, stub):
    worker, httpd, base = start(tmp_path, stub)
    try:
        yield worker, base, tmp_path / "jobs"
    finally:
        httpd.shutdown()
        httpd.server_close()


def raw_post(base, parts, length, half_close=False):
    """POST /jobs with a hand-set Content-Length; `half_close` shuts the client's sending side after `parts`, as a
    client that hung up early does, and still reads the answer."""
    host, port = base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    try:
        conn.putrequest("POST", "/jobs")
        conn.putheader("Content-Length", str(length))
        conn.endheaders()
        for part in parts:
            conn.send(part)
        if half_close:
            conn.sock.shutdown(socket.SHUT_WR)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_upload_streams_without_the_whole_bytes_path(live, monkeypatch):
    worker, base, _ = live

    def whole(body):
        raise AssertionError("the route must stream, not call Worker.submit(bytes)")
    monkeypatch.setattr(worker, "submit", whole)
    body = vrf_bytes()
    status, answer = raw_post(base, [body[i:i + 65536] for i in range(0, len(body), 65536)], len(body))
    assert status == 202
    job_id = json.loads(answer)["id"]
    assert worker.get(job_id).sha256 == hashlib.sha256(body).hexdigest() and worker.get(job_id).size == len(body)
    assert wait(base, job_id)["status"] == "done"


@pytest.mark.parametrize("length", ["0", "3", "-5", "abc"])
def test_a_bad_length_is_refused_and_leaves_nothing(live, length):
    worker, base, jobs = live
    status, _ = raw_post(base, [], length)
    assert status == 400 and not any(jobs.iterdir())


def test_a_too_large_length_is_413(live):
    worker, base, jobs = live
    status, _ = raw_post(base, [], worker.settings.max_bytes + 1)
    assert status == 413 and not any(jobs.iterdir())


def test_bad_magic_leaves_nothing(live):
    worker, base, jobs = live
    status, _ = raw_post(base, [b"NOPE" + b"x" * 100], 104)
    assert status == 400 and not any(jobs.iterdir())


def test_a_short_upload_leaves_nothing(live):
    worker, base, jobs = live
    body = vrf_bytes()[:1000]
    status, _ = raw_post(base, [body], len(body) + 500, half_close=True)
    assert status == 400 and not any(jobs.iterdir())
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker.py -v -k "streams or bad_length or too_large or bad_magic or short_upload"`
Expected: `test_upload_streams_without_the_whole_bytes_path` FAILS (the route calls `submit`); the length tests FAIL
on `-5`/`abc` (today `int()` raises inside the handler) and `0`/`3` (accepted into a 400 only after reading).

- [ ] **Step 3: Implement `reserve_folder`, `submit_streamed`, `submit`**

Replace `Worker.submit` (`server.py:428-455`) with:

```python
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
                if self.archive is not None and not self.archive.intake_ok(0):
                    raise NoSpace()
                job = Job(job_id, folder, sha256, size)
                job.game_build = self._unsupported_build(folder / "upload.vrf")
                if job.game_build is not None:
                    self._hold(job)
                else:
                    self._save(job)
                    self.queue.put_nowait(job)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if job.status != "queued":
            with self.lock:
                self.jobs[job.id] = job
            return job
        self._admitted(job)
        return job

    def submit(self, body: bytes) -> Job:
        job_id, folder = self.reserve_folder(len(body))
        try:
            (folder / "upload.vrf").write_bytes(body)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return self.submit_streamed(job_id, folder, hashlib.sha256(body).hexdigest(), len(body))
```

`intake_ok(0)` in `submit_streamed` re-checks under the admission lock that the disk still has room now that the
file is written (`intake_ok` counts what `jobs/` holds, which includes this file). Concurrent uploads can each
pass `reserve_folder` before any is written; the archive's 5 GB `ARCHIVE_SLACK` covers that overlap (15 uploads at
the 200 MB cap is 3 GB).

- [ ] **Step 4: The route**

Replace the `/jobs` branch of `do_POST` (`server.py:1654-1668`, from `length = self.headers.get("Content-Length")`
to the `return self._send(HTTPStatus.ACCEPTED, ...)`) with:

```python
            size = self._length(len(VRF_MAGIC), worker.settings.max_bytes)
            if size is None:
                return None
            try:
                job_id, folder = worker.reserve_folder(size)
            except NoSpace:
                self.close_connection = True
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "the worker is busy, try again soon"})
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
```

`rfile.read(n)` returns up to `n` bytes, or fewer only at EOF, so the first chunk is the first megabyte (or the
whole body when it is smaller), and a body of at least 4 bytes always carries the magic in it. A bad magic breaks
with `remaining > 0`, so it lands in the same cleanup as a short body. The `_length` low bound (4) refuses `0`
and `3` before any folder exists.

- [ ] **Step 5: Run the worker tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -k "worker or archive or held or upload" -q`
Expected: all pass, the existing ones unchanged.

- [ ] **Step 6: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker.py
git commit -m "replay worker: uploads stream into the job's folder"
```

---

### Task 4: The slim image and the smaller plan

**Files:**
- Modify: `replay_worker/Dockerfile`, `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`
- Test: `webapp/tests/replays/test_replay_worker_control.py` (`test_the_image_builds_a_control_venv_with_the_web_apps_pins`)

- [ ] **Step 1: Update the image test first**

In `test_the_image_builds_a_control_venv_with_the_web_apps_pins`, add after the last assertion:

```python
    # Step 5 (spec D14): the parser is the workflow's (replay_tasks/Dockerfile); the worker has none.
    assert "dotnet publish" not in dockerfile and "/opt/parser" not in dockerfile
    assert "REPLAY_PARSER_CMD" not in dockerfile
    tasks_image = (REPO / "replay_tasks" / "Dockerfile").read_text(encoding="utf-8")
    assert "dotnet publish src/CliReader" in tasks_image and "REPLAY_PARSER_CMD" in tasks_image
    assert tasks_image.rstrip().endswith('CMD ["/opt/control-venv/bin/python", "-m", "replay_tasks.main"]')
```

Every existing assertion in that test still holds for the slim image (the control venv, its pins, the gaps
modules, `REPLAY_CONTROL_CMD`, `REPLAY_HEIGHT_CMD`, `CONTROL_CACHE_DIR=/jobs/control_cache`, the final `CMD`).

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control.py -v -k image`
Expected: FAIL on `"dotnet publish" not in dockerfile`.

- [ ] **Step 2: The worker image**

In `replay_worker/Dockerfile`:
- delete stage 1 (`FROM python:3.13-slim AS source` … the `RUN python /pin/apply_parser_pin.py ...` line) and
  stage 2 (`FROM mcr.microsoft.com/dotnet/sdk:10.0 AS parser` … `RUN dotnet publish src/CliReader -c Release -o /out`);
- delete `COPY --from=parser /out /opt/parser`;
- in the final `ENV` block delete only the `REPLAY_PARSER_CMD=...` line and the `REPLAY_MEMORY_CAP_MB=0` line (both
  are the parser's; `replay_tasks/Dockerfile` keeps them);
- change the header comment's first lines to: `# The replay worker: uploads, the .vrf archive, the job records, and
  the hand-off to the replay-tasks Workflow (docs/superpowers/specs/2026-10-14-render-workflows-design.md, step 5).
  Parsing runs on the Workflow; the control venv stays for feature verification and the local control/heights
  paths (D14).`

The base stays `mcr.microsoft.com/dotnet/runtime:10.0` (its `python3` is what the server runs on today; changing the
base is out of scope). The final `ENV` block is then:

```dockerfile
ENV REPLAY_WORKER_TMP=/jobs \
    REPLAY_TIMEOUT_S=240 \
    REPLAY_MAX_BYTES=200000000 \
    REPLAY_QUEUE_SIZE=5 \
    REPLAY_WORKER_HOST=0.0.0.0 \
    REPLAY_WORKER_PORT=8080 \
    REPLAY_CONTROL_CMD='["/opt/control-venv/bin/python", "-m", "replay_worker.control_job"]' \
    REPLAY_CONTROL_WORKERS=2 \
    REPLAY_HEIGHT_CMD='["/opt/control-venv/bin/python", "-m", "replay_worker.height_job"]' \
    CONTROL_CACHE_DIR=/jobs/control_cache \
    PYTHONUNBUFFERED=1
```

(`render.yaml` overrides `REPLAY_QUEUE_SIZE` and `CONTROL_CACHE_DIR` on Render, as steps 2 and 3 set them.)

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control.py -v -k image`
Expected: PASS.

- [ ] **Step 3: `render.yaml`**

On `replay-worker`, `plan: 2c-4g` becomes `plan: standard`, and its comment becomes:

```yaml
    # 1 CPU / 2 GB: since step 5 (docs/superpowers/specs/2026-10-14-render-workflows-design.md) the worker only takes
    # uploads, keeps the archive and the job records and hands parsing, control and heights to the replay-tasks
    # Workflow. Sized from step 3's measured result sizes and the memory graph at 15 parses (RENDER_DEPLOY.md).
```

Update the web service's plan comment that names the worker's `2c-4g`.

- [ ] **Step 4: Docs**

`replay_worker/README.md`: the opening paragraph says what the worker is now: it takes uploads (streamed to
disk), keeps the archive, the receipts and the job records, and hands parsing, map control and height builds to the
`replay-tasks` Workflow; the local child paths remain for development and as the rollback for control and heights.
`RENDER_DEPLOY.md`, Workflows section:

```markdown
### Step 5: the slim worker

Before merging 5b:
1. `replay-tasks` builds from `replay_tasks/Dockerfile` (5a) and the ping is OK.
2. `replay-worker` → Environment: `REPLAY_PARSE_RUNNER`, `REPLAY_CONTROL_RUNNER` and `REPLAY_HEIGHTS_RUNNER` are
   all `workflow`. (The slim image refuses to start with a local parse runner.)
3. Memory: during a burst of 15 parses (or the largest burst seen), `replay-worker` → Metrics → memory stays well
   under 2 GB, and the largest `run result ... parse <bytes>` line times 15 fits in what is left. If not, keep
   `plan: 2c-4g` in 5b's `render.yaml` and say why in its comment.

After merging: the worker redeploys onto `standard`. Check `/health` (recipe, archive, runners), one upload parses,
and a new match's control runs. Rolling control or heights back to `local` still works on this image; parsing
can't, by design.
```

- [ ] **Step 5: Full suite and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass.

```bash
git add replay_worker/Dockerfile render.yaml replay_worker/README.md webapp/RENDER_DEPLOY.md webapp/tests/replays/test_replay_worker_control.py
git commit -m "replay worker: slim image (no parser), standard plan"
```

- [ ] **Step 6: On Render (the owner)**

Follow "Step 5: the slim worker" before and after the merge. Done when the worker runs on `standard`, its image
has no parser, one upload parses and a match's control runs, and the memory check is recorded.

# Render Workflows, step 1: foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The replay worker can start a Render Workflow run, serve it input files, and take its result back, proven by a `ping` task. Nothing the site sees changes; every runner switch stays `local`.

**Architecture:** A new standard-library module `replay_worker/runs.py` holds the Render REST client, a start pacer and the table of open runs (one-time token, deadline, result). `server.py` gains settings, two run routes and a `/runs/ping` route. A new package `replay_tasks/` holds the task bodies as plain functions (`tasks.py`, testable without the SDK) and the Render registration (`main.py`). The workflow service builds from the worker's own Dockerfile with another start command.

**Tech Stack:** Python 3.13 standard library (`http.server`, `urllib`), pytest, Render Workflows Python SDK `render==1.0.1` (image only), Render REST API v1.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md`

## Global Constraints

- `replay_worker/` stays standard library only (D13). The SDK is installed only in the image's control venv and imported only by `replay_tasks/main.py`.
- Render REST API (read from the SDK's generated client, render 1.0.1): base `https://api.render.com/v1`; `POST /task-runs` body `{"task": "<workflow-slug>/<task>", "input": [args]}`; `GET /task-runs/{id}`; `DELETE /task-runs/{id}` (cancel); `GET /task-runs?taskSlug=&limit=&cursor=` returning `[{"taskRun": {...}, "cursor"}]`; header `Authorization: Bearer <key>`. Run fields: `id` (`trn-...`), `status` in `pending | running | paused | completed | succeeded | failed | canceled`.
- `REPLAY_TASK_STARTS_PER_MIN` defaults to 1000, a safety valve only (D10). A 429 is never a failure.
- Runs never touch the database (D12). The workflow service has only `REPLAY_WORKER_URL`.
- Each kind's switch (`REPLAY_PARSE_RUNNER`, `REPLAY_CONTROL_RUNNER`, `REPLAY_HEIGHTS_RUNNER`) is `local` by default and stays `local` in this step. In `render.yaml` they are `sync: false` (D2): set by hand in the dashboard, never reset by a Blueprint sync.
- Every transfer streams in 1 MB chunks. A posted result is decoded in memory once (D18), capped at 256 MB (`run_result_max_bytes`).
- A body's `Content-Length` must be a positive integer within the route's cap; anything else is refused (400/413) before a file is opened.
- Reconciliation (D4): one list call over every open kind's slug, following cursors up to `RECONCILE_PAGES` (5) pages, then one `GET` per open run still not found.
- Test servers are closed with `shutdown()` **and** `server_close()`, in `try/finally` or fixture teardown.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`.

## Review Focus

- A result posted twice, or by a run whose token is wrong, must change nothing (`409`) — a Render retry must never overwrite the first answer.
- A run that dies on Render without posting must settle (as `infra`) within reconcile interval + grace, not hang its caller until the deadline.
- A 429 from Render while starting must delay the start, not fail the job, and must stop retrying at the run's deadline (rare on the owner's tier, but the handling stays).
- `REPLAY_*_RUNNER=workflow` without `RENDER_API_KEY` or `REPLAY_WORKFLOW_SLUG` must stop the worker at startup with a clear message, not fail later per job.
- A request for an input name the run wasn't given (e.g. `../job.json`) must be `404`, never a path outside the run's files.
- A run that has waited for a start slot past its deadline must never be started.
- An open run older than the newest 100 runs of its kind must still be found by reconciliation.

---

## File structure

| File | Responsibility |
| --- | --- |
| `replay_worker/runs.py` (new) | `RenderApi`, `RateLimited`, `RenderError`, `Pacer`, `Run`, `Runs`, `failure()`, the `ping` CLI |
| `replay_worker/server.py` (modify) | settings, `/health` additions, `GET /runs/{job}/input/{name}`, `POST /runs/{job}/result`, `POST /runs/ping`, wiring in `main()` |
| `replay_tasks/__init__.py`, `replay_tasks/tasks.py` (new) | `WorkerLink` (fetch/post over HTTP), `ping()`; later steps add `parse`, `control`, `heights` |
| `replay_tasks/main.py` (new) | `Workflows` app; registers each task with its plan, timeout and retry |
| `replay_worker/Dockerfile` (modify) | install `render==1.0.1` in the control venv; copy `replay_tasks/` |
| `webapp/tests/replays/fake_render.py` (new) | a localhost fake of the four REST routes that runs `replay_tasks.tasks` bodies in threads |
| `webapp/tests/replays/test_replay_worker_runs.py` (new) | unit tests of `runs.py` and the end-to-end ping |
| `replay_worker/README.md`, `render.yaml`, `webapp/RENDER_DEPLOY.md` (modify) | the new settings and the dashboard steps |

---

### Task 1: The Render REST client and the pacer

**Files:**
- Create: `replay_worker/runs.py`
- Create: `webapp/tests/replays/fake_render.py`
- Test: `webapp/tests/replays/test_replay_worker_runs.py`

**Interfaces:**
- Produces: `RenderApi(token: str, base: str = API, timeout_s: float = 30.0)` with `start(slug: str, args: dict) -> dict`, `get(run_id: str) -> dict`, `cancel(run_id: str) -> None`, `page(slugs: list[str], cursor: str | None = None, limit: int = 100) -> tuple[list[dict], str | None]` (the runs on this page, and the cursor for the next one or None); exceptions `RenderError`, `RateLimited(RenderError)`; `Pacer(per_min: int, clock=time.monotonic, sleep=time.sleep)` with `take(stop=lambda: False) -> bool` (False when `stop()` became true before a slot was free) and `hold(seconds: float) -> None`.
- Produces (tests): `FakeRender` with `.url` (base ending in `/v1`), `.started: list[dict]`, `.status: dict[str, str]`, `.slug_of`, `.list_calls`, `.get_calls`, `.rate_limit_next: int`, `.finish: bool`, `.runner` (callable `(kind, args) -> None` run in a thread per start; default does nothing), `.close()`; and `closed_port() -> int`.

- [ ] **Step 1: Write the fake Render API**

```python
# webapp/tests/replays/fake_render.py
"""A localhost fake of Render's task-run REST routes (POST/GET/DELETE /v1/task-runs, GET /v1/task-runs).
Each start runs `runner(kind, args)` on a thread, the way a real run would call back into the worker."""

import itertools
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeRender:
    def __init__(self, runner=None):
        self.started: list[dict] = []
        self.status: dict[str, str] = {}
        self.cancelled: list[str] = []
        self.auth: list[str] = []
        self.slug_of: dict[str, str] = {}
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []
        self.rate_limit_next = 0
        self.finish = True              # False: a started run stays "running" until a test sets its status
        self.runner = runner or (lambda kind, args: None)
        ids = itertools.count(1)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                fake.auth.append(self.headers.get("Authorization", ""))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if fake.rate_limit_next:
                    fake.rate_limit_next -= 1
                    return self._send(429, {"message": "slow down"})
                run_id = f"trn-{next(ids)}"
                kind = body["task"].split("/", 1)[1]
                fake.started.append({"id": run_id, "task": body["task"], "input": body["input"]})
                fake.slug_of[run_id] = body["task"]
                fake.status[run_id] = "running"

                def go():
                    try:
                        fake.runner(kind, body["input"][0])
                        if fake.finish and fake.status[run_id] == "running":
                            fake.status[run_id] = "completed"
                    except Exception:  # noqa: BLE001 - a failing task is a failed run
                        if fake.finish and fake.status[run_id] == "running":
                            fake.status[run_id] = "failed"
                threading.Thread(target=go, daemon=True).start()
                return self._send(202, {"id": run_id, "status": "pending"})     # Render answers 202

            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/v1/task-runs":
                    fake.list_calls.append(self.path)
                    query = urllib.parse.parse_qs(parsed.query)
                    slugs = set(query.get("taskSlug", []))
                    limit = int(query.get("limit", ["100"])[0])
                    rows = [(i, s) for i, s in reversed(list(fake.status.items()))
                            if not slugs or fake.slug_of[i] in slugs]
                    start = int(query.get("cursor", ["0"])[0])
                    page = rows[start:start + limit]
                    items = [{"taskRun": {"id": i, "status": s}, "cursor": str(start + n + 1)}
                             for n, (i, s) in enumerate(page)]
                    return self._send(200, items)
                fake.get_calls.append(self.path)
                run_id = parsed.path.rsplit("/", 1)[1]
                if run_id not in fake.status:
                    return self._send(404, {"message": "not found"})
                return self._send(200, {"id": run_id, "status": fake.status[run_id]})

            def do_DELETE(self):  # noqa: N802
                run_id = self.path.rsplit("/", 1)[1]
                fake.cancelled.append(run_id)
                fake.status[run_id] = "canceled"
                return self._send(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()     # releases the listening socket, not just the serve loop


def closed_port() -> int:
    """A localhost port nothing listens on: bound, then closed."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
```

- [ ] **Step 2: Write the failing tests**

```python
# webapp/tests/replays/test_replay_worker_runs.py
"""Workflow runs on the replay worker (replay_worker/runs.py; spec 2026-10-14-render-workflows-design.md):
the REST client, the start pacer, the run table, the run routes and a ping end to end. Localhost only."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from fake_render import FakeRender, closed_port  # noqa: E402
from replay_worker import runs  # noqa: E402


@pytest.fixture
def render():
    fake = FakeRender()
    try:
        yield fake
    finally:
        fake.close()


def test_start_get_cancel_page(render):
    api = runs.RenderApi("k3y", base=render.url)
    run = api.start("replay-tasks/ping", {"job_id": "j1"})
    assert run["id"] == "trn-1"
    assert render.started[0] == {"id": "trn-1", "task": "replay-tasks/ping", "input": [{"job_id": "j1"}]}
    assert render.auth[0] == "Bearer k3y"
    assert api.get("trn-1")["id"] == "trn-1"
    items, cursor = api.page(["replay-tasks/ping"])
    assert [r["id"] for r in items] == ["trn-1"] and cursor is None
    api.cancel("trn-1")
    assert render.cancelled == ["trn-1"]


def test_page_filters_by_every_slug_and_follows_the_cursor(render):
    api = runs.RenderApi("k", base=render.url)
    for kind in ("ping", "control", "ping"):
        api.start(f"w/{kind}", {})
    api.start("other/ping", {})
    first, cursor = api.page(["w/ping", "w/control"], limit=2)
    second, end = api.page(["w/ping", "w/control"], cursor=cursor, limit=2)
    assert [r["id"] for r in first + second] == ["trn-3", "trn-2", "trn-1"] and end is None
    assert "taskSlug=w%2Fping&taskSlug=w%2Fcontrol" in render.list_calls[0]


def test_429_is_rate_limited_and_404_is_render_error(render):
    api = runs.RenderApi("k", base=render.url)
    render.rate_limit_next = 1
    with pytest.raises(runs.RateLimited):
        api.start("w/ping", {})
    with pytest.raises(runs.RenderError):
        api.get("trn-404")


def test_unreachable_is_render_error():
    api = runs.RenderApi("k", base=f"http://127.0.0.1:{closed_port()}/v1", timeout_s=1)
    with pytest.raises(runs.RenderError):
        api.get("trn-1")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_pacer_allows_per_min_then_waits_for_the_window():
    clock = Clock()
    pacer = runs.Pacer(3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        pacer.take()
    assert clock.now == 0.0
    pacer.take()
    assert clock.now >= 60.0


def test_pacer_hold_pushes_the_next_start():
    clock = Clock()
    pacer = runs.Pacer(40, clock=clock, sleep=clock.sleep)
    pacer.hold(10)
    assert pacer.take() is True
    assert clock.now >= 10.0


def test_pacer_gives_up_when_told_to_stop():
    clock = Clock()
    pacer = runs.Pacer(1, clock=clock, sleep=clock.sleep)
    pacer.take()
    assert pacer.take(stop=lambda: clock.now >= 5.0) is False      # the next slot is 60 s away
    assert clock.now < 60.0
```

- [ ] **Step 3: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: FAIL, `ImportError: cannot import name 'runs' from 'replay_worker'`.

- [ ] **Step 4: Write `runs.py`'s client and pacer**

```python
# replay_worker/runs.py
"""Workflow runs: the replay worker's side of Render Workflows
(docs/superpowers/specs/2026-10-14-render-workflows-design.md). Standard library only, like server.py.

A run is one task run on Render doing a job this worker would otherwise do in a child process. The worker
records the run (job id, one-time token, deadline) before it starts it; the run downloads its input files
from this worker (`GET /runs/{job}/input/{name}`) and posts its output back (`POST /runs/{job}/result`), both
with the token in `X-Run-Token`. The first valid result settles the run; a later one, or one with another
token, is refused. Once a minute `Runs.reconcile` asks Render about the open runs, so a run that died without
posting settles as a machine failure.

    python3 -m replay_worker.runs ping     (inside the worker: starts a ping run through the local server)
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://api.render.com/v1"
ENDED = ("completed", "succeeded", "failed", "canceled")
RECONCILE_S = 60
RECONCILE_PAGES = 5    # pages of one list call per pass; a run still unseen after them is asked for alone
RECONCILE_PAGE_SIZE = 100
RATE_LIMIT_WAIT_S = 10
GRACE_S = 120          # a run Render reports ended may still be posting its result
CHUNK = 1 << 20


class RenderError(Exception):
    """Render's API refused the call or couldn't be reached."""


class RateLimited(RenderError):
    """429: wait and start again; never a failure (spec D10)."""


class RenderApi:
    """The four task-run routes, over urllib."""

    def __init__(self, token: str, base: str = API, timeout_s: float = 30.0):
        self.token, self.base, self.timeout_s = token, base.rstrip("/"), timeout_s

    def start(self, slug: str, args: dict) -> dict:
        return self._call("POST", "/task-runs", {"task": slug, "input": [args]})

    def get(self, run_id: str) -> dict:
        return self._call("GET", f"/task-runs/{urllib.parse.quote(run_id)}")

    def cancel(self, run_id: str) -> None:
        self._call("DELETE", f"/task-runs/{urllib.parse.quote(run_id)}")

    def page(self, slugs: list[str], cursor: str | None = None, limit: int = 100) -> tuple[list[dict], str | None]:
        """One page of runs of any of `slugs`, and the cursor for the next page (None at the end)."""
        params = [("taskSlug", s) for s in slugs] + [("limit", limit)]
        if cursor:
            params.append(("cursor", cursor))
        items = self._call("GET", f"/task-runs?{urllib.parse.urlencode(params)}") or []
        runs = [item.get("taskRun", item) for item in items]
        return runs, (items[-1].get("cursor") if len(items) == limit else None)

    def _call(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise RateLimited(f"{method} {path}: 429") from error
            raise RenderError(f"{method} {path}: HTTP {error.code}") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RenderError(f"{method} {path}: {error}") from error
        return json.loads(raw) if raw else None


class Pacer:
    """At most `per_min` starts in any 60 s window; `hold` pushes every start back (after a 429)."""

    def __init__(self, per_min: int, clock=time.monotonic, sleep=time.sleep):
        self.per_min, self.clock, self.sleep = max(1, int(per_min)), clock, sleep
        self.starts: list[float] = []
        self.not_before = 0.0
        self.lock = threading.Lock()

    def take(self, stop=lambda: False) -> bool:
        """Waits for a start slot. False, with no slot taken, when `stop()` turns true first (the run's deadline)."""
        while True:
            if stop():
                return False
            with self.lock:
                now = self.clock()
                self.starts = [t for t in self.starts if now - t < 60]
                wait = self.not_before - now
                if wait <= 0 and len(self.starts) < self.per_min:
                    self.starts.append(now)
                    return True
                if wait <= 0:
                    wait = 60 - (now - self.starts[0])
            self.sleep(min(max(wait, 0.01), 1.0))

    def hold(self, seconds: float) -> None:
        with self.lock:
            self.not_before = max(self.not_before, self.clock() + seconds)
```

- [ ] **Step 5: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add replay_worker/runs.py webapp/tests/replays/fake_render.py webapp/tests/replays/test_replay_worker_runs.py
git commit -m "replay worker: Render task-run client and start pacer"
```

---

### Task 2: The run table

**Files:**
- Modify: `replay_worker/runs.py`
- Test: `webapp/tests/replays/test_replay_worker_runs.py`

**Interfaces:**
- Consumes: `RenderApi`, `Pacer`, `RateLimited`, `RenderError` (Task 1).
- Produces: `failure(kind: str, error: str) -> dict` (`{"status": "failed", "error_kind": kind, "error": error}`); `Run` dataclass (`job_id, kind, token, files: dict[str, Path], folder: Path, deadline: float, render_id: str | None, result: dict | None, ended_seen: float | None, done: threading.Event`); `Runs(api, slug: str, pacer: Pacer, root: Path, clock=time.time)` with `open(kind, job_id, files, timeout_s, *, token=None, render_id=None, deadline=None) -> Run` (an absolute `deadline` in the table's clock overrides `timeout_s`: a parse passes its saved one, D8), `start(run, args: dict, on_started=None) -> None`, `wait(run) -> dict`, `file(job_id, name, token) -> Path | None`, `accepts(job_id, token) -> Run | None`, `deliver(job_id, token, path: Path) -> int`, `reconcile() -> None`, `close(run) -> None`, `status() -> dict`, `reconcile_forever(stop: threading.Event) -> None`. Kinds: `ping`, `parse`, `control`, `heights`; a run's Render slug is `f"{slug}/{kind}"`.

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_runs.py`:

```python
import json
import threading
import time


def make_runs(render, tmp_path, clock=time.time):
    return runs.Runs(runs.RenderApi("k", base=render.url), "replay-tasks", runs.Pacer(1000), tmp_path / "runs",
                     clock=clock)


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_first_valid_result_wins(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("ping", "j1", {"ping": write(tmp_path / "p.txt", "x")}, timeout_s=30)
    table.start(run, {"hello": 1})
    assert render.started[0]["task"] == "replay-tasks/ping"
    assert render.started[0]["input"][0] == {"hello": 1, "job_id": "j1", "token": run.token}
    assert table.deliver("j1", "wrong", write(tmp_path / "a.json", {"status": "ok", "n": 0})) == 409
    assert table.deliver("j1", run.token, write(tmp_path / "b.json", {"status": "ok", "n": 1})) == 200
    assert table.deliver("j1", run.token, write(tmp_path / "c.json", {"status": "ok", "n": 2})) == 409
    assert table.wait(run) == {"status": "ok", "n": 1}
    assert table.deliver("j1", run.token, write(tmp_path / "d.json", {"status": "ok"})) == 409  # closed


def test_a_result_that_is_not_a_json_object_is_400(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("ping", "j1", {}, timeout_s=30)
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2", encoding="utf-8")
    assert table.deliver("j1", run.token, bad) == 400
    assert table.deliver("j1", run.token, write(tmp_path / "list.json", [1])) == 400
    assert not run.done.is_set()


def test_files_only_by_name_and_token(render, tmp_path):
    table = make_runs(render, tmp_path)
    f = write(tmp_path / "p.txt", "x")
    run = table.open("ping", "j1", {"ping": f}, timeout_s=30)
    assert table.file("j1", "ping", run.token) == f
    assert table.file("j1", "ping", "nope") is None
    assert table.file("j1", "../p.txt", run.token) is None
    assert table.file("j2", "ping", run.token) is None


def test_one_open_run_per_job(render, tmp_path):
    table = make_runs(render, tmp_path)
    table.open("ping", "j1", {}, timeout_s=30)
    with pytest.raises(ValueError):
        table.open("ping", "j1", {}, timeout_s=30)


class SharedClock:
    """One fake clock for the table and its pacer: sleeping advances it."""
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_429_waits_then_starts(render, tmp_path):
    clock = SharedClock()
    table = make_runs(render, tmp_path, clock=clock)
    table.pacer = runs.Pacer(1000, clock=clock, sleep=clock.sleep)
    render.rate_limit_next = 2
    run = table.open("ping", "j1", {}, timeout_s=60)
    table.start(run, {})
    assert run.render_id == "trn-1" and not run.done.is_set()
    assert clock.now >= 1000.0 + 2 * runs.RATE_LIMIT_WAIT_S        # two holds, on the fake clock


def test_no_start_after_the_deadline(render, tmp_path):
    clock = SharedClock()
    table = make_runs(render, tmp_path, clock=clock)
    table.pacer = runs.Pacer(1000, clock=clock, sleep=clock.sleep)
    render.rate_limit_next = 100                                  # Render keeps saying 429
    run = table.open("ping", "j1", {}, timeout_s=25)
    table.start(run, {})
    assert run.render_id is None and run.result["error_kind"] == "infra"
    assert clock.now < 1000.0 + 25 + runs.RATE_LIMIT_WAIT_S + 1     # it stopped at the deadline


def test_render_down_settles_infra(tmp_path):
    table = runs.Runs(runs.RenderApi("k", base=f"http://127.0.0.1:{closed_port()}/v1", timeout_s=1), "w",
                      runs.Pacer(1000), tmp_path / "runs")
    run = table.open("ping", "j1", {}, timeout_s=30)
    table.start(run, {})
    assert table.wait(run)["error_kind"] == "infra"


def test_on_started_gets_the_render_id_before_start_returns(render, tmp_path):
    table = make_runs(render, tmp_path)
    seen = []
    run = table.open("ping", "j1", {}, timeout_s=30)
    table.start(run, {}, on_started=seen.append)
    assert seen == ["trn-1"]


def test_deadline_cancels_and_settles_timeout(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("ping", "j1", {}, timeout_s=0.3)
    table.start(run, {})
    answer = table.wait(run)
    assert answer["error_kind"] == "timeout" and render.cancelled == ["trn-1"]


def test_reconcile_settles_a_run_that_ended_without_a_result_after_grace(render, tmp_path):
    render.finish = False                         # statuses change only when this test says so
    now = [1000.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("ping", "j1", {}, timeout_s=3600)
    table.start(run, {})
    render.status["trn-1"] = "failed"
    table.reconcile()
    assert not run.done.is_set()                  # within grace: its result may still be on the way
    now[0] += runs.GRACE_S
    table.reconcile()
    assert run.result["error_kind"] == "infra" and "failed" in run.result["error"]


def test_reconcile_leaves_running_runs_and_a_result_in_grace_wins(render, tmp_path):
    render.finish = False
    now = [1000.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("ping", "j1", {}, timeout_s=3600)
    table.start(run, {})
    table.reconcile()
    assert render.status["trn-1"] == "running" and not run.done.is_set()
    render.status["trn-1"] = "completed"
    table.reconcile()
    assert table.deliver("j1", run.token, write(tmp_path / "r.json", {"status": "ok"})) == 200
    now[0] += runs.GRACE_S
    table.reconcile()
    assert run.result == {"status": "ok"}


def test_reconcile_finds_an_old_run_by_paging_in_one_list(render, tmp_path, monkeypatch):
    render.finish = False
    monkeypatch.setattr(runs, "RECONCILE_PAGE_SIZE", 10)
    now = [1000.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    old = table.open("heights", "old", {}, timeout_s=3600)
    table.start(old, {})
    api = runs.RenderApi("k", base=render.url)
    for _ in range(25):                     # 25 newer builds (not this table's) push it to the third page
        api.start("replay-tasks/heights", {})
    render.status[old.render_id] = "failed"
    table.reconcile()
    assert len(render.list_calls) == 3 and render.get_calls == []
    assert all("taskSlug=replay-tasks%2Fheights" in call for call in render.list_calls)


def test_status_counts_open_runs(render, tmp_path):
    table = make_runs(render, tmp_path)
    table.open("ping", "j1", {}, timeout_s=30)
    assert table.status()["open"] == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: the new tests FAIL with `AttributeError: module 'replay_worker.runs' has no attribute 'Runs'`.

- [ ] **Step 3: Implement the run table**

Append to `replay_worker/runs.py`:

```python
def failure(kind: str, error: str) -> dict:
    """A run's answer when it didn't produce one: kind is "infra", "timeout" or "deploy"."""
    return {"status": "failed", "error_kind": kind, "error": error}


@dataclass
class Run:
    job_id: str
    kind: str                       # "ping" | "parse" | "control" | "heights"
    token: str
    files: dict[str, Path]          # what the run may download, by name
    folder: Path                    # where a posted result lands
    deadline: float
    render_id: str | None = None
    result: dict | None = None
    ended_seen: float | None = None
    done: threading.Event = field(default_factory=threading.Event)


class Runs:
    """The open runs, by job id. Thread-safe; every settle happens once, under the lock."""

    def __init__(self, api: RenderApi, slug: str, pacer: Pacer, root: Path, clock=time.time):
        self.api, self.slug, self.pacer, self.root, self.clock = api, slug, pacer, Path(root), clock
        self.open_runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        self.last_reconcile: float | None = None
        self.last_error: str | None = None

    def open(self, kind: str, job_id: str, files: dict, timeout_s: float, *, token: str | None = None,
             render_id: str | None = None, deadline: float | None = None) -> Run:
        folder = self.root / job_id
        run = Run(job_id, kind, token or secrets.token_hex(16), {k: Path(v) for k, v in files.items()}, folder,
                  deadline if deadline is not None else self.clock() + timeout_s, render_id)
        with self.lock:
            if job_id in self.open_runs:
                raise ValueError(f"a run for job {job_id} is already open")
            self.open_runs[job_id] = run
        folder.mkdir(parents=True, exist_ok=True)
        return run

    def start(self, run: Run, args: dict, on_started=None) -> None:
        """Starts the run, pacing starts and waiting out 429s until its deadline. `on_started(render_id)` runs
        before this returns. A start that can't happen settles the run as `infra`."""
        payload = {**args, "job_id": run.job_id, "token": run.token}
        past = lambda: self.clock() >= run.deadline  # noqa: E731
        while True:
            # Checked again after the wait for a slot: a run is never started past its deadline (D10).
            if past() or not self.pacer.take(stop=past) or past():
                self._settle(run, failure("infra", "the run could not start before its deadline"))
                return
            try:
                answer = self.api.start(f"{self.slug}/{run.kind}", payload)
                break
            except RateLimited:
                self.pacer.hold(RATE_LIMIT_WAIT_S)
            except RenderError as error:
                self.last_error = str(error)
                self._settle(run, failure("infra", f"the run could not start: {error}"))
                return
        run.render_id = answer.get("id")
        if on_started is not None:
            on_started(run.render_id)

    def wait(self, run: Run) -> dict:
        """Blocks until the run settles or its deadline passes (then it is cancelled), closes it, and returns
        its answer."""
        while not run.done.wait(timeout=0.2):
            if self.clock() >= run.deadline:
                if run.render_id:
                    try:
                        self.api.cancel(run.render_id)
                    except RenderError:
                        pass
                self._settle(run, failure("timeout", "the run timed out"))
        self.close(run)
        return run.result

    def accepts(self, job_id: str, token: str | None) -> Run | None:
        with self.lock:
            run = self.open_runs.get(job_id)
        if run is None or run.done.is_set() or not secrets.compare_digest(run.token, token or ""):
            return None
        return run

    def file(self, job_id: str, name: str, token: str | None) -> Path | None:
        run = self.accepts(job_id, token)
        return None if run is None else run.files.get(name)

    def deliver(self, job_id: str, token: str | None, path: Path) -> int:
        """A posted result, already on disk at `path`: 200 settled, 400 not a JSON object, 409 refused."""
        run = self.accepts(job_id, token)
        if run is None:
            return 409
        try:
            result = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 400
        if not isinstance(result, dict):
            return 400
        return 200 if self._settle(run, result) else 409

    def reconcile(self) -> None:
        """One look at Render for every open, started run: an ended run with no result settles as `infra` once
        GRACE_S has passed since it was first seen ended."""
        with self.lock:
            todo = [r for r in self.open_runs.values() if r.render_id and not r.done.is_set()]
        if not todo:
            return
        # One list over every open kind, page after page until every open run is seen (D4); a run still unseen
        # after RECONCILE_PAGES pages is asked for alone below.
        seen: dict[str, str] = {}
        wanted = {r.render_id for r in todo}
        slugs = sorted({f"{self.slug}/{r.kind}" for r in todo})
        cursor = None
        try:
            for _ in range(RECONCILE_PAGES):
                items, cursor = self.api.page(slugs, cursor, limit=RECONCILE_PAGE_SIZE)
                for item in items:
                    seen[item.get("id")] = item.get("status")
                if cursor is None or wanted <= set(seen):
                    break
        except RenderError as error:
            self.last_error = str(error)
            return
        now = self.clock()
        self.last_reconcile = now
        for run in todo:
            status = seen.get(run.render_id)
            if status is None:
                try:
                    status = self.api.get(run.render_id).get("status")
                except RenderError:
                    continue
            if status not in ENDED:
                continue
            if run.ended_seen is None:
                run.ended_seen = now
            if now - run.ended_seen >= GRACE_S:
                self._settle(run, failure("infra", f"the run ended ({status}) without a result"))

    def reconcile_forever(self, stop: threading.Event, every_s: float = RECONCILE_S) -> None:
        while not stop.wait(every_s):
            try:
                self.reconcile()
            except Exception as error:  # noqa: BLE001 - the loop must outlive any one pass
                self.last_error = f"reconcile: {error}"

    def close(self, run: Run) -> None:
        with self.lock:
            if self.open_runs.get(run.job_id) is run:
                del self.open_runs[run.job_id]
        shutil.rmtree(run.folder, ignore_errors=True)

    def status(self) -> dict:
        with self.lock:
            open_count = len(self.open_runs)
        return {"open": open_count, "starts_per_min": self.pacer.per_min, "last_reconcile": self.last_reconcile,
                "last_error": self.last_error}

    def _settle(self, run: Run, result: dict) -> bool:
        with self.lock:
            if run.done.is_set():
                return False
            run.result = result
            run.done.set()
            return True
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/runs.py webapp/tests/replays/test_replay_worker_runs.py
git commit -m "replay worker: the run table (tokens, deadline, reconcile)"
```

---

### Task 3: Settings, run routes and health on the worker

**Files:**
- Modify: `replay_worker/server.py` (the module docstring's route list and configuration list; `Settings` and `Settings.from_env`; `make_handler`'s `do_GET`/`do_POST`; `make_server`; `main`)
- Test: `webapp/tests/replays/test_replay_worker_runs.py`

**Interfaces:**
- Consumes: `runs.Runs`, `runs.RenderApi`, `runs.Pacer`, `runs.CHUNK`, `runs.failure` (Tasks 1-2).
- Produces: `Settings.parse_runner`, `.control_runner`, `.heights_runner` (`"local" | "workflow"`), `.workflow_slug: str | None`, `.render_api_key: str | None`, `.render_api_base: str` (default `runs.API`), `.task_starts_per_min: int` (1000), `.run_result_max_bytes: int` (256 MiB); `Settings.workflow_wanted() -> bool`; `make_runs(settings) -> runs.Runs | None`; `make_handler(worker, control=None, heights=None, runs_table=None)` and `make_server(..., runs_table=None)`; the handler's `_length(low, high) -> int | None` (later steps' routes use it). Routes: `GET /runs/{job}/input/{name}` and `POST /runs/{job}/result` (header `X-Run-Token`), `POST /runs/ping`. `/health` adds `"runners": {"parse", "control", "heights"}` and `"workflow": runs_table.status() | None`.

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_runs.py`:

```python
import urllib.error
import urllib.request

from replay_worker import server  # noqa: E402


def test_settings_from_env_defaults_local():
    s = server.Settings.from_env({})
    assert (s.parse_runner, s.control_runner, s.heights_runner) == ("local", "local", "local")
    assert s.task_starts_per_min == 1000 and not s.workflow_wanted()


def test_settings_refuse_an_unknown_runner():
    with pytest.raises(ValueError):
        server.Settings.from_env({"REPLAY_CONTROL_RUNNER": "cloud"})


def test_workflow_runner_without_key_or_slug_stops_startup():
    with pytest.raises(SystemExit):
        server.make_runs(server.Settings.from_env({"REPLAY_CONTROL_RUNNER": "workflow"}))


def test_no_key_means_no_run_table():
    assert server.make_runs(server.Settings.from_env({})) is None


@pytest.fixture
def live(render, tmp_path):
    """A worker server with a run table pointed at the fake Render."""
    settings = server.Settings(temp_root=tmp_path, workflow_slug="replay-tasks", render_api_key="k",
                               render_api_base=render.url)
    table = server.make_runs(settings)
    worker = server.Worker(settings)
    httpd = server.make_server(worker, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", table
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(url, method="GET", data=None, token=None, headers=None):
    headers = {**({"X-Run-Token": token} if token else {}), **(headers or {})}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_input_route_streams_the_named_file_only(live, tmp_path):
    base, table = live
    big = tmp_path / "big.bin"
    big.write_bytes(b"a" * (3 * runs.CHUNK + 5))
    run = table.open("ping", "j1", {"vrf": big}, timeout_s=30)
    status, body = call(f"{base}/runs/j1/input/vrf", token=run.token)
    assert status == 200 and len(body) == 3 * runs.CHUNK + 5
    assert call(f"{base}/runs/j1/input/vrf", token="bad")[0] == 404
    assert call(f"{base}/runs/j1/input/..%2Fjob.json", token=run.token)[0] == 404


def test_result_route_takes_the_first_valid_post(live):
    base, table = live
    run = table.open("ping", "j1", {}, timeout_s=30)
    assert call(f"{base}/runs/j1/result", "POST", b'{"status": "ok"}', token="bad")[0] == 409
    assert call(f"{base}/runs/j1/result", "POST", b'{"status": "ok"}', token=run.token)[0] == 200
    assert call(f"{base}/runs/j1/result", "POST", b'{"status": "no"}', token=run.token)[0] == 409
    assert run.result == {"status": "ok"}


def test_result_route_refuses_bad_lengths_before_writing(live):
    import http.client
    base, table = live
    run = table.open("ping", "j2", {}, timeout_s=30)
    host, port = base.replace("http://", "").split(":")
    for length in ("0", "-5", "abc", str(server.Settings().run_result_max_bytes + 1)):
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        try:
            conn.putrequest("POST", "/runs/j2/result")
            conn.putheader("X-Run-Token", run.token)
            conn.putheader("Content-Length", length)
            conn.endheaders()
            assert conn.getresponse().status in (400, 413), length
        finally:
            conn.close()
    assert not any(run.folder.glob("result-*")) and not run.done.is_set()


def test_health_names_the_runners_and_the_workflow(live):
    base, _ = live
    body = json.loads(call(f"{base}/health")[1])
    assert body["runners"] == {"parse": "local", "control": "local", "heights": "local"}
    assert body["workflow"]["open"] == 0
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: the new tests FAIL (`Settings` has no `parse_runner`; `make_runs` missing).

- [ ] **Step 3: Add the settings**

In `server.py`, import the module beside `archive_store` (same try/except pattern):

```python
try:
    from replay_worker import archive as archive_store  # noqa: E402
    from replay_worker import runs as runs_mod  # noqa: E402
except ImportError:  # pragma: no cover - run as a plain script from its folder
    import archive as archive_store  # type: ignore[no-redef]  # noqa: E402
    import runs as runs_mod  # type: ignore[no-redef]  # noqa: E402
```

Add to `Settings` (after `archive_total_bytes`):

```python
    # Render Workflows (docs/superpowers/specs/2026-10-14-render-workflows-design.md): which kind of work runs as
    # a Workflow run instead of a child here. "local" (default) is today's child process.
    parse_runner: str = "local"
    control_runner: str = "local"
    heights_runner: str = "local"
    workflow_slug: str | None = None              # the workflow service's slug, e.g. "replay-tasks"
    render_api_key: str | None = None
    render_api_base: str = runs_mod.API
    task_starts_per_min: int = 1000               # a safety valve only (spec D10)
    run_result_max_bytes: int = 256 * 1024 ** 2   # a parse result, decoded in memory once (spec D18)

    def workflow_wanted(self) -> bool:
        return "workflow" in (self.parse_runner, self.control_runner, self.heights_runner)
```

In `from_env`, before `return settings`:

```python
        for name in ("parse", "control", "heights"):
            value = env.get(f"REPLAY_{name.upper()}_RUNNER", "local")
            if value not in ("local", "workflow"):
                raise ValueError(f"REPLAY_{name.upper()}_RUNNER must be local or workflow, not {value!r}")
            setattr(settings, f"{name}_runner", value)
        settings.workflow_slug = env.get("REPLAY_WORKFLOW_SLUG") or None
        settings.render_api_key = env.get("RENDER_API_KEY") or None
        settings.render_api_base = env.get("REPLAY_RENDER_API", settings.render_api_base)
        settings.task_starts_per_min = int(env.get("REPLAY_TASK_STARTS_PER_MIN", settings.task_starts_per_min))
```

Add, after `class Settings` and `Job`:

```python
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
```

- [ ] **Step 4: Add the routes**

Change the signatures:

```python
def make_handler(worker: Worker, control: ControlRunner | None = None, heights: HeightBuilds | None = None,
                 runs_table=None):
```

```python
def make_server(worker: Worker, host: str = "127.0.0.1", port: int = 0,
                control: ControlRunner | None = None, heights: HeightBuilds | None = None,
                runs_table=None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(worker, control, heights, runs_table))
```

Inside `Handler`, add these methods:

```python
        def _run_input(self):
            parts = self.path.split("/")          # ["", "runs", job, "input", name]
            name = urllib.parse.unquote(parts[4]) if len(parts) == 5 else ""
            path = runs_table.file(parts[2], name, self.headers.get("X-Run-Token")) if name else None
            if path is None or not path.is_file():
                return self._send(HTTPStatus.NOT_FOUND, {"error": "no such input"})
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            with path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile, runs_mod.CHUNK)

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

        def _run_result(self):
            parts = self.path.split("/")          # ["", "runs", job, "result"]
            token = self.headers.get("X-Run-Token")
            run = runs_table.accepts(parts[2], token) if len(parts) == 4 else None
            length = self.headers.get("Content-Length")
            if run is None:
                self.close_connection = True
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
```

Add `import urllib.parse` to the imports at the top of `server.py`.

In `do_GET`, first line of the method body (before `/health`):

```python
            if runs_table is not None and self.path.startswith("/runs/") and "/input/" in self.path:
                return self._run_input()
```

In `do_POST`, first lines:

```python
            if runs_table is not None and self.path.startswith("/runs/"):
                if self.path == "/runs/ping":
                    return self._run_ping()
                if self.path.endswith("/result"):
                    return self._run_result()
```

In the `/health` body, after `body["control"] = ...` is built and before it is sent:

```python
                body["runners"] = {"parse": worker.settings.parse_runner, "control": worker.settings.control_runner,
                                   "heights": worker.settings.heights_runner}
                body["workflow"] = runs_table.status() if runs_table is not None else None
```

- [ ] **Step 5: Wire `main()`**

In `main()`, after `settings = Settings.from_env()`:

```python
    runs_table = make_runs(settings)
    if runs_table is not None:
        threading.Thread(target=runs_table.reconcile_forever, args=(threading.Event(),), name="runs-reconcile",
                         daemon=True).start()
```

and pass `runs_table=runs_table` to `make_server(...)`.

- [ ] **Step 6: Update the docstrings**

In `server.py`'s module docstring, add under the routes:

```
    GET  /runs/{job}/input/{name}   (header X-Run-Token) a Workflow run's input file, streamed; 404 otherwise.
    POST /runs/{job}/result         (header X-Run-Token) the run's JSON answer: 200 taken, 409 refused (wrong
                                    token, unknown or already settled run), 413, 400.
    POST /runs/ping                 starts a ping run and waits for it: {"ok", "answer", "seconds"}.
These exist only when RENDER_API_KEY and REPLAY_WORKFLOW_SLUG are set (replay_worker/runs.py).
```

and to the configuration list:

```
    REPLAY_PARSE_RUNNER / REPLAY_CONTROL_RUNNER / REPLAY_HEIGHTS_RUNNER   "local" (default) or "workflow"
    REPLAY_WORKFLOW_SLUG     the workflow service's slug (e.g. replay-tasks)
    RENDER_API_KEY           a Render API key (set by hand in the dashboard, never in render.yaml)
    REPLAY_TASK_STARTS_PER_MIN  run starts per minute (default 1000: a safety valve; a 429 waits and retries)
```

- [ ] **Step 7: Run the tests, then the whole worker suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: all pass.
Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -k worker -q`
Expected: all pass (no behaviour changed for local runners).

- [ ] **Step 8: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_runs.py
git commit -m "replay worker: run settings, run routes and /runs/ping"
```

---

### Task 4: The task package and a ping end to end

**Files:**
- Create: `replay_tasks/__init__.py`, `replay_tasks/tasks.py`, `replay_tasks/main.py`
- Test: `webapp/tests/replays/test_replay_worker_runs.py`

**Interfaces:**
- Consumes: the worker routes (Task 3).
- Produces: `WorkerLink(base_url: str, job_id: str, token: str, timeout_s: float = 120)` with `fetch(name: str, dest: Path) -> Path`, `fetch_text(name: str) -> str`, `post_result(result: dict) -> None`, `post_file(route: str, src: Path) -> None` (route relative to `/runs/{job}/`); `link_for(args: dict) -> WorkerLink` (reads `REPLAY_WORKER_URL`); `ping(args: dict, link: WorkerLink) -> None`; `TASKS: dict[str, Callable[[dict, WorkerLink], None]]`.

- [ ] **Step 1: Write the failing end-to-end test**

Append to `test_replay_worker_runs.py`:

```python
from replay_tasks import tasks as task_bodies  # noqa: E402


def test_ping_end_to_end(render, live):
    base, _ = live
    render.runner = lambda kind, args: task_bodies.TASKS[kind](
        args, task_bodies.WorkerLink(base, args["job_id"], args["token"]))
    status, body = call(f"{base}/runs/ping", "POST", b"")
    answer = json.loads(body)
    assert status == 200 and answer["ok"] is True
    assert render.started[0]["task"] == "replay-tasks/ping"


def test_worker_link_accepts_a_bare_host_port(monkeypatch):
    monkeypatch.setenv("REPLAY_WORKER_URL", "replay-worker-abc:8080")
    link = task_bodies.link_for({"job_id": "j", "token": "t"})
    assert link.base == "http://replay-worker-abc:8080"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v -k "ping_end or bare_host"`
Expected: FAIL, `ModuleNotFoundError: No module named 'replay_tasks'`.

- [ ] **Step 3: Write the package**

```python
# replay_tasks/__init__.py
"""Render Workflow tasks for the replay worker (docs/superpowers/specs/2026-10-14-render-workflows-design.md)."""
```

```python
# replay_tasks/tasks.py
"""The task bodies, as plain functions of (args, link) so tests run them without the Render SDK; main.py
registers them with Render. Standard library only.

Every run gets `job_id` and `token` in its args. It downloads its inputs from the replay worker
(`GET /runs/{job}/input/{name}`), does its work, and posts one JSON answer (`POST /runs/{job}/result`). A
failure it can describe is posted as {"status": "failed", "error_kind", "error"}; one it can't (a kill, a
timeout) the worker learns from Render.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 1 << 20


class WorkerLink:
    def __init__(self, base_url: str, job_id: str, token: str, timeout_s: float = 120.0):
        self.base = base_url if base_url.startswith("http") else f"http://{base_url}"
        self.base = self.base.rstrip("/")
        self.job_id, self.token, self.timeout_s = job_id, token, timeout_s

    def _url(self, route: str) -> str:
        return f"{self.base}/runs/{urllib.parse.quote(self.job_id)}/{route}"

    def fetch(self, name: str, dest: Path) -> Path:
        request = urllib.request.Request(self._url(f"input/{urllib.parse.quote(name, safe='')}"),
                                         headers={"X-Run-Token": self.token})
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response, dest.open("wb") as sink:
            shutil.copyfileobj(response, sink, CHUNK)
        return dest

    def fetch_text(self, name: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            return self.fetch(name, Path(folder) / "f").read_text(encoding="utf-8")

    def post_file(self, route: str, src: Path) -> None:
        src = Path(src)
        with src.open("rb") as body:
            request = urllib.request.Request(self._url(route), data=body, method="POST",
                                             headers={"X-Run-Token": self.token,
                                                      "Content-Length": str(src.stat().st_size),
                                                      "Content-Type": "application/octet-stream"})
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response.read()

    def post_result(self, result: dict) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            self.post_file("result", path)


def link_for(args: dict) -> WorkerLink:
    return WorkerLink(os.environ["REPLAY_WORKER_URL"], args["job_id"], args["token"])


def ping(args: dict, link: WorkerLink) -> None:
    link.post_result({"status": "ok", "echo": link.fetch_text("ping")})


TASKS = {"ping": ping}
```

`urlopen` with a file object as `data` streams it in blocks (it reads the file object; `Content-Length` is set explicitly).

```python
# replay_tasks/main.py
"""Registers the task bodies with Render Workflows. The image's start command:

    sh /srv/replay_worker/entrypoint.sh /opt/control-venv/bin/python -m replay_tasks.main

Every task runs on `flex` (spec D7) with its own timeout and retries (D8, D9)."""

from render.workflows import Retry, TaskContext, Workflows

from replay_tasks import tasks

app = Workflows(default_plan="flex")
NO_RETRY = Retry(max_retries=0, wait_duration_ms=1000)


@app.task(name="ping", timeout_seconds=60, retry=NO_RETRY)
def ping(ctx: TaskContext, args: dict) -> dict:
    tasks.ping(args, tasks.link_for(args))
    return {"ok": True}


# Later steps add their @app.task blocks HERE, above this line: app.start() registers the tasks defined when it
# runs (render/workflows/app.py, start), so a block below it would never be registered.

if __name__ == "__main__":
    app.start()
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_runs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add replay_tasks webapp/tests/replays/test_replay_worker_runs.py
git commit -m "replay tasks: the task package with a ping, end to end against a fake Render"
```

---

### Task 5: The image, the Blueprint and the deploy notes

**Files:**
- Modify: `replay_worker/Dockerfile`
- Modify: `render.yaml`
- Modify: `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`

**Interfaces:**
- Consumes: `replay_tasks/main.py` (Task 4).
- Produces: one image for both services; the workflow's start command above.

- [ ] **Step 1: The image**

In `replay_worker/Dockerfile`, extend the control venv's install line (keep `--only-binary=:all:`):

```dockerfile
RUN python3 -m venv /opt/control-venv \
 && /opt/control-venv/bin/pip install --no-cache-dir --only-binary=:all: numpy==2.4.6 scipy==1.18.1 Pillow==10.4.0 \
    render==1.0.1
```

After `COPY replay_worker /srv/replay_worker` add:

```dockerfile
COPY replay_tasks /srv/replay_tasks
```

and extend the smoke test's import list with `replay_tasks.tasks, replay_tasks.main` (the `RUN PYTHONPATH=... -c "import ..."` line), so a missing SDK fails the build.

- [ ] **Step 2: The Blueprint**

In `render.yaml`, under `replay-worker`'s `envVars`, add:

```yaml
      # Render Workflows (docs/superpowers/specs/2026-10-14-render-workflows-design.md). The workflow service
      # (replay-tasks) is created by hand in the dashboard: Render can't create a Docker workflow from a
      # Blueprint.
      - key: RENDER_API_KEY
        sync: false
      - key: REPLAY_WORKFLOW_SLUG
        value: replay-tasks
      - key: REPLAY_TASK_STARTS_PER_MIN
        value: "1000"
      # The three switches are the owner's, set in the dashboard (spec D2): `sync: false`, so a Blueprint sync
      # never resets one that was flipped to workflow. Unset, each is "local" (server.py's default).
      - key: REPLAY_PARSE_RUNNER
        sync: false
      - key: REPLAY_CONTROL_RUNNER
        sync: false
      - key: REPLAY_HEIGHTS_RUNNER
        sync: false
```

- [ ] **Step 3: The deploy notes**

Add a section "Render Workflows (replay-tasks)" to `webapp/RENDER_DEPLOY.md`:

```markdown
## Render Workflows (`replay-tasks`)

Design: `docs/superpowers/specs/2026-10-14-render-workflows-design.md`. One-time setup:

1. Dashboard → New → Workflow. Repo: this one. Language: Docker. Dockerfile path: `replay_worker/Dockerfile`,
   context: repo root. Region: the same as `replay-worker`. Name/slug: `replay-tasks`.
   Start command: `sh /srv/replay_worker/entrypoint.sh /opt/control-venv/bin/python -m replay_tasks.main`.
2. Its env: `REPLAY_WORKER_URL` = the worker's private address (`replay-worker`'s "Internal address",
   host:port, e.g. `replay-worker-xxxx:8080`).
3. Dashboard → Account settings → API keys → create one; put it in `replay-worker`'s `RENDER_API_KEY`.
4. After both deploy: `replay-worker` → Shell → `python3 -m replay_worker.runs ping`. Expect
   `{"ok": true, ...}`. The workflow's Runs page shows one `ping` run.

Each kind (parse, control, heights) moves with its own switch on `replay-worker`:
`REPLAY_<KIND>_RUNNER=workflow`; set it back to `local` to roll back. The switches are `sync: false` in
`render.yaml`: the dashboard is where they live, and a Blueprint sync leaves them alone. After every later
merge, check `/health`'s `runners` still shows the kinds already moved.
```

Add the same three settings and the run routes to `replay_worker/README.md`'s HTTP table, with a pointer to the spec.

- [ ] **Step 4: Add the ping CLI**

Append to `replay_worker/runs.py`:

```python
def _ping_cli() -> int:
    port = os.environ.get("REPLAY_WORKER_PORT", "8080")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/runs/ping", data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=330) as response:
            print(response.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')}")
        return 1


if __name__ == "__main__":
    sys.exit(_ping_cli() if sys.argv[1:] == ["ping"] else 2)
```

- [ ] **Step 5: Run the whole replays suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add replay_worker/Dockerfile replay_worker/runs.py render.yaml replay_worker/README.md webapp/RENDER_DEPLOY.md
git commit -m "replay tasks: image, Blueprint settings and the workflow's setup notes"
```

- [ ] **Step 7: On Render (the owner, after merge)**

Follow `RENDER_DEPLOY.md`'s new section. Done when the ping prints `"ok": true`. The REST paths are confirmed
against the SDK; if the start is refused, read the error body for the task slug (`replay-tasks/ping`), the task's
registration (the workflow's Tasks page lists `ping`) or the API key, and fix that.

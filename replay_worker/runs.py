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

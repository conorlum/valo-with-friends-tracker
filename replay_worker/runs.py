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

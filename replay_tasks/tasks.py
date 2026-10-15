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
import subprocess
import sys
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


REPO = Path(__file__).resolve().parents[1]
WEBAPP = REPO / "webapp"


def _child_env(cache: Path) -> dict:
    from replay_worker.server import CHILD_THREADS

    env = {**os.environ, **CHILD_THREADS, "CONTROL_CACHE_DIR": str(cache)}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(WEBAPP), str(REPO), env.get("PYTHONPATH")) if p)
    return env


def _run_child(command_env: str, default: list[str], stdin: bytes, cache: Path) -> dict:
    """Runs a worker child (control_job or height_job) exactly as the worker does, and returns its answer."""
    from replay_worker.server import _child_setup

    command = json.loads(os.environ[command_env]) if os.environ.get(command_env) else default
    kwargs = {"input": stdin, "capture_output": True, "env": _child_env(cache), "cwd": str(REPO)}
    if os.name != "nt":
        kwargs["preexec_fn"] = _child_setup(int(os.environ.get("REPLAY_CONTROL_MEMORY_MB", "2048")))
    done = subprocess.run(command, **kwargs)
    try:
        answer = json.loads(done.stdout or b"")
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return {"status": "failed", "error_kind": "infra",
                "error": f"control child exited {done.returncode} without an answer"}
    return answer


def _cache_files(cache: Path) -> dict[str, str]:
    """Every .npz under the run's cache folder, by relative name, with a hash of its bytes: feature-row caches are
    rewritten in place under the same name (app/control/feature_visibility.py, persist), so a name isn't enough."""
    import hashlib

    out = {}
    for path in cache.rglob("*.npz"):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(CHUNK), b""):
                digest.update(chunk)
        out[path.relative_to(cache).as_posix()] = digest.hexdigest()
    return out


def _fetch_inputs(link: WorkerLink, names: list[str], cache: Path) -> None:
    """The worker's cached files for this run. One that can't be fetched (pruned since the run was opened) is
    skipped: the child rebuilds what it needs (spec D6)."""
    for name in names:
        try:
            link.fetch(name, cache / name)
        except OSError:
            (cache / name).unlink(missing_ok=True)


def _post_caches(link: WorkerLink, cache: Path, before: dict[str, str], map_name: str) -> list[str]:
    """Every cache file the child created or changed, before the result: once the result is in, the worker
    closes the run and refuses anything else from it. Returns the names that could not be posted."""
    from replay_worker import control_cache

    unsent = []
    after = _cache_files(cache)
    for name in sorted(n for n, digest in after.items() if before.get(n) != digest):
        if control_cache.valid(name, map_name):
            try:
                link.post_file(f"cache/{urllib.parse.quote(name, safe='')}", cache / name)
            except OSError:
                unsent.append(name)     # the answer names it, so the worker doesn't count the map as warm (D6)
    return unsent


def control(args: dict, link: WorkerLink) -> None:
    work = Path(tempfile.mkdtemp(prefix="control-"))
    try:
        cache = work / "cache"
        cache.mkdir()
        _fetch_inputs(link, args.get("files", []), cache)
        before = _cache_files(cache)
        stdin = link.fetch("task", work / "task.json").read_bytes()
        answer = _run_child("REPLAY_CONTROL_CMD", [sys.executable, "-m", "replay_worker.control_job"], stdin, cache)
        unsent = _post_caches(link, cache, before, args["map"])
        if unsent:
            answer = {**answer, "cache_unsent": unsent}
        link.post_result(answer)
    finally:
        shutil.rmtree(work, ignore_errors=True)


TASKS = {"ping": ping, "control": control}

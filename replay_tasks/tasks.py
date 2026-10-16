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
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 1 << 20


class ShortInput(OSError):
    """A download ended before its Content-Length (or had none): the input is not whole."""


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
            expected = response.headers.get("Content-Length")
            shutil.copyfileobj(response, sink, CHUNK)
            got = sink.tell()
        # A worker that restarts mid-download ends the body early, and urllib doesn't notice: a short file must
        # never be parsed or unpacked as if it were whole.
        if expected is None or not expected.isdigit() or got != int(expected):
            dest.unlink(missing_ok=True)
            raise ShortInput(f"input {name}: got {got} bytes, the worker sent Content-Length {expected}")
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


def _run_child(command_env: str, default: list[str], stdin: bytes, cache: Path, what: str = "control child") -> dict:
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
                "error": f"{what} exited {done.returncode} without an answer"}
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


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recipe() -> str:
    from app.replays.contract import current_recipe
    return current_recipe()


def parse(args: dict, link: WorkerLink) -> None:
    """One upload or re-parse: the same parse and condense the worker runs locally (replay_worker/parse_job.py)."""
    for folder in (str(WEBAPP), str(REPO)):
        if folder not in sys.path:
            sys.path.insert(0, folder)
    from app.replays.contract import ContractError
    from replay_worker import parse_job
    from replay_worker.server import Settings

    work = Path(tempfile.mkdtemp(prefix="parse-"))
    try:
        try:
            mine = _recipe()
            # A worker that can't name its recipe still parses (server.py Worker.__init__): skew can't be
            # checked then, and refusing would only fail every upload at its deadline.
            if args.get("recipe") is not None and mine != args["recipe"]:
                link.post_result({"status": "failed", "error_kind": "deploy",
                                  "error": f"this run has recipe {mine}, the worker {args['recipe']}"})
                return
            settings = Settings.from_env()
            vrf = link.fetch("vrf", work / "upload.vrf")
            got = _sha256(vrf)
            if got != args["sha256"]:      # not the upload (cut short or replaced): never parse it as if it were
                raise OSError(f"the downloaded file's sha256 is {got}, the upload's {args['sha256']}")
        except Exception as error:  # noqa: BLE001 - say what happened rather than die silently
            link.post_result({"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}"})
            return
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


def heights(args: dict, link: WorkerLink) -> None:
    """One map's height build: today's height_job child on the build's rounds, unpacked from the worker's spool."""
    from replay_worker import control_cache

    work = Path(tempfile.mkdtemp(prefix="heights-"))
    try:
        cache = work / "cache"
        cache.mkdir()
        _fetch_inputs(link, args.get("files", []), cache)          # step 2: a file it can't fetch is skipped
        before = _cache_files(cache)
        task = json.loads(link.fetch("task", work / "task.json").read_text(encoding="utf-8"))
        try:
            rounds = control_cache.unpack(link.fetch("rounds", work / "rounds.tar"), work / "rounds")
        except (ValueError, OSError, tarfile.TarError) as error:
            link.post_result({"status": "failed", "error_kind": "infra", "key": task.get("key"),
                              "error": f"the build's rounds could not be unpacked: {error}"})
            return
        task["dir"] = str(rounds)
        answer = _run_child("REPLAY_HEIGHT_CMD", [sys.executable, "-m", "replay_worker.height_job"],
                            json.dumps(task).encode("utf-8"), cache, what="height child")
        unsent = _post_caches(link, cache, before, args["map"])      # step 2: new or changed, before the result
        if unsent:      # as control does: the worker must never read a missing cache as one that arrived (D6)
            answer = {**answer, "cache_unsent": unsent}
        link.post_result(answer)
    finally:
        shutil.rmtree(work, ignore_errors=True)


TASKS = {"ping": ping, "control": control, "parse": parse, "heights": heights}

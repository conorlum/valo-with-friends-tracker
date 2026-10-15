# Render Workflows, step 2: map control on Workflows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With `REPLAY_CONTROL_RUNNER=workflow`, every map-control round runs as its own Workflow run (up to 40 at once), and the site sends rounds one match at a time up to 40, both limits tunable.

**Architecture:** `ControlRunner._run` gets a second path: instead of a child process it opens a run with the round's task and the map's cached files as inputs, waits, and handles the answer exactly as it handles a child's stdout. The map's visibility caches move to the worker's disk and are mirrored into each run; a run posts any cache it built back before its result. The site's dispatcher gets two settings in place of `IN_FLIGHT`.

**Tech Stack:** Python 3.13 standard library, pytest, the step-1 run table (`replay_worker/runs.py`), pydantic-settings on the site.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (D5, D6, D7, D8, D9, D17)

**Depends on:** step 1 merged and its ping passing on Render.

## Global Constraints

- `CONTROL_MAX_MATCHES` = 1 and `CONTROL_MAX_RUNS` = 40 on the site (D5); "match" means `replay_id`.
- Worker: `REPLAY_CONTROL_RUNS` = 40 (runs at once in workflow mode; `REPLAY_CONTROL_WORKERS` stays the local pool of 2); `REPLAY_CONTROL_QUEUE` = 64.
- `control` task: plan `flex`, `timeout_seconds=1800`, `Retry(max_retries=1, ...)` (D7-D9), defined above `main.py`'s `if __name__ == "__main__":` block.
- Caches (D6): newest 3 per map per folder (root and `feature-rows/`); a file that would take the cache folder past `control_cache.RESERVE` (2 GiB) is refused; a run posts every cache file it created **or changed**, before its result; a map is warm only once its visibility cache is on the worker's disk; a run that can't fetch a cache file skips it.
- Preemption follows where **parsing** runs (D17): a local parse preempts local children only; it never touches a Workflow run.
- `REPLAY_CONTROL_RUNNER` is `sync: false` (step 1) and stays unset (local) at merge; the owner flips it in the dashboard.
- Uploads into the cache go to `<cache>/.incoming/` (same filesystem as the cache, so installing one is a rename); `.incoming` is emptied when the server starts.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`. Test servers close with `shutdown()` and `server_close()` in `finally`.

## Review Focus

- A run that builds a visibility cache must post it **before** its result: after the result the run is closed and a cache post is refused. (A cache file that was already being uploaded when the run settled may still land: caches are named by their content's identity, so it is still correct, D6.)
- A parse arriving while 40 workflow runs are going must not mark them preempted (their results would be thrown away and re-queued forever); with heights still local, the same parse must still preempt a local height build.
- A cache file name posted by a run that isn't `<Map>.<hex>.npz` (or under `feature-rows/`) for that run's map — e.g. `../archive/x.vrf` — must be refused.
- A feature-row cache rewritten in place by the run (same name, new rows) must reach the worker.
- A map whose warming round succeeded but whose cache upload failed must not be marked warm (40 runs would each rebuild it).
- Rolling back (`REPLAY_CONTROL_RUNNER=local`) with `REPLAY_CONTROL_RUNS=40` set must still run only `REPLAY_CONTROL_WORKERS` (2) children.

---

## File structure

| File | Responsibility |
| --- | --- |
| `replay_worker/control_cache.py` (new) | which cache files a control run gets; validating and storing a posted one; pruning |
| `replay_worker/archive.py` (modify) | `cache_reserve` out of the budget |
| `replay_worker/server.py` (modify) | `Settings.control_runs`; `ControlRunner` remote path, pool limit, no preempt; `POST /runs/{job}/cache/{name}`; wiring |
| `replay_tasks/tasks.py`, `replay_tasks/main.py` (modify) | the `control` task |
| `webapp/app/config.py`, `webapp/app/services/replay_control_remote.py` (modify) | the two limits |
| tests | `test_replay_worker_control_cache.py` (new), `test_replay_worker_control_workflow.py` (new), `test_control_remote.py` (modify) |
| `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md` | settings and the switch-over |

---

### Task 1: The cache files on the worker's disk

**Files:**
- Create: `replay_worker/control_cache.py`
- Modify: `replay_worker/archive.py` (`Archive.__init__`, `budget`, `intake_ok`)
- Test: `webapp/tests/replays/test_replay_worker_control_cache.py`

**Interfaces:**
- Produces: `KEPT = 3`; `RESERVE = 2 * 1024**3`; `inputs(cache_dir: Path, task: dict) -> dict[str, Path]` (names are paths relative to `cache_dir`, posix style); `valid(name: str, map_name: str) -> bool`; `store(cache_dir: Path, name: str, src: Path, reserve: int = RESERVE) -> bool` (False, with `src` deleted, when installing it would take the cache folder past `reserve`); `has_visibility(cache_dir: Path, map_name: str) -> bool`; `clear_incoming(cache_dir: Path) -> None`; `Archive.cache_reserve: int` (default 0).

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/replays/test_replay_worker_control_cache.py
"""The visibility caches a Workflow control run gets and posts back (replay_worker/control_cache.py)."""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from replay_worker import archive, control_cache  # noqa: E402


def touch(path: Path, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_inputs_are_the_maps_newest_caches_its_height_and_features(tmp_path):
    for i in range(5):
        touch(tmp_path / f"Ascent.{i:016x}.npz", age=i)
    touch(tmp_path / "Ascent.0000000000000009.tmp.npz")
    touch(tmp_path / "Haven.0000000000000001.npz")
    touch(tmp_path / "feature-rows" / f"Ascent.{'a' * 64}.npz")
    touch(tmp_path / "heights" / "Ascent.d1.height.npz")
    touch(tmp_path / "features" / "f1.json")
    got = control_cache.inputs(tmp_path, {"map": "Ascent", "height": "d1", "features": "f1"})
    assert sorted(got) == sorted([f"Ascent.{i:016x}.npz" for i in range(3)] +
                                 [f"feature-rows/Ascent.{'a' * 64}.npz", "heights/Ascent.d1.height.npz",
                                  "features/f1.json"])
    assert all(path.is_file() for path in got.values())


def test_inputs_skip_a_missing_height_or_features(tmp_path):
    assert control_cache.inputs(tmp_path, {"map": "Ascent", "height": "nope", "features": "nope"}) == {}


def test_valid_names_only_for_the_runs_map():
    assert control_cache.valid("Ascent.0123456789abcdef.npz", "Ascent")
    assert control_cache.valid(f"feature-rows/Ascent.{'b' * 64}.npz", "Ascent")
    for bad in ("Haven.0123456789abcdef.npz", "../archive/x.npz", "Ascent.0123456789abcdef.tmp.npz",
                "heights/Ascent.d1.height.npz", "Ascent.xyz.npz", "/abs/Ascent.0123456789abcdef.npz"):
        assert not control_cache.valid(bad, "Ascent"), bad


def test_store_replaces_atomically_and_prunes_to_kept(tmp_path):
    for i in range(3):
        touch(tmp_path / f"Ascent.{i:016x}.npz", age=10 + i)
    src = touch(tmp_path / "incoming.npz")
    control_cache.store(tmp_path, "Ascent.ffffffffffffffff.npz", src)
    names = sorted(p.name for p in tmp_path.glob("Ascent.*.npz"))
    assert len(names) == control_cache.KEPT and "Ascent.ffffffffffffffff.npz" in names
    assert "Ascent.0000000000000002.npz" not in names          # the oldest went
    assert not src.exists()


def test_store_refuses_a_file_past_the_reserve(tmp_path):
    touch(tmp_path / "Ascent.0000000000000001.npz")
    big = tmp_path / ".incoming" / "x.npz"
    big.parent.mkdir()
    big.write_bytes(b"y" * 100)
    assert control_cache.store(tmp_path, "Bind.0000000000000002.npz", big, reserve=50) is False
    assert not (tmp_path / "Bind.0000000000000002.npz").exists() and not big.exists()


def test_concurrent_stores_keep_kept_and_lose_nothing_mid_rename(tmp_path):
    import threading
    (tmp_path / ".incoming").mkdir()

    def put(i):
        src = tmp_path / ".incoming" / f"{i}.npz"
        src.write_bytes(b"z")
        assert control_cache.store(tmp_path, f"Ascent.{i:016x}.npz", src)
    threads = [threading.Thread(target=put, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(list(tmp_path.glob("Ascent.*.npz"))) == control_cache.KEPT


def test_has_visibility_and_clear_incoming(tmp_path):
    assert not control_cache.has_visibility(tmp_path, "Ascent")
    touch(tmp_path / f"feature-rows/Ascent.{'c' * 64}.npz")
    assert not control_cache.has_visibility(tmp_path, "Ascent")      # feature rows alone don't warm a map
    touch(tmp_path / "Ascent.0000000000000001.npz")
    assert control_cache.has_visibility(tmp_path, "Ascent")
    touch(tmp_path / ".incoming" / "left-over.npz")
    control_cache.clear_incoming(tmp_path)
    assert not any((tmp_path / ".incoming").iterdir())


def test_archive_budget_leaves_the_cache_reserve(tmp_path):
    a = archive.Archive(tmp_path, 100 * archive.GB, max_bytes=10, queue_size=1)
    before = a.budget()
    a.cache_reserve = control_cache.RESERVE
    assert before - a.budget() == control_cache.RESERVE
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_cache.py -v`
Expected: FAIL, `ImportError: cannot import name 'control_cache'`.

- [ ] **Step 3: Implement**

```python
# replay_worker/control_cache.py
"""Map control's caches on the worker's disk, for Workflow runs (spec 2026-10-14, D6). Standard library only.

A control run starts on a blank machine. It gets the map's newest visibility caches (`<Map>.<16 hex>.npz`,
app/control/geometry.py) and feature-row caches (`feature-rows/<Map>.<64 hex>.npz`,
app/control/feature_visibility.py), plus the round's height asset and feature artifact when the worker has them,
under the same relative names, and posts back any cache it created or changed. KEPT newest per map and folder
stay; the archive keeps RESERVE free for them, and a file that would take the folder past it is refused. Cache
files are named by their content's identity, so a late one is still correct (spec D6)."""

from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path

KEPT = 3
RESERVE = 2 * 1024 ** 3
_NAME = re.compile(r"^(?P<folder>feature-rows/)?(?P<map>[A-Za-z]+)\.(?P<key>[0-9a-f]{16}|[0-9a-f]{64})\.npz$")
_LOCK = threading.Lock()          # every install and prune; reads (inputs) need none: files are replaced atomically


def _tree_bytes(cache_dir: Path) -> int:
    total = 0
    for path in Path(cache_dir).rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass                  # pruned or replaced meanwhile
    return total


def _newest(folder: Path, map_name: str) -> list[Path]:
    found = [p for p in folder.glob(f"{map_name}.*.npz") if not p.name.endswith(".tmp.npz")
             and _NAME.match(p.name)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def inputs(cache_dir: Path, task: dict) -> dict[str, Path]:
    cache_dir = Path(cache_dir)
    map_name = str(task["map"])
    out: dict[str, Path] = {}
    for prefix, folder in (("", cache_dir), ("feature-rows/", cache_dir / "feature-rows")):
        for path in _newest(folder, map_name)[:KEPT]:
            out[prefix + path.name] = path
    if task.get("height"):
        path = cache_dir / "heights" / f"{map_name}.{task['height']}.height.npz"
        if path.is_file():
            out[f"heights/{path.name}"] = path
    if task.get("features"):
        path = cache_dir / "features" / f"{task['features']}.json"
        if path.is_file():
            out[f"features/{path.name}"] = path
    return out


def valid(name: str, map_name: str) -> bool:
    match = _NAME.match(name)
    return bool(match) and match["map"] == map_name


def store(cache_dir: Path, name: str, src: Path, reserve: int = RESERVE) -> bool:
    """Moves a posted cache file into place (a rename: `src` is in `<cache>/.incoming`) and prunes its map's folder
    to KEPT. False, with `src` deleted, when the cache folder would grow past `reserve`."""
    target = Path(cache_dir) / name
    map_name = _NAME.match(name)["map"]
    try:
        with _LOCK:
            replaced = target.stat().st_size if target.is_file() else 0
            # `src` already sits under cache_dir/.incoming, so the tree's total counts it once.
            if _tree_bytes(cache_dir) - replaced > reserve:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, target)
            for old in _newest(target.parent, map_name)[KEPT:]:
                old.unlink(missing_ok=True)
            return True
    finally:
        Path(src).unlink(missing_ok=True)


def has_visibility(cache_dir: Path, map_name: str) -> bool:
    """The map's visibility cache is here: a round of it won't have to build one (what "warm" means, D6)."""
    return bool(_newest(Path(cache_dir), map_name))


def clear_incoming(cache_dir: Path) -> None:
    """Part files a restart left behind."""
    shutil.rmtree(Path(cache_dir) / ".incoming", ignore_errors=True)
    (Path(cache_dir) / ".incoming").mkdir(parents=True, exist_ok=True)
```

In `archive.py`, `Archive.__init__` after `self.held_bytes = ...`:

```python
        self.cache_reserve = 0        # map control's caches on this disk (replay_worker/control_cache.py)
```

`budget()` subtracts it:

```python
    def budget(self) -> int:
        return (self.total - self.parse_reserve - ARCHIVE_SLACK - self.pending_bytes() - self.receipt_bytes()
                - self.held_bytes() - self.cache_reserve)
```

and in `intake_ok`, subtract `self.cache_reserve` on the right-hand side of the comparison:

```python
        return held + size <= self.total - ARCHIVE_SLACK - PARSE_RESERVE_FACTOR * self.max_bytes - self.cache_reserve
```

(`os.replace` in `store` needs `src` on the same filesystem as the cache: the route in Task 3 writes each upload to
`<cache>/.incoming/`, and `main()` calls `clear_incoming` at startup.)

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_archive.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/control_cache.py replay_worker/archive.py webapp/tests/replays/test_replay_worker_control_cache.py
git commit -m "replay worker: map control caches on the disk for workflow runs"
```

---

### Task 2: The `control` task

**Files:**
- Modify: `replay_tasks/tasks.py`, `replay_tasks/main.py`
- Test: `webapp/tests/replays/test_replay_worker_control_workflow.py` (new; Task 3 adds the worker side)

**Interfaces:**
- Consumes: `WorkerLink.fetch`, `.post_file`, `.post_result` (step 1); `control_cache.valid` (Task 1); `replay_worker.server.CHILD_THREADS`, `replay_worker.server._child_setup`.
- Produces: `tasks.control(args: dict, link: WorkerLink) -> None`; args are `{"job_id", "token", "map", "files": list[str]}`; `TASKS["control"]`. The child command is `REPLAY_CONTROL_CMD` (JSON list; default `[sys.executable, "-m", "replay_worker.control_job"]`), memory cap `REPLAY_CONTROL_MEMORY_MB` (default 2048).

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/replays/test_replay_worker_control_workflow.py
"""Map control as Workflow runs (spec 2026-10-14, step 2): the control task against a fake worker link, then
ControlRunner end to end through a fake Render. The child is a stub command, as in test_replay_worker_control."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks  # noqa: E402
from replay_worker import runs, server  # noqa: E402

STUB = """
import json, os, sys, pathlib, time
task = json.loads(sys.stdin.buffer.read())
if str(task["link"].get("mode", "")).startswith("sleep"):
    time.sleep(float(task["link"]["mode"].split(":")[1]))
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
had = sorted(p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file())
if task["link"].get("build"):
    (cache / f"{task['map']}.{'c' * 16}.npz").write_bytes(b"built")
if task["link"].get("learn"):              # feature rows learned: the same file, rewritten in place
    rows = cache / "feature-rows" / f"{task['map']}.{'d' * 64}.npz"
    rows.write_bytes(rows.read_bytes() + b"+more")
mode = task["link"].get("mode", "ok")
if mode == "engine":
    print(json.dumps({"status": "failed", "error_kind": "engine", "error": "no side"}))
else:
    print(json.dumps({"status": "ok", "data": "ZA==", "summary": "cw==", "key": task["key"], "had": had}))
"""


class FakeLink:
    def __init__(self, files: dict[str, bytes]):
        self.files, self.posted, self.result, self.order = files, {}, None, []

    def fetch(self, name, dest):
        if name not in self.files:
            raise OSError(f"404 {name}")             # as urllib's HTTPError (an OSError) for a pruned file
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        self.order.append(route)
        self.posted[route] = Path(src).read_bytes()

    def post_result(self, result):
        self.order.append("result")
        self.result = result


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")
    return path


def round_task(key="k1", map_name="Ascent", **link):
    return {"key": key, "map": map_name, "blob": "", "link": {"sides": {}, "db_deaths": [], **link}}


def test_control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first(stub):
    link = FakeLink({"task": json.dumps(round_task(build=True)).encode(),
                     "Ascent.0123456789abcdef.npz": b"old", "heights/Ascent.d1.height.npz": b"h"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent",
                   "files": ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["had"] == ["Ascent.0123456789abcdef.npz", "heights/Ascent.d1.height.npz"]
    assert link.order == [f"cache/Ascent.{'c' * 16}.npz", "result"]
    assert link.posted[f"cache/Ascent.{'c' * 16}.npz"] == b"built"


def test_control_task_posts_a_cache_rewritten_in_place(stub):
    rows = f"feature-rows/Ascent.{'d' * 64}.npz"
    link = FakeLink({"task": json.dumps(round_task(learn=True)).encode(), rows: b"rows"})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": [rows]}, link)
    route = "cache/" + rows.replace("/", "%2F")
    assert link.order == [route, "result"] and link.posted[route] == b"rows+more"


def test_control_task_skips_a_cache_file_it_cannot_fetch(stub):
    link = FakeLink({"task": json.dumps(round_task()).encode()})       # the listed cache was pruned meanwhile
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": ["Ascent.0123456789abcdef.npz"]}, link)
    assert link.result["status"] == "ok" and link.result["had"] == []


def test_control_task_passes_an_engine_failure_through(stub):
    link = FakeLink({"task": json.dumps(round_task(mode="engine")).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result == {"status": "failed", "error_kind": "engine", "error": "no side"}


def test_control_task_reports_a_child_that_prints_nothing_as_infra(tmp_path, monkeypatch):
    silent = tmp_path / "silent.py"
    silent.write_text("", encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(silent)]))
    link = FakeLink({"task": json.dumps(round_task()).encode()})
    tasks.control({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py -v`
Expected: FAIL, `AttributeError: module 'replay_tasks.tasks' has no attribute 'control'`.

- [ ] **Step 3: Implement the task body**

Add to `replay_tasks/tasks.py` (imports at the top: `subprocess`, `sys`):

```python
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


def _post_caches(link: WorkerLink, cache: Path, before: dict[str, str], map_name: str) -> None:
    """Every cache file the child created or changed, before the result: once the result is in, the worker
    closes the run and refuses anything else from it."""
    from replay_worker import control_cache

    after = _cache_files(cache)
    for name in sorted(n for n, digest in after.items() if before.get(n) != digest):
        if control_cache.valid(name, map_name):
            try:
                link.post_file(f"cache/{urllib.parse.quote(name, safe='')}", cache / name)
            except OSError:
                pass     # the worker then doesn't count the map as warm (D6); the next run rebuilds


def control(args: dict, link: WorkerLink) -> None:
    work = Path(tempfile.mkdtemp(prefix="control-"))
    try:
        cache = work / "cache"
        cache.mkdir()
        _fetch_inputs(link, args.get("files", []), cache)
        before = _cache_files(cache)
        stdin = link.fetch("task", work / "task.json").read_bytes()
        answer = _run_child("REPLAY_CONTROL_CMD", [sys.executable, "-m", "replay_worker.control_job"], stdin, cache)
        _post_caches(link, cache, before, args["map"])
        link.post_result(answer)
    finally:
        shutil.rmtree(work, ignore_errors=True)


TASKS = {"ping": ping, "control": control}
```

(Replace the earlier `TASKS = {"ping": ping}` line; `TASKS` stays the last statement of `tasks.py`, after every
body it names.)

In `replay_tasks/main.py`, add below the `ping` task and **above** the `if __name__ == "__main__":` block (a task
defined after `app.start()` runs is never registered):

```python
@app.task(name="control", timeout_seconds=1800, retry=Retry(max_retries=1, wait_duration_ms=5000))
def control(ctx: TaskContext, args: dict) -> dict:
    tasks.control(args, tasks.link_for(args))
    return {"ok": True}
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add replay_tasks webapp/tests/replays/test_replay_worker_control_workflow.py
git commit -m "replay tasks: the control task"
```

---

### Task 3: `ControlRunner` runs rounds as Workflow runs

**Files:**
- Modify: `replay_worker/server.py` (`Settings`, `ControlRunner.__init__`, `preempt`, `_next`, `_run`; handler `do_POST`; `main`)
- Test: `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `control_cache.inputs`, `.valid`, `.store`, `.RESERVE` (Task 1); `tasks.control` (Task 2); `runs.Runs` (step 1).
- Produces: `Settings.control_runs: int` (40, `REPLAY_CONTROL_RUNS`); `ControlRunner(settings, idle=None, runs_table=None)`; `ControlRunner.remote(job) -> bool`; route `POST /runs/{job}/cache/{name}` (header `X-Run-Token`): 200 stored, 409 no such open control run, 400 bad name or length, 413 over 64 MiB, 507 the cache is at its reserve.

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_control_workflow.py`:

```python
@pytest.fixture
def remote(tmp_path, stub):
    """A ControlRunner in workflow mode, its server, and a fake Render that runs tasks.control."""
    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_runs=40, workflow_slug="replay-tasks",
                               render_api_key="k", render_api_base=render.url)
    table = server.make_runs(settings)
    control = server.ControlRunner(settings, runs_table=table)
    worker = server.Worker(settings, on_parse=None)
    httpd = server.make_server(worker, control=control, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: tasks.TASKS[kind](args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    try:
        yield control, render, settings, base
    finally:
        httpd.shutdown()
        httpd.server_close()
        render.close()


def wait_running(control, at_least, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if control.counts()["running"] >= at_least:
            return
        time.sleep(0.02)
    raise AssertionError(f"never {at_least} running at once")


def wait_done(control, jobs, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if all(control.get(j.id).status in ("done", "failed") for j in jobs):
            return [control.get(j.id) for j in jobs]
        time.sleep(0.05)
    raise AssertionError("rounds didn't finish")


def test_rounds_run_as_workflow_runs_and_a_built_cache_lands_on_disk(remote):
    control, render, settings, _ = remote
    first = control.submit(round_task("k0", build=True))
    wait_done(control, [first])
    assert (settings.control_cache_dir / f"Ascent.{'c' * 16}.npz").read_bytes() == b"built"
    rest = [control.submit(round_task(f"k{i}")) for i in range(1, 6)]
    done = wait_done(control, rest)
    assert all(j.status == "done" for j in done)
    assert all(f"Ascent.{'c' * 16}.npz" in j.result["had"] for j in done)    # every later run got the cache
    assert {s["task"] for s in render.started} == {"replay-tasks/control"}


def test_engine_failure_settles_engine(remote):
    control, _, _, _ = remote
    job = control.submit(round_task("bad", mode="engine"))
    assert wait_done(control, [job])[0].error_kind == "engine"


def warm_up(control):
    """A first round that builds Ascent's visibility cache, so later rounds run side by side."""
    wait_done(control, [control.submit(round_task("warm", build=True))])


def test_a_parse_does_not_preempt_workflow_runs(remote):
    control, _, _, _ = remote
    warm_up(control)
    jobs = [control.submit(round_task(f"p{i}", mode="sleep:1.5")) for i in range(3)]
    wait_running(control, 3)
    assert control.preempt() == 0
    assert all(j.status == "done" for j in wait_done(control, jobs))
    assert control.counts()["preempted"] == 0


def test_runs_ignore_the_local_pool_size(remote):
    control, _, settings, _ = remote
    assert settings.control_workers == 2
    warm_up(control)
    control.idle = lambda: False                     # a parse is on: local mode would allow only one
    jobs = [control.submit(round_task(f"w{i}", mode="sleep:1.5")) for i in range(6)]
    wait_running(control, 6)                         # all six at once: more than the local pool of 2
    assert all(j.status == "done" for j in wait_done(control, jobs))


def test_a_local_parse_still_preempts_a_local_height_build(tmp_path, stub):
    """Control on Workflows, heights still local (spec D17): the local build is a victim, as today."""
    import subprocess
    render = FakeRender()
    try:
        settings = server.Settings(temp_root=tmp_path, control_runner="workflow", workflow_slug="w",
                                   render_api_key="k", render_api_base=render.url)
        control = server.ControlRunner(settings, runs_table=server.make_runs(settings))
        build = server.ControlJob("b1", "key", "Ascent", b"{}", kind="heights")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        with control.lock:
            control.jobs["b1"], control.running["b1"], control.procs["b1"] = build, False, process
        assert control.preempt() == 1 and "b1" in control.preempted
        assert process.wait(timeout=10) is not None
    finally:
        render.close()


def test_a_map_is_not_warm_when_its_cache_never_reached_the_worker(remote, monkeypatch):
    from replay_worker import control_cache
    control, _, settings, _ = remote
    monkeypatch.setattr(control_cache, "store", lambda *a, **k: False)     # the worker refuses every cache file
    job = control.submit(round_task("w0", build=True))
    assert wait_done(control, [job])[0].status == "done"
    assert job.warm_key not in control.warm
    assert not control_cache.has_visibility(settings.control_cache_dir, "Ascent")


def test_cache_route_refuses_other_maps_and_paths(remote, tmp_path):
    import urllib.error
    control, _, settings, base = remote
    control.jobs["jid"] = server.ControlJob("jid", "key", "Ascent", b"{}")
    run = control.runs.open("control", "jid", {}, timeout_s=30)
    link = tasks.WorkerLink(base, "jid", run.token)
    src = tmp_path / "x.npz"
    for bad in ("cache/Haven.0123456789abcdef.npz", "cache/..%2F..%2Farchive%2Fx.npz"):
        src.write_bytes(b"x")
        with pytest.raises(urllib.error.HTTPError) as refused:
            link.post_file(bad, src)
        assert refused.value.code == 400
    src.write_bytes(b"x")
    link.post_file("cache/Ascent.0123456789abcdef.npz", src)
    assert (settings.control_cache_dir / "Ascent.0123456789abcdef.npz").read_bytes() == b"x"
```

Add one rollback test at the end:

```python
def test_local_mode_keeps_the_local_pool_even_with_control_runs_set(tmp_path, stub):
    settings = server.Settings(temp_root=tmp_path, control_runs=40)
    control = server.ControlRunner(settings)
    assert not control.remote(server.ControlJob("j", "k", "Ascent", b"{}"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py -v`
Expected: the new tests FAIL (`Settings` has no `control_runs`; `ControlRunner` takes no `runs_table`).

- [ ] **Step 3: Settings**

`Settings`: add `control_runs: int = 40` beside `control_workers`, and in `from_env`:

```python
        settings.control_runs = max(1, int(env.get("REPLAY_CONTROL_RUNS", settings.control_runs)))
```

Document `REPLAY_CONTROL_RUNS` in the module docstring's configuration list: "runs at once with REPLAY_CONTROL_RUNNER=workflow (default 40); REPLAY_CONTROL_WORKERS is the local pool".

- [ ] **Step 4: `ControlRunner`**

Constructor:

```python
    def __init__(self, settings: Settings, idle=None, runs_table=None):
        ...                                          # unchanged lines
        self.runs = runs_table
```

New method:

```python
    def remote(self, job: ControlJob) -> bool:
        """This job runs as a Workflow run (spec 2026-10-14) rather than a child here."""
        return self.runs is not None and job.kind == "control" and self.settings.control_runner == "workflow"
```

`preempt()`: a Workflow run is never a victim (spec D17); local children are, as today. Replace its first line
inside the lock,

```python
            alive = [job_id for job_id in self.running if job_id not in self.preempted]
```

with

```python
            # Only children on this machine: a Workflow run takes no CPU from a parse (spec D17).
            alive = [job_id for job_id in self.running
                     if job_id not in self.preempted and not self.remote(self.jobs[job_id])]
```

The rest of `preempt` is unchanged: it keeps the first local control child and kills the other local children,
including a local height build. With every control round remote, `alive` holds only local height builds.

`_next()`: replace the first two lines

```python
        idle = self.idle()
        limit = self.settings.control_workers if idle else 1
```

with

```python
        idle = self.idle()
        if self.settings.control_runner == "workflow" and self.runs is not None:
            limit = self.settings.control_runs
        else:
            limit = self.settings.control_workers if idle else 1
```

`_run()`: inside the `try:` that starts the child, wrap the child in a branch. The current block

```python
            process = subprocess.Popen(settings.height_cmd if build else settings.control_cmd, **kwargs)
            with self.lock:
                self.procs[job.id] = process
                killed_before_start = job.id in self.preempted
            if killed_before_start:
                kill_tree(process)
            stdout, _ = process.communicate(job.task, timeout=timeout)
            answer = json.loads(stdout or b"{}")
```

becomes

```python
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
```

and the following `if answer.get("status") == "ok": ...` stays as it is.

In the settlement under the lock at the end of `_run`, the warm rule changes for remote rounds (spec D6): today's

```python
                if job.kind == "control":
                    if status == "done" or kind == "engine":
                        self.warm.add(job.warm_key)
                    else:
                        self.warm.discard(job.warm_key)
```

becomes

```python
                if job.kind == "control":
                    # A remote round's map is warm only once its visibility cache is on this disk: a run whose
                    # cache upload failed would otherwise release every other round to rebuild it.
                    cached = not self.remote(job) or control_cache.has_visibility(self.settings.control_cache_dir,
                                                                                  job.map)
                    if (status == "done" or kind == "engine") and cached:
                        self.warm.add(job.warm_key)
                    else:
                        self.warm.discard(job.warm_key)
```

Add:

```python
    def _run_remote(self, job: ControlJob, timeout: float) -> dict:
        """One round as a Workflow run: the task and the map's cached files are its inputs."""
        task = json.loads(job.task)
        folder = self.settings.temp_root / "runs-in" / job.id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / "task.json").write_bytes(job.task)
            files = {"task": folder / "task.json", **control_cache.inputs(self.settings.control_cache_dir, task)}
            run = self.runs.open("control", job.id, files, timeout)
            self.runs.start(run, {"map": job.map, "files": sorted(n for n in files if n != "task")})
            answer = self.runs.wait(run)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if answer.get("error_kind") == "timeout":
            answer = {**answer, "error_kind": "infra", "error": f"control timed out after {timeout:g} s"}
        return answer
```

Import `control_cache` beside `runs` (same try/except pattern as step 1).

- [ ] **Step 5: The cache route**

`make_handler` already receives `control`. Add to `Handler`:

```python
        def _run_cache(self):
            parts = self.path.split("/")             # ["", "runs", job, "cache", name]
            name = urllib.parse.unquote(parts[4]) if len(parts) == 5 else ""
            run = runs_table.accepts(parts[2], self.headers.get("X-Run-Token"))
            job = control.get(parts[2]) if control is not None and run is not None else None
            if run is None or job is None or run.kind != "control":
                self.close_connection = True
                return self._send(HTTPStatus.CONFLICT, {"error": "no open control run takes this file"})
            if not control_cache.valid(name, job.map):
                self.close_connection = True
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
                if not control_cache.store(control.settings.control_cache_dir, name, incoming):
                    return self._send(HTTPStatus.INSUFFICIENT_STORAGE, {"error": "the cache is at its reserve"})
                return self._send(HTTPStatus.OK, {"stored": name})
            finally:
                incoming.unlink(missing_ok=True)
```

The upload lands in `<cache>/.incoming/` so `store`'s `os.replace` stays on one filesystem. In `do_POST`'s `/runs/` block add, before the `/result` check:

```python
                if "/cache/" in self.path:
                    return self._run_cache()
```

- [ ] **Step 6: `main()`**

The cache's room has to be reserved **before** the Worker opens the archive and recovers its jobs. Add a setting
the Worker applies right after `open_archive` succeeds (`Settings.archive_extra_reserve: int = 0`; in
`Worker.__init__`, after `self.archive.held_bytes = self.held_bytes`, add
`self.archive.cache_reserve = settings.archive_extra_reserve`). Then in `main()`:

```python
    runs_table = make_runs(settings)
    if settings.archive_dir is not None and settings.control_cache_dir.resolve().is_relative_to(
            settings.archive_dir.resolve()):
        settings.archive_extra_reserve += control_cache.RESERVE
    control_cache.clear_incoming(settings.control_cache_dir)
    control = ControlRunner(settings, runs_table=runs_table) if settings.control_enabled else None
    # Preemption follows where parsing runs (spec D17): a local parse protects the CPU for local children;
    # ControlRunner.preempt itself never touches a Workflow run.
    local_parse = settings.parse_runner == "local" or runs_table is None
    worker = Worker(settings, on_parse=control.preempt if (control is not None and local_parse) else None)
```

(Step 3 adds `runs_table=runs_table` to this `Worker(...)` call.)

(Move the `runs_table = make_runs(settings)` line added in step 1 up here; keep a single one.)

- [ ] **Step 7: Run the worker tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker_heights.py -v`
Expected: all pass (the local-mode tests unchanged).

- [ ] **Step 8: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_control_workflow.py
git commit -m "replay worker: map control rounds as workflow runs"
```

---

### Task 4: The site's two limits

**Files:**
- Modify: `webapp/app/config.py`
- Modify: `webapp/app/services/replay_control_remote.py` (constants near line 90; `_send`; `_submit`; the `start()` log line; the module docstring's "Up to IN_FLIGHT" sentence)
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Produces: `settings.control_max_runs: int = 40`, `settings.control_max_matches: int = 1`; module names `IN_FLIGHT` (= `settings.control_max_runs`) and `MAX_MATCHES` (= `settings.control_max_matches`); `room_for(state: State, replay_id: int) -> bool`.

- [ ] **Step 1: Keep the existing tests on their old numbers**

The existing tests count `remote.IN_FLIGHT` rounds across several replays. Add at the top of `test_control_remote.py`, after the imports:

```python
@pytest.fixture(autouse=True)
def old_limits(monkeypatch):
    """These tests were written for 8 rounds from any replays; the match limit has its own tests below."""
    monkeypatch.setattr(remote, "IN_FLIGHT", 8)
    monkeypatch.setattr(remote, "MAX_MATCHES", 1000)
```

- [ ] **Step 2: Write the failing tests**

Append to `test_control_remote.py` (its fixtures `factory`, `db`, `linked` and its `FakeWorker` are defined at the top of the file; `linked` is one linked replay of `linked.round_count` rounds):

```python
def test_another_match_in_flight_holds_this_one_back(monkeypatch, factory, db, linked):
    monkeypatch.setattr(remote, "IN_FLIGHT", 40)
    monkeypatch.setattr(remote, "MAX_MATCHES", 1)
    worker, state = FakeWorker(answer=lambda task: None), remote.State()     # every job stays running
    worker.tasks["other"] = {"key": "999:1:fp", "map": linked.map_name}
    state.in_flight["999:1:fp"] = remote.InFlight("other", 999, 1, "fp", linked.map_name, 0.0)
    assert remote.cycle(factory, worker, state, now=0)["sent"] == 0
    del state.in_flight["999:1:fp"]                                          # that match finished
    assert remote.cycle(factory, worker, state, now=1)["sent"] == linked.round_count


def test_the_runs_limit_caps_one_match(monkeypatch, factory, db, linked):
    cap = linked.round_count - 2
    monkeypatch.setattr(remote, "IN_FLIGHT", cap)
    monkeypatch.setattr(remote, "MAX_MATCHES", 1)
    worker, state = FakeWorker(), remote.State()
    assert remote.cycle(factory, worker, state, now=0)["sent"] == cap
    second = remote.cycle(factory, worker, state, now=1)       # the first `cap` come back and are stored
    assert second["stored"] == cap and second["sent"] == 2


def test_room_for(monkeypatch):
    monkeypatch.setattr(remote, "IN_FLIGHT", 40)
    monkeypatch.setattr(remote, "MAX_MATCHES", 1)
    state = remote.State()
    state.in_flight["a"] = remote.InFlight("j", 7, 1, "fp", "Ascent", 0.0)
    assert remote.room_for(state, 7)
    assert not remote.room_for(state, 8)
```

(`InFlight`'s first six fields, `replay_control_remote.py:229-235`, are `job_id, replay_id, round_number,
fingerprint, map_name, sent_at`, as these tests pass them; the rest have defaults.)

- [ ] **Step 3: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_control_remote.py -v`
Expected: every test in the file errors in setup (the autouse `old_limits` fixture patches `MAX_MATCHES`, which
doesn't exist yet). After Step 4 they all pass.

- [ ] **Step 4: Implement**

`webapp/app/config.py`, beside `replay_control_remote`:

```python
    # Map control on the worker (spec 2026-10-14-render-workflows-design.md, D5): rounds in flight at once, and
    # from how many replays. Read once at startup.
    control_max_runs: int = 40
    control_max_matches: int = 1
```

`replay_control_remote.py`: replace `IN_FLIGHT = 8` with

```python
IN_FLIGHT = settings.control_max_runs        # rounds in flight at once (CONTROL_MAX_RUNS)
MAX_MATCHES = settings.control_max_matches   # replays those rounds may come from (CONTROL_MAX_MATCHES)
```

Add after `_sendable`:

```python
def room_for(state: State, replay_id: int) -> bool:
    """A round of `replay_id` may go out: its replay is already in flight, or fewer than MAX_MATCHES are."""
    flying = {f.replay_id for f in state.in_flight.values()}
    return replay_id in flying or len(flying) < MAX_MATCHES
```

In `_send`'s loop, right after the `IN_FLIGHT` check:

```python
        if not room_for(state, p.replay_id):
            continue                         # another match is in flight: this one waits its turn
```

Because `_order` ranks the newest match first, the first replay picked is the newest; the rest are skipped until it has nothing in flight. After the loop, `_send` must still report that work was left: change its final `return held_features` to

```python
    return held_features or any(not room_for(state, p.replay_id) for _, p in todo)
```

so the idle throttle (`PLAN_IDLE_S`) doesn't start while another match is waiting.

Update the module docstring's "Up to IN_FLIGHT at a time" to "Up to IN_FLIGHT (CONTROL_MAX_RUNS) at a time, from at most MAX_MATCHES (CONTROL_MAX_MATCHES) replays". The `start()` log line becomes:

```python
    log.info("map control dispatch on: every %ss, up to %d rounds from %d replays in flight", CYCLE_S, IN_FLIGHT,
             MAX_MATCHES)
```

- [ ] **Step 5: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_control_remote.py tests/replays/test_replay_reparse_auto.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/config.py webapp/app/services/replay_control_remote.py webapp/tests/replays/test_control_remote.py
git commit -m "map control: CONTROL_MAX_RUNS and CONTROL_MAX_MATCHES replace IN_FLIGHT"
```

---

### Task 5: Settings on Render and the switch-over

**Files:**
- Modify: `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`

- [ ] **Step 1: `render.yaml`**

On `valowithfriendstracker`:

```yaml
      # Map control limits (spec 2026-10-14, D5): rounds in flight, and from how many replays.
      - key: CONTROL_MAX_RUNS
        value: "40"
      - key: CONTROL_MAX_MATCHES
        value: "1"
```

On `replay-worker` (`REPLAY_CONTROL_RUNNER` stays the `sync: false` entry from step 1; the owner sets it in the
dashboard):

```yaml
      - key: REPLAY_CONTROL_RUNS
        value: "40"
      - key: REPLAY_CONTROL_QUEUE
        value: "64"
      # The control caches on the disk, so workflow runs share them and they survive deploys (spec D6).
      - key: CONTROL_CACHE_DIR
        value: /var/replay/control_cache
```

- [ ] **Step 2: Docs**

`RENDER_DEPLOY.md`, under the Workflows section, add "Map control on Workflows":

```markdown
### Map control on Workflows (step 2)

1. Merge; let both services deploy. The worker's control is still local; the site now sends one match at a time.
2. `replay-worker` → Environment → `REPLAY_CONTROL_RUNNER=workflow` → save (redeploys).
3. Check `/health` through the site's admin page or the worker's shell (`curl localhost:8080/health`):
   `runners.control` is `workflow`, `workflow.last_error` is null.
4. Watch the next match: the workflow's Runs page shows its rounds; the first round of a map runs alone.
5. Compare one round with a local computation. Pull Render down to local ("Pulling Render down to local" above),
   pick a match whose rounds the workflow computed (its `match_uuid` and a round `N`), then in `webapp\`:
   - record the workflow's bytes:
     `docker exec -i <local postgres container> psql -U <user> -d <db> -c "SELECT encode(sha256(c.data), 'hex')
     FROM replay_round_control c JOIN replays r ON r.id = c.replay_id WHERE r.match_uuid = '<uuid>' AND
     c.round_number = <N>"` (or the same query in any SQL client on the local DB);
   - recompute that one round locally: `.\.venv\Scripts\python.exe scripts\compute_control.py --match <uuid>
     --round <N> --force --workers 1`;
   - run the query again. The two hashes must be equal. (This overwrites only the local copy; Render is untouched.)
6. Roll back: `REPLAY_CONTROL_RUNNER=local`.
Tune with `CONTROL_MAX_RUNS` / `CONTROL_MAX_MATCHES` (site) and `REPLAY_CONTROL_RUNS` (worker); keep the
worker's at least the site's. After every later merge, check `/health`'s `runners.control` is still `workflow`.
```

`replay_worker/README.md`: a "Map control on Workflows" paragraph naming `control_cache.py`, the cache route and `REPLAY_CONTROL_RUNS`.

- [ ] **Step 3: Full suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add render.yaml replay_worker/README.md webapp/RENDER_DEPLOY.md
git commit -m "map control on workflows: settings and the switch-over notes"
```

- [ ] **Step 5: On Render (the owner)**

Follow "Map control on Workflows". Done when a new match's rounds come back from workflow runs, one round
compares equal to a local computation, and the worker's memory graph during 40 runs is noted for step 5.

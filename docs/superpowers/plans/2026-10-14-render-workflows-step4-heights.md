# Render Workflows, step 4: height builds on Workflows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With `REPLAY_HEIGHTS_RUNNER=workflow`, a map's height build runs as one Workflow run as soon as it is due, instead of a child that must wait for the worker to be idle, and up to `HEIGHTS_MAX_BUILDS` maps build at once.

**Architecture:** The site still opens a build, sends its rounds in batches and starts it (`HeightBuilds`, unchanged). When the build starts, `ControlRunner` packs the spooled rounds into one tar and runs the build as a `heights` run, the same way step 2 runs a control round: the task and the map's cached files are inputs, the run unpacks the tar, runs today's `height_job` child, posts any cache it built and then its answer.

**Tech Stack:** Python 3.13 standard library (`tarfile` with the `data` filter), pytest, the step-1 run table, step 2's `control_cache` and `_run_remote`.

**Spec:** `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (D6, D7, D8, D9)

**Depends on:** step 2 merged (it adds `control_cache.py`, the cache route, `ControlRunner._run_remote` and `tasks._run_child`).

## Global Constraints

- The `heights` task: plan `flex` (D7), `timeout_seconds=3600` (`REPLAY_HEIGHT_TIMEOUT_S`, D8), `Retry(max_retries=0)` (D9: the site resends builds).
- The build's answer is today's `height_job` answer, unchanged; the site checks it as today (`replay_heights_remote.py`).
- A workflow build neither waits for the worker to be idle nor runs alone, and a parse never preempts it.
- The unit is one map (spec D16). Site: `HEIGHTS_MAX_BUILDS` = 4 builds open at once, one per map. Worker: `REPLAY_HEIGHT_BUILDS_MAX` = 4 collecting at once (today's `HEIGHT_COLLECTING_MAX = 2` stays the default for local), `REPLAY_HEIGHT_SPOOL_BYTES` = 2 GiB for every build's rounds together.
- `REPLAY_HEIGHTS_RUNNER` stays `local` in `render.yaml` at merge.
- Tests run from `webapp/` with `.\.venv\Scripts\python.exe -m pytest`.

## Review Focus

- A tar entry that escapes the unpack folder (`../x`, an absolute path, a link) must be refused, not written.
- With control `local` and heights `workflow`, a parse's preemption must not mark the remote build preempted (its answer would be thrown away and the build re-queued forever).
- A remote build must not block local control rounds from starting (today's "a build runs alone" rule is for a child on this machine).
- A build whose rounds are missing from the spool when it starts must fail with today's `inputs` answer, not a crash in packing.
- While a parse runs, `/health` must still read `idle: true` when builds are remote, or the site never opens a build (it waits for idle).
- One map's build failing (or going stale) must drop only that build and spend only its manifest's try; the other maps' builds carry on.
- When every map becomes due at once (a rules change), only `HEIGHTS_MAX_BUILDS` open; the next starts when one finishes, and every due map's rounds stay held until its own build ends.
- The `previous` height asset must reach the run when the worker has it (the report compares against it), and be absent otherwise.

---

## File structure

| File | Responsibility |
| --- | --- |
| `replay_worker/control_cache.py` (modify) | `pack(folder, dest)`; `unpack(tar, dest)` |
| `replay_worker/server.py` (modify) | `ControlRunner.remote`, `_next`, `preempt`, `_run_remote` for builds; the cache route accepts `heights` runs |
| `replay_tasks/tasks.py`, `replay_tasks/main.py` (modify) | the `heights` task |
| `webapp/app/services/replay_heights_remote.py`, `webapp/app/config.py` (modify) | several builds at once, one per map, capped by `HEIGHTS_MAX_BUILDS` |
| `replay_worker/server.py` `Settings`/`HeightBuilds` (modify) | `REPLAY_HEIGHT_BUILDS_MAX`, `REPLAY_HEIGHT_SPOOL_BYTES` |
| tests | `test_replay_worker_heights_workflow.py` (new), `test_replay_worker_control_cache.py` (modify) |
| `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md` | the switch-over |

---

### Task 1: Packing the spool

**Files:**
- Modify: `replay_worker/control_cache.py`
- Test: `webapp/tests/replays/test_replay_worker_control_cache.py`

**Interfaces:**
- Produces: `pack(folder: Path, dest: Path) -> Path` (an uncompressed tar of `folder`'s files, paths relative to it; the blobs are already gzip); `unpack(tar: Path, dest: Path) -> Path` (raises `ValueError` on any entry that isn't a plain file or folder inside `dest`).

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_control_cache.py`:

```python
import io
import tarfile

import pytest


def test_pack_and_unpack_round_trip(tmp_path):
    spool = tmp_path / "spool"
    (spool / "m1").mkdir(parents=True)
    (spool / "m1" / "1.json.gz").write_bytes(b"one")
    (spool / "m2").mkdir()
    (spool / "m2" / "12.json.gz").write_bytes(b"twelve")
    tar = control_cache.pack(spool, tmp_path / "rounds.tar")
    out = control_cache.unpack(tar, tmp_path / "out")
    assert (out / "m1" / "1.json.gz").read_bytes() == b"one"
    assert (out / "m2" / "12.json.gz").read_bytes() == b"twelve"


@pytest.mark.parametrize("name,kind", [("../evil", tarfile.REGTYPE), ("/abs/evil", tarfile.REGTYPE),
                                       ("m1/link", tarfile.SYMTYPE)])
def test_unpack_refuses_entries_outside_the_folder(tmp_path, name, kind):
    tar_path = tmp_path / "bad.tar"
    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        if kind == tarfile.SYMTYPE:
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        else:
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        control_cache.unpack(tar_path, tmp_path / "out")
    assert not (tmp_path / "evil").exists()


def test_pack_of_a_missing_folder_is_value_error(tmp_path):
    with pytest.raises(ValueError):
        control_cache.pack(tmp_path / "nope", tmp_path / "rounds.tar")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_cache.py -v`
Expected: the new tests FAIL (`pack` missing).

- [ ] **Step 3: Implement**

Add to `control_cache.py` (import `tarfile`):

```python
def pack(folder: Path, dest: Path) -> Path:
    """A height build's spooled rounds as one tar for its run (uncompressed: the blobs are gzip already)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"no rounds at {folder}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w") as tar:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(folder).as_posix(), recursive=False)
    return dest


def unpack(tar_path: Path, dest: Path) -> Path:
    """Unpacks a pack() tar; any entry that isn't a plain file or folder inside `dest` is refused."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r") as tar:
        members = tar.getmembers()
        for member in members:
            target = (dest / member.name).resolve()
            if not (member.isfile() or member.isdir()) or not target.is_relative_to(dest) or target == dest:
                raise ValueError(f"refused tar entry {member.name!r}")
        tar.extractall(dest, members=members, filter="data")
    return dest
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_control_cache.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/control_cache.py webapp/tests/replays/test_replay_worker_control_cache.py
git commit -m "replay worker: pack a height build's rounds for its run"
```

---

### Task 2: The `heights` task

**Files:**
- Modify: `replay_tasks/tasks.py`, `replay_tasks/main.py`
- Test: `webapp/tests/replays/test_replay_worker_heights_workflow.py` (new)

**Interfaces:**
- Consumes: `control_cache.unpack`, `.valid`; `tasks._run_child`, `tasks._cache_files` (step 2).
- Produces: `tasks.heights(args, link)`; args `{"job_id", "token", "map", "files": list[str]}`; the child command is `REPLAY_HEIGHT_CMD` (default `[sys.executable, "-m", "replay_worker.height_job"]`).

- [ ] **Step 1: Generalise `_run_child`'s failure text**

In `tasks.py`, change `_run_child`'s signature to `_run_child(command_env, default, stdin, cache, what="control child")` and its failure text to `f"{what} exited {done.returncode} without an answer"`. `control()` keeps calling it with the default.

- [ ] **Step 2: Write the failing tests**

```python
# webapp/tests/replays/test_replay_worker_heights_workflow.py
"""Height builds as Workflow runs (spec 2026-10-14, step 4): the heights task with a stub child, then a build
end to end through HeightBuilds, ControlRunner and a fake Render."""

import base64
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
from replay_worker import control_cache, server  # noqa: E402

STUB = """
import json, os, sys, pathlib
task = json.loads(sys.stdin.buffer.read())
folder = pathlib.Path(task["dir"])
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
rounds = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
prev = (cache / "heights" / f"{task['map']}.{task.get('previous')}.height.npz").is_file()
print(json.dumps({"status": "ok", "key": task["key"], "rounds_seen": rounds, "had_previous": prev,
                  "asset": "YQ=="}))
"""


class FakeLink:
    def __init__(self, files):
        self.files, self.result = files, None

    def fetch(self, name, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.files[name])
        return Path(dest)

    def post_file(self, route, src):
        pass

    def post_result(self, result):
        self.result = result


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / "stub_height.py"
    path.write_text(STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_HEIGHT_CMD", json.dumps([sys.executable, str(path)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")


def test_heights_task_unpacks_the_rounds_and_passes_previous(tmp_path, stub):
    spool = tmp_path / "spool"
    (spool / "m1").mkdir(parents=True)
    (spool / "m1" / "1.json.gz").write_bytes(b"r")
    tar = control_cache.pack(spool, tmp_path / "rounds.tar").read_bytes()
    task = {"key": "k", "map": "Ascent", "dir": "/worker/path/ignored", "previous": "d0"}
    link = FakeLink({"task": json.dumps(task).encode(), "rounds": tar, "heights/Ascent.d0.height.npz": b"p"})
    tasks.heights({"job_id": "j", "token": "t", "map": "Ascent", "files": ["heights/Ascent.d0.height.npz"]}, link)
    assert link.result["status"] == "ok"
    assert link.result["rounds_seen"] == ["m1/1.json.gz"] and link.result["had_previous"] is True


def test_heights_task_refuses_a_bad_tar_as_infra(tmp_path, stub):
    link = FakeLink({"task": b'{"key": "k", "map": "Ascent"}', "rounds": b"not a tar"})
    tasks.heights({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    assert link.result["status"] == "failed" and link.result["error_kind"] == "infra"
```

- [ ] **Step 3: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_heights_workflow.py -v`
Expected: FAIL, `AttributeError: module 'replay_tasks.tasks' has no attribute 'heights'`.

- [ ] **Step 4: Implement**

In `tasks.py`:

```python
def heights(args: dict, link: WorkerLink) -> None:
    """One map's height build: today's height_job child on the build's rounds, unpacked from the worker's spool."""
    from replay_worker import control_cache

    work = Path(tempfile.mkdtemp(prefix="heights-"))
    try:
        cache = work / "cache"
        cache.mkdir()
        for name in args.get("files", []):
            link.fetch(name, cache / name)
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
        for name in sorted(_cache_files(cache) - before):
            if control_cache.valid(name, args["map"]):
                try:
                    link.post_file(f"cache/{urllib.parse.quote(name, safe='')}", cache / name)
                except OSError:
                    pass
        link.post_result(answer)
    finally:
        shutil.rmtree(work, ignore_errors=True)
```

Add `import tarfile` at the top, and `"heights": heights` to `TASKS`.

In `main.py`:

```python
@app.task(name="heights", timeout_seconds=3600, retry=NO_RETRY)
def heights(ctx: TaskContext, args: dict) -> dict:
    tasks.heights(args, tasks.link_for(args))
    return {"ok": True}
```

- [ ] **Step 5: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_heights_workflow.py tests/replays/test_replay_worker_control_workflow.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add replay_tasks webapp/tests/replays/test_replay_worker_heights_workflow.py
git commit -m "replay tasks: the heights task"
```

---

### Task 3: `ControlRunner` runs builds as Workflow runs

**Files:**
- Modify: `replay_worker/server.py` (`ControlRunner.remote`, `preempt`, `_next`, `_run`'s timeout line is unchanged, `_run_remote`; handler `_run_cache`)
- Test: `webapp/tests/replays/test_replay_worker_heights_workflow.py`

**Interfaces:**
- Consumes: `control_cache.pack`, `.inputs` (Task 1, step 2); `tasks.heights` (Task 2).
- Produces: `ControlRunner.remote(job)` is true for a `heights` job when `heights_runner == "workflow"` (and for `control` as in step 2).

- [ ] **Step 1: Write the failing tests**

Append to `test_replay_worker_heights_workflow.py`:

```python
@pytest.fixture
def remote(tmp_path, stub):
    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               heights_runner="workflow", workflow_slug="replay-tasks", render_api_key="k",
                               render_api_base=render.url)
    table = server.make_runs(settings)
    control = server.ControlRunner(settings, runs_table=table)
    builds = server.HeightBuilds(settings, control)
    worker = server.Worker(settings)
    httpd = server.make_server(worker, control=control, heights=builds, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: tasks.TASKS[kind](args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    yield control, builds, render, settings, worker, base
    httpd.shutdown()
    render.close()


MATCH = "0123abcd-0000-0000-0000-000000000001"     # HeightBuilds.add accepts hex match ids only (MATCH_ID)


def open_and_fill(builds, map_name="Ascent"):
    manifest = {"map": map_name, "replays": [[MATCH, 1, "sha", 2]], "refinement_history": []}
    build = builds.open("key-1", map_name, 2, manifest)
    blob = base64.b64encode(b"round").decode()
    builds.add(build["id"], [{"match": MATCH, "n": 1, "blob": blob}, {"match": MATCH, "n": 2, "blob": blob}])
    return build


def wait_build(builds, build_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        body = builds.get(build_id)
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("build didn't finish")


def test_a_build_runs_as_a_workflow_run(remote):
    control, builds, render, _, _, _ = remote
    build = open_and_fill(builds)
    builds.start(build["id"], None)
    body = wait_build(builds, build["id"])
    assert body["status"] == "done"
    assert body["result"]["rounds_seen"] == [f"{MATCH}/1.json.gz", f"{MATCH}/2.json.gz"]
    assert [s["task"] for s in render.started] == ["replay-tasks/heights"]


def test_a_remote_build_does_not_block_control_or_get_preempted(remote):
    control, builds, _, _, _, _ = remote
    build = open_and_fill(builds)
    builds.start(build["id"], None)
    assert control.preempt() == 0
    body = wait_build(builds, build["id"])
    assert body["status"] == "done"
```

`HeightBuilds.add` checks only that a blob is non-empty base64; it never reads the round, and the stub child doesn't either.

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_heights_workflow.py -v`
Expected: the two new tests FAIL (the build runs the local `height_cmd` or waits for idle).

- [ ] **Step 3: Implement**

`ControlRunner.remote`:

```python
    def remote(self, job: ControlJob) -> bool:
        if self.runs is None:
            return False
        if job.kind == "heights":
            return self.settings.heights_runner == "workflow"
        return self.settings.control_runner == "workflow"
```

`preempt`: after the existing early return, exclude remote jobs from the victims:

```python
            alive = [job_id for job_id in self.running
                     if job_id not in self.preempted and not self.remote(self.jobs[job_id])]
```

`_next`: a remote build starts at once and takes no slot; local builds keep today's rules. At the top of `_next`, before the limit:

```python
        for job_id in self.pending:
            job = self.jobs[job_id]
            if job.kind == "heights" and self.remote(job):
                return job, False                    # another machine: it neither waits for idle nor runs alone
```

and change the two "a build runs alone" checks to look at local builds only:

```python
        if any(self.jobs[j].kind == "heights" and not self.remote(self.jobs[j]) for j in self.running):
            return None                              # a local build runs alone
```

and in the `limit` count, count only jobs that aren't remote builds:

```python
        running = [j for j in self.running if not (self.jobs[j].kind == "heights" and self.remote(self.jobs[j]))]
        if len(running) >= limit:
            return None
```

(replacing `if len(self.running) >= limit: return None`).

`_run_remote(job, timeout)` gains the build case:

```python
    def _run_remote(self, job: ControlJob, timeout: float) -> dict:
        task = json.loads(job.task)
        folder = self.settings.temp_root / "runs-in" / job.id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / "task.json").write_bytes(job.task)
            files = {"task": folder / "task.json"}
            if job.kind == "heights":
                try:
                    files["rounds"] = control_cache.pack(Path(task["dir"]), folder / "rounds.tar")
                except ValueError as missing:
                    return {"status": "failed", "error_kind": "inputs", "error": str(missing), "key": task.get("key")}
                files.update(control_cache.inputs(self.settings.control_cache_dir,
                                                  {"map": job.map, "height": task.get("previous")}))
            else:
                files.update(control_cache.inputs(self.settings.control_cache_dir, task))
            run = self.runs.open(job.kind, job.id, files, timeout)
            self.runs.start(run, {"map": job.map, "files": sorted(n for n in files if n not in ("task", "rounds"))})
            answer = self.runs.wait(run)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if answer.get("error_kind") == "timeout":
            what = "height build" if job.kind == "heights" else "control"
            answer = {**answer, "error_kind": "infra", "error": f"{what} timed out after {timeout:g} s"}
        return answer
```

The cache route (`_run_cache`) accepts a `heights` run as well as a `control` one: change `run.kind != "control"` to `run.kind not in ("control", "heights")`.

`/health`'s `idle` is what the site waits on before it opens a build (`replay_heights_remote._idle`, "one build at
a time, opened only while the worker reports idle"). A remote build takes nothing from a parse, so in that mode the
field reads true. Replace `body["idle"] = worker.idle()` with:

```python
                # "A height build may start now" (app/services/replay_heights_remote.py). A Workflow build runs on
                # another machine, so a parse here never holds it back.
                remote_builds = runs_table is not None and worker.settings.heights_runner == "workflow"
                body["idle"] = True if remote_builds else worker.idle()
```

and update the docstring line `"idle" (no parse running or waiting)` to `"idle" (a height build may start: no parse
running or waiting, or builds run as Workflow runs)`. Add to the tests (Step 1's fixture yields `worker` and `base`):

```python
def test_health_reads_idle_during_a_parse_when_builds_are_remote(remote, monkeypatch):
    import urllib.request
    _, _, _, _, worker, base = remote
    monkeypatch.setattr(worker, "idle", lambda: False)          # a parse is running
    with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
        assert json.loads(response.read())["idle"] is True
```

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_heights_workflow.py tests/replays/test_replay_worker_heights.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker_control_workflow.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_heights_workflow.py
git commit -m "replay worker: height builds as workflow runs"
```

---

### Task 4: Several maps' builds at once

**Files:**
- Modify: `replay_worker/server.py` (`Settings`, `from_env`, `HeightBuilds.open`, `main`)
- Modify: `webapp/app/config.py`
- Modify: `webapp/app/services/replay_heights_remote.py` (`HeightState`, `step`, the module docstring's "Sent, resumably")
- Test: `webapp/tests/replays/test_replay_worker_heights.py`, `webapp/tests/replays/test_heights_remote.py`

**Interfaces:**
- Produces (worker): `Settings.height_builds_max: int` (2; `REPLAY_HEIGHT_BUILDS_MAX`), `Settings.height_spool_bytes` read from `REPLAY_HEIGHT_SPOOL_BYTES`.
- Produces (site): `settings.heights_max_builds: int = 4`; `HeightState.builds: dict[str, Build]` (manifest key → build); `HeightState.build` (read-only: the only build when there is exactly one, else `None`, so the existing tests read as before); `MAX_BUILDS = settings.heights_max_builds`; `_try_build(session_factory, session, client, hstate, build, due, now, counts, held) -> None`.

- [ ] **Step 1: Write the failing tests**

Worker, in `test_replay_worker_heights.py` (its `worker` fixture takes `Settings` overrides):

```python
def test_collecting_builds_are_capped_by_the_setting(worker):
    control, builds, _ = worker(height_builds_max=3)
    for i, name in enumerate(("Ascent", "Bind", "Haven")):
        builds.open(f"k{i}", name, 1, MANIFEST)
    with pytest.raises(OverflowError):
        builds.open("k3", "Lotus", 1, MANIFEST)


def test_spool_bytes_come_from_the_environment():
    assert server.Settings.from_env({"REPLAY_HEIGHT_SPOOL_BYTES": "2147483648"}).height_spool_bytes == 2 ** 31
    assert server.Settings.from_env({"REPLAY_HEIGHT_BUILDS_MAX": "4"}).height_builds_max == 4
```

`HeightBuilds.open` checks only the map name's shape and that the manifest is a dict, so one `MANIFEST` serves every map.

Site, in `test_heights_remote.py`. Its `on` fixture gives two maps geometry (Ascent, the `linked` replay's map, and Bind). `clone` adds replays of a map, and two replays make a map's first build due (`FIRST_BUILD_MATCHES`):

```python
def second_map(db, replay, name="Bind", count=2):
    """`count` valid replays of another map with geometry (the `on` fixture's Bind), so two maps are due."""
    made = clone(db, replay, count)
    for copy in made:
        copy.map_name = name
    db.commit()
    return made


def test_two_due_maps_build_at_once(rig, factory, db, linked, monkeypatch):
    monkeypatch.setattr(hr, "MAX_BUILDS", 2)
    rig.settings.height_builds_max = 2
    clone(db, linked, 1)
    second_map(db, linked)
    rig.cycle(factory, step_s=1)
    assert {b.map_name for b in rig.state.heights.builds.values()} == {linked.map_name, "Bind"}
    rig.until(factory, lambda s: total(s, "heights_live") == 2, cycles=100, step_s=1)
    assert db.query(ControlHeight).count() == 2


def test_the_cap_opens_one_and_holds_both(rig, factory, db, linked, monkeypatch):
    monkeypatch.setattr(hr, "MAX_BUILDS", 1)
    clone(db, linked, 1)
    second_map(db, linked)
    session = factory()
    try:
        held = hr.step(factory, session, rig.client, rig.state.heights, 1.0, {})
    finally:
        session.close()
    assert len(rig.state.heights.builds) == 1
    assert held == {linked.map_name, "Bind"}         # one building, one waiting its turn: both maps' rounds wait


def test_one_maps_failure_leaves_the_other_building(rig, factory, db, linked, monkeypatch):
    monkeypatch.setattr(hr, "MAX_BUILDS", 2)
    rig.settings.height_builds_max = 2
    one_round_batches(db, monkeypatch)               # both builds stay collecting for several cycles
    clone(db, linked, 1)
    second_map(db, linked)
    rig.cycle(factory, step_s=1)
    builds = {b.map_name: b for b in rig.state.heights.builds.values()}
    builds["Bind"].opened_at = -10_000               # Bind's collecting is past COLLECT_STALE_S: its try fails
    counts = rig.cycle(factory, step_s=1)
    assert counts["heights_failed"] == 1
    assert [b.map_name for b in rig.state.heights.builds.values()] == [linked.map_name]
```

(`rig.settings` is the worker's live `Settings` object, so setting `height_builds_max` on it takes effect for the next `open`.)

- [ ] **Step 2: Run them to see them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_replay_worker_heights.py tests/replays/test_heights_remote.py -v`
Expected: the new tests FAIL (`height_builds_max` unknown; `HeightState` has no `builds`).

- [ ] **Step 3: The worker**

`Settings`: `height_builds_max: int = 2` beside `height_spool_bytes`. `from_env`:

```python
        settings.height_builds_max = max(1, int(env.get("REPLAY_HEIGHT_BUILDS_MAX", settings.height_builds_max)))
        settings.height_spool_bytes = int(env.get("REPLAY_HEIGHT_SPOOL_BYTES", settings.height_spool_bytes))
```

`HeightBuilds.open`: replace `>= HEIGHT_COLLECTING_MAX` with `>= self.settings.height_builds_max`, and delete the constant (update the class docstring's "at most HEIGHT_COLLECTING_MAX builds collect at once" to name the setting).

`main()`: when the spool (`settings.temp_root`) is on the archive disk, its cap joins the reserve:

```python
    if worker.archive is not None and settings.temp_root.resolve().is_relative_to(worker.archive.root.resolve()):
        worker.archive.cache_reserve += settings.height_spool_bytes
```

- [ ] **Step 4: The site**

`config.py`, beside `control_max_matches`:

```python
    # Height rebuilds open at once, one per map (spec 2026-10-14, D16); the worker's REPLAY_HEIGHT_BUILDS_MAX
    # must be at least this.
    heights_max_builds: int = 4
```

`replay_heights_remote.py`:

```python
MAX_BUILDS = settings.heights_max_builds     # maps whose rebuild is open at once (HEIGHTS_MAX_BUILDS)


@dataclass
class HeightState:
    builds: dict = field(default_factory=dict)     # manifest key -> Build, one per map, at most MAX_BUILDS
    tries: dict = field(default_factory=dict)       # manifest key -> (failures, last)

    @property
    def build(self) -> Build | None:
        """The only build, when exactly one is open (tests and logs)."""
        return next(iter(self.builds.values())) if len(self.builds) == 1 else None
    # failed / may_try / spent unchanged
```

Rewrite `step` around a per-build helper that holds today's `try`/`except` unchanged in substance:

```python
def _try_build(session_factory, session, client, hstate: HeightState, build: Build, due: dict, now: float,
               counts: dict, held: set) -> None:
    """One look at one map's build; its failure drops only it and spends only its manifest's try."""
    from app.services.replay_control_remote import Conflict, Unreachable, WorkerBusy

    try:
        if _advance(session_factory, session, client, build, now, counts):
            hstate.builds.pop(build.key, None)
            held.discard(build.map_name)
    except (Unreachable, WorkerBusy):
        counts["unreachable"] = counts.get("unreachable", 0) + 1
    except _Stale as stale:
        log.info("heights: %s build dropped: %s", build.map_name, stale)
        _cancel(client, build)
        hstate.builds.pop(build.key, None)
        counts["heights_stale"] += 1
    except (BuildFailed, Conflict, AttributeError, KeyError, TypeError, ValueError) as error:
        # The last four: a worker's answer that isn't shaped as the protocol says (`state.get` on a list, a
        # missing field). Handled here, where the build can be dropped; left to `cycle`'s catch-all the build
        # would stay and meet the same answer every cycle without spending a try. A database error is none of
        # these and still reaches `cycle`, which rolls back and takes its lock again.
        log.warning("heights: %s build failed: %s: %s", build.map_name, type(error).__name__, error)
        _cancel(client, build)
        hstate.builds.pop(build.key, None)
        hstate.failed(build.key, now)
        if hstate.spent(build.key):
            _retire_spent_evidence(session_factory, due[build.key], counts)
            held.discard(due[build.key].map_name)
        counts["heights_failed"] += 1


def step(session_factory, session, client, hstate: HeightState, now: float, counts: dict) -> set[str]:
    """One cycle's work on height rebuilds. Returns the maps whose rounds are held."""
    from app.services.replay_control_remote import Unreachable, WorkerBusy

    for key in COUNTS:
        counts.setdefault(key, 0)
    plan = plan_maps(session)
    for name in plan.off:
        if _turn_off(session_factory, name):
            counts["heights_off"] += 1
            log.info("heights: %s off: what its heights were built from is gone", name)
    due = {hi.key(d.manifest): d for d in plan.due}
    for key, d in due.items():
        if hstate.spent(key):
            _retire_spent_evidence(session_factory, d, counts)
    held = {d.map_name for key, d in due.items() if not hstate.spent(key)}
    for key, build in list(hstate.builds.items()):
        if key not in due:
            _cancel(client, build)                    # its inputs are no longer the map's
            del hstate.builds[key]
            counts["heights_stale"] += 1
    for build in list(hstate.builds.values()):
        _try_build(session_factory, session, client, hstate, build, due, now, counts, held)
    building = {b.map_name for b in hstate.builds.values()}
    ready = [d for key, d in due.items()
             if key not in hstate.builds and d.map_name not in building and hstate.may_try(key, now)]
    room = MAX_BUILDS - len(hstate.builds)
    if not ready or room <= 0:
        return held
    try:
        answer = client.health()
        if _other_deploy(answer):
            return {b.map_name for b in hstate.builds.values()}   # the deploy gap: open nothing, hold only what runs
        if not _idle(client, answer):
            return held
    except (Unreachable, WorkerBusy):
        counts["unreachable"] = counts.get("unreachable", 0) + 1
        return held
    except BuildFailed as error:                      # no boolean idle field: as today, a failed try for the first ready map
        log.warning("heights: %s build failed: %s", ready[0].map_name, error)
        hstate.failed(hi.key(ready[0].manifest), now)
        counts["heights_failed"] += 1
        return held
    actives = control_heights.active_digests(session)
    for d in ready[:room]:
        rounds = [(rid, uuid, n) for rid, uuid, count in d.replays for n in range(1, count + 1)]
        build = Build(d.map_name, hi.key(d.manifest), d.manifest, rounds, opened_at=now,
                      previous=actives.get(d.map_name))
        hstate.builds[build.key] = build
        log.info("heights: %s rebuild (%s), %d rounds", d.map_name, d.why, len(rounds))
        _try_build(session_factory, session, client, hstate, build, due, now, counts, held)
    return held
```

Two behaviours differ from today and are intended:
- With builds open, the deploy gap holds those builds' maps rather than nothing. Today nothing could be open at that point, so the old `return set()` held nothing.
- A missing `idle` field fails the first ready map's try, as today's `_idle` raising inside the old `try` did.

Read the old `step` side by side with this one. Every other branch maps one-to-one.

Find every other reader of `hstate.build` with `rg -n "heights\.build\b|hstate\.build\b" webapp/app` (the dispatcher's status page or logs, if any) and move it to `builds`.

The module docstring's "Sent, resumably. One build at a time, opened only while the worker reports idle" becomes "Sent, resumably. Up to MAX_BUILDS builds at once, one per map, opened only while the worker reports idle (always, once builds run as Workflow runs)".

- [ ] **Step 5: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays/test_heights_remote.py tests/replays/test_replay_worker_heights.py tests/replays/test_control_remote.py -v`
Expected: all pass, including every existing test (they see one build through `HeightState.build`).

- [ ] **Step 6: Commit**

```bash
git add replay_worker/server.py webapp/app/config.py webapp/app/services/replay_heights_remote.py webapp/tests/replays/test_heights_remote.py webapp/tests/replays/test_replay_worker_heights.py
git commit -m "heights: several maps' rebuilds at once, capped by HEIGHTS_MAX_BUILDS"
```

---

### Task 5: The switch-over

**Files:**
- Modify: `render.yaml`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md`

- [ ] **Step 0: `render.yaml`**

On `valowithfriendstracker`:

```yaml
      # Height rebuilds open at once, one per map (spec 2026-10-14, D16).
      - key: HEIGHTS_MAX_BUILDS
        value: "4"
```

On `replay-worker`:

```yaml
      - key: REPLAY_HEIGHT_BUILDS_MAX
        value: "4"
      - key: REPLAY_HEIGHT_SPOOL_BYTES
        value: "2147483648"
```

With heights still `local`, these let up to 4 builds collect while each still waits its turn to run alone, as today.

- [ ] **Step 1: Docs**

`RENDER_DEPLOY.md`, Workflows section:

```markdown
### Height builds on Workflows (step 4)

1. `replay-worker` → Environment → `REPLAY_HEIGHTS_RUNNER=workflow` → save.
2. Each due map (5 new matches since its last build) shows as a `heights` run on the workflow's Runs page as soon
   as it is due, up to `HEIGHTS_MAX_BUILDS` at once. `scripts/control_heights.py list` shows which maps are due.
3. Done when the site accepts a rebuild (`heights_live` in the dispatcher's counts, or `control_heights.py list`).
4. Tune with `HEIGHTS_MAX_BUILDS` (site); keep the worker's `REPLAY_HEIGHT_BUILDS_MAX` at least as large, and raise
   `REPLAY_HEIGHT_SPOOL_BYTES` if the worker answers "the spool is full".
5. Roll back: `REPLAY_HEIGHTS_RUNNER=local`.
```

`replay_worker/README.md`, "Height builds": with `REPLAY_HEIGHTS_RUNNER=workflow` a build is one Workflow run that neither waits for idle nor runs alone; the spool goes to it as one tar.

- [ ] **Step 2: Full suite and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests/replays -q`
Expected: all pass.

```bash
git add render.yaml replay_worker/README.md webapp/RENDER_DEPLOY.md
git commit -m "height builds on workflows: settings and the switch-over notes"
```

- [ ] **Step 3: On Render (the owner)**

Follow "Height builds on Workflows". Done when one rebuild is accepted. When two maps are due together, both
should show as running at once.

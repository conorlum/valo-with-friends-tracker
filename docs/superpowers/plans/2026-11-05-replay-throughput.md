# Replay Task Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep 30+ map-control Workflow runs busy when control needs recomputing, and stop losing work to
30-minute cancellations.

**Architecture:** A map's visibility cache becomes its own Workflow task, a *warm build*: one per warm key, with a
long time limit, saving the cache to the worker's disk. After that, every round of the key runs at once. The time
limits come off the 30-minute floor and count from when a run actually starts. Each run reports whether it found
the cache or built it, so a wrong "warm" heals itself. `/health` shows enough to ramp concurrency in measured
steps.

**Tech Stack:** Python 3.13 stdlib (`replay_worker/`, `replay_tasks/`), numpy (the control child), FastAPI site
(`webapp/app/services/`), Render Workflows (`render==1.0.1` SDK), pytest.

## Why (the evidence this plan rests on)

- Every cancelled run in the Render dashboard ended at 29:59. The worker cancels a run itself when its deadline
  passes (`replay_worker/runs.py` `Runs.wait`), and a control round's deadline is 30 minutes from when the worker
  *opens* it, queue time included (`Settings.control_timeout_s`, `replay_tasks/main.py` `timeout_seconds=1800`,
  site `STALE_JOB_S = 1800`).
- A map counts as cold until one of its rounds finishes **and** its visibility cache reaches the worker's disk. A
  cold map runs one round at a time (`ControlRunner._next`). A run posts its caches only after its round finishes
  (`replay_tasks/tasks.py` `control`), so a round killed at 29:59 throws away the cache it built. The map stays
  cold, the next round starts again from nothing, and so on: one run in progress, cancelled at 29:59, over and
  over.
- The worker's warm list lives only in memory, so every worker deploy makes every map cold.
- Render's own cap is not the limit: at least 20 concurrent runs per workspace (Hobby), 50 on Pro
  (https://render.com/docs/workflows-limits). A task's `timeout_seconds` may be 30 s to 24 h
  (https://render.com/docs/workflows-defining).

## Risks this plan mitigates (owner-approved, 2026-11-05)

| # | Risk | Mitigation (task) |
|---|------|-------------------|
| 1 | The 29:59 cancellations aren't cache builds | Task 0 gate; Task 1 makes every run report where its cache came from |
| 2 | An old-heights cache makes a map look warm, releasing 30+ rounds that each build the cache | A key is warm only by the **exact file name** its warm build reported, while that file is on disk; a round that still had to build drops the key (Task 4) |
| 3 | The 2 GB cache budget makes maps evict each other at higher concurrency | `/health` shows the bytes per map and an eviction count; the reserve is configurable (Tasks 1, 6); the ramp stops on any eviction (Task 7) |
| 4 | The worker or site becomes the bottleneck | `/health` shows open runs by kind and the oldest one's age; the ramp goes in steps with stop criteria (Tasks 1, 7) |
| 5 | Parallel rounds overwrite each other's feature-row caches | A smaller feature-row file never replaces a larger one (Task 6). Correctness is already guarded: `test_feature_visibility_cache.py::test_cached_visibility_matches_original_*` proves cached rows give the uncached answers |
| 6 | Long limits hide hung runs | The limits are finite (2 h a round, 6 h a warm build, 23 h a height build); Render's own limit sits above each; `/health` shows the oldest open run per kind (Tasks 1, 5) |
| 7 | Three services must agree during the rollout | Warm builds sit behind `REPLAY_WARM_BUILDS` (off by default, `sync: false`); the `started` post is best effort both ways (Tasks 2, 4, 7) |
| 8 | Test drift | The control output is unchanged (scheduling only), so no revision bump; each task runs `scripts/test_catalog.py plan` for its files (every task) |

## Global Constraints

- Repo is PUBLIC: no credentials, no secrets, no dashboard values beyond what `render.yaml` already holds.
- `replay_worker/server.py`, `replay_worker/runs.py`, `replay_worker/control_cache.py` and `replay_tasks/tasks.py`
  stay **standard library only**. Only `replay_worker/control_job.py`, `replay_worker/warm_job.py` and `app.control`
  may import numpy, and only inside the child (`tests/replays/test_control_isolation.py`).
- Never change what a control round computes: no change to `app/control/engine.py`, `CONTROL_REVISION`,
  `GEOMETRY_VERSION` or `cache_key`. This is scheduling only.
- Local mode (`REPLAY_CONTROL_RUNNER=local`) keeps today's behaviour exactly. Warm builds apply only when
  `control_runner == "workflow"`, a run table exists, and `REPLAY_WARM_BUILDS` is on.
- Never merge. The furthest this goes is an open PR whose link goes to the owner.
- Tests run from `webapp/` with the main checkout's interpreter (worktrees have no venv):
  `../../../../webapp/.venv313/Scripts/python.exe -m pytest <files> -n 0 -q` from
  `<worktree>/webapp`. Before each commit, `python scripts/test_catalog.py plan --git` names any other suites the
  changed files need; run them too.
- New tests go into the existing test files named in each task (the catalog `tests/suites.toml` already lists them).
  A new file under `replay_worker/` is already guarded by `"../replay_worker/**"`.
- Time limits, in one place (Task 5 pins their order in a test):

  | What | Worker (`render.yaml` env) | Render task `timeout_seconds` | Site backstop |
  |---|---|---|---|
  | control round | `REPLAY_CONTROL_TIMEOUT_S` 7200 | 7500 | `STALE_JOB_S` 15000 (> 2 x 7200) |
  | warming round (fallback only) | `REPLAY_CONTROL_WARM_TIMEOUT_S` 7200 | (control's) 7500 | (as control) |
  | warm build | `REPLAY_WARM_BUILD_TIMEOUT_S` 21600 | 21900 | none: the site never sees warm builds |
  | height build | `REPLAY_HEIGHT_TIMEOUT_S` 82800 | 86400 (Render's maximum) | `STALE_BUILD_S` 166200 (> 2 x 82800) |

  The site backstop is twice the worker's limit because a run's limit restarts when it starts (Task 2): the worst
  case is a full limit queued, then a full limit working.

---

### Task 0: Confirm the cause (gate, owner + implementer, no code)

**Files:** none.

- [ ] **Step 1: Owner reads three cancelled `control` runs in the Render dashboard** (replay-tasks workflow, Runs).
  For each, copy the run's input: its `map` and its `files` list. Never copy its `token`.
- [ ] **Step 2: Classify.** A visibility cache is a name matching `^<Map>\.[0-9a-f]{16}\.npz$` (no folder prefix).
  - No such name in `files` on all three: the run had no cache and was building it. **Proceed.**
  - A cache name present on any of them: that run had the cache and was still slow. **Stop** and report to the
    owner: the bottleneck is elsewhere and this plan's Task 4 would not help it. Tasks 1, 2 and 5 still apply on
    their own.
- [ ] **Step 3: Record the result** (maps, and whether `files` held a cache) in the PR description.

---

### Task 1: Every run says where its cache came from; `/health` shows the cache and the open runs

**Files:**
- Modify: `webapp/app/control/geometry.py` (`visibility`, new `visibility_name`)
- Modify: `webapp/app/control/task.py` (`compute_task`)
- Modify: `replay_worker/control_cache.py` (`store`, new `EVICTIONS`, new `usage`)
- Modify: `replay_worker/runs.py` (`Run`, `Runs.open`, `Runs.status`)
- Modify: `replay_worker/server.py` (`/health` body)
- Test: `webapp/tests/replays/test_control_task.py`, `webapp/tests/replays/test_replay_worker_control_cache.py`,
  `webapp/tests/replays/test_replay_worker_runs.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Produces: `geometry.visibility_name(geo) -> str` (`"<Map>.<16 hex>.npz"`); `compute_task` result keys
  `"visibility_source"` (`"cache"` | `"built"`) and `"visibility_s"` (float) whenever the geometry loaded;
  `control_cache.EVICTIONS: int`; `control_cache.usage(cache_dir: Path) -> {"bytes": int, "maps": {map: int},
  "evictions": int}`; `Run.opened: float`; `Runs.status()["by_kind"] -> {kind: {"open": int, "oldest_s": float}}`;
  `/health` `control.cache` = `usage(...)` plus `"reserve"` (Task 6 adds the reserve; this task writes
  `control_cache.RESERVE`).

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_control_task.py`, after `test_a_map_the_machine_has_no_geometry_for_is_an_infra_failure`:

```python
def test_a_round_says_where_its_visibility_cache_came_from(toy):
    toy.visibility_source, toy.visibility_s = "cache", 0.25
    t, _ = make_task(toy)
    result = task.compute_task(t)
    assert result["status"] == "ok"
    assert result["visibility_source"] == "cache" and result["visibility_s"] == 0.25


def test_the_visibility_cache_name_is_one_the_worker_takes_for_that_map(toy):
    import re

    from app.control import geometry
    from replay_worker import control_cache

    name = geometry.visibility_name(toy)
    assert re.fullmatch(rf"{toy.name}\.[0-9a-f]{{16}}\.npz", name)
    assert control_cache.valid(name, toy.name) and control_cache.visibility_name(name)
```

The file puts only `HERE` on `sys.path`; `replay_worker` lives at the repo root, so add
`sys.path.insert(0, str(HERE.parents[2]))` on the line after the existing `sys.path.insert(0, str(HERE))`.

In `webapp/tests/replays/test_replay_worker_control_cache.py`:

```python
def test_usage_counts_bytes_per_map_and_evictions(tmp_path):
    (tmp_path / "feature-rows").mkdir()
    (tmp_path / f"Ascent.{'a' * 16}.npz").write_bytes(b"x" * 10)
    (tmp_path / "feature-rows" / f"Ascent.{'d' * 64}.npz").write_bytes(b"y" * 5)
    (tmp_path / f"Haven.{'b' * 16}.npz").write_bytes(b"z" * 7)
    before = control_cache.EVICTIONS
    found = control_cache.usage(tmp_path)
    assert found["maps"] == {"Ascent": 15, "Haven": 7}
    assert found["bytes"] == 22 and found["evictions"] == before


def test_a_store_that_drops_files_counts_them(tmp_path):
    old = tmp_path / f"Ascent.{'a' * 16}.npz"
    old.write_bytes(b"x" * 60)
    incoming = tmp_path / ".incoming"
    incoming.mkdir()
    src = incoming / "n.npz"
    src.write_bytes(b"y" * 60)
    before = control_cache.EVICTIONS
    assert control_cache.store(tmp_path, f"Haven.{'b' * 16}.npz", src, reserve=100)
    assert not old.exists() and control_cache.EVICTIONS == before + 1
```

In `webapp/tests/replays/test_replay_worker_runs.py`, after `test_status_counts_open_runs`:

```python
def test_status_counts_open_runs_by_kind_with_the_oldest_age(render, tmp_path):
    now = [100.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    table.open("control", "c1", {}, timeout_s=60)
    now[0] = 130.0
    table.open("control", "c2", {}, timeout_s=60)
    table.open("heights", "h1", {}, timeout_s=60)
    now[0] = 145.0
    by_kind = table.status()["by_kind"]
    assert by_kind == {"control": {"open": 2, "oldest_s": 45.0}, "heights": {"open": 1, "oldest_s": 15.0}}
```

In `webapp/tests/replays/test_replay_worker_control_workflow.py`, after `test_engine_failure_settles_engine`:

```python
def test_health_shows_the_control_cache_and_open_runs_by_kind(remote):
    import urllib.request

    control, _, settings, base = remote
    warm_up(control)
    with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
        body = json.loads(response.read())
    assert body["control"]["cache"]["maps"] == {"Ascent": len(b"built")}
    assert body["control"]["cache"]["reserve"] > 0
    assert body["workflow"]["by_kind"] == {}           # nothing open once the round settled
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_task.py tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_runs.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: the six new tests FAIL (`KeyError: 'visibility_source'`, `AttributeError: ... 'visibility_name'`,
`'usage'`, `'EVICTIONS'`, `KeyError: 'by_kind'`, `KeyError: 'cache'`).

- [ ] **Step 3: Implement**

`webapp/app/control/geometry.py`, above `def visibility(`:

```python
def visibility_name(geo: Geometry) -> str:
    """The file name of the map's visibility cache for exactly this geometry: what `visibility` reads and writes,
    and what a warm build reports to the replay worker (replay_worker/control_cache.py takes the same shape)."""
    return f"{geo.name}.{cache_key(geo)[:16]}.npz"
```

and in `visibility`, replace `path = directory / f"{geo.name}.{cache_key(geo)[:16]}.npz"` with
`path = directory / visibility_name(geo)`.

`webapp/app/control/task.py`, `compute_task`: add `geo = None` as the first line inside the second `try:` (before
`options = ...`), and replace the last two lines

```python
    result.update({"key": task.get("key"), "seconds": time.time() - started, "peak": peak_memory()})
    return result
```

with

```python
    result.update({"key": task.get("key"), "seconds": time.time() - started, "peak": peak_memory()})
    if geo is not None and getattr(geo, "visibility_source", None):
        # Whether this round found the map's visibility cache or had to build it: the replay worker's warm builds
        # read it (a round that built it means the key wasn't really warm).
        result.update({"visibility_source": geo.visibility_source, "visibility_s": geo.visibility_s})
    return result
```

`replay_worker/control_cache.py`: under `_LOCK = ...` add

```python
EVICTIONS = 0                     # files `store` dropped to make room since this process started (/health)
```

In `store`, make `global EVICTIONS` the first line of the body (after the docstring), and inside `if over > 0:`,
just before `for path in victims:`, add:

```python
                EVICTIONS += len(victims)
```

Then add after `has_visibility`:

```python
def usage(cache_dir: Path) -> dict:
    """What /health shows of the cache: every byte under it, the bytes of each map's visibility and feature-row
    caches, and how many files `store` has dropped to make room since this process started."""
    per_map: dict[str, int] = {}
    for folder in (Path(cache_dir), Path(cache_dir) / "feature-rows"):
        for path in folder.glob("*.npz"):
            match = _NAME.fullmatch(path.name)
            if not match:
                continue
            try:
                per_map[match["map"]] = per_map.get(match["map"], 0) + path.stat().st_size
            except OSError:
                pass                  # pruned meanwhile
    return {"bytes": _tree_bytes(cache_dir), "maps": dict(sorted(per_map.items())), "evictions": EVICTIONS}
```

(`_NAME`'s `map` group already matches both folders: the `folder` group is optional.)

`replay_worker/runs.py`: add a field to `Run` after `done`:

```python
    opened: float = 0.0             # when the worker opened it (Runs.status: the oldest open run per kind)
```

In `Runs.open`, build the run with `opened=self.clock()`:

```python
        now = self.clock()
        run = Run(job_id, kind, token or secrets.token_hex(16), {k: Path(v) for k, v in files.items()}, folder,
                  deadline if deadline is not None else now + timeout_s, render_id, opened=now)
```

Replace `Runs.status` with:

```python
    def status(self) -> dict:
        now = self.clock()
        with self.lock:
            open_runs = list(self.open_runs.values())
        by_kind: dict[str, dict] = {}
        for run in open_runs:
            entry = by_kind.setdefault(run.kind, {"open": 0, "oldest_s": 0.0})
            entry["open"] += 1
            entry["oldest_s"] = max(entry["oldest_s"], round(now - run.opened, 1))
        return {"open": len(open_runs), "by_kind": by_kind, "starts_per_min": self.pacer.per_min,
                "last_reconcile": self.last_reconcile, "last_error": self.last_error}
```

`replay_worker/server.py`, in the `/health` handler, after the `body["control"] = {...}` statement:

```python
                if control is not None:
                    body["control"]["cache"] = {**control_cache.usage(control.settings.control_cache_dir),
                                                "reserve": control_cache.RESERVE}
```

- [ ] **Step 4: Run them to see them pass**

Run: the Step 2 command. Expected: all PASS (including every test that passed before).

- [ ] **Step 5: Commit**

```bash
git add webapp/app/control/geometry.py webapp/app/control/task.py replay_worker/control_cache.py replay_worker/runs.py replay_worker/server.py webapp/tests/replays/test_control_task.py webapp/tests/replays/test_replay_worker_control_cache.py webapp/tests/replays/test_replay_worker_runs.py webapp/tests/replays/test_replay_worker_control_workflow.py
git commit -F <message file>     # "Replay runs report where their cache came from; /health shows the cache and open runs"
```

---

### Task 2: A run's time limit counts from when it starts, not from when the worker queued it

**Files:**
- Modify: `replay_worker/runs.py` (`Run`, `Runs.open`, new `Runs.started`)
- Modify: `replay_worker/server.py` (`do_POST`: new route `POST /runs/{job}/started`; module docstring route list)
- Modify: `replay_tasks/tasks.py` (`WorkerLink.post_started`; call it first in `control` and `heights`)
- Test: `webapp/tests/replays/test_replay_worker_runs.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`,
  `webapp/tests/replays/test_replay_worker_heights_workflow.py` (its `FakeLink` only)

**Interfaces:**
- Consumes: `Run.opened` (Task 1).
- Produces: `Run.timeout_s: float`, `Run.extend_on_start: bool`, `Run.started_at: float | None`;
  `Runs.started(job_id: str, token: str | None) -> bool`; route `POST /runs/{job}/started` (header `X-Run-Token`,
  empty body) answering 200 `{"ok": true}` or 409; `WorkerLink.post_started() -> None` (never raises an `OSError`).

Rule: a run opened **without** an explicit `deadline` (control, warm, heights, ping) gets `deadline = opened +
timeout_s` as today; when it posts `started`, the deadline becomes `max(deadline, started_at + timeout_s)`, once.
A run opened **with** a `deadline` (parse: its job's one deadline, saved for restarts) never moves. An older task
image that never posts `started` keeps today's deadline; an older worker 404s the post and the task carries on.

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_replay_worker_runs.py`, after `test_deadline_cancels_and_settles_timeout`:

```python
def test_started_gives_a_run_its_whole_limit_from_that_moment(render, tmp_path):
    now = [100.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("control", "j1", {}, timeout_s=50)
    assert run.deadline == 150.0
    now[0] = 140.0                                   # it waited 40 s in Render's queue
    assert table.started("j1", run.token)
    assert run.deadline == 190.0 and run.started_at == 140.0
    now[0] = 160.0
    assert table.started("j1", run.token)            # a second post never moves it again
    assert run.deadline == 190.0


def test_started_never_moves_an_explicit_deadline(render, tmp_path):
    now = [100.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("parse", "j2", {}, 0, deadline=130.0)
    now[0] = 120.0
    assert table.started("j2", run.token)
    assert run.deadline == 130.0


def test_started_refuses_a_wrong_token_or_an_unknown_job(render, tmp_path):
    table = make_runs(render, tmp_path)
    table.open("control", "j3", {}, timeout_s=50)
    assert not table.started("j3", "nope")
    assert not table.started("nobody", "nope")
```

In `webapp/tests/replays/test_replay_worker_control_workflow.py`:
1. Add to `FakeLink`:

```python
    def post_started(self):
        self.order.append("started")
```

2. The two existing `link.order` assertions (in `test_control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first`
   and `test_control_task_posts_a_cache_rewritten_in_place`) now begin with `"started"`:
   `["started", f"cache/Ascent.{'c' * 16}.npz", "result"]` and `["started", route, "result"]`.
3. Add after `test_health_shows_the_control_cache_and_open_runs_by_kind`:

```python
def test_started_route_moves_the_deadline_and_refuses_a_wrong_token(remote):
    import urllib.error
    import urllib.request

    control, _, _, base = remote
    table = control.runs
    run = table.open("control", "manual", {}, timeout_s=50)
    before = run.deadline

    def post(token):
        request = urllib.request.Request(f"{base}/runs/manual/started", data=b"", method="POST",
                                         headers={"X-Run-Token": token, "Content-Length": "0"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    assert post("wrong") == 409 and run.deadline == before
    time.sleep(0.05)
    assert post(run.token) == 200 and run.deadline > before
    table.close(run)


def test_a_task_carries_on_when_the_worker_does_not_know_the_started_route():
    link = tasks.WorkerLink("http://127.0.0.1:9", "j", "t", timeout_s=1)    # nothing listens on port 9
    link.post_started()                                                      # must not raise
```

In `webapp/tests/replays/test_replay_worker_heights_workflow.py`, add the same `post_started` method to its
`FakeLink` (that file asserts no `.order`).

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_runs.py tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_heights_workflow.py -n 0 -q`
Expected: the new tests FAIL (`AttributeError: 'Runs' object has no attribute 'started'`, 404 instead of 409,
`AttributeError: 'WorkerLink' object has no attribute 'post_started'`); the changed `order` assertions FAIL.

- [ ] **Step 3: Implement**

`replay_worker/runs.py`, `Run` gets three more fields after `opened`:

```python
    timeout_s: float = 0.0          # its limit, counted again from `started` (Runs.started)
    extend_on_start: bool = False   # False for a run opened with an explicit deadline (a parse): it never moves
    started_at: float | None = None
```

`Runs.open` passes them:

```python
        now = self.clock()
        explicit = deadline is not None
        run = Run(job_id, kind, token or secrets.token_hex(16), {k: Path(v) for k, v in files.items()}, folder,
                  deadline if explicit else now + timeout_s, render_id, opened=now, timeout_s=timeout_s,
                  extend_on_start=not explicit)
```

New method after `accepts`:

```python
    def started(self, job_id: str, token: str | None) -> bool:
        """The run says it has begun its work. A run's limit counts from here, once: the time it waited in
        Render's queue must not eat its working time. A run opened with an explicit deadline (a parse, whose one
        deadline is saved for restarts) never moves. False when no open run takes this token."""
        run = self.accepts(job_id, token)
        if run is None:
            return False
        with self.lock:
            if run.extend_on_start and run.started_at is None:
                run.started_at = self.clock()
                run.deadline = max(run.deadline, run.started_at + run.timeout_s)
        return True
```

(`wait` already re-reads `run.deadline` on every 0.2 s pass, so a moved deadline takes effect at once.)

`replay_worker/server.py`: in the module docstring's route list, after the `POST /runs/{job}/result` entry add

```
    POST /runs/{job}/started        (header X-Run-Token) the run has begun: its limit counts from now. 200; 409.
```

In `make_handler`, add a method beside `_run_result`:

```python
        def _run_started(self):
            parts = self.path.split("/")          # ["", "runs", job, "started"]
            token = self.headers.get("X-Run-Token")
            ok = len(parts) == 4 and runs_table.started(parts[2], token)
            return self._send(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"ok": ok})
```

and in `do_POST`, inside `if runs_table is not None and self.path.startswith("/runs/"):`, after the `/result` line:

```python
                if self.path.endswith("/started"):
                    return self._run_started()
```

`replay_tasks/tasks.py`, in `WorkerLink` after `post_result`:

```python
    def post_started(self) -> None:
        """Tells the worker the run has begun, so its limit counts from now (not from when it was queued). Best
        effort: an older worker answers 404 and a run must carry on either way."""
        request = urllib.request.Request(self._url("started"), data=b"", method="POST",
                                         headers={"X-Run-Token": self.token, "Content-Length": "0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response.read()
        except OSError:
            pass
```

and make `link.post_started()` the first statement of `control(args, link)` and of `heights(args, link)` (before
`work = ...`). Not `parse`: its deadline is explicit and never moves.

- [ ] **Step 4: Run them to see them pass**

Run: the Step 2 command. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/runs.py replay_worker/server.py replay_tasks/tasks.py webapp/tests/replays/test_replay_worker_runs.py webapp/tests/replays/test_replay_worker_control_workflow.py webapp/tests/replays/test_replay_worker_heights_workflow.py
git commit -F <message file>     # "A Workflow run's limit counts from when it starts"
```

---

### Task 3: The warm build task: a map's visibility cache, built by itself

**Files:**
- Modify: `webapp/app/control/task.py` (new `_load_options`, new `warm_task`; `compute_task` uses `_load_options`)
- Create: `replay_worker/warm_job.py`
- Modify: `replay_tasks/tasks.py` (new `warm`; `TASKS`)
- Modify: `replay_tasks/main.py` (register `warm`)
- Test: `webapp/tests/replays/test_control_task.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `geometry.visibility_name` (Task 1), `WorkerLink.post_started` (Task 2).
- Produces: `task.warm_task(task: dict) -> dict`, answering `{"status": "ok", "visibility": "<Map>.<16 hex>.npz",
  "visibility_source": "cache" | "built", "visibility_s": float, "key", "seconds", "peak"}` or
  `{"status": "failed", "error_kind": "infra", "error", "key", "seconds", "peak"}`. Its input is a control task with
  `blob`, `link`, `gaps` and `gaps_only` removed (`map`, `height`, `heights`, `features`, `height_mode`, `inputs`,
  `geometry` kept). `replay_worker/warm_job.py`: stdin task JSON, stdout answer JSON. `tasks.warm(args, link)`;
  `tasks.TASKS["warm"]`; Render task `replay-tasks/warm`; env override `REPLAY_WARM_CMD` (JSON argv, as
  `REPLAY_CONTROL_CMD`).

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_control_task.py`, after the Task 1 tests:

```python
def test_a_warm_build_names_the_cache_file_its_rounds_will_read(toy):
    from app.control import geometry

    toy.visibility_source, toy.visibility_s = "built", 1.5
    result = task.warm_task({"key": "warm:Open::", "map": toy.name})
    assert result["status"] == "ok" and result["key"] == "warm:Open::"
    assert result["visibility"] == geometry.visibility_name(toy)
    assert result["visibility_source"] == "built" and result["visibility_s"] == 1.5


def test_a_warm_build_for_a_map_with_no_geometry_is_an_infra_failure():
    result = task.warm_task({"key": "w", "map": "NoSuchMap"})
    assert result["status"] == "failed" and result["error_kind"] == "infra"
    assert "GeometryError" in result["error"]
```

In `webapp/tests/replays/test_replay_worker_control_workflow.py`, below `STUB` add:

```python
WARM_STUB = """
import json, os, sys, pathlib
task = json.loads(sys.stdin.buffer.read())
if task["map"] == "Broken":
    print(json.dumps({"status": "failed", "error_kind": "infra", "error": "no geometry", "key": task["key"]}))
    sys.exit(0)
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
name = f"{task['map']}.{'e' * 16}.npz"
source = "cache" if (cache / name).is_file() else "built"
if source == "built":
    (cache / name).write_bytes(b"vis")
print(json.dumps({"status": "ok", "visibility": name, "visibility_source": source, "key": task["key"]}))
"""


@pytest.fixture
def warm_stub(tmp_path, monkeypatch):
    path = tmp_path / "warm_stub.py"
    path.write_text(WARM_STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_WARM_CMD", json.dumps([sys.executable, str(path)]))
    return path


def test_warm_task_posts_the_new_cache_then_the_answer_naming_it(warm_stub):
    link = FakeLink({"task": json.dumps({"key": "warm:Ascent::", "map": "Ascent"}).encode()})
    tasks.warm({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    name = f"Ascent.{'e' * 16}.npz"
    assert link.order == ["started", f"cache/{name}", "result"]
    assert link.result["status"] == "ok" and link.result["visibility"] == name
    assert link.result["visibility_source"] == "built"


def test_warm_task_with_the_cache_already_there_posts_nothing_but_the_answer(warm_stub):
    name = f"Ascent.{'e' * 16}.npz"
    link = FakeLink({"task": json.dumps({"key": "w", "map": "Ascent"}).encode(), name: b"vis"})
    tasks.warm({"job_id": "j", "token": "t", "map": "Ascent", "files": [name]}, link)
    assert link.order == ["started", "result"] and link.result["visibility_source"] == "cache"


def test_the_warm_task_is_registered_with_render():
    import ast

    tree = ast.parse((REPO / "replay_tasks" / "main.py").read_text(encoding="utf-8"))
    names = {kw.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
             for kw in node.keywords if kw.arg == "name"}
    assert "warm" in names and "warm" in tasks.TASKS
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_task.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: the five new tests FAIL (`AttributeError: ... 'warm_task'`, `... 'warm'`, `assert 'warm' in names`).

- [ ] **Step 3: Implement**

`webapp/app/control/task.py`, above `def compute_task(`:

```python
def _load_options(task: dict) -> dict:
    """`_load`'s options for a task: exactly the geometry a round of it is computed with."""
    options = {'exact': task['features']} if task.get('features') else {}
    if task.get('height_mode'):
        options['height_mode'] = task['height_mode']
    return options


def warm_task(task: dict) -> dict:
    """A warm build (replay_worker/warm_job.py): loads the map's geometry exactly as `compute_task` would for a
    round of this task, building its visibility cache when this machine lacks it, and names the cache file. The
    replay worker counts the task's warm key as warm while that file is on its disk."""
    started = time.time()
    try:
        from app.control import geometry

        geo = _load(task["map"], task.get("heights"), task.get("height"), **_load_options(task))
        result = {"status": "ok", "visibility": geometry.visibility_name(geo),
                  "visibility_source": geo.visibility_source, "visibility_s": geo.visibility_s}
    except Exception as error:  # noqa: BLE001 - the worker retries, then falls back to a warming round
        result = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}"}
    result.update({"key": task.get("key"), "seconds": time.time() - started, "peak": peak_memory()})
    return result
```

and in `compute_task` replace

```python
        options = {'exact': task['features']} if task.get('features') else {}
        if task.get('height_mode'):
            options['height_mode'] = task['height_mode']
        geo = _load(task["map"], task.get("heights"), task.get("height"), **options)
```

with

```python
        geo = _load(task["map"], task.get("heights"), task.get("height"), **_load_options(task))
```

Create `replay_worker/warm_job.py`:

```python
"""A map's visibility cache, built (or found) by itself before any round of its warm key runs: the replay worker's
warm build (docs/superpowers/plans/2026-11-05-replay-throughput.md). Same task JSON as control_job, same control
venv interpreter; the server never imports this module.

    <control venv python> -m replay_worker.warm_job  < task.json  > result.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"


def main() -> int:
    task = json.loads(sys.stdin.buffer.read())
    try:
        if str(WEBAPP) not in sys.path:
            sys.path.insert(0, str(WEBAPP))
        from app.control.task import warm_task

        result = warm_task(task)
    except Exception as error:  # noqa: BLE001 - even an import failure answers in JSON
        result = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}",
                  "key": task.get("key")}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`replay_tasks/tasks.py`, after `control`:

```python
def warm(args: dict, link: WorkerLink) -> None:
    """One warm key's visibility cache: the warm child alone, then the cache posted before the answer naming it
    (the worker counts the key warm only once that file is on its disk)."""
    link.post_started()
    work = Path(tempfile.mkdtemp(prefix="warm-"))
    try:
        cache = work / "cache"
        cache.mkdir()
        _fetch_inputs(link, args.get("files", []), cache)
        before = _cache_files(cache)
        stdin = link.fetch("task", work / "task.json").read_bytes()
        answer = _run_child("REPLAY_WARM_CMD", [sys.executable, "-m", "replay_worker.warm_job"], stdin, cache,
                            what="warm child")
        unsent = _post_caches(link, cache, before, args["map"])
        if unsent:
            answer = {**answer, "cache_unsent": unsent}
        link.post_result(answer)
    finally:
        shutil.rmtree(work, ignore_errors=True)
```

and `TASKS = {"ping": ping, "control": control, "parse": parse, "heights": heights, "warm": warm}`.

`replay_tasks/main.py`, after the `control` task block (above the "Later steps add" comment):

```python
@app.task(name="warm", timeout_seconds=21900, retry=Retry(max_retries=1, wait_duration_ms=5000))
def warm(ctx: TaskContext, args: dict) -> dict:
    tasks.warm(args, tasks.link_for(args))
    return {"ok": True}
```

Also extend the module docstring's second paragraph: "warm (a map's visibility cache by itself) runs on `flex` with
a 6 h 5 min limit, above the worker's 6 h (`REPLAY_WARM_BUILD_TIMEOUT_S`)."

- [ ] **Step 4: Run them to see them pass**

Run: the Step 2 command, then `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_isolation.py -n 0 -q`
Expected: all PASS (the isolation test proves the server still imports no numpy).

- [ ] **Step 5: Commit**

```bash
git add webapp/app/control/task.py replay_worker/warm_job.py replay_tasks/tasks.py replay_tasks/main.py webapp/tests/replays/test_control_task.py webapp/tests/replays/test_replay_worker_control_workflow.py
git commit -F <message file>     # "The warm build: a map's visibility cache as its own Workflow task"
```

---

### Task 4: The worker schedules warm builds; a key is warm by its exact cache file

**Files:**
- Modify: `replay_worker/server.py` (`Settings`, `Settings.from_env`, module docstring env list, `WARM_BUILD_TRIES`,
  `ControlRunner`: `__init__`, `remote`, `counts`, new `uses_warm_builds` / `_ready` / `_ask_warm`, `_next`, `_run`;
  `_run_cache` route accepts `warm` runs)
- Modify: `render.yaml` (worker: `REPLAY_WARM_BUILDS`, `sync: false`)
- Test: `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `tasks.warm` / Render task `warm` (Task 3); round results' `visibility_source` (Task 1).
- Produces: `Settings.warm_builds: bool` (env `REPLAY_WARM_BUILDS`, `"1"` or `"true"`, default off);
  `Settings.warm_build_timeout_s: float` (env `REPLAY_WARM_BUILD_TIMEOUT_S`, default 21600.0);
  `server.WARM_BUILD_TRIES = 2`; `ControlJob.kind` may be `"warm"`; `ControlRunner.counts()["warm_builds"]` (only
  while warm builds are on) = `{"ready": [warm keys], "building": [warm keys], "failures": {warm key: n},
  "misses": int}`.

Rules (only while `uses_warm_builds()`; local mode and switch-off keep today's code path untouched):
1. A control round may start, beside any number of rounds of its map, when its warm key is **ready**: the key has a
   recorded cache name **and** that file is on this disk (`_ready`). No per-map one-at-a-time rule.
2. A round whose key isn't ready waits, and its key gets **one** warm build (`_ask_warm`), queued ahead of every
   round. A warm build counts against `control_runs` like any run.
3. A warm build that answers ok, names a valid cache of its map that isn't in `cache_unsent`, and whose file is on
   disk records `warm_names[key] = name`. Otherwise the key's failure count goes up.
4. After `WARM_BUILD_TRIES` failed warm builds for a key, its rounds fall back to today's rule (one round warms
   the map alone, through the `self.warm` set), so a broken map still surfaces its real error.
5. A round on a ready key that reports `visibility_source == "built"` is a **miss**: count it and forget the key's
   name, so the remaining rounds wait for a new warm build instead of each building the cache (risk 2).
6. A round's machine failure or time-out does **not** make its key cold. Only rules 5 and "the file left the disk"
   (rule 1) do.

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_replay_worker_control_workflow.py`:

1. In `STUB`, make the round report where its cache came from and add an `infra` mode. Replace the `if mode ==
   "engine":` … `else:` block at the end with:

```python
source = task["link"].get("source") or ("cache" if any(n.count(".") == 2 and "/" not in n for n in had) else "built")
if mode == "engine":
    print(json.dumps({"status": "failed", "error_kind": "engine", "error": "no side"}))
elif mode == "infra":
    print(json.dumps({"status": "failed", "error_kind": "infra", "error": "machine went away"}))
else:
    print(json.dumps({"status": "ok", "data": "ZA==", "summary": "cw==", "key": task["key"], "had": had,
                      "visibility_source": source}))
```

2. Add a fixture beside `remote`:

```python
@pytest.fixture
def warm_remote(tmp_path, stub, warm_stub):
    """`remote` with warm builds on (REPLAY_WARM_BUILDS)."""
    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_runs=40, workflow_slug="replay-tasks",
                               render_api_key="k", render_api_base=render.url, warm_builds=True)
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


def warm_starts(render):
    return [s["task"] for s in render.started].count("replay-tasks/warm")
```

3. The tests:

```python
def test_a_cold_map_gets_one_warm_build_then_its_rounds_run_side_by_side(warm_remote):
    control, render, _, _ = warm_remote
    jobs = [control.submit(round_task(f"r{i}", mode="sleep:1.5")) for i in range(6)]
    wait_running(control, 6)
    done = wait_done(control, jobs)
    assert all(j.status == "done" for j in done)
    assert warm_starts(render) == 1 and render.started[0]["task"] == "replay-tasks/warm"
    assert all(f"Ascent.{'e' * 16}.npz" in j.result["had"] for j in done)
    assert control.counts()["warm_builds"]["misses"] == 0


def test_a_new_height_digest_gets_its_own_warm_build(warm_remote):
    control, render, _, _ = warm_remote
    wait_done(control, [control.submit(round_task("a"))])
    wait_done(control, [control.submit({**round_task("b"), "height": "d2"})])
    assert warm_starts(render) == 2


def test_a_warm_build_that_keeps_failing_falls_back_to_one_warming_round(warm_remote):
    control, render, _, _ = warm_remote
    job = control.submit(round_task("x", map_name="Broken", build=True))
    assert wait_done(control, [job])[0].status == "done"
    assert warm_starts(render) == server.WARM_BUILD_TRIES
    assert control.counts()["warm_builds"]["failures"] == {job.warm_key: server.WARM_BUILD_TRIES}


def test_a_round_that_still_built_the_cache_sends_its_key_back_for_a_warm_build(warm_remote):
    control, render, _, _ = warm_remote
    wait_done(control, [control.submit(round_task("a"))])
    wait_done(control, [control.submit(round_task("b", source="built"))])
    assert control.counts()["warm_builds"]["misses"] == 1
    wait_done(control, [control.submit(round_task("c"))])
    assert warm_starts(render) == 2


def test_a_key_whose_cache_left_the_disk_is_warmed_again(warm_remote):
    control, render, settings, _ = warm_remote
    wait_done(control, [control.submit(round_task("a"))])
    (settings.control_cache_dir / f"Ascent.{'e' * 16}.npz").unlink()
    wait_done(control, [control.submit(round_task("b"))])
    assert warm_starts(render) == 2


def test_a_round_that_fails_for_the_machine_leaves_its_key_warm(warm_remote):
    control, render, _, _ = warm_remote
    wait_done(control, [control.submit(round_task("a"))])
    assert wait_done(control, [control.submit(round_task("b", mode="infra"))])[0].error_kind == "infra"
    wait_done(control, [control.submit(round_task("c"))])
    assert warm_starts(render) == 1


def test_warm_builds_off_keeps_todays_rule_and_reports_nothing(remote):
    control, render, _, _ = remote
    wait_done(control, [control.submit(round_task("a", build=True))])
    assert warm_starts(render) == 0 and "warm_builds" not in control.counts()


def test_settings_read_the_warm_build_switch_and_limit():
    on = server.Settings.from_env({"REPLAY_WARM_BUILDS": "true", "REPLAY_WARM_BUILD_TIMEOUT_S": "600"})
    assert on.warm_builds and on.warm_build_timeout_s == 600.0
    assert not server.Settings.from_env({}).warm_builds
```

(`Settings.from_env(cls, env=os.environ)` takes a mapping, so a plain dict works.)

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: the eight new tests FAIL (`TypeError: ... unexpected keyword argument 'warm_builds'` and friends). The
existing tests still PASS with the changed `STUB` (none of them reads `visibility_source`).

- [ ] **Step 3: Implement**

`replay_worker/server.py`:

a. `Settings`, after `control_warm_timeout_s`:

```python
    warm_builds: bool = False               # REPLAY_WARM_BUILDS: a map's visibility cache as its own run (workflow)
    warm_build_timeout_s: float = 21600.0
```

b. `Settings.from_env`, after the `control_warm_timeout_s` lines:

```python
        settings.warm_builds = env.get("REPLAY_WARM_BUILDS", "").strip().lower() in ("1", "true")
        settings.warm_build_timeout_s = float(env.get("REPLAY_WARM_BUILD_TIMEOUT_S", settings.warm_build_timeout_s))
```

c. Module docstring env list, after `REPLAY_CONTROL_RUNS`:

```
    REPLAY_WARM_BUILDS       "true": with REPLAY_CONTROL_RUNNER=workflow, a cold warm key's visibility cache is one
                             warm build (replay_tasks warm) before its rounds run, all at once (default off);
    REPLAY_WARM_BUILD_TIMEOUT_S  a warm build's limit (default 21600; Render's task limit is 300 s above it);
```

d. Beside `CONTROL_FINISHED_KEPT = 200`:

```python
WARM_BUILD_TRIES = 2     # failed warm builds of one key before its rounds fall back to one warming round
```

e. `ControlRunner.__init__`, after `self.warm: set[str] = set()`:

```python
        # Warm builds (REPLAY_WARM_BUILDS): a key is ready while the cache file its warm build named is on disk.
        self.warm_names: dict[str, str] = {}     # warm key -> its visibility cache's file name
        self.warm_jobs: dict[str, str] = {}      # warm key -> its warm build's job id, queued or running
        self.warm_failures: dict[str, int] = {}  # warm key -> failed warm builds (WARM_BUILD_TRIES: fall back)
        self.warm_misses = 0                     # rounds on a ready key that still had to build the cache
```

f. `ControlRunner.remote`: add as the first line after `if self.runs is None: return False`:

```python
        if job.kind == "warm":
            return True                          # a warm build only ever runs on Workflows
```

g. New methods after `remote`:

```python
    def uses_warm_builds(self) -> bool:
        return self.settings.warm_builds and self.settings.control_runner == "workflow" and self.runs is not None

    def _ready(self, job: "ControlJob") -> bool:
        """The round's warm key has a cache name from its warm build, and that file is on this disk (under the
        lock). The name is the exact geometry's, so a cache from other heights never counts (risk 2)."""
        name = self.warm_names.get(job.warm_key)
        return name is not None and (self.settings.control_cache_dir / name).is_file()

    def _ask_warm(self, job: "ControlJob") -> None:
        """One warm build per warm key, ahead of every round (under the lock). Its task is the round's own less
        what only a round needs, so it loads exactly the geometry the round will."""
        if job.warm_key in self.warm_jobs:
            return
        task = json.loads(job.task)
        for name in ("blob", "link", "gaps", "gaps_only"):
            task.pop(name, None)
        task["key"] = f"warm:{job.warm_key}"
        warm = ControlJob(uuid.uuid4().hex, task["key"], job.map, json.dumps(task).encode("utf-8"),
                          warm_key=job.warm_key, kind="warm")
        self.jobs[warm.id] = warm
        self.warm_jobs[job.warm_key] = warm.id
        self.pending.insert(0, warm.id)
        self.wake.set()
```

h. `counts`:

```python
    def counts(self) -> dict:
        with self.lock:
            body = {"queued": len(self.pending), "running": len(self.running), "warm": sorted(self.warm),
                    "preempted": self.preempted_total}
            if self.uses_warm_builds():
                body["warm_builds"] = {"ready": sorted(self.warm_names), "building": sorted(self.warm_jobs),
                                       "failures": dict(sorted(self.warm_failures.items())),
                                       "misses": self.warm_misses}
            return body
```

i. `_next`: change the final loop's header to iterate over a copy (`_ask_warm` inserts into `self.pending`) and add
the warm-build branch before today's warm checks:

```python
        for job_id in list(self.pending):
            job = self.jobs[job_id]
            if job.kind == "heights":
                continue
            if local_build and not self.remote(job):
                continue                             # a local build runs alone among the local children
            if job.kind == "warm":
                return job, False                    # its own run: neither waits for a map nor holds one
            if self.uses_warm_builds():
                if self._ready(job):
                    return job, False                # its cache is on disk: beside any number of its map's rounds
                if self.warm_failures.get(job.warm_key, 0) < WARM_BUILD_TRIES:
                    self._ask_warm(job)              # the round waits for its key's one warm build
                    continue
                # Warm builds keep failing for this key: today's rule below, one round warms the map alone.
            if job.warm_key in self.warm:
                if job.map in warming_maps:
                    continue
                return job, False
            if job.map not in busy_maps:
                return job, True
        return None
```

j. `_run`: the timeout line becomes

```python
        timeout = settings.height_timeout_s if build else settings.warm_build_timeout_s if job.kind == "warm" \
            else settings.control_warm_timeout_s if warming else settings.control_timeout_s
        what = "height build" if build else "warm build" if job.kind == "warm" else "control"
```

(replace the existing `what = ...` line). In the settlement `else:` branch (not preempted), after
`job.task = b""`, insert:

```python
                if job.kind == "warm":
                    self.warm_jobs.pop(job.warm_key, None)
                    name = (result or {}).get("visibility")
                    if status == "done" and name and control_cache.valid(name, job.map) and name not in unsent \
                            and (self.settings.control_cache_dir / name).is_file():
                        self.warm_names[job.warm_key] = name
                        self.warm_failures.pop(job.warm_key, None)
                    else:
                        self.warm_failures[job.warm_key] = self.warm_failures.get(job.warm_key, 0) + 1
                        _log(f"warm build {job.warm_key} failed: {error or 'its cache is not on this disk'}")
                    del self.jobs[job.id]            # nobody asks after a warm build by id
                elif job.kind == "control" and self.uses_warm_builds() \
                        and (result or {}).get("visibility_source") == "built" and job.warm_key in self.warm_names:
                    # A miss: the name said warm, yet this round built the cache. Forget it so the rounds still
                    # waiting get one new warm build instead of each building the cache (risk 2).
                    self.warm_misses += 1
                    self.warm_names.pop(job.warm_key, None)
                    _log(f"warm key {job.warm_key} missed: a round had to build its cache")
```

and leave today's `if job.kind == "control":` warm-set block after it unchanged (it only matters on the fallback
path). Rule 6 holds because nothing above touches `warm_names` on a round's failure. The finished-job pruning that
follows filters on `j.kind == "control"`, so the deleted warm job never reaches it. (`_log` is the module's own
logger, defined above `ControlJob`.) Update `ControlJob.kind`'s comment to `# or "heights" (a height build) or
"warm" (a warm build)`.

k. `_run_cache` route: both `run.kind not in ("control", "heights")` checks become
`run.kind not in ("control", "heights", "warm")`.

`render.yaml`, worker `envVars`, after `REPLAY_HEIGHTS_RUNNER`:

```yaml
      # Warm builds (docs/superpowers/plans/2026-11-05-replay-throughput.md): "true" builds a cold map's visibility
      # cache as its own run before any of its rounds, which then run all at once. The owner's switch, set in the
      # dashboard: `sync: false`, so a Blueprint sync never resets it. Unset, off (today's one-warming-round rule).
      - key: REPLAY_WARM_BUILDS
        sync: false
```

- [ ] **Step 4: Run them to see them pass**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker_runs.py -n 0 -q`
Expected: all PASS. `test_replay_worker_control.py` (local mode) must pass unchanged: rules apply only in workflow
mode with the switch on.

- [ ] **Step 5: Commit**

```bash
git add replay_worker/server.py render.yaml webapp/tests/replays/test_replay_worker_control_workflow.py
git commit -F <message file>     # "Warm builds: a cold key gets one; its rounds then run at once"
```

---

### Task 5: Time limits off the 30-minute floor, in a pinned order

**Files:**
- Modify: `replay_tasks/main.py` (`control` 1800 -> 7500, `heights` 3600 -> 86400; `warm` is 21900 from Task 3)
- Modify: `render.yaml` (worker env: four limits)
- Modify: `webapp/app/services/replay_control_remote.py` (`STALE_JOB_S` 1800 -> 15000)
- Modify: `webapp/app/services/replay_heights_remote.py` (`STALE_BUILD_S` 7200 -> 166200)
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Consumes: the Global Constraints' limits table; `Runs.started` (Task 2) is why the site waits out twice the
  worker's limit.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

At the end of `webapp/tests/replays/test_control_remote.py`:

```python
def test_every_time_limit_outlasts_the_one_inside_it():
    """Render's task limit sits above the worker's (the worker decides, and says why), within Render's 24 h
    ceiling; the site waits out twice the worker's (a run's limit restarts when it starts: queued, then working)."""
    import ast
    import re

    from app.services import replay_heights_remote

    root = Path(remote.__file__).resolve().parents[3]
    yaml_text = (root / "render.yaml").read_text(encoding="utf-8")

    def env(name):
        return float(re.search(rf"- key: {name}\n\s+value: \"([0-9.]+)\"", yaml_text).group(1))

    render = {}
    for node in ast.walk(ast.parse((root / "replay_tasks" / "main.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "task":
            kw = {k.arg: k.value for k in node.keywords}
            render[kw["name"].value] = kw["timeout_seconds"].value
    assert all(30 <= v <= 86400 for v in render.values())
    assert render["control"] >= env("REPLAY_CONTROL_TIMEOUT_S") + 300
    assert render["control"] >= env("REPLAY_CONTROL_WARM_TIMEOUT_S") + 300
    assert render["warm"] >= env("REPLAY_WARM_BUILD_TIMEOUT_S") + 300
    assert render["heights"] >= env("REPLAY_HEIGHT_TIMEOUT_S") + 300
    assert remote.STALE_JOB_S > 2 * env("REPLAY_CONTROL_TIMEOUT_S")
    assert replay_heights_remote.STALE_BUILD_S > 2 * env("REPLAY_HEIGHT_TIMEOUT_S")
```

(`remote` is the file's alias for `app.services.replay_control_remote`, and the file already imports `Path`.)

- [ ] **Step 2: Run it to see it fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_remote.py -n 0 -q -k outlasts`
Expected: FAIL (`AttributeError: 'NoneType' object has no attribute 'group'`: the env values aren't in `render.yaml`
yet).

- [ ] **Step 3: Implement**

`render.yaml`, worker `envVars`, after `REPLAY_CONTROL_QUEUE`:

```yaml
      # Time limits (docs/superpowers/plans/2026-11-05-replay-throughput.md). A round that needs longer than its
      # limit is cancelled and retried from nothing, so each sits well above the slowest real run; Render's own task
      # limit (replay_tasks/main.py) is 300 s above each, and tests/replays/test_control_remote.py pins the order.
      - key: REPLAY_CONTROL_TIMEOUT_S
        value: "7200"
      - key: REPLAY_CONTROL_WARM_TIMEOUT_S
        value: "7200"
      - key: REPLAY_WARM_BUILD_TIMEOUT_S
        value: "21600"
      - key: REPLAY_HEIGHT_TIMEOUT_S
        value: "82800"
```

`replay_tasks/main.py`: `name="control", timeout_seconds=7500` and `name="heights", timeout_seconds=86400`; in the
docstring, note the limits sit 300 s above the worker's (`render.yaml`), 24 h being Render's maximum.

`webapp/app/services/replay_control_remote.py`:

```python
STALE_JOB_S = 15000     # > 2 x the worker's round limit (render.yaml REPLAY_CONTROL_TIMEOUT_S): a backstop only
```

`webapp/app/services/replay_heights_remote.py`:

```python
STALE_BUILD_S = 166200           # a build seen running this long: > 2 x the worker's REPLAY_HEIGHT_TIMEOUT_S
```

- [ ] **Step 4: Run the suites that read these constants**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_remote.py tests/replays/test_heights_remote.py -n 0 -q`
Expected: all PASS. Those tests use the constants by name (`remote.STALE_JOB_S + 1` and multiples), so they follow
the new values. If one hard-codes 1800 or 7200, change it to the constant, never the other way round.

- [ ] **Step 5: Commit**

```bash
git add replay_tasks/main.py render.yaml webapp/app/services/replay_control_remote.py webapp/app/services/replay_heights_remote.py webapp/tests/replays/test_control_remote.py
git commit -F <message file>     # "Replay time limits: 2 h a round, 6 h a warm build, 23 h a height build"
```

---

### Task 6: The cache budget is configurable, and feature rows never shrink

**Files:**
- Modify: `replay_worker/control_cache.py` (`store`)
- Modify: `replay_worker/server.py` (`Settings.control_cache_reserve`, `from_env`, docstring env list, `_run_cache`,
  `main`'s `archive_extra_reserve`, `/health` `control.cache.reserve`)
- Modify: `render.yaml` (worker: `REPLAY_CONTROL_CACHE_RESERVE_BYTES`)
- Test: `webapp/tests/replays/test_replay_worker_control_cache.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `control_cache.usage`, `/health` `control.cache` (Task 1).
- Produces: `Settings.control_cache_reserve: int` (env `REPLAY_CONTROL_CACHE_RESERVE_BYTES`, default
  `control_cache.RESERVE`); `/health` `control.cache.reserve` = that setting.

- [ ] **Step 1: Write the failing tests**

In `webapp/tests/replays/test_replay_worker_control_cache.py`:

```python
def test_a_smaller_feature_row_file_never_replaces_a_larger_one(tmp_path):
    name = f"feature-rows/Ascent.{'d' * 64}.npz"
    (tmp_path / "feature-rows").mkdir()
    (tmp_path / name).write_bytes(b"x" * 100)
    incoming = tmp_path / ".incoming"
    incoming.mkdir()
    src = incoming / "a.npz"
    src.write_bytes(b"y" * 10)                      # a run that started from fewer rows
    assert control_cache.store(tmp_path, name, src)
    assert (tmp_path / name).read_bytes() == b"x" * 100 and not src.exists()
    src.write_bytes(b"z" * 200)                     # a run that learned more
    assert control_cache.store(tmp_path, name, src)
    assert (tmp_path / name).read_bytes() == b"z" * 200


def test_a_smaller_visibility_file_still_replaces_one_of_the_same_name(tmp_path):
    name = f"Ascent.{'a' * 16}.npz"
    (tmp_path / name).write_bytes(b"x" * 100)
    incoming = tmp_path / ".incoming"
    incoming.mkdir()
    src = incoming / "a.npz"
    src.write_bytes(b"y" * 10)
    assert control_cache.store(tmp_path, name, src)
    assert (tmp_path / name).read_bytes() == b"y" * 10      # only feature rows grow-only: they're rewritten in place
```

In `webapp/tests/replays/test_replay_worker_control_workflow.py`:

```python
def test_the_cache_reserve_is_a_setting_the_store_and_health_use(tmp_path, monkeypatch):
    settings = server.Settings.from_env({"REPLAY_CONTROL_CACHE_RESERVE_BYTES": "12345"})
    assert settings.control_cache_reserve == 12345
    assert server.Settings.from_env({}).control_cache_reserve == server.control_cache.RESERVE
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: `test_a_smaller_feature_row_file_never_replaces_a_larger_one` FAILS (the file is replaced) and the
settings test FAILS (`AttributeError`). The visibility test passes already: it pins what must not change.

- [ ] **Step 3: Implement**

`replay_worker/control_cache.py`, `store`: right after `with _LOCK:`, before `replaced = ...`:

```python
            if _NAME.fullmatch(name)["folder"] and target.is_file() and \
                    Path(src).stat().st_size < target.stat().st_size:
                # Feature rows are rewritten in place by every round that learns some, and parallel rounds post
                # their own copies: one that started from fewer rows must not undo another's. Rows only cache
                # answers (test_feature_visibility_cache), so dropping these costs time, never correctness.
                return True
```

(`finally` already deletes `src`; returning True keeps the run from listing the file as unsent.)

`replay_worker/server.py`:
- `Settings`, after `control_cache_dir`: `control_cache_reserve: int = control_cache.RESERVE   # bytes the cache may hold (REPLAY_CONTROL_CACHE_RESERVE_BYTES)`
  (`control_cache` is imported near the top of `server.py`, above `Settings`).
- `from_env`: `settings.control_cache_reserve = int(env.get("REPLAY_CONTROL_CACHE_RESERVE_BYTES", settings.control_cache_reserve))`
- Docstring env list: `REPLAY_CONTROL_CACHE_RESERVE_BYTES  bytes the Workflow control cache may hold (default 2 GiB); the archive leaves it free.`
- `_run_cache`: `control_cache.store(control.settings.control_cache_dir, name, incoming, reserve=control.settings.control_cache_reserve)`
- `main`: `settings.archive_extra_reserve += control_cache.RESERVE` becomes `... += settings.control_cache_reserve`
- `/health` (Task 1's line): `"reserve": control.settings.control_cache_reserve`

`render.yaml`, worker `envVars`, after `CONTROL_CACHE_DIR`:

```yaml
      # The control cache's budget on the disk (bytes), taken out of the archive's. /health control.cache shows its
      # bytes per map and evictions: raise this if evictions climb (docs/superpowers/plans/2026-11-05-replay-throughput.md).
      - key: REPLAY_CONTROL_CACHE_RESERVE_BYTES
        value: "2147483648"
```

- [ ] **Step 4: Run them to see them pass**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_archive.py -n 0 -q`
Expected: all PASS (`test_a_feature_row_cache_rewritten_in_place_reaches_the_worker_through_the_route` still
passes: its rewrite only grows the file).

- [ ] **Step 5: Commit**

```bash
git add replay_worker/control_cache.py replay_worker/server.py render.yaml webapp/tests/replays/test_replay_worker_control_cache.py webapp/tests/replays/test_replay_worker_control_workflow.py
git commit -F <message file>     # "Control cache: configurable reserve; feature rows never shrink"
```

---

### Task 7: Docs, the full test run, the PR, and the rollout runbook

**Files:**
- Modify: `webapp/RENDER_DEPLOY.md` (new section "Warm builds, time limits and the concurrency ramp")
- Modify: `CLAUDE.md` (one paragraph under "Critical Util" or beside the replay notes: what warm builds are and
  where the switch is)

- [ ] **Step 1: Write the docs.** In `webapp/RENDER_DEPLOY.md`, the section holds: what a warm build is; the
  switch `REPLAY_WARM_BUILDS` (dashboard, `sync: false`, off by default); the limits table from Global
  Constraints; what `/health` now shows (`control.cache`, `control.warm_builds`, `workflow.by_kind`); and the
  runbook below, verbatim. In `CLAUDE.md`, three sentences: warm builds exist, the switch, the doc to read.

- [ ] **Step 2: Run every suite the branch touches**

Run: `../../../../webapp/.venv313/Scripts/python.exe scripts/test_catalog.py plan --git`, then the commands it
prints (offline lane at least; the slow lane too, because `app/control/task.py` and `geometry.py` changed, per
CLAUDE.md "Run the slow lane before merging changes to control"). Compare failures against the known baseline in
memory (`project_test_suite_stale_neon_env`), not against zero: new failures only.

- [ ] **Step 3: Commit the docs, push the branch, open the PR** (never merge). The PR body holds Task 0's result,
  the risk table above with each mitigation's commit, and the runbook. Give the owner the link.

#### Rollout runbook (owner; each step moves on once its checks pass, no waiting days)

Read the worker's `/health` from its Render shell:
`python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/health').read().decode())"`

1. **Merge** (owner). The site, the worker and the replay-tasks workflow deploy. Warm builds stay off.
   - Check: the replay-tasks workflow lists a `warm` task. `/health` shows `control.cache` and `workflow.by_kind`.
     Control runs that start now carry the new limits (the dashboard shows runs past 30:00 instead of cancelled at
     29:59).
2. **Note your workspace plan's run cap** (Workspace settings or https://render.com/docs/workflows-limits: 20 on
   Hobby, 50 on Pro). `CONTROL_MAX_RUNS` is 40: past the cap, runs wait in Render's queue (no longer at a cost,
   since the limit starts when a run starts).
3. **Flip `REPLAY_WARM_BUILDS=true`** on replay-worker (dashboard). The worker restarts.
   - Check within an hour: `control.warm_builds.building` lists keys, then they move to `ready`; `misses` is 0;
     `failures` is empty or under 2 per key; `workflow.by_kind.control.open` climbs past 2 while a key is ready.
   - Stop and switch it off if `misses` grows (keys going wrong: risk 2), or a key reaches 2 failures on a map that
     worked before.
4. **Check the cache budget.** `control.cache.bytes` against `control.cache.reserve`, and `control.cache.evictions`.
   - If `evictions` > 0 or bytes > 60 % of reserve: raise `REPLAY_CONTROL_CACHE_RESERVE_BYTES` in `render.yaml` by
     PR (the archive gives up the same bytes) before step 5.
5. **Ramp the matches in flight** by PR, one value per PR: `CONTROL_MAX_MATCHES` 2 -> 4, then 4 -> 6.
   - Check after each: `workflow.by_kind.control.open` reaches about 20 rounds per two matches; the site's
     `map control dispatch:` log shows `busy` and `unreachable` at 0; `workflow.by_kind.control.oldest_s` stays
     under the round limit (7200); `control.cache.evictions` stays 0.
   - Stop at the step before any check fails, and bring the numbers back here.
6. **Done** when a control recompute holds 30+ open control runs for its whole run, and the dashboard shows no
   runs cancelled by the limit.

## Out of scope (named so they aren't lost)

- Letting gaps-only work fill free slots while control work is still queued (`replay_control_remote._submit` sends
  gaps only when no control is sendable). A site change with its own ordering questions; worth doing after the ramp
  shows whether slots sit idle.
- A progress heartbeat that fails a run that stops progressing. The finite limits plus `/health`'s oldest-run age
  cover risk 6 for now; Render's reconcile already settles a dead machine within about 3 minutes.
- Persisting warm keys across worker restarts. After a deploy, each key's warm build finds its cache on disk and
  answers in seconds, so the gain is small.

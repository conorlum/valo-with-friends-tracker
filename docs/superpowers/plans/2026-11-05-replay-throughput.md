# Replay Task Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep 30+ map-control Workflow runs busy when control needs recomputing, and stop losing work to
30-minute cancellations.

**Architecture:** Workflow runs get long limits (local mode keeps today's), counted from when a run actually starts,
with a timeout and a "started" report settled atomically. A map's visibility cache becomes its own Workflow task, a
*warm build*: one per warm identity (map, heights, features, height mode). Once a probe round confirms it read the
cache, every round of that identity runs at once, each holding a lease on the exact cache file. Each run logs its
phases as it goes and reports where its cache came from, and `/health` shows enough to ramp concurrency in measured
steps.

**Tech Stack:** Python 3.13 stdlib (`replay_worker/`, `replay_tasks/`), numpy (control children), FastAPI site
(`webapp/app/services/`), Render Workflows (`render==1.0.1` SDK), pytest.

**Revision 2 (2026-11-05):** rewritten after two independent reviews of revision 1 (both pasted into the planning
session; their findings are folded in below, tagged F1-F9 and S1-S8/N1-N11 where cited). Owner decisions: build everything in one PR (warm builds included, behind their
switch); the height-build limit comes from the longest measured build (3x it, plus 3 h); feature rows keep today's
last-write-wins (see "Decided: feature-row overwrites" at the end).

## Amendment A1 (2026-11-05): warm builds are ON from the merge

The owner's standing rule (`judgment.md`, Preferences): a new feature switch for the friends site goes into
`render.yaml` **set on from the merge**, and the code rides out the deploy gap by itself without spending tries.
This amendment **wins over every task and runbook line below that says otherwise**:

1. **Task 4, `render.yaml`:** the worker's `REPLAY_WARM_BUILDS` entry is `value: "true"` (not `sync: false`), with
   the comment: "Warm builds (docs/superpowers/plans/2026-11-05-replay-throughput.md): a cold map's visibility cache
   is built as its own run, confirmed by one probe round, then the rest run at once. On from the merge; to switch it
   off, set "false" here by PR (a dashboard change is reset by a Blueprint sync)." `Settings` keeps its default off
   (tests and local runs), so only the deployed worker turns it on.
2. **Task 4, a ninth rule: a refused start spends no try.** In the deploy gap the worker can come up before the
   replay-tasks workflow has registered `warm`, so Render refuses the start (`runs.Runs.start` settles it as `infra`,
   error text beginning `the run could not start`). Such a warm build does **not** count toward `WARM_BUILD_TRIES`:
   the identity's rounds keep waiting, and a new warm build is asked after `WARM_START_RETRY_S = 60`. Code:
   - beside `WARM_BUILD_TRIES`: `WARM_START_RETRY_S = 60   # after Render refused a warm build's start (a deploy gap)`
   - `ControlRunner.__init__`, with the other warm state:
     `self.warm_retry_at: dict[str, float] = {}  # no warm build for this identity before this time (start refused)`
   - `_next`, in the warm-builds branch, just before `if self.warm_failures.get(job.warm_id, 0) < WARM_BUILD_TRIES:`:
     `if self.warm_retry_at.get(job.warm_id, 0.0) > time.time(): continue        # rule 9: its start was refused`
   - settlement, in the warm build's `else:` (not ok) branch, replacing the failure count:
     ```python
                    elif (error or "").startswith("the run could not start"):
                        # Rule 9: Render refused the start (a deploy gap, before the workflow knows `warm`): no try
                        # is spent; the identity's rounds wait and a warm build is asked again after a minute.
                        self.warm_retry_at[job.warm_id] = time.time() + WARM_START_RETRY_S
                    else:
                        self.warm_failures[job.warm_id] = self.warm_failures.get(job.warm_id, 0) + 1
                        _log(f"warm build {job.warm_id} failed: {error or 'its cache is not on this disk'}")
     ```
     (the `if status == "done" and name …:` test above it stays first, so this is `if … / elif … / else`).
   - `counts()["warm_builds"]` gains `"start_refused": sorted(i for i, t in self.warm_retry_at.items() if t > time.time())`.
   - Test, in `test_replay_worker_control_workflow.py` beside the other `warm_remote` tests:
     ```python
     def test_a_refused_warm_start_spends_no_try_and_is_asked_again(warm_remote, monkeypatch):
         control, render, _, _ = warm_remote
         monkeypatch.setattr(server, "WARM_START_RETRY_S", 0.5)
         real, refused = control.runs.api.start, []

         def flaky(slug, args):                    # the workflow hasn't registered `warm` yet: Render answers 404
             if slug.endswith("/warm") and len(refused) < 3:
                 refused.append(slug)
                 raise runs.RenderError("POST /task-runs: HTTP 404")
             return real(slug, args)

         monkeypatch.setattr(control.runs.api, "start", flaky)
         job = control.submit(round_task("a"))
         assert wait_done(control, [job])[0].status == "done"
         assert len(refused) == 3 and control.counts()["warm_builds"]["failures"] == {}
         assert warm_starts(render) == 1                # Render saw only the start it accepted
     ```
3. **Risk 7:** warm builds are on from the merge; a start refused in the deploy gap spends no try (rule 9).
4. **Task 7 docs and the runbook:** the switch is on from the merge in `render.yaml`; turning it off is a PR setting
   it to `"false"`. Runbook step 2 becomes "Run a control recompute and read three control run logs: their
   `visibility … in N s` line against the round's total time, and record it in the PR thread". Step 3 becomes
   "Check warm builds within an hour of the merge: …" with the same checks and stop rule, where "switch it off"
   means a PR setting `REPLAY_WARM_BUILDS` to `"false"`.

## Why (the evidence this plan rests on)

- Every cancelled run in the Render dashboard ended at 29:59 (owner, 2026-11-04). The worker cancels a run itself
  when its deadline passes (`replay_worker/runs.py` `Runs.wait`), and a remote round's deadline is 30 minutes from
  when the worker *opens* it, queue time included (`Settings.control_timeout_s` / `control_warm_timeout_s` = 1800,
  `replay_tasks/main.py` `timeout_seconds=1800`, site `STALE_JOB_S = 1800`).
- A map is cold until one of its rounds finishes and its visibility cache reaches the worker's disk. A cold map runs
  one round at a time (`ControlRunner._next`, `busy_maps` / `warming_maps`). A run posts its caches only after the
  child exits (`replay_tasks/tasks.py` `control`), so a round killed at 29:59 loses the cache it built, its `infra`
  result discards the map's warmth (`server.py` `_run` settlement), and the next round starts from nothing. The
  site retries a round at most 3 times, 300 s apart, then gives it up until it restarts (`MAX_TRIES`, `BACKOFF_S`),
  so a cold map can end up with nothing in progress at all.
- The worker's warm list is in memory only, so every worker deploy makes every map cold.
- Not yet proven: that the 29:59 runs were spending their time on the visibility build rather than on the round
  itself. Task 1's phase log answers it from the next cancelled or slow run's log.
- Render limits compute, not run count (https://render.com/docs/workflows-limits, read 2026-11-05): new runs may
  start with up to 16 CPU per minute in total on Hobby (32 on Pro), and runs over that wait in Render's queue. A
  `flex` run uses up to 1 CPU (render-workflows spec D7), so about 16 runs can start per minute on Hobby. A task's
  `timeout_seconds` may be 30 s to 24 h, per execution attempt, and each retry gets a fresh one
  (https://render.com/docs/workflows-defining).

## Risks and their mitigations

| # | Risk | Mitigation (task) |
|---|------|-------------------|
| 1 | The cause isn't the visibility build | Phase lines in every run's live log; rounds report `visibility_source` (Task 1); the runbook reads them before relying on warm builds (Task 7) |
| 2 | A wrong "warm" releases 30+ rounds that each build the cache | Warm identity includes height mode; a key is ready only by the exact file its warm build named, while that file is on disk; **one probe round** must report `cache` before the rest go; each round **leases** the exact file until it settles, and a round whose file vanished is requeued and the key re-warmed, never run cold (Task 4) |
| 3 | The cache budget makes maps evict each other | `/health` shows bytes per map, budget evictions and per-map prunes; the reserve is a setting; the runbook checks the archive's free space before raising it (Tasks 1, 6, 7) |
| 4 | The worker or site becomes the bottleneck | `/health` shows, per kind, runs waiting for Render, runs started, and the oldest of each; the ramp goes in steps with stop rules (Tasks 2, 7) |
| 5 | Parallel rounds overwrite each other's feature rows | Accepted (owner): last write wins; kept rows still reach every later round. Task 1 logs rows reused vs built. Costs time only: `test_feature_visibility_cache.py::test_cached_visibility_matches_original_*` proves cached rows give the uncached answers |
| 6 | Long limits hide hung runs | Finite limits; the height limit is set from measured builds; `/health` shows the oldest started run per kind (Tasks 2, 5) |
| 7 | The services deploy separately | Warm builds behind `REPLAY_WARM_BUILDS` (off by default, `sync: false`); `started` is best effort both ways; new limit settings that an old worker ignores (Tasks 2, 4, 5) |
| 8 | Test drift | Scheduling only: no revision bump; each task runs `scripts/test_catalog.py plan --git` (every task) |
| 9 | A worker deploy (every merge restarts the single-instance worker) now loses up to 2 h of a round or 6 h of a warm build, and Render keeps running the orphan | Runbook: merge only while no warm build is building and control is quiet (Task 7). Saving warm-build tokens across restarts is out of scope |
| 10 | New worker + old task image: Render's old 1800 s limit ends a round and retries it once | The same runbook rule: merge while control is quiet, then check both images are up before a recompute (Task 7) |
| 11 | Local mode drifts under the new limits | Workflow-only limit settings and site backstops; a test pins local's 1800 / 3600 / 1800 / 7200 (Task 5) |

## Global Constraints

- Repo is PUBLIC: no credentials, no secrets, no run tokens, no dashboard values beyond what `render.yaml` holds.
- `replay_worker/server.py`, `replay_worker/runs.py`, `replay_worker/control_cache.py` and `replay_tasks/tasks.py`
  stay **standard library only**. Only `replay_worker/control_job.py`, `replay_worker/warm_job.py` and `app.control`
  may import numpy, and only inside the child (`tests/replays/test_control_isolation.py`).
- Never change what a control round computes: no change to `app/control/engine.py`, `CONTROL_REVISION`,
  `GEOMETRY_VERSION` or `cache_key`.
- **Local mode keeps today's behaviour exactly**, including its limits: `REPLAY_CONTROL_TIMEOUT_S`,
  `REPLAY_CONTROL_WARM_TIMEOUT_S`, `REPLAY_HEIGHT_TIMEOUT_S`, site `STALE_JOB_S = 1800` and `STALE_BUILD_S = 7200`
  are untouched. Warm builds apply only when `control_runner == "workflow"`, a run table exists, and
  `REPLAY_WARM_BUILDS` is on.
- Never merge. The furthest this goes is an open PR whose link goes to the owner.
- Tests run from `<worktree>/webapp` with the main checkout's interpreter (worktrees have no venv):
  `../../../../webapp/.venv313/Scripts/python.exe -m pytest <files> -n 0 -q`. Before each commit,
  `python scripts/test_catalog.py plan --git` names other suites the changed files need; run them too.
- New tests go into the existing test files named in each task (`tests/suites.toml` already lists them). A new file
  under `replay_worker/` is already guarded by `"../replay_worker/**"`.
- Commit messages: write them to a scratch file and `git commit -F <file>` (no heredocs), ending with the
  `Co-Authored-By` line the session gives.
- Workflow time limits (Task 5 pins their order in a test). The height limit is 3 h (Task 0).

  | What | Worker setting (Workflow runs only) | Render task `timeout_seconds` | Site backstop (Workflow only) |
  |---|---|---|---|
  | control round (warming or not) | `REPLAY_CONTROL_RUN_TIMEOUT_S` 7200 | 7500 | `STALE_RUN_S` 15000 (> 2 x 7200) |
  | warm build | `REPLAY_WARM_BUILD_TIMEOUT_S` 21600 | 21900 | none: the site never sees warm builds |
  | height build | `REPLAY_HEIGHT_RUN_TIMEOUT_S` 10800 | 11100 | `STALE_BUILD_RUN_S` 22200 (> 2 x 10800) |

  A run's deadline never passes `opened + 2 x limit` (Task 2), which is why each site backstop exceeds twice the
  limit: the worst case is a queue wait plus a full attempt, or two attempts.

---

### Task 0: Measure the height builds (owner, no code)

**Files:** none.

- [x] **Step 1: The longest height build.** Owner, 2026-11-05: the longest of the last ~200 completed `heights`
  runs took about 2 minutes. Owner's choice: a 3 h limit (`REPLAY_HEIGHT_RUN_TIMEOUT_S` 10800), far above it.
- [x] **Step 2: A cancelled control run's log.** Owner, 2026-11-05: it shows no log lines at all.
- [x] **Step 3: Did the cancelled runs ever start?** Yes (owner, 2026-11-05, Render dashboard): a cancelled control
  run was queued for 9 ms, then its one attempt ran 30 min (Nov 4 11:35:06 PM to Nov 5 12:05:06 AM) until the
  worker cancelled it. Render isn't holding runs back; the run was working. Its empty log is expected today:
  `tasks._run_child` captures the child's stderr and discards it, and the task itself writes nothing. Task 1's
  phase lines fix that and show whether the 30 min went into the visibility build or the round. **Gate passed.**

---

### Task 1: Runs log their phases live and report where their cache came from; `/health` shows the cache

**Files:**
- Modify: `webapp/app/control/geometry.py` (`visibility`, new `visibility_name`)
- Modify: `webapp/app/control/task.py` (new `_phase`; `_load`; `compute_task`)
- Modify: `replay_tasks/tasks.py` (`_child_env`, `_run_child`)
- Modify: `replay_worker/control_cache.py` (`store`, new `EVICTIONS`, `KEPT_PRUNED`, `usage`)
- Modify: `replay_worker/server.py` (`/health` body)
- Test: `webapp/tests/replays/test_control_task.py`, `webapp/tests/replays/test_replay_worker_control_cache.py`,
  `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Produces: `geometry.visibility_name(geo) -> str` (`"<Map>.<16 hex>.npz"`); `task._phase(message: str) -> None`
  (writes `control phase: <message>` to stderr only when env `CONTROL_PHASE_LOG` is set); `compute_task` result keys
  `"visibility_source"` (`"cache"` | `"built"`) and `"visibility_s"` (float) whenever the geometry loaded, on success
  and on a round's own (`engine`) failure; `tasks._child_env` sets `CONTROL_PHASE_LOG=1`; `tasks._run_child` passes
  the child's stderr straight through to the run's own stderr (Render's log), reading only stdout;
  `control_cache.EVICTIONS: int`, `control_cache.KEPT_PRUNED: int`; `control_cache.usage(cache_dir) -> {"bytes",
  "maps": {map: bytes}, "evictions", "kept_pruned"}`; `/health` `control.cache` = `usage(...)` + `"reserve"`.

- [ ] **Step 1: Write the failing tests**

`webapp/tests/replays/test_control_task.py`: the file puts only `HERE` on `sys.path`; add
`sys.path.insert(0, str(HERE.parents[2]))` (the repo root, for `replay_worker`) on the line after it. Then, after
`test_a_map_the_machine_has_no_geometry_for_is_an_infra_failure`:

```python
def test_a_round_says_where_its_visibility_cache_came_from(toy):
    toy.visibility_source, toy.visibility_s = "cache", 0.25
    t, _ = make_task(toy)
    result = task.compute_task(t)
    assert result["status"] == "ok"
    assert result["visibility_source"] == "cache" and result["visibility_s"] == 0.25


def test_a_round_the_engine_refuses_still_says_where_its_cache_came_from(toy):
    toy.visibility_source = "built"
    players = {0: (None, [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}
    t, _ = make_task(toy, sides={}, players=players)     # as test_a_round_the_engine_refuses_is_the_rounds_own_failure
    result = task.compute_task(t)
    assert result["status"] == "failed" and result["error_kind"] == "engine"
    assert result["visibility_source"] == "built"


def test_the_visibility_cache_name_is_one_the_worker_takes_for_that_map(toy):
    import re

    from app.control import geometry
    from replay_worker import control_cache

    name = geometry.visibility_name(toy)
    assert re.fullmatch(rf"{toy.name}\.[0-9a-f]{{16}}\.npz", name)
    assert control_cache.valid(name, toy.name) and control_cache.visibility_name(name)


def test_the_feature_row_line_reports_reuse_only_when_a_row_cache_exists(toy):
    from types import SimpleNamespace

    assert task._rows_line(toy) is None                       # no feature rows on this map: no line
    toy._feature_sight_cache = SimpleNamespace(row_hits=30, row_builds=10)
    assert task._rows_line(toy) == "Open: feature rows 30 reused, 10 built"


def test_phase_lines_go_to_stderr_only_when_asked(monkeypatch, capsys):
    task._phase("quiet")
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("CONTROL_PHASE_LOG", "1")
    task._phase("Ascent: visibility start")
    assert capsys.readouterr().err == "control phase: Ascent: visibility start\n"
```

`webapp/tests/replays/test_replay_worker_control_cache.py`:

```python
def test_usage_counts_bytes_per_map_and_the_counters(tmp_path):
    (tmp_path / "feature-rows").mkdir()
    (tmp_path / f"Ascent.{'a' * 16}.npz").write_bytes(b"x" * 10)
    (tmp_path / "feature-rows" / f"Ascent.{'d' * 64}.npz").write_bytes(b"y" * 5)
    (tmp_path / f"Haven.{'b' * 16}.npz").write_bytes(b"z" * 7)
    found = control_cache.usage(tmp_path)
    assert found["maps"] == {"Ascent": 15, "Haven": 7} and found["bytes"] == 22
    assert found["evictions"] == control_cache.EVICTIONS and found["kept_pruned"] == control_cache.KEPT_PRUNED


def test_a_store_that_drops_files_for_room_counts_evictions(tmp_path):
    old = tmp_path / f"Ascent.{'a' * 16}.npz"
    old.write_bytes(b"x" * 60)
    (tmp_path / ".incoming").mkdir()
    src = tmp_path / ".incoming" / "n.npz"
    src.write_bytes(b"y" * 60)
    before = control_cache.EVICTIONS
    assert control_cache.store(tmp_path, f"Haven.{'b' * 16}.npz", src, reserve=100)
    assert not old.exists() and control_cache.EVICTIONS == before + 1


def test_a_store_past_kept_counts_the_prune(tmp_path):
    import os
    import time

    for i, key in enumerate("abc"):
        path = tmp_path / f"Ascent.{key * 16}.npz"
        path.write_bytes(b"x")
        os.utime(path, (time.time() - 100 + i, time.time() - 100 + i))
    (tmp_path / ".incoming").mkdir()
    src = tmp_path / ".incoming" / "n.npz"
    src.write_bytes(b"y")
    before = control_cache.KEPT_PRUNED
    assert control_cache.store(tmp_path, f"Ascent.{'d' * 16}.npz", src)
    assert not (tmp_path / f"Ascent.{'a' * 16}.npz").exists() and control_cache.KEPT_PRUNED == before + 1
```

`webapp/tests/replays/test_replay_worker_control_workflow.py`, after `test_engine_failure_settles_engine`:

```python
def test_health_shows_the_control_cache(remote):
    import urllib.request

    control, _, _, base = remote
    warm_up(control)
    with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
        body = json.loads(response.read())
    assert body["control"]["cache"]["maps"] == {"Ascent": len(b"built")}
    assert body["control"]["cache"]["reserve"] > 0


def test_a_childs_stderr_reaches_the_runs_own_log(tmp_path, monkeypatch, capfd):
    say = tmp_path / "say.py"
    say.write_text("import json, os, sys\n"
                   "sys.stderr.write('control phase: ' + os.environ.get('CONTROL_PHASE_LOG', '-') + '\\n')\n"
                   "print(json.dumps({'status': 'ok'}))\n", encoding="utf-8")
    monkeypatch.setenv("REPLAY_CONTROL_CMD", json.dumps([sys.executable, str(say)]))
    monkeypatch.setenv("REPLAY_CONTROL_MEMORY_MB", "0")
    answer = tasks._run_child("REPLAY_CONTROL_CMD", [], b"{}", tmp_path)
    assert answer == {"status": "ok"}
    assert "control phase: 1" in capfd.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_task.py tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: the ten new tests FAIL (missing result keys, `visibility_name`, `_phase`, `_rows_line`, `usage`, `KEPT_PRUNED`,
`cache` in `/health`, and the child's stderr captured instead of passed through).

- [ ] **Step 3: Implement**

`webapp/app/control/geometry.py`, above `def visibility(`:

```python
def visibility_name(geo: Geometry) -> str:
    """The file name of the map's visibility cache for exactly this geometry: what `visibility` reads and writes,
    and what a warm build reports to the replay worker (replay_worker/control_cache.py takes the same shape)."""
    return f"{geo.name}.{cache_key(geo)[:16]}.npz"
```

and in `visibility` replace `path = directory / f"{geo.name}.{cache_key(geo)[:16]}.npz"` with
`path = directory / visibility_name(geo)`.

`webapp/app/control/task.py`: add `import os` to the imports, then above `def _load(`:

```python
def _phase(message: str) -> None:
    """One progress line on stderr, only when CONTROL_PHASE_LOG is set (replay_tasks sets it for Workflow runs):
    the run's log then shows where its time went even when the run is killed before it answers."""
    if os.environ.get("CONTROL_PHASE_LOG"):
        sys.stderr.write(f"control phase: {message}\n")
        sys.stderr.flush()
```

In `_load`, wrap the existing `geometry.visibility(geo)` call (inside its `try:`):

```python
        _phase(f"{name}: visibility start")
        try:
            geometry.visibility(geo)
        except Exception:
            ...                                     # unchanged
        _phase(f"{name}: visibility {geo.visibility_source} in {geo.visibility_s:.1f} s")
```

Below `_phase`, add:

```python
def _rows_line(geo) -> str | None:
    """How many feature-row answers this round reused from the map's shared rows file and how many it worked out
    itself, or None on a map that has none (it never creates the row cache just to report it)."""
    cache = getattr(geo, "_feature_sight_cache", None)
    if cache is None:
        return None
    return f"{geo.name}: feature rows {cache.row_hits} reused, {cache.row_builds} built"
```

In `compute_task`: add `geo = None` as the first line of the second `try:` block (before `options = ...`). After
`rc = engine.compute_round(blob, geo, link, observer=guard)` add
`_phase(f"{task['map']}: round computed at {time.time() - started:.1f} s")` and
`rows = _rows_line(geo)` / `if rows: _phase(rows)`, and after the `result["gaps"] = ...`
line inside `if job:` add `_phase(f"{task['map']}: gaps done at {time.time() - started:.1f} s")`. Replace the
function's last two lines with:

```python
    result.update({"key": task.get("key"), "seconds": time.time() - started, "peak": peak_memory()})
    if geo is not None and getattr(geo, "visibility_source", None):
        # Whether this round found the map's visibility cache or had to build it: the replay worker's warm builds
        # read it (a round that built it means its key wasn't really warm).
        result.update({"visibility_source": geo.visibility_source, "visibility_s": geo.visibility_s})
    return result
```

`replay_tasks/tasks.py`:
- `_child_env`: `env = {**os.environ, **CHILD_THREADS, "CONTROL_CACHE_DIR": str(cache), "CONTROL_PHASE_LOG": "1"}`
- `_run_child`: replace `"capture_output": True` in `kwargs` with `"stdout": subprocess.PIPE` (stderr is then
  inherited: the child's lines reach Render's log as they are written). Nothing reads `done.stderr`. Add to its
  docstring: "The child's stderr goes straight to this run's log, live."

`replay_worker/control_cache.py`, under `_LOCK = ...`:

```python
EVICTIONS = 0                     # files `store` dropped for room since this process started (/health)
KEPT_PRUNED = 0                   # older generations `store` dropped past KEPT since this process started (/health)
```

In `store`, make `global EVICTIONS, KEPT_PRUNED` the first line of the body (after the docstring). Inside
`if over > 0:`, just before `for path in victims:`, add `EVICTIONS += len(victims)`. Replace the KEPT prune loop

```python
            for old in _newest(target.parent, map_name)[KEPT:]:
                old.unlink(missing_ok=True)
```

with

```python
            for old in _newest(target.parent, map_name)[KEPT:]:
                old.unlink(missing_ok=True)
                KEPT_PRUNED += 1
```

After `has_visibility`, add:

```python
def usage(cache_dir: Path) -> dict:
    """What /health shows of the cache: every byte under it (incoming files too), each map's visibility and
    feature-row bytes, and the files `store` dropped since this process started."""
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
    return {"bytes": _tree_bytes(cache_dir), "maps": dict(sorted(per_map.items())), "evictions": EVICTIONS,
            "kept_pruned": KEPT_PRUNED}
```

`replay_worker/server.py`, `/health` handler, after the `body["control"] = {...}` statement:

```python
                if control is not None:
                    body["control"]["cache"] = {**control_cache.usage(control.settings.control_cache_dir),
                                                "reserve": control_cache.RESERVE}
```

- [ ] **Step 4: Run them to see them pass**

Run: the Step 2 command. Expected: all PASS, old and new.

- [ ] **Step 5: Commit** (`git add` the files listed above; message: "Replay runs log their phases live and report
  where their cache came from; /health shows the cache")

---

### Task 2: A run's limit counts from when it starts; a timeout and a start can't cross

**Files:**
- Modify: `replay_worker/runs.py` (`Run`, `Runs.open`, `Runs.wait`, new `Runs.started`, `Runs.status`)
- Modify: `replay_worker/server.py` (route `POST /runs/{job}/started`; module docstring route list)
- Modify: `replay_tasks/tasks.py` (`WorkerLink.post_started`; first statement of `control` and `heights`)
- Test: `webapp/tests/replays/test_replay_worker_runs.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`,
  `webapp/tests/replays/test_replay_worker_heights_workflow.py` (its `FakeLink` only)

**Interfaces:**
- Produces: `Run.opened`, `Run.timeout_s`, `Run.extend_on_start`, `Run.started_at`, `Run.timing_out`;
  `Runs.started(job_id, token) -> bool`; route `POST /runs/{job}/started` (header `X-Run-Token`, empty body): 200
  `{"ok": true}` or 409 `{"ok": false}`; `WorkerLink.post_started() -> None` (never raises an `OSError`);
  `Runs.status()["by_kind"] -> {kind: {"open", "waiting", "oldest_wait_s", "oldest_run_s"}}`.

Rules:
1. A run opened **without** an explicit `deadline` (control, warm, heights, ping) starts with
   `deadline = opened + timeout_s`, as today.
2. Each `started` post (one per Render attempt) sets `started_at = now` and
   `deadline = min(max(deadline, now + timeout_s), opened + 2 * timeout_s)`. So a retried attempt gets a full limit
   unless the run has already used up twice its limit.
3. A run opened **with** a `deadline` (parse: its job's one deadline, saved for restarts) records `started_at` but its
   deadline never moves.
4. `wait` claims a timeout under the run table's lock (`timing_out = True`) before it cancels. A `started` post that
   arrives after the claim is refused (409), and one that arrives before it moves the deadline, so the claim
   re-reads the new deadline and doesn't fire.
5. An older task image never posts `started`, so rule 1's deadline holds. An older worker answers the post with a
   404, which `post_started` swallows.

- [ ] **Step 1: Write the failing tests**

`webapp/tests/replays/test_replay_worker_runs.py`, after `test_deadline_cancels_and_settles_timeout`:

```python
def test_each_start_gives_a_full_limit_up_to_twice_the_limit_from_opening(render, tmp_path):
    now = [100.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("control", "j1", {}, timeout_s=50)
    assert run.deadline == 150.0
    now[0] = 140.0                                   # 40 s in Render's queue
    assert table.started("j1", run.token)
    assert run.deadline == 190.0 and run.started_at == 140.0
    now[0] = 170.0                                   # Render retried: a second attempt starts
    assert table.started("j1", run.token)
    assert run.deadline == 200.0                     # min(170 + 50, 100 + 2 x 50)


def test_started_never_moves_an_explicit_deadline(render, tmp_path):
    now = [100.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    run = table.open("parse", "j2", {}, 0, deadline=130.0)
    now[0] = 120.0
    assert table.started("j2", run.token)
    assert run.deadline == 130.0 and run.started_at == 120.0


def test_started_refuses_a_wrong_token_an_unknown_job_or_a_claimed_timeout(render, tmp_path):
    table = make_runs(render, tmp_path)
    run = table.open("control", "j3", {}, timeout_s=50)
    assert not table.started("j3", "nope")
    assert not table.started("nobody", "nope")
    run.timing_out = True                            # wait() has claimed the timeout
    assert not table.started("j3", run.token)


def test_status_counts_waiting_and_started_runs_by_kind(render, tmp_path):
    now = [100.0]
    table = make_runs(render, tmp_path, clock=lambda: now[0])
    a = table.open("control", "c1", {}, timeout_s=600)
    table.open("control", "c2", {}, timeout_s=600)
    table.open("heights", "h1", {}, timeout_s=600)
    now[0] = 130.0
    table.started("c1", a.token)
    now[0] = 145.0
    assert table.status()["by_kind"] == {
        "control": {"open": 2, "waiting": 1, "oldest_wait_s": 45.0, "oldest_run_s": 15.0},
        "heights": {"open": 1, "waiting": 1, "oldest_wait_s": 45.0, "oldest_run_s": 0.0}}
```

`webapp/tests/replays/test_replay_worker_control_workflow.py`:
1. Add to `FakeLink`: `def post_started(self): self.order.append("started")`.
2. The two existing `link.order` assertions now begin with `"started"`: in
   `test_control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first`,
   `["started", f"cache/Ascent.{'c' * 16}.npz", "result"]`; in `test_control_task_posts_a_cache_rewritten_in_place`,
   `["started", route, "result"]`.
3. Add:

```python
def post_started_to(base, job, token):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(f"{base}/runs/{job}/started", data=b"", method="POST",
                                     headers={"X-Run-Token": token, "Content-Length": "0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_started_route_moves_the_deadline_and_refuses_a_wrong_token(remote):
    control, _, _, base = remote
    table = control.runs
    now = [1000.0]
    table.clock = lambda: now[0]
    run = table.open("control", "manual", {}, timeout_s=50)
    assert post_started_to(base, "manual", "wrong") == 409 and run.deadline == 1050.0
    now[0] = 1010.0
    assert post_started_to(base, "manual", run.token) == 200 and run.deadline == 1060.0
    table.close(run)


def test_an_older_worker_without_the_route_answers_404_and_the_task_carries_on(tmp_path):
    from fake_render import closed_port

    settings = server.Settings(temp_root=tmp_path / "tmp")
    httpd = server.make_server(server.Worker(settings, on_parse=None), runs_table=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert post_started_to(base, "j", "t") == 404
        tasks.WorkerLink(base, "j", "t", timeout_s=5).post_started()                       # 404: no raise
        tasks.WorkerLink(f"http://127.0.0.1:{closed_port()}", "j", "t", timeout_s=1).post_started()   # refused
    finally:
        httpd.shutdown()
        httpd.server_close()
```

`webapp/tests/replays/test_replay_worker_heights_workflow.py`: its `FakeLink` has no `order` list. Add a no-op:
`def post_started(self): pass`.

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_runs.py tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_heights_workflow.py -n 0 -q`
Expected: the six new tests FAIL (`started`, `by_kind`, 404 instead of 409, `post_started`), and the two edited
`order` assertions FAIL. The heights tests still pass (`tasks.heights` doesn't call `post_started` yet).

- [ ] **Step 3: Implement**

`replay_worker/runs.py`, `Run`: after `done`, add

```python
    opened: float = 0.0             # when the worker opened it
    timeout_s: float = 0.0          # its limit, given again from each start (Runs.started)
    extend_on_start: bool = False   # False for a run opened with an explicit deadline (a parse): it never moves
    started_at: float | None = None  # the latest attempt's start, as the run reported it
    timing_out: bool = False        # wait() has claimed the timeout: a late start is refused
```

`Runs.open` (replace the `run = Run(...)` statement):

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
        """An attempt of the run says it has begun its work. Each attempt gets a full limit from now, but never past
        twice the limit from opening (Render gives a retry a fresh limit; the site's backstop allows twice ours). A
        run opened with an explicit deadline (a parse) never moves. False when no open run takes the token, or wait()
        has already claimed its timeout."""
        run = self.accepts(job_id, token)
        if run is None:
            return False
        with self.lock:
            if run.done.is_set() or run.timing_out:
                return False
            now = self.clock()
            run.started_at = now
            if run.extend_on_start:
                run.deadline = min(max(run.deadline, now + run.timeout_s), run.opened + 2 * run.timeout_s)
        return True
```

`Runs.wait` (replace the method):

```python
    def wait(self, run: Run) -> dict:
        """Blocks until the run settles or its deadline passes, closes it, and returns its answer. The timeout is
        claimed under the lock, so a start that lands first moves the deadline and one that lands after is refused;
        Render is told to cancel outside the lock."""
        while not run.done.wait(timeout=0.2):
            with self.lock:
                expired = not run.done.is_set() and not run.timing_out and self.clock() >= run.deadline
                if expired:
                    run.timing_out = True
            if expired:
                if run.render_id:
                    try:
                        self.api.cancel(run.render_id)
                    except RenderError:
                        pass
                self._settle(run, failure("timeout", "the run timed out"))
        self.close(run)
        return run.result
```

`Runs.status` (replace the method):

```python
    def status(self) -> dict:
        now = self.clock()
        with self.lock:
            open_runs = list(self.open_runs.values())
        by_kind: dict[str, dict] = {}
        for run in open_runs:
            entry = by_kind.setdefault(run.kind, {"open": 0, "waiting": 0, "oldest_wait_s": 0.0,
                                                  "oldest_run_s": 0.0})
            entry["open"] += 1
            if run.started_at is None:            # Render hasn't started it (or an old task image never says)
                entry["waiting"] += 1
                entry["oldest_wait_s"] = max(entry["oldest_wait_s"], round(now - run.opened, 1))
            else:
                entry["oldest_run_s"] = max(entry["oldest_run_s"], round(now - run.started_at, 1))
        return {"open": len(open_runs), "by_kind": by_kind, "starts_per_min": self.pacer.per_min,
                "last_reconcile": self.last_reconcile, "last_error": self.last_error}
```

`replay_worker/server.py`: in the module docstring's route list, after `POST /runs/{job}/result`, add
`POST /runs/{job}/started  (header X-Run-Token) an attempt has begun: its limit counts from now. 200; 409.`
In `make_handler`, beside `_run_result`:

```python
        def _run_started(self):
            parts = self.path.split("/")          # ["", "runs", job, "started"]
            ok = len(parts) == 4 and runs_table.started(parts[2], self.headers.get("X-Run-Token"))
            return self._send(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"ok": ok})
```

and in `do_POST`, inside the `/runs/` block after the `/result` line:
`if self.path.endswith("/started"): return self._run_started()`.

`replay_tasks/tasks.py`, `WorkerLink`, after `post_result`:

```python
    def post_started(self) -> None:
        """Tells the worker this attempt has begun, so its limit counts from now, not from when it was queued. Best
        effort: an older worker answers 404 and a run must carry on either way."""
        request = urllib.request.Request(self._url("started"), data=b"", method="POST",
                                         headers={"X-Run-Token": self.token, "Content-Length": "0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response.read()
        except OSError:
            pass
```

Make `link.post_started()` the first statement of `control(args, link)` and of `heights(args, link)`. Not `parse`:
its deadline is explicit, and its `started_at` isn't needed.

- [ ] **Step 4: Run them to see them pass** (the Step 2 command). Expected: all PASS.

- [ ] **Step 5: Commit** (message: "A Workflow run's limit counts from when each attempt starts")

---

### Task 3: The warm build task: a map's visibility cache, built by itself

**Files:**
- Modify: `webapp/app/control/task.py` (new `_load_options`, new `warm_task`; `compute_task` uses `_load_options`)
- Create: `replay_worker/warm_job.py`
- Modify: `replay_tasks/tasks.py` (new `warm`; `TASKS`), `replay_tasks/main.py` (register `warm`)
- Test: `webapp/tests/replays/test_control_task.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `geometry.visibility_name`, `task._phase` (Task 1); `WorkerLink.post_started` (Task 2).
- Produces: `task.warm_task(task) -> dict`: `{"status": "ok", "visibility": "<Map>.<16 hex>.npz",
  "visibility_source", "visibility_s", "key", "seconds", "peak"}` or `{"status": "failed", "error_kind": "infra",
  "error", "key", "seconds", "peak"}`. Its input is a control task without `blob`, `link`, `gaps` and `gaps_only`.
  `replay_worker/warm_job.py`: stdin task JSON, stdout answer JSON. `tasks.warm(args, link)`; `tasks.TASKS["warm"]`;
  Render task `replay-tasks/warm` (limit 21900 s); env override `REPLAY_WARM_CMD` (JSON argv).

- [ ] **Step 1: Write the failing tests**

`webapp/tests/replays/test_control_task.py`:

```python
def test_a_warm_build_names_the_cache_file_its_rounds_will_read(toy):
    from app.control import geometry

    toy.visibility_source, toy.visibility_s = "built", 1.5
    result = task.warm_task({"key": "warm:Open", "map": toy.name})
    assert result["status"] == "ok" and result["key"] == "warm:Open"
    assert result["visibility"] == geometry.visibility_name(toy)
    assert result["visibility_source"] == "built" and result["visibility_s"] == 1.5


def test_a_warm_build_for_a_map_with_no_geometry_is_an_infra_failure():
    result = task.warm_task({"key": "w", "map": "NoSuchMap"})
    assert result["status"] == "failed" and result["error_kind"] == "infra"
    assert "GeometryError" in result["error"]


def test_a_warm_build_loads_with_the_rounds_own_height_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(task, "_load", lambda *a, **k: seen.append(k) or (_ for _ in ()).throw(RuntimeError("x")))
    task.warm_task({"key": "w", "map": "Ascent", "height_mode": "flat", "features": "f" * 64})
    assert seen == [{"height_mode": "flat", "exact": "f" * 64}]
```

`webapp/tests/replays/test_replay_worker_control_workflow.py`, below `STUB`:

```python
WARM_STUB = """
import json, os, sys, pathlib
task = json.loads(sys.stdin.buffer.read())
if task["map"] == "Broken":
    print(json.dumps({"status": "failed", "error_kind": "infra", "error": "no geometry", "key": task["key"]}))
    sys.exit(0)
cache = pathlib.Path(os.environ["CONTROL_CACHE_DIR"])
name = f"{task['map']}.{(task.get('height_mode') or 'e')[0] * 16}.npz"
source = "cache" if (cache / name).is_file() else "built"
if source == "built":
    (cache / name).write_bytes(b"vis")
print(json.dumps({"status": "ok", "visibility": name, "visibility_source": source, "visibility_s": 0.1,
                  "seconds": 0.2, "key": task["key"]}))
"""


@pytest.fixture
def warm_stub(tmp_path, monkeypatch):
    path = tmp_path / "warm_stub.py"
    path.write_text(WARM_STUB, encoding="utf-8")
    monkeypatch.setenv("REPLAY_WARM_CMD", json.dumps([sys.executable, str(path)]))
    return path


def test_warm_task_posts_the_new_cache_then_the_answer_naming_it(warm_stub):
    link = FakeLink({"task": json.dumps({"key": "warm:Ascent", "map": "Ascent"}).encode()})
    tasks.warm({"job_id": "j", "token": "t", "map": "Ascent", "files": []}, link)
    name = f"Ascent.{'e' * 16}.npz"
    assert link.order == ["started", f"cache/{name}", "result"]
    assert link.result["visibility"] == name and link.result["visibility_source"] == "built"


def test_warm_task_with_the_cache_already_there_posts_only_the_answer(warm_stub):
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

(The stub names a `flat` task's file `Ascent.ffff….npz` and any other `Ascent.eeee….npz`, so Task 4 can test that
two height modes need two files.)

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_task.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: the six new tests FAIL (`warm_task`, `tasks.warm`, `'warm' in names`).

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
    replay worker counts the task's warm identity as ready while that file is on its disk."""
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

and in `compute_task` replace the three `options` lines and the `_load` call with
`geo = _load(task["map"], task.get("heights"), task.get("height"), **_load_options(task))`.

Create `replay_worker/warm_job.py`:

```python
"""A map's visibility cache, built (or found) by itself before any round of its warm identity runs: the replay
worker's warm build (docs/superpowers/plans/2026-11-05-replay-throughput.md). Same task JSON as control_job, same
control venv interpreter; the server never imports this module.

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
    """One warm identity's visibility cache: the warm child alone, then the cache posted before the answer naming it
    (the worker counts the identity ready only once that file is on its disk)."""
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

`TASKS = {"ping": ping, "control": control, "parse": parse, "heights": heights, "warm": warm}`.

`replay_tasks/main.py`, after the `control` task block (above the "Later steps add" comment):

```python
@app.task(name="warm", timeout_seconds=21900, retry=Retry(max_retries=1, wait_duration_ms=5000))
def warm(ctx: TaskContext, args: dict) -> dict:
    tasks.warm(args, tasks.link_for(args))
    return {"ok": True}
```

Add to the docstring: "warm (a map's visibility cache by itself) runs on `flex`, its limit 300 s above the
worker's `REPLAY_WARM_BUILD_TIMEOUT_S`."

- [ ] **Step 4: Run them to see them pass**

Run: the Step 2 command, then `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_isolation.py -n 0 -q`.
Expected: all PASS.

- [ ] **Step 5: Commit** (message: "The warm build: a map's visibility cache as its own Workflow task")

---

### Task 4: The worker schedules warm builds, probes, and leases the exact cache file

**Files:**
- Modify: `replay_worker/control_cache.py` (new `_LEASES`, `lease`, `unlease`; `_evictable` and the KEPT prune skip
  leased files)
- Modify: `replay_worker/server.py` (`Settings`, `from_env`, docstring env list, `WARM_BUILD_TRIES`,
  `WARM_SECONDS_KEPT`; `ControlJob` new fields; `ControlRunner` `__init__`, `submit`, `remote`, `counts`, new
  `uses_warm_builds` / `_ready` / `_ask_warm`, `_next`, `_run`, `_run_remote`; `_run_cache` accepts `warm` runs)
- Modify: `render.yaml` (worker: `REPLAY_WARM_BUILDS`, `sync: false`)
- Test: `webapp/tests/replays/test_replay_worker_control_cache.py`,
  `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `tasks.warm` (Task 3); results' `visibility_source` and `seconds` (Tasks 1, 3).
- Produces: `control_cache.lease(cache_dir, name) -> bool`, `control_cache.unlease(name) -> None`;
  `Settings.warm_builds: bool` (env `REPLAY_WARM_BUILDS`: `"1"`/`"true"`, default off);
  `Settings.warm_build_timeout_s: float` (env `REPLAY_WARM_BUILD_TIMEOUT_S`, default 21600.0);
  `server.WARM_BUILD_TRIES = 2`; `ControlJob.warm_id: str`, `ControlJob.released_name: str | None`;
  `ControlJob.kind` may be `"warm"`; `ControlRunner.counts()["warm_builds"]` (only while on) =
  `{"ready": [ids], "confirmed": [ids], "building": [ids], "failures": {id: n}, "misses": int,
  "recent_seconds": [float]}`.

Rules (only while `uses_warm_builds()`; local mode and the switch-off path keep today's code):
1. **Identity.** `warm_id` = today's `warm_key` + `|` + the task's `height_mode` (empty when absent). Two height
   modes on one map and digest are two identities (review F3/S3). The local path keeps using `warm_key`.
2. **Ready.** An identity is ready while `warm_names[id]` is set **and** that file is on this disk.
3. **Warm build.** A round whose identity isn't ready waits, and the identity gets one warm build, queued ahead of
   every round. It counts against `control_runs`. An ok answer naming a valid cache of its map, not in
   `cache_unsent`, with the file on disk, sets `warm_names[id]`, clears its failures and drops any old confirmation.
   Anything else increments its failures.
4. **Fallback.** After `WARM_BUILD_TRIES` failed warm builds, the identity's rounds fall back to today's rule (one
   round warms the map alone, through `self.warm`). A worker restart clears the count.
5. **Probe.** A ready identity that isn't confirmed releases **one** round (the probe) and holds the rest. A probe that
   reports `visibility_source == "cache"` confirms the identity, and every round then goes at once. A probe that
   reports `"built"` is a miss (rule 7). A probe with no `visibility_source` (a machine failure) frees the probe slot,
   so the next round probes.
6. **Lease.** A released round carries the exact name (`released_name`). `_run_remote` leases that file for the
   run's whole life and adds it to the run's inputs by name. If the lease fails because the file is gone, the round
   is put back at the front of the queue uncounted, the name is forgotten, and the identity is warmed again: it never
   runs cold.
7. **Miss.** A round that reports `"built"` while `warm_names[id]` is still the name it was released under forgets
   that name and its confirmation, and counts one miss. A late round from an older name changes nothing.
8. **Machine failures don't cool anything.** A round's machine failure or timeout leaves `warm_names` and
   `confirmed` alone.

- [ ] **Step 1: Write the failing tests**

`webapp/tests/replays/test_replay_worker_control_cache.py`:

```python
def test_a_leased_file_survives_both_kept_pruning_and_eviction(tmp_path):
    import os
    import time

    leased = f"Ascent.{'a' * 16}.npz"
    for i, key in enumerate("abc"):
        path = tmp_path / f"Ascent.{key * 16}.npz"
        path.write_bytes(b"x" * 10)
        os.utime(path, (time.time() - 100 + i, time.time() - 100 + i))
    assert control_cache.lease(tmp_path, leased)
    try:
        (tmp_path / ".incoming").mkdir()
        src = tmp_path / ".incoming" / "n.npz"
        src.write_bytes(b"y" * 10)
        assert control_cache.store(tmp_path, f"Ascent.{'d' * 16}.npz", src, reserve=35)
        assert (tmp_path / leased).exists()            # the oldest, yet neither pruned nor evicted
    finally:
        control_cache.unlease(leased)


def test_lease_refuses_a_file_that_is_gone_and_unlease_balances(tmp_path):
    assert not control_cache.lease(tmp_path, f"Ascent.{'a' * 16}.npz")
    (tmp_path / f"Ascent.{'b' * 16}.npz").write_bytes(b"x")
    assert control_cache.lease(tmp_path, f"Ascent.{'b' * 16}.npz")
    assert control_cache.lease(tmp_path, f"Ascent.{'b' * 16}.npz")
    control_cache.unlease(f"Ascent.{'b' * 16}.npz")
    assert f"Ascent.{'b' * 16}.npz" in control_cache._LEASES
    control_cache.unlease(f"Ascent.{'b' * 16}.npz")
    assert f"Ascent.{'b' * 16}.npz" not in control_cache._LEASES
```

`webapp/tests/replays/test_replay_worker_control_workflow.py`:

1. In `STUB`, report where the cache came from and add an `infra` mode. Replace the final `if mode == "engine":` …
   `else:` block with:

```python
source = task["link"].get("source") or ("cache" if any(n.count(".") == 2 and "/" not in n for n in had) else "built")
if mode == "engine":
    print(json.dumps({"status": "failed", "error_kind": "engine", "error": "no side", "visibility_source": source}))
elif mode == "infra":
    print(json.dumps({"status": "failed", "error_kind": "infra", "error": "machine went away"}))
else:
    print(json.dumps({"status": "ok", "data": "ZA==", "summary": "cw==", "key": task["key"], "had": had,
                      "visibility_source": source}))
```

2. A fixture and helpers:

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


def warm_and_confirm(control):
    """One warm build, then one probe round that reads its cache: the identity is confirmed."""
    wait_done(control, [control.submit(round_task("probe"))])
```

3. The tests:

```python
def test_a_cold_map_gets_one_warm_build_one_probe_then_every_round_at_once(warm_remote):
    control, render, _, _ = warm_remote
    jobs = [control.submit(round_task(f"r{i}", mode="sleep:1.5")) for i in range(6)]
    done = wait_done(control, jobs)
    assert all(j.status == "done" for j in done)
    assert warm_starts(render) == 1 and render.started[0]["task"] == "replay-tasks/warm"
    assert all(f"Ascent.{'e' * 16}.npz" in j.result["had"] for j in done)
    state = control.counts()["warm_builds"]
    assert state["misses"] == 0 and state["confirmed"] == [jobs[0].warm_id]


def test_after_confirmation_rounds_run_side_by_side(warm_remote):
    control, _, _, _ = warm_remote
    warm_and_confirm(control)
    jobs = [control.submit(round_task(f"s{i}", mode="sleep:1.5")) for i in range(6)]
    wait_running(control, 6)
    assert all(j.status == "done" for j in wait_done(control, jobs))


def test_a_probe_that_built_the_cache_holds_the_rest_and_warms_again(warm_remote):
    control, render, _, _ = warm_remote
    probe = control.submit(round_task("p", source="built", mode="sleep:0.5"))
    rest = [control.submit(round_task(f"q{i}")) for i in range(3)]
    wait_done(control, [probe])
    assert control.counts()["warm_builds"]["misses"] == 1
    assert all(j.status == "done" for j in wait_done(control, rest))
    assert warm_starts(render) == 2


def test_two_height_modes_are_two_identities_with_two_files(warm_remote):
    control, render, _, _ = warm_remote
    a = control.submit(round_task("a"))
    b = control.submit({**round_task("b"), "height_mode": "flat"})
    wait_done(control, [a, b])
    assert a.warm_id != b.warm_id and warm_starts(render) == 2
    assert f"Ascent.{'f' * 16}.npz" in b.result["had"]


def test_a_new_height_digest_gets_its_own_warm_build(warm_remote):
    control, render, _, _ = warm_remote
    warm_and_confirm(control)
    wait_done(control, [control.submit({**round_task("b"), "height": "d2"})])
    assert warm_starts(render) == 2


def test_a_warm_build_that_keeps_failing_falls_back_to_one_warming_round(warm_remote):
    control, render, _, _ = warm_remote
    job = control.submit(round_task("x", map_name="Broken", build=True))
    assert wait_done(control, [job])[0].status == "done"
    assert warm_starts(render) == server.WARM_BUILD_TRIES
    assert control.counts()["warm_builds"]["failures"] == {job.warm_id: server.WARM_BUILD_TRIES}


def test_a_round_whose_file_left_the_disk_waits_for_a_new_warm_build_never_runs_cold(warm_remote):
    control, render, settings, _ = warm_remote
    warm_and_confirm(control)
    (settings.control_cache_dir / f"Ascent.{'e' * 16}.npz").unlink()
    job = control.submit(round_task("b"))
    assert wait_done(control, [job])[0].status == "done"
    assert warm_starts(render) == 2
    assert f"Ascent.{'e' * 16}.npz" in job.result["had"]      # it ran with the cache, not cold


def test_a_round_whose_lease_fails_is_requeued_uncounted_and_runs_after_a_new_warm_build(warm_remote, monkeypatch):
    from replay_worker import control_cache

    control, render, _, _ = warm_remote
    warm_and_confirm(control)
    real, calls = control_cache.lease, []

    def flaky(cache_dir, name):                    # the file vanishes between scheduling and the run's lease
        calls.append(name)
        return False if len(calls) == 1 else real(cache_dir, name)

    monkeypatch.setattr(control_cache, "lease", flaky)
    job = control.submit(round_task("b"))
    assert wait_done(control, [job])[0].status == "done"
    assert len(calls) == 2 and warm_starts(render) == 2
    assert f"Ascent.{'e' * 16}.npz" in job.result["had"]


def test_a_round_that_fails_for_the_machine_leaves_its_identity_confirmed(warm_remote):
    control, render, _, _ = warm_remote
    warm_and_confirm(control)
    assert wait_done(control, [control.submit(round_task("b", mode="infra"))])[0].error_kind == "infra"
    wait_done(control, [control.submit(round_task("c"))])
    assert warm_starts(render) == 1


def test_health_lists_only_identities_whose_file_is_on_disk(warm_remote):
    control, _, settings, _ = warm_remote
    warm_and_confirm(control)
    assert control.counts()["warm_builds"]["ready"]
    (settings.control_cache_dir / f"Ascent.{'e' * 16}.npz").unlink()
    assert control.counts()["warm_builds"]["ready"] == []
    assert control.counts()["warm_builds"]["recent_seconds"] == [0.2]


def test_warm_builds_off_keeps_todays_rule_and_reports_nothing(remote):
    control, render, _, _ = remote
    wait_done(control, [control.submit(round_task("a", build=True))])
    assert warm_starts(render) == 0 and "warm_builds" not in control.counts()


def test_settings_read_the_warm_build_switch_and_limit():
    on = server.Settings.from_env({"REPLAY_WARM_BUILDS": "true", "REPLAY_WARM_BUILD_TIMEOUT_S": "600"})
    assert on.warm_builds and on.warm_build_timeout_s == 600.0
    assert not server.Settings.from_env({}).warm_builds
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q`
Expected: the two lease tests and the settings test FAIL; the ten `warm_remote` tests ERROR in fixture setup
(`unexpected keyword argument 'warm_builds'`). `test_warm_builds_off_keeps_todays_rule_and_reports_nothing` already
passes: it pins today's behaviour. Existing tests still pass with the changed `STUB`.

- [ ] **Step 3: Implement**

**a. `replay_worker/control_cache.py`.** Under the counters:

```python
_LEASES: dict[str, int] = {}      # relative name -> open runs that must read it: never pruned or evicted meanwhile


def lease(cache_dir: Path, name: str) -> bool:
    """Holds `name` (relative to the cache) on disk for a run that must read it. False when it is already gone."""
    with _LOCK:
        if not (Path(cache_dir) / name).is_file():
            return False
        _LEASES[name] = _LEASES.get(name, 0) + 1
        return True


def unlease(name: str) -> None:
    with _LOCK:
        left = _LEASES.get(name, 0) - 1
        if left > 0:
            _LEASES[name] = left
        else:
            _LEASES.pop(name, None)
```

In `_evictable`, skip leased files: change `if path != keep:` to
`if path != keep and path.relative_to(cache_dir).as_posix() not in _LEASES:`. In `store`'s KEPT prune loop, skip
them too:

```python
            for old in _newest(target.parent, map_name)[KEPT:]:
                if old.relative_to(cache_dir).as_posix() in _LEASES:
                    continue                  # a running round reads it: it goes on a later store
                old.unlink(missing_ok=True)
                KEPT_PRUNED += 1
```

(`store` already holds `_LOCK` around both, so a lease and a prune can't interleave.)

**b. `replay_worker/server.py`.**

`Settings`, after `control_warm_timeout_s`:

```python
    warm_builds: bool = False               # REPLAY_WARM_BUILDS: a cold identity's visibility cache as its own run
    warm_build_timeout_s: float = 21600.0   # REPLAY_WARM_BUILD_TIMEOUT_S
```

`from_env`, after the `control_warm_timeout_s` lines:

```python
        settings.warm_builds = env.get("REPLAY_WARM_BUILDS", "").strip().lower() in ("1", "true")
        settings.warm_build_timeout_s = float(env.get("REPLAY_WARM_BUILD_TIMEOUT_S", settings.warm_build_timeout_s))
```

Docstring env list, after `REPLAY_CONTROL_RUNS`:

```
    REPLAY_WARM_BUILDS       "true": with REPLAY_CONTROL_RUNNER=workflow, a cold identity's visibility cache is one
                             warm build (replay_tasks warm) before its rounds, then one probe round, then the rest at
                             once (default off; a restart clears failed-build counts);
    REPLAY_WARM_BUILD_TIMEOUT_S  a warm build's limit (default 21600; Render's task limit is 300 s above it);
```

Beside `CONTROL_FINISHED_KEPT = 200`:

```python
WARM_BUILD_TRIES = 2     # failed warm builds of one identity before its rounds fall back to one warming round
WARM_SECONDS_KEPT = 20   # recent warm-build durations /health shows
```

`ControlJob`: after `kind`, add

```python
    warm_id: str = ""            # warm_key plus height mode: what warm builds track (REPLAY_WARM_BUILDS only)
    released_name: str | None = None   # the exact cache file this round was released to read (warm builds)
```

and change `kind`'s comment to `# or "heights" (a height build) or "warm" (a warm build)`.

`ControlRunner.submit`: after the `ControlJob(...)` construction, add
`job.warm_id = f"{job.warm_key}|{task.get('height_mode') or ''}"`.

`ControlRunner.__init__`, after `self.warm: set[str] = set()`:

```python
        # Warm builds (REPLAY_WARM_BUILDS), by warm_id.
        self.warm_names: dict[str, str] = {}     # the cache file its warm build named
        self.warm_confirmed: set[str] = set()    # a probe round read that file
        self.warm_probe: dict[str, str] = {}     # the probe round running now, by job id
        self.warm_jobs: dict[str, str] = {}      # its warm build, queued or running, by job id
        self.warm_failures: dict[str, int] = {}  # failed warm builds (WARM_BUILD_TRIES: fall back)
        self.warm_misses = 0                     # rounds released to a name that still had to build the cache
        self.warm_seconds: list[float] = []      # the last WARM_SECONDS_KEPT warm builds' durations
```

`ControlRunner.remote`, first line after `if self.runs is None: return False`:
`if job.kind == "warm": return True                  # a warm build only ever runs on Workflows`

New methods after `remote`:

```python
    def uses_warm_builds(self) -> bool:
        return self.settings.warm_builds and self.settings.control_runner == "workflow" and self.runs is not None

    def _ready(self, job: "ControlJob") -> bool:
        """The round's identity has a cache name from its warm build, and that file is on this disk (under the lock).
        The name is the exact geometry's, so a cache from other heights or another height mode never counts."""
        name = self.warm_names.get(job.warm_id)
        return name is not None and (self.settings.control_cache_dir / name).is_file()

    def _ask_warm(self, job: "ControlJob") -> None:
        """One warm build per identity, ahead of every round (under the lock). Its task is the round's own less what
        only a round needs, so it loads exactly the geometry the round will."""
        if job.warm_id in self.warm_jobs:
            return
        task = json.loads(job.task)
        for name in ("blob", "link", "gaps", "gaps_only"):
            task.pop(name, None)
        task["key"] = f"warm:{job.warm_id}"
        warm = ControlJob(uuid.uuid4().hex, task["key"], job.map, json.dumps(task).encode("utf-8"),
                          warm_key=job.warm_key, kind="warm", warm_id=job.warm_id)
        self.jobs[warm.id] = warm
        self.warm_jobs[job.warm_id] = warm.id
        self.pending.insert(0, warm.id)
        self.wake.set()
```

`counts` (replace):

```python
    def counts(self) -> dict:
        with self.lock:
            body = {"queued": len(self.pending), "running": len(self.running), "warm": sorted(self.warm),
                    "preempted": self.preempted_total}
            if self.uses_warm_builds():
                ready = [i for i, n in self.warm_names.items() if (self.settings.control_cache_dir / n).is_file()]
                body["warm_builds"] = {"ready": sorted(ready), "confirmed": sorted(self.warm_confirmed),
                                       "building": sorted(self.warm_jobs),
                                       "failures": dict(sorted(self.warm_failures.items())),
                                       "misses": self.warm_misses, "recent_seconds": list(self.warm_seconds)}
            return body
```

`_next`: iterate over a copy and add the warm branches before today's warm checks (the loop's tail becomes):

```python
        for job_id in list(self.pending):            # _ask_warm inserts into self.pending
            job = self.jobs[job_id]
            if job.kind == "heights":
                continue
            if local_build and not self.remote(job):
                continue                             # a local build runs alone among the local children
            if job.kind == "warm":
                return job, False                    # its own run: it neither waits for a map nor holds one
            if self.uses_warm_builds():
                if self._ready(job):
                    if job.warm_id not in self.warm_confirmed:
                        if job.warm_id in self.warm_probe:
                            continue                 # one probe at a time; the rest wait for its answer (risk 2)
                        self.warm_probe[job.warm_id] = job.id
                    job.released_name = self.warm_names[job.warm_id]
                    return job, False
                if self.warm_failures.get(job.warm_id, 0) < WARM_BUILD_TRIES:
                    self._ask_warm(job)              # the round waits for its identity's one warm build
                    continue
                # Warm builds keep failing for this identity: today's rule below, one round warms the map alone.
            if job.warm_key in self.warm:
                if job.map in warming_maps:
                    continue
                return job, False
            if job.map not in busy_maps:
                return job, True
        return None
```

`_run`:
- Add `answer: dict = {}` beside `status, error, kind, result = "failed", None, "infra", None`, so settlement can
  read a failed round's `visibility_source`. The two existing `answer = ...` assignments stay.
- The timeout and label lines become:

```python
        timeout = settings.height_timeout_s if build else settings.warm_build_timeout_s if job.kind == "warm" \
            else settings.control_warm_timeout_s if warming else settings.control_timeout_s
        what = "height build" if build else "warm build" if job.kind == "warm" else "control"
```

  (Task 5 replaces this block again with the Workflow-only limits.)
- Settlement, under the lock. Today it reads `if job.id in self.preempted: … else: …`. Add a branch between them,
  for a round whose leased file was gone (rule 6):

```python
            elif kind == "rewarm":
                # Its warm cache left the disk before the run could lease it: back to the front, uncounted, and the
                # identity warmed again. It never runs cold (risk 2).
                if self.warm_names.get(job.warm_id) == job.released_name:
                    self.warm_names.pop(job.warm_id, None)
                    self.warm_confirmed.discard(job.warm_id)
                if self.warm_probe.get(job.warm_id) == job.id:
                    self.warm_probe.pop(job.warm_id, None)
                job.released_name, job.status = None, "queued"
                self.pending.insert(0, job.id)
```

  In the `else:` branch, after `job.task = b""`, insert:

```python
                if job.kind == "warm":
                    self.warm_jobs.pop(job.warm_id, None)
                    name = (result or {}).get("visibility")
                    if status == "done" and name and control_cache.valid(name, job.map) and name not in unsent \
                            and (self.settings.control_cache_dir / name).is_file():
                        if self.warm_names.get(job.warm_id) != name:
                            self.warm_confirmed.discard(job.warm_id)
                        self.warm_names[job.warm_id] = name
                        self.warm_failures.pop(job.warm_id, None)
                    else:
                        self.warm_failures[job.warm_id] = self.warm_failures.get(job.warm_id, 0) + 1
                        _log(f"warm build {job.warm_id} failed: {error or 'its cache is not on this disk'}")
                    if isinstance(answer.get("seconds"), (int, float)):
                        self.warm_seconds = (self.warm_seconds + [round(float(answer["seconds"]), 1)])[-WARM_SECONDS_KEPT:]
                    del self.jobs[job.id]            # nobody asks after a warm build by id
                elif job.kind == "control" and self.uses_warm_builds() and job.released_name:
                    source = answer.get("visibility_source")
                    current = self.warm_names.get(job.warm_id) == job.released_name
                    if self.warm_probe.get(job.warm_id) == job.id:
                        self.warm_probe.pop(job.warm_id, None)     # whatever happened, the probe slot is free
                    if source == "built" and current:
                        self.warm_misses += 1                      # rule 7
                        self.warm_names.pop(job.warm_id, None)
                        self.warm_confirmed.discard(job.warm_id)
                        _log(f"warm identity {job.warm_id} missed: a round had to build its cache")
                    elif source == "cache" and current:
                        self.warm_confirmed.add(job.warm_id)       # rule 5
```

  Leave today's `if job.kind == "control":` warm-set block after it unchanged (it serves the fallback path). Rule 8
  holds because a machine failure has no `visibility_source`, so nothing above touches the warm state. The
  finished-job pruning that follows filters on `j.kind == "control"`, so the deleted warm job never reaches it.
  (`_log` is the module's logger, defined above `ControlJob`.)

`_run_remote`: lease the exact file for a released round. Replace the `else:` branch that fills `files` and the
`run = self.runs.open(...)` / `try` block with:

```python
            else:
                files.update(control_cache.inputs(self.settings.control_cache_dir, task))
            leased = None
            if job.kind == "control" and job.released_name:
                if not control_cache.lease(self.settings.control_cache_dir, job.released_name):
                    return {"status": "failed", "error_kind": "rewarm", "key": task.get("key"),
                            "error": f"{job.released_name} left the disk before the run could read it"}
                leased = job.released_name
                files[leased] = self.settings.control_cache_dir / leased   # by name, whatever `inputs` chose
            try:
                run = self.runs.open(job.kind, job.id, files, timeout)
                try:
                    self.runs.start(run, {"map": job.map,
                                          "files": sorted(n for n in files if n not in ("task", "rounds"))})
                    answer = self.runs.wait(run)
                except BaseException:
                    self.runs.close(run)          # wait closes it itself; a failure before that must not leak it
                    raise
            finally:
                if leased:
                    control_cache.unlease(leased)
```

The existing time-out relabel at the end of `_run_remote` becomes:

```python
        if answer.get("error_kind") == "timeout":
            what = "height build" if job.kind == "heights" else "warm build" if job.kind == "warm" else "control"
            answer = {**answer, "error_kind": "infra", "error": f"{what} timed out after {timeout:g} s"}
```

`_run_cache` route: both `run.kind not in ("control", "heights")` checks become
`run.kind not in ("control", "heights", "warm")`.

**c. `render.yaml`**, worker `envVars`, after `REPLAY_HEIGHTS_RUNNER`:

```yaml
      # Warm builds (docs/superpowers/plans/2026-11-05-replay-throughput.md): "true" builds a cold map's visibility
      # cache as its own run, confirms it with one probe round, then runs the rest at once. The owner's switch, set in
      # the dashboard: `sync: false`, so a Blueprint sync never resets it. Unset, off (today's rule).
      - key: REPLAY_WARM_BUILDS
        sync: false
```

- [ ] **Step 4: Run them to see them pass**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker_runs.py -n 0 -q`
Expected: all PASS. `test_replay_worker_control.py` (local mode) passes unchanged.

- [ ] **Step 5: Commit** (message: "Warm builds: one per identity, a probe round, then every round at once on a leased file")

---

### Task 5: Workflow time limits, separate from local's, in a pinned order

**Files:**
- Modify: `replay_worker/server.py` (`Settings`, `from_env`, docstring env list, `_run`'s timeout block)
- Modify: `replay_tasks/main.py` (`control` 1800 -> 7500, `heights` 3600 -> 11100)
- Modify: `render.yaml` (worker env: three Workflow limits)
- Modify: `webapp/app/services/replay_control_remote.py` (new `STALE_RUN_S`; `_collect`)
- Modify: `webapp/app/services/replay_heights_remote.py` (new `STALE_BUILD_RUN_S`; `Build.stale_s`; `step`;
  `_advance`)
- Test: `webapp/tests/replays/test_control_remote.py`, `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `Runs.started` (Task 2).
- Produces: `Settings.control_run_timeout_s` (env `REPLAY_CONTROL_RUN_TIMEOUT_S`, default 1800.0),
  `Settings.height_run_timeout_s` (env `REPLAY_HEIGHT_RUN_TIMEOUT_S`, default 3600.0): used only for runs on
  Workflows. `replay_control_remote.STALE_RUN_S = 15000`; `replay_heights_remote.STALE_BUILD_RUN_S = 22200`;
  `Build.stale_s: float`.

- [ ] **Step 1: Write the failing tests**

`webapp/tests/replays/test_control_remote.py`, at the end:

```python
def test_every_workflow_limit_outlasts_the_one_inside_it_and_local_keeps_todays():
    """Render's task limit sits above the worker's Workflow limit, within Render's 24 h; the site waits out twice the
    worker's (a run's deadline never passes opening + 2 x its limit). Local mode keeps today's numbers."""
    import ast
    import re

    from app.services import replay_heights_remote as heights

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
    assert render["control"] >= env("REPLAY_CONTROL_RUN_TIMEOUT_S") + 300
    assert render["warm"] >= env("REPLAY_WARM_BUILD_TIMEOUT_S") + 300
    assert render["heights"] >= env("REPLAY_HEIGHT_RUN_TIMEOUT_S") + 300
    assert remote.STALE_RUN_S > 2 * env("REPLAY_CONTROL_RUN_TIMEOUT_S")
    assert heights.STALE_BUILD_RUN_S > 2 * env("REPLAY_HEIGHT_RUN_TIMEOUT_S")
    # Local mode: untouched.
    assert remote.STALE_JOB_S == 1800 and heights.STALE_BUILD_S == 7200
    assert "REPLAY_CONTROL_TIMEOUT_S" not in yaml_text and "REPLAY_HEIGHT_TIMEOUT_S" not in yaml_text
```

`webapp/tests/replays/test_replay_worker_control_workflow.py`:

```python
def test_workflow_limits_apply_to_runs_and_local_limits_to_children():
    s = server.Settings.from_env({"REPLAY_CONTROL_RUN_TIMEOUT_S": "7200", "REPLAY_HEIGHT_RUN_TIMEOUT_S": "18000"})
    assert (s.control_run_timeout_s, s.height_run_timeout_s) == (7200.0, 18000.0)
    assert (s.control_timeout_s, s.control_warm_timeout_s, s.height_timeout_s) == (1800.0, 1800.0, 3600.0)


def test_a_workflow_round_is_opened_with_the_workflow_limit(remote, monkeypatch):
    control, _, settings, _ = remote
    settings.control_run_timeout_s = 1234.0
    seen = []
    real = control.runs.open
    monkeypatch.setattr(control.runs, "open", lambda kind, job_id, files, timeout_s, **k:
                        seen.append((kind, timeout_s)) or real(kind, job_id, files, timeout_s, **k))
    wait_done(control, [control.submit(round_task("t", build=True))])
    assert seen == [("control", 1234.0)]
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_remote.py tests/replays/test_replay_worker_control_workflow.py -n 0 -q -k "limit"`
Expected: the three tests FAIL (missing env values in `render.yaml`, missing settings, a warming round opened with
`control_warm_timeout_s`).

- [ ] **Step 3: Implement**

`replay_worker/server.py`:
- `Settings`, after `warm_build_timeout_s`:

```python
    control_run_timeout_s: float = 1800.0   # a control round on Workflows (REPLAY_CONTROL_RUN_TIMEOUT_S); local
                                            # children keep control_timeout_s / control_warm_timeout_s
    height_run_timeout_s: float = 3600.0    # a height build on Workflows (REPLAY_HEIGHT_RUN_TIMEOUT_S); local keeps
                                            # height_timeout_s
```

- `from_env`:

```python
        settings.control_run_timeout_s = float(env.get("REPLAY_CONTROL_RUN_TIMEOUT_S", settings.control_run_timeout_s))
        settings.height_run_timeout_s = float(env.get("REPLAY_HEIGHT_RUN_TIMEOUT_S", settings.height_run_timeout_s))
```

- Docstring env list: `REPLAY_CONTROL_RUN_TIMEOUT_S / REPLAY_HEIGHT_RUN_TIMEOUT_S  a round's / height build's limit
  on Workflows (defaults 1800 / 3600); local children keep REPLAY_CONTROL_TIMEOUT_S, REPLAY_CONTROL_WARM_TIMEOUT_S,
  REPLAY_HEIGHT_TIMEOUT_S;`
- `_run`: replace Task 4's `timeout = ...` statement with:

```python
        remote = self.remote(job)
        if job.kind == "warm":
            timeout = settings.warm_build_timeout_s
        elif build:
            timeout = settings.height_run_timeout_s if remote else settings.height_timeout_s
        elif remote:
            timeout = settings.control_run_timeout_s       # warming or not: one Workflow limit
        else:
            timeout = settings.control_warm_timeout_s if warming else settings.control_timeout_s
```

`replay_tasks/main.py`: `name="control", timeout_seconds=7500`; `name="heights", timeout_seconds=11100`. Docstring: "Each limit sits 300 s above the worker's Workflow limit (`render.yaml`);
tests/replays/test_control_remote.py pins the order."

`render.yaml`, worker `envVars`, after `REPLAY_CONTROL_QUEUE`:

```yaml
      # Workflow time limits (docs/superpowers/plans/2026-11-05-replay-throughput.md). Only runs on Workflows use
      # them: local children keep REPLAY_CONTROL_TIMEOUT_S / REPLAY_HEIGHT_TIMEOUT_S (their image defaults, 1800 and
      # 3600). Render's own task limits (replay_tasks/main.py) are 300 s above; tests/replays/test_control_remote.py
      # pins the order. Height builds took at most ~2 min over ~200 runs (2026-11-05); 3 h is the owner's margin.
      - key: REPLAY_CONTROL_RUN_TIMEOUT_S
        value: "7200"
      - key: REPLAY_WARM_BUILD_TIMEOUT_S
        value: "21600"
      - key: REPLAY_HEIGHT_RUN_TIMEOUT_S
        value: "10800"
```

`webapp/app/services/replay_control_remote.py`: under `STALE_JOB_S = 1800`, add

```python
STALE_RUN_S = 15000     # the same backstop while control runs on Workflows: > 2 x REPLAY_CONTROL_RUN_TIMEOUT_S
```

and in `_collect` change `elif now - f.running_since > STALE_JOB_S:` to
`elif now - f.running_since > (STALE_RUN_S if state.remote_control else STALE_JOB_S):` and the warning's
`STALE_JOB_S` argument to the same expression.

`webapp/app/services/replay_heights_remote.py`: under `STALE_BUILD_S = 7200`, add

```python
STALE_BUILD_RUN_S = 22200   # the same backstop for a build on Workflows: > 2 x REPLAY_HEIGHT_RUN_TIMEOUT_S
```

Add `stale_s: float = STALE_BUILD_S` as the last field of `Build`. In `step`, where `Build(...)` is constructed (after
`runners` is read), pass
`stale_s=STALE_BUILD_RUN_S if runners.get("heights") == "workflow" else STALE_BUILD_S`. In `_advance`, change
`if now - build.running_since > STALE_BUILD_S:` to `if now - build.running_since > build.stale_s:`.

- [ ] **Step 4: Run the suites that read these constants**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_control_remote.py tests/replays/test_heights_remote.py tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_control.py -n 0 -q`
Expected: all PASS. Existing stale tests use the local constants by name, and their state is local
(`remote_control` False, builds made with the default `stale_s`).

- [ ] **Step 5: Commit** (message: "Workflow time limits: 2 h a round, 6 h a warm build, measured for heights; local keeps today's")

---

### Task 6: The cache budget is a setting

**Files:**
- Modify: `replay_worker/server.py` (`Settings.control_cache_reserve`, `from_env`, docstring env list, `_run_cache`,
  `main`'s `archive_extra_reserve`, `/health` `control.cache.reserve`)
- Modify: `render.yaml` (worker: `REPLAY_CONTROL_CACHE_RESERVE_BYTES`)
- Test: `webapp/tests/replays/test_replay_worker_control_workflow.py`

**Interfaces:**
- Consumes: `/health` `control.cache` (Task 1).
- Produces: `Settings.control_cache_reserve: int` (env `REPLAY_CONTROL_CACHE_RESERVE_BYTES`, default
  `control_cache.RESERVE`).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_cache_reserve_setting_reaches_the_store_and_health(tmp_path, stub):
    import urllib.request

    render = FakeRender()
    settings = server.Settings(temp_root=tmp_path / "tmp", control_cache_dir=tmp_path / "cache",
                               control_runner="workflow", control_runs=4, workflow_slug="replay-tasks",
                               render_api_key="k", render_api_base=render.url, control_cache_reserve=3)
    table = server.make_runs(settings)
    control = server.ControlRunner(settings, runs_table=table)
    httpd = server.make_server(server.Worker(settings, on_parse=None), control=control, runs_table=table)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: tasks.TASKS[kind](args, tasks.WorkerLink(base, args["job_id"], args["token"]))
    try:
        job = control.submit(round_task("big", build=True))     # writes b"built": 5 bytes, past a 3-byte reserve
        wait_done(control, [job])
        assert not (settings.control_cache_dir / f"Ascent.{'c' * 16}.npz").exists()
        with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
            assert json.loads(response.read())["control"]["cache"]["reserve"] == 3
    finally:
        httpd.shutdown()
        httpd.server_close()
        render.close()


def test_the_cache_reserve_is_read_from_the_environment():
    assert server.Settings.from_env({"REPLAY_CONTROL_CACHE_RESERVE_BYTES": "12345"}).control_cache_reserve == 12345
    assert server.Settings.from_env({}).control_cache_reserve == server.control_cache.RESERVE
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py -n 0 -q -k reserve`
Expected: both FAIL (`unexpected keyword argument 'control_cache_reserve'`, `AttributeError`).

- [ ] **Step 3: Implement**

`replay_worker/server.py`:
- `Settings`, after `control_cache_dir`:
  `control_cache_reserve: int = control_cache.RESERVE   # bytes the cache may hold (REPLAY_CONTROL_CACHE_RESERVE_BYTES)`
  (`control_cache` is imported near the top of the module, above `Settings`).
- `from_env`:
  `settings.control_cache_reserve = int(env.get("REPLAY_CONTROL_CACHE_RESERVE_BYTES", settings.control_cache_reserve))`
- Docstring env list: `REPLAY_CONTROL_CACHE_RESERVE_BYTES  bytes the Workflow control cache may hold (default 2 GiB);
  the archive leaves them free.`
- `_run_cache`: `control_cache.store(control.settings.control_cache_dir, name, incoming,
  reserve=control.settings.control_cache_reserve)`
- `main`: `settings.archive_extra_reserve += control_cache.RESERVE` becomes
  `settings.archive_extra_reserve += settings.control_cache_reserve`
- `/health` (Task 1's line): `"reserve": control.settings.control_cache_reserve`

`render.yaml`, worker `envVars`, after `CONTROL_CACHE_DIR`:

```yaml
      # The control cache's budget on the disk (bytes), taken out of the archive's. /health control.cache shows its
      # bytes per map, evictions and per-map prunes. Before raising it, check /health archive has the room
      # (docs/superpowers/plans/2026-11-05-replay-throughput.md).
      - key: REPLAY_CONTROL_CACHE_RESERVE_BYTES
        value: "2147483648"
```

- [ ] **Step 4: Run them to see them pass**

Run: `../../../../webapp/.venv313/Scripts/python.exe -m pytest tests/replays/test_replay_worker_control_workflow.py tests/replays/test_replay_worker_control_cache.py tests/replays/test_replay_worker_archive.py -n 0 -q`
Expected: all PASS.

- [ ] **Step 5: Commit** (message: "Control cache: the reserve is a setting")

---

### Task 7: Docs, the full test run, the PR, and the rollout runbook

**Files:**
- Modify: `webapp/RENDER_DEPLOY.md` (new section "Warm builds, time limits and the concurrency ramp")
- Modify: `CLAUDE.md` and `AGENTS.md` (they mirror each other: the same short paragraph in both)
- Modify: `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (decisions D6 and D8)

- [ ] **Step 1: Write the docs.**
  - `webapp/RENDER_DEPLOY.md`: what a warm build, a probe round and a lease are; the switch `REPLAY_WARM_BUILDS`
    (dashboard, `sync: false`, off by default); the Workflow limits table from Global Constraints, with local's
    unchanged; what `/health` shows (`control.cache`, `control.warm_builds`, `workflow.by_kind`); the phase lines in
    run logs (`control phase: …`); and the runbook below, verbatim.
  - `CLAUDE.md` and `AGENTS.md`, beside the replay notes: three sentences (warm builds exist and are behind
    `REPLAY_WARM_BUILDS`; Workflow limits are separate from local's; read `webapp/RENDER_DEPLOY.md`).
  - The spec: D6 gains "With REPLAY_WARM_BUILDS on, a cold identity is warmed by its own `warm` run, confirmed by one
    probe round, then every round runs at once (2026-11-05 throughput plan)". D8 gains "Workflow runs: control
    7200 s, warm 21600 s, heights 10800 s, each counted from its attempt's start; local keeps 1800 / 3600 (2026-11-05
    throughput plan)".

- [ ] **Step 2: Run every suite the branch touches.**
  Run `../../../../webapp/.venv313/Scripts/python.exe scripts/test_catalog.py plan --git` and the commands it
  prints: the offline lane, plus the slow lane (CLAUDE.md requires it for changes to control, and
  `app/control/task.py` and `geometry.py` changed). Compare failures against the known baseline in memory
  (`project_test_suite_stale_neon_env`): new failures only.

- [ ] **Step 3: Commit the docs, push the branch, open the PR.** Never merge. The PR body holds Task 0's findings, Task 0
  step 3's answer, the risk table with each mitigation's commit, and the runbook. Give the owner the link.

#### Rollout runbook (owner; each step moves on once its checks pass, no waiting days)

Read the worker's `/health` from its Render shell:
`python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/health').read().decode())"`

1. **Merge while control is quiet** (owner): no recompute running and `control.warm_builds` absent or its
   `building` empty. Every merge restarts the single-instance worker and drops its open control runs (risk 9), and
   the worker and the replay-tasks workflow come up separately (risk 10).
   - Check: the replay-tasks workflow lists a `warm` task and has deployed this commit; `/health` shows
     `control.cache` and `workflow.by_kind`. Control run logs show `control phase:` lines.
2. **Run a control recompute with warm builds still off.** Read three control run logs: their `visibility … in N s`
   line against the round's total time. Record it in the PR. If the visibility build is a small share and rounds
   still run long, stop here and bring the logs back before step 3: the bottleneck is the round, not the cache.
3. **Flip `REPLAY_WARM_BUILDS=true`** on replay-worker in the dashboard. The worker restarts.
   - Check within an hour: identities move through `building` → `ready` → `confirmed`; `misses` stays 0;
     `failures` is empty or under 2 per identity; `recent_seconds` shows warm-build times well under 21600;
     `workflow.by_kind.control.open` climbs past 2 once an identity is confirmed.
   - Stop and switch it off if `misses` grows, or an identity reaches 2 failures on a map that worked before. A
     worker restart clears the failure counts.
4. **Check the cache budget.** `control.cache.bytes` against `control.cache.reserve`, and `evictions` and
   `kept_pruned`, both counted since the worker last started.
   - If either counter grows during a recompute, or `bytes` passes 60 % of `reserve`: confirm in `/health`
     `archive` that the archive has that much free, then raise `REPLAY_CONTROL_CACHE_RESERVE_BYTES` by PR.
5. **Read the concurrency** during a recompute with a backlog (rounds still to send):
   - Render starts about 16 `flex` runs a minute on Hobby (32 CPU a minute on Pro), so `workflow.by_kind.control.waiting`
     above 0 for the first few minutes is normal. Persistent `waiting` means Render's compute limit is queueing runs.
   - Two matches allow about 40 rounds (`CONTROL_MAX_RUNS` 40, about 20 rounds a match), so 30+ is reachable with
     `CONTROL_MAX_MATCHES` at 2. Raise it to 3, by PR, only if `control.open` stays under 30 while a backlog waits
     and `waiting` is 0.
   - Also check: `workflow.by_kind.control.oldest_run_s` stays under 7200; the site's `map control dispatch:` log
     shows `busy` and `unreachable` at 0; `evictions` stays flat.
6. **Done** when a recompute holds 30+ open control runs from the end of warm-up to the start of its last match's
   tail, and the dashboard shows no runs cancelled by a limit.

## Decided: feature-row overwrites (risk 5)

Keep today's last-write-wins (owner, 2026-11-05). Rows are per map and shared by every later replay of it; a burst of
parallel rounds keeps only the last posted copy's new rows, but what is kept carries to every upcoming round, and
the answers are identical either way. Task 1's `feature rows N reused, M built` line shows the cost if it ever
matters.

## Out of scope (named so they aren't lost)

- Letting gaps-only work fill free slots while control work is still queued (`replay_control_remote._submit`).
- Saving open warm-build and round tokens across worker restarts (parse's D9 pattern), so a deploy doesn't lose them.
- Persisting warm identities across restarts: after a deploy, each identity's warm build finds its cache on disk and
  answers in seconds, then one probe confirms it.

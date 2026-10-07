# Timing gaps on the worker, and a parse kills one control child: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every round the replay worker computes map control for also gets its timing gaps, stored by the site with nobody running `compute_control.py`; and a parse arriving on the worker kills one control child, not both, so control keeps one child running beside a parse.

**Architecture:** Three changes to code that exists. (1) `ControlRunner` in `replay_worker/server.py` allows one child while the parse side is busy and two while it is idle; `preempt` kills every running child but the one started first. (2) The control child (`app/control/task.py`, `replay_worker/control_job.py`) computes gaps from keys the site sends, so it never imports the web app's SQLAlchemy code, and deletes its tick cache file when the task ends; the worker image ships `app/gaps`. (3) The site's dispatcher (`app/services/replay_control_remote.py`) adds a `gaps` block to every control task, stores the returned gap run after the control row, and, when no control round is waiting, sends `gaps_only` tasks for rounds whose control is current but whose gaps are not.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, stdlib `subprocess`/`threading` on the worker, pytest. No migration, no new dependency.

**Spec:** `docs/superpowers/plans/2026-10-07-auto-reparse-queue.md`, sections "Timing gaps on the worker" and decision 4. This is the first of two PRs; the second is `docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md` and does not need to exist for this one to ship.

Two places where this plan departs from the spec, both smaller than what it says:

- The spec moves four pure helpers out of `app/services/replay_gaps.py`. Fourteen existing tests monkeypatch those names on that module, so moving them breaks all of them. Here the site sends the gap fingerprint and the cache key in the task, and the child uses them. `replay_gaps.py` is not touched.
- The spec has `store._delete` delete gap rows explicitly for the SQLite tests. PostgreSQL already cascades them (`fk_replay_gaps_round`, `fk_replay_round_gap_runs_round`), and adding the deletes would break every test fixture that builds its tables from `test_replay_store.TABLES`, which has no gap tables. Left out.

## Global Constraints

- Branch from `origin/main` after `git fetch` (local `main` goes stale). Work in a worktree under `.claude/worktrees/`.
- Run tests from `webapp/` in the worktree with the main checkout's interpreter, `webapp\.venv313\Scripts\python.exe` of the main checkout, as `PY -m pytest ... -p no:cacheprovider` (written `PY -m pytest` below).
- Before Task 1, run `PY -m pytest tests/replays -k "not pg" -p no:cacheprovider -q` on the clean branch and write the pass and fail counts into the PR description. `test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched` is known to fail on Windows with `ConnectionResetError`; leave it alone.
- The worker server process never imports numpy, `app.control` or `app.gaps` (`tests/replays/test_control_isolation.py`). The control child never imports `app.services` or SQLAlchemy: the control venv has only numpy, scipy and Pillow.
- The web app never imports `app.control` or `app.gaps` (`test_control_isolation.py`). The dispatcher uses `app.services.replay_gaps` and `app.services.replay_gaps_store`, which are database-only.
- `app/services/replay_gaps.py` is not edited.
- The dispatcher never runs in demo mode (`enabled()` is False). No gap row is ever written to the demo database.
- `REPLAY_CONTROL_WORKERS` stays 2. Nothing on Render is changed by this branch; deploying is the owner's.
- The repo is public: no credentials, no real player names, no machine paths in code, tests or docs.
- Comments and docstrings match the surrounding code: plain sentences, reasons not narration, docs referenced by path.

## Review Focus

1. **A worker image from before this change gets a task with a `gaps` block.** Its child fails the gap run on an import and returns a `failed` run with an empty fingerprint. Expected: the control row is stored, the gap run is not stored as the round's failure, and the round is asked again as gaps only. Pinned in Task 3.
2. **A gap result that arrives after the round's control moved** (a re-ingest, a new link). Expected: nothing is stored. Pinned in Tasks 3 and 4.
3. **Gap rows after a JSON round trip.** The local command stores rows in-process; the worker's go through JSON, which turns integer dict keys into strings. Expected: they store. Pinned in Task 3.
4. **A parse arrives while exactly one child runs.** Expected: nothing is killed, and no second child starts until the parse side is idle. Pinned in Task 1.
5. **A child killed mid-round leaves a tick cache file.** Expected: the next child deletes files older than six hours, so the worker's disk does not fill. Pinned in Task 2.

---

## File structure

| File | Change |
|---|---|
| `replay_worker/server.py` | Task 1: `ControlRunner._next`, `ControlRunner.preempt`, docstrings |
| `webapp/tests/replays/test_replay_worker_control.py` | Tasks 1-2 |
| `webapp/app/control/task.py` | Task 2: `_cache_path`, `_gaps` |
| `replay_worker/control_job.py` | Task 2: `figures`, the tick cache cleanup |
| `replay_worker/Dockerfile` | Task 2: copy `app/gaps`, the smoke test |
| `webapp/tests/replays/test_gaps_task.py` | Task 2 |
| `webapp/app/services/replay_control_remote.py` | Tasks 3-4 |
| `webapp/tests/replays/test_control_remote.py` | Tasks 3-4 |
| `docs/superpowers/plans/2026-10-05-control-idle-queue.md`, `replay_worker/README.md`, `webapp/RENDER_DEPLOY.md` | Task 5 |

---

### Task 1: One child beside a parse; a parse kills the child started last

**Files:**
- Modify: `replay_worker/server.py` (`ControlRunner.preempt`, `ControlRunner._next`, the module docstring, `Worker.idle`'s docstring)
- Test: `webapp/tests/replays/test_replay_worker_control.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ControlRunner.preempt() -> int` now returns the number of children killed, which is `max(0, running - 1)`. `ControlRunner._next()` admits up to `control_workers` children while `self.idle()` is true and one while it is false.

- [ ] **Step 1: Replace the four tests the new rule changes**

In `webapp/tests/replays/test_replay_worker_control.py`, delete these four tests whole:

- `test_nothing_starts_while_the_worker_is_busy`
- `test_a_parse_kills_every_child_and_their_rounds_go_back_first_uncounted`
- `test_a_preempted_warming_round_stays_cold_and_warms_alone_again`
- `test_a_child_started_in_the_admission_gap_is_killed_by_the_parse_starting`

Change the comment above `wait_until` to:

```python
# ---------------------------------------------------------------- one child beside a parse; a parse kills one
# (docs/superpowers/plans/2026-10-05-control-idle-queue.md, D4 as amended 2026-10-07)
```

Put these in their place:

```python
def test_one_child_runs_while_the_worker_is_busy_and_two_once_it_is_idle(runner):
    busy = {"on": True}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # it runs though a parse is on: one is allowed
    jobs = [r.submit(task(f"a{i}:1:f", mode="sleep:0.6")) for i in range(3)]
    wait_all(r, jobs)
    assert overlaps(log)[0] == 1, "never two beside a parse"
    for p in log.iterdir():
        p.unlink()
    busy["on"] = False
    jobs = [r.submit(task(f"b{i}:1:f", mode="sleep:0.6")) for i in range(3)]
    wait_all(r, jobs)
    assert overlaps(log)[0] == 2


def test_a_parse_kills_the_child_started_last_and_its_round_goes_back_first_uncounted(runner):
    busy = {"on": False}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # the map is warm: two may run at once
    a = r.submit(task("a:1:f", mode="sleep:6"))
    wait_until(lambda: r.counts()["running"] == 1)
    b = r.submit(task("b:1:f", mode="sleep:30"))
    c = r.submit(task("c:1:f"))
    wait_until(lambda: r.counts()["running"] == 2)
    busy["on"] = True
    assert r.preempt() == 1
    wait_until(lambda: r.counts()["running"] == 1)
    assert r.get(a.id).status == "running", "the child started first is left alone"
    assert r.get(b.id).status == "queued" and r.pending[:2] == [b.id, c.id]
    assert r.get(b.id).error is None and r.get(b.id).task, "uncounted, task kept"
    assert r.counts()["preempted"] == 1 and "Ascent" in r.counts()["warm"]
    assert r.preempt() == 0, "a second parse finds one child: nothing to kill"
    r.jobs[b.id].task = json.dumps(task("b:1:f")).encode()      # let it finish quickly this time
    wait_all(r, [a, b, c])                                       # still busy: they finish one at a time
    assert r.counts()["preempted"] == 1


def test_a_preempted_warming_round_leaves_its_map_cold(runner):
    busy = {"on": False}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # Ascent is warm
    first = r.submit(task("a:1:f", mode="sleep:6"))
    wait_until(lambda: r.counts()["running"] == 1)
    warming = r.submit(task("s:1:f", "Split", mode="sleep:30"))  # another map warms beside a warm one
    wait_until(lambda: r.counts()["running"] == 2)
    busy["on"] = True
    assert r.preempt() == 1
    wait_until(lambda: r.counts()["running"] == 1)
    assert r.get(warming.id).status == "queued" and "Split" not in r.counts()["warm"]
    r.jobs[warming.id].task = json.dumps(task("s:1:f", "Split")).encode()
    busy["on"] = False
    wait_all(r, [first, warming])
    assert "Split" in r.counts()["warm"]
```

Replace `test_preemption_at_settlement_wins_over_every_other_ending` whole with this. The old one preempted a lone child, which is no longer killed; this one runs a second child on another map, whose first round has the long warm timeout and so outlives the one under test:

```python
@pytest.mark.parametrize("ending", ["done", "timeout", "garbage"])
def test_preemption_at_settlement_wins_over_every_other_ending(runner, ending):
    mode = {"done": "ok", "timeout": "hang", "garbage": "garbage"}[ending]
    overrides = {"control_timeout_s": 1.0} if ending == "timeout" else {}
    r, log = runner(**overrides)
    wait_all(r, [r.submit(task("w:1:f"))])                       # Ascent is warm
    gate = threading.Event()

    def at_settlement(job):
        # the preempt lands after the child has ended, before settlement; idle goes false first so it can't rerun
        if job.key == "p:1:f" and not gate.is_set():
            r.idle = lambda: False
            r.preempt()
            gate.set()

    r._before_settle = at_settlement
    keep = r.submit(task("k:1:f", "Split", mode="sleep:8"))      # started first, on the warm timeout: it stays
    wait_until(lambda: r.counts()["running"] == 1)
    job = r.submit(task("p:1:f", mode=mode))
    assert gate.wait(30)
    wait_until(lambda: r.get(job.id).status == "queued")
    assert r.get(job.id).error is None and r.get(job.id).task
    assert r.pending == [job.id] and r.counts()["preempted"] == 1
    assert r.get(keep.id).status == "running" and "Ascent" in r.counts()["warm"], "warmth unchanged"
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py -k "busy or kills or warming or settlement" -p no:cacheprovider -q`
Expected: FAIL. `test_one_child_runs_while_the_worker_is_busy...` times out in `wait_all` ("jobs didn't finish"), since nothing starts while busy today; the kill test fails on `assert r.preempt() == 1` (it returns 2).

- [ ] **Step 3: Change `preempt` and `_next`**

In `replay_worker/server.py`, replace `ControlRunner.preempt` with:

```python
    def preempt(self) -> int:
        """A parse is coming: kill every running child but the one started first, so the parse has a CPU and
        control keeps one (D4 as amended 2026-10-07; the child started last has done the least work).
        `self.running` keeps start order. How each run ends is decided at settlement, under the lock, where a
        preempted mark wins over everything else."""
        with self.lock:
            alive = [job_id for job_id in self.running if job_id not in self.preempted]
            victims = [(job_id, self.procs.get(job_id)) for job_id in alive[1:]]
            self.preempted.update(job_id for job_id, _ in victims)
        for _, process in victims:
            if process is not None:
                try:
                    kill_tree(process)
                except Exception:  # noqa: BLE001 - a parse must never fail because a kill did
                    traceback.print_exc()
        return len(victims)
```

In `ControlRunner._next`, replace its first two lines of code:

```python
        if len(self.running) >= self.settings.control_workers or not self.idle():
            return None
```

with:

```python
        # Beside a parse (running or waiting) control keeps one child; idle, the whole pool.
        limit = self.settings.control_workers if self.idle() else 1
        if len(self.running) >= limit:
            return None
```

- [ ] **Step 4: Bring the docstrings in line**

In the module docstring of `replay_worker/server.py`, replace the sentence that starts "It runs only while nothing is parsing or waiting to parse" through "(docs/superpowers/plans/2026-10-05-control-idle-queue.md, D4)." with:

```
It runs on up to `control_workers` children while nothing is parsing or waiting to parse (`Worker.idle`),
and on one beside a parse; a parse arriving kills every running child but the one started first, and their
rounds go back to the front of the queue uncounted (docs/superpowers/plans/2026-10-05-control-idle-queue.md,
D4 as amended 2026-10-07).
```

In the same docstring's configuration list, change the `REPLAY_CONTROL_WORKERS` entry to:

```
    REPLAY_CONTROL_WORKERS   children at once while no parse is on (default 2: the 2-CPU plan's two cores;
                             one beside a parse; never from the core count, which a container reports
                             for the host)
```

Replace `Worker.idle`'s docstring with:

```python
        """No parse running and none waiting: map control may use its whole pool; otherwise one child.
        Between a queue's `get` and `parsing = True` this reads True for a few bytecodes; a second child
        started in that gap is killed by the `on_parse()` call at the top of `_run`
        (docs/superpowers/plans/2026-10-05-control-idle-queue.md, Task 2)."""
```

Change the class docstring of `ControlRunner`: replace "At most `control_workers` children at once;" with "At most `control_workers` children at once, and one beside a parse;".

- [ ] **Step 5: Run the worker's control tests**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker.py -p no:cacheprovider -q`
Expected: every test passes except the known `test_control_off_answers_404_and_parsing_is_untouched` on Windows.

- [ ] **Step 6: Commit**

```bash
git add replay_worker/server.py webapp/tests/replays/test_replay_worker_control.py
git commit -m "worker: a parse kills one control child, and control keeps one beside it"
```

---

### Task 2: The child computes gaps without the web app's code, and keeps no tick cache

**Files:**
- Modify: `webapp/app/control/task.py` (`_cache_path`, `_gaps`)
- Modify: `replay_worker/control_job.py`
- Modify: `replay_worker/Dockerfile`
- Test: `webapp/tests/replays/test_gaps_task.py`, `webapp/tests/replays/test_replay_worker_control.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces, for Task 3:
  - A task's `gaps` block may carry `"gap_fingerprint": str` and `"engine_key": str`. With `gap_fingerprint` present, `compute_task` writes it into `result["gaps"]["run"]["fingerprint"]`, takes `gaps_revision` from `app.gaps.detect.GAPS_REVISION`, and never imports `app.services`. Without it, behaviour is unchanged (the local command).
  - `control_job.run(task)` adds `"figures": cf.figures_hash()` to every result.
  - `control_job.run(task)` deletes the round's tick cache file after computing.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_gaps_task.py`:

```python
# ---------------------------------------------------------------- the replay worker's child (no web app code)


def test_a_task_with_the_sites_gap_keys_never_imports_the_web_apps_services(tmp_path, monkeypatch):
    from app.gaps import detect

    task = _task(tmp_path, monkeypatch)
    task["gaps"].update(gap_fingerprint="9" * 16, engine_key="e" * 16)
    monkeypatch.setitem(sys.modules, "app.services.replay_gaps", None)    # importing it now raises ImportError
    result = compute_task(task)
    run = result["gaps"]["run"]
    assert result["status"] == "ok" and run["status"] == "ok", run.get("error")
    assert run["fingerprint"] == "9" * 16 and run["gaps_revision"] == detect.GAPS_REVISION
    assert [p.name for p in tmp_path.iterdir()] == [f"1-r1-{'e' * 16}.ticks.pkl.gz"]


def test_a_gaps_only_task_with_the_sites_keys_needs_no_services_either(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch, gaps_only=True)
    task["gaps"].update(gap_fingerprint="9" * 16, engine_key="e" * 16)
    monkeypatch.setitem(sys.modules, "app.services.replay_gaps", None)
    result = compute_task(task)
    assert result["status"] == "ok" and "data" not in result
    assert result["gaps"]["run"]["status"] == "ok" and result["gaps"]["run"]["fingerprint"] == "9" * 16
```

Append to `webapp/tests/replays/test_replay_worker_control.py`:

```python
def test_the_child_returns_its_figures_and_keeps_no_tick_cache(tmp_path, monkeypatch):
    import os

    from control_toys import blob, open_hall

    from app.control import geometry
    from app.control import task as control_task
    from app.replays import control_format as cf
    from app.replays import format as fmt

    monkeypatch.setattr(geometry, "cache_dir", lambda: tmp_path)
    geo = open_hall()
    monkeypatch.setitem(control_task._GEOMETRY, geo.name, geo)
    gaps_dir = tmp_path / "gaps"
    gaps_dir.mkdir()
    old = gaps_dir / "9-r9-dead.ticks.pkl.gz"                    # a killed child's leftover
    old.write_bytes(b"x")
    stale = time.time() - control_job.STALE_CACHE_S - 60
    os.utime(old, (stale, stale))
    recent = gaps_dir / "8-r8-live.ticks.pkl.gz"                 # another child's file, being written now
    recent.write_bytes(b"x")
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}, t_end=3.0)
    t = {"key": "k", "map": geo.name, "blob": base64.b64encode(fmt.encode_blob(data)).decode(),
         "link": {"sides": {"0": "attack", "5": "defense"}, "db_deaths": []},
         "gaps": {"replay_id": 1, "round": 1, "fingerprint": "c" * 16, "gap_fingerprint": "9" * 16,
                  "engine_key": "e" * 16}}
    result = control_job.run(t)
    assert result["status"] == "ok" and result["figures"] == cf.figures_hash()
    assert result["gaps"]["run"]["fingerprint"] == "9" * 16
    assert sorted(p.name for p in gaps_dir.iterdir()) == [recent.name], "its own file and the old one are gone"
    json.dumps(result)                                           # the whole answer is JSON
```

In the same file, in `test_the_image_builds_a_control_venv_with_the_web_apps_pins`, add after the line that asserts `"python3-venv" in dockerfile`:

```python
    assert "COPY webapp/app/gaps /srv/webapp/app/gaps" in dockerfile
    smoke = re.search(r'/opt/control-venv/bin/python -c "import ([^"]+)"', dockerfile).group(1)
    assert {"app.gaps.detect", "app.gaps.cache", "app.gaps.rows", "app.gaps.backshots"} <= set(smoke.split(", "))
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_gaps_task.py -k "sites" tests/replays/test_replay_worker_control.py -k "sites or figures or image" -p no:cacheprovider -q`
Expected: FAIL. The two `test_gaps_task.py` tests fail on `run["status"] == "ok"` with an `ImportError` in the run's error; the child test fails with `AttributeError: module 'replay_worker.control_job' has no attribute 'STALE_CACHE_S'`; the image test fails on the `COPY` assertion.

- [ ] **Step 3: Use the site's keys in `task.py`**

In `webapp/app/control/task.py`, replace `_cache_path` with:

```python
def _cache_path(job: dict, map_name: str) -> Path:
    from app.gaps import cache

    key = job.get("engine_key")
    if key is None:             # the local command; the replay worker's tasks carry the key (no SQLAlchemy there)
        from app.services.replay_gaps import engine_key

        key = engine_key(job["fingerprint"], map_name)
    return cache.cache_path(job["replay_id"], job["round"], key)
```

In `_gaps`, replace these lines:

```python
        from app.replays import choke_assets
        from app.services.replay_gaps import GAPS_REVISION, gap_fingerprint

        run.update({"fingerprint": gap_fingerprint(job["fingerprint"], geo.name), "gaps_revision": GAPS_REVISION,
                    "chokes_hash": choke_assets.asset_hash(geo.name)})
```

with:

```python
        from app.replays import choke_assets

        if job.get("gap_fingerprint"):
            # The replay worker: the web app sent the fingerprint it will check the result against, and the
            # control venv has no SQLAlchemy to import app.services with. The revision is the detector's own,
            # so a worker on other gap rules says so.
            from app.gaps.detect import GAPS_REVISION

            fingerprint = job["gap_fingerprint"]
        else:
            from app.services.replay_gaps import GAPS_REVISION, gap_fingerprint

            fingerprint = gap_fingerprint(job["fingerprint"], geo.name)
        run.update({"fingerprint": fingerprint, "gaps_revision": GAPS_REVISION,
                    "chokes_hash": choke_assets.asset_hash(geo.name)})
```

Add one sentence to the module docstring's "Timing gaps" paragraph, after "never changes control's result.":

```
The replay worker's tasks also carry `gap_fingerprint` and `engine_key` in that block (the web app computes
them; the worker's interpreter cannot import app.services).
```

- [ ] **Step 4: Return the figures and drop the tick cache in `control_job.py`**

Replace `replay_worker/control_job.py` from the imports down to the end of `run` with:

```python
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
STALE_CACHE_S = 6 * 3600        # a tick cache file older than this was left by a killed child


def _drop_tick_cache(task: dict) -> None:
    """The worker keeps no tick cache: a full run reads back the file it has just written and nothing reads
    it again, and the disk's space accounting (replay_worker/archive.py) doesn't know about it. This round's
    file goes, and any file old enough to be a killed child's."""
    job = task.get("gaps")
    if not job:
        return
    from app.control import geometry

    folder = Path(geometry.cache_dir()) / "gaps"
    cutoff = time.time() - STALE_CACHE_S
    try:
        files = list(folder.iterdir())
    except OSError:
        return
    mine = f"{job['replay_id']}-r{job['round']}-"
    for path in files:
        try:
            if path.name.startswith(mine) or path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def run(task: dict) -> dict:
    if str(WEBAPP) not in sys.path:
        sys.path.insert(0, str(WEBAPP))
    from app.control.task import compute_task
    from app.replays import control_format as cf

    try:
        result = compute_task({**task, "blob": base64.b64decode(task["blob"])})
    finally:
        _drop_tick_cache(task)
    for name in ("data", "summary"):
        if result.get(name) is not None:
            result[name] = base64.b64encode(result[name]).decode("ascii")
    # What the web app checks before it stores: the engine's revisions, and the game figures it read
    # (app/control/hearing.json and utility.json), which are in both fingerprints.
    result.update({"revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION, "figures": cf.figures_hash()})
    return result
```

Change the module docstring's result sentence to:

```
The result (stdout): app/control/task.py's result with the bytes in base64, plus the engine's
CONTROL_REVISION and DATA_VERSION and the hash of the game figures it read, so the web app can drop a result
from another deploy. A task with a `gaps` block also returns the round's timing gaps; its tick cache file is
deleted when the task ends.
```

- [ ] **Step 5: Ship `app/gaps` in the image**

In `replay_worker/Dockerfile`, after `COPY webapp/app/control /srv/webapp/app/control` add:

```dockerfile
COPY webapp/app/gaps /srv/webapp/app/gaps
```

Change the comment two lines above the `COPY` block's control line from "and the control engine, run only by the control venv's children." to "and the control engine and the gap detector, run only by the control venv's children."

Replace the smoke-test `RUN` line with:

```dockerfile
RUN PYTHONPATH=/srv/webapp:/srv /opt/control-venv/bin/python -c "import numpy, scipy, PIL, app.control.engine, app.control.task, app.gaps.detect, app.gaps.cache, app.gaps.rows, app.gaps.backshots, replay_worker.control_job"
```

- [ ] **Step 6: Run the tests**

Run: `PY -m pytest tests/replays/test_gaps_task.py tests/replays/test_replay_worker_control.py tests/replays/test_control_isolation.py tests/replays/test_control_task.py -p no:cacheprovider -q`
Expected: PASS, but for the known Windows failure.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/control/task.py replay_worker/control_job.py replay_worker/Dockerfile webapp/tests/replays/test_gaps_task.py webapp/tests/replays/test_replay_worker_control.py
git commit -m "worker: the control child computes timing gaps from the site's keys and keeps no tick cache"
```

---

### Task 3: The dispatcher asks for gaps with control and stores them

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py`
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Consumes from Task 2: the `gap_fingerprint` and `engine_key` keys of a task's `gaps` block; `result["figures"]`; `result["gaps"] = {"run": {...}, "rows": [...]}`.
- Produces, for Task 4 and for the second plan:
  - `gaps_job(replay_id: int, round_number: int, fingerprint: str, map_name: str) -> dict`
  - `_gaps_usable(result: dict, f: InFlight) -> bool`
  - `_store_gaps(session_factory, f: InFlight, result: dict, counts: dict) -> bool`
  - `cycle`'s counts gain the keys `gaps_stored`, `gaps_dropped`, `gaps_skipped`.

- [ ] **Step 1: Make the fake worker answer with gaps, and write the failing tests**

In `webapp/tests/replays/test_control_remote.py`, add to the imports:

```python
import json

from test_gaps_store import ROW  # noqa: E402

from app.models.replay import ReplayGap, ReplayRoundGapRun  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.services import replay_gaps  # noqa: E402
```

and change the existing `from app.models.replay import ReplayRoundControl` line so `ReplayRoundControl` is still imported (keep it; the new line adds the two gap models).

Add above `class FakeWorker`:

```python
def gap_answer(task, status="ok", rows=(), **change):
    """A task's `gaps` result, as replay_worker/control_job.py returns it for this deploy's rules."""
    run = {"status": status, "fingerprint": task["gaps"]["gap_fingerprint"], "gaps_revision": replay_gaps.GAPS_REVISION,
           "chokes_hash": choke_assets.asset_hash(task["map"]), "notes": {},
           "error": None if status == "ok" else "RuntimeError: boom"}
    run.update(change)
    return {"run": run, "rows": json.loads(json.dumps(list(rows)))}
```

Replace `FakeWorker.ok` with:

```python
    def ok(self, task):
        result = {"status": "ok", "revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION,
                  "geometry": rc.geometry_inputs(task["map"]), "figures": cf.figures_hash()}
        if not task.get("gaps_only"):
            result.update(data=base64.b64encode(gzip.compress(b"d")).decode(),
                          summary=base64.b64encode(cf.pack_summary({})).decode())
        if "gaps" in task:
            result["gaps"] = gap_answer(task)
        return {"status": "done", "result": result}
```

In `test_missing_rounds_are_sent_then_stored`, change the task-shape assertion to:

```python
    assert set(task) == {"key", "map", "blob", "link", "gaps"} and set(task["link"]) == {"sides", "db_deaths"}
```

Add after `def rows(db, replay)`:

```python
def gap_runs(db, replay):
    db.expire_all()
    return {r.round_number: r for r in db.query(ReplayRoundGapRun).filter(ReplayRoundGapRun.replay_id == replay.id)}


def test_a_control_task_asks_for_gaps_and_both_are_stored(factory, db, linked):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["gaps"] = gap_answer(task, rows=[ROW])     # through JSON, as the worker sends it
        return job

    worker, state = FakeWorker(answer), remote.State()
    remote.cycle(factory, worker, state, now=0)
    task = next(iter(worker.tasks.values()))
    [planned] = rc.plan(db, rounds={int(task["key"].split(":")[1])}, force=True)
    assert task["gaps"] == {"replay_id": linked.id, "round": planned.round_number, "fingerprint": planned.fingerprint,
                            "gap_fingerprint": replay_gaps.gap_fingerprint(planned.fingerprint, planned.map_name),
                            "engine_key": replay_gaps.engine_key(planned.fingerprint, planned.map_name)}
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["stored"] == counts["gaps_stored"] == remote.IN_FLIGHT and counts["gaps_dropped"] == 0
    runs = gap_runs(db, linked)
    assert sorted(runs) == sorted(rows(db, linked)) and runs[planned.round_number].status == "ok"
    assert runs[planned.round_number].fingerprint == task["gaps"]["gap_fingerprint"]
    assert db.query(ReplayGap).filter(ReplayGap.replay_id == linked.id).count() == remote.IN_FLIGHT
    assert replay_gaps.round_gaps(db, linked, planned.round_number)[0] == "ok"


@pytest.mark.parametrize("change", [
    lambda job, task: job["result"]["gaps"]["run"].update(gaps_revision=replay_gaps.GAPS_REVISION + 1),
    lambda job, task: job["result"]["gaps"]["run"].update(chokes_hash="0" * 16),
    lambda job, task: job["result"]["gaps"]["run"].update(fingerprint="0" * 16),
    lambda job, task: job["result"].update(figures="0" * 16),
    lambda job, task: job["result"].pop("figures"),
    lambda job, task: job["result"].pop("gaps"),
    # a worker image from before it could compute gaps: the run fails on an import, with nothing filled in
    lambda job, task: job["result"].update(gaps={"run": {"fingerprint": "", "gaps_revision": 0, "chokes_hash": None,
                                                         "notes": {}, "status": "failed",
                                                         "error": "ModuleNotFoundError: No module named 'sqlalchemy'"},
                                                 "rows": []}),
])
def test_gaps_this_deploy_cannot_trust_are_dropped_and_control_is_kept(factory, db, linked, change):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        change(job, task)
        return job

    worker, state = FakeWorker(answer), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["stored"] == counts["gaps_dropped"] == remote.IN_FLIGHT and counts["gaps_stored"] == 0
    assert len(rows(db, linked)) == remote.IN_FLIGHT and not gap_runs(db, linked)


def test_a_gap_failure_is_stored_as_the_gap_runs_and_control_stays_ok(factory, db, linked):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["gaps"] = gap_answer(task, status="failed")
        return job

    worker, state = FakeWorker(answer), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_stored"] == remote.IN_FLIGHT
    assert all(r.status == "ok" for r in rows(db, linked).values())
    assert all(r.status == "failed" and r.error.startswith("RuntimeError") for r in gap_runs(db, linked).values())


def test_gaps_are_not_stored_for_a_round_whose_control_was_skipped(factory, db, linked):
    worker, state = FakeWorker(), remote.State()
    remote.cycle(factory, worker, state, now=0)
    linked.recipe = linked.recipe + "x"                          # re-ingested meanwhile: every fingerprint moved
    db.commit()
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["skipped"] == remote.IN_FLIGHT and counts["gaps_stored"] == 0
    assert not rows(db, linked) and not gap_runs(db, linked)
```

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_control_remote.py -p no:cacheprovider -q`
Expected: FAIL. `test_missing_rounds_are_sent_then_stored` fails on the task-shape assertion (no `gaps` key), and the new tests fail with `KeyError: 'gaps'` in `gap_answer` or `KeyError: 'gaps_stored'`.

- [ ] **Step 3: Send the `gaps` block and store what comes back**

In `webapp/app/services/replay_control_remote.py`, add to the imports:

```python
from app.replays import choke_assets
from app.services import replay_gaps
from app.services.replay_gaps_store import STORED as GAPS_STORED
from app.services.replay_gaps_store import store_gaps
```

Add after `task_key`:

```python
def gaps_job(replay_id: int, round_number: int, fingerprint: str, map_name: str) -> dict:
    """A task's `gaps` block (app/control/task.py): the round, its control fingerprint, and the two keys the
    worker's child cannot compute for itself, since it has no SQLAlchemy to import app.services with."""
    return {"replay_id": replay_id, "round": round_number, "fingerprint": fingerprint,
            "gap_fingerprint": replay_gaps.gap_fingerprint(fingerprint, map_name),
            "engine_key": replay_gaps.engine_key(fingerprint, map_name)}


def _gaps_usable(result: dict, f: InFlight) -> bool:
    """A gap run this deploy may store: computed under its gap rules, its choke asset and its game figures,
    for the fingerprint the round was planned with. A worker on other code fails this, and so does one too old
    to compute gaps (its run comes back failed with nothing filled in); the round is asked again as gaps only."""
    run = (result.get("gaps") or {}).get("run") or {}
    return (run.get("status") in ("ok", "failed")
            and run.get("gaps_revision") == replay_gaps.GAPS_REVISION
            and run.get("chokes_hash") == choke_assets.asset_hash(f.map_name)
            and run.get("fingerprint") == replay_gaps.gap_fingerprint(f.fingerprint, f.map_name)
            and result.get("figures") == cf.figures_hash())


def _store_gaps(session_factory, f: InFlight, result: dict, counts: dict) -> bool:
    """Stores a result's gap run and rows (app/services/replay_gaps_store.py), or drops them."""
    if not _gaps_usable(result, f):
        counts["gaps_dropped"] += 1
        return False
    gaps = result["gaps"]
    outcome = store_gaps(session_factory, f.replay_id, f.round_number, gaps["run"], gaps.get("rows") or [])
    counts["gaps_stored" if outcome == GAPS_STORED else "gaps_skipped"] += 1
    return outcome == GAPS_STORED
```

`gaps_job` and `_gaps_usable` name `InFlight`, which is defined above `task_key`; keep that order.

In `_collect`, replace the block from `del state.in_flight[key]` (the one after the `running` branch, just before `if status == "done":`) to the end of the function with:

```python
        del state.in_flight[key]
        result = None
        if status == "done":
            result = job.get("result") or {}
            if result.get("revision") != cf.CONTROL_REVISION or result.get("data_version") != cf.DATA_VERSION:
                state.failed(key, now)
                counts["dropped_revision"] += 1
                continue
            if not _matches_geometry(result, f.map_name):
                state.failed(key, now)
                counts["dropped_geometry"] += 1
                continue
            row = {"status": "ok", "data": base64.b64decode(result["data"]),
                   "summary": base64.b64decode(result["summary"])}
        elif status == "failed" and job.get("error_kind") == "engine":
            row = {"status": "failed", "error": job.get("error") or "failed on the replay worker"}
        else:
            state.failed(key, now)
            counts["infra_failed"] += 1
            continue
        outcome = store_round(session_factory, f.replay_id, f.round_number, f.fingerprint, row, require_current=True)
        counts["stored" if outcome == STORED else "skipped"] += 1
        if row["status"] == "failed" and outcome == STORED:
            counts["stored_failed"] += 1
        # The round's timing gaps came with it; like the local command, never for a round whose control
        # wasn't stored. A gap failure is the gap run's and is stored as that.
        if result is not None and outcome == STORED:
            _store_gaps(session_factory, f, result, counts)
```

In `_submit`, replace the line that builds `task`:

```python
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]}}
```

with:

```python
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]},
                "gaps": gaps_job(p.replay_id, p.round_number, p.fingerprint, p.map_name)}
```

In `cycle`, add the three new keys to the tuple the counts are built from:

```python
    counts: dict[str, int] = {k: 0 for k in ("sent", "stored", "stored_failed", "skipped", "forgotten", "timed_out",
                                             "dropped_revision", "dropped_geometry", "infra_failed", "busy",
                                             "unreachable", "gaps_stored", "gaps_dropped", "gaps_skipped")}
```

In the module docstring, add to point 1 ("Collect"), after "with BACKOFF_S between.":

```
   A stored `ok` round's timing gaps come in the same result and are stored after it
   (app/services/replay_gaps_store.py), when they were computed under this deploy's gap rules, choke asset
   and game figures; otherwise they are dropped and the round is asked again as gaps only.
```

- [ ] **Step 4: Run the dispatcher's tests**

Run: `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_control_isolation.py -p no:cacheprovider -q`
Expected: PASS (the `pg` test is skipped without `VALO_TEST_DATABASE_URL`).

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/replay_control_remote.py webapp/tests/replays/test_control_remote.py
git commit -m "control dispatch: ask the worker for each round's timing gaps and store them"
```

---

### Task 4: Gaps-only tasks for rounds whose control is current

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py`
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Consumes from Task 3: `gaps_job`, `_gaps_usable`, `_store_gaps`, `FakeWorker.ok` (which already answers a `gaps_only` task without `data`), `gap_answer`, `gap_runs`.
- Produces, for the second plan:
  - `InFlight.gaps_only: bool` (default False).
  - `_submit(session, client, state, now, counts)` sends control rounds first and gaps-only rounds only when every sendable control round has been sent.
  - `cycle`'s counts gain `gaps_sent`.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_control_remote.py`:

```python
# ---------------------------------------------------------------- gaps only: control is current, gaps are not


def all_control(db, replay):
    for n in range(1, replay.round_count + 1):
        put_row(db, replay, n)


def put_gap_run(factory, db, replay, n, status="ok", fingerprint=None):
    [planned] = rc.plan(db, rounds={n}, force=True)
    run = {"status": status, "gaps_revision": replay_gaps.GAPS_REVISION, "chokes_hash": None, "notes": {},
           "fingerprint": fingerprint or replay_gaps.gap_fingerprint(planned.fingerprint, planned.map_name),
           "error": None if status == "ok" else "RuntimeError: boom"}
    assert store_gaps(factory, replay.id, n, run, []) == "stored"


def test_rounds_with_current_control_and_no_gaps_are_sent_gaps_only(factory, db, linked):
    all_control(db, linked)
    worker, state = FakeWorker(), remote.State()
    counts = remote.cycle(factory, worker, state, now=0)
    assert counts["gaps_sent"] == remote.IN_FLIGHT and counts["sent"] == 0
    assert all(t["gaps_only"] is True and t["key"].split(":")[3].startswith("g") for t in worker.tasks.values())
    before = {n: r.computed_at for n, r in rows(db, linked).items()}
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_stored"] == remote.IN_FLIGHT and counts["stored"] == 0
    assert sorted(gap_runs(db, linked)) == list(range(1, remote.IN_FLIGHT + 1))
    assert {n: r.computed_at for n, r in rows(db, linked).items()} == before, "control is not rewritten"


def test_control_rounds_go_before_gaps_only_ones(factory, db, linked):
    all_control(db, linked)
    db.query(ReplayRoundControl).filter(ReplayRoundControl.round_number == 5).delete()
    db.commit()
    worker = FakeWorker()
    counts = remote.cycle(factory, worker, remote.State(), now=0)
    assert counts["sent"] == 1 and counts["gaps_sent"] == remote.IN_FLIGHT - 1
    first = worker.tasks["j0"]
    assert first["key"].split(":")[1] == "5" and "gaps_only" not in first


def test_no_gaps_only_task_is_sent_while_control_rounds_are_left_over(factory, db, linked):
    worker = FakeWorker()                                        # nothing has control: more than fits in flight
    counts = remote.cycle(factory, worker, remote.State(), now=0)
    assert counts["sent"] == remote.IN_FLIGHT and counts["gaps_sent"] == 0


def test_current_gaps_and_a_current_gap_failure_are_left_alone_and_stale_gaps_are_sent(factory, db, linked):
    all_control(db, linked)
    for n in range(1, linked.round_count + 1):
        put_gap_run(factory, db, linked, n)
    put_gap_run(factory, db, linked, 2, status="failed")         # failed under the current rules: it would repeat
    put_gap_run(factory, db, linked, 3, fingerprint="0" * 16)    # computed under other rules: stale
    worker = FakeWorker()
    remote.cycle(factory, worker, remote.State(), now=0)
    assert sent_rounds(worker) == [3]


def test_a_gaps_only_result_this_deploy_cannot_trust_counts_a_try_and_is_asked_again(factory, db, linked):
    all_control(db, linked)

    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["gaps"]["run"]["gaps_revision"] = replay_gaps.GAPS_REVISION + 1
        return job

    worker, state = FakeWorker(answer), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_dropped"] == remote.IN_FLIGHT and not gap_runs(db, linked)
    assert all(count == 1 for count, _ in state.tries.values())
    before = len(worker.tasks)
    remote.cycle(factory, worker, state, now=2)
    assert len(worker.tasks) == before, "backoff"
    remote.cycle(factory, worker, state, now=2 + remote.BACKOFF_S)
    assert len(worker.tasks) > before


def test_a_gaps_only_result_is_not_stored_once_the_rounds_control_moved(factory, db, linked):
    all_control(db, linked)
    worker, state = FakeWorker(), remote.State()
    remote.cycle(factory, worker, state, now=0)
    linked.recipe = linked.recipe + "x"                          # re-ingested meanwhile
    db.commit()
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_skipped"] == remote.IN_FLIGHT and counts["gaps_stored"] == 0 and not gap_runs(db, linked)


def test_a_failed_gaps_only_job_counts_a_try(factory, db, linked):
    all_control(db, linked)
    worker = FakeWorker(lambda task: {"status": "failed", "error_kind": "infra", "error": "MemoryError"})
    state = remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["infra_failed"] == remote.IN_FLIGHT and not gap_runs(db, linked)
    assert len(rows(db, linked)) == linked.round_count, "control is untouched"
```

Add `from app.services.replay_gaps_store import store_gaps  # noqa: E402` to the file's imports.

Three existing tests in this file give rounds control rows but no gap runs, so after this task they would send gaps-only tasks and plan twice. Change them:

Replace `sent_rounds` with a version that tells the two kinds of task apart:

```python
def sent_rounds(worker, gaps_only=False):
    return sorted(int(t["key"].split(":")[1]) for t in worker.tasks.values()
                  if bool(t.get("gaps_only")) == gaps_only)
```

and in `test_current_gaps_and_a_current_gap_failure_are_left_alone_and_stale_gaps_are_sent` above, the last line is then `assert sent_rounds(worker, gaps_only=True) == [3]`. `test_stale_rounds_are_sent_and_a_current_failure_is_not` keeps `sent_rounds(worker) == [1, 3]`, which now counts control tasks only.

In `test_planning_is_throttled_while_nothing_can_be_sent`, give every round current gaps as well and count two plans per planning pass (the control plan, then the forced one the gap plan reads). Its body becomes:

```python
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
        put_gap_run(factory, db, linked, n)
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(1) or real(session, **kw))
    state = remote.State()
    remote.cycle(factory, FakeWorker(), state, now=0)
    remote.cycle(factory, FakeWorker(), state, now=remote.CYCLE_S)
    assert len(calls) == 2, "one planning pass: the control plan and the forced plan for gaps"
    remote.cycle(factory, FakeWorker(), state, now=remote.PLAN_IDLE_S + 1)
    assert len(calls) == 4
```

`put_gap_run` calls `rc.plan` itself, which is why it runs before the `monkeypatch.setattr`.

In `test_rounds_that_can_never_be_sent_dont_defeat_the_throttle`, change the last line to `assert len(calls) == 2`.

- [ ] **Step 2: Run them and see them fail**

Run: `PY -m pytest tests/replays/test_control_remote.py -p no:cacheprovider -q`
Expected: FAIL. The new tests fail with `KeyError: 'gaps_sent'`; the two throttle tests fail on their new counts (1, not 2).

- [ ] **Step 3: Add `gaps_only` to an in-flight job**

In `webapp/app/services/replay_control_remote.py`, add a field at the end of `InFlight`:

```python
    gaps_only: bool = False              # the round's control is current; only its timing gaps were asked for
```

Add to the model imports: `ReplayRoundControl` (the line becomes `from app.models.replay import Replay, ReplayRound, ReplayRoundControl`).

- [ ] **Step 4: Collect a gaps-only job**

Add above `_collect`:

```python
def _control_is_current(session_factory, f: InFlight) -> bool:
    """The round still has the `ok` control row its gaps were computed for, under its current inputs."""
    session = session_factory()
    try:
        replay = session.get(Replay, f.replay_id)
        row = session.get(ReplayRoundControl, (f.replay_id, f.round_number))
        if replay is None or row is None or row.status != "ok":
            return False
        current = replay_control.round_fingerprint(replay, replay_control.side_groups(session, replay), f.round_number)
        return row.fingerprint == f.fingerprint == current
    finally:
        session.rollback()
        session.close()


def _collect_gaps_only(session_factory, state: State, key: str, f: InFlight, job: dict, now: float,
                       counts: dict) -> None:
    """A finished gaps-only job: its gaps are stored when the result is this deploy's and the round's control
    hasn't moved. Any failure of the job is retried like a control job's; a gap run's own failure comes back
    in a done job and is stored as the round's."""
    if job.get("status") != "done":
        state.failed(key, now)
        counts["infra_failed"] += 1
        return
    result = job.get("result") or {}
    if (result.get("revision") != cf.CONTROL_REVISION or not _matches_geometry(result, f.map_name)
            or not _gaps_usable(result, f)):
        state.failed(key, now)
        counts["gaps_dropped"] += 1
        return
    if not _control_is_current(session_factory, f):
        counts["gaps_skipped"] += 1
        return
    _store_gaps(session_factory, f, result, counts)
```

In `_collect`, right after the `del state.in_flight[key]` that precedes `result = None`, add:

```python
        if f.gaps_only:
            _collect_gaps_only(session_factory, state, key, f, job, now, counts)
            continue
```

- [ ] **Step 5: Send gaps-only tasks when no control round is waiting**

Replace `_submit` whole with:

```python
def _key(p, gaps_only: bool, gap_prints: dict) -> str:
    """A task's key on the worker, which dedupes by it. A gaps-only task's ends in the gap fingerprint, so
    new gap rules are a new job there and not an old result handed back."""
    key = task_key(p.replay_id, p.round_number, p.fingerprint)
    if not gaps_only:
        return key
    known = (p.fingerprint, p.map_name)
    if known not in gap_prints:
        gap_prints[known] = replay_gaps.gap_fingerprint(p.fingerprint, p.map_name)
    return f"{key}:g{gap_prints[known]}"


def _send(session, client, state: State, now: float, counts: dict, todo: list, keys: dict, gaps_only: bool) -> bool:
    """Submits `todo` in order while there is room in flight. False when it stopped early (no room, a busy or
    unreachable worker), having marked that sendable work is left."""
    for p in todo:
        if len(state.in_flight) >= IN_FLIGHT:
            state.last_found = True          # sendable work is left: plan again next cycle
            return False
        key = keys[id(p)]
        row = session.get(ReplayRound, (p.replay_id, p.round_number))
        if row is None:
            continue
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]},
                "gaps": gaps_job(p.replay_id, p.round_number, p.fingerprint, p.map_name)}
        if gaps_only:
            task["gaps_only"] = True
        features = (replay_control.geometry_inputs(p.map_name) or {}).get("features")
        if features:       # only a map with enabled features: an absent key means no verification
            task["features"] = features
        try:
            answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            state.last_found = True
            return False
        except (WorkerGone, Unreachable):
            counts["unreachable"] += 1
            state.last_found = True
            return False
        state.in_flight[key] = InFlight(answer["id"], p.replay_id, p.round_number, p.fingerprint, p.map_name, now,
                                        gaps_only=gaps_only)
        counts["gaps_sent" if gaps_only else "sent"] += 1
    return True


def _sendable(state: State, now: float, planned: list, gaps_only: bool, gap_prints: dict) -> tuple[list, dict]:
    """The planned rounds that aren't in flight and haven't used up their tries, and each one's key."""
    todo, keys = [], {}
    for p in planned:
        key = _key(p, gaps_only, gap_prints)
        if key not in state.in_flight and state.may_try(key, now):
            todo.append(p)
            keys[id(p)] = key
    return todo, keys


def _submit(session, client, state: State, now: float, counts: dict) -> None:
    if len(state.in_flight) >= IN_FLIGHT:
        return
    if state.last_planned is not None and not state.last_found and now - state.last_planned < PLAN_IDLE_S:
        return
    # D1/D3: never computed, or out of date, linked or not. A failure under the current inputs stays put.
    planned = [p for p in replay_control.plan(session) if p.computable and p.reason in ("missing", "stale")]
    gap_prints: dict = {}
    todo, keys = _sendable(state, now, planned, False, gap_prints)
    state.last_planned, state.last_found = now, False
    if not _send(session, client, state, now, counts, _order(session, todo), keys, gaps_only=False):
        return
    # Every sendable control round is on its way. Now the rounds whose control is current but whose timing
    # gaps aren't (new gap rules, an edited choke asset, a result that was dropped): gaps only, control kept
    # (app/services/replay_gaps.py, plan_gaps).
    every = replay_control.plan(session, force=True)
    todo, keys = _sendable(state, now, replay_gaps.plan_gaps(session, planned, every), True, gap_prints)
    _send(session, client, state, now, counts, _order(session, todo), keys, gaps_only=True)
```

`_order` returns a new sorted list of the same objects, so `keys[id(p)]` still finds each one.

Add `"gaps_sent"` to the counts tuple in `cycle`:

```python
    counts: dict[str, int] = {k: 0 for k in ("sent", "stored", "stored_failed", "skipped", "forgotten", "timed_out",
                                             "dropped_revision", "dropped_geometry", "infra_failed", "busy",
                                             "unreachable", "gaps_sent", "gaps_stored", "gaps_dropped",
                                             "gaps_skipped")}
```

In the module docstring, add to point 2 ("Submit"), after "Only a job seen `running` for STALE_JOB_S does.":

```
   When every sendable control round is on its way, the rounds whose control is current but whose timing
   gaps are missing or stale (`replay_gaps.plan_gaps`) are sent as `gaps_only` tasks, through the same
   queue and limit.
```

- [ ] **Step 6: Run the dispatcher's tests**

Run: `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_control_isolation.py tests/replays/test_gaps_task.py -p no:cacheprovider -q`
Expected: PASS.

`test_missing_rounds_are_sent_then_stored` ends with `assert remote.cycle(factory, worker, state, now=20)["sent"] == 0`. It still holds: by then every round has control and, since Task 3, its gaps.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/services/replay_control_remote.py webapp/tests/replays/test_control_remote.py
git commit -m "control dispatch: gaps-only tasks for rounds whose control is current"
```

---

### Task 5: Docs, the full suite, and the checks only the live services can give

**Files:**
- Modify: `docs/superpowers/plans/2026-10-05-control-idle-queue.md` (the D4 row)
- Modify: `replay_worker/README.md`
- Modify: `webapp/RENDER_DEPLOY.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code reads.

- [ ] **Step 1: Mark D4 amended**

In `docs/superpowers/plans/2026-10-05-control-idle-queue.md`, append to the end of the D4 row's decision cell (before the ` | user (...)` source cell):

```
 **Amended 2026-10-07 (the user's call):** a parse kills every running child but the one started first, and control keeps one child running beside a parse (`docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md`, Task 1).
```

- [ ] **Step 2: The worker's README**

In `replay_worker/README.md`, add a section before "## Configuration":

```markdown
## Map control and timing gaps

`POST /control` and `GET /control/{id}` (`server.py`, `control_job.py`; `docs/map-control-worker-plan.md`). The
web app sends one round at a time and stores the result; the worker has no database.

- Up to `REPLAY_CONTROL_WORKERS` children (2) while nothing is parsing or waiting to parse, and one beside
  a parse. A parse arriving kills every running child but the one started first; a killed round goes back to
  the front of the queue and is not counted as a failure.
- A task with a `gaps` block also returns the round's timing gaps (`webapp/app/gaps`, shipped in the image).
  The web app sends the gap fingerprint and the cache key, because the control interpreter has no SQLAlchemy
  to import the web app's own helpers with.
- The child writes a tick cache file under `CONTROL_CACHE_DIR/gaps` while it runs and deletes it when the
  task ends, along with any file older than six hours.
- Every result names the engine's revisions and the hash of the game figures it read, so the web app can
  drop a result from another deploy.
```

- [ ] **Step 3: The deploy note**

In `webapp/RENDER_DEPLOY.md`, add at the end:

```markdown
**Timing gaps on the worker (2026-10-07).** The site and the `replay-worker` deploy separately from the same
merge. In either order nothing breaks: a new site with an old worker gets gap runs it drops (and asks for
again, up to three times a round until the worker is new); an old site with a new worker asks for no gaps.
After both are live, the site's log line `map control dispatch: ...` shows `gaps_stored`. A parse and one
control child now run together, so look at the worker's memory graph on the first upload after the deploy;
if it nears 4 GB, lower `REPLAY_CONTROL_MEMORY_MB` in the dashboard.
```

- [ ] **Step 4: The whole replay suite**

Run: `PY -m pytest tests/replays -k "not pg" -p no:cacheprovider -q`
Expected: the baseline's pass count plus the tests this plan adds, less the four it deletes; the only failure is the known Windows one.

If `VALO_TEST_DATABASE_URL` is set, also run: `PY -m pytest tests/replays -k "pg" -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-10-05-control-idle-queue.md replay_worker/README.md webapp/RENDER_DEPLOY.md
git commit -m "docs: timing gaps on the worker, and the one-child rule beside a parse"
```

- [ ] **Step 6: Write the live checks into the PR description**

These cannot be checked from the tests; list them in the PR for the owner:

1. The worker image builds (its smoke test imports `app.gaps`).
2. After both services deploy, the site's dispatch log shows `gaps_stored`, and a round's "Timing" panel on a replay page reads current, not stale.
3. During one upload: the worker's log shows one control child still running, and its memory graph stays under 4 GB.
4. A round with gaps finishes inside the child's time and memory caps (`REPLAY_CONTROL_TIMEOUT_S` 900, `REPLAY_CONTROL_MEMORY_MB` 2048): no rise in `infra_failed`.

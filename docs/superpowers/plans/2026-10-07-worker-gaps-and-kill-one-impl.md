# Timing gaps on the worker, and a parse kills one control child: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every round the replay worker computes map control for also gets its timing gaps, stored by the site with nobody running `compute_control.py`; and a parse arriving on the worker kills one control child, not both, so control keeps one child running beside a parse.

**Architecture:** Three changes to code that exists. (1) `ControlRunner` in `replay_worker/server.py` allows one child while the parse side is busy and two while it is idle; `preempt` kills every running child but the one started first. (2) The control child (`app/control/task.py`, `replay_worker/control_job.py`) computes gaps from keys the site sends, so it never imports the web app's SQLAlchemy code, and deletes its tick cache file when the task ends; the worker image ships `app/gaps`. (3) The worker advertises `control.gaps_protocol=1` in `/health`; the dispatcher sends gaps only to a capable worker and keeps sending plain control to older workers. Returned gaps are validated and written under the replay lock in one transaction, after control. Gaps-only tasks use the same capability gate and queue.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, stdlib `subprocess`/`threading` on the worker, pytest. No migration, no new dependency.

**Spec:** `docs/superpowers/plans/2026-10-07-auto-reparse-queue.md`, sections "Timing gaps on the worker" and decision 4. This is the first of two PRs; the second is `docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md` and does not need to exist for this one to ship.

Two places where this plan departs from the spec, both smaller than what it says:

- The spec moves four pure helpers out of `app/services/replay_gaps.py`. Fourteen existing tests monkeypatch those names on that module, so moving them breaks all of them. Here the site sends the gap fingerprint and the cache key in the task, and the child uses them. `replay_gaps.py` is not touched.
- The spec has `store._delete` delete gap rows explicitly for the SQLite tests. PostgreSQL already cascades them (`fk_replay_gaps_round`, `fk_replay_round_gap_runs_round`), and adding the deletes would break every test fixture that builds its tables from `test_replay_store.TABLES`, which has no gap tables. Left out.

## Review incorporated (2026-10-07)

Findings 1 and 4 of `2026-10-07-auto-reparse-queue-impl-review.md` are addressed by the capability gate and the transactional gap writer. The plan remains five tasks with no migration. Tasks 3-4 below replace the original collector snippets; do not retain their detached freshness precheck or fabricated old-image response. These implementation details supersede the corresponding older design prose.

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

1. **The site deploys before the worker.** An old image has no `app.gaps`; giving it a gaps block fails the entire control task, including `_CacheGuard(None)`. Expected: its missing capability makes the site send plain control, with no gaps-only work and no gap retry consumption. Once the worker updates, missing gaps are picked up automatically. Pinned in Tasks 2-4.
2. **Control moves between collection and the gap write** (a re-ingest or relink). Expected: the gap writer checks freshness under the replay advisory lock in the write transaction, stores nothing stale, and preserves any newer gap run. Pinned in Tasks 3 and 4.
3. **Gap rows after a JSON round trip.** The local command stores rows in-process; the worker's go through JSON, which turns integer dict keys into strings. Expected: they store. Pinned in Task 3.
4. **A parse arrives while exactly one child runs.** Expected: nothing is killed, and no second child starts until the parse side is idle. Pinned in Task 1.
5. **A child killed mid-round leaves a tick cache file.** Expected: the next child deletes files older than six hours, so the worker's disk does not fill. Pinned in Task 2.

---

## File structure

| File | Change |
|---|---|
| `replay_worker/server.py` | Tasks 1-2: one-child rule and `control.gaps_protocol` in health |
| `webapp/tests/replays/test_replay_worker_control.py` | Tasks 1-2 |
| `webapp/app/control/task.py` | Task 2: `_cache_path`, `_gaps` |
| `replay_worker/control_job.py` | Task 2: `figures`, the tick cache cleanup |
| `replay_worker/Dockerfile` | Task 2: copy `app/gaps`, the smoke test |
| `webapp/tests/replays/test_gaps_task.py` | Task 2 |
| `webapp/app/services/replay_control_remote.py` | Tasks 3-4 |
| `webapp/tests/replays/test_control_remote.py` | Tasks 3-4 |
| `webapp/app/services/replay_gaps_store.py`, `webapp/tests/replays/test_gaps_store.py` | Task 3: atomic freshness guard |
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

### Task 2: The child computes gaps, cleans its cache, and advertises the capability

**Files:**
- Modify: `webapp/app/control/task.py` (`_cache_path`, `_gaps`)
- Modify: `replay_worker/control_job.py`
- Modify: `replay_worker/Dockerfile`
- Modify: `replay_worker/server.py` (stdlib protocol constant and health response)
- Test: `webapp/tests/replays/test_gaps_task.py`, `webapp/tests/replays/test_replay_worker_control.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces, for Task 3:
  - A task's `gaps` block may carry `"gap_fingerprint": str` and `"engine_key": str`. With `gap_fingerprint` present, `compute_task` writes it into `result["gaps"]["run"]["fingerprint"]`, takes `gaps_revision` from `app.gaps.detect.GAPS_REVISION`, and never imports `app.services`. Without it, behaviour is unchanged (the local command).
  - `control_job.run(task)` adds `"figures": cf.figures_hash()` to every result.
  - `control_job.run(task)` deletes the round's tick cache file after computing.
  - `GET /health` exposes `control.gaps_protocol=1`. The server uses a stdlib constant, never a detector import; absent or unsupported versions are treated as incapable by the site.

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
import uuid
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
    key = job.get("engine_key")
    mine = None if key is None else f"{job['replay_id']}-r{job['round']}-{key}.ticks.pkl.gz"
    for path in files:
        try:
            owned = mine is not None and path.name in (mine, mine + ".tmp")
            stale_tick = ".ticks.pkl.gz" in path.name and path.stat().st_mtime < cutoff
            if owned or stale_tick:
                path.unlink()
        except OSError:
            pass


def run(task: dict) -> dict:
    if str(WEBAPP) not in sys.path:
        sys.path.insert(0, str(WEBAPP))
    from app.control.task import compute_task
    from app.replays import control_format as cf

    # Worker caches are never reused by another invocation. Distinct task keys for one round
    # can overlap during a deploy or relink, so isolate each child's temporary cache.
    if task.get("gaps", {}).get("engine_key"):
        task = {**task, "gaps": {**task["gaps"],
                                "engine_key": f"{task['gaps']['engine_key']}-{uuid.uuid4().hex}"}}
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

Add a cleanup regression with two live task keys for the same replay/round: each child uses a unique invocation suffix, and one child's cleanup leaves the other's recent cache and temporary file intact. Cleanup removes only its own exact names plus expired tick-cache files, never every file sharing a replay/round prefix. The local command keeps its existing reusable cache key; only the worker wrapper adds the suffix.

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

- [ ] **Step 6A: Advertise and test the worker's gap protocol**

Add `GAPS_PROTOCOL = 1` to the stdlib-only worker server and include `gaps_protocol` in the health response's `control` object. This describes the deployed child's task/result protocol; `control.enabled` remains the separate on/off switch. Advertise 1 only in the image that includes the child and `app/gaps` changes above. Add an HTTP health test and extend the isolation test to ensure obtaining health does not import `app.control` or `app.gaps`.

Add a regression reproduction for an old task with `app.gaps` unavailable: the existing `_cache_path` fallback raises again in `_CacheGuard(None)` and the whole result is `failed/infra`. Use the unchanged task implementation or a frozen test fixture from the base revision, not a fake successful control response. Task 3's capability tests must prevent sending this image a gaps block.

Run the worker control tests and isolation tests again. Expected: capability 1 on the new worker, the reproduced old-image failure, and no new imports in the server.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/control/task.py replay_worker/control_job.py replay_worker/server.py replay_worker/Dockerfile webapp/tests/replays/test_gaps_task.py webapp/tests/replays/test_replay_worker_control.py
git commit -m "worker: the control child computes timing gaps from the site's keys and keeps no tick cache"
```

---

### Task 3: Capability-gated control tasks and an atomic gap writer

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py`
- Modify: `webapp/app/services/replay_gaps_store.py`
- Test: `webapp/tests/replays/test_control_remote.py`, `webapp/tests/replays/test_gaps_store.py`

**Interfaces:**
- `ControlClient.health() -> dict`, using its existing urllib error handling.
- `supports_gaps(health) -> bool`: only `control.enabled is True` and `control.gaps_protocol == 1` qualify. Missing, unknown, malformed or unreachable capabilities never qualify.
- `gaps_job(replay_id, round_number, fingerprint, map_name) -> dict`, containing the existing three job fields plus the site's `gap_fingerprint` and `engine_key`.
- `InFlight.expect_gaps: bool = False`; it records the capability used at submission, not the capability observed later during collection.
- `store_gaps(..., expected_control_fingerprint: str | None = None) -> str`. The default preserves the local command's existing API; all remote writes supply the expected fingerprint.
- Counts gain `gaps_stored`, `gaps_dropped`, `gaps_skipped`, `gaps_unavailable`.

- [ ] **Step 1: Write capability and result tests before implementation**

Extend the fake worker with `health`, a configurable gap capability and a count of health reads. It must produce plain control answers for plain tasks, and JSON-round-tripped gap rows only when requested. Preserve the existing task shape assertion for legacy workers; for capable workers expect `gaps` as well.

Test absent protocol (the old worker), unsupported protocol, control disabled, and health failure. No gaps block or gaps-only task is sent and no gap retry is consumed. With an absent/unsupported gap protocol but control enabled, plain control is still submitted and stored. A health failure leaves control on its existing best-effort path, using plain tasks. Later health reporting protocol 1 causes the missing gap runs to be scheduled without resetting the site process.

Test a capable full task carries both keys, stores its control and JSON-round-tripped gaps, stores a genuine current gap failure without changing control's status, and drops gaps with a wrong revision, choke hash or fingerprint. Validate returned control revision, data version, geometry and, for capable tasks, figures before storing control. Missing/wrong figures from a capable result are a control trust failure, not something to label current.

Keep the old-image missing-package reproduction from Task 2 as the reason for the capability gate. Do not simulate it as successful control plus failed gaps.

- [ ] **Step 2: Write writer race tests before changing the writer**

For both a full result and a gaps-only result, change the replay's link after result validation but before `store_gaps` enters its write transaction. The old run must be skipped and existing newer rows must survive. Also cover a re-ingest/deletion, missing or failed control, and control with an old data version.

Add a PostgreSQL interleaving test: pause the remote writer after acquiring the replay lock, try a relink/replace from another session, and establish that the complete freshness check and write are serialized. Exercise an already-current gap run written by another writer: the late result must not delete or replace it. Tests may use a narrow seam before invoking the writer; do not simulate freshness with a detached precheck.

- [ ] **Step 3: Extend `store_gaps` with a guarded transaction**

When `expected_control_fingerprint` is supplied:

1. Resolve the replay's match UUID using a scalar query, acquire `replay_db.advisory_lock` for it, then reload the replay and round/control rows after obtaining the lock. A deleted/replaced row returns skipped. Do not reuse an ORM snapshot loaded before waiting for the lock.
2. Recompute current round inputs in that same session. Require current fingerprint = expected fingerprint = the existing `ok` control row's fingerprint, with the current control data version.
3. Recompute the site's gap fingerprint from those inputs. Require the returned run to carry it, the current gap revision and current choke hash. The remote collector has already checked worker revision, geometry and figures.
4. If a gap run already exists under this same fingerprint, return skipped without deleting anything (including a current failed run, unless an explicit local retry overrides it).
5. Only now delete old gap rows, merge the run, insert rows, and commit in this transaction. Any failure rolls the transaction back.

Preserve the existing API and replacement behavior for local callers that omit the new argument, but acquire the replay advisory lock for all gap writers so local and remote writes serialize. Remote callers additionally require the guarded checks above. A foreign-key exception alone is not a freshness guard: a relink retains the replay/round keys. The writer continues to import only database and pure replay helpers.

- [ ] **Step 4: Send and collect full tasks with the capability gate**

Read health once per planning pass and carry the capability through `_submit` and `_send`; do not persist a positive capability across passes or deploys. A plain task keeps the legacy worker key. A full task containing gaps uses a distinct key including protocol 1 and the gap fingerprint, so deduplication cannot hand back the earlier plain task's result. `InFlight.expect_gaps` records this choice.

Build the gap block with the site's pure helpers. Validate the control result as above and call `store_round(require_current=True)`. Only after successful control storage, validate the gap result and call the guarded `store_gaps(expected_control_fingerprint=f.fingerprint)`. A link change in between the two commits is checked by the writer and produces `gaps_skipped`. A plain task's absent gaps are expected and do not count as dropped or failed.

A deploy can occur between health and submission. If a task reaches an old worker despite the fresh capability read, its infra failure is retried under the next pass's capability and task key. It must not exhaust the retry budget for subsequent plain tasks or require a restart to recover when the worker updates. Test this interleaving explicitly.

- [ ] **Step 5: Validate and commit**

Run: `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_gaps_store.py tests/replays/test_control_isolation.py -p no:cacheprovider -q`.

Run the guarded-writer interleavings against the test PostgreSQL database when `VALO_TEST_DATABASE_URL` is configured; otherwise record that they were skipped and must pass before release. Never point this suite at either deployed database.

Commit the dispatcher, writer and tests with a message describing capability-gated gaps and guarded storage.

---

### Task 4: Gaps-only tasks for rounds whose control is current

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py`
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- `InFlight.gaps_only: bool = False` in addition to `expect_gaps`.
- `_sendable(state, now, planned, gaps_only, gap_prints, ...) -> (todo, keys)` and `_send(...)`, preserved for the second plan's hold-back integration.
- `_submit(session, client, state, now, counts)` submits control first, then gaps-only work only when every sendable control task has been submitted and the worker supports gaps.
- Counts gain `gaps_sent`.

- [ ] **Step 1: Write the failing scheduling tests**

Cover current control with no gaps, stale gap rules/assets, a current gap failure left alone, control-before-gaps ordering, and a pool filled entirely by control work. No gaps-only work is sent to a worker without protocol 1. Upgrade the same fake worker in place and prove gaps are then scheduled without a site restart or manual retry reset.

Test that a gap result arriving after control moves is skipped by the guarded writer and does not rewrite either control or newer gaps. A returned revision, data version, geometry, figures, gap revision/hash/fingerprint mismatch counts as a retryable trust failure. A genuine current detector failure is stored as the round's gap failure and is not retried by default. Infra job failures back off and consume the normal three-try budget.

- [ ] **Step 2: Implement keys and admission**

A gaps-only key contains replay id, round, control fingerprint, protocol version and gap fingerprint. Use a prefix distinct from both plain and full-control tasks. The planner uses `replay_control.plan` for control and its forced plan for `replay_gaps.plan_gaps`, as the local command does. Only send gaps-only work with fresh positive capability; an unavailable capability leaves this work pending rather than consuming tries or disabling future planning.

Keep `IN_FLIGHT`, newest-match ordering and backoff behavior. After a planning pass with nothing sendable, retain the existing `PLAN_IDLE_S` throttle. Capability upgrades and a completed/replaced replay must become visible by the next permitted planning pass.

- [ ] **Step 3: Collect through the guarded writer**

For a done gaps-only job, check the control revision and data version, geometry, figures and gap run metadata, then call the guarded writer with `f.fingerprint`. There is no `_control_is_current` read in a separate session: the check belongs inside the writer's transaction. A skipped stale result is not an engine failure and consumes no retry.

Delete completed in-flight entries only after choosing the appropriate settlement path. Preserve the existing queued/running timeout semantics and separate genuine detector failures from transport/trust failures.

- [ ] **Step 4: Update existing fixtures and validate**

Give throttle tests current gap runs where they formerly populated only current control, and count the control and forced planning calls. Distinguish plain/full control from gaps-only tasks in `sent_rounds`. Use `CONTROL_TABLES`, which includes both gap tables.

Run: `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_gaps_store.py tests/replays/test_control_isolation.py tests/replays/test_gaps_task.py -p no:cacheprovider -q`.

Commit the dispatcher and tests. The second plan consumes these interfaces, not the superseded detached freshness check.

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
- Health advertises `control.gaps_protocol=1`; an older worker receives plain control until it updates.
- Every result names the engine's revisions and the hash of the game figures it read, so the web app can
  drop a result from another deploy.
```

- [ ] **Step 3: The deploy note**

In `webapp/RENDER_DEPLOY.md`, add at the end:

```markdown
**Timing gaps on the worker (2026-10-07).** The site and the `replay-worker` deploy separately from the same
merge. Either deployment order is supported by the health capability gate: a new site with an old worker
sends plain control and leaves gaps pending; an old site with a new worker asks for no gaps. Once the worker
advertises control.gaps_protocol=1, the next planning pass picks up missing gaps without a site restart.
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
4. Deploy-skew check: plain control works before the worker advertises protocol 1, then gap work resumes without resetting retries.
5. A round with gaps finishes inside the child's time and memory caps (`REPLAY_CONTROL_TIMEOUT_S` 900, `REPLAY_CONTROL_MEMORY_MB` 2048): no rise in `infra_failed`.

---

## Second review incorporated (2026-10-07)

`2026-10-07-auto-reparse-queue-impl-review-2.md` reviewed this rewrite against the code. Every finding is
accepted. Where this section and a task above differ, this section wins.

**Gates (A-5).** Every check command ends `-p no:cacheprovider -q -rs`, and the expected result includes
"0 skipped among `test_pg_*`". The PostgreSQL interleaving tests are named `test_pg_...` and use the `pg`
fixture of `tests/replays/test_replay_store.py`. No other test name contains `pg` (so not "upgrade"), because
the suite is split with `-k "pg"`.

**Task 2 (A-7, A-10).** The old-image reproduction sets `sys.modules["app.gaps"]` to `None` on a task with a
`gaps` block and asserts `status == "failed"` and `error_kind == "infra"`. The `/health` line of the server's
module docstring names `gaps_protocol`.

**Task 3, keys and state (A-1).**

- Plain task key: `"{replay}:{round}:{fingerprint}"` (unchanged). Full task with gaps:
  `"{replay}:{round}:{fingerprint}:g1:{gap_fingerprint}"`. Gaps-only task (task 4):
  `"{replay}:{round}:{fingerprint}:go1:{gap_fingerprint}"`. Suffixes, so the first three fields keep their
  positions.
- `state.in_flight` and `state.tries` are keyed by the worker key. A round is left out of planning while any
  job for its `(replay_id, round_number)` is in flight, whatever its kind, so a capability change never
  makes a second job for a round.
- The tests' fake worker is a legacy worker by default (its health names no `gaps_protocol`). The existing
  tests stay as they are and are the old-worker regression; a capable worker is asked for per test.
- Without the capability the forced plan and `plan_gaps` do not run at all.

**Task 3, functions (A-3).**

- `_sendable(state, now, rounds, *, kind, capable=False, held=frozenset()) -> list[tuple[key, round]]`: the
  planned rounds of `kind` (`"control"` or `"gaps"`) that are not held, not in flight for their round, and
  pass `may_try` for their worker key.
- `_send(session, client, state, now, counts, todo, *, kind) -> bool`: submits in `_order` until `IN_FLIGHT`,
  a busy worker or an unreachable one; returns whether sendable work was left.
- Task 3 creates both with `kind="control"`; task 4 adds `kind="gaps"`; the second plan passes `held`.

**Task 3, tests (A-2, A-4, A-8).** The sentence "Later health reporting protocol 1 causes the missing gap
runs to be scheduled" belongs to task 4. Task 3 tests that, once health reports protocol 1, rounds still
needing control go out as full tasks. `app/services/replay_control_remote.py` joins the file list of
`test_the_web_apps_control_views_import_nothing_heavy` (`main.py` imports it lazily, so the import test never
loads it), and the dispatcher compares a run's revision with `app.services.replay_gaps.GAPS_REVISION`. The
guarded writer never replaces a run stored under the same gap fingerprint, ok or failed; the unguarded path
keeps today's replace.

**Task 5 (A-6, A-9).** The deploy note and the PR draft say that, once both services are live, the site sends
a gaps-only task for every stored round whose control is current and whose gap run is missing or stale, one
worker child at a time beside parses, with the read-only query that counts them and `gaps_sent` in the
dispatch log until it drains. The live checks add the worker server process's memory after 200 finished
rounds that carry gap rows (`CONTROL_FINISHED_KEPT`).

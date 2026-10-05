# Map control idle queue: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The replay worker recomputes every round whose map control is missing or out of date, on both CPUs whenever it isn't parsing, newest match first; the replay page says which control revision a round was computed under, or why it's out of date.

**Architecture:** Three changes to code that exists. (1) The web app's dispatcher (`app/services/replay_control_remote.py`) sends `stale` rounds as well as `missing` ones, from unlinked replays too, ordered by the match's `played_at`; a job waiting in the worker's queue never uses up its retries. (2) The worker's `ControlRunner` (`replay_worker/server.py`) starts a child only while the parse side is idle; a parse arriving kills every running child, and its round goes back to the front of the queue, decided once, at settlement, under the runner's lock. (3) The viewer reads the revision already stored in each row's data header (`app/control/encode.py:98`, `"revision": cf.CONTROL_REVISION`, there since Stage 3, `db1a983`) and the server says the current one; no migration.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, stdlib `subprocess`/`threading` on the worker, vanilla JS in the viewer (Node for its tests).

**Spec:** Agreed in conversation on 2026-10-05; no separate spec file. Revised the same day after an external review (findings 1-3 and 9-11, and the two plausible risks; see "Review log" at the end).

| # | Decision | Source |
|---|---|---|
| D1 | The dispatcher sends `missing` **and** `stale` rounds. This reverses "full recomputes stay local" in `docs/map-control-worker-plan.md` (decision 5, S1). `scripts/compute_control.py` keeps working beside it. | user, 2026-10-05 |
| D2 | Order: linked replays by `matches.played_at` newest first; then the rest (unlinked, or a NULL `played_at`) by `replays.created_at` newest first; within a replay, round order. Ties and interleaving don't matter. | user |
| D3 | Unlinked replays are no longer held back. Their rows go stale when the link arrives (the link is in the fingerprint) and are recomputed. Until the companion plan (`2026-10-05-replay-own-sides-and-deaths.md`) lands, an unlinked replay's page still hides control (`app/routers/replays.py:355`), and its rows lack attack/defence and DB-only deaths; they use idle time only. | user |
| D4 | Control children run only while nothing is parsing and both parse queues are empty. A parse arriving kills **all** running children; their rounds go back to the front of the control queue as unstarted, with no failure counted, the task kept, and the map's warmth left as it was. Preemption overrides every other outcome of that run (done, timeout, bad output). | user ("kill both and put at queue") |
| D5 | Two children at once (`REPLAY_CONTROL_WORKERS`, default 2, unchanged): the worker is now `2c-4g` (2 CPU, 4 GB, $85/month). `render.yaml`'s `plan:` changes to match, since a Blueprint sync resets the plan to whatever the file says. | user |
| D6 | The page shows the revision a round was computed under, or says why it's out of date. | user |
| D7 | Deploying (image rebuild, `REPLAY_CONTROL_REMOTE=true`, the plan change) is the user's. This branch writes the steps down and changes nothing on Render. | standing rule |
| D8 | Time a job spends **queued** on the worker never counts against the dispatcher's retry budget; only a job seen `running` for longer than `STALE_JOB_S` counts. A long parse batch delays control but can't make the dispatcher give up on a round. | review finding 2 |

## Global Constraints

- Run tests from `webapp/` in the worktree with the main checkout's interpreter: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe -m pytest ... -p no:cacheprovider` (written `PY -m pytest` below).
- Baseline on clean `main` (2026-10-05): the replay suite with `-k "not pg"` gives 329 passed, 1 failed, 3 deselected. The failure, `test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched` (`ConnectionResetError` WinError 10054), is pre-existing; leave it alone and don't count it against a task.
- The worker server process never imports numpy or `app.control` (`tests/replays/test_control_isolation.py`). The dispatcher never imports the engine.
- The ValoMaths demo never runs the dispatcher (`enabled()` is False in demo mode). No migration in this plan.
- The control child (`replay_worker/control_job.py`) starts no processes of its own; numpy threads are capped by `CHILD_THREADS`. Killing it means killing one process (or its group on Linux).
- Never commit a credential. Never edit anything on Render.
- Comments and docstrings match the surrounding code: plain sentences, reasons not narration, docs referenced by path.

## Review Focus

1. **Preemption racing every other ending of a run** (the child finishing, timing out, printing garbage, or failing to start). Expected: if `preempt()` marked the job at any point before settlement, it is requeued once, at the front, with its task, uncounted, warmth unchanged. Pinned in Task 3 with a deterministic barrier.
2. **A parse batch longer than three `STALE_JOB_S` windows.** Expected: the rounds in flight wait and are stored when the worker gets to them. Pinned in Task 1.
3. **A job admitted in the gap between a queue `get` and `parsing = True`.** Expected: the parse hook called at the start of `_run` kills it. Pinned in Task 3.
4. **Rounds that can never be sent** (`no_map`, `old_blob`, invalid replays, exhausted retries). Expected: they don't keep the dispatcher planning every cycle. Pinned in Task 1.
5. **A fresh row whose data header has no `revision`** (none should exist; the header has carried it since Stage 3). Expected: "revision not recorded", never blank or "rundefined". Pinned in Task 4.

---

## File structure

| File | Change |
|---|---|
| `webapp/app/services/replay_control_remote.py` | Task 1 |
| `webapp/tests/replays/test_control_remote.py` | Task 1 |
| `replay_worker/server.py` | Tasks 2-3 |
| `webapp/tests/replays/test_replay_worker.py` | Task 2 |
| `webapp/tests/replays/test_replay_worker_control.py` | Task 3 |
| `webapp/app/routers/replays.py`, `webapp/app/templates/replays/replay.html`, `webapp/app/static/js/replay_control.js`, `webapp/app/static/js/replay.js` | Task 4 |
| `webapp/tests/replays/test_control_store.py`, `webapp/tests/replays/test_control_viewer.py` | Task 4 |
| `render.yaml`, `docs/map-control-worker-plan.md`, `.gitignore` | Task 5 |

---

### Task 1: The dispatcher sends stale and unlinked rounds, newest match first, and never gives up on a queued job

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py` (docstring; `InFlight`; `State`; `_collect`'s queued/running branch; `_submit`; delete `_unstarted`; new `_rank`, `_order`, `PLAN_IDLE_S`)
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Consumes: `replay_control.plan(db) -> list[PlannedRound]` (unchanged).
- Produces: `PLAN_IDLE_S: int`; `InFlight.running_since: float | None`; `State.last_planned: float | None`, `State.last_found: bool`; `_rank(todo, played: dict[int, datetime], created: dict[int, datetime]) -> list`; `_order(session, todo) -> list`.

- [ ] **Step 1: Replace the two tests whose meaning changes, and add the new ones**

Delete `test_unlinked_replays_wait_for_their_link` and `test_stale_rounds_stay_for_the_local_command`. Add (imports at the top beside the others):

```python
from datetime import datetime, timezone

from app.models.match import Match  # noqa: E402
from test_control_store import put_row  # noqa: E402


def sent_rounds(worker):
    return sorted(int(t["key"].split(":")[1]) for t in worker.tasks.values())


def test_unlinked_replays_are_sent_too(factory, db, linked):
    linked.link_status = "unlinked"
    linked.match_id = None
    db.commit()
    worker = FakeWorker()
    assert remote.cycle(factory, worker, remote.State(), now=0)["sent"] == remote.IN_FLIGHT


def test_stale_rounds_are_sent_and_a_current_failure_is_not(factory, db, linked):
    put_row(db, linked, 1, fingerprint="0" * 16)                    # stale: an old fingerprint
    put_row(db, linked, 2, status="failed")                         # failed under the current inputs
    put_row(db, linked, 3, status="failed", fingerprint="1" * 16)   # failed under old inputs: stale
    for n in range(4, linked.round_count + 1):
        put_row(db, linked, n)
    worker = FakeWorker()
    remote.cycle(factory, worker, remote.State(), now=0)
    assert sent_rounds(worker) == [1, 3]


def test_rank_puts_linked_by_played_at_before_the_rest_by_upload_time():
    class P:
        def __init__(self, rid, n):
            self.replay_id, self.round_number = rid, n
    played = {1: datetime(2026, 9, 1, tzinfo=timezone.utc), 2: datetime(2026, 10, 1, tzinfo=timezone.utc)}
    created = {3: datetime(2026, 10, 4, tzinfo=timezone.utc), 4: datetime(2026, 10, 3, tzinfo=timezone.utc)}
    ranked = remote._rank([P(1, 1), P(3, 2), P(2, 2), P(2, 1), P(4, 1), P(3, 1)], played, created)
    assert [(p.replay_id, p.round_number) for p in ranked] == [(2, 1), (2, 2), (1, 1), (3, 1), (3, 2), (4, 1)]


@pytest.mark.parametrize("unlink", [False, True])
def test_order_reads_played_at_through_the_link_or_falls_back_to_upload_time(db, linked, monkeypatch, unlink):
    db.get(Match, linked.match_id).played_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    if unlink:
        linked.link_status, linked.match_id = "unlinked", None
    db.commit()
    seen = {}
    monkeypatch.setattr(remote, "_rank", lambda todo, played, created: seen.update(played=played, created=created) or todo)
    remote._order(db, rc.plan(db))
    if unlink:
        assert seen["played"] == {} and set(seen["created"]) == {linked.id}
    else:
        assert set(seen["played"]) == {linked.id} and seen["created"] == {}


def test_planning_is_throttled_while_nothing_can_be_sent(factory, db, linked, monkeypatch):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(1) or real(session, **kw))
    state = remote.State()
    remote.cycle(factory, FakeWorker(), state, now=0)
    remote.cycle(factory, FakeWorker(), state, now=remote.CYCLE_S)
    assert len(calls) == 1
    remote.cycle(factory, FakeWorker(), state, now=remote.PLAN_IDLE_S + 1)
    assert len(calls) == 2


def test_rounds_that_can_never_be_sent_dont_defeat_the_throttle(factory, db, linked, monkeypatch):
    monkeypatch.setattr(rc, "map_layer", lambda name: None)      # every round is no_map, and has no row
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(1) or real(session, **kw))
    state = remote.State()
    for t in range(0, 100, remote.CYCLE_S):
        remote.cycle(factory, FakeWorker(), state, now=t)
    assert len(calls) == 1


def test_exhausted_retries_dont_defeat_the_throttle(factory, db, linked):
    state = remote.State()
    for p in rc.plan(db):
        for _ in range(remote.MAX_TRIES):
            state.failed(remote.task_key(p.replay_id, p.round_number, p.fingerprint), 0)
    worker = FakeWorker()
    remote.cycle(factory, worker, state, now=1)
    assert not worker.tasks and state.last_found is False


def test_a_job_queued_through_a_long_parse_batch_is_still_collected(factory, db, linked):
    phase = {"queued": True}
    worker = FakeWorker(answer=lambda task: {"status": "queued"} if phase["queued"] else FakeWorker.ok(worker, task))
    state = remote.State()
    remote.cycle(factory, worker, state, now=0)
    sent = len(worker.tasks)
    t = 0
    for _ in range(4):                               # four STALE_JOB_S windows queued on the worker
        t += remote.STALE_JOB_S + 1
        remote.cycle(factory, worker, state, now=t)
    assert len(state.in_flight) == sent and not state.tries, "queued time never counts (D8)"
    phase["queued"] = False
    counts = remote.cycle(factory, worker, state, now=t + 1)
    assert counts["stored"] == sent


def test_a_job_running_past_the_limit_counts_as_a_failure(factory, db, linked):
    worker = FakeWorker(answer=lambda task: {"status": "running"})
    state = remote.State()
    remote.cycle(factory, worker, state, now=0)
    remote.cycle(factory, worker, state, now=1)                      # first seen running
    counts = remote.cycle(factory, worker, state, now=2 + remote.STALE_JOB_S)
    assert counts["timed_out"] == remote.IN_FLIGHT and all(c == 1 for c, _ in state.tries.values())
```

`FakeWorker.job` returns `self.answer(...) or {"status": "running"}`; the `{"status": "queued"}` answer above is truthy, so it passes through.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_remote.py -q -p no:cacheprovider`
Expected: the new tests FAIL (`AttributeError: ... '_rank'`, `'PLAN_IDLE_S'`; the long-batch test fails on `state.tries`).

- [ ] **Step 3: Implement**

Imports and constants:

```python
from app.models.match import Match
...
PLAN_IDLE_S = 120               # after a plan that found nothing to send, wait this long before planning again
```

`InFlight` gains `running_since: float | None = None` (last field, defaulted). `State` gains:

```python
    last_planned: float | None = None   # when plan() last ran
    last_found: bool = True             # whether it left anything sendable unsent
```

`_collect`'s queued/running branch (D8):

```python
        status = job.get("status")
        if status == "queued":
            continue                     # D8: waiting behind parses on the worker is not a failure
        if status == "running":
            if f.running_since is None:
                f.running_since = now
            elif now - f.running_since > STALE_JOB_S:
                del state.in_flight[key]
                state.failed(key, now)
                counts["timed_out"] += 1
            continue
```

A preempted job goes back to `queued` on the worker; its next `running` keeps the earlier `running_since`, which is conservative (the worker's own per-child timeout is 900 s / 1800 s warm, well inside `STALE_JOB_S`).

Delete `_unstarted`. Add `_rank` and `_order`:

```python
def _rank(todo: list, played: dict, created: dict) -> list:
    """D2: linked replays by their match's played_at, newest first; then the rest by upload time, newest
    first; rounds in order. Ties fall back to the replay id, newest first: rough order is enough."""
    def key(p):
        if p.replay_id in played:
            return (0, -played[p.replay_id].timestamp(), -p.replay_id, p.round_number)
        when = created.get(p.replay_id)
        return (1, -(when.timestamp() if when else 0.0), -p.replay_id, p.round_number)
    return sorted(todo, key=key)


def _order(session, todo: list) -> list:
    ids = {p.replay_id for p in todo}
    if not ids:
        return []
    played, created = {}, {}
    for rid, status, made, at in (session.query(Replay.id, Replay.link_status, Replay.created_at, Match.played_at)
                                  .outerjoin(Match, Match.id == Replay.match_id)
                                  .filter(Replay.id.in_(ids))):
        if status == "linked" and at is not None:
            played[rid] = at
        else:
            created[rid] = made
    return _rank(todo, played, created)
```

`_submit`:

```python
def _submit(session, client, state: State, now: float, counts: dict) -> None:
    if len(state.in_flight) >= IN_FLIGHT:
        return
    if state.last_planned is not None and not state.last_found and now - state.last_planned < PLAN_IDLE_S:
        return
    # D1/D3: never computed, or out of date, linked or not. A failure under the current inputs stays put.
    todo = [p for p in replay_control.plan(session) if p.computable and p.reason in ("missing", "stale")]
    todo = [p for p in todo if task_key(p.replay_id, p.round_number, p.fingerprint) not in state.in_flight
            and state.may_try(task_key(p.replay_id, p.round_number, p.fingerprint), now)]
    state.last_planned, state.last_found = now, False
    for p in _order(session, todo):
        if len(state.in_flight) >= IN_FLIGHT:
            state.last_found = True          # sendable work is left: plan again next cycle
            break
        key = task_key(p.replay_id, p.round_number, p.fingerprint)
        row = session.get(ReplayRound, (p.replay_id, p.round_number))
        if row is None:
            continue
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]}}
        try:
            answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            state.last_found = True
            break
        except (WorkerGone, Unreachable):
            counts["unreachable"] += 1
            state.last_found = True
            break
        state.in_flight[key] = InFlight(answer["id"], p.replay_id, p.round_number, p.fingerprint, p.map_name, now)
        counts["sent"] += 1
```

A round in backoff (`may_try` false only until `BACKOFF_S`) is filtered out and so doesn't keep `last_found` true; `PLAN_IDLE_S` (120 s) is shorter than `BACKOFF_S` (300 s), so it's re-planned in time. A cycle that filled the window sets `last_found`, so the next free slot is planned at once.

Module docstring: replace step 2 and the "A cycle first counts..." paragraph with:

```
2. **Submit** every round `plan()` lists as `missing` or `stale` (docs/superpowers/plans/2026-10-05-control-idle-queue.md,
   D1-D3), from linked and unlinked replays alike: linked ones by their match's played_at, newest first, then the
   rest by upload time. A failure under the current inputs is left alone. Up to IN_FLIGHT at a time; the worker
   runs them only while it isn't parsing, so a job may sit `queued` there for a long time, and that never counts
   as a failure (D8). Only a job seen `running` for STALE_JOB_S does.

`plan()` runs when there is room in flight; after a plan that left nothing sendable it waits PLAN_IDLE_S. On
PostgreSQL ...
```

and in step 1, change "or one out for STALE_JOB_S" to "or one seen running for STALE_JOB_S".

- [ ] **Step 4: Run** `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_control_store.py -q -p no:cacheprovider` → PASS.

- [ ] **Step 5: Commit** (`"control dispatch: send stale and unlinked rounds, newest match first; queued time isn't failure"`)

---

### Task 2: The worker knows when it's parsing, and calls a hook when a parse comes

**Files:**
- Modify: `replay_worker/server.py` (`Worker.__init__`, `submit`, `submit_reparse`, `_next`, `_run`; new `Worker.idle`)
- Test: `webapp/tests/replays/test_replay_worker.py`

**Interfaces:**
- Produces: `Worker(settings, on_parse: Callable[[], None] | None = None)`; `Worker.idle() -> bool`; `Worker.parsing: bool`. `on_parse` is called outside the worker's lock when an upload or reparse is accepted, and again at the start of every parse.

- [ ] **Step 1: Write the failing test** (in `test_replay_worker.py`, which has the `stub` fixture with a `slow` mode and `vrf_bytes()`):

```python
def test_the_worker_says_when_it_is_idle_and_calls_the_parse_hook(tmp_path, stub):
    calls = []
    settings = server.Settings(parser_cmd=[sys.executable, str(stub), "{vrf}", "{out}", "slow"],
                               temp_root=tmp_path / "jobs")
    settings.temp_root.mkdir(exist_ok=True)
    worker = server.Worker(settings, on_parse=lambda: calls.append(time.time()))
    assert worker.idle()
    job = worker.submit(vrf_bytes())
    assert calls, "accepting an upload calls the hook at once"
    assert not worker.idle()
    end = time.time() + 60
    while worker.get(job.id).status not in ("done", "failed") and time.time() < end:
        time.sleep(0.05)
    assert worker.get(job.id).status == "done"
    assert worker.idle() and len(calls) >= 2, "a parse starting calls it again"
```

- [ ] **Step 2: Run** `PY -m pytest tests/replays/test_replay_worker.py -k idle -q -p no:cacheprovider` → FAIL (`unexpected keyword argument 'on_parse'`).

- [ ] **Step 3: Implement**

```python
    def __init__(self, settings: Settings, on_parse=None):
        self.settings = settings
        self.on_parse = on_parse or (lambda: None)   # map control's preempt (D4); always called outside self.lock
        self.parsing = False
        ...

    def idle(self) -> bool:
        """No parse running and none waiting: map control may run."""
        return not self.parsing and self.queue.empty() and self.reparse_queue.empty()
```

`submit` and `submit_reparse`: call `self.on_parse()` after `put_nowait` succeeds.

`_next` sets `self.parsing = True` right after it takes a job, in both branches (`job = self.queue.get(); self.parsing = True; return job`, and the same inside the archive loop). `_run` calls `self.on_parse()` right after `_next()` returns, and sets `self.parsing = False` as the last line of its `finally`.

Between a queue's `get` and `parsing = True` there is a window of a few bytecodes where `idle()` reads True. That is accepted, not closed: a child started in it is killed by the `on_parse()` call at the top of `_run` (Task 3 pins this). Say so in `idle()`'s docstring.

- [ ] **Step 4: Run** `PY -m pytest tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_worker_control.py -q -p no:cacheprovider` → PASS except the pre-existing failure.

- [ ] **Step 5: Commit** (`"replay worker: say when it's idle, and call a hook when a parse comes"`)

---

### Task 3: Control runs only while the worker is idle, and a parse kills it

**Files:**
- Modify: `replay_worker/server.py` (`kill_tree`; `ControlRunner.__init__`, `_next`, `_run`; new `preempt`, `_Preempted`; `counts`; `main`; module docstring)
- Test: `webapp/tests/replays/test_replay_worker_control.py`

**Interfaces:**
- Consumes: `Worker.idle()`, `Worker(on_parse=...)` (Task 2).
- Produces: `ControlRunner(settings, idle: Callable[[], bool] | None = None)`; `ControlRunner.idle` (assignable); `ControlRunner.preempt() -> int`; `counts()["preempted"]: int`; `ControlRunner._before_settle: Callable[[ControlJob], None]` (a test seam, a no-op by default).

- [ ] **Step 1: Write the failing tests**

```python
def wait_until(predicate, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_nothing_starts_while_the_worker_is_busy(runner):
    busy = {"on": True}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    job = r.submit(task("a:1:f"))
    time.sleep(1.5)
    assert r.get(job.id).status == "queued" and not list(log.iterdir())
    busy["on"] = False
    wait_all(r, [job])
    assert r.get(job.id).status == "done"


def test_a_parse_kills_every_child_and_their_rounds_go_back_first_uncounted(runner):
    busy = {"on": False}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    wait_all(r, [r.submit(task("w:1:f"))])                       # the map is warm: two may run at once
    a, b = r.submit(task("a:1:f", mode="sleep:30")), r.submit(task("b:1:f", mode="sleep:30"))
    c = r.submit(task("c:1:f"))
    wait_until(lambda: r.counts()["running"] == 2)
    busy["on"] = True
    assert r.preempt() == 2
    wait_until(lambda: r.counts()["running"] == 0)
    assert {r.get(a.id).status, r.get(b.id).status} == {"queued"} and r.get(c.id).status == "queued"
    assert set(r.pending[:2]) == {a.id, b.id} and r.pending[2] == c.id
    assert r.get(a.id).error is None and r.get(a.id).task, "uncounted, task kept"
    assert r.counts()["preempted"] == 2 and "Ascent" in r.counts()["warm"]
    busy["on"] = False
    r.jobs[a.id].task = json.dumps(task("a:1:f")).encode()     # let them finish quickly this time
    r.jobs[b.id].task = json.dumps(task("b:1:f")).encode()
    wait_all(r, [a, b, c])


def test_a_preempted_warming_round_stays_cold_and_warms_alone_again(runner):
    busy = {"on": False}
    r, log = runner()
    r.idle = lambda: not busy["on"]
    first = r.submit(task("w:1:f", mode="sleep:30"))
    second = r.submit(task("x:1:f"))
    wait_until(lambda: r.counts()["running"] == 1)
    busy["on"] = True
    assert r.preempt() == 1
    wait_until(lambda: r.counts()["running"] == 0)
    assert r.get(first.id).status == "queued" and "Ascent" not in r.counts()["warm"]
    r.jobs[first.id].task = json.dumps(task("w:1:f", mode="sleep:1")).encode()
    busy["on"] = False
    wait_all(r, [first, second])
    starts = sorted((float(p.name.split("_")[0]), p.name) for p in log.iterdir() if "_start_" in p.name)
    ends = sorted(float(p.name.split("_")[0]) for p in log.iterdir() if "_end_" in p.name)
    # after the resume, the warming round ran alone: the second round started after it ended
    assert starts[-1][0] >= ends[-2]


@pytest.mark.parametrize("ending", ["done", "timeout", "garbage"])
def test_preemption_at_settlement_wins_over_every_other_ending(runner, ending):
    mode = {"done": "ok", "timeout": "hang", "garbage": "garbage"}[ending]
    overrides = {"control_timeout_s": 1.0, "control_warm_timeout_s": 1.0} if ending == "timeout" else {}
    r, log = runner(**overrides)
    gate = threading.Event()
    # the preempt lands after the child has ended, before settlement; idle goes false first so it can't rerun
    r._before_settle = lambda job: (setattr(r, "idle", lambda: False), r.preempt(), gate.set())
    job = r.submit(task("p:1:f", mode=mode))
    assert gate.wait(30)
    wait_until(lambda: r.counts()["running"] == 0)
    assert r.get(job.id).status == "queued" and r.get(job.id).error is None and r.get(job.id).task
    assert r.pending == [job.id] and r.counts()["preempted"] == 1
    assert "Ascent" not in r.counts()["warm"], "warmth unchanged: it was cold"


def test_a_child_started_in_the_admission_gap_is_killed_by_the_parse_starting(runner):
    r, log = runner()
    job = r.submit(task("g:1:f", mode="sleep:30"))
    wait_until(lambda: r.counts()["running"] == 1)               # admitted while idle() read True
    r.idle = lambda: False
    assert r.preempt() == 1                                      # what Worker._run's on_parse() does
    wait_until(lambda: r.counts()["running"] == 0)
    assert r.get(job.id).status == "queued"


def test_the_worker_wires_its_parse_hook_to_control(monkeypatch):
    made = {}

    class Fake:
        server_address = ("x", 1)

        def serve_forever(self):
            pass
    monkeypatch.setattr(server, "make_server", lambda worker, host, port, control: made.update(
        worker=worker, control=control) or Fake())
    monkeypatch.setattr(server.Settings, "from_env", classmethod(lambda cls, env=None: cls(control_cmd=["true"])))
    server.main()
    assert made["worker"].on_parse == made["control"].preempt
    assert made["control"].idle == made["worker"].idle
```

Add a `garbage` mode to the `STUB`: `elif mode == "garbage": print("not json")` before the final `else`.

- [ ] **Step 2: Run** `PY -m pytest tests/replays/test_replay_worker_control.py -q -p no:cacheprovider` → the new tests FAIL.

- [ ] **Step 3: Implement**

`kill_tree` (review finding 3): never return early on Linux, bound every wait.

```python
def kill_tree(process: subprocess.Popen) -> None:
    """Kill a child and everything it started; every wait is bounded. On Linux the child leads its own process
    group (setsid), so the group is killed even when the leader has already exited."""
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
                               check=False, timeout=10)
            except subprocess.TimeoutExpired:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
```

(The parser path also calls `kill_tree`; this only makes it stricter. Check `test_the_timeout_kills_a_hung_parse_and_cleans_up` still passes.)

`ControlRunner.__init__` gains:

```python
        self.idle = idle or (lambda: True)    # the parse side has nothing running or waiting (D4)
        self.procs: dict[str, subprocess.Popen] = {}
        self.preempted: set[str] = set()       # running jobs a parse has killed; settled as requeued (D4)
        self.preempted_total = 0
        self._before_settle = lambda job: None  # tests only: runs after the child ends, before settlement
```

`_next`: first line `if len(self.running) >= self.settings.control_workers or not self.idle(): return None`.

`preempt`:

```python
    def preempt(self) -> int:
        """A parse is coming: kill every running child (D4). How each run ends is decided at settlement,
        under the lock, where a preempted mark wins over everything else."""
        with self.lock:
            victims = [(job_id, self.procs.get(job_id)) for job_id in self.running if job_id not in self.preempted]
            self.preempted.update(job_id for job_id, _ in victims)
        for _, process in victims:
            if process is not None:
                kill_tree(process)
        return len(victims)
```

`_run`: the process starts **outside** the lock (`preexec_fn` must not run while this thread holds a lock another thread needs; the plausible Linux deadlock in the review), is registered under it, and is killed at once if a preempt landed in between. Settlement is one block under the lock that checks `preempted` first:

```python
    def _run(self, job: ControlJob, warming: bool) -> None:
        settings = self.settings
        timeout = settings.control_warm_timeout_s if warming else settings.control_timeout_s
        env = {**os.environ, **CHILD_THREADS}
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(WEBAPP), str(WEBAPP.parent), env.get("PYTHONPATH")) if p)
        kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env,
                  "cwd": str(WEBAPP.parent)}
        if os.name != "nt":
            kwargs["preexec_fn"] = _child_setup(settings.control_memory_mb)
        status, error, kind, result = "failed", None, "infra", None
        process = None
        try:
            process = subprocess.Popen(settings.control_cmd, **kwargs)
            with self.lock:
                self.procs[job.id] = process
                killed_before_start = job.id in self.preempted
            if killed_before_start:
                kill_tree(process)
            stdout, _ = process.communicate(job.task, timeout=timeout)
            answer = json.loads(stdout or b"{}")
            if answer.get("status") == "ok":
                status, kind, result = "done", None, answer
            else:
                error = answer.get("error") or f"control child exited {process.returncode}"
                kind = answer.get("error_kind") or "infra"
        except subprocess.TimeoutExpired:
            error = f"control timed out after {timeout:g} s"
        except (OSError, ValueError) as failure:
            error = f"control child failed: {failure}"
        except Exception:  # noqa: BLE001 - one bad round must not stop the runner
            traceback.print_exc()
            error = "control child failed"
        finally:
            if process is not None:
                kill_tree(process)                     # bounded; a no-op for a child that exited cleanly
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        if stream is not None:
                            stream.close()
                    except OSError:
                        pass
        self._before_settle(job)
        with self.lock:
            self.procs.pop(job.id, None)
            self.running.pop(job.id, None)
            if job.id in self.preempted:
                # D4: whatever the child did, a parse killed this run. Back to the front, uncounted, task kept,
                # warmth untouched.
                self.preempted.discard(job.id)
                self.preempted_total += 1
                job.status = "queued"
                self.pending.insert(0, job.id)
            else:
                job.status, job.error, job.error_kind, job.result = status, error, kind, result
                job.finished = time.time()
                job.task = b""
                # Past loading (ok, or the round's own failure): the map's cache is there. Any machine
                # failure makes the map cold again, so its next round warms alone.
                if status == "done" or kind == "engine":
                    self.warm.add(job.map)
                else:
                    self.warm.discard(job.map)
                finished = sorted((j for j in self.jobs.values() if j.finished), key=lambda j: j.finished)
                for old in finished[:-CONTROL_FINISHED_KEPT]:
                    del self.jobs[old.id]
                    if self.by_key.get(old.key) == old.id:
                        del self.by_key[old.key]
        self.wake.set()
```

`communicate(timeout=...)` raising `TimeoutExpired` leaves the child running; the `finally` kills it and closes the pipes rather than draining them, so a descendant holding a pipe can't hang the thread (finding 3). On Windows, closing a pipe a descendant still writes to is harmless to us.

`counts()` adds `"preempted": self.preempted_total`.

`main`:

```python
def main() -> None:
    settings = Settings.from_env()
    control = ControlRunner(settings) if settings.control_enabled else None
    worker = Worker(settings, on_parse=control.preempt if control else None)
    if control is not None:
        # Wired after the Worker exists. Until then idle() reads True, but the control queue is empty: the web
        # app hasn't sent anything to a server that isn't listening yet.
        control.idle = worker.idle
    server = make_server(worker, os.environ.get("REPLAY_WORKER_HOST", "0.0.0.0"),
                         int(os.environ.get("REPLAY_WORKER_PORT", "8080")), control)
    ...
```

Module docstring: replace the control paragraph's priority sentence with "It runs only while nothing is parsing or waiting to parse (`Worker.idle`), on up to `control_workers` children; a parse arriving kills every running child, and their rounds go back to the front of the queue uncounted (docs/superpowers/plans/2026-10-05-control-idle-queue.md, D4)." and `REPLAY_CONTROL_WORKERS`'s note to "(default 2: the 2-CPU plan's two cores, used only while no parse is running)".

- [ ] **Step 4: Run** `PY -m pytest tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_control_isolation.py -q -p no:cacheprovider` → PASS except the pre-existing failure. Run the new tests three times (`--count` isn't installed; loop in the shell) to catch flakiness.

- [ ] **Step 5: Linux check.** The tests above run on Windows here. Before merging, the user (or CI if one exists) runs `pytest tests/replays/test_replay_worker_control.py` inside the worker image or any Linux Python 3.13 (`docker build -f replay_worker/Dockerfile .` then `docker run ... python -m pytest ...`), since `preexec_fn`, `killpg` and `nice` only run there. If Docker isn't available, say so in the PR; don't claim it was checked.

- [ ] **Step 6: Commit** (`"replay worker: control runs only while idle; a parse kills it and requeues its rounds"`)

---

### Task 4: The page says which revision a round was computed under

**Files:**
- Modify: `webapp/app/routers/replays.py` (`replay_round_control`), `webapp/app/templates/replays/replay.html` (`loadControl`), `webapp/app/static/js/replay_control.js` (`ControlCache.get`; new exported `revisionNote`), `webapp/app/static/js/replay.js` (the two stale messages)
- Test: `webapp/tests/replays/test_control_store.py`, `webapp/tests/replays/test_control_viewer.py`

**Interfaces:**
- Produces: response header `X-Control-Current-Revision` on every `control.bin` 200 and 304; the JS control value gains `revision` (from `parsed.header.revision`, or null) and `currentRevision`; `ReplayControl.revisionNote(value) -> string`.

Why no column: every `ok` row's data already starts with a JSON header holding `"revision"` (`encode.py:98`, since `db1a983`), and the viewer already parses it (`replay_control.js`, `parse` → `out.header`). Whether a stale row is stale because of the revision or its inputs follows from comparing that number with the current one.

- [ ] **Step 1: Write the failing tests**

`test_control_store.py`:

```python
def test_the_endpoint_says_the_current_revision_on_200_and_304(db, linked):
    put_row(db, linked, 1)
    first = call(db, 1)
    assert first.headers["x-control-current-revision"] == str(cf.CONTROL_REVISION)
    again = call(db, 1, headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304 and again.headers["x-control-current-revision"] == str(cf.CONTROL_REVISION)
```

(Use however the existing ETag test, just above `test_a_stale_row_is_still_served_but_flagged`, passes `if-none-match` to `call`.)

`test_control_viewer.py`, in its Node style (it already `require`s `replay_control.js` through `NODE`; follow `READ_STDIN`'s pattern to run a snippet):

```python
@pytest.mark.parametrize("value, expected", [
    ({"stale": False, "revision": 5, "currentRevision": "5"}, "Map control r5."),
    ({"stale": False, "revision": None, "currentRevision": "5"}, "Map control (revision not recorded)."),
    ({"stale": True, "revision": 4, "currentRevision": "5"},
     "Out of date: computed under r4, current r5. Queued for recompute."),
    ({"stale": True, "revision": 5, "currentRevision": "5"},
     "Out of date: computed under r5, but its inputs changed (a new link, the replay, or the map's geometry). "
     "Queued for recompute."),
    ({"stale": True, "revision": None, "currentRevision": "5"},
     "Out of date: revision not recorded. Queued for recompute."),
])
def test_the_revision_note(value, expected):
    script = f"const C = require({str(CONTROL_JS)!r}); process.stdout.write(C.revisionNote({json.dumps(value)}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    assert out == expected
```

- [ ] **Step 2: Run** `PY -m pytest tests/replays/test_control_store.py tests/replays/test_control_viewer.py -k "revision" -q -p no:cacheprovider` → FAIL.

- [ ] **Step 3: Implement**

Router, the last `return` of `replay_round_control`:

```python
    headers = {"X-Control-Current-Revision": str(cf.CONTROL_REVISION)}
    if answer.stale:
        headers["X-Control-Stale"] = "1"
    return _stored_gzip(request, answer.row.data, "application/octet-stream", headers)
```

(Import `control_format as cf` if the router doesn't already.)

`replay.html`'s `loadControl`:

```js
            var stale = r.headers.get("X-Control-Stale") === "1";
            var current = r.headers.get("X-Control-Current-Revision");
            return r.arrayBuffer().then(function (buffer) {
              return { status: "ok", buffer: buffer, stale: stale, currentRevision: current };
            });
```

`replay_control.js`, in `ControlCache.get`'s success branch:

```js
        var parsed = parse(answer.buffer);
        var revision = parsed.header && typeof parsed.header.revision === "number" ? parsed.header.revision : null;
        return { status: "ok", stale: !!answer.stale, revision: revision, currentRevision: answer.currentRevision || null,
                 parsed: parsed, cursor: new Cursor(parsed) };
```

and a new function, exported beside the others the module exports:

```js
  // D6: the revision a round's control was computed under (its data header), and why it's out of date.
  function revisionNote(value) {
    var mine = typeof value.revision === "number" ? "r" + value.revision : null;
    var current = value.currentRevision ? "r" + value.currentRevision : null;
    if (!value.stale) return mine ? "Map control " + mine + "." : "Map control (revision not recorded).";
    if (!mine) return "Out of date: revision not recorded. Queued for recompute.";
    if (current && mine !== current) return "Out of date: computed under " + mine + ", current " + current + ". Queued for recompute.";
    return "Out of date: computed under " + mine + ", but its inputs changed (a new link, the replay, or the " +
      "map's geometry). Queued for recompute.";
  }
```

`replay.js` ~1613: `? ReplayControl.revisionNote(value)` replaces `? (value.stale ? "Computed from older inputs; it will be refreshed." : "")` (use the name `replay.js` already uses for the control module). ~2069: the match table reads `rows.stale` from `players.json`, which has no revisions; change only its text to `"Out of date for some rounds: queued for recompute."`.

- [ ] **Step 4: Run** `PY -m pytest tests/replays/test_control_store.py tests/replays/test_control_views.py tests/replays/test_control_viewer.py tests/replays/test_control_remote.py -q -p no:cacheprovider` → PASS.

Browser check (the `run` skill, or by hand): start the app locally (`CLAUDE.md`, "Running the webapp locally"), open a replay with control, confirm `Map control r5.`; set one row's fingerprint to `'0000000000000000'` in local Postgres and confirm the "inputs changed" note; restore it.

- [ ] **Step 5: Commit** (`"replay page: say which control revision a round was computed under, or why it's out of date"`)

---

### Task 5: render.yaml, the old plan doc, .gitignore

**Files:** `render.yaml`, `docs/map-control-worker-plan.md`, `.gitignore`

- [ ] **Step 1: render.yaml** — replace the `replay-worker`'s six-line sizing comment and `plan: 4c-8g` with:

```yaml
    # 2 CPU / 4 GB ("Pro", Blueprint ID 2c-4g; the user's choice on 2026-10-05). Map control uses both cores,
    # but only while nothing is parsing; a parse kills its children (docs/superpowers/plans/2026-10-05-control-idle-queue.md).
    # Memory: the condense peaked near 1 GB on a 60 MB export, ~3.3 GB scaled to the 200 MB upload cap, before the
    # parser's own (unmeasured) memory. 4 GB is tight for the largest uploads; re-size from Render's measured peak.
    plan: 2c-4g
```

- [ ] **Step 2:** under `docs/map-control-worker-plan.md`'s title:

```
> **Superseded in part (2026-10-05):** the dispatcher now also sends stale and unlinked rounds, newest match first,
> and the worker runs control only while it isn't parsing (a parse kills it). See
> docs/superpowers/plans/2026-10-05-control-idle-queue.md. Decision 5 and S1 below are no longer in force.
```

- [ ] **Step 3:** `.gitignore`: add `.claude/worktrees/` under `.worktrees/`.

- [ ] **Step 4: Run** `PY -m pytest tests/replays -q -k "not pg" -p no:cacheprovider` → everything passes except the pre-existing failure.

- [ ] **Step 5: Commit** (`"render: the replay worker on 2c-4g; mark the old plan's decisions superseded"`)

---

## Deploy steps (the user's)

1. Merge. Both web services redeploy (no migration). The worker image rebuilds from `replay_worker/Dockerfile`.
2. Confirm `replay-worker` shows **2c-4g** in the dashboard.
3. Set `REPLAY_CONTROL_REMOTE=true` on `valowithfriendstracker` only (dashboard, or uncomment it in `render.yaml` in a follow-up commit). Never on `valomaths`.
4. Watch the web log for `map control dispatch: sent N ...` and the worker's `GET /health` → `control.queued / running / preempted`.
5. At about 45 CPU-seconds a round on two cores, ~460 rounds take about 3 hours of idle time. Spot-check a replay page: `Map control r5.`

## Review log (2026-10-05)

An external review confirmed 11 findings against the first draft; this revision takes all of plan 1's:
1 (settlement decided once, under the lock, preemption first: Task 3), 2 (queued time never counts: D8, Task 1),
3 (`kill_tree` never returns early on Linux, every wait bounded, pipes closed not drained: Task 3), 9 (no column:
the data header already carries the revision, so legacy rows don't exist for `ok` rows; the note handles a missing
one anyway: Task 4), 10 (the `_unstarted` fast path is gone; the throttle tracks sendable work: Task 1), 11 (tests
now force the settlement race with a seam, resume after preemption, check the warming round runs alone, exercise
a NULL `match_id`, and the idle test runs where it lives). Plausible risks: `Popen` moved outside the lock; the
admission gap is accepted and pinned by a test instead of synchronised. Not confirmed by the reviewer: a
fingerprint that never settles.

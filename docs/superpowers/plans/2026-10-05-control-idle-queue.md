# Map control idle queue: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The replay worker recomputes every round whose map control is missing or out of date, on both CPUs whenever it isn't parsing, newest match first; the replay page says which control revision a round was computed under.

**Architecture:** Three changes to code that exists. (1) The web app's dispatcher (`app/services/replay_control_remote.py`) sends `stale` rounds as well as `missing` ones, from unlinked replays too, ordered by the match's `played_at`. (2) The worker's `ControlRunner` (`replay_worker/server.py`) starts a child only while the parse side is idle, and a parse arriving kills every running child and puts its round back at the front of the queue, uncounted. (3) A `revision` column on `replay_round_control` (migration 0017) records `CONTROL_REVISION` at store time; the control endpoint returns it and the current one as headers, and the viewer shows `control r6` or why the round is out of date.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL (SQLite in tests), stdlib `subprocess`/`threading` on the worker, vanilla JS in the viewer.

**Spec:** This plan's design was agreed in conversation on 2026-10-05; there is no separate spec file. The decisions it carries:

| # | Decision | Source |
|---|---|---|
| D1 | The dispatcher sends `missing` **and** `stale` rounds. This reverses "full recomputes stay local" in `docs/map-control-worker-plan.md` (decision 5, S1). `scripts/compute_control.py` keeps working beside it. | user, 2026-10-05 |
| D2 | Order: linked replays by `matches.played_at` newest first; then unlinked replays by `replays.created_at` newest first; within a replay, round order. Ties and interleaving don't matter. | user |
| D3 | Unlinked replays are no longer held back. Their rows go stale when the link arrives (the link is in the fingerprint) and are recomputed. Until the companion plan (`2026-10-05-replay-own-sides-and-deaths.md`) lands, an unlinked replay's page still hides control (`app/routers/replays.py`, `"control": ... if linked else None`), and its rows lack attack/defence and DB-only deaths; computing them uses idle time only. | user ("how much do we really need this link?") |
| D4 | Control children run only while nothing is parsing and both parse queues are empty. A parse arriving kills **all** running children; their rounds go back to the front of the control queue as unstarted, with no failure counted. | user ("kill both and put at queue, uploads are infrequent") |
| D5 | Two children at once (`REPLAY_CONTROL_WORKERS`, default 2, unchanged): the worker is now `2c-4g` (2 CPU, 4 GB, $85/month). `render.yaml`'s `plan:` changes to match, since a Blueprint sync resets the plan to whatever the file says. | user |
| D6 | The page shows the revision a round was computed under, or says why it's out of date. | user ("so I don't get confused when debugging") |
| D7 | Deploying (image rebuild, `REPLAY_CONTROL_REMOTE=true`, the plan change) is the user's. This branch writes the steps down and changes nothing on Render. | standing rule |

## Global Constraints

- Run tests from `webapp/` in the worktree with the main checkout's interpreter: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe -m pytest ...` (written `PY -m pytest` below).
- Baseline on clean `main` (2026-10-05): `tests/replays/test_control_remote.py tests/replays/test_replay_worker_control.py` give 20 passed, 1 skipped, 1 failed. The failure, `test_control_off_answers_404_and_parsing_is_untouched` (`ConnectionResetError` WinError 10054 on Windows), is pre-existing; leave it alone and don't count it against a task.
- The worker server process never imports numpy or `app.control` (`tests/replays/test_control_isolation.py` enforces it). The dispatcher never imports the engine.
- The ValoMaths demo never runs the dispatcher (`enabled()` already returns False in demo mode); migration 0017 is additive so the demo DB just gets a nullable column.
- Never commit a credential. Never edit anything on Render.
- Comments and docstrings match the surrounding code: plain sentences, reasons not narration, docs referenced by path.

## Review Focus

1. **A parse arriving while a map is warming.** The killed child was the map's first round (building its visibility cache). Expected: the map stays cold, the round goes back to the front, and it runs alone again when the worker is idle. Pinned in Task 3.
2. **The upload that arrives while a child is between `Popen` and registration.** Expected: that child is killed too, or never starts. Pinned in Task 3 (`preempt` holds the lock that `_run` registers under, and `_run` checks `preempted` before it starts the process).
3. **A long run of parses (an archive reparse batch).** Expected: control waits the whole time and resumes after, nothing is marked failed, and no in-flight job on the web app times out into a retry storm. The dispatcher's `STALE_JOB_S` (1800 s) would drop a job that sat queued on the worker behind a long reparse; that only re-asks it (the worker dedupes by key), so it's harmless. Pinned in Task 2 (a dropped-then-re-asked key stays one job) and Task 3.
4. **A replay whose round is `failed` with the current fingerprint.** Expected: still not re-sent (it would fail the same way); a `failed` row with an old fingerprint is `stale` and is re-sent. Pinned in Task 1.
5. **A row written before migration 0017 (`revision` NULL).** Expected: the page says "computed under an older revision", never "r None" or a blank. Pinned in Task 4.

---

## File structure

| File | Change |
|---|---|
| `webapp/app/services/replay_control_remote.py` | Task 1: plan stale + unlinked rounds, order by played-at, throttle planning |
| `webapp/tests/replays/test_control_remote.py` | Task 1: new tests; two old tests change meaning |
| `replay_worker/server.py` | Tasks 2-3: `Worker` reports idle and calls a parse hook; `ControlRunner` waits for idle, preempts, uses `Popen` |
| `webapp/tests/replays/test_replay_worker_control.py` | Tasks 2-3 |
| `webapp/alembic/versions/0017_control_revision.py`, `webapp/app/models/replay.py`, `webapp/app/services/replay_control_store.py`, `webapp/app/services/replay_control.py`, `webapp/app/routers/replays.py`, `webapp/app/templates/replays/replay.html`, `webapp/app/static/js/replay_control.js`, `webapp/app/static/js/replay.js` | Task 4 |
| `webapp/tests/replays/test_control_store.py` | Task 4 |
| `render.yaml`, `docs/map-control-worker-plan.md`, `.gitignore` | Task 5 |

---

### Task 1: The dispatcher sends stale and unlinked rounds, newest match first

**Files:**
- Modify: `webapp/app/services/replay_control_remote.py` (module docstring; `_unstarted`; `_submit`; `State`; new `_order`, `PLAN_IDLE_S`)
- Test: `webapp/tests/replays/test_control_remote.py`

**Interfaces:**
- Consumes: `replay_control.plan(db) -> list[PlannedRound]` (unchanged; `reason` in `missing|stale|failed|retry_failed|forced|no_map|old_blob`).
- Produces: `remote.PLAN_IDLE_S: int`; `State.last_planned: float`, `State.last_found: bool`; `_order(session, todo) -> list[PlannedRound]`.

- [ ] **Step 1: Replace the two tests whose meaning changes, and add the new ones**

In `test_control_remote.py`, delete `test_unlinked_replays_wait_for_their_link` and `test_stale_rounds_stay_for_the_local_command`, and add:

```python
from datetime import datetime, timedelta, timezone

from app.models.match import Match  # noqa: E402
from app.models.replay import Replay  # noqa: E402
from test_control_store import put_row  # noqa: E402


def sent_rounds(worker):
    return [(t["key"].split(":")[0], int(t["key"].split(":")[1])) for t in worker.tasks.values()]


def test_unlinked_replays_are_sent_too(factory, db, linked):
    linked.link_status = "unlinked"
    db.commit()
    worker = FakeWorker()
    assert remote.cycle(factory, worker, remote.State(), now=0)["sent"] == remote.IN_FLIGHT


def test_stale_rounds_are_sent_and_a_current_failure_is_not(factory, db, linked):
    put_row(db, linked, 1, fingerprint="0" * 16)                # stale: an old fingerprint
    put_row(db, linked, 2, status="failed")                     # failed with the current fingerprint
    put_row(db, linked, 3, status="failed", fingerprint="1" * 16)  # failed under old inputs: stale
    for n in range(4, linked.round_count + 1):
        put_row(db, linked, n)                                  # current and ok
    worker = FakeWorker()
    remote.cycle(factory, worker, remote.State(), now=0)
    assert sorted(n for _, n in sent_rounds(worker)) == [1, 3]


def test_order_reads_played_at_through_the_link_and_keeps_unlinked_rounds(db, linked):
    match = db.get(Match, linked.match_id)
    match.played_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    db.commit()
    rounds = [p.round_number for p in remote._order(db, rc.plan(db))]
    assert rounds == list(range(1, linked.round_count + 1))
    linked.link_status = "unlinked"
    db.commit()
    assert len(remote._order(db, rc.plan(db))) == linked.round_count, "an unlinked replay's rounds stay listed"


def test_order_puts_linked_by_played_at_before_unlinked_by_upload_time(db):
    class P:  # the fields _order reads
        def __init__(self, rid, n):
            self.replay_id, self.round_number = rid, n
    played = {1: datetime(2026, 9, 1, tzinfo=timezone.utc), 2: datetime(2026, 10, 1, tzinfo=timezone.utc)}
    created = {3: datetime(2026, 10, 4, tzinfo=timezone.utc), 4: datetime(2026, 10, 3, tzinfo=timezone.utc)}
    ranked = remote._rank([P(1, 1), P(3, 2), P(2, 2), P(2, 1), P(4, 1)], played, created)
    assert [(p.replay_id, p.round_number) for p in ranked] == [(2, 1), (2, 2), (1, 1), (3, 2), (4, 1)]


def test_planning_is_throttled_while_nothing_needs_computing(factory, db, linked, monkeypatch):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(1) or real(session, **kw))
    state = remote.State()
    remote.cycle(factory, FakeWorker(), state, now=0)
    remote.cycle(factory, FakeWorker(), state, now=remote.CYCLE_S)
    assert len(calls) == 1, "nothing found: the next plan waits PLAN_IDLE_S"
    remote.cycle(factory, FakeWorker(), state, now=remote.PLAN_IDLE_S + 1)
    assert len(calls) == 2


def test_a_round_with_no_row_is_planned_at_once_even_inside_the_throttle(factory, db, linked):
    for n in range(2, linked.round_count + 1):
        put_row(db, linked, n)
    state = remote.State(last_planned=0.0, last_found=False)
    worker = FakeWorker()
    remote.cycle(factory, worker, state, now=1)
    assert sent_rounds(worker) == [(str(linked.id), 1)]
```

The fixture holds one replay, so the DB-backed `_order` test only proves it reads `played_at` through `Replay.match_id` and keeps unlinked rounds. The pure `_rank` test pins the ranking itself.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_remote.py -q`
Expected: the new tests FAIL (`AttributeError: module ... has no attribute '_order'` / `'_rank'` / `'PLAN_IDLE_S'`, and `sent` 0 for unlinked).

- [ ] **Step 3: Implement**

In `replay_control_remote.py`:

Add the import and constant:

```python
from app.models.match import Match
...
PLAN_IDLE_S = 120                # after a plan that found nothing, wait this long unless a round has no row at all
```

Extend `State`:

```python
@dataclass
class State:
    in_flight: dict[str, InFlight] = field(default_factory=dict)
    tries: dict[str, tuple[int, float]] = field(default_factory=dict)   # key -> (failures, last)
    last_planned: float | None = None   # when plan() last ran
    last_found: bool = True             # whether it found anything to send
```

Rename `_unstarted`'s docstring to say what it's now for (the fast path), unlinked included:

```python
def _unstarted(session) -> int:
    """Rounds with no control row at all: these skip the planning throttle."""
    return (session.query(ReplayRound.replay_id)
            .outerjoin(ReplayRoundControl, (ReplayRoundControl.replay_id == ReplayRound.replay_id)
                       & (ReplayRoundControl.round_number == ReplayRound.round_number))
            .filter(ReplayRoundControl.replay_id.is_(None))
            .count())
```

Add the ordering:

```python
def _rank(todo: list, played: dict, created: dict) -> list:
    """Linked replays by their match's played_at, newest first; then the rest by upload time, newest
    first; rounds in order. Ties fall back to the replay id, newest first (D2: rough order is enough)."""
    floor = 0.0

    def key(p):
        if p.replay_id in played:
            return (0, -played[p.replay_id].timestamp(), -p.replay_id, p.round_number)
        when = created.get(p.replay_id)
        return (1, -(when.timestamp() if when else floor), -p.replay_id, p.round_number)
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

Replace `_submit`:

```python
def _submit(session, client, state: State, now: float, counts: dict) -> None:
    if len(state.in_flight) >= IN_FLIGHT:
        return
    throttled = (state.last_planned is not None and not state.last_found
                 and now - state.last_planned < PLAN_IDLE_S)
    if throttled and not _unstarted(session):
        return
    # D1/D3: never computed, or out of date, linked or not. A failure under the current inputs stays put.
    todo = [p for p in replay_control.plan(session) if p.computable and p.reason in ("missing", "stale")]
    state.last_planned, state.last_found = now, bool(todo)
    for p in _order(session, todo):
        if len(state.in_flight) >= IN_FLIGHT:
            break
        key = task_key(p.replay_id, p.round_number, p.fingerprint)
        if key in state.in_flight or not state.may_try(key, now):
            continue
        row = session.get(ReplayRound, (p.replay_id, p.round_number))
        if row is None:
            continue
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]}}
        try:
            answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            break
        except (WorkerGone, Unreachable):
            counts["unreachable"] += 1
            break
        state.in_flight[key] = InFlight(answer["id"], p.replay_id, p.round_number, p.fingerprint, p.map_name, now)
        counts["sent"] += 1
```

`plan()` never emits a `stale`/`missing` round without `link` (it always fills it; unlinked gives `{"linked": False, "sides": {}, "db_deaths": []}`), so `p.link[...]` is safe.

Rewrite the module docstring's step 2 and the paragraph after it:

```
2. **Submit** every round `plan()` lists as `missing` or `stale` (docs/superpowers/plans/2026-10-05-control-idle-queue.md,
   D1-D3), from linked and unlinked replays alike: linked ones by their match's played_at, newest first, then the
   rest by upload time. A failure under the current inputs is left alone. Up to IN_FLIGHT at a time; the worker
   runs them only while it isn't parsing.

`plan()` runs when there is room in flight, except that after a plan that found nothing it waits PLAN_IDLE_S,
unless some round has no control row at all (one cheap query). ...
```

(Keep the advisory-lock and "Off unless" paragraphs as they are.)

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_control_store.py -q`
Expected: all PASS. `test_missing_rounds_are_sent_then_stored` still passes: its single replay is linked and has no rows.

- [ ] **Step 5: Commit**

```
git add webapp/app/services/replay_control_remote.py webapp/tests/replays/test_control_remote.py
git commit -F <msg file>   # "control dispatch: send stale and unlinked rounds, newest match first"
```

---

### Task 2: The worker knows when it's parsing, and tells control

**Files:**
- Modify: `replay_worker/server.py` (`Worker.__init__`, `Worker.submit`, `Worker.submit_reparse`, `Worker._run`; new `Worker.idle`)
- Test: `webapp/tests/replays/test_replay_worker.py`

**Interfaces:**
- Produces: `Worker(settings, on_parse: Callable[[], None] | None = None)`; `Worker.idle() -> bool` (no parse running and both queues empty); `Worker.parsing: bool`. `on_parse` is called (outside the worker's lock) when an upload or reparse is accepted and again when a parse starts.

- [ ] **Step 1: Write the failing test**

Add to `tests/replays/test_replay_worker.py`, which already has the stub parser (`stub` fixture, `STUB` with a `slow` mode that sleeps 3 s) and `vrf_bytes()`:

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

- [ ] **Step 2: Run it to see it fail**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py -k idle -q`
Expected: FAIL, `TypeError: ... unexpected keyword argument 'on_parse'`.

- [ ] **Step 3: Implement**

```python
    def __init__(self, settings: Settings, on_parse=None):
        self.settings = settings
        self.on_parse = on_parse or (lambda: None)   # map control's preempt (D4); called outside self.lock
        self.parsing = False
        ...

    def idle(self) -> bool:
        """No parse running and none waiting: map control may run."""
        return not self.parsing and self.queue.empty() and self.reparse_queue.empty()
```

In `submit` and `submit_reparse`, after the job is queued successfully (after the `put_nowait` that didn't raise), call `self.on_parse()`.

In `_run`, call the hook before processing and clear the flag when the job is settled:

```python
    def _run(self) -> None:
        while True:
            job = self._next()          # sets self.parsing (below)
            self.on_parse()
            status, error, result = "failed", REASON_PARSE, None
            try:
                ...
            finally:
                ...
                (self.queue if job.kind == "upload" else self.reparse_queue).task_done()
                self.parsing = False
```

The flag is set inside `_next`, in the same step that takes the job off its queue, so `idle()` never reads True while a job is between the queue and the parser:

```python
    def _next(self) -> Job:
        if self.archive is None:
            job = self.queue.get()
            self.parsing = True
            return job
        while True:
            for source in (self.queue, self.reparse_queue):
                try:
                    job = source.get_nowait()
                    self.parsing = True
                    return job
                except queue.Empty:
                    pass
            ...
```

The control scheduler's 1 s poll can still race a `submit` that hasn't reached `put_nowait`; `on_parse` from `submit` then kills whatever started (Task 3).

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_worker_control.py -q`
Expected: PASS except the pre-existing `test_control_off_answers_404_and_parsing_is_untouched`.

- [ ] **Step 5: Commit** (`"replay worker: say when it's idle, and call a hook when a parse comes"`)

---

### Task 3: Control runs only while the worker is idle, and a parse kills it

**Files:**
- Modify: `replay_worker/server.py` (`ControlRunner.__init__`, `_next`, `_run`; new `preempt`; `counts`; `main`; module docstring)
- Test: `webapp/tests/replays/test_replay_worker_control.py`

**Interfaces:**
- Consumes: `Worker.idle()`, `Worker(on_parse=...)` from Task 2.
- Produces: `ControlRunner(settings, idle: Callable[[], bool] | None = None)`; `ControlRunner.preempt() -> int` (children killed); `counts()` gains `"preempted": int` (total since start).

- [ ] **Step 1: Write the failing tests**

```python
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
    r, log = runner()
    warm = r.submit(task("w:1:f"))            # warm the map first, so two can run at once
    wait_all(r, [warm])
    a, b = r.submit(task("a:1:f", mode="sleep:20")), r.submit(task("b:1:f", mode="sleep:20"))
    c = r.submit(task("c:1:f"))
    end = time.time() + 10
    while r.counts()["running"] < 2 and time.time() < end:
        time.sleep(0.05)
    busy = {"on": True}
    r.idle = lambda: not busy["on"]
    assert r.preempt() == 2
    assert r.get(a.id).status == "queued" and r.get(b.id).status == "queued"
    assert r.pending[:2] == [a.id, b.id] or r.pending[:2] == [b.id, a.id], "back at the front, ahead of c"
    assert r.get(a.id).error is None and r.counts()["preempted"] == 2
    assert "Ascent" in r.counts()["warm"], "a preempted round leaves its map's warmth alone"
    busy["on"] = False


def test_a_preempted_warming_round_runs_alone_again(runner):
    r, log = runner()
    first = r.submit(task("w:1:f", mode="sleep:20"))
    end = time.time() + 10
    while r.counts()["running"] < 1 and time.time() < end:
        time.sleep(0.05)
    r.idle = lambda: False
    assert r.preempt() == 1
    assert r.get(first.id).status == "queued" and "Ascent" not in r.counts()["warm"]


def test_the_worker_wires_its_parse_hook_to_control(monkeypatch):
    made = {}
    monkeypatch.setattr(server, "make_server", lambda worker, host, port, control: made.update(
        worker=worker, control=control) or type("S", (), {"server_address": ("x", 1), "serve_forever": lambda s: None})())
    monkeypatch.setattr(server.Settings, "from_env", classmethod(lambda cls, env=None: cls(control_cmd=["true"])))
    server.main()
    assert made["worker"].on_parse == made["control"].preempt
    assert made["control"].idle == made["worker"].idle
```

`sleep:20` children are killed by `preempt`, so the tests don't wait 20 s. The `runner` fixture's runners keep their scheduler threads; set `r.idle = lambda: False` at the end of a test if a leftover job would otherwise run into the next.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py -q`
Expected: the four new tests FAIL (`AttributeError: 'ControlRunner' object has no attribute 'preempt'`, and the busy test runs the job).

- [ ] **Step 3: Implement**

`ControlRunner.__init__`:

```python
    def __init__(self, settings: Settings, idle=None):
        self.settings = settings
        self.idle = idle or (lambda: True)    # the parse side has nothing running or waiting (D4)
        self.lock = threading.Lock()
        self.jobs: dict[str, ControlJob] = {}
        self.by_key: dict[str, str] = {}
        self.pending: list[str] = []
        self.running: dict[str, bool] = {}     # job id -> warming
        self.procs: dict[str, subprocess.Popen] = {}
        self.preempted: set[str] = set()       # running jobs a parse has killed; _run puts them back
        self.preempted_total = 0
        self.warm: set[str] = set()
        ...
```

`_next` returns None while busy:

```python
        if len(self.running) >= self.settings.control_workers or not self.idle():
            return None
```

`preempt`:

```python
    def preempt(self) -> int:
        """A parse is coming: kill every running child. Their rounds go back to the front of the queue
        uncounted (D4); the map's warmth is left as it was."""
        with self.lock:
            victims = [(job_id, self.procs.get(job_id)) for job_id in self.running if job_id not in self.preempted]
            self.preempted.update(job_id for job_id, _ in victims)
        for _, process in victims:
            if process is not None:
                kill_tree(process)
        return len(victims)
```

A victim with no process yet (between scheduling and `Popen`) is still marked; `_run` checks the mark before starting the process.

`_run`, with `Popen` so `preempt` can reach the child:

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
        try:
            with self.lock:
                if job.id in self.preempted:
                    raise _Preempted
                process = subprocess.Popen(settings.control_cmd, **kwargs)
                self.procs[job.id] = process
            try:
                stdout, _ = process.communicate(job.task, timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(process)
                process.communicate()
                raise
            if job.id in self.preempted:
                raise _Preempted
            answer = json.loads(stdout or b"{}")
            if answer.get("status") == "ok":
                status, kind, result = "done", None, answer
            else:
                error = answer.get("error") or f"control child exited {process.returncode}"
                kind = answer.get("error_kind") or "infra"
        except _Preempted:
            with self.lock:
                self.procs.pop(job.id, None)
                self.preempted.discard(job.id)
                self.running.pop(job.id, None)
                self.preempted_total += 1
                job.status = "queued"
                self.pending.insert(0, job.id)
            self.wake.set()
            return
        except subprocess.TimeoutExpired:
            error = f"control timed out after {timeout:g} s"
        except (OSError, ValueError) as failure:
            error = f"control child failed: {failure}"
        except Exception:  # noqa: BLE001 - one bad round must not stop the runner
            traceback.print_exc()
            error = "control child failed"
        with self.lock:
            self.procs.pop(job.id, None)
            ... (the existing settle block, unchanged)
```

with, beside `_JobFailed`:

```python
class _Preempted(Exception):
    """A parse killed this control child (D4): the round goes back to the queue, not to failed."""
```

`process.communicate()` after `kill_tree` only reaps the pipes. A preempted child's `communicate` returns early because the process died; its `job.id in self.preempted` check routes it to `_Preempted` whatever it printed. The `ValueError` from `json.loads` on a killed child's partial stdout can't happen first, because the preempted check comes before the parse.

`counts`:

```python
            return {"queued": len(self.pending), "running": len(self.running), "warm": sorted(self.warm),
                    "preempted": self.preempted_total}
```

`main`:

```python
def main() -> None:
    settings = Settings.from_env()
    control = ControlRunner(settings) if settings.control_enabled else None
    worker = Worker(settings, on_parse=control.preempt if control else None)
    if control is not None:
        control.idle = worker.idle
    server = make_server(worker, os.environ.get("REPLAY_WORKER_HOST", "0.0.0.0"),
                         int(os.environ.get("REPLAY_WORKER_PORT", "8080")), control)
    ...
```

`Worker(...)` with the archive on runs `_recover()` in its constructor, which may queue parses before `control.idle` is wired; `ControlRunner` starts with `idle = lambda: True` and an empty queue (the web app hasn't sent anything yet), so nothing can start in that window. Note this in a comment.

Module docstring: replace the "Map control has its own queue and never shares the parse thread" paragraph's priority sentence with: "It runs only while nothing is parsing or waiting to parse (`Worker.idle`), on up to `control_workers` children; a parse arriving kills every running child, and their rounds go back to the front of the queue uncounted (docs/superpowers/plans/2026-10-05-control-idle-queue.md, D4)." Keep `REPLAY_CONTROL_WORKERS`'s line; change its default note to "(default 2: the 2-CPU plan's two cores, used only while no parse is running)".

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_control_isolation.py -q`
Expected: PASS except the pre-existing failure. `test_a_round_past_its_timeout_is_killed_and_is_the_machines_failure` must still pass (timeouts are still `infra` failures).

- [ ] **Step 5: Commit** (`"replay worker: control runs only while idle; a parse kills it and requeues its rounds"`)

---

### Task 4: Rows record their control revision, and the page says it

**Files:**
- Create: `webapp/alembic/versions/0017_control_revision.py`
- Modify: `webapp/app/models/replay.py` (`ReplayRoundControl.revision`), `webapp/app/services/replay_control_store.py`, `webapp/app/services/replay_control.py` (`RoundControlAnswer`, `round_control`), `webapp/app/routers/replays.py` (`replay_round_control`), `webapp/app/templates/replays/replay.html`, `webapp/app/static/js/replay_control.js`, `webapp/app/static/js/replay.js`
- Test: `webapp/tests/replays/test_control_store.py`

**Interfaces:**
- Produces: column `replay_round_control.revision SMALLINT NULL`; `RoundControlAnswer.revision: int | None`, `RoundControlAnswer.stale_reason: str | None` (`"revision"` | `"inputs"` | None); response headers `X-Control-Revision` (the row's, or `"unknown"`), `X-Control-Current-Revision`, and `X-Control-Stale-Reason` when stale; the JS control value gains `revision`, `currentRevision`, `staleReason`.

- [ ] **Step 1: Write the failing tests**

In `test_control_store.py` (`put_row` gains a `revision=cf.CONTROL_REVISION` keyword it passes to the row):

```python
def test_a_stored_row_records_its_control_revision(factory, db, linked):
    [planned] = rc.plan(db, rounds={1})
    store_round(factory, linked.id, 1, planned.fingerprint,
                {"status": "ok", "data": gzip.compress(b"d"), "summary": cf.pack_summary({})})
    db.expire_all()
    assert db.get(ReplayRoundControl, (linked.id, 1)).revision == cf.CONTROL_REVISION


def test_the_endpoint_says_the_rows_revision_and_the_current_one(db, linked):
    put_row(db, linked, 1)
    response = call(db, 1)
    assert response.headers["x-control-revision"] == str(cf.CONTROL_REVISION)
    assert response.headers["x-control-current-revision"] == str(cf.CONTROL_REVISION)
    assert "x-control-stale-reason" not in response.headers


def test_a_row_from_an_older_revision_says_so(db, linked, monkeypatch):
    put_row(db, linked, 1)
    monkeypatch.setattr(cf, "CONTROL_REVISION", cf.CONTROL_REVISION + 1)   # moves the fingerprint too
    response = call(db, 1)
    assert response.headers["x-control-stale"] == "1"
    assert response.headers["x-control-stale-reason"] == "revision"
    assert response.headers["x-control-revision"] == str(cf.CONTROL_REVISION - 1)


def test_a_row_stale_for_another_reason_says_inputs(db, linked):
    put_row(db, linked, 1, fingerprint="0" * 16)
    response = call(db, 1)
    assert response.headers["x-control-stale-reason"] == "inputs"


def test_a_row_from_before_the_column_reads_unknown_and_older(db, linked, monkeypatch):
    put_row(db, linked, 1, revision=None, fingerprint="0" * 16)
    response = call(db, 1)
    assert response.headers["x-control-revision"] == "unknown"
    assert response.headers["x-control-stale-reason"] == "revision"
```

and extend `test_pg_migration_0014_cascades_and_holds_its_checks`'s neighbour pattern with a Postgres check if `pg` is available:

```python
def test_pg_migration_0017_adds_a_nullable_revision(pg):
    columns = {c["name"]: c for c in sa.inspect(pg.get_bind()).get_columns("replay_round_control")}
    assert columns["revision"]["nullable"] is True
```

(Follow whatever `test_pg_migration_0014...` does to get an inspector from the `pg` fixture; it skips without a local Postgres.)

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_control_store.py -q`
Expected: FAIL (`TypeError: 'revision' is an invalid keyword argument`).

- [ ] **Step 3: Implement**

Migration `0017_control_revision.py`:

```python
"""control revision

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-05

`replay_round_control.revision`: the CONTROL_REVISION a row was computed under, so the replay page can say it
(docs/superpowers/plans/2026-10-05-control-idle-queue.md, D6). Nullable: rows from before this read as "an older
revision". Additive: the ValoMaths demo just gets the column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("replay_round_control", sa.Column("revision", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("replay_round_control", "revision")
```

Model, after `data_version`:

```python
    revision: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # control_format.CONTROL_REVISION; migration 0017
```

`store_round`'s merge gains `revision=cf.CONTROL_REVISION,` (both ok and failed rows: a failure is also "computed under" a revision).

`replay_control.py`:

```python
@dataclass
class RoundControlAnswer:
    status: str                              # ok | not_ready | failed | no_map | old_blob
    row: ReplayRoundControl | None = None
    stale: bool = False
    revision: int | None = None              # the row's CONTROL_REVISION (None: written before migration 0017)
    stale_reason: str | None = None          # revision | inputs, when stale
```

`round_control`'s `load_only` adds `ReplayRoundControl.revision`, and its last lines become:

```python
    stale = row.fingerprint != round_fingerprint(replay, side_groups(db, replay), n)
    reason = None
    if stale:
        reason = "revision" if row.revision != cf.CONTROL_REVISION else "inputs"
    return RoundControlAnswer("ok", row, stale, row.revision, reason)
```

Router `replay_round_control`, replacing the last `return`:

```python
    headers = {"X-Control-Revision": str(answer.revision) if answer.revision is not None else "unknown",
               "X-Control-Current-Revision": str(cf.CONTROL_REVISION)}
    if answer.stale:
        headers.update({"X-Control-Stale": "1", "X-Control-Stale-Reason": answer.stale_reason})
    return _stored_gzip(request, answer.row.data, "application/octet-stream", headers)
```

(Import `control_format as cf` in the router if it isn't already.) A 304 carries the same headers, because `_stored_gzip` merges them into both answers.

`replay.html`'s `loadControl`, replacing the `var stale` line and the return:

```js
            var stale = r.headers.get("X-Control-Stale") === "1";
            var meta = { stale: stale, revision: r.headers.get("X-Control-Revision"),
                         currentRevision: r.headers.get("X-Control-Current-Revision"),
                         staleReason: r.headers.get("X-Control-Stale-Reason") };
            return r.arrayBuffer().then(function (buffer) { return Object.assign({ status: "ok", buffer: buffer }, meta); });
```

`replay_control.js`'s `ControlCache.get`, passing the fields through:

```js
        return { status: "ok", stale: !!answer.stale, revision: answer.revision || null,
                 currentRevision: answer.currentRevision || null, staleReason: answer.staleReason || null,
                 parsed: parsed, cursor: new Cursor(parsed) };
```

`replay.js`: add one function near `CONTROL_STATUS` and use it at both stale messages (line ~1613 and ~2069):

```js
  // D6: which revision the round's control was computed under, and why it's out of date.
  function controlRevisionNote(value) {
    var current = value.currentRevision ? "r" + value.currentRevision : "the current revision";
    var mine = value.revision && value.revision !== "unknown" ? "r" + value.revision : "an older revision";
    if (!value.stale) return value.revision && value.revision !== "unknown" ? "Map control " + mine + "." : "";
    if (value.staleReason === "inputs") {
      return "Out of date: computed under " + mine + ", but its inputs changed (a new link, the replay, or the " +
        "map's geometry). Queued for recompute.";
    }
    return "Out of date: computed under " + mine + ", current " + current + ". Queued for recompute.";
  }
```

At ~1613: `? controlRevisionNote(value)` replaces `? (value.stale ? "Computed from older inputs; it will be refreshed." : "")`.

At ~2069 the table reads `rows.stale` from `players.json` (`replay_control_views`), which doesn't carry revisions; leave its text as it is but change the wording to match: `"Out of date for some rounds: queued for recompute."`. Don't add revisions to `players.json` (YAGNI: the per-round note covers debugging).

The fresh-row note ("Map control r5.") shows on every round. If the viewer's status line then never empties, check `setControlStatus("")`'s callers still behave (the `knowing` sentence is appended after it, which is fine).

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/replays/test_control_store.py tests/replays/test_control_views.py tests/replays/test_control_viewer.py tests/replays/test_control_remote.py -q`
Expected: PASS (`test_control_viewer.py` skips without Node).

Then check it in a browser: start the app locally (`CLAUDE.md`, "Running the webapp locally"; `alembic upgrade head` first), open a replay with control, and confirm the note reads `Map control r5.` on a fresh round. Set one row's fingerprint to `'0000000000000000'` in local Postgres and confirm the "inputs changed" note; restore it after.

- [ ] **Step 5: Commit** (`"control: rows record their revision; the replay page says it, or why it's out of date"`)

---

### Task 5: The plan in render.yaml, the old plan doc, and the deploy steps

**Files:**
- Modify: `render.yaml` (the `replay-worker` service's `plan:` and its comment block), `docs/map-control-worker-plan.md` (a superseded note), `.gitignore` (`.claude/worktrees/`)

- [ ] **Step 1: render.yaml**

```yaml
    # 2 CPU / 4 GB ("Pro", Blueprint ID 2c-4g; the user's choice on 2026-10-05). Map control uses both cores,
    # but only while nothing is parsing; a parse kills its children (docs/superpowers/plans/2026-10-05-control-idle-queue.md).
    # Memory: the condense peaked near 1 GB on a 60 MB export, ~3.3 GB scaled to the 200 MB upload cap, before the
    # parser's own (unmeasured) memory. 4 GB is tight for the largest uploads; re-size from Render's measured peak.
    plan: 2c-4g
```

replacing the current six-line comment and `plan: 4c-8g`.

- [ ] **Step 2: docs/map-control-worker-plan.md**

Under the title, add:

```
> **Superseded in part (2026-10-05):** the dispatcher now also sends stale and unlinked rounds, newest match first,
> and the worker runs control only while it isn't parsing (a parse kills it). See
> docs/superpowers/plans/2026-10-05-control-idle-queue.md. Decision 5 and S1 below are no longer in force.
```

- [ ] **Step 3: .gitignore** — add `.claude/worktrees/` under the existing `.worktrees/` line.

- [ ] **Step 4: Deploy steps (the user's), appended to this plan's end as written below; don't run them.**

- [ ] **Step 5: Run the whole replay suite**

Run: `PY -m pytest tests/replays -q`
Expected: PASS except the pre-existing worker failure (and Node/Postgres skips).

- [ ] **Step 6: Commit** (`"render: the replay worker on 2c-4g; mark the old plan's decisions superseded"`)

---

## Deploy steps (the user's)

1. Merge. Both web services redeploy and run `alembic upgrade head` (0017, additive). The worker image rebuilds from `replay_worker/Dockerfile`.
2. In the dashboard, confirm `replay-worker` shows **2c-4g** (the Blueprint sync applies `render.yaml`'s plan).
3. Set `REPLAY_CONTROL_REMOTE=true` on `valowithfriendstracker` (uncomment it in `render.yaml` in a follow-up commit, or set it in the dashboard). Never on `valomaths`.
4. Watch the web service's log for `map control dispatch: sent N ...` lines and the worker's `GET /health` → `control.queued/running/preempted`.
5. At about 45 CPU-seconds a round on two cores, the current corpus (~460 rounds) takes about 3 hours of idle time. Spot-check a replay page: fresh rounds say `Map control r5.`

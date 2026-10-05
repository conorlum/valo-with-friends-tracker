# Map control for uploads on the replay worker: plan

> **Superseded in part (2026-10-05):** the dispatcher now also sends stale and unlinked rounds, newest match first,
> and the worker runs control only while it isn't parsing (a parse kills it). See
> docs/superpowers/plans/2026-10-05-control-idle-queue.md. Decisions 1 and 5, S1 and the "keep the 8 GB plan's
> headroom" memory note below are no longer in force.

The follow-up agreed in `docs/replay-map-control-plan.md` (Stage 3 settled list, last bullet): "uploads get
control on Render by reusing the replay worker (4 CPU / 8 GB, idle between parses, credential-free): the web
app queues rounds, the worker returns bytes, uploads keep priority. Full recomputes after a revision bump stay
local." Register R3.1 (AFK run 2026-09-30): code only, on a branch; deploying, env changes and any prod
migration are the user's.

## Goal

A friend uploads a replay; within minutes its rounds have map control on the site, with nobody running
`scripts/compute_control.py`. Nothing about uploads gets slower or less reliable.

**In scope:** the worker computes one round's control on request; the web app finds rounds with no control,
sends them, collects the bytes and stores them; tests; the deploy steps written down for the user.
**Out of scope:** recomputing stale rows (a revision bump, new geometry, a new link): those stay local, as
settled. Any change to the engine, the formats or `CONTROL_REVISION`. New paid resources (none needed).

## Architecture

```
web app (has DB)                                   replay worker (no DB, no secrets)
  dispatcher thread, every 20 s                      POST /control   {map, blob, link} -> 202 {id}
    1. collect: GET /control/{id} for in-flight  <-  GET  /control/{id} -> queued|running|done|failed (+bytes)
       done -> store the row (fingerprint planned)    ControlRunner: its own queue, up to N child
    2. plan(): rounds "missing" (never computed),       processes, each `python -m replay_worker.control_job`
       not in flight -> POST each (blob + link)         (niced, time-capped, memory-capped), which runs
                                                        the engine and prints the row's bytes
```

- **The worker.** A second, independent queue beside the parse queue. The parse path is untouched: its thread,
  its queue and its limits don't change, and the server process never imports numpy.
  - Each round runs in a **child process** (`REPLAY_CONTROL_CMD`, default the venv's
    `python -m replay_worker.control_job`): task JSON on stdin, result JSON on stdout. The child imports the
    engine (`app.control`), loads the map's geometry and its visibility bitsets (built on first use into
    `CONTROL_CACHE_DIR`), and returns `data` and `summary` exactly as `compute_control.compute_task` makes them
    (it reuses that function).
  - **Uploads keep priority** by never sharing a core: control children are capped at `cores - 2` (2 on the
    4-CPU plan) and run at `nice 10`, so a parse always has its own core and the scheduler favours it. A child is
    killed after `REPLAY_CONTROL_TIMEOUT_S` (default 900 s; an average round is ~45 s of CPU, Abyss about twice
    that) and capped at `REPLAY_CONTROL_MEMORY_MB` (default 2048; a round peaks ~0.2 GB).
  - Memory: a parse peaks near 3 GB at the upload cap (render.yaml's note); two control children at ~0.2-0.5 GB
    each keep the 8 GB plan's headroom.
  - `GET /health` reports control's queue and whether it is enabled. With `REPLAY_CONTROL=0` or no engine
    (numpy missing), `POST /control` answers 404 and nothing else changes.
- **The worker image** (`replay_worker/Dockerfile`) gets a venv at `/opt/control-venv` with numpy, scipy and
  Pillow pinned to `webapp/requirements.txt`'s versions, and `webapp/app/control` copied in. The server keeps
  running on the system `python3` with no numpy (the isolation test stays: `replay_worker/*.py` and
  `app/replays` import nothing heavy; `control_job.py` imports the engine only inside its functions, and it's
  run only by the venv's interpreter).
- **The web app's dispatcher** (`app/services/replay_control_remote.py`, stdlib and the DB):
  - Off unless `REPLAY_CONTROL_REMOTE=1` and `REPLAY_WORKER_URL` is set, and never in demo mode.
  - A daemon thread started in the app's lifespan. Each cycle takes a Postgres advisory lock (`pg_try_advisory_lock`)
    so only one instance dispatches (Render overlaps the old and new instance during a deploy), then:
    1. **collect** each in-flight job: `done` -> store the row with the fingerprint it was planned with (the same
       merge as `compute_control.store_result`), but only if the result's `CONTROL_REVISION` and `DATA_VERSION`
       match the web app's (a worker on another deploy is dropped and the round asked again); `failed` -> stored
       `failed` with the error, like a local failure (skipped until `--retry-failed` or new inputs); the worker
       answering 404 (it restarted and forgot the job) or a job older than 30 min -> dropped, asked again.
    2. **submit** rounds `plan()` lists as `missing` (never computed) and not in flight, newest replay first, up
       to `REMOTE_IN_FLIGHT` (8) at a time; `503` (the worker's control queue is full) stops this cycle.
  - In-flight jobs live in memory only: a restart forgets them and the rounds are asked again. No new table and
    no migration.
- **Which rounds:** `missing` only, from any source (an upload, or a local ingest nobody has computed yet).
  `stale` rounds (a revision bump, geometry or link change) stay for the local command, as settled.

## Decisions

| # | Question | Choice | Why |
|---|---|---|---|
| 1 | How uploads keep priority | Separate queue; control children capped at cores-2 and niced; parse never waits | No preemption logic; a parse always has a core |
| 2 | Where control runs on the worker | A child process per round, run by a separate venv interpreter | The server stays stdlib-only; a crash or OOM kills one round, not the worker |
| 3 | The queue | Implicit: the dispatcher asks `plan()` for `missing` rounds each cycle; in-flight kept in memory | No migration; a lost job is just asked again |
| 4 | Who drives | A web-app background thread with an advisory lock | The worker has no DB; the web app is always on |
| 5 | Which rounds | `missing` only, any source | "Full recomputes stay local"; a new local ingest benefits too |
| 6 | Visibility bitsets on the worker | Built on first use per map into `/jobs/control_cache` (lost on redeploy) | No Render build-time risk; the first round of a map after a deploy waits a few minutes |
| 7 | Revision skew between services | Result carries CONTROL_REVISION and DATA_VERSION; a mismatch is dropped | The two services deploy separately |
| 8 | Failures | Engine errors stored `failed` (like local); infrastructure errors (timeout, kill, lost job) retried by asking again | Matches the local command's rule |
| 9 | Off switch | `REPLAY_CONTROL_REMOTE` on the web app (default off); `REPLAY_CONTROL=0` on the worker | Nothing changes on deploy until the user turns it on |

## Risks

- **No Docker here:** the Dockerfile change can't be built locally (as in Stage 3); the first build is Render's.
  The child command is tested locally with the dev venv instead.
- **Numpy on the worker's Python:** the runtime image's `python3` version decides which wheels install; the venv
  step pins versions that have wheels for 3.11-3.13.
- **Duplicate work across a deploy:** in-flight jobs are forgotten on restart; at worst a round is computed twice.
- **Engine differences from local:** the same code and pins; control is display-only, so a last-digit
  difference in a float wouldn't matter anyway.

## Revised after the P2 review (2026-09-30)

A fresh reviewer found 3 blockers and 7 should-fixes; all applied except N2 (rejected, below). These override the
sections above where they differ.

- **B1 One visibility build per map.** The runner runs a map's first round alone ("warming"), with its own long
  timeout (`REPLAY_CONTROL_WARM_TIMEOUT_S`, 1800 s), before any other child of that map starts. A child whose cache
  load fails deletes the cache file and reports an infrastructure error (retried).
- **B2 A fixed pool.** `REPLAY_CONTROL_WORKERS` (default 2), never from `os.cpu_count()` (a container reports the
  host's cores). Children run with `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and `MKL_NUM_THREADS` = 1.
- **B3 The image.** Install `python3-venv`; `pip install --only-binary=:all:` with the requirements.txt pins, so a
  missing wheel fails the build; a smoke step `RUN /opt/control-venv/bin/python -c "import numpy, scipy, PIL,
  app.control.engine, app.control.task"`. `compute_task` moves to `app/control/task.py` (the local command and the
  child both import it); `webapp/app/control` is copied into the image.
- **S1 Unlinked uploads (tier 2, a card).** Control needs each slot's side from the link; an upload is often
  stored unlinked and linked by the next crawl, which would make a just-computed row stale (and stale is
  local-only). The dispatcher takes **linked replays only**; an upload's rounds are sent once the crawl links it
  (they are still `missing` then).
- **S2 Geometry skew.** The child returns the geometry it used (sight and walk mask hashes, specials, scale); the
  dispatcher drops a result unless they equal the web app's `geometry_inputs(map)`.
- **S3 The lock.** `pg_try_advisory_xact_lock` inside each cycle's transaction, skipped on SQLite; duplicates across
  instances are made harmless by S5's dedupe anyway.
- **S4 Failure kinds.** The child reports `error_kind`: `engine` (a `ControlError` or format error: stored
  `failed`, like local) or `infra` (anything else: memory, missing assets, cache, import: retried, up to 3 times
  per round and fingerprint, with backoff, then left for the local command).
- **S5 Dedupe and sizing.** Each task carries a key (replay id, round, fingerprint); the worker returns the existing
  job for a key it already has. The worker's control queue (`REPLAY_CONTROL_QUEUE`, 32) is larger than the
  dispatcher's in-flight limit (8).
- **S6 Store checks.** At collect time, under the replay's advisory lock, the round's fingerprint is recomputed;
  the result is stored only if it still matches the planned one and no row with that fingerprint exists. The store
  function moves to `app/services/replay_control_store.py` (the local command uses it too).
- **S7 Tests without Docker, and a way to check.** One dispatcher cycle is a plain function tested on SQLite with a
  fake worker client; the runner is tested with a stub child command; the isolation test also checks that
  importing `replay_worker.server` loads no numpy and no `app.control`. The dispatcher logs one line per cycle that
  did something (sent, stored, dropped and why).
- **N1 Cheap cycles.** A cycle first counts rounds of linked replays with no control row (one query); `plan()` runs
  only when that is above zero. Docstrings that say rows are written only by `compute_control.py` are updated.
- **N2 rejected:** rebasing the runtime image on `python:3.13-slim` with a self-contained parser would unify the
  Pythons, but it rebuilds the parse path's image, which the settled "uploads keep priority" (and reliability)
  argues against changing in the same step. It can be its own change later.

## Implementation steps (P3)

| Step | Files | Check |
|---|---|---|
| 1. Move `compute_task` to `app/control/task.py`; `compute_control.py` imports it; the task reports `error_kind` and the geometry it used | `app/control/task.py`, `scripts/compute_control.py`, tests | `pytest tests/replays/test_control_store.py tests/replays/test_control_task.py` passes; the local command's behaviour is unchanged |
| 2. Move `store_result` to `app/services/replay_control_store.py` with the S6 checks | that file, `scripts/compute_control.py`, tests | store tests: a matching result is stored; a changed fingerprint or an existing current row is not |
| 3. The worker: `ControlRunner` (queue, dedupe, warm-up per map, fixed pool, timeouts, nice, env), `POST /control`, `GET /control/{id}`, `/health`; `replay_worker/control_job.py` (stdin task -> stdout result, run by the venv) | `replay_worker/server.py`, `replay_worker/control_job.py`, `tests/replays/test_replay_worker.py` | stub-command tests: dedupe, the pool never exceeds its size, warm-up runs alone, timeout kills, disabled -> 404; `control_job` in-process on a toy map returns the same bytes as `compute_task` |
| 4. The dispatcher: `cycle()` (collect, check, store, submit) and the lifespan thread | `app/services/replay_control_remote.py`, `app/main.py`, `app/config.py` | SQLite tests with a fake client: submit linked missing rounds only; store; drop on revision or geometry skew; 404 re-ask; 503 stops; engine vs infra failures; off by default and in demo mode |
| 5. The image and config | `replay_worker/Dockerfile`, `render.yaml` (a commented, off-by-default env var), docs | `docker` isn't available here: the Dockerfile is reviewed by reading; the isolation test covers the server |
| 6. Isolation | `tests/replays/test_control_isolation.py` | importing `replay_worker.server` loads no numpy or `app.control`; the web app still doesn't import the engine |

### After the re-check (P2 re-check + P4, no blockers)

- **A map goes cold again** after any infra failure of one of its rounds, or when its warming child ends with no
  cache file: the next round of that map warms alone (stub-command test in step 3).
- **Paths in the image:** `ENV PYTHONPATH=/srv/webapp` for the smoke `RUN` and the child command
  (`REPLAY_CONTROL_CMD=["/opt/control-venv/bin/python","-m","replay_worker.control_job"]`), and
  `ENV CONTROL_CACHE_DIR=/jobs/control_cache` (`/jobs` is the worker user's; `/srv/webapp` isn't writable).
- **Only numpy, scipy and Pillow** go into the venv, at requirements.txt's versions (not the whole file).
- **Step 5's checks:** a test that reads the Dockerfile (the three pins equal requirements.txt's,
  `--only-binary=:all:`, `COPY webapp/app/control`, the smoke `RUN`, the ENV lines); and, when the network allows,
  `pip download --only-binary=:all:` of the three pins for Linux x86-64 on Python 3.11, 3.12 and 3.13 (the base
  image's `python3` is unverified here; Ubuntu 24.04's would be 3.12).
- **Isolation after steps 1, 2 and 4**, not only 6; the web app's new modules never import `app.control.task`.
- **A last step:** the full suite on `.venv313`, failure set compared by name with the base.
- The memory cap counts address space: a hit is an infra failure (retried up to 3 times), and the in-process
  control_job test also runs under the cap on Linux (skipped on Windows).

### What step 5 found

`pip download --only-binary=:all:` for Linux x86-64: numpy 2.4.6, scipy 1.18.1 and Pillow 10.4.0 all have wheels
for Python 3.12 and 3.13, but **scipy 1.18.1 has none for 3.11**. So the build works if the .NET 10 runtime image's
`python3` is 3.12 or newer (Ubuntu 24.04's is 3.12) and fails at the pip step, loudly, if it is 3.11 (Debian 12).
If that happens, pin scipy for the worker only to 1.17.1 (the newest with 3.11 wheels): control is display-only.

## User-only steps (tier 3)

1. Merge; Render builds the new worker image (its first with numpy, scipy and Pillow). Check its build log for the
   smoke import line.
2. Optional first: `compute_control.py --dry-run` (read-only) shows how many rounds are missing, since turning the
   dispatcher on sends every missing round of a linked replay, not only new uploads.
3. Set `REPLAY_CONTROL_REMOTE=1` on the `valowithfriendstracker` web service.
4. Upload a replay on a map with the layer (not a `no_map` map); once it is linked, its rounds fill in
   (`control.bin` goes 202 -> 200). The web service log shows the dispatcher's lines.

# Replay work on Render Workflows (design)

Status: approved in conversation 2026-10-14; revised the same day after the pre-build review
(`docs/superpowers/plans/2026-10-14-render-workflows-review.md`; the owner's two calls are D9 and D14). Plans per step
under `docs/superpowers/plans/2026-10-14-render-workflows-step*.md`.

Background: `replay_worker/README.md`, `docs/map-control-worker-plan.md`,
`docs/superpowers/specs/2026-10-01-control-heights-design.md`, `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`,
Render's docs: https://render.com/docs/workflows (and `-defining`, `-running`, `-limits`, `-sdk-python`). The REST
paths were read from the `render==1.0.1` wheel and confirmed by the review.

## What the owner asked for (2026-10-14)

| # | Request |
| --- | --- |
| O1 | Move upload parsing, map control and height builds to Render Workflows, for speed. |
| O2 | The number of map-control runs is tunable. For now: one match at a time, and at most 40 runs. |
| O3 | Keep the replay worker's disk rather than adding object storage (no outside account, no early-access product). |
| O4 | Up to 15 parses at once; one uploader may have 10 in progress. |
| O5 | A map's height build starts as soon as it is due (no waiting for the worker to be idle); the limit is how many builds run at once. |

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **The worker hands its work to Workflows** (approach A). `replay-worker` keeps its HTTP API, the `.vrf` archive, the re-parse receipts and the job records. Where it starts a child process today, it starts a Workflow run instead. The site's upload, re-parse and control code talk to the worker as now. | The re-parse protocol already lives on the worker's disk; the site needs no Render API key; the smallest change. |
| D2 | **A per-kind runner switch**: `REPLAY_PARSE_RUNNER`, `REPLAY_CONTROL_RUNNER`, `REPLAY_HEIGHTS_RUNNER`, each `local` (default, today's child processes) or `workflow`. They are dashboard-managed (`sync: false` in `render.yaml`), so a Blueprint sync never resets a switch the owner flipped. | Each kind moves alone, and rollback is one env var. |
| D3 | **Runs fetch inputs from, and post results to, the worker** over Render's private network. Nothing large passes through Workflows' arguments (4 MB cap). | O3; a 200 MB `.vrf` cannot be a task argument, and task runs have no disk. |
| D4 | **Results are pushed**, guarded by a one-time token per run; the first valid result wins. Once a minute the worker asks Render about its open runs: one list call covering every task kind, following its pages up to a small cap, then a lookup per open run still not found. | Render's own retries or a leftover run can't overwrite a newer answer; few API calls. |
| D5 | **Map control limits on the site**: `CONTROL_MAX_MATCHES` (1) and `CONTROL_MAX_RUNS` (40) replace `IN_FLIGHT = 8`; rounds go out until either limit is reached. "Match" means replay (`replay_id`). On the worker, `REPLAY_CONTROL_RUNS` (40) is how many control runs it keeps going; `REPLAY_CONTROL_WORKERS` stays the local pool (2). | O2. |
| D6 | **Map control's caches live on the worker's disk** (`<archive>/control_cache`). A map's first round still runs alone ("warming"); its run posts back every cache file it created **or changed** (feature-row caches are rewritten in place) **before** its result; later runs download them. A map counts as warm only once its visibility cache is on the worker's disk. The newest 3 per map and folder are kept, and a file that would take the cache past `RESERVE` (2 GiB, taken out of the archive's budget) is refused. A run that can't fetch a cache file skips it and rebuilds. Cache files are named by their content's identity, so one arriving after its run settled is still correct and is kept. | A run starts on a blank machine; without this every run rebuilds a 10-15 MB cache that can take up to 30 minutes. The caches also survive deploys. |
| D7 | **Machine size: every task on `flex`** (up to 1 CPU, up to 4 GB). Control and height children are single-threaded and capped at 2 GB today. Parse is compared against today's `parse_seconds` after the switch and moved to `2c-4g` only if clearly slower. | The owner's choice. |
| D8 | **Timeouts**: the parser keeps its own 240 s (`REPLAY_TIMEOUT_S`) inside the run; a parse **run** (download, parse, condense, post) has one deadline per job, `REPLAY_PARSE_RUN_TIMEOUT_S` = 1200 s (the site's 20-minute stuck limit), saved in the job record and kept across deploy refusals and restarts; the `parse` task's `timeout_seconds` is 1260. Control 1800 s and heights 3600 s, as today. | Same limits and failure reasons; a refused or restarted parse never gets a fresh 20 minutes. |
| D9 | **Retries and restarts**: Render retries parse 0, control 1, heights 0. A worker restart follows **today's rule** (the owner's call, 2026-10-14): a parse job that was running starts over, except that the worker saves the run's token and deadline before starting it and its Render id as soon as it has one, and reconnects to that run when the id was saved. A rare second execution of the parser costs compute only: the token makes the first result win. The re-parse protocol's guarantee is unchanged: one job per attempt id. | Today a restart already re-runs the parse; a durable start/result state machine buys nothing this site needs. |
| D10 | **Starts are not throttled in practice**: `REPLAY_TASK_STARTS_PER_MIN` defaults to 1000, a safety valve only, and never makes a run start past its deadline. A 429 still means wait and start again; it is never a failure. | The owner's workspace tier allows far more root-level starts than this site makes. |
| D11 | **Deploy skew**: the worker passes its parser recipe to every parse run (control results already carry revisions the site checks). A parse run whose image has another recipe refuses with `error_kind: "deploy"`; the worker waits 60 s and starts a new run, within the job's one deadline (D8). | The worker and the workflow deploy separately. |
| D12 | **Runs never touch the database.** The workflow service gets only the worker's address. | The worker was designed with no DB credentials; keep it. |
| D13 | **The worker calls Render's REST API with the standard library** (`urllib`), like the rest of `server.py`. The workflow image uses the `render` SDK (`render==1.0.1`) for task definitions only. | `server.py` is standard library only. |
| D14 | **Step 5 keeps the local code** (the owner's call, 2026-10-14): the local child paths stay as the test and development mode and the rollback path. Step 5 takes the .NET parser out of the worker's image (production must then parse on Workflows; the worker refuses `REPLAY_PARSE_RUNNER=local` without a parser), makes `POST /jobs` stream to disk, and moves the worker to 1 CPU / 2 GB. The control venv stays for feature verification (`verify_feature_upload`) and the local control path. | The local code is what ~94 worker tests exercise; removing it is the riskiest, least valuable part. |
| D15 | **Parses: 15 at once** (`REPLAY_PARSE_PARALLEL=15`). The worker's queue (`REPLAY_QUEUE_SIZE=15`) is a waiting line on top of the running ones, so up to 15 run and 15 wait. Jobs recovered after a restart go to a recovery list worked first, never to the bounded queue, so a restart never fails an accepted job. The site's per-uploader batch (`MAX_UNFINISHED`) goes from 5 to 10. | O4; the owner's choice, 2026-10-14. |
| D16 | **Height builds: the unit is one map** (all of that map's stored rounds → its heights). On the site, `HeightState.build` becomes `builds` (one per map, keyed by input manifest), and `HEIGHTS_MAX_BUILDS` (4) caps how many are open at once; a due map starts as soon as there is room, and a build looked at in a cycle is never reopened in that same cycle. On the worker, `HEIGHT_COLLECTING_MAX` becomes `REPLAY_HEIGHT_BUILDS_MAX` (4), counting every unfinished build (collecting, queued or running); the spool goes to `REPLAY_HEIGHT_SPOOL_DIR` on the disk (`/var/replay/height_builds`), capped by `REPLAY_HEIGHT_SPOOL_BYTES` (2 GiB), and twice that (the spool and its packed tars) is reserved. With builds on Workflows, `/health`'s `idle` reads true, so nothing waits for parses. | O5. A map is due only after 5 new matches, so usually 0-1 are due; a rules or masks change makes every map due at once, and the cap keeps that from opening a dozen spools. |
| D17 | **Preemption follows where parsing runs**: a local parse still preempts local children (a local control child beyond the first, and any local height build); it never touches a Workflow run. A Workflow parse preempts nothing. | Mixed switches during the migration (e.g. control on Workflows, heights local) must keep today's protection for what is still local. |
| D18 | **Memory**: every transfer streams in 1 MB chunks. A parse result is decoded in memory once, as the worker does today; its size is capped at 256 MB (`run_result_max_bytes`) and the real sizes are measured in step 3 before step 5 shrinks the worker. | Holding results is today's behaviour; a file-handoff redesign isn't needed at this scale. |

## How the pieces fit

```
site ──HTTP (unchanged)──> replay-worker (disk: archive, jobs, control_cache, height_builds)
                               │  start run (REST, token, ids)        ▲
                               ▼                                      │ GET inputs / POST caches, then result
                           Render Workflow "replay-tasks" ── run ─────┘   (private network)
                           tasks: parse, control, heights (one machine per run)
```

### The workflow service `replay-tasks`

- Docker, created by hand in the dashboard, same region as `replay-worker`, auto-deploy from `main`.
- Image: through step 4 the worker's own `replay_worker/Dockerfile` with another start command; from step 5a its own
  `replay_tasks/Dockerfile` (the pinned parser, the control venv with `render`, the app modules).
- Env: `REPLAY_WORKER_URL` (the worker's private `host:port`, set by hand), and the parser/control settings the image sets.
- Three tasks, each a thin wrapper around today's child, unchanged:

| Task | Input (arguments) | What it runs | Output |
| --- | --- | --- | --- |
| `parse` | `{job_id, token, recipe, sha256}` | `GET /runs/{job}/input/vrf` → `CliReader export` → `condense_export_dir` (`replay_worker/parse_job.py`) | `POST /runs/{job}/result` with today's result dict |
| `control` | `{job_id, token, map, files}` | `GET` the task body and each named cached file → `replay_worker.control_job` | `POST /runs/{job}/cache/{name}` for each new or changed cache file, **then** `POST /runs/{job}/result` |
| `heights` | `{job_id, token, map, files}` | `GET` the task, the spooled rounds as one tar and the cached files → `replay_worker.height_job` | as `control` |

- A refusal or a crash posts `{"status": "failed", "error", "error_kind"}` when it can; when it can't (killed,
  timed out), the worker learns from reconciliation (D4).

### The worker

- **Runner**: at each place a child starts today (`Worker._process`, `ControlRunner._run`), a branch on the
  kind's switch: `local` keeps today's code; `workflow` opens a run in the run table (`replay_worker/runs.py`: job
  id, token, Render run id, deadline), registers its input files, starts it and waits for the result route to settle it.
- **Run routes** (private, like every route there): `GET /runs/{job}/input/{name}`, `POST /runs/{job}/cache/{name}`,
  `POST /runs/{job}/result`, all with header `X-Run-Token`; `POST /runs/ping`. A result with a wrong token, for an
  unknown run or for a run already settled is refused (`409`) and changes nothing. A body's `Content-Length` must
  be a positive number within the route's cap, or the request is refused before anything is written.
- **Load**: in workflow mode the worker only serves files and keeps records. It stays on `2c-4g` through step 4
  (the `local` rollback needs it) and moves to 1 CPU / 2 GB in step 5, after the measurements of D18 and Render's
  memory graph at the configured concurrency.
- **Restart** (D9): parse jobs reload from `job.json`; one with a saved Render id reconnects, one without starts
  over with its saved token and deadline. Recovered jobs never go through the bounded queue (D15). Control and
  height runs are in memory as today: on restart they are forgotten and the site asks again; a late result for a
  forgotten run is refused by its token.
- **Health**: `/health` gains `runners: {parse, control, heights}` and `workflow: {starts_per_min, open,
  last_reconcile, last_error}`.

### The site

- `replay_control_remote.py`: `IN_FLIGHT` → `settings.control_max_runs` (40) and `settings.control_max_matches`
  (1), read in `_send` and `_submit`.
- `replay_heights_remote.py`: several builds, one per map, at most `settings.heights_max_builds` (D16).
- `replay_upload.py`: `MAX_UNFINISHED = 10`.
- `render.yaml`: the site's limits; on `replay-worker` the runner switches (`sync: false`), `RENDER_API_KEY`
  (`sync: false`), `REPLAY_WORKFLOW_SLUG`, `REPLAY_CONTROL_RUNS`, `REPLAY_CONTROL_QUEUE` ≥ 40, `CONTROL_CACHE_DIR`,
  `REPLAY_PARSE_PARALLEL`, `REPLAY_QUEUE_SIZE`, the height settings.

## Steps

| Step | What | Done when |
| --- | --- | --- |
| 1 | **Foundation.** The `replay-tasks` entry point with a `ping` task; the run table, the run routes, the Render REST client, start pacing, reconciliation, settings, health. Every switch stays `local`. | Deployed; `ping` round-trips worker → run → worker on Render; nothing else changed. |
| 2 | **Map control on Workflows.** Control runs; caches on the disk; preemption by parse locality (D17); the site's two limits. | `REPLAY_CONTROL_RUNNER=workflow` on Render; one selected round compares byte-identical with a local computation; the switch back to `local` still works. |
| 3 | **Parsing on Workflows.** Parse runs, up to 15 at once (D15), restart rule (D9), one deadline (D8), deploy refusal (D11); 10 uploads per uploader. | `REPLAY_PARSE_RUNNER=workflow`; many uploads parse in parallel; a restart mid-parse reconnects; `parse_seconds` and result sizes recorded (D7, D18). |
| 4 | **Height builds on Workflows.** The spool on the disk and served as a tar; build runs; several maps at once (D16). | `REPLAY_HEIGHTS_RUNNER=workflow`; rebuilds of two maps run together and both are accepted by the site's checks. |
| 5 | **Slim the worker** (two PRs: 5a gives the workflow its own `replay_tasks/Dockerfile`; 5b the rest). The .NET parser leaves the worker's image; `POST /jobs` streams to disk; 1 CPU / 2 GB plan. Local code stays (D14). | All three switches `workflow`; the worker image has no parser; one upload parses and a match's control runs on the smaller plan; memory measured with 15 parses. |

Each step is its own PR and its own plan. Steps 2-4 merge with their switch `local`; the owner flips it on Render.

## Testing

- Worker: tests of the workflow path against a fake Render API (start, result push, wrong and repeated tokens,
  reconciliation across pages, deadline cancel, 429 waits within the deadline, restart with planted records,
  recovery beyond the queue size). The existing local-mode tests stay green unchanged.
- Tasks: each wrapper run in-process against a fake worker link.
- Site: `_send` respects both control limits; height builds for two maps at once, the cap, one map's failure, and
  no reopening in the same cycle.
- On Render: one kind at a time, by flipping its switch; compare outputs and times with the `local` runner.

## Out of scope

- Object storage; the site calling Render; giving runs database access.
- A durable start/result state machine for parses (D9).
- Removing the local code paths (D14); moving feature verification to a task.
- Changing what control, parsing or heights compute.

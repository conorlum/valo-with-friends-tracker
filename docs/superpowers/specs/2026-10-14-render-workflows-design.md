# Replay work on Render Workflows (design)

Status: approved in conversation 2026-10-14; plans per step under `docs/superpowers/plans/2026-10-14-render-workflows-step*.md`.

Background: `replay_worker/README.md`, `docs/map-control-worker-plan.md`,
`docs/superpowers/specs/2026-10-01-control-heights-design.md`, `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`,
Render's docs: https://render.com/docs/workflows (and `-defining`, `-running`, `-limits`, `-sdk-python`).

## What the owner asked for (2026-10-14)

| # | Request |
| --- | --- |
| O1 | Move upload parsing, map control and height builds to Render Workflows, for speed. |
| O2 | The number of map-control runs is tunable. For now: one match at a time, and at most 40 runs. |
| O3 | Keep the replay worker's disk rather than adding object storage (no outside account, no early-access product). |
| O4 | Up to 15 parses at once. |
| O5 | A map's height build starts as soon as it is due (no waiting for the worker to be idle); the limit is how many builds run at once. |

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **The worker hands its work to Workflows** (approach A). `replay-worker` keeps its HTTP API, the `.vrf` archive, the re-parse receipts and the job records. Where it starts a child process today, it starts a Workflow run instead. The site's upload, re-parse and control code talk to the worker as now. | The exactly-once re-parse protocol already lives on the worker's disk; the site needs no Render API key; the smallest change. |
| D2 | **A per-kind runner switch**: `REPLAY_PARSE_RUNNER`, `REPLAY_CONTROL_RUNNER`, `REPLAY_HEIGHTS_RUNNER`, each `local` (default, today's child processes) or `workflow`. | Each kind moves alone, and rollback is one env var until step 5. |
| D3 | **Runs fetch inputs from, and post results to, the worker** over Render's private network. Nothing large passes through Workflows' arguments (4 MB cap). | O3; a 200 MB `.vrf` cannot be a task argument, and task runs have no disk. |
| D4 | **Results are pushed**, guarded by a one-time token per run; the first valid result wins. The worker reconciles with Render once a minute (one list call), never per run. | Render's own retries or a run left over from a restart can't overwrite a newer answer; few API calls. |
| D5 | **Map control limits on the site**: `CONTROL_MAX_MATCHES` (1) and `CONTROL_MAX_RUNS` (40) replace `IN_FLIGHT = 8`; rounds go out until either limit is reached. "Match" means replay (`replay_id`). | O2. |
| D6 | **Visibility caches live on the worker's disk** (`<archive>/control_cache`). A map's first round still runs alone ("warming"); its run posts the new cache file back; every later run downloads it. The newest 3 per map are kept, inside a fixed `CONTROL_CACHE_RESERVE` (2 GB) taken out of the archive's space budget. | A run starts on a blank machine; without this every run rebuilds a 10-15 MB cache that can take up to 30 minutes. The caches also survive deploys. |
| D7 | **Machine size: every task on `flex`** (up to 1 CPU, up to 4 GB). Control and height children are single-threaded and capped at 2 GB today. Parse is compared against today's `parse_seconds` after the switch and moved to `2c-4g` only if clearly slower. | The owner's choice; nothing here uses a second core that we know of. |
| D8 | **Timeouts as today**: parse 240 s, control 1800 s, heights 3600 s (`timeout_seconds` on each task; the worker keeps its own deadline too). | Same limits, same failure reasons. |
| D9 | **Retries**: parse 0 (the worker decides), control 1, heights 0 (the site resends builds). | A parse must never run twice for one attempt id. |
| D10 | **The start rate is limited** by `REPLAY_TASK_STARTS_PER_MIN` (40). A 429 means wait and start again; it is never a failure. | Render limits root-level run starts per workspace (50/min on Hobby). |
| D11 | **Deploy skew**: the worker passes its parser recipe to every parse run (control results already carry revisions the site checks). A parse run whose image has another recipe refuses with `error_kind: "deploy"`; the worker puts the job back and tries again after 60 s, within the job's existing stuck deadline. | The worker and the workflow deploy separately. |
| D12 | **Runs never touch the database.** The workflow service gets only the worker's address. | The worker was designed with no DB credentials; keep it. |
| D13 | **The worker calls Render's REST API with the standard library** (`urllib`), like the rest of `server.py`. The workflow image uses the `render` SDK (`render>=1.0.1`) for task definitions only. | `server.py` is standard library only. |
| D15 | **Parses: 15 at once** (`REPLAY_PARSE_PARALLEL=15`, and the worker's queue `REPLAY_QUEUE_SIZE=15` so it admits that many). The site's per-uploader batch (`MAX_UNFINISHED`, 5) is unchanged: 15 is reached by several friends uploading together. | O4. |
| D16 | **Height builds: the unit is one map** (all of that map's stored rounds → its heights). On the site, `HeightState.build` becomes `builds` (one per map, keyed by input manifest), and `HEIGHTS_MAX_BUILDS` (4) caps how many are open at once; a due map starts as soon as there is room. On the worker, `HEIGHT_COLLECTING_MAX` becomes `REPLAY_HEIGHT_BUILDS_MAX` (4) and the spool cap `REPLAY_HEIGHT_SPOOL_BYTES` (2 GiB), with the spool on the disk inside the reserve. With builds on Workflows, `/health`'s `idle` reads true, so nothing waits for parses. | O5. A map is due only after 5 new matches, so usually 0-1 are due; a rules or masks change makes every map due at once, and the cap keeps that from opening a dozen spools. |
| D14 | **Step 5 slims the worker** but keeps the control venv for feature verification (`verify_feature_upload`, a short synchronous child). Moving that to a task is out of scope. | Keep step 5 small. |

## How the pieces fit

```
site ──HTTP (unchanged)──> replay-worker (disk: archive, jobs, control_cache)
                               │  start run (REST, token, ids)        ▲
                               ▼                                      │ GET inputs / POST result (private network)
                           Render Workflow "replay-tasks" ── run ─────┘
                           tasks: parse, control, heights (one machine per run)
```

### The workflow service `replay-tasks`

- Docker, created by hand in the dashboard (Render can't create Docker workflows from the API, CLI or, as far
  as the docs say, a Blueprint), same region as `replay-worker`, auto-deploy from `main`.
- Image: `replay_tasks/Dockerfile`, the worker image's three stages (the pinned parser, the control venv, the app
  modules) plus `render` in the control venv and `replay_tasks/main.py` as the start command.
- Env: `REPLAY_WORKER_URL` (the worker's private `host:port`, set by hand), the same `REPLAY_PARSER_CMD`,
  `REPLAY_MEMORY_CAP_MB` and `CONTROL_CACHE_DIR` the worker image sets.
- Three tasks, each a thin wrapper around today's child, unchanged:

| Task | Input (arguments) | What it runs | Output |
| --- | --- | --- | --- |
| `parse` | `{job_id, token, recipe}` | `GET /runs/{job_id}/input` (the `.vrf`) → `CliReader export` → `condense_export_dir` | `POST /runs/{job_id}/result` with today's result dict |
| `control` | `{job_id, token, map, height?, features?}` | `GET` the task body, the height asset, the feature artifact and the map's visibility caches → `replay_worker.control_job` (stdin/stdout) | `POST` the child's JSON; then `POST /control_cache/{map}` for any new cache file |
| `heights` | `{job_id, token, map}` | `GET` the spooled rounds as one tar → `replay_worker.height_job` | `POST` the child's JSON |

- A refusal or a crash posts `{"status": "failed", "error", "error_kind"}` when it can; when it can't (killed,
  timed out), the worker learns from reconciliation (D4).

### The worker

- **Runner**: at each place a child starts today (`Worker._process`, `ControlRunner._run`), a branch on the
  kind's switch: `local` keeps today's code untouched; `workflow` opens a run in the run table
  (`replay_worker/runs.py`: job id, token, Render run id, deadline), registers its input files, starts it and
  waits for the result route to settle it. A parse records the run's id in `job.json` before it waits.
- **Run routes** (private, like every route there): `GET /runs/{id}/input[/{name}]`, `POST /runs/{id}/result`
  (header `X-Run-Token`), `GET /control_cache/{map}` (a list) and `/control_cache/{map}/{file}`,
  `POST /control_cache/{map}/{file}`. A result with a wrong token, for an unknown run or for a run already
  settled is refused (`409`) and changes nothing. **Every run route streams** from and to disk in chunks (1 MB),
  never holding a whole file in memory: 40 control runs fetching a 15 MB cache and five parse runs fetching a
  200 MB `.vrf` at once must keep the worker's memory flat.
- **Load**: in workflow mode the worker only serves files and keeps records; its CPU and memory use drop
  well below today's. It stays on `2c-4g` through step 4 (the `local` rollback needs it) and moves to
  1 CPU / 2 GB in step 5, after checking Render's metrics for the worker during steps 2 and 3.
- **Reconciliation**: every 60 s, `GET /v1/task-runs` (paged, newest first) for the worker's open runs; a run
  that ended `failed` or `canceled` with no result posted settles as `infra` (control, heights) or as
  `parse failed` (parse). A run past its deadline is cancelled and settles as a timeout.
- **Start pacing** (D10): a token bucket around every start; on `429` the run stays `queued` and retries later.
- **What stops mattering in workflow mode**: preemption (a parse no longer kills control children; the
  `on_parse` hook does nothing), the "idle" gate for height builds, and the 2-children pool (`REPLAY_CONTROL_WORKERS`
  becomes the number of runs, at least 40). Warming per map stays (D6).
- **Restart**: parse jobs reload from `job.json` as today; a job recorded as `parsing` with a Render run id
  re-attaches (reads the run; waits for its result or reconciles it) and never starts a second run. Control
  and height runs are in memory as today: on restart they are forgotten and the site asks again; a late result
  for a forgotten run is refused by its token.
- **Health**: `/health` gains `runners: {parse, control, heights}` and `workflow: {starts_per_min, open_runs,
  last_reconcile, last_error}`.

### The site

- `replay_control_remote.py`: `IN_FLIGHT` → `settings.control_max_runs` (40) and `settings.control_max_matches`
  (1), read in `_send` and `_submit`. `_send` skips a round of a replay not already in flight when the replays
  in flight are at the match limit. Nothing else changes; the site does not know which runner the worker uses.
- `render.yaml`: the two settings as env vars on `valowithfriendstracker`; the worker's runner switches,
  `REPLAY_CONTROL_WORKERS=40`, `REPLAY_CONTROL_QUEUE` ≥ 40, `RENDER_API_KEY` (by hand, `sync: false`) and
  `REPLAY_WORKFLOW_SLUG` on `replay-worker`.

## Steps

| Step | What | Done when |
| --- | --- | --- |
| 1 | **Foundation.** The `replay-tasks` entry point with a `ping` task (each later step adds its task); the run table, the run routes, the Render REST client, start pacing, reconciliation, settings, health. Every switch stays `local`. Confirm the REST endpoints and the private-network reach with one no-op `ping` task. | Deployed; `ping` round-trips worker → run → worker on Render; nothing else changed. |
| 2 | **Map control on Workflows.** `WorkflowRunner` for control; visibility caches on the disk; the site's two limits. | `REPLAY_CONTROL_RUNNER=workflow` on Render; a new match's rounds computed with byte-identical results to local for a sample; the switch back to `local` still works. |
| 3 | **Parsing on Workflows.** Parse runs, up to 15 at once (D15), with re-attach on restart; the deploy-skew refusal; uploads and automatic re-parses unchanged on the site. | `REPLAY_PARSE_RUNNER=workflow`; many uploads parse in parallel; a restart mid-parse re-attaches; `parse_seconds` compared with today's (D7). |
| 4 | **Height builds on Workflows.** The spool served as a tar; build runs; several maps' builds at once (D16). | `REPLAY_HEIGHTS_RUNNER=workflow`; rebuilds of two maps run together and are accepted by the site's checks. |
| 5 | **Slim the worker** (two PRs: 5a gives the workflow its own `replay_tasks/Dockerfile`, 5b the rest). Remove the local child paths for parse, control and heights, the preemption and idle code, and the .NET parser from the worker image; `POST /jobs` streams the upload to disk instead of reading it whole;
1 CPU / 2 GB plan. Keep the control venv for feature verification (D14). | The worker image has no parser; all three kinds run on Workflows only; the plan in `render.yaml` is smaller. |

Each step is its own PR and its own plan. Steps 2-4 merge with their switch `local`; the owner flips it on Render.

## Testing

- Worker: tests of the workflow path against a fake Render API (start, result push, wrong and
  repeated tokens, reconciliation of a failed run with no result, deadline cancel, 429 pacing, restart
  re-attach for parse). The existing local-mode tests stay green unchanged through step 4; step 5 moves them onto a
  fake-Render harness.
- Tasks: each wrapper run in-process against a fake worker gives the same bytes as running the child directly.
- Site: `_send` respects both limits (one replay at a time; 40 rounds; a long overtime replay stops at 40).
- On Render: one kind at a time, by flipping its switch; compare outputs and times with the `local` runner.

## Out of scope

- Object storage; the site calling Render; giving runs database access.
- Moving feature verification to a task (D14).
- Changing what control, parsing or heights compute.

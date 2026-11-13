# Deploy drain pipeline (PR 1 of 2)
Goal: a deploy no longer kills in-flight replay work. A GitHub Action drains the friends site (pauses new work,
waits for running work to finish, up to 60 min), deploys `replay-tasks`, `replay-worker`, `valowithfriendstracker`
and `valomaths` from one commit, in that order, then resumes. PR 1 ships everything with the Action manual-only
(`workflow_dispatch`) and `render.yaml` still `autoDeploy: true`; PR 2 (later, not this build) flips the merge trigger
and `autoDeploy: false` after the owner's setup and a live `dry_run`.
Done when:
- `POST /admin/deploy/drain` (Bearer `REPLAY_ADMIN_TOKEN`) starts a drain; for its duration the site sends no new
  control/gaps rounds, opens no height or Critical Util build, reserves no auto re-parse, and the worker accepts uploads
  but starts no parse; `GET /admin/deploy/status` says what is still running and `quiet: true` once nothing is.
- `POST /admin/deploy/resume` (or 2 h without an owner poll or request) undoes all of it.
- The upload page and the Site health card show "Deploying: N runs finishing, new work paused" while draining and
  "Last deploy failed" after a failed one, until a successful one.
- `.github/workflows/deploy.yml` (manual, inputs `skip_drain`, `dry_run`) runs `webapp/scripts/deploy_pipeline.py`,
  whose whole flow is tested offline with fakes.
- `webapp/RENDER_DEPLOY.md` has the owner's setup checklist; the warm-build state table has the new rows first.

## Decisions (settled with the owner 2026-11-12/13; not re-asked)
D1 Trigger (PR 2): every merge to main except merges touching only `docs/**`, `**/*.md`, `webapp/tests/**`
   (`paths-ignore`). PR 1: `workflow_dispatch` only.
D2 Merges collapse: a new Action arriving during a drain takes it over, keeping the original 60-min clock; the old
   Action is cancelled. Arriving while deploys run, it waits for them to finish, then runs its own full drain.
D3 Manual inputs: `skip_drain` (default off) deploys at once; `dry_run` drains, waits, resumes, deploys nothing.
D4 No CI gate.
D5 Paused while draining: new control and gaps rounds, height builds, auto re-parses, Critical Util builds (the
   remote util-build loop's new opens and the local child's starts). Worker "hold parses": uploads accepted and
   stored, no parse starts (uploads, re-parses, released held jobs) until resume.
D6 Waited for: control/gaps rounds incl. queued on the worker and not yet collected by the site; warm builds;
   height builds; a Critical Util build already open or a local child already running; a parse already running;
   uploads arriving (at the site and at the worker), then 60 quiet seconds (a new upload restarts the 60 s).
   Not waited for: map-pool check, archive sync, replay-states, site-health flush, layout walls.
D7 Hard limit 60 min from the drain's start, then deploy anyway; even then wait for an upload in progress + its 60 s.
D8 Forced deploy: the worker cancels its active Render Workflow runs; a `forced_deploy` row on the Site health card
   says what was lost (new recorded kind).
D9 Deploy order replay-tasks → replay-worker → valowithfriendstracker → valomaths, same commit, each live before the next.
D10 Resume after deploys. On a failed deploy: resume anyway, fail the Action, banner "Last deploy failed" until the
    next successful deploy.
D11 Safety expiry 2 h. AMENDED 2026-11-13 (plan review F3, owner): every owner poll and every drain request sets
    `expires_at` = now + 2 h, so it fires only when the Action has stopped polling for 2 h; `started_at` and the
    60-min clock never move.
D12 Visibility: banner (upload page + Site health card) and the Actions log. No audio.
D13 Rollout: PR 1 as above; owner creates 4 deploy hooks, adds secrets, turns off auto-deploy for demo and
    replay-tasks, runs `dry_run`; PR 2 flips the trigger and `autoDeploy: false`.

### My calls (approved by the owner 2026-11-13)
C1 Drain state is a DB table `deploy_drains` (migration 0034), one row per drain: history doubles as the "last deploy"
   record and as the stuck-upload clock's drain intervals.
C2 Ownership is arbitrated by the site: each call carries the Actions run id; only the owner may poll-as-owner,
   move to `deploying`, force or resume (409 otherwise). A superseded Action exits on its next owner poll.
C3 "Live" = the service reports the target commit: site and demo `/health` gain `commit` (`RENDER_GIT_COMMIT`); the
   worker's `/health` gains `commit`; `replay-tasks`' commit comes from a ping run's answer. Deploys are triggered
   with the hook URL plus `ref=<sha>`. 30 min per service, then that service has failed; the first failure stops later
   deploys. `dry_run` prints all four commits and fails when any is unreadable.
C4 `skip_drain` still takes the drain row, straight into `deploying` (new work paused during the deploys; no wait, no
   cancel), so a merge during it queues behind it.
C5 The worker's parse hold is a file with an expiry on its persistent disk (temp dir when the archive is off); the
   site's control cycle re-syncs it to the DB every pass.
C6 `forced_deploy` resolves when a later drain finishes with outcome `ok` and nothing forced.
C7 Stuck-upload timeouts (20 / 60 min, and the no-job-id case) don't count time inside a drain.
C8 Admin routes live under `/admin/deploy/*`, same `require_admin` as `/admin/replays` (404 without the token, 404 in demo).

## Rules
R1 Drain active ⇔ a `deploy_drains` row with `ended_at IS NULL` and `expires_at > now`. At most one row has
   `ended_at IS NULL` (partial unique index); an expired unended row is closed `expired` in the same transaction that
   reads it as inactive. Every write (request, poll, deploying, force, finish) runs under one PostgreSQL transaction
   advisory lock (`DRAIN_LOCK`), so two calls never interleave. A request carries `run_id` and `sha`.
   No active drain → a new row (`started_at` = `requested_at` = now). Active in `draining` → take over: `run_id` and
   `sha` := the new ones, `superseded` := the old run id, `started_at` kept. Active in `deploying` → `busy`, no change.
   Every request and owner poll sets `expires_at` = now + 2 h (D11).
R2 Only the owner run polls as owner, moves `draining → deploying`, forces or ends the drain; anyone else gets 409.
R3 While active (read from the DB each time, uncached, by every scheduler): `replay_control_remote` sends nothing —
   checked at `_submit`'s entry and again before each send in `_send` (collect still runs; rounds in flight are
   collected and stored); `replay_reparse_auto` reserves and re-sends nothing (accepted attempts are still collected
   and settled); `replay_heights_remote` opens nothing: no new build, and a build without a worker job id (unopened,
   or cleared by `WorkerGone`) is skipped with no try spent, while builds with a job id advance and are collected;
   `util_build_remote` starts no push/open from `idle`, abandons a `pushing` preparation refunding its try, and
   follows and stores an `open` build; `util_rebuild.request()` returns False without spawning. Each scheduler
   re-checks the pause immediately before every admission it makes (a re-parse reservation, a height build open, a
   util push and its open), not only at the start of its pass (review 2 F3).
R4 Worker calls are fenced by drain id: `POST /drain/hold {drain_id, until}`, `POST /drain/release {drain_id}`,
   `POST /drain/cancel {drain_id}`. The worker keeps the hold's `drain_id`; a release or cancel naming another id is
   ignored (200 `{"ignored": true}`). Drain ids only go up: a hold with an id above the highest the worker has seen
   (persisted with the hold file, kept after release) replaces the old; a hold with a lower id is ignored, so a
   delayed stale hold or release never touches a newer drain. Each control cycle syncs: active →
   hold when the worker's `drain.drain_id` differs or `hold_until` is null or more than 60 s off; inactive → release
   (with the worker's own `drain_id`) when the worker reports `hold` or `starts_blocked`. The drain and resume routes
   do the same at once. A failed sync never stops the cycle (`loop_failed`/`loop_ok("drain_hold_sync")`).
R5 Worker hold: while `now < until`, no parse is admitted (uploads, re-parses, released held jobs, recovered jobs).
   The hold check and parse admission share one lock: a job dequeued while held goes back to the front of its queue,
   in order; a parse admitted before the hold was set is running and is counted (by the admission counter, not job
   status). POST /jobs still streams, stores and queues (202). Expiry or release clears the hold AND the start
   barrier (R9) and wakes the parse thread and the control scheduler. The hold survives a worker restart (file); a
   corrupt or missing file = no hold.
R6 Worker `/health` gains `commit` and a `drain` block: `{"hold": bool, "hold_until": float|null, "drain_id": int|null,
   "starts_blocked": bool, "cancelling": int, "uploads": {"receiving": int, "last_end_age_s": float|null},
   "parses": {"running": int, "queued": int}, "util": {"building": int}, "runs": {"active": int}}`.
R7 Site upload counter: an ASGI middleware counts `POST /replays/upload` requests from first byte to response end
   (`receiving`, `last_end_age_s`), in-process. Status `quiet` is true only when all hold: worker reachable; worker
   `drain.hold` true (acknowledged); the dispatcher has completed a cycle after the drain's `requested_at`
   (`LIVE.at`); and these are all zero: site control in flight (`LIVE.in_flight`), worker `control.queued` +
   `control.running`, worker `warm_builds.building`, worker `heights` collecting/queued/running, site height builds
   with a job id, site util phase ≠ idle, worker `drain.util.building`, local util child running, worker
   `drain.parses.running`, worker `drain.cancelling`, and `uploads_quiet`. `waiting` lists each unmet item by name (counts, or 1 for a flag).
R8 `uploads_quiet` = site and worker `receiving` both 0 and each `last_end_age_s` null or ≥ 60.
R9 Force: `POST /drain/cancel {drain_id}` returns at once (200 `{"cancelled": [...]}`, the runs it claimed): under
   the lock it sets the start barrier (no new Render run starts, ping runs excepted) and marks every open run
   `cancelled` (a per-run flag, so a run's own start checks it before pacing, after pacing and after `api.start`
   returns, whatever the barrier says later; a run whose `api.start` returns after it was cancelled is cancelled at
   Render at once); the Render cancel calls run on a background thread, `drain.cancelling` counting those not yet
   done. A repeated cancel for the same drain is idempotent. Each run settles with `error_kind` `drain`: once a run is
   marked `cancelled`, a result it posts, a reconciliation that finds it ended, or any other settlement is refused,
   so cancel always wins. Settlement: a control round (leased or not) and a warm build go back to
   the front of the queue with nothing counted (no try, no `warm_failures`, no `warm_missed`; a warm identity whose
   file isn't on the disk is untracked); a Workflow parse goes back to `queued` with its input kept; a height build
   or util build ends `failed` with `error_kind: "drain"`, which the site counts as no try. The barrier lifts on
   `POST /drain/release`. The site writes one `forced_deploy` event with `record_now` (key = drain id, context = the
   cancelled list and the `waiting` counts) and marks the row `forced`.
R10 End: `POST /admin/deploy/resume {run_id, outcome: ok|failed|dry_run}` sets `ended_at`, `outcome`, `commit` (the
    drain's `sha` for ok/failed), then releases the worker hold and barrier (R4 retries it if this fails). `ok` with
    nothing forced resolves every open `forced_deploy` row (`resolve_now`).
R11 Stuck-upload clocks (`STUCK_AFTER`, `QUEUED_STUCK_AFTER`, the no-job-id case) measure `now - created` minus the
    overlap of [created, now] with every drain's [started_at, ended_at or min(now, expires_at)]. A drain closed as
    `expired` gets `ended_at = expires_at`, not the time it was noticed.
R12 Banner: active, phase `draining` → "Deploying: N runs finishing, new work paused" (N = the row's
    `waiting_total`, set when the drain starts and on every owner poll); phase `deploying` → "Deploying <short sha>:
    new work paused". Not active and the latest drain with outcome `ok` or `failed` is `failed` → "Last deploy failed
    (<short sha>, <time>)"; `dry_run` and `expired` rows are ignored for this. Shown on `/replays/upload` and in
    `errors.json`'s card payload. Never in demo mode.
R13 Pipeline: drain (unless `skip_drain`) → owner poll every 30 s until `quiet` or 3600 s after the drain's
    `started_at` (the server's, so a takeover keeps the clock) → at the limit poll until `uploads_quiet`, then force, then
    poll until worker `drain.cancelling` is 0 (5 min limit, then go on) → `deploying` → for each service in D9 order:
    (before the worker and site hooks only) poll until `uploads_quiet` again, 15 min limit, then go on — uploads
    arriving during the deploys are protected up to each hook, not through Render's build-to-cutover window, which
    the pipeline can't see (review 3 F4, documented in RENDER_DEPLOY.md) → hook with `ref`, poll its commit every 30 s (owner polls keep the expiry fresh), 30 min limit → resume with the
    outcome. No pipeline call blocks longer than 60 s: the tasks commit is probed asynchronously (the owner poll with
    `tasks: true` starts a background ping on the site if none is running and returns the last finished result with
    its start time; only a result started after the tasks hook counts). Any exception after the drain began still calls resume
    (outcome `failed`). `dry_run`: no hooks, no force; waits up to 6 min (owner polls continuing) for the first
    tasks probe to finish; prints the four commits; outcome `dry_run`; exits 1 if a commit
    is unreadable.
R14 Takeover: a `superseded` run id in the drain answer is cancelled with `gh run cancel` (failure logged, not
    fatal). A 409 on any owner call ends the run "superseded", exit 0, without resuming. A `busy` answer re-requests
    every 30 s until it isn't busy, for at most 90 min (then exit 1, "another deploy never finished"). The job's
    limit is 360 min (GitHub's maximum), which covers 90 busy + 60 drain + 5 cancel + 4×(15 upload + 30 deploy).
R15 Every committed file is free of secrets: hook URLs, token and site URLs come from GitHub secrets
    (`RENDER_DEPLOY_HOOK_TASKS`, `_WORKER`, `_SITE`, `_DEMO`, `REPLAY_ADMIN_TOKEN`, `SITE_URL`, `DEMO_URL`); the
    script never prints a hook URL or the token.
R16 Demo mode: drain routes 404, no banner, no drain-table reads on demo pages; the demo's `/health` still reports `commit`.


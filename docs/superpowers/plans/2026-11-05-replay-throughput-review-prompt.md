# Review prompt: replay task throughput plan

Paste everything below the line into a fresh session (Claude Code, Codex, or another reviewer) opened at the root of
this repo on branch `worktree-replay-throughput`.

---

You are reviewing an implementation plan before anyone builds it. Don't write code or change any file except the
review file named at the end. Read the plan, check every claim it makes against the code, and report what is wrong,
missing or risky.

**The plan:** `docs/superpowers/plans/2026-11-05-replay-throughput.md`. Read it in full first.

**The goal it serves:** the owner wants 30+ map-control Render Workflow runs busy whenever map control needs
recomputing. Today they see about 1 run in progress, and cancelled runs that all end at 29:59. The plan says why
(a cold map runs one round at a time; the round that builds its visibility cache is killed at 30 minutes, the cache
it built is lost, and the loop repeats) and fixes it with warm builds, longer limits counted from when a run
starts, and a measured concurrency ramp. The owner has accepted eight named risks; the plan's risk table maps each
to its mitigation.

**Code to read (paths from the repo root):**
- `replay_worker/server.py`: `Settings`, `Settings.from_env`, `keep_local_sizing`, `ControlJob`, `ControlRunner`
  (`_next`, `_schedule`, `_run`, `_run_remote`, `counts`, `preempt`), the HTTP handler (`/health`, `_run_cache`,
  `_run_result`, `do_POST`), `main`
- `replay_worker/runs.py` (all of it), `replay_worker/control_cache.py` (all of it), `replay_worker/control_job.py`
- `replay_tasks/main.py`, `replay_tasks/tasks.py`
- `webapp/app/control/task.py` (`_load`, `compute_task`), `webapp/app/control/geometry.py` (`cache_key`,
  `visibility`), `webapp/app/control/feature_visibility.py` (`SightCache.__init__`, `_load_rows`, `persist`)
- `webapp/app/services/replay_control_remote.py` (`STALE_JOB_S`, `_collect`, `_send`, `_submit`, `room_for`),
  `webapp/app/services/replay_heights_remote.py` (`STALE_BUILD_S` and where it is read)
- `render.yaml`
- The tests the plan changes: `webapp/tests/replays/test_replay_worker_control_workflow.py`,
  `test_replay_worker_runs.py`, `test_replay_worker_control_cache.py`, `test_control_task.py`,
  `test_control_remote.py`, `test_replay_worker_heights_workflow.py`, and the fake Render in
  `webapp/tests/replays/fake_render.py`
- Context: `CLAUDE.md` (root), `docs/superpowers/specs/2026-10-14-render-workflows-design.md` (the design these
  runs came from: its decisions D4, D6, D8-D10, D16, D17 constrain this plan)

**Check, and say for each whether it holds:**

1. **The diagnosis.** Does the code really produce the loop the plan describes? Trace a cold map's first round end
   to end: where its deadline is set, what cancels it at 29:59, when its caches would have been posted, and why
   the next round of that map can't start in parallel. Is anything else able to produce "1 in progress, cancels
   at 29:59" that the plan's Task 0 gate wouldn't catch?
2. **Warm-key correctness (risk 2).** The plan treats a key as warm only while the exact file its warm build named
   is on disk, and forgets the key when a round reports `visibility_source == "built"`. Can a round on a "ready"
   key still be sent without that file (`control_cache.inputs` sends only the newest `KEPT` caches of a map; two
   warm keys of one map; pruning in `store`)? Can a stale name survive a height rebuild? Does `warm_task` load
   exactly the geometry `compute_task` would (same `_load` key and options, features, `height_mode`)? Does
   `_GEOMETRY` memoisation make `visibility_source` misleading anywhere a warm build or round runs?
3. **The scheduler change (Task 4).** Read the new `_next` against the old one and against `_schedule`. Look for:
   a warm build that is never started or started twice; a round that waits forever (a warm build that hangs, a key
   whose failures never reach `WARM_BUILD_TRIES`, `self.pending` filling to `control_queue` with rounds waiting
   on warm builds); iteration while `_ask_warm` inserts into `self.pending`; warm jobs leaking in `self.jobs`;
   counting against `control_runs`; interaction with `preempt`, local height builds, and the fallback path; thread
   safety (what `_run` does under `self.lock`, and the file stat in `_ready` under it).
4. **The time limits (Tasks 2, 5).** Is extending a deadline once at `started` safe with `Runs.wait`'s polling,
   `Runs.reconcile`, Render's `Retry(max_retries=1)` on control (does a retried attempt post `started` again, and
   what then?), and a worker restart mid-run? Is the parse path really untouched (explicit deadline)? Do the site
   backstops (`STALE_JOB_S` 15000, `STALE_BUILD_S` 166200) do anything harmful at those values: a round or build
   held "running" for hours that should have been retried sooner, or a map's rounds held back by a height build?
   Is 24 h Render's real maximum for `timeout_seconds` (`https://render.com/docs/workflows-defining`)?
5. **Rollout safety (risk 7).** The site, the worker and the replay-tasks workflow deploy separately from one merge.
   For each order they could come up in, does anything fail: a new worker calling a `warm` task that isn't
   registered yet, a new task image posting `started` to an old worker, an old worker receiving a cache from a
   `warm` run, `render.yaml` env changes on a Blueprint sync? Is `REPLAY_WARM_BUILDS` off by default everywhere,
   including tests?
6. **The cache budget and feature rows (risks 3, 5).** Is "a smaller feature-row file never replaces a larger one"
   sound given how `persist` writes (`savez_compressed`, capped by `MAX_ROW_BYTES`)? Could it pin a corrupt or
   stale file forever? Does making the reserve configurable keep the archive's budget arithmetic right
   (`archive_extra_reserve`, `keep_local_sizing`'s local path)?
7. **Measurement.** Will the `/health` fields the runbook reads (`control.cache`, `control.warm_builds`,
   `workflow.by_kind`) answer each runbook check? Is anything the owner needs to decide a ramp step missing?
   Is `usage()` cheap enough for `/health`, which the site calls every 20 s?
8. **The tests.** For each test the plan writes: does it fail before the change for the reason the plan states,
   and pass after? Do the stubs behave as the tests assume (the `STUB` cache detection `n.count(".") == 2`, the
   `WARM_STUB` names, the `Broken` map, FakeRender's runner)? Are there timing races (`wait_running` thresholds,
   `sleep:1.5`)? Does any existing test break that the plan doesn't mention?
9. **Scope and constraints.** Does any task break the Global Constraints (stdlib-only worker modules, no change to
   what a round computes, local mode untouched, no merge)? Is any task too big to review on its own, or two tasks
   that can't be reviewed apart?
10. **Simpler alternatives.** Is there a smaller change that gets the same throughput with the same safety (for
    example, posting a cache the moment the child writes it instead of a separate warm task)? Say why it is or
    isn't better; don't just list it.

**How to report.** Verify every claim against the code before you make it, and quote the file and line. Rank findings
by severity:
- **Blocker:** the plan as written would do something wrong or fail to fix the problem.
- **Should fix:** a real gap or risk the plan doesn't cover.
- **Nit:** wording, naming, or a cheaper test.

For each finding give: the plan section, what's wrong, the evidence (file:line), the failure it would cause, and the
smallest change to the plan that fixes it. Where the plan is right on a point you checked, say so in one line, so
the owner can see what was covered. Say "I didn't check X" rather than guess.

Write the review to `docs/superpowers/plans/2026-11-05-replay-throughput-review.md`, with a response table at the
end (finding, severity, proposed change, and an empty "owner decision" column). Then stop.

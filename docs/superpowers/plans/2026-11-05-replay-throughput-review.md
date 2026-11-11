# Replay throughput plan review

Reviewed 2026-11-05 on `worktree-replay-throughput`. This is a static review of the plan and referenced source, not an implementation. Recommendation: revise before building. The cold-map loop is plausible and supported by the code, but Task 0 does not establish it as the production cause, and the exact-file readiness guarantee is incomplete.

References below are repository-relative `file:line` locations in the current checkout. Plan references use `docs/superpowers/plans/2026-11-05-replay-throughput.md` (abbreviated **plan**). No implementation files were changed. Tests proposed by the plan were inspected, not executed against a hypothetical implementation. I did not inspect private dashboard runs, measure production cache sizes or HTTP latency, or verify the owner's workspace limits.

## Findings

### F1 — Blocker: Task 0 classifies missing input as proof of where time was spent

**Plan:** Task 0, plan:82-88; diagnosis, plan:20-27.

The gate says that no visibility filename means the run “was building it” and permits the entire plan. It proves only that no visibility file was offered. After building visibility, the child still computes the round, gaps, moments and util before any cache upload (`replay_tasks/tasks.py:164-171`; `webapp/app/control/task.py:414`, `443-449`; `_gaps` at `296-395`). A cold run could build visibility quickly and spend the remaining 30 minutes elsewhere. Fetch/setup failures or a long queue can also consume the deadline without an expensive visibility build (`replay_worker/runs.py:180-209`; `replay_tasks/tasks.py:133-140`). Conversely, an offered cache can be for another geometry, or fail download; the gate's “cache present => bottleneck elsewhere” is also invalid (`control_cache.inputs`, `replay_worker/control_cache.py:53-59`; `geometry.visibility`, `webapp/app/control/geometry.py:1373-1380`).

**Failure:** The gate can approve warm builds for a round-compute bottleneck, or reject them when only an obsolete cache was offered. Dashboard duration alone cannot distinguish the worker's cancel from the independent 1800-second Render timeout (`replay_tasks/main.py:26`).

**Smallest correction:** Make Task 0 inconclusive on filenames alone. Require timing evidence separating queue/setup, visibility and round work, plus the cancellation reason. Task 1 only reports after completion; permit a diagnostic longer-limit run or a visibility-start/end log before this gate. Compare the exact expected cache identity, not any map filename.

### F2 — Blocker: ready does not guarantee the exact file reaches a round

**Plan:** Task 4 rules 1 and 5; plan:956-960, 1007-1014; risk 2.

`_ready` checks a path, but `_run_remote` independently obtains the newest KEPT inputs later (`replay_worker/server.py:1555-1560`; `replay_worker/control_cache.py:53-59`). There is no reservation between that check and input download. `store` can remove that exact file for budget or generation pruning (`replay_worker/control_cache.py:108-123`), and `_fetch_inputs` silently skips missing files (`replay_tasks/tasks.py:133-140`). Then every already-released round can rebuild visibility. Forgetting a key on a completed miss cannot stop those runs; the first miss can arrive two hours later.

A stable directory with only two generations is fine (KEPT is 3). Indeed, under ordinary successful pruning, an existing file is among the kept newest files. The actual gap is concurrent eviction/pruning after readiness/input selection, plus a file snapshot that consists only of mutable paths. The existing “fetch pruned file and rebuild” behavior is intentional D6 behavior, but is incompatible with the plan's stronger promise at 30+ concurrency.

**Failure:** Precisely the expensive fan-out risk 2 claims to prevent remains possible.

**Smallest correction:** Explicitly include the recorded exact name in round inputs and protect its bytes until transfer completes (for example a per-run hard link/snapshot or a reference-counted lease honored by pruning). If a required warm input cannot be supplied, requeue/re-warm instead of releasing a cold child. Add a test that evicts the exact file between scheduling, selection and fetch; a post-completion miss test alone is insufficient.

### F3 — Should fix: warm-key identity omits geometry options

**Plan:** Task 4 `_ask_warm` and `_ready`; plan:962-976.

Existing `submit` builds `warm_key` from map, height digest and feature digest only (`replay_worker/server.py:1312-1314`). `_load` additionally distinguishes explicit heights paths and `height_mode` (`webapp/app/control/task.py:145-157`). In particular, `flat` returns before attaching heights, whereas `legacy_default` may attach the committed asset (`webapp/app/control/geometry.py:575-584`). Tasks with no explicit height digest can therefore share the warm key while requiring different visibility names. An exact filename stored for one task does not prove another task with the same incomplete key will read it.

**Failure:** Readiness can refer to the wrong geometry and repeatedly release cold rounds; a height-mode transition is not fixed by the digest-change test.

**Smallest correction:** Define warm identity from all geometry-loading options, preferably the pinned geometry identity available to production tasks. At minimum include normalized height mode and supported explicit heights identity. Add same-map/same-height/different-mode coverage. Keep the original key path for local mode if its behavior must stay unchanged.

### F4 — Blocker: the proposed heights FakeLink method breaks existing tests

**Plan:** Task 2, plan:415-416: add the “same `post_started` method” as the control FakeLink.

That method is `self.order.append("started")` (plan:374-375). Heights `FakeLink.__init__` defines only `files` and `result`, not `order` (`webapp/tests/replays/test_replay_worker_heights_workflow.py:34-36`). The new first statement of `tasks.heights` calls it before the task's try/finally.

**Failure:** Existing heights wrapper tests raise `AttributeError` before exercising the child; Task 2 cannot pass as written.

**Smallest correction:** Make heights FakeLink's `post_started` a no-op, or initialize and assert an order list. Add an explicit real HTTP 404 test: port 9 tests connection refusal, not an older worker answering an unknown route (plan:410-412).

### F5 — Blocker: global time-limit env changes violate the local-mode constraint

**Plan:** Global Constraints “local ... exactly”; Task 5, plan:1146-1158; site constants at plan:1166-1172.

Settings reads these env vars regardless of runner (`replay_worker/server.py:292-300`); `_run` uses the same timeout for local subprocesses (`1434-1468`). `keep_local_sizing` restores queue/cache/spool sizing but not timeout values (`336-357`). Blueprint sync therefore makes a local control child live for 7200 seconds and a local height child for 82800 seconds, instead of 1800/3600. The site stale constants are also shared by local and remote paths (`webapp/app/services/replay_control_remote.py:430-437`; `replay_heights_remote.py:391-396`).

**Failure:** A rollback to local can hold resources, and the site's map-height hold, far longer than before. Unchanged tests constructed from Settings defaults will not catch the deployed env behavior.

**Smallest correction:** Separate workflow limits/backstops from local limits, or explicitly restore prior local values by runner with the owner's intended env-override policy. Test from the proposed Blueprint env with each mixed runner configuration. Alternatively obtain and document a deliberate relaxation of the global constraint.

### F6 — Should fix: compressed file size is not a feature-row quality or growth order

**Plan:** Task 6, plan:1259-1267; risk 5.

`persist` writes compressed snapshots (`webapp/app/control/feature_visibility.py:393-408`), not append-only sets. The in-memory cache evicts rows at its cap (`280-300`), and parallel rounds can learn different rows. Larger compressed bytes need not mean more useful rows or a superset; equal-size snapshots can still replace each other. More seriously, `_load_rows` clears invalid data and allows rebuilding (`347-387`), but the worker's proposed size guard rejects that repaired snapshot whenever a corrupt stored file is larger. Nothing currently validates the uploaded NPZ in the stdlib worker (`replay_worker/server.py:1898-1917`).

**Failure:** A corrupt large file can be served and ignored forever, while useful repairs are acknowledged as stored but discarded. The risk-5 mitigation does not ensure accumulated row knowledge.

**Smallest correction:** Describe this as a best-effort heuristic, with explicit corruption repair and bounded expiry, rather than “rows never shrink.” If preserving accumulated knowledge is required, use a validated row-set merge in the permitted child or immutable snapshot selection by validated metadata. Add real compressed-row tests for disjoint rows, equal size, eviction and corrupt-large/valid-small replacement. The cached-answer equivalence tests validate answers, not this replacement policy.

### F7 — Should fix: measurement can pass while runs are queued or keys are no longer ready

**Plan:** Tasks 1/4 and runbook, plan:982-990, 1329-1346.

`by_kind.open` counts local open-run records, including pacer/API-start waits and Render-queued runs (`replay_worker/runs.py:159-195`). Proposed `oldest_s` is age since opening, not since execution began. `counts.ready` lists all `warm_names` without checking file existence, even though `_ready` does check it. Thus a pruned cache stays “ready” in health. EVICTIONS is increased only for budget victims, not KEPT pruning (`replay_worker/control_cache.py:118-123`; plan:239-243), even though either can invalidate a warm name. Counters reset on every restart.

**Failure:** “30+ open” cannot certify the goal of 30+ busy executions; “oldest < 7200” can fail for healthy queued-plus-running work, whose allowed total is almost 14400. A zero eviction count can conceal generation churn.

**Smallest correction:** Report awaiting-start versus started, execution age, and stale warm names accurately; distinguish worker-reported started from actual current Render state on retries. Use dashboard running counts and completed rounds/time, with enough backlog, for ramp acceptance. Count pruning separately or include it in eviction totals; compare deltas within one process uptime. Record successful throughput and HTTP/transfer/resource measurements for the worker/site bottleneck check. “30+ for its whole run” also needs an exception for warm-up and the final draining tail.

### F8 — Should fix: once-only started and finite queue limits need an explicit retry/race policy

**Plan:** Task 2 `Runs.started`, plan:459-473; Task 7's queue assurance, plan:1330-1331.

Re-reading `deadline` each poll handles normal extension. It does not make the timeout decision atomic: `wait` can observe the old expired value, then `started` extends it, then `wait` cancels the Render run (`replay_worker/runs.py:202-209`). `accepts` and subsequent mutation likewise occur in separate lock sections (`213-220`; proposed method). A Render retry executes `control` again and posts started again (`replay_tasks/main.py:26-29`); once-only handling leaves it only the first attempt's remaining budget. Reconciliation still works at task-run level, but a late retry cannot get a new full worker budget.

The initial queue deadline is deliberately finite (`runs.py:180-184`, `202-209`), so queued runs can still be canceled before they ever post started. Queue time is not free or unbounded as the runbook implies.

**Failure:** A boundary start can be canceled after an accepted extension. A retry or long queue can still lose useful work; the two-times site backstop is not evidence of a full limit for each attempt.

**Smallest correction:** Document a total logical-run budget covering retries, or introduce authenticated attempt identities and a bounded retry-aware policy. Atomically claim timeout versus start/result settlement under the run lock, then perform network cancellation outside it. Add fake-clock/event tests for the race, a second attempt, and a queue that outlasts the initial limit. Keep parse's explicit deadline unchanged.

### F9 — Should fix: current Render limit documentation does not support the stated concurrency caps

**Plan:** diagnosis plan:29; runbook plan:1329-1330.

The official [limits page](https://render.com/docs/workflows-limits) checked for this review describes aggregate compute and compute provisioning per minute (Hobby 16 CPU/64 GB; Pro 32 CPU/128 GB), not a 20/50 concurrent-run table. The [task definition page](https://render.com/docs/workflows-defining-tasks) does confirm a 30-second to 24-hour timeout per attempt and a fresh timeout on each retry. The repository pins SDK 1.0.1 (`replay_worker/Dockerfile:32`), whereas the current documentation recommends a newer SDK; this review did not execute registration with that old SDK at the new limits.

**Failure:** Ramp decisions can be based on an unsupported cap or fail to account for provisioning delays.

**Smallest correction:** Replace the fixed 20/50 assertion with current documented limits and the owner's actual workspace settings. Smoke-test task registration with the pinned SDK, including timeout 86400, before rollout; no SDK migration is required merely by this finding.

## Coverage and points that hold

1. **Diagnosis, conditional:** `ControlRunner._next` allows a cold map only when no held job has that map (`server.py:1400-1416`). `_run_remote` opens the deadline before submission (`1557-1561`); the default is 1800 (`237-238`). `Runs.wait` cancels at the deadline (`runs.py:202-209`). Visibility is written before round work, but posted only after the child returns (`geometry.py:1380-1390`; `tasks.py:167-171`). A failed/canceled run without a posted cache leaves the map cold (`server.py:1520-1526`). This supports the loop, but does not prove the dashboard cause (F1).
2. **Geometry and heights:** `warm_task` and `compute_task` use the same proposed `_load_options` and `_load` call. Base visibility cache identity does not include attached feature artifacts (`geometry.py:1313-1337`); compute attaches/validates those after `_load` (`task.py:415-417`). Omitting that attachment from a visibility-only warm task is reasonable, but it does not warm feature rows or verify the round's full provenance. A different height digest gets a separate existing key; height-mode identity still needs F3. `_GEOMETRY` retains the original source/time on repeated calls (`task.py:150-169`; `geometry.py:1370-1371`), so those values are not per-call measurements. Production warm and round wrappers spawn fresh child processes (`tasks.py:99-107`), so memoization does not mislabel independent production runs; it matters for in-process tests and direct API reuse.
3. **Scheduler:** Copying pending avoids mutation during iteration. The first pass inserts warm jobs and can return None, but `_ask_warm` sets wake and `_schedule` also polls every second (`server.py:1418-1432`); this is a short delay, not a permanent stall. All access in `_next`, settlement and counts uses the runner lock. `warm_jobs` dedupes a key; warm jobs count in held slots; failed warm jobs are removed and increment failures until fallback. No normal-path warm-job leak or duplicate start was found. Remote warm jobs are excluded from local preemption through `remote` (`1358-1368`), and mixed local-height ordering stays intact. Disk stat under the runner lock is brief local metadata IO, but not a cache lease (F2). Waiting rounds filling control_queue do not prevent `_ask_warm` inserting internal warm work; they can stop additional site submissions until progress. A hung warm build blocks its key up to its finite queued-plus-started budget, twice before fallback; two such builds may hold the site's two replay slots for many hours. That is a real operational tradeoff to state, not an infinite scheduler deadlock.
4. **Limits/restarts:** Parse passes the explicit saved deadline (`server.py:963-965`) and Task 2 does not post started for parse: correct D8/D9 preservation. Control/heights/warm tables remain memory-only, as D9 specifies; restart forgets them, old tokens fail, and the site can resubmit on 404 (`replay_control_remote.py:417-422`). The worker's normal timeout and reconciliation still settle before the enlarged site backstops; the site collects settled failures immediately, not only after those hours. A genuinely running/stuck height build holds its map (`replay_heights_remote.py:16-17`; `replay_control_remote.py:667`); the proposed limits explicitly lengthen this window. F5 and F8 are the exceptions.
5. **All deployment permutations:** While warm stays off, site-first can still encounter old worker/workflow limits; worker-first can still encounter an old task's 1800-second limit; task-first posts to an old worker and ignores HTTPError (an OSError). No new required result fields are consumed by the site. Thus all six site/worker/task orders are protocol-compatible, but not all fix cancellations immediately. Enabling warm before registration fails starts and spends the two warm tries; keep the runbook gate. An old worker has no warm run table/job, so a leftover warm cache/result is refused; tokens prevent adopting it. New timeout env vars are already understood by old workers, so a Blueprint sync can lengthen their limits before the task image changes. [Blueprint documentation](https://render.com/docs/blueprint-spec#prompting-for-secret-values) confirms existing-service sync ignores `sync: false`; it does not create a new switch value on an existing service. Settings defaults and ordinary test fixtures keep warm off; warm_remote explicitly enables it. A value already manually set true is not reset by the merge: check that precondition.
6. **Budget/health cost:** Replacing RESERVE in `main` with the configured reserve keeps archive subtraction consistent when the cache is under the archive (`server.py:2270-2273`). Local rollback reserves the actual leftover on-disk bytes and relocates the active cache (`340-346`), so that arithmetic remains right. Heights spool reserve remains separate (`2274-2276`). `usage` scans filenames and stats, never reads NPZ contents; it is proportional to file count, not stored gigabytes (`control_cache.py:25-33` and proposed usage). Likely cheap at the capped normal cache population; I did not measure it with production feature/heights artifacts or partial uploads. It is a non-atomic snapshot and bytes includes incoming files, so temporary spikes need interpretation.
7. **Constraints/task boundaries:** Proposed worker modules stay stdlib-only; warm imports app.control only inside the separate child. Docker copies entire replay_worker/replay_tasks directories (`replay_worker/Dockerfile:46-47`), so the new module is shipped. No engine/revision/cache-key algorithm change or merge is requested. Local timeouts violate the constraint (F5). Tasks 3 and 4 are coherent separate commits, but the scheduler switch requires Task 3 deployed first; Task 2's deadline change and Task 5's larger values should be validated together. Task 7 should also update AGENTS.md when CLAUDE.md changes, per `AGENTS.md:3`.

## Proposed test audit

These are static expectations, not claims of a green test run. Before/after below assumes earlier prerequisite tasks are already implemented.

| Proposed tests | Before / after assessment |
|---|---|
| Task 1: round source; visibility filename | Missing result field / missing helper before; proposed code should satisfy both. The fixture preloads geometry (`test_control_task.py:21-25`), so these do not exercise real cache loading. |
| Task 1: usage; eviction count | Missing APIs before; arithmetic and map counting match the supplied fixtures after. Add KEPT-prune coverage (F7). |
| Task 1: open runs by kind; health cache | Missing fields before; proposed code matches the fake-clock and built-cache expectations. `warm_up` waits for settlement (`test_replay_worker_control_workflow.py:172-174`), so open={} is sound. |
| Task 2: started extends; explicit deadline; wrong/unknown token | Missing method before; normal-path expectations match the code after. Missing expired/done/closed run, concurrent cancellation and retry cases (F8). |
| Task 2: started HTTP route; inaccessible worker | Missing route/method before; likely pass after. Sleep 0.05 is unnecessary with a fake clock. Connection refusal does not prove 404 compatibility. Heights FakeLink change fails after as written (F4). |
| Task 3: warm names round file; missing geometry | Missing function before; fixture-backed success and GeometryError handling match afterward. Neither proves a real same-height/options geometry load. Add real option/height coverage. |
| Task 3: warm uploads before result; already-cached warm skips upload; registration | Missing warm function/registration before; wrapper snapshots and WARM_STUB satisfy the expected order afterward. Registration test checks AST/mapping, not SDK acceptance. WARM_STUB intentionally omits visibility_s, which these assertions do not require. |
| Task 4: cold fan-out | Fixture rejects warm_builds before. After, one warm task plus six round tasks is plausible. `wait_running(6)` counts worker assignments before six Render executions actually start (`server.py:1428-1432`); it does not prove active-execution concurrency. Use barriers/events rather than a 1.5-second timing window. |
| Task 4: new height | Separate key causes two warm starts after. WARM_STUB returns the same e*16 filename for every height (plan:567), so this cannot catch wrong-height file selection. |
| Task 4: Broken fallback | Broken warm stub fails; two failed warm jobs reach fallback; `build=True` control stub writes a valid Broken cache (`test_replay_worker_control_workflow.py:28-29`). Expected successful fallback is consistent. It tests a stub failure, not real absent geometry. |
| Task 4: built miss; disk deletion | Forced `source="built"` and explicit unlink exercise the specified transitions after implementation. They miss fetch-time eviction, two active geometry generations and F3. |
| Task 4: machine failure preserves ready | Infra stub produces a failed round; proposed branch retains warm_names, so next round uses it. Existing self.warm may be cleared, but ready still takes priority. |
| Task 4: switch off; settings | Off-path test already passes before implementation; it is a regression check, contrary to the blanket “eight new tests fail” wording (plan:897). Settings test fails before and should pass after. |
| Task 5: limit ordering | Existing registration has no warm task and env limits are absent: fails before, though exact first failure is expression-order dependent. Literal timeout/env changes satisfy arithmetic afterward. It does not verify deployed runner locality, retries, dashboard env overrides or SDK validity. Existing control stale tests use the named constant (`test_control_remote.py:327-354`, `1031-1035`); those inspected cases adapt. |
| Task 6: feature smaller/larger; visibility smaller | Feature-size assertion fails before; it passes with the heuristic after. Visibility replacement already passes. Arbitrary x/y/z bytes establish file-size policy only, not valid rows or repair (F6). |
| Task 6: configured reserve | Missing setting before; should pass after. Despite its name it tests only Settings, never store, health or archive integration. Extend it to exercise those three paths. |

The modified STUB's `n.count(".")==2` recognizes both supplied root e*16 and c*16 names, so it will not falsely mark an ordinary supplied warm-stub file cold. It also accepts any unrelated root visibility generation; it is not an exact-key test. FakeRender runs each callback in a thread and catches exceptions as failed status (`webapp/tests/replays/fake_render.py:61-69`); it simulates neither queue delays nor automatic retries. Its fixtures do not run the production reconciliation loop, so a swallowed callback exception can make a test wait for its timeout. Existing 1.5-second tests already have this timing style, but copying it does not prove robust concurrency under load. No other definite existing-test regression was established by static inspection; I did not execute every existing test or the slow lane.

## Smaller alternative

Posting visibility as soon as its child writes it can preserve that work even if the round later times out. It is not a one-line wrapper change: `_run_child` blocks until exit (`replay_tasks/tasks.py:107`), temporary NPZ files must be excluded, uploads need a reliable exact identity and completion signal, and the scheduler currently marks warm only at settlement (`server.py:1520-1524`). A watcher plus early-ready protocol could release rounds sooner, but couples live child progress, upload retries and scheduler state. A separate warm child is easier to bound and test if the goal is explicitly independent cache builds. The smallest staged intervention is instrumentation plus longer workflow limits first, measuring whether the existing first-round cache upload then unlocks adequate throughput. That may meet the owner's goal without Task 3/4; it will not unlock rounds while the first long round is still computing. Decide from Task 0 timing evidence, not filenames.

## Owner response table

| Finding | Severity | Proposed change | Owner decision |
|---|---|---|---|
| F1 Diagnosis gate | Blocker | Require phase timing, exact cache identity and cancel reason | |
| F2 Exact file handoff | Blocker | Lease/snapshot the exact input; requeue a required-input miss | |
| F3 Warm identity | Should fix | Include normalized geometry load options; test mode changes | |
| F4 Heights FakeLink | Blocker | No-op started method or initialize order; test real 404 | |
| F5 Local limits | Blocker | Preserve local timeouts/backstops under Blueprint env | |
| F6 Row-file heuristic | Should fix | Permit validated repair; test real compressed snapshots | |
| F7 Ramp measurements | Should fix | Distinguish queued/started, truthful ready, prune counters and throughput | |
| F8 Started/retry policy | Should fix | Atomic timeout/start claim and explicit bounded retry policy | |
| F9 Render limits | Should fix | Correct caps and smoke-test pinned SDK registration | |

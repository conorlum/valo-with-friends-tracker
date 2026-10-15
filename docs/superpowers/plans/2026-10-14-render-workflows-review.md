# Render Workflows pre-build review

Step 1 — build after fixes.
Step 2 — build after fixes.
Step 3 — rework.
Step 4 — build after fixes.
Step 5 — rework.

**8 blockers.** Reviewed against clean commit `0354faa`, branch `worktree-render-workflows`, in `.claude/worktrees/render-workflows`. No implementation changes, environment-file reads, credential use, or live Render API calls were made. Tests below are assessed by inspection unless an executed scratch check is explicitly identified.

Reference abbreviations in this review:

- **S**: `docs/superpowers/specs/2026-10-14-render-workflows-design.md`.
- **P1–P5**: `docs/superpowers/plans/2026-10-14-render-workflows-step1-foundation.md`, `...step2-control.md`, `...step3-parse.md`, `...step4-heights.md`, `...step5-slim-worker.md`, respectively. A reference such as P3:659 means that file's line 659.
- **server**: `replay_worker/server.py`; **archive**: `replay_worker/archive.py`.
- **scratch**: `C:/Users/User/.codex/visualizations/2026/10/14/01a13cbf-5d90-7330-91d5-a2e194cb8d16/review-scratch` (outside the repository).
- **SDK**: the extracted `render==1.0.1` wheel at `<scratch>/sdk`; paths under SDK are relative to that directory.

## Findings, most serious first

### F01 — Restart recovery does not provide the promised single parse

**Severity: blocker. Plan/task: P3 Task 3; P1 Tasks 2–3.**

P3:653–659 starts the external run before saving its token/id. A restart after Render accepts POST but before `on_started` persists the response leaves an ordinary `parsing` record without a run id; recovery starts another run. A lost HTTP response has the same ambiguity. Persisting only after `start()` returns cannot close this window.

There is a second loss window: P1:595–600 loads the uploaded answer and signals an in-memory event, P1:575/640–644 closes and deletes the run folder, and P3:661–662 clears the saved run identity **before** the existing durable settlement (`server:793–796`, `802–813`). A restart after a task receives 200 but before settlement loses a successful answer. P3:670–671 then treats an ended reconnected run without an answer as permission to parse again. That does not prove the previous parse never executed.

**Concrete fix:** replace the start/result protocol in the plan with a durable state machine: persist `start_intent` with token, recipe, absolute deadline and job identity before POST; persist the returned id without clearing the intent; atomically commit an accepted answer on the archive disk before returning 200; recover that answer before considering a start; clear the identity only with durable job settlement. An ambiguous POST must remain fenced as `start_unknown`, and must not automatically issue another POST. Recover its callback with the saved token, or fail/await resolution at the original deadline. Permit a fresh run after an explicit deploy refusal, which proves parsing did not start. Do not infer that proof from generic `infra` or “completed without result.” The wheel's `RunTask` model has `task` and `input`, with no declared idempotency-key field (`SDK/render/public_api/models/run_task.py:25–26`); an alternative relying on server-side idempotency needs verification first.

Add crash tests at acceptance-before-id-save, answer-before-job-save, and id-cleared-before-settlement. These are absent from P3:456–537.

### F02 — Fifteen waiting slots plus fifteen running parses are not fifteen admitted jobs; recovery drops jobs

**Severity: blocker. Plan/task: P3 Task 3 and Task 4.**

P3:732–735 describes `queue_size=15` as “15 waiting/running jobs.” The real queue is a waiting queue (`server:357–358`), and `_next` removes a job before parsing (`server:738–742`). With fifteen consumers, fifteen more uploads can wait. Reparses have a separate waiting queue; held jobs bypass both. More critically, `_recover` fills the bounded queue before the worker threads start (`server:388–392`, `948–958`). Accepted records beyond fifteen are marked failed and their input is deleted, including records for runs still executing.

**Executed evidence:** `<scratch>/checks.py` planted thirty accepted records (fifteen parsing, fifteen queued), suppressed consumer startup, and called the unchanged recovery path: `RECOVERY: accepted_records=30 queued=15 failed=15`. P3 modifies the fields read by recovery, not this capacity behavior (P3:561).

**Concrete fix:** distinguish admitted-job capacity from waiting-queue capacity in the supplied code. Enforce the stated fifteen-upload admission limit across incoming/queued/running uploads with a shared reservation, while retaining the separate held-upload policy. Recover already-started runs directly into a recovery backlog/run table before starting new work; never delete an accepted input because an in-memory queue is full. Include every recovered live run in the concurrency limit before admitting new starts. Add a fifteen-active plus waiting/held/reparse restart test, including records accepted by an earlier version.

### F03 — Deploy refusal and restart reset the parse's overall deadline

**Severity: blocker. Plan/task: P3 Task 3, `_process_remote`.**

Every `Runs.open` receives a fresh `parse_run_timeout_s` (P3:650–653). No original deadline is persisted. Eleven deploy-refused runs can each consume nearly 1200 seconds, with ten additional 60-second sleeps (P3:647–669). Restart grants another full allowance. This contradicts S:33's retry “within the job's existing stuck deadline”; the site's upload timeout is twenty minutes (`webapp/app/services/replay_upload.py:55`, `400–402`).

**Concrete fix:** persist one `run_deadline` for the job, preserve it across restart and deploy refusal, and pass only `max(0, run_deadline - time.time())` to `Runs.open`. Bound the deploy sleep by that remaining time and stop before another start when it expires. Explicitly distinguish the unchanged 240-second parser timeout from the overall transfer/run deadline in S:D8, which currently says `timeout_seconds=240` while P3:378 registers 1260. Add a fake-clock test covering repeated refusals and restart near the original deadline.

### F04 — Height `step()` reopens a build immediately after collecting its completed result

**Severity: blocker. Plan/task: P4 Task 4, supplied `step` replacement.**

P4:692 creates `due` once. `_try_build` removes a successful build at P4:656–658; `ready` at P4:705–706 then selects that same still-present due manifest, creates another `Build`, and calls `_try_build` again at P4:731. `HeightBuilds.open` returns the already-done worker build for the same key (`server:1389–1392`), so a successful result can be collected/stored/count twice in one cycle. The existing code advances only once (`webapp/app/services/replay_heights_remote.py:440–443`). This also makes the proposed “two live builds” count an unreliable assertion.

**Executed evidence:** executing the exact proposed `step()` with a completed-build stub produced `HEIGHT STEP: advances_for_one_finished_build_in_one_cycle=['a', 'a']` in `<scratch>/checks.py`.

**Concrete fix:** take `attempted = set(hstate.builds)` before advancing existing builds and exclude those keys from this cycle's `ready` list (`key not in attempted`). Re-plan the next cycle; do not reopen a manifest just settled from the old snapshot. Add a regression asserting one `_finish`/store/count per completed manifest per cycle and no extra `open_build` after collection.

### F05 — Step 5 deletes methods/settings that retained paths still call

**Severity: blocker. Plan/task: P5 Task 4.**

P5:271 says to delete `_parse_coming` and its call at the top of `_run`, but `_admitted` also calls it (`server:483–488`). The retained upload and reparse admission paths call `_admitted` (`server:454`, and `server:502–527`/`598–654`). The resulting attribute error occurs after queue admission and can prevent the HTTP response and job registration.

P1 adds `/health` reads of `settings.parse_runner`, `control_runner`, `heights_runner` (P1:932–934). P5 removes those fields (P5:221/277) but only instructs changing `idle` and `limits.memory_cap` in health (P5:273). Following those edits leaves `/health` raising `AttributeError`.

**Concrete fix:** also delete `_admitted`'s `_parse_coming()` invocation. Replace the retained `body['runners']` construction with `dict.fromkeys(('parse', 'control', 'heights'), 'workflow')`, or explicitly remove that newly introduced health key. Enumerate all deleted-name references in the plan and give exact replacements. Require one upload, one automatic reparse, and `/health` through the final server after removal.

### F06 — Step 5's test migration is neither mechanical nor complete, and its new upload tests are not runnable as supplied

**Severity: blocker. Plan/tasks: P5 Tasks 2–5.**

The promised unchanged test bodies (P5:157) rely on local filesystem/process behavior that the harness changes:

- The queue-overflow test assumes one consumer (`webapp/tests/replays/test_replay_worker.py:178–187`); the harness enables fifteen. The asserted third-request 503 is no longer deterministic/correct without setting `parse_parallel=1` for that test.
- The timeout test reads `stub.pid` from the worker's job directory (`.../test_replay_worker.py:151–160`); the task now parses in its own temporary directory (P3:351–357). Its PID assertion cannot pass unchanged.
- Empty-directory assertions (`.../test_replay_worker.py:132`, `173`, `187`, `205`) now see the persistent `runs`/intake directories. The harness changes the default root to `tmp_path/'tmp'` (P5:116) while those tests inspect `tmp_path/'jobs'`.
- `tasks.parse` uses its own `Settings.from_env()` (P3:350–357), but Harness exports only command overrides and control memory (P5:103–119). `parser_build`, parser memory and other parser overrides do not automatically follow the worker settings; the isolated task `_recipe()` also does not follow a monkeypatch of `server.current_recipe` in `.../test_replay_worker.py:257–267`.
- P5:155 omits actual constructors in `test_heights_remote.py:99–106/142–145`, `test_replay_archive_web.py:331–344`, `test_feature_acceptance.py:115`, and `test_feature_transport.py:81–83`. Removing required arguments/local execution breaks those integrations. It also omits the new `test_replay_worker_runs.py` fixture and tests P1:696–725, whose settings/default expectations are removed, and `test_replay_worker_parse_job.py`'s server alias checks P3:74–77 after P5 removes the aliases.
- There is no `h` fixture in the plan or current `test_replay_worker.py` (`54–69`); P5:325 merely says “call it h here.” All three new tests request that missing fixture.
- `post_raw` (P5:332–341) never closes or half-closes its socket. The short-upload test waits for a response while the server waits for the missing fifty bytes. Its 30-second timeout is not a clean EOF, and it races the server's identical timeout. The comment claiming “then hangs up” is false.

**Concrete fix:** replace Task 3 with explicit per-file fixture/helper and assertion edits. Preserve queue tests with an explicit single-consumer setting and add separate fifteen-consumer admission tests; expose task-child start/exit events through a shared log for the timeout test; assert no job/partial files rather than an entirely empty service root; export/restore each task setting used by converted tests. Convert the omitted files above and update the newly added run/settings/alias tests. Define an actual `h` fixture yielding the harness with teardown. In the short-body client use `conn.sock.shutdown(socket.SHUT_WR)` after sending the partial body, read the 400 response, and `conn.close()` in `finally`; then assert cleanup without an arbitrary sleep. Keep 5a before 5b.

### F07 — The height spool never moves to the persistent disk, its tar duplicates its space, and step 5 drops its reserve

**Severity: blocker. Plan/tasks: P2 Task 3; P4 Tasks 3–5; P5 Task 4.**

D16 requires the spool on the disk inside the reserve (S:37). `HeightBuilds` uses `settings.temp_root/'height_builds'` (`server:1375`). The current image sets `REPLAY_WORKER_TMP=/jobs` (`replay_worker/Dockerfile:60`), while the disk is `/var/replay` (`render.yaml:79–89`). P2 sets only `CONTROL_CACHE_DIR` to `/var/replay/control_cache` (P2:873–875). P4's reserve condition (P4:615–616) therefore stays false on that configuration. No task supplies the promised move of `temp_root/runs` to the disk either (P2:216–217 versus its actual Task 3).

Packing also retains the spool and a second complete tar simultaneously (P4:448–455); the tar lives under `temp_root/'runs-in'`, is not in `HeightBuilds._spooled` (`server:1515–1516`), and can add another 2 GiB plus tar overhead. Finally, P5's complete `main()` replacement restores only `control_cache.RESERVE` (P5:287–289), losing P4's added spool reservation even if the path was fixed.

**Concrete fix:** give heights a dedicated persistent spool root such as `<archive>/height_builds` and its packed inputs `<archive>/height_runs`, retaining `/jobs` for local parser scratch through step 4. Configure/create those paths explicitly. Reserve both spool and simultaneous tar bytes (with overhead), or supply code that serves a known-length tar without retaining a second copy. Set all reservations before recovery/consumer startup and carry them into P5's `main()`. Include a capacity test at the 2-GiB spool limit with four packed builds plus fifteen admitted uploads, and a restart test proving the disk selection. Remove P2's incorrect claim that its cache route writes under the run folder: its supplied code writes `<cache>/.incoming` (P2:665).

### F08 — “Streamed results” still load whole files, up to 1 GiB per simultaneous callback

**Severity: blocker. Plan/tasks: P1 Tasks 2–3; P5 Task 6.**

The network copy is chunked, but `Runs.deliver` immediately executes `json.loads(Path(path).read_text(...))` (P1:595). The configured allowance is 1 GiB (P1:796), so multiple callbacks can each allocate a complete string plus a decoded object. `Run.result` then keeps the object, and the worker serializes/retains finished results (`server:794–796`, `1563–1569`, `1630–1634`). This does not meet S:78–80/P5:21's no-whole-file/flat-memory promise and is material before reducing the worker to 2 GB.

**Concrete fix:** change the planned run/result interface to file ownership (`result_path` plus small metadata), with the accepted answer atomically stored on the persistent job disk before acknowledgment. Give exact code for bounded validation and streamed result delivery to the site; do not decode every callback in its HTTP handler or retain all completed payloads in RAM. If the existing dict protocol is deliberately retained, replace the 1-GiB cap with an evidenced strict payload bound and bound concurrent decodes; amend the streaming claim and prove the maximum concurrent memory fits before step 5. Add a memory-sensitive test; P1:739–745 only compares response length, so it cannot distinguish `read()` from chunking.

### F09 — Per-kind rollback combinations can leave a local height child unpreempted

**Severity: should-fix. Plan/tasks: P2 Task 3; P4 Task 3.**

P2:572–573 returns from `preempt` solely because control uses workflows, and P2:694–695 disconnects the parse hook on that same condition. During step 2, heights still run locally; the real preempt code deliberately kills a height child when a local parse arrives (`server:1195–1205`). P4 excludes remote victims but retains the early return (P4:410), so `control=workflow, heights=local, parse=local` remains wrong. Conversely, a workflow parse does not need to preempt local computation (S:88–89).

**Concrete fix:** wire the hook according to **parse** locality. For a local parse, always invoke the runner's preempt method; inside it choose only local jobs, keep at most the first local control child, and preempt every local height child. Remove the `control_runner=='workflow'` blanket early return. Add mixed-runner tests that actually start and hold both kinds before submitting a parse, including rollback from workflow heights.

### F10 — Reconciliation implements multiple lists and per-run GETs, without pagination

**Severity: should-fix. Plan/task: P1 Tasks 1–2.**

S:D4 and S:84 require a shared paged list, never polling each run. `recent()` discards cursors and fetches only one page (P1:291–293). `reconcile()` issues one list per kind and calls `get()` for every open id absent from the first page (P1:611–625). A long-running height build can fall beyond one hundred newer control/height starts. FakeRender ignores the query, filtering, limit and cursor (P1:115–123), so the proposed tests do not expose the discrepancy.

**Concrete fix:** provide `recent(slugs, cursor, limit)` returning page items plus cursor; query all required `taskSlug` values together using `urlencode(..., doseq=True)` (SDK supports a list, `SDK/render/public_api/api/workflow_tasks_beta/list_task_runs.py:17/29–33`). Page until the tracked ids are found or the list ends, and remove the per-id fallback. Update the fake to enforce filtering and pagination; assert the exact number/type of calls with an open run behind more than one hundred newer entries. A paged sweep can require multiple HTTP calls; describe that exception accurately instead of promising literally one request for arbitrarily many pages.

### F11 — Pacing can start a run after its deadline; the 429 test spins for twenty seconds

**Severity: should-fix. Plan/task: P1 Task 2.**

`Runs.start` checks the deadline before `Pacer.take`, not after it (P1:547–552). `take` can wait indefinitely relative to that run (P1:322–333). Executed scratch output: `PACER: deadline=1.0 start_times=[61]`.

The test P1:424–430 replaces `sleep` with a no-op but leaves `time.monotonic` real. Each of its two 429s sets a real ten-second hold. It busy-spins through both holds; it does not make the test clock advance.

**Concrete fix:** add a deadline/cancellation argument to `Pacer.take`, stop waiting at the run deadline, and recheck before POST (also bound the HTTP timeout to remaining time). Share a fake clock between `Runs` and `Pacer` in the 429 test. Assert that no start is made after the deadline while preserving 429-as-wait behavior.

### F12 — Serializing sweeps does not serialize expiry against held-job release

**Severity: should-fix. Plan/task: P3 Task 3.**

The revised `_release_held` makes selection/marking atomic (P3:610–619), fixing double release between consumers. But `_sweep` snapshots expired held jobs under the lock and deletes their uploads and changes their state outside it (`server:976–983`). Another parse thread can release that job after the snapshot and start it before the sweeper deletes its file. P3's `sweep_lock` only excludes other sweepers (P3:598–602).

**Concrete fix:** claim expiry under `self.lock` before releasing it: recheck `status=='held'`, transition to an explicit expiring/terminal state, and make release ignore that state. Only that claimant may delete the input and publish the failure. Do not hold the worker lock while acquiring the archive/admission locks. Add a barrier test interleaving held release with expiry and assert exactly one outcome and a balanced parse counter.

### F13 — Cache pruning is not a byte cap and does not pin files for open runs

**Severity: should-fix. Plan/tasks: P2 Tasks 1 and 3.**

P2:186–193 only prunes to three names per map per directory. It never checks total bytes against the 2-GiB reserve, including `.incoming`, feature artifacts and height assets. A permitted 64-MiB cache post (P2:662) times six retained files across twelve maps already exceeds 2 GiB. `inputs()` returns paths to shared cache files (P2:168–177), so later pruning can remove a file while another open run has not yet fetched it. Concurrent stores also race the unguarded `stat()`/prune at P2:157–160/192–193.

**Concrete fix:** introduce a cache-store lock, account installed and in-progress bytes against the fixed reserve, and prune/reject before accepting bytes that exceed it. Pin each run's input snapshot with references or hard links on the same filesystem until run closure; prune only unpinned files. Clean `.incoming` parts on error/startup. Add concurrent upload/download/prune and over-reserve tests, not only three one-byte files.

### F14 — A cache upload admitted before settlement can still install after settlement

**Severity: should-fix. Plan/task: P2 Task 3, cache route.**

`_run_cache` checks `runs_table.accepts` before reading the body (P2:653), then unconditionally stores it after that read (P2:678). The run may settle, time out or be replaced during the transfer. The final store is not guarded by the run lock/token generation. This does not satisfy the stated “already settled is refused” property in P2:27; a delayed retry can change the shared cache after its run is finished. The result route does perform a final `_settle` check (P1:600).

**Concrete fix:** expose `Runs.install_cache(job_id, token, incoming, ...)` that revalidates the current open run and token and commits the cache under the same settlement exclusion, with a fixed lock order for run/cache locks. Remove the incoming file on every exit. Test a gated cache upload whose run settles before its last chunk, asserting 409 and unchanged installed bytes.

### F15 — Feature-row cache updates are discarded; warming can succeed without a shared cache

**Severity: should-fix. Plan/tasks: P2 Task 2 and Task 3; P4 Task 2.**

Both wrappers post only names in `_cache_files(cache) - before` (P2:393–397; P4:269/280). Feature-row caches are updated under the **same** filename: their identity is fixed at `webapp/app/control/feature_visibility.py:132–134`, and `persist` replaces that path when new rows are learned (`327–342`). Those updates never reach the worker. Executed scratch output after replacing an existing file: `CACHE MODIFIED: posted_names=set()`.

Also, a lost cache post is swallowed and the successful/engine result still warms the map (P2:399–403 and unchanged `server:1327–1333`). That can release forty runs which all rebuild the missing visibility cache. The chosen solo-warming guarantee needs acknowledgment of the shared cache, not just an engine status.

**Concrete fix:** snapshot content hashes, upload changed as well as newly named files, and define how concurrent feature-row snapshots are merged or selected without losing useful rows. Include an acknowledged cache manifest in the wrapper answer; mark the warm key only when the worker has the required visibility cache. Add tests for same-name modification, failed cache upload, and warming when the shared file is absent.

### F16 — The worker's new height cap still counts only collecting builds

**Severity: should-fix. Plan/task: P4 Task 4.**

The sole change at P4:610 replaces a constant in `sum(... if b['job'] is None)` (`server:1396`). Started/running builds do not count, and the one-map replacement rule only drops collecting builds (`server:1394–1395`). A changed manifest can leave a running build on a map and open another. The site's `_cancel` is best effort and the worker rejects cancel after start (`webapp/app/services/replay_heights_remote.py:234–242`; `server:1460–1463/1507–1513`). Thus the four-build/one-map bound does not cover outstanding workflow runs when evidence changes or a dispatcher restarts.

**Concrete fix:** count collecting, queued and running builds toward the worker cap and enforce one unfinished build per map. On a stale started build, either cancel its workflow and release its slot only after terminal confirmation, or wait for it to finish before opening that map's replacement. Add an evidence-change-during-run test and a dispatcher-restart test proving at most four unfinished remote builds and one per map.

### F17 — New fixtures leak workers/listeners and the restart test races its own old worker

**Severity: should-fix. Plans/tasks: P1 Task 1/3; P2 Task 3; P3 Task 3; P4 Task 3; P5 Task 2.**

FakeRender.close calls `shutdown` only (P1:135–136). Its listening socket stays open: executed output `FAKE CLOSE: listening_socket_fileno=668`. New live fixtures do the same (P1:726; P2:466; P3:443; P4:346). Harness.close neither closes the listening socket nor stops/joins the infinite worker/scheduler/sweeper loops (P5:131–138; current loops at `server:769–771`, `1240–1244`, `1497–1501`). They can keep touching deleted test directories and restored environment settings.

P3's restart test explicitly keeps the first worker and fake task alive (P3:523–537); fixture teardown opens its gate (P3:442), allowing that task to post to the old worker against the same disk after the second worker has settled. The existing restart test deliberately uses one live Worker because of Windows file locks (`webapp/tests/replays/test_replay_worker_archive.py:119`). P3:475 waits only for the fake task to begin; that happens before the start response/id-save callback (P1:106–113/560–562). Scratch reproduced `ON_STARTED TEST RACE: [('runner_seen', None)] after_return=trn-1`.

**Concrete fix:** add stop/drain APIs for the test harness's workers, scheduler, height sweeper and reconciliation, keep thread handles, close listeners with `server_close`, and join every started thread. Use fixture `try/finally` even when assertions fail. Model restart by terminating an isolated worker subprocess, or by planted durable records plus one live Worker; do not leave two owners of the same job directories. Wait for the durable `run_id` itself rather than task entry, and test polling completion without sleeps.

### F18 — Upload streaming needs length validation and a pre-stream disk reservation

**Severity: should-fix. Plan/task: P5 Task 5; P1 Task 3 result route.**

P5:415–425 checks only an upper bound. Zero length skips the magic check and submits an empty file; negative lengths enter `rfile.read(-1)`, reading until EOF without the size cap. The new result route likewise accepts negative lengths (P1:873–881). Concurrent uploads write complete intake files before entering `submit_path`'s admission/space check (P5:420–442 versus `395–403`). Those files are outside `archive.intake_ok`'s counted jobs/pending/receipts (`archive:206`), so their transient space is unreserved.

**Concrete fix:** validate a decimal Content-Length with `4 <= size <= max_bytes` for `.vrf` and `0 < size <= run_result_max_bytes` for answers; reject invalid syntax/negative/zero lengths with 400 before opening a file. Reserve upload bytes and an admission slot before reading, release them on every failure, and transfer the reservation atomically into the job on success. Make part-file removal unconditional in `finally`, including socket errors and `submit_path` failures. Add zero/negative/nonnumeric lengths and simultaneous pre-admission upload tests.

### F19 — The plans still contain implementation placeholders and incomplete acceptance procedures

**Severity: should-fix. Plans/tasks: P1 Task 5; P2 Task 4/5; P3 Task 1; P4 Task 3/4/5; P5 Task 3/4/5/6.**

These are concrete missing instructions, not requests to redo the architecture:

- P2:779: “Check InFlight's positional fields ... if unchanged.” They are already verifiable: `webapp/app/services/replay_control_remote.py:229–235` matches the six required fields. Replace the instruction with that confirmed signature.
- P3:136: `# ... the moved names here ...`, and P3:189: “Behaviour change to check.” Include the complete moved definitions/imports and the resolved cleanup behavior in the final code block. P3:50's shorthand “REASON_* move out” must mean only REASON_PARSE/REASON_TIMEOUT, the two existing constants (`server:172–173`).
- P4:426 says change two “runs alone” checks but gives only one corrected check. Supply the pending-local-build branch too (`server:1219–1225`), using only local running jobs for its drain decision.
- P4:739–741: “Read the old step side by side” / find other readers “if any.” The search verifies no other application reader outside the replaced `step`; record that result. Correct F04 before claiming one-to-one behavior.
- P5:157–203 gives a general conversion rule and one example instead of edits for all affected helpers/tests; P5:325 leaves fixture `h` unnamed/undefined. F06 lists the actual missing changes.
- P5:405: `# ... the rest of today's submit body ...`; P5:503: copy “any other” ENV lines. Supply the complete `submit_path` and final ENV block. Explicitly retain timeout/max-byte/queue/host/port settings and omit removed local-pool settings.
- P1:1259–1260 says a 400/404 means the slug **or REST path** differs from the SDK. The wheel confirms the paths; replace the speculative instruction with “inspect the response for task slug, registration, or authorization/configuration errors.” No live response was examined here.
- P2:890–891's bare `compute_control.py` command does not select a replay/round and can skip already-current data. Its parser supports `--match`, `--round`, `--force`, `--workers` (`webapp/scripts/compute_control.py:334–339`). Supply a concrete selected-round comparison procedure preserving the workflow output, e.g. run against a separate local snapshot with `--match <uuid> --round <n> --force --workers 1` and compare saved bytes with a specified query/script.
- P4:815 accepts one rebuild, whereas S:114 requires **two maps concurrently and accepted**. Require both maps' verified results, not just two running entries. P5:529 measures five uploads although fifteen parses are configured; specify measurement at the configured concurrency before accepting the smaller worker.
- Each added `@app.task` block must be inserted **before** P1:1131–1132's `app.start()`, and TASKS must follow all referenced body definitions. P2/P3/P4 merely say “add.” The SDK copies the current registry at start (`SDK/render/workflows/app.py:203–214`); a bottom-of-file append is too late for registration.

**Concrete fix:** replace each item above with resolved code, insertion locations, and executable assertions. Deployment observation remains an appropriate owner task; choosing missing implementation is not.

### F19b — Dashboard runner flips conflict with Blueprint-managed local values

**Severity: should-fix. Plans/tasks: P1 Task 5; P2 Task 5; P3 Task 4; P4 Task 5.**

P1:1192–1197 supplies `value: local` for all three runner settings, and later plans explicitly retain those values while directing the owner to flip one setting in the dashboard (P2:866/885–886; P3:727/749; P4:21/792). A subsequent Blueprint sync can reapply the local values, undoing already-activated kinds during the staged migration. The existing deployment guide documents this same interaction for an environment flag (`webapp/RENDER_DEPLOY.md:268–269`). Render confirms that Blueprints can modify existing environment values and ignore `sync: false` entries on updates. [Blueprint environment-variable reference](https://render.com/docs/blueprint-spec#setting-environment-variables).

**Concrete fix:** make the three switches operator-managed (`sync: false`, or omit their values from the Blueprint and rely on the code's local defaults), with explicit initial setup instructions. Preserve the owner’s dashboard values during later step syncs and verify all already-migrated kinds after each merge. P1 already requires manual API-key entry on an existing Blueprint; document the switch ownership alongside that step.

### F20 — Align the written interfaces with the supplied code, and preserve failed-parse timing deliberately

**Severity: nit. Plans/tasks: P1 Task 3; P2 Task 3; P3 Task 1; P5 Task 4.**

P1:683 describes `task_starts_per_min=40`, while its code/test say 1000 (P1:699/795); correct the interface to 1000. S:63 says post control result **then** cache, opposite P2:27/396–403's correct cache-first ordering; correct S:63. S:75–77's `/control_cache/...` routes are replaced by per-run cache routes in P2:650–686; document the actual protocol. S:89/104 names `REPLAY_CONTROL_WORKERS>=40`, but P2 deliberately introduces `REPLAY_CONTROL_RUNS=40` and keeps local workers at two (P2:18), correctly preserving rollback; update the spec to that interface.

The parse extraction also stops recording elapsed time on a nonzero parser exit: current `server:838–841` sets `job.parse_seconds` before raising; P3:165–167 raises before returning seconds and P3:215–217 only copies detail. State that difference or attach elapsed seconds to `ParseFailed` and copy it in both local and remote failure handling. P3:696's “five at once” commit text should say fifteen. P5:303's grep cannot “find only verify_feature_upload's subprocess.run”: `Popen|preempt|idle\(` does not match `subprocess.run`; specify zero executable local-path matches and check feature verification separately.

## Current-code edit/quote audit

All referenced files were opened in this worktree. The following inventory records the actual anchors for every edit family; it distinguishes current code from code introduced by an earlier task. There is no missing quoted eight-line child block: the scratch comparison returned `QUOTED CONTROL CHILD BLOCK matches: True`.

| Plan edit | Actual current text/location or dependency | Result |
| --- | --- | --- |
| P1 Task 3 archive import | `server:162–165`, try `from replay_worker import archive as archive_store` / fallback `import archive as archive_store` | Matches. |
| P1 settings/from_env insertion | `server:209–239`, `archive_total_bytes` at 238; `return settings` at 271 | Exists. Do not remove the `supported_builds` field at 239. |
| P1 handler/server/main signatures | `server:1556`, `1844–1846`, `1849–1859` | Current signatures match the intended additions. |
| P1 health/GET/POST insertions | `server:1595–1608`, `1637–1639` | Health builds `body['control']` at 1600–1602; route insertion anchors exist. |
| P1 Docker install/copy/smoke | `replay_worker/Dockerfile:30–31/43/45` | Pins and COPY line match. Smoke import is one long line; append within its import list. |
| P1/P2/P3/P4 Blueprint additions | `render.yaml:20–61` site env; `64–89` worker definition/env; plan at 73 | Service sections exist. Existing local-pool settings are image ENV (`Dockerfile:68`), not a current YAML entry. |
| P2 archive accounting | `archive:160`, `199–207` | Actual constructor line is `self.held_bytes = lambda: 0 ...`; budget ends `- self.held_bytes())`; intake comparison lacks cache reserve as quoted. |
| P2 ControlRunner edits | `server:1120–1135`, `1190–1208`, `1210–1238`, `1256–1340` | `_next` quoted two assignment lines are 1213–1214, after its comment. Child block is exactly 1273–1280. |
| P2 site constants/send/submit/log | `webapp/app/services/replay_control_remote.py:91/482–548/551–580/673` | `IN_FLIGHT = 8`, `return held_features`, and log string match. Both dispatch kinds use `_send`; the added room check covers both. |
| P2 InFlight “check” | `.../replay_control_remote.py:229–239` | Six quoted positional fields match; additional optional fields follow. No implementation decision remains. |
| P3 moved helpers/local parse | `server:172–173/181/194–205/311–340/817–864` | Named definitions and result shape exist. The extraction changes failed-parse timing (F20). |
| P3 queue/counter/save/recover | `server:353–392/730–800/893–969` | Three `self.parsing=True` writes, one False in `_run` finally, one initialization False; save loop is exactly `('game_build', 'detail')`. Recovery capacity must change (F02). |
| P3 uploader limit | `webapp/app/services/replay_upload.py:66/185–188`; `webapp/app/routers/replays.py:121` | MAX_UNFINISHED is five; message/router read the constant. Existing tests read it (`test_replay_upload.py:132–140`, `test_replay_upload_held.py:60`). |
| P4 pack/task/remote/cache edits | New helpers come from P2 Tasks 1–3, not the checkout | Names/signatures exist by this build order. `what=` must be added before the height task calls it (P4:167–169). |
| P4 local-build checks/health | `server:1217–1225/1605`; height docstring `server:17` | Quoted `body['idle'] = worker.idle()` is exact. Second local-build check lacks replacement code (F19). |
| P4 collection cap/spool | `server:233/1344/1366–1368/1396/1430/1515–1516` | Constant and count match; changing just the threshold still counts only collectors (F16). |
| P4 HeightState/step | `webapp/app/services/replay_heights_remote.py:116–130/401–466` | Actual dataclass fields are `build`, `tries`. Existing methods must be retained. Existing step differs from proposed completion flow (F04). |
| P5 image clone/entrypoint | `replay_worker/Dockerfile:73–74`; `replay_worker/entrypoint.sh:8–18` | ENTRYPOINT/CMD match. Without archive env, chown is skipped; privilege drop executes the supplied command. |
| P5 “unchanged fixture conversion” | `test_replay_worker.py:54–69`, `test_replay_worker_archive.py:36–41`, `test_replay_worker_held.py:39–45`, `test_replay_worker_control.py:48–61`, `test_replay_worker_heights.py:42–56` | Some are helpers/direct constructors, not one swappable fixture; bodies also need edits (F06). No fixture `h` exists. |
| P5 submit/upload replacement | `server:428–455/1654–1668` | Quoted `write_bytes(body)`, hash/size construction, and full read exist. Plan omits full replacement body (F19). |
| P5 slim retained dependencies | `server:1056–1091`, especially interpreter at 1077; Docker copies at 35–42 | Control venv and app/control dependency are real. Keep `_child_setup` for feature verification even after deleting local runner paths. |
| P5 README opening | `replay_worker/README.md:3–6` | Wording exists across a line break: “It parses one / uploaded .vrf at a time ...”. |

The literal/current-text discrepancies are the nonexistent `h` fixture and the claim that existing fixtures/test bodies can be used unchanged (F06); the incorrect P2 run-folder/cross-filesystem description (F07); the unprovided second build-check replacement and generic placeholders (F19); and the grep's impossible expected match (F20). The spec/interface disagreements concern proposed code, not stale current-source quotes.

## Cross-plan interface audit

| Interface | Verified agreement or mismatch |
| --- | --- |
| `RenderApi`, `RateLimited`, `RenderError` | Constructor/start/get/cancel uses agree through all plans. Paged shared reconciliation is missing (F10). |
| `Pacer` | `take()/hold()` calls agree, but lack deadline control (F11). |
| `Run` / `Runs` | `open/start(wait,on_started)/wait/accepts/file/deliver/status/reconcile_forever` names and actual call signatures agree. The record lacks durable start/result/deadline fields required by P3's guarantees (F01/F03); `open_runs` is only in memory. |
| `make_runs` | Settings fields agree through step 4. P5's unconditional credential requirement conflicts with retained P1 no-key tests and must be migrated (F06). All supplied implementations use `temp_root/'runs'`; none performs P2's promised disk move (F07). |
| `ControlRunner.remote/_run_remote` | Added in P2 before P4/P5 use. P4 correctly maps previous height to `inputs(..., {'height': previous})`. P5 leaves callers/tests of removed constructor parameters and attributes (F05/F06). |
| `control_cache.*` | `inputs/valid/store/RESERVE` exist in P2 before later calls; `pack/unpack` are introduced before use in P4. Cache route name is `/runs/{job}/cache/{quoted-relative-name}`, differing from S (F20). |
| `tasks._run_child/_cache_files` | Added by P2; P4 changes signature to include `what='control child'` before its call with `what='height child'`. These names agree. `_cache_files` is a set of names, inadequate for updates (F15). |
| `parse_job.parse_file` | P3's local and task calls match its keyword-only signature and two-value return. The plan's optional “step 2 need not be merged” would require adding `sys`/WEBAPP independently, but the requested build order already supplies them. Server re-export tests conflict with P5 alias removal (F06). |
| Settings | `control_max_runs`, `control_max_matches`, `heights_max_builds`, `control_runs`, `parse_parallel`, `parse_run_timeout_s`, `height_builds_max`, `height_spool_bytes` spellings and call sites agree. P1 interface default forty disagrees with code 1000; S's local-pool variable disagrees with P2's separate remote count (F20). |
| `HeightState.builds` | P4 keys by manifest and supplies the read-only single-build compatibility property. No other application reader of `hstate.build`/`heights.build` exists outside the replaced step; existing tests read it. Completion flow has a same-cycle reopen bug (F04). |
| task registration | Task wrappers use `ctx, args`, matching `[args]` REST inputs and SDK signature. Exact insertion before `app.start` must be stated (F19). |
| deploy order | P5 explicitly gates 5b on 5a's separate workflow Dockerfile and a successful ping (P5:58–72/525). This ordering is correct and must remain an operational gate even if all code is prepared in one session. |

## Per-test audit

`F→P` below means the test is structurally expected to fail before its target implementation and pass afterward, **by inspection**, not an assertion that the proposed suite was executed. Fixtures/helpers were checked against the current file or earlier task's supplied code. All localhost fixtures share the teardown defects in F17 unless corrected. Line numbers identify each exact test in its plan.

| Plan:line, test name (without `test_`) | Before/after, fixtures, and limitations |
| --- | --- |
| P1:167 `start_get_cancel_recent` | F→P; `render` exists in Task 1. Fake does not enforce real request paths beyond the broad branches or HTTP 202. |
| P1:179 `429_is_rate_limited_and_404_is_render_error` | F→P; valid fixture. |
| P1:188 `unreachable_is_render_error` | F→P; assumes localhost port 9 is closed. Bind/close a controlled socket or fake urlopen to avoid dependence on another service. |
| P1:205 `pacer_allows_per_min_then_waits_for_the_window` | F→P; fake Clock supplies both clock and sleep correctly. |
| P1:215 `pacer_hold_pushes_the_next_start` | F→P; same valid fake clock. |
| P1:384 `first_valid_result_wins` | F→P; valid make_runs/write helpers; proves sequential delivery only. Scratch confirmed 409/200/409. |
| P1:397 `a_result_that_is_not_a_json_object_is_400` | F→P; valid helper; malformed/non-object cases checked. |
| P1:407 `files_only_by_name_and_token` | F→P; valid helper; proves exact file allowlist. |
| P1:417 `one_open_run_per_job` | F→P; valid helper; leaves run cleanup to fixture/process. |
| P1:424 `429_waits_then_starts` | Passes only after real-time busy spin; fix clock, F11. No all-the-way-to-deadline test. |
| P1:433 `render_down_settles_infra` | F→P subject to port-9 assumption; no ambiguous accepted-start scenario. |
| P1:441 `on_started_gets_the_render_id_before_start_returns` | F→P, but “before start returns” does not mean before remote execution or durable start intent; F01/F17. |
| P1:449 `deadline_cancels_and_settles_timeout` | F→P; bounded 0.3-second deadline, no pacer contention case. |
| P1:457 `reconcile_settles_a_run_that_ended_without_a_result_after_grace` | Can race FakeRender's default runner overwriting `failed` with `completed` after the test assignment; both end states settle but its final error assertion requires `failed`. Gate task completion or make status transitions deterministic. |
| P1:470 `reconcile_leaves_running_runs_and_a_result_in_grace_wins` | F→P; fake can already be completed at first reconcile, so does not establish the “running” part. |
| P1:485 `status_counts_open_runs` | F→P; valid helper. |
| P1:696 `settings_from_env_defaults_local` | F→P in step 1; intentionally incompatible with step 5, F06. |
| P1:702 `settings_refuse_an_unknown_runner` | F→P; removed setting requires test retirement in step 5. |
| P1:707 `workflow_runner_without_key_or_slug_stops_startup` | F→P; tests missing both, not key-only/slug-only separately; update in step 5. |
| P1:712 `no_key_means_no_run_table` | F→P through step 4; fails by design after P5; migration absent. |
| P1:739 `input_route_streams_the_named_file_only` | F→P; fixtures and `call` exist; downloading a three-MiB response does not prove bounded worker reads/memory. |
| P1:750 `result_route_takes_the_first_valid_post` | F→P; valid sequential HTTP/token test; add simultaneous posts. |
| P1:759 `health_names_the_runners_and_the_workflow` | F→P through step 4; P5 removes fields it reads, F05/F06. |
| P1:1005 `ping_end_to_end` | F→P; `render/live/call` supplied before Task 4. `call` times out in ten seconds although route's deadline is 300; fine only for the quick fake. |
| P1:1015 `worker_link_accepts_a_bare_host_port` | F→P; valid monkeypatch and args. |
| P2:84 `inputs_are_the_maps_newest_caches_its_height_and_features` | F→P; touch helper exists. Covers synthetic short height/artifact ids, not validation at HTTP ingress. |
| P2:99 `inputs_skip_a_missing_height_or_features` | F→P; no fixture mismatch. |
| P2:103 `valid_names_only_for_the_runs_map` | F→P; add backslashes/drive paths, folder-specific hash lengths, and non-ASCII/wrong tokens at routes. |
| P2:111 `store_replaces_atomically_and_prunes_to_kept` | F→P; one-byte files test retention only, not atomic observation/concurrency/byte reserve. |
| P2:122 `archive_budget_leaves_the_cache_reserve` | F→P; real Archive signature matches, `archive:152`; setting an attribute before modification would not change old budget. |
| P2:313 `control_task_mirrors_caches_runs_the_child_and_posts_new_caches_first` | F→P; stub/FakeLink valid. Tests new names only; same-name changes missed. |
| P2:324 `control_task_passes_an_engine_failure_through` | F→P; valid stub. |
| P2:330 `control_task_reports_a_child_that_prints_nothing_as_infra` | F→P; valid silent Python child; no retry/child timeout test. |
| P2:479 `rounds_run_as_workflow_runs_and_a_built_cache_lands_on_disk` | F→P; remote fixture/helpers valid. No actual engine equivalence is established by the stub. |
| P2:491 `engine_failure_settles_engine` | F→P; valid fixture. |
| P2:497 `a_parse_does_not_preempt_workflow_runs` | Passes even on an empty old local runner: it starts no jobs or parse. Not a regression test for preemption. |
| P2:502 `runs_ignore_the_local_pool_size` | Can pass with the old one-/two-child limit because it only waits for six jobs eventually to finish. Gate tasks and assert simultaneous starts above two. |
| P2:510 `cache_route_refuses_other_maps_and_paths` | F→P; direct `ControlJob('jid','key','Ascent',b'{}')` matches current fields (`server:1017–1030`); manually opened run never closed. Add teardown and late-token test. |
| P2:530 `local_mode_keeps_the_local_pool_even_with_control_runs_set` | F→P only for missing `remote`; assertion proves locality, not a two-child pool. Test measured overlap during rollback. |
| P2:749 `another_match_in_flight_holds_this_one_back` | F→P; existing FakeWorker/factory/db/linked and required InFlight fields match. |
| P2:760 `the_runs_limit_caps_one_match` | Existing IN_FLIGHT already enforces the selected cap, so the behavior assertion can pass before the change. The new monkeypatch of absent MAX_MATCHES is the only pre-change failure. Still useful as a combined-limit regression. |
| P2:770 `room_for` | F→P; State and InFlight shape valid. Add two real replays and a 45-round test to cover refill/drain behavior. |
| P3:74 `server_still_exports_the_moved_names` | F→P after extraction; contradicts P5 alias deletion unless changed. |
| P3:80 `a_parser_that_exits_nonzero_is_parse_failed_with_its_last_lines` | F→P; writes real stub, correct parse_file keyword arguments. |
| P3:91 `a_parser_that_hangs_is_parse_timed_out` | F→P; uses current bounded kill_tree including Windows taskkill (`server:311–329`); one-second timeout, real process. |
| P3:297 `parse_refuses_another_recipe` | F→P; FakeLink/_recipe monkeypatch supplied; no parse call asserted, add that assertion. |
| P3:304 `parse_reports_a_parse_failure_with_detail` | F→P; patches parse_job.parse_file, the exact module read by the task. |
| P3:316 `parse_posts_the_result` | F→P; stub bypasses actual condenser bytes and settings propagation. |
| P3:456 `five_uploads_parse_at_once` | F→P with five threads; valid fixture, but does not test the required fifteen. Gate timeout is only thirty seconds; assert max active, not only list length. |
| P3:470 `the_run_id_is_saved_before_the_result` | Flaky: `seen` precedes on_started/id persistence; F17 reproduced that ordering. Poll the durable record. |
| P3:483 `a_deploy_refusal_waits_and_starts_a_new_run` | F→P; valid fixture/monkeypatch; no total deadline/retry exhaustion covered. |
| P3:497 `failures_keep_todays_reasons` | F→P; sequential submissions make pop order stable; covers parse/time/contract messages, not wrong-match reparse rejection. |
| P3:516 `a_restart_reconnects_to_a_running_parse` | Two live owners and no reconciliation loops; F17. Does not cover accepted-start ambiguity, dead recovered run, overflow or durable result recovery. |
| P4:71 `pack_and_unpack_round_trip` | F→P; Path/tar helpers valid. |
| P4:85 `unpack_refuses_entries_outside_the_folder` | F→P for ../, absolute path and symlink; traversal/absolute cases reproduced. Add hard links, device/FIFO entries, Windows drive/backslash cases, and valid in-tree symlinks (links are forbidden by contract). |
| P4:101 `pack_of_a_missing_folder_is_value_error` | F→P; does not test a spool disappearing during pack or missing one manifest round. |
| P4:231 `heights_task_unpacks_the_rounds_and_passes_previous` | F→P; FakeLink/stub valid. Previous id `d0` works in direct wrapper test but is invalid through HeightBuilds.start's twelve-hex check (`server:1455`); add a full-route previous-asset test. |
| P4:243 `heights_task_refuses_a_bad_tar_as_infra` | F→P; FakeLink valid; import tarfile is specified at P4:291. |
| P4:371 `a_build_runs_as_a_workflow_run` | F→P; open/add/start signatures and hex MATCH match current code; stub does not verify actual height result or site acceptance. |
| P4:381 `a_remote_build_does_not_block_control_or_get_preempted` | Does not submit a control job or wait until the height run starts; zero preemptions can happen on the old pending/finished build too. Gate build start, enqueue a control round, and assert overlap and unchanged height status/token. |
| P4:490 `health_reads_idle_during_a_parse_when_builds_are_remote` | F→P; fixture yields worker/base as used. Direct idle monkeypatch is a sound unit test, not an actual parallel-parse integration. |
| P4:529 `collecting_builds_are_capped_by_the_setting` | F→P; worker fixture accepts overrides (`test_replay_worker_heights.py:49–55`), shared MANIFEST is allowed by open. Does not test active builds, F16. |
| P4:537 `spool_bytes_come_from_the_environment` | F→P; both env names match supplied Settings fields. |
| P4:556 `two_due_maps_build_at_once` | Helpers/rig.settings/clone exist. `ControlHeight.count()==2` is useful, but `heights_live==2` can be satisfied by F04's duplicated count before the second distinct result; fix dispatcher first and wait on distinct stored map names. Rig is local mode, so this tests simultaneous open builds, not remote compute overlap. |
| P4:567 `the_cap_opens_one_and_holds_both` | F→P; valid factory/session/clone helpers, held set assertion checks both maps. |
| P4:580 `one_maps_failure_leaves_the_other_building` | F→P; one_round_batches and live settings exist. Checks collecting timeout isolation, not failure of a running remote map. |
| P5:344 `upload_streams_and_hashes` | Missing h fixture, F06. Current full-read implementation already computes the same hash; does not prove streaming. Supply a guarded reader/memory assertion. |
| P5:352 `a_bad_magic_leaves_nothing_behind` | Missing h fixture; otherwise ordinary bad magic is useful. Need deterministic socket teardown. |
| P5:358 `a_short_upload_leaves_nothing_behind` | Missing h fixture plus client/server mutual wait and timeout race; replace client with explicit half-close, F06. |

Changed existing-test groups also need explicit attention: P2's autouse `old_limits` fixture at P2:738–741 makes pre-change tests fail during setup on missing MAX_MATCHES, so P2:784's “old ones pass” is not literal. P3's upload-limit tests use MAX_UNFINISHED and can remain structurally unchanged (source locations in quote audit). P4's read-only single-build property supports current readers, but F04 changes behavior. P5's deletion list must not remove tests of retained feature verification, height/queue protocols, or kill-tree behavior in the task; move those assertions to the correct layer. The slim Dockerfile also contradicts existing text assertions for `python3-venv`, gap smoke imports, `/jobs/control_cache`, and REPLAY_HEIGHT_CMD (`test_replay_worker_control.py:352–360`); revise them to assert the two images' new responsibilities, not just drop the checks.

## Correctness checks and spec coverage

Sequential wrong/duplicate result tokens are handled by the proposed final `_settle` lock; scratch obtained 409, 200, 409. Input lookup is an exact name allowlist rather than path concatenation (P1:585–587), so encoded `../job.json` does not escape it. Tar unpack pre-validates every entry's resolved destination and file/directory type, then uses `filter='data'` (P4:129–140); the traversal/absolute examples were refused in scratch. These are sound foundations. The missing race, durability, capacity and memory tests are identified above.

| Decision | Tasks that implement it; uncovered or conflicting part |
| --- | --- |
| D1 worker remains site's contact/archive owner | P1 Tasks 2–4; P2 Task 3; P3 Task 3; P4 Task 3. Durable parse handoff still needs F01/F02. |
| D2 per-kind switches through step 4 | P1 Task 3 and Tasks 5 in P1/P2/P4, P3 Task 4; removed in P5 Task 4/6. Mixed locality violates intended behavior (F09); Blueprint values can reset manual activation (F19b). |
| D3 private input/result transfer | P1 Task 3/4; wrappers in P2/P3/P4. Routes exist by build order; actual private-network reach remains an owner deployment check. |
| D4 first valid push; shared reconcile | P1 Task 2/3. Sequential result behavior covered; durable acknowledgment, late cache token and shared pagination missing (F01/F10/F14). |
| D5 one replay, forty rounds, tunable | P2 Task 4/5. Required Settings names agree; actual 45-round refill and two-replay drain integration test missing (F19/test audit). |
| D6 persistent caches, warm alone, newest three, reserve | P2 Tasks 1–3/5; P4 wrappers. Byte limit, pinning, same-name updates and cache-ack warmth missing (F13/F15). Spec route/order needs alignment (F20). |
| D7 all tasks flex; compare parse speed | P1 Task 4's `default_plan='flex'`; later registrations inherit it; P3 Task 4 supplies comparison. No finding disputes the selected task size. |
| D8 timeouts | P2/P4 registration and worker timeout; P3 parser timeout and longer run envelope. Align S's decorator wording and preserve overall deadline (F03). |
| D9 retries parse=0/control=1/heights=0 | P1 NO_RETRY; P2:416; P3:378; P4:296. SDK signatures correct; restart can still execute another parse (F01). |
| D10 safety valve 1000, 429 waits | P1 Tasks 1–3/5. Fix deadline-aware waiting and inconsistent interface default (F11/F20). |
| D11 recipe skew with bounded defer | P3 Task 2/3. Explicit refusal is present; whole-job deadline is not (F03). |
| D12 runs/worker have no DB credentials | P1 task link and image/setup tasks; child modules `replay_worker/control_job.py` and `height_job.py` use app computation, not DB sessions. No added credential path was found. Live service env was intentionally not inspected. |
| D13 stdlib worker REST; SDK only task registration | P1 Tasks 1/4/5. Imports adhere to this division; actual Docker build remains unverified. |
| D14 retain synchronous feature verification/control venv | P5 Task 6 and retained `server:1056–1091`. Keep `_child_setup`; missing feature integration-test conversion in F06. |
| D15 fifteen parses/queue; ten per uploader | P3 Tasks 3–4. Constants/env supplied, but active-plus-queued admission and recovery are wrong (F02); tests exercise five. |
| D16 four maps, persistent bounded spool, idle bypass | P4 Tasks 3–5. `/health` idle override is supplied correctly; persistent spool/total byte accounting and active/per-map cap need F07/F16; completion flow needs F04. |

| Step “done when” | Task coverage and gap |
| --- | --- |
| 1 deployed ping round-trip | P1 Task 5 Step 7. Local fake and CLI provided; live deployment is not part of this read-only review. |
| 2 control workflow, equal sample bytes, local rollback | P2 Task 5 Steps 2/5. Provide the selected-round comparison command and substantive rollback/concurrency test (F09/F19). |
| 3 parallel parsing, restart reattach, timing comparison | P3 Task 4 Steps 2/4; claimed by Task 3 tests. Durable/recovery invariants remain unproved and broken (F01–F03/F17). |
| 4 two concurrent maps accepted | P4 Task 5 Step 3 is weaker: one accepted plus two shown running. Require both accepted concurrently, F19. |
| 5 parser-free worker, workflow-only, smaller plan | P5 Tasks 1/4/5/6, 5a before 5b. Missing migrations/runtime refs and actual configured-load memory proof prevent completion (F05–F08/F19). |

## Executed checks and Render verification

The isolated script reads fenced snippets into scratch-only modules and imports the current stdlib worker with bytecode disabled; it does not edit/replace repository code or read environment files. Command:

`C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -B <scratch>/checks.py`

Relevant output:

```text
PACER: deadline= 1.0 start_times= [61]
CACHE: retained_bytes= 3072 with_32_feature_rows_and_KEPT= 3
CACHE MODIFIED: posted_names= set()
RESULT: 409 200 409
TAR: ../evil refused
TAR: /abs/evil refused
CURRENT Worker._next calls self.lock: False
RECOVERY: accepted_records=30 queued= 15 failed= 15
FAKE CLOSE: listening_socket_fileno= 668
QUOTED CONTROL CHILD BLOCK matches: True
HEIGHT STEP: advances_for_one_finished_build_in_one_cycle= ['a', 'a']
ON_STARTED TEST RACE: [('runner_seen', None)] after_return= trn-1
DONE
```

Downloaded the public wheel using `python -m pip download --no-deps --retries 0 render==1.0.1 --dest <temporary folder>`. It succeeded; no installation into the repo was made. SHA-256: `db3b8004a9a7b4f2541b6127c6dd097e870bf83f016ed0065d5eecac73b9bb07`.

| Fact | Verified source in render 1.0.1 |
| --- | --- |
| Base `/v1`, Bearer header | `render/client/client.py:89–90`; `render/public_api/client.py:179–180`. |
| POST `/task-runs`, body `task`/`input`, accepted response | `render/public_api/api/workflow_tasks_beta/create_task.py:20–25/36–39`; `render/public_api/models/run_task.py:25–44`. Response is HTTP **202**, while FakeRender returns 200; urllib accepts either success status. Make fake fidelity exact. |
| GET `/task-runs/{id}` | `render/public_api/api/workflow_tasks_beta/get_task_run.py:16–18`. |
| DELETE `/task-runs/{id}`, success 204 | `render/public_api/api/workflow_tasks_beta/cancel_task_run.py:15–17/26–28`. |
| GET `/task-runs`, `taskSlug`, limit, cursor | `render/public_api/api/workflow_tasks_beta/list_task_runs.py:13–33/61–64`. taskSlug supports a list. |
| Envelope `taskRun`, `cursor` | `render/public_api/models/task_run_with_cursor.py:33–37/47–49`. |
| Status spellings | `render/public_api/models/task_run_status.py:5–11`: canceled, completed, failed, paused, pending, running, succeeded. Matches P1's spellings. |
| `Workflows(default_plan=...)` | `render/workflows/app.py:72–78`. |
| `@app.task(name=, retry=, timeout_seconds=, plan=)` | `render/workflows/app.py:120–128`. |
| `Retry(max_retries=, wait_duration_ms=, backoff_scaling=1.5)` | `render/workflows/task.py:30–36`. |
| ctx excluded from supplied inputs; startup registry | `render/workflows/task.py:142–152`; `render/workflows/app.py:203–214`. |

The official [Render Python SDK reference](https://render.com/docs/workflows-sdk-python) also documents the decorator arguments, leading TaskContext, positional-list inputs, and inherited default plan. No real API request was used to confirm behavior.

## Unverified items and open questions

- **[unverified]** Live private-network reach, actual workflow slug/registration, all deployment smoke checks, measured parser speed and configured-load worker memory: require the owner's Render deployment. No credentials or real API calls were permitted.
- **[unverified]** List ordering is asserted newest-first in S:84; the inspected generated SDK exposes cursors/filters but does not establish that ordering guarantee. Do not make correctness depend on an undocumented order; page to all tracked ids or verify the API guarantee separately.
- **[unverified]** Docker image builds, binary-wheel availability in the actual Linux runtime, and feature verification in the slim image: no Docker build was run. The code/import dependencies and smoke-test text were inspected.
- **[unverified]** Full pytest execution: this worktree has no `webapp/.venv/Scripts/python.exe`; the bundled interpreter reported `ModuleNotFoundError: No module named 'pytest'`. The parent checkout's virtualenv was not used. All planned test bodies/fixtures were audited and the listed stdlib scratch checks were executed; no suite-pass claim is made.
- Open protocol question if continued automatic recovery from an ambiguous start is required: what verified Render idempotency/recovery mechanism can identify a run accepted before its response was saved? Until one is established, preserve the durable start fence and never issue a second start for that ambiguous parse attempt (F01).

Only this review is intended as a new repository file, left uncommitted. The approved worker-as-contact architecture, disk choice, no-DB boundary, limits, and task sizing remain the basis of the review.

## Response (2026-10-14)

Every claim was checked against the code before acting; the spec and all five plans were revised. The owner made
two calls: F01 follows **today's restart rule** (spec D9), and step 5 **keeps the local code** (spec D14).

| Finding | Disposition |
| --- | --- |
| F01 | Partly adopted. Token and one deadline saved before a start, the Render id as soon as known, identity kept until the job's final save; reconnect when the id was saved, else start over with the saved token (P3 Task 3). A durable start/result state machine is out of scope by the owner's call (D9): a rare second execution costs compute only and the token makes the first result win. |
| F02 | Adopted: recovered jobs go to a recovery list worked first, never the bounded queue; the wording says 15 run + 15 wait (D15; P3 Task 3, test with 12 recovered jobs and a queue of 2). The shared admission reservation was not adopted. |
| F03 | Adopted: one `run_deadline` per job, saved and kept across refusals and restarts (D8; P3 Task 3, deadline test). |
| F04 | Adopted: builds looked at in a cycle can't be reopened in it (`attempted`), with a regression test (P4 Task 4). |
| F05, F06 | Moot: step 5 no longer removes local code (D14). P5 rewritten: no test migration; the upload tests have a real fixture and a half-closing client. |
| F07 | Adopted: `REPLAY_HEIGHT_SPOOL_DIR` on the disk, tars beside it, twice the spool cap reserved before the Worker recovers (D16; P4 Task 4); reserves set through `Settings.archive_extra_reserve` before `Worker.__init__` (P2 Task 3). |
| F08 | Partly adopted: result cap 256 MB, sizes logged and measured in step 3, the streaming claim corrected (D18). The file-ownership redesign was not adopted: holding a result in memory is today's behaviour. |
| F09 | Adopted: preemption follows parse locality and only ever touches local children (D17; P2 Task 3, mixed-switch test). |
| F10 | Adopted, lighter: one list over every open kind's slug, following cursors up to 5 pages, then a GET per run still unseen; the fake enforces filtering and cursors (D4; P1). |
| F11 | Adopted: `Pacer.take(stop=...)`, deadline rechecked before every POST; the 429 test shares a fake clock (P1). |
| F12 | Adopted: expiry claims a held job under the lock (`expiring`) before deleting it (P3 Task 3). |
| F13 | Adopted, lighter: a store lock and a byte check against the reserve (507 when full); `.incoming` cleared at startup; a run skips a cache file it can't fetch. Pinning was not adopted (rare, and the run then rebuilds). |
| F14 | Not adopted: cache files are named by their content's identity, so a late one is still correct; the wording that overclaimed was corrected (D6). |
| F15 | Adopted: changed files (by hash) are posted as well as new ones; a map is warm only once its visibility cache is on the worker's disk (D6; P2 Tasks 2-3). |
| F16 | Adopted: the worker counts every unfinished build, and one per map (503 to the site: busy, no try spent) (P4 Task 4). |
| F17 | Adopted, lighter: every fixture closes its server with `server_close()` in `finally`; restarts are tested with planted records and one live Worker; the durable `run_id` is polled. Stop/join APIs for worker threads were not added. |
| F18 | Adopted: `Content-Length` validated on every body route (`_length`); intake checked before streaming; the upload streams into the job's own folder, deleted on any failure (P1, P5). |
| F19 | Adopted: each placeholder replaced (moved-lines list, both build checks, the compute comparison procedure, task registration above `app.start()`, two-map acceptance, measurement at the configured concurrency). |
| F19b | Adopted: the three switches are `sync: false` (D2; P1 Task 5). |
| F20 | Adopted: interface defaults, cache-first order, route names, `REPLAY_CONTROL_RUNS`, failed-parse `parse_seconds`, commit text. |

Execution note: the worktree has no `webapp/.venv`; tests run with the main checkout's interpreter
(`C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv\Scripts\python.exe`) from the worktree's
`webapp\`, or a venv created in the worktree first.

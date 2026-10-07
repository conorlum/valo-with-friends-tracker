# W1 review findings: worker gaps + kill-one (plan A), auto re-parse queue (plan B)

Reviewed 2026-10-07 against base commit `d05a480` (origin/main `7c1f255` plus the docs commit). Read-only; no
tests run. All `path:line` references are to files at `d05a480`.

Note for the orchestrator: the worktree changed while I was reading. Commit `1c8e97a` (plan A task 1) landed
and plan A task 2 edits were uncommitted in the tree (`replay_worker/control_job.py`, `server.py`,
`Dockerfile`, `app/control/task.py`, three test files). I checked evidence with `git show d05a480:<path>`
where it mattered, so nothing below describes the in-progress build.

Plan lines: "A:NNN" is `2026-10-07-worker-gaps-and-kill-one-impl.md`, "B:NNN" is
`2026-10-07-auto-reparse-queue-impl.md`.

## Verdict, plan A

Buildable. Tasks 1, 2 and 5 carry real code and their anchors, fixtures and test names exist on the base
(`runner`, `task`, `wait_all`, `wait_until`, `overlaps`, `_task(tmp_path, monkeypatch, **extra)`,
`CONTROL_TABLES`, `cf.figures_hash`, the D4 row, `## Configuration`). The new `preempt`/`_next` code is
correct against `ControlRunner` as it stands, and the four replacement tests exercise what they claim. No
blocker. The weak part is tasks 3-4: the rewrite kept interface names from the deleted code (`_send`,
`_sendable(state, now, planned, gaps_only, gap_prints, ...)`) without defining them, leaves the task-key
formats open in a way that silently breaks existing assertions, and puts one test in task 3 that only task 4
can make pass. Review findings 1 and 4 are resolved in substance (capability gate, guarded writer under the
replay lock), but only as prose: no test is named, so nothing but a reader can confirm it.

## Verdict, plan B

Buildable after fixes; one blocker. Task 2 is concrete and correct against `store_replay` (the replacement
range matches `store.py:92-125` exactly, including the `existing =session` typo; the two "already pass" tests
do pass on the base). Tasks 1 and 3-8 are prose. The blocker: migration `0018` adds a mapped column, and the
PostgreSQL tests run on a schema nobody recreates, so every pg test that loads `ReplayUpload` fails until the
test database is upgraded, and no step does that. Beyond it: the worker attempt protocol has no wire
contract, a helper the task 3 collector needs is created in task 4, the clock type handed to `step` is
undefined, and six of eight tasks have no exact check command. Review findings 2, 3, 5 and 6 are resolved in
prose and the listed tests cover the right cases; none is named.

---

## Plan A findings

### A-1. Task keys, and what `in_flight` / `tries` are keyed by, are undefined; existing assertions break silently
- **Severity:** should-fix
- **Task:** A task 3 step 4 (A:578), task 4 step 2 (A:614), task 4 step 4 (A:626)
- **Evidence:** A:578 "A full task containing gaps uses a distinct key including protocol 1 and the gap
  fingerprint"; A:614 "Use a prefix distinct from both plain and full-control tasks". On the base one string
  is the worker key, the `state.in_flight` key and the `state.tries` key
  (`webapp/app/services/replay_control_remote.py:131-132, 223-224, 249`). Existing tests parse it by position:
  `webapp/tests/replays/test_control_remote.py:63` and `:81` (`int(t["key"].split(":")[1])` as the round),
  `:228`, `:238`, `:240`; `:157` fills `state.tries` with `remote.task_key(...)` and `:160` expects nothing sent.
- **Problem:** (1) A prefix-first key makes `split(":")[1]` return the replay id, which is an integer too, so
  `sent_rounds` keeps passing or failing for the wrong reason. (2) If `in_flight` stays keyed by the worker
  key, a round in flight as a plain task is planned again as a full task the pass after the worker's
  capability flips (different key, so "not in flight"): two jobs for one round, the second's gaps discarded
  because control reads "already stored". (3) Whether `tries` is per worker key or per round decides whether
  `test_exhausted_retries_dont_defeat_the_throttle` survives a capable fake. (4) The fake worker's default
  capability is not stated, and it decides whether about twenty existing tests change.
- **Fix:** Add to task 3's Interfaces: plain key `"{replay}:{round}:{fingerprint}"` (unchanged); full key
  `"{replay}:{round}:{fingerprint}:g1:{gap_fingerprint}"`; gaps-only key
  `"{replay}:{round}:{fingerprint}:go1:{gap_fingerprint}"` (suffixes, so the first three fields keep their
  positions; the worker dedupes on the whole string either way). `state.tries` is keyed by the worker key;
  a round is excluded from planning while any `InFlight` has its `(replay_id, round_number)`. `FakeWorker`
  defaults to no `gaps_protocol` (a legacy worker), so the existing tests stay unmodified and are the
  old-worker regression; capable behaviour is opt-in per test. State whether the forced plan and `plan_gaps`
  run at all without the capability (recommend: no, skip them).
- **Needs the owner?** No.

### A-2. A task 3 test can only pass after task 4
- **Severity:** should-fix
- **Task:** A task 3 step 1 (A:552)
- **Evidence:** A:552 "Later health reporting protocol 1 causes the missing gap runs to be scheduled without
  resetting the site process." Task 3 sends gaps only inside a full control task; the rounds in that test
  already have current control (stored plain), and the only thing that schedules gaps for them is the
  gaps-only planner of task 4 (A:594-616; `plan_gaps`, `webapp/app/services/replay_gaps.py:90`).
- **Problem:** Written first as task 3 says, the test stays red at task 3's commit, so the task cannot be
  committed green as written.
- **Fix:** Delete that sentence from A:552 (task 4 step 1, A:608, already has it). In task 3 keep only:
  "after health reports protocol 1, rounds still needing control are sent as full tasks".
- **Needs the owner?** No.

### A-3. `_send` and `_sendable` are named but never defined
- **Severity:** should-fix
- **Task:** A task 3 step 4 (A:578), task 4 Interfaces (A:602)
- **Evidence:** A:578 "carry the capability through `_submit` and `_send`"; A:602 "`_sendable(state, now,
  planned, gaps_only, gap_prints, ...) -> (todo, keys)` and `_send(...)`, preserved for the second plan's
  hold-back integration". The base has neither; planning and sending are one function
  (`replay_control_remote.py:216-250`). B:377 depends on `_sendable(..., held=frozenset())`.
- **Problem:** The parameter names are left over from the deleted code (`planned`, `gap_prints`, `keys`), the
  list is elided, and task 3 uses `_send` a task before it is introduced. Plan B's hold-back needs one
  filter that covers both task kinds.
- **Fix:** Define both in task 3's Interfaces. `_sendable(state, now, rounds, *, kind, held=frozenset()) ->
  list`: the planned rounds of `kind` ("control" or "gaps") that are not held, not in flight for their round
  and pass `may_try` for their worker key. `_send(session, client, state, now, counts, rounds, *, kind,
  expect_gaps) -> bool`: submits in `_order` until `IN_FLIGHT`, busy or unreachable; returns whether
  sendable work was left (it sets `state.last_found`). Task 3 creates both with `kind="control"`; task 4 adds
  `kind="gaps"`; plan B adds the `held` argument's caller.
- **Needs the owner?** No.

### A-4. No test enforces the isolation rule on the module tasks 3-4 rewrite
- **Severity:** should-fix
- **Task:** A task 3 (files), task 3 step 5 check
- **Evidence:** `webapp/app/main.py:33` imports `replay_control_remote` inside `lifespan`, so
  `test_the_web_app_does_not_import_the_engine` (`webapp/tests/replays/test_control_isolation.py:28-35`, which
  only runs `import app.main`) never loads it. The static list at `test_control_isolation.py:59-66` names
  `replay_gaps.py`, `replay_gaps_store.py` and others, not `replay_control_remote.py`.
- **Problem:** Task 3 validates "the current gap revision" (A:570). An implementer who imports
  `app.gaps.detect.GAPS_REVISION` in the dispatcher breaks "the web app never imports `app.gaps`" and the
  named check (`test_control_isolation.py`) still passes.
- **Fix:** In task 3 step 1, add `WEBAPP / "app" / "services" / "replay_control_remote.py"` to the list at
  `test_control_isolation.py:59-66`, and say the dispatcher compares against
  `app.services.replay_gaps.GAPS_REVISION` (`replay_gaps.py:24`).
- **Needs the owner?** No.

### A-5. The pg gate can pass without running a pg test, and "pg" is a substring match
- **Severity:** should-fix
- **Task:** A task 3 step 5 (A:586-588), task 5 step 4 (A:691-695), task 4 step 1 (A:608)
- **Evidence:** `webapp/tests/_postgres.py:45-47` skips without the URL; every pg test on the base is named
  `test_pg_*` (11 in `tests/replays`, for example `test_replay_store.py:311`
  `test_pg_concurrent_stores_of_one_replay_serialise`, the threading precedent) and the plans select with
  `-k "pg"` / `-k "not pg"`. A:608 asks for a test about "Upgrade the same fake worker in place".
- **Problem:** `-q` hides skips, so a missing or misread URL reads as a green run with the interleaving
  tests never executed. And any SQLite test with "upgrade" in its name contains "pg": it is dropped from
  the `-k "not pg"` run and counted in the pg run.
- **Fix:** Task 3: "name the interleaving tests `test_pg_...`, build them on the `pg` fixture
  (`test_replay_store.py:246`) with the two-session pattern of `test_replay_store.py:311`". Add `-rs` to the
  commands at A:586 and A:694 and state the expected result as "0 skipped among `test_pg_*`". Add to Global
  Constraints: "no test name outside the pg tests contains `pg` (so not `upgrade`)".
- **Needs the owner?** No.

### A-6. Merging starts a backlog gaps-only pass on the live worker, and the plan does not say so
- **Severity:** should-fix
- **Task:** A task 5 step 3 (A:680-687) and step 6 (A:704-713)
- **Evidence:** Task 4 sends a gaps-only task for every round whose control is current and whose gap run is
  missing or stale (A:614; `replay_gaps.py:90-118`). The dispatcher is already on in production (design,
  `2026-10-07-auto-reparse-queue.md:390-393`). There is no switch on this path. On the worker a gaps-only
  task has no tick cache, so it runs the engine (`webapp/app/control/task.py:303-313`).
- **Problem:** At merge, with both services deployed, the worker begins an engine run per backlog round,
  one child at a time beside parses. That is the intended design (owner's answer 3), but its size is
  unknown and neither the deploy note nor the live checks mention it. The owner's calibration is that
  recompute timing is theirs to see ("Still give it a card, because recompute timing is mine").
- **Fix:** Add to the deploy note and to the PR draft's live checks: "On deploy the site starts sending
  gaps-only tasks for every stored round with current control and no current gap run; count them first
  with `SELECT count(*) ...` (give the read-only query) and expect `gaps_sent` in the dispatch log until
  it drains." Put it on a decision card.
- **Needs the owner?** No to build; yes as a card, since it runs on the live worker at merge.

### A-7. Step 6A's old-image reproduction names an implementation that no longer exists
- **Severity:** nit
- **Task:** A task 2 step 6A (A:520)
- **Evidence:** A:520 "Use the unchanged task implementation or a frozen test fixture from the base
  revision". Step 3 has already rewritten `_cache_path` (A:364-376). The rewritten function still starts
  with `from app.gaps import cache`, and `_CacheGuard.__init__` imports it too (`task.py:217-218`), so the
  failure is the same.
- **Problem:** Two readings (vendor the base file, or patch the import). Also, nulling only
  `sys.modules["app.gaps.cache"]` does not fail `from app.gaps import cache` once the package has the
  attribute.
- **Fix:** Replace the sentence with: "Reproduce with `monkeypatch.setitem(sys.modules, "app.gaps", None)`
  on a task that has a `gaps` block and no site keys; assert `status == "failed"` and
  `error_kind == "infra"`."
- **Needs the owner?** No.

### A-8. The guarded writer's "explicit local retry" has nothing behind it
- **Severity:** nit
- **Task:** A task 3 step 3, item 4 (A:571)
- **Evidence:** A:571 "(including a current failed run, unless an explicit local retry overrides it)".
  Local callers omit the argument and take the unguarded path (A:574; `scripts/compute_control.py:224`).
- **Problem:** No parameter carries a retry into the guarded path.
- **Fix:** Reword: "The guarded path never replaces a run stored under the same gap fingerprint, ok or
  failed. The unguarded path keeps today's replace."
- **Needs the owner?** No.

### A-9. The server keeps 200 finished results in memory, now with gap rows
- **Severity:** nit
- **Task:** A task 5 step 6 (A:704-713)
- **Evidence:** `replay_worker/server.py:546` `CONTROL_FINISHED_KEPT = 200`; a finished job keeps `result`
  (`server.py:746`), which will include `gaps.rows`.
- **Problem:** The live checks watch the child's memory, not the server process, which shares the 4 GB.
- **Fix:** Add a live check: "the server process's memory after 200 gap-carrying rounds".
- **Needs the owner?** No.

### A-10. Small anchor mismatches in task 1 and task 2
- **Severity:** nit
- **Task:** A task 1 step 4 (A:224, A:250), task 2 step 5 (A:503)
- **Evidence:** The sentences to replace span line breaks (`server.py:20-21`, `:586-587`); the Dockerfile
  comment is three lines above `COPY webapp/app/control`, not two (`replay_worker/Dockerfile`, the
  `# and the control engine` line); the `/health` line of the module docstring (`server.py:10-11`) is not
  updated for `gaps_protocol`.
- **Problem:** None for a careful implementer; an exact-string edit fails.
- **Fix:** Say "the sentence, across its line break" and add the `/health` docstring line to step 6A.
- **Needs the owner?** No.

---

## Plan B findings

### B-1. The PostgreSQL test database is never upgraded to 0018, so the required pg tests cannot pass
- **Severity:** blocker
- **Task:** B task 3 step 1 (B:248-252); it also gates tasks 5 (B:367) and 6 (B:399) and task 8 step 4 (B:464)
- **Evidence:** The `pg` fixture only deletes rows (`webapp/tests/replays/test_replay_store.py:246-264`); pg
  tests run "On the real schema (alembic head, `VALO_TEST_DATABASE_URL`)"
  (`webapp/tests/replays/test_control_store.py:194-195`). Alembic takes its URL from `settings.database_url`
  (`webapp/alembic/env.py:6, 12`), not from the test variable. B:250 adds a mapped `auto_context` column.
- **Problem:** Once the model has the column, any ORM load of `ReplayUpload` on the test database fails
  with an undefined column. The pg tests of tasks 3, 5 and 6 all load it. No step upgrades the test
  database or says how.
- **Fix:** Add to task 3 step 1, before any pg test: "Upgrade the test database: run
  `PY -m alembic upgrade head` with `DATABASE_URL` set from `VALO_TEST_DATABASE_URL` for that one command
  (never printed), after checking the database name ends in `_test`. Confirm `alembic current` reads
  `0018`." Name the migration test `test_pg_migration_0018_adds_a_nullable_context_and_drops_only_it`,
  beside the precedent at `test_control_store.py:194`, and have it leave the database at head. The extra
  nullable column does not disturb the `-1` branch's tests.
- **Needs the owner?** No.

### B-2. The attempt protocol has no wire contract, and its status codes already collide
- **Severity:** should-fix
- **Task:** B task 1 steps 3-4 (B:86-102), task 3 step 2 (B:254-258), task 5 step 1 (B:333)
- **Evidence:** B:100 "returns the receipt identity, state, job id, sha and size, plus job status when
  available. Unknown id is 404"; B:90 "Same id with another identity is 409"; B:256 the client must
  "distinguish unknown receipt, definite refusal, accepted, expired and transport uncertainty". Today
  `POST /reparse` answers 404 for "the archive is off" (`replay_worker/server.py:861`) and for "no archived
  file" (`:876`), 409 for a tombstone (`:878`), 503 for a full queue (`:880`). The client raises one
  `WorkerRefused` for every 4xx (`webapp/app/services/replay_upload.py:150-151`).
- **Problem:** No field names, state names or per-case codes are given. 404 would mean unknown receipt,
  archive off, or file missing; 409 would mean tombstoned, identity conflict, or closed. Three tasks (the
  worker, the client, the fake in task 5) each have to guess, and "do not clear a reservation merely
  because one lookup returned 404" (B:343) depends on telling them apart. The Interfaces block also lists
  the `WorkerClient` methods under task 1 (B:67-72) though task 3 builds them (B:74).
- **Fix:** Add a contract table to task 1 and have tasks 3 and 5 cite it. Every attempt answer is JSON with
  `"code"`: `accepted` (202 first time, 200 on a repeat; `attempt_id`, `job_id`, `match_uuid`, `sha256`,
  `size`, `state: "accepted"`, `job_status`), `unknown_attempt` (404, GET only), `no_archived_file` (404),
  `archive_off` (404), `sha_mismatch` (409), `deleted` (409), `identity_conflict` (409), `closed` (409 on
  POST, 200 on close), `expired` (200, `state: "accepted"`, `job_status: "expired"`), `queue_full` (503).
  The client branches on `code`, never on the status alone. Move the `WorkerClient` signatures to task 3.
- **Needs the owner?** No.

### B-3. Task 3's collector needs the handshake helper that task 4 creates
- **Severity:** should-fix
- **Task:** B task 3 step 3 (B:264), task 4 Interfaces (B:297) and step 3 (B:316)
- **Evidence:** B:264 the shared collector must "Require worker recipe = collecting site's `current_recipe`,
  archive enabled, control enabled, gap protocol 1, and re-parse protocol 1". B:297 creates
  `worker_off_reason(health, recipe)` in the new `replay_reparse_auto.py` in task 4, and B:316 says "Status
  and step must call this same helper". The collector lives in `replay_upload.py`
  (`replay_upload.py:210, 253`), which `replay_reparse_auto` must import for the client and the tag.
- **Problem:** Built in order, task 3 writes its own copy of the check; task 4 then writes a second. Two
  copies of the capability gate is how review finding 5 happened. Importing task 4's helper back into
  `replay_upload.py` is a circular import.
- **Fix:** Move `worker_off_reason(health, recipe)` and the protocol constants to task 3, in
  `replay_upload.py` (or a small `replay_reparse_protocol.py` that both import). Task 4 keeps `off_reason()`
  for the site's settings and imports the worker check. Change B:316 to "collector, step and status call
  this one helper".
- **Needs the owner?** No.

### B-4. The clock handed to `step` and `classify` has no type, and settlement uses the wall clock
- **Severity:** should-fix
- **Task:** B task 4 Interfaces (B:299), task 5 Interfaces (B:328), task 5 step 3 (B:353), task 6
- **Evidence:** The dispatcher's `now` is a float and its tests pass `now=0`, `now=1`
  (`replay_control_remote.py:253-255`; `test_control_remote.py:61, 67`). The upload side's `now` is a
  `datetime` (`replay_upload.py:210-212`), `_finish` stamps `finished_at` from `datetime.now` whatever it
  was given (`replay_upload.py:291-293`), and SQLite returns naive datetimes
  (`replay_upload.py:215` works around it).
- **Problem:** `BACKOFF_S` and `SETTLE_S` compare the injected `now` with `finished_at`. With a float epoch
  from the cycle and a wall-clock `finished_at`, a test at `now=0` never leaves backoff, and one at real
  time cannot step the clock. Tasks 4, 5 and 6 would each pick a convention.
- **Fix:** State in task 4: "`now` is float epoch seconds everywhere in `replay_reparse_auto`; convert once
  with `datetime.fromtimestamp(now, timezone.utc)`; read `finished_at` as UTC when naive. The automatic
  collector passes that datetime to `_finish` (add an optional `now` argument, default unchanged), so an
  injected clock also stamps settlement."
- **Needs the owner?** No.

### B-5. The crash-injection tests assume a second live worker on one disk, which the suite avoids on Windows
- **Severity:** should-fix
- **Task:** B task 1 step 1 (B:80)
- **Evidence:** B:80 "Inject worker crashes after preparing a copied file, after its durable job record,
  after its acceptance receipt, and before replying. On restart, retrying the same id ...". The existing
  restart test builds the disk state by hand: "Written by hand, as a killed worker would leave them (one
  live Worker only: Windows file locks)" (`webapp/tests/replays/test_replay_worker_archive.py:117`).
- **Problem:** An in-process fault hook leaves the first `Worker`'s daemon threads and open files alive
  while the "restarted" one opens the same folders. On this machine that is a flaky or failing test.
- **Fix:** Reword: "Build each crash point's on-disk state by hand (receipt file, job folder, `job.json`,
  as `test_replay_worker_archive.py:112-141` does), then start one worker and retry the id." Keep the
  in-process hook only for "before replying", where the first worker stays the only one.
- **Needs the owner?** No.

### B-6. Six tasks have no exact check command, and no test is named for any review finding
- **Severity:** should-fix
- **Task:** B task 1 step 5 (B:106), task 3 step 5 (B:286), task 4 step 3 (B:318), task 5 step 5 (B:367),
  task 6 step 4 (B:399), task 7 step 4 (B:425)
- **Evidence:** For example B:286 "Run replay archive-web, upload, store, isolation and migration tests";
  B:106 "Prove stdlib-only imports and backward compatibility for manual reparsing".
- **Problem:** There is no yes/no gate to run, "migration tests" names no file, and "manual
  `POST /reparse {match_uuid}` is unchanged" has no named proof. The first review's findings 2, 3, 5 and 6
  cannot be shown resolved by a command.
- **Fix:** Add the commands in the table at the end of this file. Add to task 1: "these pass unmodified:
  `test_replay_worker_archive.py::test_a_reparse_runs_from_the_archive_and_leaves_the_file_in_place` and
  `test_replay_archive_web.py::test_a_reparse_through_the_admin_routes_stores_and_keeps_the_file`". Name one
  acceptance test per review finding and list them in the PR draft: finding 2
  `test_a_site_on_a_new_recipe_never_stores_an_old_target_result`, finding 3
  `test_a_lost_response_recovers_the_same_attempt_and_one_job`, finding 5
  `test_worker_control_off_holds_and_reserves_nothing`, finding 6
  `test_a_stale_local_replay_with_an_archived_file_stays_local`. Add `-rs` and "0 skipped among `test_pg_*`"
  to tasks 3, 5 and 6. No test name outside the pg tests may contain `pg` (so not `upgrade`; see A-5).
- **Needs the owner?** No.

### B-7. Nothing proves the merge is inert on the live site while the switch is off
- **Severity:** should-fix
- **Task:** B task 6 steps 1 and 3 (B:381, B:393), task 3
- **Evidence:** B:393 "pass an upload client for safe recovery even if the auto switch is off". The
  dispatcher is on in production and cycles every 20 s (`replay_control_remote.py:49`; design `:390-393`).
  Task 3 rewrites `collect_unfinished` and `refresh_job`, the path every friend's upload takes
  (`replay_upload.py:210-288`; `webapp/app/routers/replays.py:224`).
- **Problem:** B:381 tests that nothing is held or reserved with the switch off. It does not test that
  `step` makes no worker call (no `/health`, no `/archive`, no attempt lookup) when the switch is off and
  no automatic row exists, nor that ordinary uploads are untouched. The owner approves deployed plumbing
  when it is shown inert.
- **Fix:** Add to task 6 step 1: "Switch off and no `auto-reparse:` row: `step` makes zero worker calls and
  one query; the cycle's tasks and counts equal a cycle without the worker client." Add to task 3 step 5:
  "`test_replay_upload.py` and `test_replay_archive_web.py` pass with no existing assertion changed."
- **Needs the owner?** No to build; it belongs on the card for code that ships on merge.

### B-8. Advertising `archive.reparse_protocol` breaks an exact-equality test the plan does not mention
- **Severity:** should-fix
- **Task:** B task 1 step 2 (B:84)
- **Evidence:** B:84 "Health reports `recipe` and `archive.reparse_protocol=1` ... Archive/control `enabled`
  remain separate capability switches."
  `webapp/tests/replays/test_replay_worker_archive.py:56`:
  `assert health["archive"] == {"enabled": False, "reason": "REPLAY_ARCHIVE_DIR is not set"}`.
- **Problem:** Read as "always advertised", the key lands in the archive-off shape (`server.py:261-264`) and
  that test fails.
- **Fix:** Say which: "advertise `reparse_protocol` only in the enabled shape (`Archive.status`,
  `replay_worker/archive.py:195-201`); the off shape is unchanged". The site's gate requires
  `archive.enabled` anyway.
- **Needs the owner?** No.

### B-9. `collect_unfinished(..., session_prefix=None)` "keeps" a filter the base does not have
- **Severity:** should-fix
- **Task:** B task 3 Interfaces (B:245), step 2 (B:258)
- **Evidence:** B:245 "`collect_unfinished(..., session_prefix=None)` keeps the prefix filter".
  `replay_upload.py:253`: `def collect_unfinished(session_factory, worker, now=None) -> int`.
- **Problem:** The parameter came from the deleted code. What it filters, who passes it and whether archive
  sync's call (`webapp/app/services/replay_archive_sync.py:51`) changes are all undefined.
- **Fix:** Define it or drop it. Suggested: "`session_prefix=None` collects every unfinished row (archive
  sync, unchanged call); `session_prefix=AUTO_PREFIX` collects only rows whose `session_key` starts with it
  (the control cycle's step). Both routes send an automatic row through the same guarded collector."
- **Needs the owner?** No.

### B-10. The migration is not checked the way the owner approves additive migrations
- **Severity:** should-fix
- **Task:** B task 3 step 1 (B:250), task 8 step 4 (B:464)
- **Evidence:** B:464 "Verify the additive migration's upgrade/downgrade and existing rows" names no
  database. The owner's rule: fine "once it has gone up, down and up again on the local copy of the real
  data with the same counts. Still give it one card, since it runs on both Render services at merge."
- **Problem:** A round trip on the empty test database does not meet that bar.
- **Fix:** Add to task 8 step 4: "On the local development database: `replay_uploads` row count, upgrade
  to 0018, downgrade to 0017, upgrade to 0018, the count again; record all three numbers on the card." If
  the run may not touch that database, record it as an owed check on the card instead.
- **Needs the owner?** No to build; the card is theirs.

### B-11. New terminal outcomes must fit the column and the worker's ack vocabulary
- **Severity:** nit
- **Task:** B task 3 step 4 (B:274)
- **Evidence:** B:274 "a terminal superseded/kept-existing result with a reason". `store_outcome` is
  `String(16)` (`webapp/app/models/replay.py:210`); the worker accepts only
  `("stored", "replaced", "unchanged", "kept_existing", "failed")` and answers 400 otherwise
  (`replay_worker/archive.py:37, 261-262`); `send_ack` sends `store_outcome` as is
  (`replay_upload.py:303-310`).
- **Problem:** `superseded` as an outcome earns a 400 on every ack and a "refused: outcome must be one of"
  note in `archive_ack`.
- **Fix:** "Use `kept_existing` for a superseded attempt and `failed` for a refused one; put the
  distinction in `error` and in `auto_context`." (No archived file is lost either way: a non-keeping ack
  only drops a pending file, `archive.py:264-267`, and a re-parse has none.)
- **Needs the owner?** No.

### B-12. How the cycle learns that a replacement finished is not said
- **Severity:** nit
- **Task:** B task 6 step 2 (B:389), task 5 Interfaces (B:328)
- **Evidence:** B:389 "Reset `last_planned/last_found` on a successful replacement or hold-back release";
  B:328 `step(...) -> frozenset[int]` returns only the held ids.
- **Problem:** The signal between task 5's `step` and task 6's `cycle` is undefined.
- **Fix:** "The cycle resets the throttle when `counts["reparse_finished"]` is non-zero or the held set
  differs from the previous cycle's, which `State.reparse` remembers."
- **Needs the owner?** No.

---

## Were the first review's findings resolved?

| Finding | Where the rewrite answers it | Resolved? |
|---|---|---|
| 1. New site + old worker loses control | A tasks 2-4: `control.gaps_protocol`, plain tasks to an incapable worker, old-image reproduction | Yes in design. The task 3 recovery test is misplaced (A-2); key and in-flight rules open (A-1). |
| 2. Site recipe change does not invalidate a tagged result | B task 3 steps 3-5: collector checks site recipe, tag, context, result, and the selected replay's snapshot | Yes in prose; the right cases are listed at B:282. No named test (B-6). |
| 3. Acceptance before a durable record | B tasks 1 and 5: committed reservation, deterministic job id, receipts, close fence | Yes in prose. No wire contract (B-2); crash tests need rewording (B-5). |
| 4. Gap freshness and write in separate transactions | A task 3 step 3: one transaction under the replay lock, expected fingerprint | Yes. |
| 5. Runs with worker control disabled | B Global constraints (B:25), `worker_off_reason` | Yes, but the helper is created after its first user (B-3). |
| 6. Local ingests eligible | B task 4 steps 1-2, task 3 step 4 | Yes. |

## Per-task table

`PY` is the main checkout's `.venv313` interpreter, run from the worktree's `webapp/`. Every command ends
`-p no:cacheprovider -q -rs`.

| Task | Buildable as written | Check command |
|---|---|---|
| A1 | yes (committed as `1c8e97a` during this review) | `PY -m pytest tests/replays/test_replay_worker_control.py tests/replays/test_replay_worker.py` |
| A2 | yes with fixes (A-7, A-10) | `PY -m pytest tests/replays/test_gaps_task.py tests/replays/test_replay_worker_control.py tests/replays/test_control_isolation.py tests/replays/test_control_task.py` |
| A3 | yes with fixes (A-1, A-2, A-3, A-4, A-5, A-8) | `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_gaps_store.py tests/replays/test_control_isolation.py`; 0 skipped among `test_pg_*` |
| A4 | yes with fixes (A-1, A-3) | `PY -m pytest tests/replays/test_control_remote.py tests/replays/test_gaps_store.py tests/replays/test_control_isolation.py tests/replays/test_gaps_task.py` |
| A5 | yes (add A-6, A-9 to the notes) | `PY -m pytest tests/replays -k "not pg"` then `PY -m pytest tests/replays -k "pg"`; 0 skipped |
| B1 | yes with fixes (B-2, B-5, B-8) | `PY -m pytest tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_contract.py tests/replays/test_control_isolation.py tests/replays/test_replay_isolation.py` |
| B2 | yes | `PY -m pytest tests/replays/test_replay_store.py tests/replays/test_replay_isolation.py tests/replays/test_replay_impact.py` |
| B3 | no, until B-1 and B-3 (also B-4, B-9, B-11) | after upgrading the test database to 0018: `PY -m pytest tests/replays/test_replay_archive_web.py tests/replays/test_replay_upload.py tests/replays/test_replay_store.py tests/replays/test_control_store.py tests/replays/test_replay_isolation.py tests/replays/test_control_isolation.py`; 0 skipped among `test_pg_*` |
| B4 | yes with fixes (B-3, B-4) | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_isolation.py tests/replays/test_control_isolation.py` |
| B5 | yes with fixes (B-1, B-2, B-4) | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_archive_web.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_upload.py`; 0 skipped among `test_pg_*` |
| B6 | yes with fixes (B-1, B-7, B-12) | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_control_remote.py tests/replays/test_gaps_store.py tests/replays/test_control_isolation.py tests/replays/test_replay_isolation.py`; 0 skipped among `test_pg_*` |
| B7 | yes with fixes (B-6) | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_archive_web.py` |
| B8 | yes with fixes (B-10) | `PY -m pytest tests/replays -k "not pg"` then `PY -m pytest tests/replays -k "pg"`; 0 skipped; `REPLAY_REPARSE_AUTO` still commented out in `render.yaml` |

---

## What the author did with each finding (2026-10-07)

Every finding is accepted; none is rejected. The binding text is the section "Second review incorporated" at
the end of each plan, which wins over the task prose above it where they differ. No open blocker.

| Finding | Disposition |
|---|---|
| A-1 | Accepted: the three key formats, `tries` by worker key, a round excluded while any job for it is in flight, the fake worker a legacy one by default, no forced plan without the capability. |
| A-2 | Accepted: the "missing gap runs after an upgrade" test moves to task 4; task 3 tests that rounds still needing control go out as full tasks. |
| A-3 | Accepted: `_sendable` and `_send` are defined in task 3. |
| A-4 | Accepted: the dispatcher joins the isolation test's file list and compares against `app.services.replay_gaps.GAPS_REVISION`. |
| A-5 | Accepted: `-rs` on the gates, `test_pg_` names, no other test name contains `pg`. |
| A-6 | Accepted: the deploy note and the PR draft say the backlog pass starts at deploy, with a read-only count query; it gets a decision card. |
| A-7 | Accepted, and already built that way in task 2 (`sys.modules["app.gaps"] = None`). |
| A-8 | Accepted: the guarded path never replaces a run under the same gap fingerprint. |
| A-9 | Accepted: a live check on the server process's memory. |
| A-10 | Accepted, and already built that way (the `/health` docstring line names `gaps_protocol`). |
| B-1 (blocker) | Accepted: task 3 step 1 upgrades the test database to head before any pg test, and the migration test leaves it at head. Closed. |
| B-2 | Accepted: the wire contract table is in plan B's amendment; client, worker and fake cite it. |
| B-3 | Accepted: `worker_off_reason` and the protocol constants are created in task 3. |
| B-4 | Accepted: `now` is float epoch seconds in the automatic service; `_finish` takes an optional `now`. |
| B-5 | Accepted: crash points are built on disk by hand; one live worker per test. |
| B-6 | Accepted: the commands of the table above are the tasks' gates, and the four acceptance tests are named. |
| B-7 | Accepted: the switch-off inertness test, and the upload tests pass with no assertion changed. |
| B-8 | Accepted: `reparse_protocol` only in the archive's enabled shape. |
| B-9 | Accepted: `session_prefix` is defined as the suggested filter. |
| B-10 | Accepted: the round trip runs on the local development database too, with the counts on the card. |
| B-11 | Accepted: outcomes stay within the worker's ack vocabulary. |
| B-12 | Accepted: the cycle resets its throttle on `reparse_finished` or a changed held set. |

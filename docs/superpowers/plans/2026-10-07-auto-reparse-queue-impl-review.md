# Review of the worker gaps and automatic re-parse implementation plans

Reviewed 2026-10-07 against the code in the `plans-auto-reparse-queue` checkout. This reviews both `2026-10-07-worker-gaps-and-kill-one-impl.md` and `2026-10-07-auto-reparse-queue-impl.md`; it does not implement or amend them.

The build order makes sense, and the same-file, same-match Impact carry-over is consistent with the page's existing fingerprint guard. Both plans need corrections before implementation. Six actionable findings follow, ordered by priority.

## 1. [P1] A new site with the old worker loses control computation, not just gaps

Location: worker-gaps plan, Task 3's old-worker test at line 612 and Task 5's deployment claim at line 1158.

The old image does not copy `app/gaps`. Its existing `compute_task` tries `_cache_path` when the new site supplies `gaps`; that import fails. Its fallback constructs `_CacheGuard(None)`, whose constructor unconditionally imports `app.gaps.cache` too. This second import escapes to the outer exception handler and fails the entire control task as `infra`. There is no successful control result or failed gap run for the new site's collector to retain. Control consumes its retry budget until the worker updates, and exhausted rounds require another retry reset.

The proposed fake-worker test invents a successful control response with a failed gap run. That is not what the actual old image returns. A local reproduction with `app.gaps` made unavailable returned `failed / infra / ModuleNotFoundError`.

Correction: gate sending the gaps block on a worker capability, or enforce worker-first deployment and remove the claim that either order is safe. Since the two services deploy independently, a capability gate is the stronger solution. Add a compatibility test using the old task's real missing-package behavior, including recovery after the worker updates.

## 2. [P1] A site recipe change does not invalidate an old tagged result

Location: auto-reparse plan, Task 3's proposed `refresh_job` check at line 416; Task 5's mid-flight test at line 1028.

Start an attempt under recipe A, deploy the site onto recipe B, then collect the worker's completed A result. The proposed check compares the result only with the saved tag A, so it passes and calls `store_replay`. The replay and its control/gaps are replaced even though the result is already stale to the collecting site. `step` performs collection before its health handshake; the archive-sync collector also uses `refresh_job`, so the later handshake cannot prevent the write. If a same-file B replay has already been stored, the same-file replacement rule can also regress it to A.

The existing proposed test changes the result recipe, not the site's recipe. An isolated execution of the proposed check with site B, tag A and result A marked the upload `stored`.

Correction: make the store guard account for the site's current recipe as well as the attempt's target, in the shared collection path. Also protect the replacement against a replay that has advanced since selection. Add tests for site-only deployment, collection by archive sync, and a newer same-file replay stored before the old result arrives. Account for overlapping old/new site processes when deciding whether a collector should defer or fail an attempt belonging to another deploy.

## 3. [P1] Worker acceptance precedes any durable attempt record

Location: auto-reparse plan, Task 5's `_submit`, lines 1162-1182.

`worker.reparse` accepts a job before the attempt row is inserted and committed. A site restart in this interval, a database commit failure, or a lost HTTP response leaves a worker job with no durable row. The next cycle cannot collect it, count it as an attempt, or recognize it as the automatic re-parse already in flight. `/health.queued` counts uploads only; it exposes neither the re-parse queue nor the active parse. Another automatic job can therefore be submitted beside the orphan, and repeated failures bypass the two-attempt limit.

Injecting a commit failure after worker acceptance into the proposed `_submit` twice produced two accepted jobs and zero attempt rows. The restart test covers only a restart after a successful attempt commit.

Correction: persist an attempt reservation before submission and make worker submission recoverable and idempotent using that reservation's identity. A pre-created row alone does not resolve a lost response: the site must be able to recover the accepted job id without submitting another job. Add crash/commit-failure/lost-response tests at the acceptance boundary.

## 4. [P2] Gap freshness checking and gap storage are separate transactions

Location: worker-gaps plan, Task 3's `_store_gaps` at line 688 and Task 4's `_control_is_current` / `_collect_gaps_only` at lines 949-982.

For a gaps-only result, `_control_is_current` reads the control fingerprint, closes its session, and then `_store_gaps` calls the existing unconditional `store_gaps` in a new transaction. A relink can change control inputs between the check and the write while retaining the same replay/round keys, so the foreign key does not reject the stale result. The old result can delete and replace a newer gap run. The full control-plus-gaps path has the same interval between `store_round` committing and `store_gaps` writing. The read endpoint marks such a run stale, but that does not prevent the write or the loss of newer results.

Correction: put the current-input/current-control check and the gap replacement in one transaction under the replay's advisory lock, with an expected control fingerprint passed to the writer. Cover both collection paths with a test that relinks between validation and storage, and ensure a newer gap run survives.

## 5. [P2] Automatic re-parsing can run with worker control disabled

Location: auto-reparse plan, Task 5's `_may_start` at line 1147 and `step` handshake at line 1209; Task 7's status calculation.

The site checks its own control setting, but never checks `health['control']['enabled']`. A worker with its archive enabled and a matching recipe, but control disabled, passes the handshake. Missing control counts are treated as zero. Re-parses then replace replays and delete their control/gaps, while the dispatcher receives 404 from every control submission. The queue continues to the next replay after settling, and status reports `running`.

Executing the proposed `_may_start` with `control.enabled=False` and an empty queue returned `True`.

Correction: require the worker's control capability before holding or submitting replays, and report a clear stopped reason in status. Test a matching worker with control disabled or its control capability absent. This is the worker counterpart of the plan's explicit dependency on control restoration.

## 6. [P2] Local ingests are included in automatic eligibility

Location: auto-reparse plan, Task 4's `classify` query and classification at lines 789-825.

The owner's answer 5 in the design says locally ingested replays stay with `reingest_replays.py`. The implementation lists all replay sources and never excludes `source='local'`. A local replay whose recording is also archived is classified `eligible`, held back from control, and automatically replaced. Collection stores it as `source='upload'` as well. Archive presence is not a substitute for the stated source restriction.

An isolated execution of the proposed classifier with a stale local replay and matching archive sha classified it `eligible`.

Correction: give local replays an explicit nonautomatic classification so status can still count them, and exclude them from hold-back/submission. Add a local-source test with a matching archived recording.

## Validation scope

Read both implementation plans, their design, the worker's queue/image/task paths, the dispatcher, both result stores, archive-sync collection, the replay store, admin routes and relevant test fixtures. Reproductions used the repository's Python environment, proposed code extracted into memory, synthetic inputs and temporary SQLite tables. No live database or service was contacted. No production code or plan text was changed. The unimplemented plans' full suites were not run.

No correctness issue found in the proposed Impact carry-over conditions or the basic one-child admission/preemption changes during this review. Real worker memory and timing remain the plans' stated deployment checks.

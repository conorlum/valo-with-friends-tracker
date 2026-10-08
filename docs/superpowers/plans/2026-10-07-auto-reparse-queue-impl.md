# Auto re-parse queue: implementation plan

> **For agentic workers:** Implement this plan task-by-task with checkbox tracking. This is a plan, not authorization to enable the live switch.

**Goal:** A stale uploaded replay is parsed again from the worker's archived recording and safely replaced on the site, one at a time behind uploads. A switch enables new attempts and an admin route reports progress. Local ingests remain with `reingest_replays.py`.

**Architecture:** The control dispatcher runs the automatic step between collection and submission, holding its existing PostgreSQL advisory lock. A committed `replay_uploads` reservation precedes any worker request. Its UUID is the idempotency key for a durable worker receipt; a restart or lost response resumes that same attempt. A nullable `auto_context` column records the selected replay's immutable snapshot. Shared result collection checks the attempt, the collecting site's recipe, the current worker capabilities and the replay snapshot, and commits replay replacement and attempt settlement together. Control and gaps of waiting uploaded replays are held back until replacement or a final refusal.

**Tech Stack:** Existing Python/FastAPI/SQLAlchemy/Alembic/urllib/pytest. One additive migration, no new dependency.

**Depends on:** `2026-10-07-worker-gaps-and-kill-one-impl.md`, merged first, including its capability gate and atomic gap writer. Keep five tasks in that plan and eight here.

## Review incorporated (2026-10-07)

This revision replaces the original unsafe submission/collection snippets and addresses findings 2, 3, 5 and 6 in `2026-10-07-auto-reparse-queue-impl-review.md`. It also consumes that review's corrections to the first plan. The build order, default-off switch, two accepted attempts per file/recipe, owner-managed activation and local-ingest exclusion remain.

Two implementation changes supersede older prose in `2026-10-07-auto-reparse-queue.md`: submission now needs a durable worker idempotency protocol, and the attempt snapshot needs one nullable JSON column. Do not pack that snapshot into error text, infer it from a nullable foreign key, or retain the old no-migration claim. Recipe staleness is still inequality; recipe strings are never ordered to determine which deploy is newer.

## Global constraints

- Branch from freshly fetched `origin/main` after the first PR merges; use a worktree under `.claude/worktrees/`.
- Run tests from that worktree's `webapp/` with the main checkout's `.venv313` interpreter, as `PY -m pytest ... -p no:cacheprovider` below. Record the replay-suite baseline in the PR. Keep the known Windows connection-reset failure separate from new failures.
- `REPLAY_REPARSE_AUTO` defaults false and remains commented out in `render.yaml`. No live deployment, settings changes or production DB work is part of implementation.
- Never create or resume a new automatic submission in demo mode. Existing replay-store demo protections remain. The additive migration contains no data and introduces no worker connection on the demo.
- New submissions require site control enabled, a worker URL, equal site/worker recipes, worker archive enabled, worker control enabled, `control.gaps_protocol == 1`, and `archive.reparse_protocol == 1`.
- Local replays are counted by status but never automatically held, reserved, submitted or replaced, even if their recording is archived.
- Select from existing replay rows, never from the archive index. A tombstone always wins under the replay lock.
- One unfinished automatic reservation or accepted attempt site-wide, across all recipes and site processes. Ambiguous acceptance is still unfinished; it is not permission for another attempt.
- At most two accepted worker attempts per match/file/target recipe, with one-hour backoff. A definite refusal before acceptance does not consume a parse attempt. A contract refusal after acceptance is final after one.
- The worker remains stdlib-only with no database credentials; the site imports no engine or scorer. Preserve isolation tests and the Impact release process.
- Comments/docs describe reasons. Commit no credentials, actual player identities or machine paths.

## Review and acceptance focus

1. A site-only or worker-only deploy while a result is pending cannot store a result stale to the collecting site or overwrite a replay that advanced since selection.
2. An older overlapping site process must defer attempts belonging to a different recipe while its own worker handshake fails; it must not fail the newer site's attempts.
3. A crash before submission, after worker acceptance, or before the acceptance acknowledgement commits resumes the same durable attempt id. A lost response cannot create a second parse.
4. A current worker with control disabled/missing, an unsupported gaps protocol or an unsupported re-parse protocol causes no hold-back or new reservation; status gives the same stopped reason.
5. A local replay with a matching archived file stays local and its control remains eligible for ordinary dispatch.
6. Deletion/replacement during parsing, terminal result expiry, and switching off during uncertain submission each settle safely without recreating a replay or resubmitting an accepted id.

## File structure

| File | Change |
|---|---|
| `webapp/app/replays/contract.py` | Task 1: `current_recipe` |
| `replay_worker/server.py`, `replay_worker/archive.py` | Task 1: health recipe, durable re-parse receipts and recovery/close endpoints |
| `webapp/tests/replays/test_replay_worker.py`, `test_replay_worker_archive.py` | Task 1: protocol and restart tests |
| `webapp/app/replays/store.py`, `test_replay_store.py` | Task 2: Impact carry-over; Task 3: guarded caller-owned transaction |
| `webapp/app/models/replay.py`, `webapp/alembic/versions/<next>_replay_upload_auto_context.py` | Task 3: nullable attempt snapshot |
| `webapp/app/services/replay_upload.py`, `replay_archive_sync.py` | Task 3: protocol client and shared automatic collection |
| `webapp/tests/replays/test_replay_archive_web.py`, `test_replay_upload.py` | Task 3: guards and atomic collection tests |
| `webapp/app/config.py`, `webapp/app/services/replay_reparse_auto.py` | Tasks 4-5, 7: setting, eligibility, recovery, submission and status |
| `webapp/tests/replays/test_replay_reparse_auto.py` | Tasks 4-7 |
| `webapp/app/services/replay_control_remote.py` | Task 6: one locked cycle and hold-back |
| `webapp/app/routers/replay_admin.py`, `webapp/scripts/reparse_archive.py` | Task 7: status route and script |
| `render.yaml`, `webapp/app/main.py`, `webapp/RENDER_DEPLOY.md`, `docs/replay-viewer-plan.md`, `replay_worker/README.md` | Task 8: switch, protocols and release checks |

---

### Task 1: The recipe handshake and durable worker attempt protocol

**Files:** contract, worker server/archive, their existing worker tests. No database code in the worker.

**Interfaces:**

```python
current_recipe(pin: ParserPin | None = None, static_dir: Path | None = None) -> str
WorkerClient.reparse(match_uuid, *, attempt_id=None, expected_sha256=None) -> dict
WorkerClient.reparse_attempt(attempt_id) -> dict
WorkerClient.close_reparse_attempt(attempt_id, match_uuid, expected_sha256) -> dict
```

The worker HTTP protocol is implemented here; its web client is added in Task 3. Existing manual `POST /reparse {match_uuid}` remains supported.

- [ ] **Step 1: Write the recipe and HTTP protocol tests**

Add the original health/condensed-result equality test. Add tests for repeated and concurrent submission of the same attempt UUID, a changed payload with that UUID, a changed archived sha, queue full, an accepted attempt queried after restart, an accepted attempt whose result was swept, and closing an unknown attempt before a delayed submission arrives. Two independent site clients requesting one attempt must observe one job.

Inject worker crashes after preparing a copied file, after its durable job record, after its acceptance receipt, and before replying. On restart, retrying the same id either completes that same preparation once or returns its one accepted job. It never makes a second job or silently re-parses an expired accepted id.

- [ ] **Step 2: Add `current_recipe` and report it**

Use `fmt.recipe((pin or load_pin()).commit, fmt.assets_revision(static_dir or fmt.STATIC_DIR))`. Compute `Worker.recipe` once at startup; failures leave it null and ordinary parsing still works. Health reports `recipe` and `archive.reparse_protocol=1` alongside the first plan's `control.gaps_protocol=1`. Use stdlib protocol constants, not imports of the database or engines. Archive/control `enabled` remain separate capability switches.

- [ ] **Step 3: Implement durable attempt identity and receipts**

For an automatic request, require a valid UUID `attempt_id` and an expected 64-hex sha. Derive a deterministic job id, `auto` plus the UUID's 32 hex digits. Normalize the match UUID. The immutable request identity is `(attempt_id, match_uuid, expected_sha256)`.

Serialize attempt lookup/creation/close with a dedicated worker lock and durable atomic JSON writes on the archive disk. Persist receipts separately from sweepable job/result folders. A receipt distinguishes preparing, accepted and closed-before-acceptance; an accepted receipt always names the deterministic job. Same id with another identity is 409. An existing accepted id returns its original job identity even if the archive's file later changes or disappears.

For a new/preparing id, copy and hash the archived recording under the existing archive protection, and compare it to `expected_sha256` before admission. A definite missing file, tombstone or other recording is refused without acceptance. All manual/automatic queue admissions use the same admission lock so a full queue cannot leave a falsely accepted job.

Define and test the write order: persist preparation identity, prepare the file, atomically write its job record, persist acceptance, enqueue once, then reply. If a crash leaves a durable job but no acceptance receipt, recovery reconstructs acceptance for that deterministic id before any retry can prepare another job. If only preparation exists, safely finish or clean/retry that same preparation. A running/queued job recovered by the existing `_recover` path must not be enqueued a second time by receipt recovery.

Accepted/closed receipt identities are not removed by the existing finished-job sweep. Missing accepted results become a terminal `expired` answer, not a new submission opportunity. Keep receipts small, include them in disk accounting, and document their retention; deleting them is not normal cleanup because an old caller can retry its id. A restored disk containing inconsistent accepted job state fails that attempt rather than guessing that it was never accepted.

- [ ] **Step 4: Add recovery and close routes**

`GET /reparse/attempts/{attempt_id}` returns the receipt identity, state, job id, sha and size, plus job status when available. Unknown id is 404. Accepted-with-expired-result is an explicit terminal response. Queries do not enqueue work.

`POST /reparse/attempts/{attempt_id}/close` accepts the same immutable identity. Under the admission lock it returns the existing accepted job, or writes a durable closed-before-acceptance receipt. A later POST of that id is refused. Closing does not interrupt an accepted parse. This fence allows the site to stop an uncertain reservation without a delayed request accepting it afterwards.

- [ ] **Step 5: Validate and commit**

Run the worker, worker-archive, contract and isolation tests. Prove stdlib-only imports and backward compatibility for manual reparsing. Commit the protocol and tests; do not enable the switch.

---

### Task 2: A same-file replace keeps the per-kill Impact split

**Files:**
- Modify: `webapp/app/replays/store.py` (`store_replay`)
- Test: `webapp/tests/replays/test_replay_store.py`

**Interfaces:**
- Produces: `store_replay` behaviour only. When the existing row is the same `source_sha256`, was linked, and the new row links to the same match, `kill_impact` is copied to the new row.

Why this is safe: `replays.kill_impact` is `{kill_events.id: [gain, loss]}` plus a fingerprint of the match's stored `impact_scores` rows (`app/replays/db.py`, `kill_impact_for_page`). It depends on the match's rows, not on the replay's blobs, and the page shows it only while that fingerprint is current. `write_link` clears it because only a user-run script may compute it; a copy computes nothing.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/tests/replays/test_replay_store.py`, before the `# ---- PostgreSQL only` comment line:

```python
def test_a_new_recipe_of_the_same_file_keeps_the_per_kill_split(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="upload")
    split = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.get(Replay, first.replay_id).kill_impact = split
    db.commit()
    result = store.store_replay(db, replace(condensed, recipe=condensed.recipe + "x"), source="upload")
    row = db.get(Replay, result.replay_id)
    assert result.action == "replaced" and row.recipe.endswith("x") and row.link_status == "linked"
    assert row.kill_impact == split


def test_another_recording_never_inherits_the_split(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="local")
    db.get(Replay, first.replay_id).kill_impact = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.commit()
    other = replace(condensed, source_sha256="f" * 64)
    result = store.store_replay(db, other, source="local", replace=True)
    assert result.action == "replaced" and db.get(Replay, result.replay_id).kill_impact is None


def test_the_split_is_not_carried_to_a_replay_that_no_longer_links(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="upload")
    db.get(Replay, first.replay_id).kill_impact = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.commit()
    db.query(KillEvent).delete()
    db.query(Round).delete()
    db.query(MatchPlayer).delete()
    db.query(Match).delete()
    db.commit()
    result = store.store_replay(db, replace(condensed, recipe=condensed.recipe + "x"), source="upload")
    row = db.get(Replay, result.replay_id)
    assert row.link_status != "linked" and row.kill_impact is None
```

- [ ] **Step 2: Run them and see the first fail**

Run: `PY -m pytest tests/replays/test_replay_store.py -k "split" -p no:cacheprovider -q`
Expected: `test_a_new_recipe_of_the_same_file_keeps_the_per_kill_split` FAILS on `row.kill_impact == split` (it is None). The other two already pass; they pin what must stay true.

- [ ] **Step 3: Carry the split**

In `webapp/app/replays/store.py`, in `store_replay`, replace from the line `existing =session.query(Replay)...` down to the final `return StoreResult(action, row.id, status, row.link_report or {})` with:

```python
        existing = session.query(Replay).filter(Replay.match_uuid == uuid).one_or_none()
        same_source = existing is not None and existing.source_sha256 == condensed.source_sha256
        if existing is not None and not replace:
            if same_source and existing.recipe == condensed.recipe:
                session.rollback()
                return StoreResult("unchanged", existing.id, existing.link_status, {"reason": "same file and recipe"})
            if not same_source and replay_db.is_linked(existing):
                session.rollback()
                return StoreResult("kept_existing", existing.id, existing.link_status,
                                   {"reason": "a linked replay of this match exists; the new recording was not stored"})
            if not same_source:
                # An unlinked or refused replay can't squat the UUID: the new one replaces it only
                # if it links, and a savepoint undoes the attempt otherwise.
                savepoint = session.begin_nested()
                _delete(session, existing)
                row = _insert(session, condensed, source)
                status = replay_db.link_replay(session, row)
                if status != "linked":
                    savepoint.rollback()
                    session.rollback()
                    return StoreResult("kept_existing", existing.id, existing.link_status,
                                       {"reason": "the new recording did not link, so it doesn't replace the "
                                                  "existing unlinked one", "new_link_status": status})
                savepoint.commit()
                session.commit()
                return StoreResult("replaced", row.id, status, row.link_report or {})
        action = "stored"
        carried = None
        if existing is not None:
            # The same recording under a new recipe (a re-parse): the per-kill Impact split describes the
            # match's impact_scores rows, not the blobs, so it is kept when the new row links to the same
            # match. The page still shows it only while its fingerprint is current (db.kill_impact_for_page).
            if same_source and replay_db.is_linked(existing) and existing.kill_impact:
                carried = (existing.match_id, existing.kill_impact)
            _delete(session, existing)
            action = "replaced"
        row = _insert(session, condensed, source)
        status = replay_db.link_replay(session, row)
        if carried is not None and status == "linked" and row.match_id == carried[0]:
            row.kill_impact = carried[1]
        session.commit()
        return StoreResult(action, row.id, status, row.link_report or {})
```

In the module docstring, change the second dedupe bullet to:

```
   - the same `source_sha256`, another recipe (a re-ingest or a re-parse after a condenser change):
     replaced, keeping the per-kill Impact split when it links to the same match;
```

- [ ] **Step 4: Run the store's tests**

Run: `PY -m pytest tests/replays/test_replay_store.py tests/replays/test_replay_isolation.py tests/replays/test_replay_impact.py -p no:cacheprovider -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/replays/store.py webapp/tests/replays/test_replay_store.py
git commit -m "replays: a same-file replace keeps the per-kill Impact split"
```

---

### Task 3: Durable attempt snapshots and shared guarded collection

**Files:** upload model, next Alembic migration, upload client/service, replay store, archive-sync service, their tests.

**Interfaces:**
- `ReplayUpload.auto_context: dict | None`, using the repository's JSON/JSONB variant.
- `AUTO_PREFIX = 'auto-reparse:'`; `auto_recipe(session_key)` still reads the target recipe from the tag.
- `collect_unfinished(..., session_prefix=None)` keeps the prefix filter, but shared automatic collection must use the protocol and guards below in every caller.
- `store_replay(..., commit=True, expected_auto=None)` retains its current default API. `expected_auto` is a typed snapshot from the context; `commit=False` lets automatic collection own replay replacement and attempt settlement in one transaction.

- [ ] **Step 1: Add the one nullable context column and migration tests**

Choose the next free migration revision after rebasing (0018 if the schema head is still 0017). The upgrade adds nullable `auto_context`; the downgrade drops only that column. No replay or upload data is rewritten. Ordinary/manual upload rows leave it null. Cover SQLite fixture creation and PostgreSQL upgrade/downgrade with existing rows.

A reservation's context stores `version=1`, normalized `match_uuid`, `selected_replay_id`, `source_recipe`, `source_sha256`, `target_recipe`, and `phase='reserved'`. Later copy/update the JSON to mark `accepted` or `refused_before_acceptance`, preserving the immutable snapshot. Terminal errors remain plain `error` text. Do not use `ReplayUpload.replay_id` as the only snapshot: replacement can set that foreign key null, and SQLite tests can reuse ids.

- [ ] **Step 2: Add the HTTP client and tag helpers**

Implement Task 1's optional request fields, lookup and close methods. Preserve response bodies/statuses needed to distinguish unknown receipt, definite refusal, accepted, expired and transport uncertainty. A transient transport failure is not evidence of nonacceptance. Old workers without protocol 1 must never receive automatic attempt requests.

An automatic tag without a valid context fails safely without replacing anything; normal upload/admin rows retain their existing behavior. Add the tagged collection prefix, and ensure it does not accidentally collect unrelated uploads.

- [ ] **Step 3: Define shared deploy-skew collection behavior**

Use one automatic collector from `refresh_job`/`collect_unfinished`, whether invoked by the control cycle or archive sync. Do not put safety checks only in the automatic step.

Before storing an automatic result, obtain fresh worker health. Require worker recipe = collecting site's `current_recipe`, archive enabled, control enabled, gap protocol 1, and re-parse protocol 1. If this collector's handshake fails, defer the row unchanged and expose the stopped/deferred reason. In particular, an old site on A seeing worker B must leave B attempts to the matching collector; it cannot classify their results as failures merely because A differs. During site-first deployment, the B site defers A attempts until the worker also moves to B.

Once this collector has a matching handshake, require result recipe = tag target = context target = current site recipe, result sha = the immutable selected sha, and result match UUID = selected match. An obsolete target then fails with `another recipe`; nothing is stored. Thus a site-only deploy cannot write an A result through a B collector, and an overlapping A collector cannot invalidate B attempts. No ordering of recipe strings is used.

Protocol lookup/recovery always comes before normal job polling for reserved rows. `auto_context.phase='reserved'` is never handed to the legacy no-job-id/stuck-row path. The shared collector may look up a receipt, bind an accepted job and collect its result; only the automatic step under the dispatcher lock may submit/resubmit or close an uncertain reservation. Archive sync never starts a reserved parse. For accepted rows, a confirmed queued/parsing job stays unfinished; the ordinary upload's elapsed-time cutoff cannot release the one-at-a-time gate while that accepted automatic job is still live. A receipt confirming terminal failure/expiry can settle it.

- [ ] **Step 4: Make replay replacement conditional and settlement atomic**

Acquire the match advisory lock using the context's UUID before locking the upload row; use this lock order consistently for reservation settlement and replay writes. Reload both the attempt and replay after locks are held. Inside the store transaction, validate tombstones and require an uploaded replay with the selected id, source recipe and sha. The result and selected target must still match the collecting site's recipe.

If the existing replay has already advanced, do not replace it. An already-current replay of this same UUID/file/target is an unchanged success; another advanced/replaced replay is a terminal superseded/kept-existing result with a reason. A selected replay that is gone is never recreated. A local replay is never replaced through this path. Test SQLite id reuse by changing the source recipe while keeping/reusing the selected id.

Refactor the store's transaction ownership deliberately: with `commit=False`, no branch commits or rolls back the caller's transaction, including unchanged and kept-existing branches. The normal default wrapper retains its existing commit/rollback semantics. Automatic collection updates the upload's terminal state, outcome and new replay id in the same commit as replacement. A crash/failure before this commit leaves the old replay and unfinished attempt; after it, a second collector observes the terminal row and cannot replace again. Send/retry archive acknowledgements only after that commit.

Preserve Task 2's Impact carry-over. If a guard/store error occurs, roll back the replay changes before recording a terminal failed attempt in a fresh, correctly locked transaction. Never acknowledge a successful replacement before its settlement commits.

- [ ] **Step 5: Write and run collection regression tests**

Cover successful same-file replacement; wrong worker/result recipe; site B collecting tag/result A; archive sync collecting that same row; old collector A deferring a B attempt when worker B is current; worker control/protocol disabled; selected replay advanced to B before an A result; selected replay deleted or changed to local; wrong sha/UUID; and deletion mid-flight.

Inject failure immediately before settlement commit and assert both replay replacement and terminal attempt state roll back. Run concurrent collectors against PostgreSQL and verify one store/settlement with consistent lock order. Include a postcommit acknowledgement failure and assert collection does not replace again.

Run replay archive-web, upload, store, isolation and migration tests. Commit model, migration, client and guarded collector together.

---

### Task 4: The setting and eligibility

**Files:** config; new `replay_reparse_auto.py`; new `test_replay_reparse_auto.py`.

**Interfaces:**
- `settings.replay_reparse_auto: bool = False`.
- `MAX_TRIES=2`, `BACKOFF_S=3600`, `SETTLE_S=180`, `INDEX_S=120`, `TAG_MAX=64`.
- `off_reason()` reports site settings; `worker_off_reason(health, recipe)` centralizes worker recipe/archive/control/protocol checks for step and status.
- `Entry(replay_id, match_uuid, map_name, state, rank, attempts, last_error)`.
- `classify(session, recipe, index, builds, now)` returns every stored replay. States: `deleted`, `local`, `current`, `resolving`, `in_flight`, `gave_up`, `unsupported_build`, `unknown`, `no_archive`, `other_recording`, `backoff`, `eligible`.
- `HELD = ('eligible', 'backoff', 'resolving', 'in_flight')`, restricted to uploaded replay entries.

- [ ] **Step 1: Write eligibility and settings tests**

Use synthetic fixtures and `CONTROL_TABLES` plus `ReplayUpload`. Cover each prerequisite independently, default-off/demo behavior, tag length, newest linked match then unlinked upload ordering, two accepted attempts, one-hour backoff and a final accepted contract refusal. A reserved or accepted unfinished attempt under any recipe blocks another site-wide attempt. Refused-before-acceptance rows do not count as parse attempts.

Add a stale local replay with exactly matching archive sha. Expect `local`, no hold-back, no reservation and no replacement. Current local rows are counted as local too; uploaded current rows are current. Tombstones take precedence over all classifications.

- [ ] **Step 2: Implement classification**

Start from existing replay rows, with their source, recipe, sha and match date. Classify tombstones and local rows first. Count accepted terminal attempts by `(match_uuid, source_sha256, target_recipe)` using validated context, not just sha or a nullable replay foreign key. Look for unfinished reservations across all target recipes before declaring a stale uploaded replay eligible. A reserved row is `resolving`; a known accepted row is `in_flight`.

Only after those checks apply supported build, archive availability and sha equality, retry limit and backoff. Index `None` means unknown, not an empty archive. A new target recipe resets finished-attempt counts but never bypasses an unfinished old attempt. Keep final refusal detection and plain error reporting.

- [ ] **Step 3: Implement gates and validate**

Add the setting beside the existing control setting. `worker_off_reason` requires explicit enabled flags and exactly supported protocols; absent capabilities fail closed for automatic reparsing. Status and step must call this same helper.

Run eligibility, config and isolation tests, then commit. No worker request is performed by classification itself.

---

### Task 5: Reserve, recover and submit one attempt

**Files:** automatic service and tests.

**Interfaces:**
- `Memo(index=None, index_at=None)` caches only archive reads; durable correctness state is in the DB and worker receipts.
- `step(session_factory, worker, memo, control_in_flight, now, counts) -> frozenset[int]`, called while the dispatcher's separate lock session holds its advisory lock.
- Counts include `reparse_reserved`, `reparse_sent`, `reparse_recovered`, `reparse_finished`, `reparse_refused`, `reparse_deferred`.

- [ ] **Step 1: Write the failure-boundary tests**

Replace the old fake worker with a protocol fake holding durable receipts separately from jobs and an accepted-job count. Cover restart before the first POST, worker acceptance followed by a lost response, failure to commit the accepted phase/job metadata, and retries by another process with no memo. In every case exactly one worker job belongs to the reservation UUID and no second reservation is created.

Cover definite missing/changed archive recording before acceptance (zero parse attempts), accepted parse failure/backoff/final refusal, accepted result expiry, old-target reservation across a deploy, disabled control/missing protocol, ordinary queued/unfinished uploads, control draining, and switching off during both known and uncertain acceptance. A closed unknown id cannot be accepted by a late request.

- [ ] **Step 2: Recover unfinished reservations before considering new work**

Query all automatic unfinished rows across recipes. Resolve each via its deterministic attempt id. A known accepted receipt binds the same job and selected sha, then uses the shared collector; persistence failure leaves the row recoverable by that same id. A transport failure leaves the reservation unfinished and blocks new attempts.

Before treating another target as obsolete or closing its unknown receipt, require a fresh matching site/worker recipe and supported archive protocol. An older A process seeing worker B must defer B's reservations unchanged, including unknown/preparing ones; it cannot close them because their target differs from A. Apply the same rule when that process's switch is off. Recovery reads can still identify accepted work, but mismatched collectors cannot submit, fence, fail or store it. Add an overlapping-deploy test for both accepted and not-yet-accepted B reservations.

For an unknown/preparing receipt, resend only the same identity, only when the switch and matching capabilities are on, the selected replay is still eligible/currently stale for that target, and upload/control gates allow it. Recheck selection under the replay lock before submission. If the target changed, the replay was superseded/deleted/local, or the switch is off, use the close fence. A close response naming an already-accepted job means collect/defer it; a closed-before-acceptance receipt means terminal nonattempt refusal. Do not clear a reservation merely because one lookup returned 404.

On recovery, ignore this reservation itself when checking for other unfinished automatic rows; otherwise it blocks its own resume forever. Revalidate its snapshot and finished-attempt budget directly rather than requiring its classifier state to be `eligible` (it is correctly `resolving`). Any other unfinished reservation or accepted attempt remains a blocker.

Known accepted attempts may still be collected with the switch off when this collector's deployment/worker handshake matches, so a safe in-flight replacement finishes. Closing unaccepted reservations creates no new parse. If the worker is unreachable, keep unresolved reservations and report them until acceptance can be established or fenced; a timeout is not a substitute for evidence.

- [ ] **Step 3: Apply eligibility and pacing gates**

With a valid worker handshake, read the archive index at most once per `INDEX_S`. Refresh it after a definite refusal or recording mismatch. Classify existing uploaded replays and return their held ids; local rows are excluded.

Do not reserve while any ordinary upload is unfinished, any automatic reservation/accepted attempt is unfinished, the worker has uploads queued, site control is in flight, worker control is queued/running, or `SETTLE_S` has not passed since the last accepted attempt settled. Prefer newest linked match, then unlinked upload time. Ambiguous rows retain the gate across restarts and recipe changes.

Before another new reservation, also check restoration of the most recent successful automatic replacement from database state, using its context UUID and stored outcome. For a still-existing uploaded replay on that target, no computable round may have missing/stale control or missing/stale gaps for current `ok` control. Current terminal engine/detector failures count as settled; exhausted infra retries that leave rows missing do not. Deletion or a superseding recording releases this restoration barrier. Expose `restoration_pending` and the remaining round counts in status instead of inferring successful restoration from an empty worker queue. Test an exhausted infra budget, including restart, and prove no subsequent replay is stripped of control until restoration is resolved.

- [ ] **Step 4: Commit the reservation before any worker submission**

Within the outer dispatch lock, use a separate short transaction to acquire the selected match lock, reload/revalidate its uploaded source, id, recipe, sha and tombstone, and check for any existing unfinished automatic row. Insert a UUID `ReplayUpload`, status queued, immutable context phase reserved, expected sha, target tag, and deterministic worker job id. Commit it before calling the worker. If that commit fails, there must be no worker request.

Then submit with that attempt UUID and expected sha. Verify the receipt's identity/sha before marking accepted. A lost response or a failed acceptance-metadata commit leaves the committed reservation to recover; never insert a second row to resolve uncertainty. A definite preacceptance refusal settles phase refused-before-acceptance, invalidates the index and consumes no parse attempt. Do not acknowledge nonexistent jobs.

The same id may be retried to retrieve its receipt, even when the first accepted job is finished or its result expired. It may never start another job. Accepted failures count once; repeated receipt/transport recovery does not count as additional attempts.

- [ ] **Step 5: Validate and commit**

Run the automatic service tests and the real localhost worker protocol/archive-web tests, not only the fake. Verify worker restarts and DB failures independently. Run PostgreSQL multi-instance reservation tests with the existing dispatcher lock: only one reservation is committed, even when two site versions have different recipes.

Commit the service and tests. New attempts remain disabled by default.

---

### Task 6: The control cycle and hold-back

**Files:** dispatcher, automatic service tests and dispatcher tests.

**Interfaces:** `State.reparse` contains the memo; `_sendable(..., held=frozenset())` excludes held ids for both full control and gaps-only work. `cycle(..., worker=None)` uses the upload protocol client in addition to its control client.

- [ ] **Step 1: Write integration tests**

Test eligible/resolving/in-flight/backoff uploaded replays held from both task kinds, control released after safe settlement or final failure, and ordinary local control still dispatched. With the switch off, a mismatched deployment, missing protocol or worker control disabled, hold nothing for speculative new attempts. Confirm no new reservation is committed under those gates.

Test replacement completion invalidates the planning throttle so the new replay's rounds are submitted promptly. Test exceptions during step reads/stores, including a real failed database transaction, while control dispatch still proceeds on a usable session and the outer advisory lock remains held.

- [ ] **Step 2: Keep transaction ownership separate**

The cycle's lock session only owns `pg_try_advisory_xact_lock`; it is never passed to operations that can commit or abort it. Collection, automatic reads/reservations/settlements and ordinary planning/stores use separate sessions. Retain the outer lock through collect, automatic step and control submission. A database failure in the automatic step closes/rolls back that step's session, not the lock or planning session.

Catch step failures, log them and fall back to empty speculative hold-back. Keep durable reservations intact. Before control planning, refresh its session so a replay committed by automatic settlement is not read from an old ORM snapshot. Reset `last_planned/last_found` on a successful replacement or hold-back release rather than relying only on elapsed time.

- [ ] **Step 3: Wire lifecycle and counts**

When site control and the worker URL are configured, pass an upload client for safe recovery even if the auto switch is off. `step` recovers/collects accepted work or fences unaccepted work before it considers creating a new reservation, and never creates one with the switch off. Archive sync uses the same guarded collection path and can race safely.

Merge the automatic counts and `held_back` into the existing dispatch log. With no worker client, ordinary control remains available and no automatic work is attempted. Missing configuration is a stopped reason, not a startup exception.

- [ ] **Step 4: Validate and commit**

Run automatic, dispatcher, gap-store and isolation tests. PostgreSQL tests must establish lock retention, concurrent collection and one reservation across site processes; a fake RuntimeError-only test is insufficient. Commit integration and tests.

---

### Task 7: Status route and `--status`

**Files:** automatic service, admin router, archive script and tests.

**Interfaces:** `status(session, worker) -> dict`, `GET /admin/replays/reparse/status`, `reparse_archive.describe(body) -> list[str]`, `--status`.

- [ ] **Step 1: Write status and access tests**

Cover switch off, site control off, URL absent, worker unreachable, recipe skew, archive off, worker control off, missing/unsupported gaps or re-parse protocol, local/current/eligible/resolving/in-flight/backoff/gave-up counts, accepted-result expiry and deferred old/new deployment collection. Test actual HTTP 404 for absent/wrong admin credentials and demo mode, as well as the authorized route.

- [ ] **Step 2: Implement database-derived progress**

Return `running`, `reason`, `site_recipe`, `worker_recipe`, worker capability values, all classification counts, `gave_up` entries (`match_uuid`, map, accepted attempts, last error), unresolved/deferred attempt information, `restoration_pending`, and control/gap round wait counts. `running` means the automatic queue is enabled with a matching capable worker; it does not claim a parse is active or that its pacing/restoration gates currently permit the next reservation.

Use the same settings/worker gates as `step`. Explicitly expose `resolving` reservations so a worker outage or ambiguous submission is visible after a restart. Distinguish a stopped new-attempt queue from an accepted attempt still finishing. Count local replays without changing them. Read progress only: status never submits, closes or collects an attempt.

- [ ] **Step 3: Add the route and script output**

Use existing `require_admin` and the configured upload client. `--status` prints running/stopped reason, both recipes, capability state, counts, gave-up details, resolving/deferred attempts and waiting rounds, then exits without mutation. Keep existing manual `--match` behavior. Mock the actual route response in script tests and cover service failure output/exit code.

- [ ] **Step 4: Validate and commit**

Run automatic status, replay-admin/archive-web and script tests. Commit service, route, script and tests.

---

### Task 8: Switch, migration/deployment docs and release checks

**Files:** Render blueprint comments, app lifespan comments, deployment guide, replay-viewer plan and worker README. Update the design's superseded architecture notes as part of implementation documentation, referencing this revision.

- [ ] **Step 1: Declare the switch commented out**

```yaml
      # Automatically re-parse stale uploaded replays, one accepted attempt at a time.
      # Off until the owner completes RENDER_DEPLOY.md checks. Requires site control,
      # matching worker recipe, archive/control enabled and both worker protocols.
      # - key: REPLAY_REPARSE_AUTO
      #   value: "true"
```

Do not set the live variable. Update the lifespan comment to describe the shared control/re-parse thread and recovery with new submissions off.

- [ ] **Step 2: Document schema and protocol rollout**

The additive nullable migration runs through the existing Alembic build path on both sites; it does not put replay data on the demo. Ship the worker protocol first, then the migrated site with the switch still off. An older worker causes stopped automatic status, not best-effort automatic requests. The first plan's capability gate independently keeps ordinary control compatible.

Explain durable reservations, deterministic worker ids, receipts retained beyond result cleanup, acceptance recovery, and the close fence. Switching off stops new acceptance; an already accepted safe result can still be collected. Unknown acceptance remains resolving until the worker can answer or be fenced. Recipe-skew collection can defer during rollout; only the matching current collector finalizes old-target results without replay mutation.

State that stale local replays stay with `reingest_replays.py`; only stale uploaded replays with matching archived recordings use this queue. Preserve Riot/demo restrictions and site URLs.

- [ ] **Step 3: Document the owner's switch-on checks**

1. Both PRs are deployed, migration is at head, and `--status` shows equal recipes, archive/control enabled, gaps protocol 1 and re-parse protocol 1. No unresolved reservation is left over from testing.
2. Manually re-parse one synthetic/owner-selected match and verify new replay data, current control/gaps after recomputation and the existing per-kill Impact values.
3. Measure worker memory with parsing plus one control child, including the largest file, and measure parse/control/gap durations.
4. Exercise one automatic attempt with the switch under owner control, including a site/worker restart or lost response, and verify one reservation UUID maps to one accepted worker job.

Then the owner can enable the variable. Document one-at-a-time pacing, accepted-attempt backoff/give-up, and status interpretation. Document the database restoration barrier as well as the worker drain: exhausted infra retries leave the next reservation waiting with `restoration_pending`, and status names the remaining rounds. Resolve failed restoration before bulk activation.

- [ ] **Step 4: Run the complete replay checks**

Run `PY -m pytest tests/replays -k "not pg" -p no:cacheprovider -q` and compare with the recorded baseline. Run the PostgreSQL subset against the protected test database. Verify the additive migration's upgrade/downgrade and existing rows. Record skipped platform/DB checks explicitly; required race tests must pass before release.

Check docs/code isolation, the worker image build and its smoke test, and that no credential or actual player data appears in the patch. Commit docs with the switch still off.

- [ ] **Step 5: Put live checks in the PR description**

Record matching recipes/capabilities across images, memory/disk and measured durations, upload priority during a re-parse, restart/lost-response recovery with one accepted id, deployment skew without stale writes or invalidated newer attempts, and current control/gaps after replacement. Activation is the owner's final step, after a concrete reviewed implementation and these checks.

---

## Second review incorporated (2026-10-07)

`2026-10-07-auto-reparse-queue-impl-review-2.md` reviewed this rewrite against the code. Every finding is
accepted, the one blocker (B-1) included. Where this section and a task above differ, this section wins.

**Gates (B-6).** Every command is run from `webapp/` and ends `-p no:cacheprovider -q -rs`; where a task has
PostgreSQL tests the expected result includes "0 skipped among `test_pg_*`". No test name outside the pg
tests contains `pg` (so not "upgrade").

| Task | Check |
|---|---|
| 1 | `PY -m pytest tests/replays/test_replay_worker.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_worker_control.py tests/replays/test_replay_contract.py tests/replays/test_control_isolation.py tests/replays/test_replay_isolation.py` |
| 2 | as written in the task |
| 3 | after the test database is at `0018`: `PY -m pytest tests/replays/test_replay_archive_web.py tests/replays/test_replay_upload.py tests/replays/test_replay_store.py tests/replays/test_control_store.py tests/replays/test_replay_isolation.py tests/replays/test_control_isolation.py` |
| 4 | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_isolation.py tests/replays/test_control_isolation.py` plus the config tests |
| 5 | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_archive_web.py tests/replays/test_replay_worker_archive.py tests/replays/test_replay_upload.py` |
| 6 | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_control_remote.py tests/replays/test_gaps_store.py tests/replays/test_control_isolation.py tests/replays/test_replay_isolation.py` |
| 7 | `PY -m pytest tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_archive_web.py` plus the script's tests |
| 8 | `PY -m pytest tests/replays -k "not pg"` then `-k "pg"`; `REPLAY_REPARSE_AUTO` only in commented lines of `render.yaml` |

One named acceptance test per finding of the first review, listed in the PR draft: finding 2
`test_a_site_on_a_new_recipe_never_stores_an_old_target_result`, finding 3
`test_a_lost_response_recovers_the_same_attempt_and_one_job`, finding 5
`test_worker_control_off_holds_and_reserves_nothing`, finding 6
`test_a_stale_local_replay_with_an_archived_file_stays_local`. Task 1 also requires these to pass unmodified:
`test_replay_worker_archive.py::test_a_reparse_runs_from_the_archive_and_leaves_the_file_in_place` and
`test_replay_archive_web.py::test_a_reparse_through_the_admin_routes_stores_and_keeps_the_file`.

**Task 1, the wire contract (B-2).** Every answer of the attempt routes is JSON with a `"code"`; the client,
the worker and the tests' fake branch on `code`, never on the HTTP status alone. The manual
`POST /reparse {match_uuid}` (no `attempt_id`) keeps its answers exactly.

| `code` | HTTP | When | Fields |
|---|---|---|---|
| `accepted` | 202 the first time, 200 on a repeat, a lookup or a close | the attempt has its one job | `attempt_id`, `job_id`, `match_uuid`, `sha256`, `size`, `state: "accepted"`, `job_status` (`queued`, `parsing`, `done`, `failed`, or `expired` when the result was swept) |
| `preparing` | 200 (lookup only) | a receipt exists, no job yet | `attempt_id`, `match_uuid`, `sha256`, `state: "preparing"` |
| `closed` | 200 on close and lookup, 409 on a later POST | closed before acceptance | `attempt_id`, `state: "closed"` |
| `unknown_attempt` | 404 (lookup only) | no receipt | |
| `archive_off` | 404 | the archive is off | |
| `no_archived_file` | 404 | nothing archived for the match | |
| `sha_mismatch` | 409 | the archived recording is another file | `sha256` (the archive's) |
| `deleted` | 409 | the match is tombstoned | |
| `identity_conflict` | 409 | the id was used with another match or sha | |
| `bad_request` | 400 | not a UUID, not a 64-hex sha | |
| `queue_full` | 503 | no room; nothing was accepted | |

`no_archived_file`, `sha_mismatch` and `deleted` are definite refusals before acceptance: the receipt is
removed (or never written), so they consume no parse attempt. The `WorkerClient` methods are built in task 3.

**Task 1, tests (B-5, B-8).** Each crash point's on-disk state is built by hand (the receipt file, the job
folder and its `job.json`, as `test_replay_worker_archive.py` already does for a killed worker), then one
worker is started and the id retried; Windows file locks rule out a second live worker on one folder. An
in-process hook is used only for "before replying". `reparse_protocol` is advertised only in the archive's
enabled shape (`Archive.status`); the archive-off shape is unchanged.

**Task 3 (B-1, B-3, B-4, B-9, B-11).**

- Before any pg test: upgrade the test database to head (`PY -m alembic upgrade head` with `DATABASE_URL`
  naming the `*_test` database for that command only) and confirm `alembic current` reads `0018`. The
  migration test is `test_pg_migration_0018_adds_a_nullable_context_and_drops_only_it`; it leaves the
  database at head.
- `worker_off_reason(health, recipe)` and the protocol constants are created here, in
  `app/services/replay_upload.py`, because the shared collector is their first user. Task 4 keeps
  `off_reason()` for the site's settings and imports the worker check. Collector, step and status call this
  one helper.
- `collect_unfinished(session_factory, worker, now=None, session_prefix=None)`: `None` collects every
  unfinished row (archive sync, unchanged call); `AUTO_PREFIX` collects only rows whose `session_key` starts
  with it (the control cycle's step). Both send an automatic row through the same guarded collector.
- `_finish` takes an optional `now` (default unchanged), so an injected clock also stamps settlement.
- Outcomes stay within the worker's ack vocabulary (`stored`, `replaced`, `unchanged`, `kept_existing`,
  `failed`): a superseded attempt is `kept_existing`, a refused one `failed`; the distinction goes in `error`
  and in `auto_context`.
- `test_replay_upload.py` and `test_replay_archive_web.py` pass with no existing assertion changed (B-7).

**Task 4 (B-4).** `now` is float epoch seconds everywhere in `replay_reparse_auto`; it is converted once
with `datetime.fromtimestamp(now, timezone.utc)`, and a naive `finished_at` (SQLite) is read as UTC.

**Task 6 (B-7, B-12).** Add: with the switch off and no `auto-reparse:` row, `step` makes no worker call at
all (no health, no archive index, no attempt lookup), and the cycle's tasks and counts equal a cycle without
the upload client. The cycle resets its planning throttle when `counts["reparse_finished"]` is non-zero or
the held set differs from the previous cycle's, which `State.reparse` remembers.

**Task 8 (B-10).** The migration's round trip (up, down to `0017`, up) also runs on the local development
database with the `replay_uploads` counts before, between and after, and the database is left at the
revision it was found at; the numbers go on the migration's decision card.

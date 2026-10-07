# Auto re-parse queue: plan

Status: plan only, 2026-10-07. No code, migration or setting has been changed.

Base read: `origin/afk/2026-10-05-utility-review` at `a9d1a87` (PR #118, not merged into `origin/main` when
this was written). That branch holds `CONDENSE_REVISION = 13`; `origin/main` is still at 12. This branch
(`plans-auto-reparse-queue`) is cut from it.

## Goal

When the condenser, the pinned parser or the map/agent assets change, every stored replay whose `.vrf` the
worker has archived is parsed again by the worker and replaced on the site, with nobody running a script.
The site decides what is stale and paces the work; the worker stays a parser with no database.

Done, from the owner's side:

- They set one environment variable on the friends site, once. After the next merge that changes the recipe,
  stored replays move to the new recipe on their own, newest match first, and their map control follows.
- One command (or one admin URL) shows how many replays are current, waiting, in flight, or given up on and
  why. No log reading.
- Unsetting the variable stops new re-parses within one cycle. A friend's upload is never queued behind the
  backlog.

## Scope

In scope:

- Choosing stale replays and submitting them to the worker's existing `POST /reparse`, one at a time.
- A recipe handshake between site and worker.
- Sequencing re-parses against uploads and against the control idle queue.
- A bounded retry record per file and recipe, with no schema change.
- A status route and a `--status` flag on the existing script.
- Keeping the per-kill Impact split across a same-file replace.

Out of scope:

- Timing gaps on the worker. The site's dispatcher does not compute gaps today (see "Where the code
  contradicts the prompt"); that needs its own plan.
- Replays ingested locally (`source = 'local'`) whose `.vrf` is on the owner's PC, not in the worker's
  archive. `scripts/reingest_replays.py` stays their route.
- Preempting a running parse when an upload arrives.
- Any general job framework, any new table, any change to the scorer or to `CONDENSE_REVISION` itself.
- Deploying or switching it on. That is the owner's step.

## How it works

1. The worker's `/health` reports the recipe it would stamp on a parse today. The site computes its own
   current recipe from the same three inputs (`replay_parser.json` commit, `CONDENSE_REVISION` and
   `FORMAT_VERSION`, the assets hash).
2. Each cycle of the existing control dispatcher thread calls one new step. If the switch is off, the two
   recipes differ, or the worker's archive is off, the step does nothing and holds nothing back.
3. If an automatic re-parse is in flight, the step polls it with the same `refresh_job` an upload uses and
   returns. At most one is ever in flight.
4. Otherwise it lists the eligible replays: stored recipe differs from the current one, the worker's archive
   index holds the same file (same sha256), no tombstone, a game build the pinned parser supports, and fewer
   than two attempts under the current recipe.
5. The control dispatcher skips rounds of those replays. Computing control on a blob that is about to be
   replaced is thrown-away work.
6. If no upload is waiting or unfinished, and the worker's control queue has drained since the last
   re-parse was stored, the step submits the newest eligible match to `POST /reparse` and records a
   `replay_uploads` row tagged with the target recipe.
7. The worker parses it behind any upload that arrives meanwhile. The result is stored by the existing
   `store_replay` in one locked transaction: rounds and players replaced, the link remade, old control rows
   gone. A result whose recipe is not the one asked for is not stored.
8. The replay is now current, so step 5 stops holding its rounds back. The control dispatcher sends them, and
   the worker runs them now that it is not parsing. When its control queue is empty again, step 6 picks the
   next match.

## Decisions

### 1. Where the logic lives

Choice: a new module, `app/services/replay_reparse_auto.py`, holding one `step(...)` function that the
control dispatcher's `cycle` calls between `_collect` and `_submit`. Same daemon thread, same advisory lock,
same 20 s cadence. The step returns the set of replay ids waiting for a re-parse, which `_submit` filters out.

Reason: re-parses and control jobs compete for one worker, and a re-parse invalidates control's output for
that replay (the recipe is in the control fingerprint). One loop has to order them. Run separately, the
control queue would recompute every round on the old blobs, the re-parse would delete those rows, and control
would run a second time. The dispatcher is also the place that already owns "one instance at a time" and
"never in demo mode".

Rejected: an independent second thread with its own lock. It is the more obvious copy of the pattern, but two
schedulers would each need to read the other's state across threads, and Render's overlapping instances
during a deploy could give the two locks to different processes. Also rejected: logic on the worker. It has
no database, so it cannot know which replays are stored, stale, tombstoned or already failed.

### 2. Eligibility, and how the site learns the archive

Choice: a replay is eligible when all of these hold.

- `replays.recipe != current recipe` (inequality, never ordering).
- The worker's `GET /archive` index has an entry for its `match_uuid` whose `sha256` equals
  `replays.source_sha256`.
- No `replay_deletions` row for the match.
- `replays.game_branch` is in the pin's `supported_replay_builds`.
- No unfinished `replay_uploads` row for this file under the current recipe, and fewer than `MAX_TRIES = 2`
  finished ones (see decision 5).

The list starts from `replays` rows, never from the archive index, so the feature can only replace a replay
that exists. The archive index is fetched with the existing `WorkerClient.archive()` only when the step is
about to choose (nothing in flight, gates passed), not every cycle.

Reason: requiring the same sha256 keeps this strictly "the same recording under a new recipe". Then
`store_replay` takes its "same file, another recipe: replaced" branch and never the "another recording"
rules, which can keep or refuse. The build check uses a column the site already has and saves a doomed parse.

Rejected: letting the worker list what is stale. It would need each stored recipe pushed to it, which is a
second copy of the site's state. Also rejected: skipping the sha256 check and trusting the UUID alone.

### 3. Knowing the worker is on the same recipe

Choice: the worker adds `"recipe"` to `/health`, computed once at start-up with the same call the condenser
uses (`fmt.recipe(load_pin().commit, fmt.assets_revision())`). The site compares it with its own value and
queues nothing unless they are equal. A worker too old to report it counts as unequal. As a second check,
`refresh_job` refuses to store an automatic re-parse whose result recipe is not the one in its row's tag.

Reason: comparing the whole recipe, not only the condenser revision, also covers a parser pin or asset change
that reached one service first. The check at store time closes the gap where the worker redeploys between
submit and parse. Both directions of deploy skew then do nothing: site ahead, the worker would produce the
old recipe; worker ahead, the result would look stale to the site and be asked for again.

Rejected: finding out from the result of a real re-parse. It costs a full parse, and under the current
`store_replay` an intermediate recipe would be stored and the replay's control deleted for no gain.

### 4. Priority and pacing

Choice:

- One automatic re-parse in flight, site-wide.
- None is submitted while the worker's `/health` shows uploads queued, or the site has an unfinished upload
  that is not an automatic re-parse.
- After one is stored, the next waits `SETTLE_S = 180` and then for the worker's control `queued` and
  `running` to both read 0.
- Order: newest match first, by `matches.played_at` for linked replays, then by `replays.created_at`. This is
  the control queue's D2 order.
- The control dispatcher does not send rounds of replays that are waiting for a re-parse.

Reason: the worker already drains every upload before any re-parse (`Worker._next`), and one in flight
bounds a friend's worst wait to the remainder of a single parse. The drain gate matters because the worker
runs control only while both parse queues are empty (`Worker.idle`): a re-parse always queued would stop
control entirely, and every re-parsed replay would sit without control until the whole archive was done. With
the gate, one replay at a time is without control. `SETTLE_S` covers the dispatcher's own `PLAN_IDLE_S` (120
s) plus a cycle, so "empty" is not read before the new rounds are submitted.

Rejected: filling the worker's five-slot re-parse queue. It is faster for the parse alone but starves control
for hours and leaves up to five replays stripped of control at once. Also rejected: killing a running
re-parse when an upload arrives; it is a worker change that buys back at most a few minutes.

### 5. Failures

Choice: every automatic re-parse is a `replay_uploads` row with
`session_key = "auto-reparse:<target recipe>"` and the file's `source_sha256`. That row is the whole record.
A file gets at most two attempts per recipe, at least `BACKOFF_S = 3600` apart, whatever the failure. A
contract refusal (`not condensable: ...`, which includes an unsupported build) is final after one.

| Case | What happens | What stops a repeat |
| --- | --- | --- |
| No archived file (never archived, evicted, a local ingest) | Not eligible; never submitted. Shown as "no archived file". | Nothing to record: the index says so each time. |
| File evicted between the index read and the submit (worker 404) | No row. The next index read drops it. | Same. |
| Unsupported build | Filtered by `game_branch` before any submit. If the manifest disagrees, the worker answers `not condensable: replay_build`; the row is `failed`. | Final after one row under this recipe. |
| Parser failure or timeout | Row `failed` with the worker's reason. | Two rows under this recipe, then left alone. |
| Worker restart | Archive on: the worker re-queues the job from disk and the same job id answers. Job lost: `refresh_job` fails the row after `STUCK_AFTER` (20 min, 60 if still queued). | Counts as one attempt. |
| Worker on another recipe at parse time | Result not stored; row `failed: worker recipe differs`. | Counts as one attempt; the handshake stops new ones. |
| Match tombstoned meanwhile | The worker refuses (409) or drops the job; `store_replay` refuses under the match lock. Row `failed`. | The tombstone makes it ineligible. |

A new recipe is a new tag, so every file gets fresh attempts when the code changes. That is correct: a parser
update is the thing most likely to fix an old failure.

Reason: the rows survive a site restart, so a broken file cannot be retried once per deploy for ever. The
control queue keeps its tries in memory, which is fine for a job of seconds and wrong for one of minutes.

Rejected: an in-memory counter like the control queue's. Also rejected: a new attempts table (decision 9).

### 6. The replay's data during and after

Choice: keep `store_replay` as the only writer, and add one thing to it: when a replace is the same file and
the new row links to the same match, carry `kill_impact` over from the old row.

What a viewer sees:

- While the worker parses: the old replay, complete, with its old control. Nothing is touched until the
  result is in hand.
- At the store: one transaction under the match's advisory lock deletes the old rounds, players and control
  and inserts the new ones, then links. A failure rolls back and the old rows stay. A reader sees all old or
  all new.
- After: new rounds; the link, kill map and DB-only deaths remade in that transaction; control "not ready"
  until the dispatcher's rounds come back (decision 4 keeps that to one replay at a time); the per-kill Impact
  split still there.
- Page addresses use the match UUID, so the new replay id breaks no link.

Reason for the carry-over: the split is keyed by `kill_events.id` and guarded by a fingerprint of the stored
`impact_scores` rows. It does not depend on the replay's blobs. Today `write_link` clears it and only a
user-run script or the next crawl restores it, because the web service never runs the scorer. Without the
carry-over, every automatic re-parse would hide the split until the next crawl.

Rejected: keeping the old control rows and serving them as "stale" against the new rounds. It needs the
store to move rows across the replace, and it would draw control computed from one blob over another.

Not solved here: timing gaps. Their rows cascade away with the old rounds and nothing on the site recomputes
them (see the contradiction below and owner question 3).

### 7. The off switch

Choice: its own setting, `REPLAY_REPARSE_AUTO`, default false, declared commented-out in `render.yaml` like
`REPLAY_CONTROL_REMOTE`. It takes effect only when the control dispatcher is also on (`REPLAY_CONTROL_REMOTE`,
`REPLAY_WORKER_URL`, not demo mode). With it set but control off, the site starts normally and the status
route says why nothing is running.

Reason: re-parsing rewrites stored replays and is much heavier than control, so switching control on must not
switch this on. It depends on control being on because a re-parse deletes that replay's control, and without
the dispatcher only a local script would bring it back, which is the manual step this feature removes.

Rejected: sharing `REPLAY_CONTROL_REMOTE`. Also rejected: refusing to start when the pairing is wrong; the
owner's rule is to degrade.

Demo site: `enabled()` requires `not demo_mode`; the demo service has no worker URL; `store_replay` refuses
in demo mode and on the demo database. Three independent stops.

### 8. Seeing progress

Choice: `GET /admin/replays/reparse/status`, behind the existing `REPLAY_ADMIN_TOKEN` gate, and
`scripts/reparse_archive.py --status` to print it. Everything is derived from the database and two worker
calls, so it is right after a restart. It reports:

- whether the queue is running, and if not, the one reason (switch off, control off, worker unreachable,
  recipes differ with both values, archive off);
- counts: current, eligible, in flight, stale with no archived file, stale with another recording archived,
  unsupported build, given up;
- for each given-up replay: match UUID, map, attempts, the last error;
- rounds still waiting for control, and rounds with no current gap run.

Reason: the owner's tooling for the archive is already scripts over these admin routes.

Rejected: a web page (more surface for the same numbers). Also rejected: log lines only. The dispatcher
still logs its counts each cycle, as it does for control.

### 9. Schema

Choice: none. The attempt record is `replay_uploads`, using columns it already has: `session_key` (the tag
with the target recipe), `source_sha256`, `status`, `error`, `store_outcome`, `finished_at`, `replay_id`.

Reason: the manual re-parse route already writes such a row with `session_key = "admin-reparse"`, and the
archive sync thread already collects and acks them. Today's recipe is about 29 characters, so the tag fits
the 64-character column; the step treats a tag that would not fit as "off" and says so in the status.

Rejected: a nullable `target_recipe` column on `replay_uploads`. It is cleaner to query, but it is a
migration on both databases for one string. If the tag ever feels cramped, that is the additive fallback.

### 10. Tests

Unit tests, against the in-process fake worker the control dispatcher's tests already use:

- Eligibility: each condition alone makes a replay ineligible; a matching sha256 is required.
- Handshake: nothing is submitted when the worker's recipe differs or is absent; a result with another
  recipe is not stored and counts as an attempt.
- Pacing: one in flight; none while an upload is queued or unfinished; none until `SETTLE_S` has passed and
  control has drained; newest match first.
- Control: rounds of waiting replays are not sent; they are sent once the replay is current; nothing is held
  back when the switch is off or the recipes differ.
- Failures: two attempts per file and recipe, the backoff, a contract refusal final after one, a new recipe
  resets the count, a lost job fails after the stuck window.
- Deletion: a tombstoned match is never submitted; a tombstone arriving mid-parse leaves no replay.
- Store: a same-file replace keeps `kill_impact` when the match is the same and drops it when not; a failed
  store leaves the old rows.
- Switch: off by default, off without control, off in demo mode.
- PostgreSQL: one instance runs the step (the existing advisory-lock test, extended).
- Worker: `/health` carries the recipe the condenser stamps.
- Status route: 404 without the token; each "not running" reason; the counts.

Only checkable on the live services:

- The site's and the worker's recipes are equal after a normal deploy of both (the assets hash is computed
  on Linux in both, but this has never been compared across the two images).
- Real durations: one parse on the 2-CPU worker, and control for one match, which together set how long a
  full archive takes.
- An upload sent during a re-parse starts as soon as that parse ends.
- Behaviour across a real deploy of each service while a re-parse is queued.
- Memory and disk on the worker during a re-parse of the largest archived file.

## Risks

| Risk | How the design limits it |
| --- | --- |
| A deleted match comes back. | The list starts from existing `replays` rows; the tombstone is checked by the step, by the worker, and by `store_replay` under the match lock. |
| A friend's upload waits. | One in flight; nothing submitted while an upload is queued or unfinished; the worker drains uploads first. Worst case is the rest of one parse. |
| Control starves, or every replay loses control at once. | The next re-parse waits for the control queue to drain, so one replay at a time is without control. |
| Control is computed twice. | Rounds of replays waiting for a re-parse are held back. |
| A loop: re-parsed, still stale, re-parsed again. | The recipe handshake, the recipe check at store time, and two attempts per file and recipe recorded in the database. |
| A file that always fails is retried for ever. | Same record; a contract refusal is final after one. |
| Timing gaps vanish for re-parsed replays. | Not limited by this design. Reported in the status; see owner question 3. |
| The whole archive is re-parsed after a small asset edit. | Intended (the recipe says the data is stale), paced, and stoppable by unsetting one variable. See owner question 1. |
| The feature runs on the demo. | Three independent stops (decision 7). |
| The site's cycle slows down. | The step makes one `/health` call per cycle and one database query; the archive index is read only when choosing. Stores use their own sessions so the cycle's lock is not released early. |

## Where the code contradicts the prompt

- "Its map control and timing gaps are stale again, and the control idle queue picks them up." Only control
  is picked up. `replay_control_remote._submit` sends no `gaps` key, and the only caller of `store_gaps` is
  `scripts/compute_control.py`. Gap rows are deleted with the old rounds (foreign-key cascade) and return
  only when that script is run locally.
- "Re-parses share the worker's queue with uploads." They have their own queue of the same size, read only
  when the upload queue is empty. A re-parse that has started is not interrupted by an upload.
- `docs/replay-viewer-plan.md` says a stale upload's page offers "re-upload to refresh". I found that text
  only in `scripts/reingest_replays.py`, not in the app.

## Verified and assumed

Verified by reading the code on the base above: the worker's queues, `idle` rule and control preemption
(`replay_worker/server.py`); the archive index's fields and the tombstone checks (`replay_worker/archive.py`);
`store_replay`'s dedupe rules and tombstone refusal; `refresh_job`, `collect_unfinished` and the ack path;
the admin re-parse route and its `replay_uploads` row; the control dispatcher end to end; how the recipe is
built and that the site can compute it without the parser; that `write_link` clears `kill_impact` and what
restores it; that replay pages are addressed by match UUID; the cascade from rounds to control and gap rows.

Assumed, not checked (no database or live service was touched):

- That `REPLAY_ARCHIVE_DIR` and `REPLAY_CONTROL_REMOTE` are on in the Render dashboard. Both are commented
  out in `render.yaml`.
- How many replays are stored and how many are archived. `render.yaml` estimates room for about 350.
- Timings. The worker's README records 179 s for one 60 MB match on the development PC. Control time per
  round on the worker is not recorded anywhere I read. At a guess of 10 to 15 minutes per match for parse
  plus control, a full archive is two to four days of the worker's idle time.
- That most stored replays are uploads with an archived file, not local ingests.

## Questions for the owner

1. Once switched on, should it follow every later recipe change by itself? Recommended: yes. That is the
   point of the feature, it is paced, and one variable stops it. The alternative is a setting that names the
   recipe it approves, which you would edit after each condenser change.
2. Is it acceptable that an upload arriving mid-re-parse waits for that one parse to finish (a few minutes at
   worst)? Recommended: yes. Preempting a running parse is a worker change to add only if the wait is felt.
3. Timing gaps are not recomputed by the site, so a re-parsed replay has none until `compute_control.py` is
   run locally. Ship this first, or put gaps on the worker first? Recommended: ship this with the switch off,
   plan gaps on the worker next, and switch on after it, unless losing gaps for a while does not bother you.
4. Are the worker's archive and the control idle queue both on in production today? Recommended: confirm
   before the build; with either off the feature idles and says so.
5. Locally ingested replays are not in the worker's archive, so this never touches them. Leave them to
   `reingest_replays.py`? Recommended: yes.

## Rough size

No migration. About 250 lines of code and 400 of tests.

| File | Change |
| --- | --- |
| `webapp/app/services/replay_reparse_auto.py` | New, about 180 lines: `enabled`, `current_recipe`, eligibility, `step`, `status`. |
| `webapp/app/services/replay_control_remote.py` | About 25 lines: call the step, hold back waiting replays, two counts. |
| `webapp/app/services/replay_upload.py` | About 15 lines: the recipe check for tagged rows in `refresh_job`. |
| `webapp/app/replays/store.py` | About 10 lines: carry `kill_impact` across a same-file, same-match replace. |
| `webapp/app/routers/replay_admin.py` | About 25 lines: the status route. |
| `webapp/app/config.py`, `render.yaml` | The setting, and its commented-out line. |
| `webapp/scripts/reparse_archive.py` | About 20 lines: `--status`. |
| `replay_worker/server.py`, `replay_worker/README.md` | About 10 lines: `recipe` in `/health`. |
| `webapp/tests/replays/test_replay_reparse_auto.py` | New, about 300 lines. |
| `test_control_remote.py`, `test_replay_worker.py`, `test_replay_archive_web.py`, the store tests | About 100 lines between them. |
| `docs/replay-viewer-plan.md`, `webapp/RENDER_DEPLOY.md` | A paragraph each: the freshness rule's new route, and the switch-on steps. |

Order of deploy when it is built: the worker first (so `/health` reports its recipe), then the site with the
switch still off, then one manual `reparse_archive.py --match` as a check, then the switch.

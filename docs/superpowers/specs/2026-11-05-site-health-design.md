# Site health: from replay errors to every failure the site can see

Status: design, revised after the P2 review (2026-11-05, 15 findings, all applied). Friends site only; nothing here
runs on the demo.

## Why

The upload page's "Show replay errors" card (`app/services/replay_errors.py`) lists only replay-pipeline problems
that are already stored in the database. Most other failures in the app are caught and sent to `log.exception` /
`log.warning`, which only reaches Render's log stream: nobody reads it, and it is gone after a restart. Pages that
crash leave no trace at all (`app/main.py` has no exception handler). This design gives those failures a stored
home and shows them on the same card, renamed **Site health**.

## Decisions taken with the owner (2026-11-05)

| Item | Decision |
|---|---|
| Stored events table | Yes. Cleared automatically by a later success of the same job, or by the PR that fixes it; never from the site. |
| Scrubbing and retention (1) | Approved as written |
| Traceback tails on the card | Approved: anyone with the upload code may see them, after scrubbing |
| Pages that crash | Yes |
| Critical Util background build | Yes |
| Background loops gone quiet | Yes, as a "last finished a pass" check |
| Matches with no Impact scores | Yes |
| Linked replays missing per-kill Impact | Yes |
| Replays still unlinked after days | **No**: not useful, only discouraging |
| Map-pool patch-note failures | Yes |
| tracker.gg crawl freshness | **No** |
| Impact rows on an old scoring version | **No**: each scoring version replaced all stored rows at once, so mixed versions can't build up |
| Cache fallbacks | Yes, if recording is cheap (batched counts) |
| Replay worker overruns and refusals | Yes |
| Slow pages | Yes, threshold 5 s, first page loads only |
| Browser-side JS errors | **No**, not for now |
| Coach points that failed their checks | **No**: the coach command already prints them at the end of every run |
| Card name | **Site health** |
| Demo | Everything off, as `replay_errors` already is |

## 1. The events table

Migration `0031_site_events`: `site_events`, one row per distinct problem; `site_event_resolutions`, one row
per applied entry of the resolved file (see Clearing); and `site_event_marks`, what a clear or a deduplicated
record leaves behind (see "Writers take turns"). All new, no foreign keys.

| Column | Meaning |
|---|---|
| `kind` | one of the kinds below, e.g. `page_error`, `loop_failed` |
| `key` | what failed, stable across repeats: a route template, a loop name, a player id, a warm identity |
| `error` | the last error text, scrubbed (see below), at most 2,000 characters |
| `context` | JSONB: the last occurrence's details (traceback tail, exit code, figures) |
| `count` | occurrences since first seen or last cleared |
| `first_seen`, `last_seen` | UTC |

Primary key `(kind, key)`. A repeat updates the row: `count + 1`, and the latest `error`/`context`/`last_seen`
when it is at least as recent as the stored one; a late write of an older occurrence (another process's flush)
never moves `last_seen` back, and `first_seen` keeps the earliest (branch review B4).

- The model goes in its own file, `app/models/site_events.py`. It must not touch any model file the scoring
  manifest digests: an edit to those makes ingest refuse to run.
- **Recording never fails or stalls its caller, and nothing it watches waits on the database for it** (branch
  review B3): `site_health.record` (alias `enqueue`), `resolve` and `resolve_prefix` put an entry on an in-memory
  queue, in the order things happened; the flush thread writes them. Neither a request nor a background loop (the
  dispatcher holds its advisory lock while it runs) waits on the pool (`app/db.py`: 30 s pool timeout) or a
  database that is down. Only a process about to exit, the Critical Util child, writes at once
  (`record_now`/`resolve_now`, its own short session, under the writers' lock and marks like a flush; each returns
  whether it committed). A child that died without recording is handed to the next child (`--previous`), which
  writes it before building, so a failure and a later success's clear come from one process in order. The parent
  keeps a handed failure until a child's exit code says it was stored, and hands it on again otherwise; it carries
  an id the write deduplicates on, so one handed twice is stored once. It is timed when its child exited. A clear the
  child couldn't store is queued by the parent (fifth and sixth reviews). Every other command-line process that
  records must flush before it exits: the cache pre-warms (`flush_for`) and `scripts/map_pool.py refresh` do.
  Everything catches and logs its own failure.
- **Batched recording for hot paths** (cache fallbacks, slow pages): `site_health.count(kind, key, ...)` adds to an
  in-memory counter and keeps each key's last occurrence time; one daemon thread, `site-health-flush`, writes the
  batch and the queue every 60 s in one transaction, one write per key between two resolves of it. A counter's
  context is joined with the stored one (slow pages keep the slowest ever and the last five; others the last five
  items), not replaced (B5/B16); the stored context is read under the writers' lock, so two processes' flushes of
  one key both land. A record can carry `dedupe(seen, context)`: it is stored only if the key's mark doesn't already
  count it, and the mark keeps what it counted (a warm-cache loss: the highest count per worker boot), so a loss the
  old and new site both report during a deploy is stored once, a late report of an earlier boot isn't counted
  again, and a restart re-reading a count a PR already cleared doesn't bring the row back. A failed flush puts its entries back ahead of what was queued
  meanwhile, minus counters a success queued meanwhile covers; a repeated resolve with no record of its keys
  between replaces the earlier one, so an outage's queue holds its failures. `last_seen` is the occurrence time, not the flush time (P2 finding 2). Every time is
  Python UTC (`datetime.now(timezone.utc)`), never the server's `now()`. The site runs one uvicorn process
  (`render.yaml`), so per-process memory is the whole picture; shutdown waits up to 10 s for the loops' threads
  to finish their pass, then up to 5 s for the final flush (B3).
  The cache pre-warms run in command-line processes with no flush thread, so they flush at their end (`flush_for`).
- **Writers take turns** (fifth and sixth reviews): every health write (a flush, `record_now`, `resolve_now`,
  applying a PR entry, pruning) holds one transaction-level advisory lock (`site_health.WRITERS_LOCK`), so the
  writes of all processes are serial: none can deadlock another, and each reads what the last one committed before
  it writes. A write that waits over 30 s (`lock_timeout`) fails like a lost connection and waits for the next
  flush. Each clear leaves a mark in `site_event_marks` (kind, key, `prefix`, `cleared_at`): an occurrence last seen
  at or before it is never stored again, by any process, so the old process's late flush during a deploy can't
  bring back what the new process's success cleared. Marks untouched for 30 days are pruned with the rows.
- **One bad entry never blocks the rest** (B2): text is made storable first (a NUL, which PostgreSQL's text and
  JSONB refuse, is written as `\x00`; keys are cut to the column's 300 characters). If the database still refuses
  the batch, it is written again one entry per savepoint and the refused entries are dropped and logged; a lost
  connection keeps everything for the next flush (the queue stays bounded at 1,000, oldest dropped first, and a
  drop is logged).
- **Scrubbing**: before anything is stored, remove credentials from connection URLs
  (`postgresql://user:pass@` becomes `postgresql://user:***@`), Bearer/Basic credentials, and the values of
  password, passwd, pwd, secret, token, api key, access key, authorization and key, written `name=value`,
  `name: value` or `"name": "value"` (a quoted value up to its closing quote, or to the end of the text when it was
  cut short, in linear time: R4-1 found exponential backtracking), and the whole value of a context field named
  like one (`token`, `db_password`, `X-Api-Key`). A name may carry any prefix (`db_password=`, `RIOT_API_KEY=`,
  `PGPASSWORD=`); escaped JSON (`{\"password\": \"x\"}`), bytes (`b'x'`) and SQL's doubled quote (`'it''s'`)
  are hidden too. Every pattern runs in linear time (the URL pattern's repeats are bounded), and text is cut to
  four times what is kept before it is scrubbed, then to what is kept (2,000 characters; worker refusals 300), so
  no long error stalls a caller. The queue holds an error's text cut the same way. The card is visible to anyone with the upload code, and the repo is public.
- **Retention**: rows untouched for 30 days are deleted by the flush thread; at most 500 keys per kind (the oldest
  `last_seen` goes first).

### Clearing

- **Automatic**: the same job succeeding for the same key calls `site_health.resolve(kind, key)`; the flush
  deletes the row if it was last seen before the success (a failure after it stays). It is queued in order with the
  records, and the delete always runs, so it also clears a row another process wrote (B2, B5, B6); its mark keeps
  another process's earlier failure from coming back after it (fifth and sixth reviews). A recompute aimed at the errors therefore clears them as it fixes them. Each kind below says what
  counts as success. A kind with no success signal is cleared only by the PR that fixes it. (Owner, 2026-11-06,
  D1: a PR naming the fixed errors is the manual way to remove them; a later success of the same thing clearing
  them on its own is welcome on top of that.)
- **By PR, never from the site** (owner's decision, 2026-11-05): there is no Clear button and no clear endpoint.
  The PR that fixes a problem adds an entry to the committed file `app/data/site_health_resolved.json`:
  `{"kind": ..., "key": ... or null for the whole kind, "fixed_by": "<PR or note>"}`. An entry's identity is its
  kind, key and `fixed_by`: a merged entry is never edited, and a later fix of the same key is a new entry with its
  own `fixed_by`, so it applies again (B1/B8). Only stored kinds can be named (`site_health.RECORDED_KINDS`; a
  test checks the file). **Each entry is applied once** (P2 finding 1: every merge redeploys, so re-applying at
  every start would wipe later recurrences): the first process that starts with an entry stores it in
  `site_event_resolutions` (entry hash, `cutoff`, `applied`) with `cutoff` = that process's start time plus a
  10 min grace. The flush thread does this, never startup itself (B7 of the first review). Render starts the new process
  before it stops the old one, so the old code can still record for a few minutes; the grace clears those too. The
  flush thread (and each later start) deletes the entry's matching rows with `last_seen < cutoff` once the clock
  passes `cutoff`, then marks it applied; later starts skip applied entries. A recurrence after the cutoff stays.
  A due entry is claimed (`UPDATE ... WHERE applied_at IS NULL`) in the transaction that deletes, so two overlapping
  processes never both apply it, and a late flush of an occurrence from before an applied entry's cutoff (the old
  process) is dropped (B7): applying leaves a mark at the cutoff, under the writers' lock, so a flush waiting for
  it sees it once it writes. Once every
  entry is applied the thread stops checking. A failure that recurs inside the grace is cleared with the rest: after
  a fix PR, check the card again once the job has run past the cutoff.
  Entries are kept in the file (they cost nothing and record what fixed what); a test checks every entry names a
  known kind.
- `scripts/site_health.py list` (through `with_friends_db.py --read-only`) prints the stored rows, to find the
  `kind` and `key` a PR's entry needs. It has no clear command.
- A cleared problem comes back on its next occurrence with `count` 1.

## 2. What is recorded or checked

"Recorded" kinds write to the table when they happen. "Checked" kinds read data that is already stored, the
way the card works today, and disappear on their own once the data is right.

### 2a. Pages that crash: `page_error` (recorded)

An `@app.exception_handler(Exception)` in `app/main.py`, a plain `def` (Starlette runs an `async` handler on the
event loop), enqueues the record, then returns the same 500 response as today. It never touches the database.
Key: method plus the route template from `request.scope["route"].path` (`GET /players/{display_name}`), not the
filled-in URL, so one bug is one row. Context: exception type, the last 15 traceback lines, the path without its
query string, whether the viewer was logged in (from `request.session`, no DB call). A crash after the response
has started is not passed to the handler by Starlette, so it isn't recorded.
A route that takes a secret in its path (the upload guide's `{key}`) stores its route template as the path,
for crashes and slow pages alike (second review B1). HTTPException (404, 403) is not an error and is not recorded. Cleared only by a PR (a later success of the same
route doesn't prove the bug is fixed for other inputs).

### 2b. Critical Util background build: `util_build_failed` (recorded), `util_build_stale` (checked)

- The child process (`app/services/util_rebuild.py` `main`) wraps `_build` and records its own exception with the
  traceback tail, then exits with a distinct code (3) meaning "failed and recorded". A finished build resolves the
  kind; a child that exits because another holds the build lock is not a success and resolves nothing.
- The parent, in `request()`, checks the previous child's exit code before starting another, and enqueues a record
  for any non-zero code other than 3 (killed for memory, for example). Context: exit code, pid, how long it ran.
- `util_build_stale`: the stored build is out of date, the oldest current util row newer than the build is over
  3 h old (there is no stored "stale since"), and no build holds the build's advisory lock
  (`pg_locks`, since `running()` knows only this process's child). `site_health` never imports `replay_util` at
  module level (it pulls in numpy, which the isolation tests forbid); the check reads key and timestamp columns only,
  and the `corpus_key` compare runs in the card's background verifier, not in the 55 s computation.

### 2c. Background loops gone quiet: `loop_quiet` (checked), `loop_failed` (recorded)

All four loops (control dispatch `replay_control_remote.py:792`, archive sync `replay_archive_sync.py:89`, match
states `replay_match_states.py:74`, map pool `map_pool_refresh.py:177`; threads in the one uvicorn process, started
from `main.py` lifespan when their `start()` returns a stop event) already catch errors per pass, so a failing pass
doesn't kill the thread; nothing restarts a thread that does stop, and a pass that hangs logs nothing at all (the
past "held_back N; 0 in flight" case).

- Each loop is registered when `start()` starts it (the start time is its first beat) and calls
  `site_health.beat(name, phase)` at the start and end of every pass: an in-memory timestamp, no DB write.
- `loop_quiet` (checked, in-memory): a registered loop that hasn't finished a pass in 3x its cycle plus 2 min
  (pass lengths aren't logged today). Cycles: control dispatch 20 s, archive sync 300 s, match
  states 60 s, map pool daily (allowed 26 h). Shown with "last finished a pass at …" and, when one is under way,
  "in a pass since …". It disappears when the loop beats again.
- `loop_failed` (recorded): the `except` in each `run_forever` records with key = loop name, context = traceback
  tail; the next finished pass resolves that key only. Failures caught inside a pass that still finishes record
  under the same kind with their own keys, and each is resolved only where that step succeeds (P2 finding 5):
  `reparse_pass` (`replay_control_remote.py:733`) when the re-parse step returns, `height_rebuild_step` (`:772`) when
  the heights step returns, `util_build_step` after the remote Util step returns (outside the control cycle's
  lock transaction), `archive_sync:worker` (`replay_archive_sync.py:81-83`) on a cycle with no worker error,
  `map_pool:check` (`map_pool_refresh.py:151-154`, `:171-173`) on a check with no errors. Every clean pass queues
  its resolve (no cache of open keys: it went stale across processes), and repeated resolves are merged in the
  queue. An archive acknowledgement the worker never answers counts as a worker error. `scripts/map_pool.py
  refresh` flushes what its check queued.
- The card shows heartbeats only for loops in this process; with one uvicorn process that is all of them.

### 2d. Matches with no Impact scores: `unscored_match` (checked)

Matches older than 1 h (so an ingest in progress isn't listed) with no `impact_scores` row for any of their
rounds. One `NOT EXISTS` query. Link: the match page. During a scoring release every match can show briefly;
that is expected. The query's cost on the friends DB is unmeasured here (no local copy).

### 2e. Linked replays missing per-kill Impact: `kill_impact_missing` (checked)

Linked replays of scored matches (unscored ones are already `unscored_match`) whose `kill_impact` is NULL, or whose
stored fingerprint (`kill_impact->>'fingerprint'`) no longer matches the match's `impact_scores`
(`replays.db.impact_fingerprint`). The NULL check is one query. The fingerprint check is one query per linked
replay, so it runs in the card's background verifier thread with its own pending condition (the existing verifier
re-runs only for feature digests), recomputed every verifier pass, since a rescore changes `impact_scores` without
changing anything cheap to key a cache on; it reads only the stored fingerprint, never the splits. Reproduce
(read-only): `kill_impact_reproduce` selects the stored fingerprint to compare with `impact_fingerprint`; the
repair, `scripts/link_replays.py --refresh-impact`, writes the split of every linked replay. Both checked kinds read
at most 200 items, plus one to tell the card it was cut ("more than 200"; the rest query lists them all).

### 2f. Map-pool patch notes: `map_pool_note` (checked)

`map_pool_patch_notes` rows whose `last_error` is set ("parser miss" or given up): the page's URL, the miss count,
the error. These are already stored; nothing new is recorded.

### 2g. Cache fallbacks: `cache_fallback` (recorded, batched)

`site_health.count` calls in the places that today log and fall back to a live recompute: player view cache
decode failure and invalid blob (`player_view_cache.py` 368-404, 583, 604), site stats write-through and viewer
cache failures (`site_stats.py` 163, 201, 220; `viewer_site_stats_cache.py` 81, 88; `site_stats_cache.py` 356),
pre-warm failures (`player_view_cache.py` 765, `account_groups.py` 280, 298), the group cache read
(`account_groups.py` 238) and the player page write-through (`players.py` 291). Key: the cache and the failure
(e.g. `player_view_cache:decode`), not the player, so one bug is one row; context keeps the last 5 player or
viewer ids. Cleared only by a PR.

### 2h. Replay worker overruns and refusals: `worker_overrun`, `worker_refused` (recorded)

In `replay_control_remote.py`: a job that ran over its running backstop (`f.stale_s`, fixed when the worker
admitted the round, from its `runner`) and a refused job
(line 666), plus infrastructure failures (511; not compat refusals, `:513`). `worker_overrun` key: map plus replay
and round; a later stored result for that round resolves it, the gaps-only path included when its gaps were
stored (a skip because the round's control moved on proves nothing, B10).
`worker_refused` key: map plus the refusal's reason class, with replay and round in context, so the deploy gap
(the worker on the old protocol refusing every round) is a few rows, not 500; a later stored result on that map
resolves it.

### 2i. Slow pages: `slow_page` (recorded, batched)

A pure ASGI middleware (not `app.middleware("http")`) times each request to its last body chunk and reads the
route template from `scope["route"].path` afterwards; unrouted responses (maintenance 503, 404s, `/static`) are
skipped. Only **first page loads** count: routes that return a full HTML page. Data,
fragment and click-behind routes don't: `/players/{name}/career`, `/players/{name}/recent-matches`,
`/stats/friends/career`, `/stats/map-control`, `/stats/all`, every `*.json`, `*.bin` and `*.txt` route, uploads,
and anything under `/static`. Threshold 5 s. Key: the route template; context: the slowest time, the last 5
times and paths, how many slow requests since first seen. To keep new routes honest, every GET route is listed as
`page` or `not_page` in one table in `site_health`, and a test fails when a route is in neither. Cleared only by a PR.

### 2j. Warm cache files deleted before use: `warm_vanished` (from the replay-throughput branch)

Built on `afk/2026-11-05-replay-throughput` (merged as PR #187): the worker counts each map
variant's visibility cache file that its cleanup deleted before any round used it; the dispatcher reads the
counts from the worker's `/health` every cycle into `LIVE.warm_vanished` and logs an error when one rises; the
card gains a `warm_vanished` kind. Any count above 0 is worth a warning (none is expected); after 2 losses, with
failures and misses, that map variant has fallen back to the slower way. Results stay correct.

What Site health adds: the worker's counts live in memory and reset when the worker restarts, so the card's
evidence disappears with them. At the point where the dispatcher logs a rising count, it also calls
`site_health.record("warm_vanished", identity, ..., {"times": n, "map": ..., "boot": ...})`, so the history
survives worker and site restarts. Counts compare only within one worker process (main's boot-aware alarm): a
site restart empties the in-memory counts and re-logs every nonzero one, so it records only for a new worker
`boot` or a count above the stored row's `times`. The card shows both rows: the live one (this worker process's
count) and the stored history (B18). No extra `/health` call:
the dispatcher already reads it. No success signal, so only the PR with the fix clears it (the fix: protect the file until
its rounds are done, or keep more files per map). Off when `REPLAY_WARM_BUILDS` is off, and on the demo.

**Ordering** (owner, 2026-11-05): this work is built stacked on `afk/2026-11-05-replay-throughput` (482249a1), so
2j is built with the rest, last. Its PR stacks on the throughput PR: the throughput PR merges first, and this PR is
retargeted to `main` before it merges.

## 3. The card

- Renamed **Site health** (summary "Show site health"). The same card, endpoints and prompt; the routes keep
  their paths (`/replays/upload/errors.json`, `/replays/upload/errors/prompt.txt`) so nothing linked breaks.
- Sections in groups: **Pages** (crashes, slow pages), **Background work** (loops, Critical Util, worker,
  warm cache), **Data** (unscored matches, per-kill Impact, map pool, caches), then the existing **Replays**
  kinds unchanged.
- Recorded rows show count, first and last seen, and their `kind` and `key` (what a fixing PR names in `site_health_resolved.json`). No buttons.
  Today's rows are replay-shaped (`replay_errors._row`, upload.html's map/round/agent table), so site-event rows get
  their own shape (kind, key, count, first/last seen, error, context) and render path; `KINDS` gains a group.
- "Copy prompt" includes the new kinds, with the traceback tail as the error, the reproduce command where one
  exists, and a read-only `SELECT … FROM site_events WHERE kind = …` as the rest query; its first line says
  Site health.
- The card's computation stays cached for 55 s and is read-only: the site has no way to clear anything.
- A failed upload whose file is gone is retired from `upload_failed` and `store_failed` by a PR entry in
  `app/data/retired_uploads.json` (`upload_id`, `reason`, `fixed_by`); its other upload kinds stay visible.
  A later stored upload of the same file clears both failure kinds.

## 4. Tests

- `record`/`resolve`/`count`: upsert, count, scrubbing, retention, never raises when the DB is down.
- Each kind: one test that it is recorded or checked and shows on the card, and one that its success signal
  clears it (where it has one).
- The route table: every GET route is classified.
- Clearing by PR: an entry clears only rows last seen before the process started, a row seen after start stays,
  and every entry in `site_health_resolved.json` names a known kind.
- The card has no route that writes.
- Demo mode: no recording, no query, no thread (a lifespan test: no `site-health-flush` thread, no `site_events`
  query, no startup clearing).
- Clearing applies once: two simulated starts, a row recorded between them survives the second; a row inside the
  grace is cleared.
- A crashing request with the database down still returns its 500 in under 1 s.
- Each inner loop key is resolved only by its own step's success.
- Migration: `tests/test_migration_lifecycle.py`'s table set gains the new tables, and 0031 goes up, down and up.
- `app/services/site_health.py` joins the import list in `tests/replays/test_control_isolation.py` (replay_errors
  and replay_control_remote will import it).
- New test files and the new module go into `tests/suites.toml`.

## 5. Build order

1. Table, `site_health` module, the resolved-file clearing at startup, the `list` script, card rename and groups.
2. Pages that crash, loops (failures and quiet), Critical Util build.
3. Unscored matches, per-kill Impact, map pool notes.
4. Cache fallbacks, worker overruns and refusals, slow pages.
5. Warm cache history, once the replay-throughput branch is in.

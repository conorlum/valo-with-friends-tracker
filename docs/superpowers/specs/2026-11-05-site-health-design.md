# Site health: from replay errors to every failure the site can see

Status: design, awaiting the owner's review. Friends site only; nothing here runs on the demo.

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

Migration `0031_site_events`: one row per distinct problem.

| Column | Meaning |
|---|---|
| `kind` | one of the kinds below, e.g. `page_error`, `loop_failed` |
| `key` | what failed, stable across repeats: a route template, a loop name, a player id, a warm identity |
| `error` | the last error text, scrubbed (see below), at most 2,000 characters |
| `context` | JSONB: the last occurrence's details (traceback tail, exit code, figures) |
| `count` | occurrences since first seen or last cleared |
| `first_seen`, `last_seen` | UTC |

Primary key `(kind, key)`. A repeat updates the row: `count + 1`, new `error`/`context`, new `last_seen`.

- The model goes in its own file, `app/models/site_events.py`. It must not touch any model file the scoring
  manifest digests: an edit to those makes ingest refuse to run.
- **Recording never fails its caller.** `site_health.record(kind, key, error, context)` uses its own short
  session (never the caller's, which may be mid-rollback), catches everything, and logs if the record itself
  failed. One upsert statement.
- **Batched recording for hot paths** (cache fallbacks, slow pages): `site_health.count(kind, key, ...)` adds to an
  in-memory counter; one daemon thread, `site-health-flush`, writes the batch every 60 s as one statement. The
  site runs one uvicorn process (`render.yaml`), so per-process memory is the whole picture; the flush also runs
  at shutdown.
- **Scrubbing**: before anything is stored, remove credentials from connection URLs
  (`postgresql://user:pass@` becomes `postgresql://user:***@`) and the values of `password=`, `token=` and `key=`
  pairs. The card is visible to anyone with the upload code, and the repo is public.
- **Retention**: rows untouched for 30 days are deleted by the flush thread; at most 500 keys per kind (the oldest
  `last_seen` goes first).

### Clearing

- **Automatic**: the same job succeeding for the same key calls `site_health.resolve(kind, key)`, which deletes
  the row. A recompute aimed at the errors therefore clears them as it fixes them. Each kind below says what
  counts as success. A kind with no success signal is cleared only by the PR that fixes it.
- **By PR, never from the site** (owner's decision, 2026-11-05): there is no Clear button and no clear endpoint.
  The PR that fixes a problem adds an entry to the committed file `app/data/site_health_resolved.json`:
  `{"kind": ..., "key": ... or null for the whole kind, "fixed_by": "<PR or note>"}`. At startup the site deletes
  each matching row whose `last_seen` is before that process started, so only occurrences from before the fix's
  deploy are cleared; one that happens again after it stays. Entries are kept (they cost nothing and record what
  fixed what); a test checks every entry names a known kind.
- `scripts/site_health.py list` (through `with_friends_db.py --read-only`) prints the stored rows, to find the
  `kind` and `key` a PR's entry needs. It has no clear command.
- A cleared problem comes back on its next occurrence with `count` 1.

## 2. What is recorded or checked

"Recorded" kinds write to the table when they happen. "Checked" kinds read data that is already stored, the
way the card works today, and disappear on their own once the data is right.

### 2a. Pages that crash: `page_error` (recorded)

An `@app.exception_handler(Exception)` in `app/main.py` records, then returns the same 500 response as today.
Key: method plus the route template (`GET /players/{display_name}`), not the filled-in URL, so one bug is one
row. Context: exception type, the last 15 traceback lines, the path, whether the viewer was logged in.
HTTPException (404, 403) is not an error and is not recorded. Cleared only by a PR (a later success of the same
route doesn't prove the bug is fixed for other inputs).

### 2b. Critical Util background build: `util_build_failed` (recorded), `util_build_stale` (checked)

- The child process (`app/services/util_rebuild.py` `main`) wraps `_build` and records its own exception with the
  traceback tail before exiting non-zero; a success resolves the kind.
- The parent, in `request()`, checks the previous child's exit code before starting another, and records a
  non-zero code that the child didn't report itself (killed for memory, for example). Context: exit code, pid,
  how long it ran.
- `util_build_stale`: the stored build is out of date and has been for over 3 h (from `replay_util`'s stored
  build and `corpus_key`), with no build running.

### 2c. Background loops gone quiet: `loop_quiet` (checked), `loop_failed` (recorded)

All five loops already catch errors per pass, so a failing pass doesn't kill the thread; nothing restarts a thread
that does stop, and a pass that hangs logs nothing at all (the past "held_back N; 0 in flight" case).

- Each loop calls `site_health.beat(name)` at the end of every finished pass: an in-memory timestamp, no DB write.
- `loop_quiet` (checked, in-memory): a loop that is switched on and hasn't finished a pass in 3x its cycle plus
  2 min. Cycles: control dispatch 20 s, archive sync 300 s, match states 60 s, map pool daily (allowed 26 h).
  Shown with "last finished a pass at …". It disappears when the loop beats again.
- `loop_failed` (recorded): the `except` in each `run_forever` records with key = loop name, context = traceback
  tail. The next finished pass resolves it. The inner failures that are logged today record under the same kind
  with their own keys: `control_dispatch`, `reparse_pass` (`replay_control_remote.py:733`), `height_rebuild_step`
  (`:772`), `archive_sync`, `match_states`, `map_pool`.
- The card shows heartbeats only for loops in this process; with one uvicorn process that is all of them.

### 2d. Matches with no Impact scores: `unscored_match` (checked)

Matches older than 1 h (so an ingest in progress isn't listed) with no `impact_scores` row for any of their
rounds. One `NOT EXISTS` query. Link: the match page.

### 2e. Linked replays missing per-kill Impact: `kill_impact_missing` (checked)

Linked replays whose `kill_impact` is NULL, or whose stored fingerprint no longer matches the match's
`impact_scores` (`replays.db.impact_fingerprint`). The NULL check is one query. The fingerprint check is one query
per linked replay, so it runs in the card's existing background verifier thread (beside the feature check)
and its results are cached per (replay id, the stored split's fingerprint) until either changes. Reproduce:
`scripts/link_replays.py` for that match.

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

In `replay_control_remote.py`: a job that ran over `STALE_JOB_S` (line 456) and a refused job (line 666), plus
infrastructure failures (511). Key: map plus replay and round. A later stored result for that round resolves it.

### 2i. Slow pages: `slow_page` (recorded, batched)

A middleware times each request. Only **first page loads** count: routes that return a full HTML page. Data,
fragment and click-behind routes don't: `/players/{name}/career`, `/players/{name}/recent-matches`,
`/stats/friends/career`, `/stats/map-control`, `/stats/all`, every `*.json`, `*.bin` and `*.txt` route, uploads,
and anything under `/static`. Threshold 5 s. Key: the route template; context: the slowest time, the last 5
times and paths, how many slow requests since first seen. To keep new routes honest, every GET route is listed as
`page` or `not_page` in one table in `site_health`, and a test fails when a route is in neither. Cleared only by a PR.

### 2j. Warm cache files deleted before use: `warm_vanished` (from the replay-throughput branch)

Built on `afk/2026-11-05-replay-throughput` (commit 482249a1, not merged yet): the worker counts each map
variant's visibility cache file that its cleanup deleted before any round used it; the dispatcher reads the
counts from the worker's `/health` every cycle into `LIVE.warm_vanished` and logs an error when one rises; the
card gains a `warm_vanished` kind. Any count above 0 is worth a warning (none is expected); after 2 losses, with
failures and misses, that map variant has fallen back to the slower way. Results stay correct.

What Site health adds: the worker's counts live in memory and reset when the worker restarts, so the card's
evidence disappears with them. At the point where the dispatcher logs a rising count, it also calls
`site_health.record("warm_vanished", identity, ..., {"times": n, "map": ...})`, so the history survives worker and
site restarts. The card's existing live row stays; the stored row carries the history. No extra `/health` call:
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
- "Copy prompt" includes the new kinds, with the traceback tail as the error and the reproduce command where one
  exists.
- The card's computation stays cached for 55 s and is read-only: the site has no way to clear anything.

## 4. Tests

- `record`/`resolve`/`count`: upsert, count, scrubbing, retention, never raises when the DB is down.
- Each kind: one test that it is recorded or checked and shows on the card, and one that its success signal
  clears it (where it has one).
- The route table: every GET route is classified.
- Clearing by PR: an entry clears only rows last seen before the process started, a row seen after start stays,
  and every entry in `site_health_resolved.json` names a known kind.
- The card has no route that writes.
- Demo mode: no recording, no query, no thread.
- New test files and the new module go into `tests/suites.toml`.

## 5. Build order

1. Table, `site_health` module, the resolved-file clearing at startup, the `list` script, card rename and groups.
2. Pages that crash, loops (failures and quiet), Critical Util build.
3. Unscored matches, per-kill Impact, map pool notes.
4. Cache fallbacks, worker overruns and refusals, slow pages.
5. Warm cache history, once the replay-throughput branch is in.

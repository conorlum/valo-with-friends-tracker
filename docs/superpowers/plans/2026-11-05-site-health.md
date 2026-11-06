# Site health: implementation plan

Spec: `docs/superpowers/specs/2026-11-05-site-health-design.md` (revised after P2, 504d1398). The spec wins on
behaviour; this plan orders the work. Paths are under `webapp/`. Lines are at d8f673fe.

Conventions for every item:
- Python: `C:\Users\conor\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe`, pytest cwd
  `webapp/`, always `-n 0`. Suites per changed file: `python scripts/test_catalog.py plan <paths>`; the item's brief
  carries the default-lane command it prints. The PostgreSQL lane runs with
  `VALO_TEST_DATABASE_URL=postgresql://postgres@localhost:55439/valo_site_health_test` and
  `-n 0 -m "postgres and not corpus and not slow" --require-postgres`.
- Red first: the listed tests are written and fail before the code. Where the code comes first, a mutant spec.
- Demo mode (`settings.demo_mode`) is off-switch for every piece: no record, no query, no thread. Each item that
  adds a writer or reader has one demo test.
- Real-data probes: none. There is no local copy of the friends DB (SCOPE.md); items that read stored rows (W11)
  build their test rows from the models' real columns (`app/models/impact_score.py`, `app/models/replay.py`,
  `app/models/map_pool.py`), and the plan says the live counts are unverified.
- Pin part P1 (manifest digests, isolation scans, test catalog/lanes) runs for any item that touches `app/models/**`,
  `tests/suites.toml`, `replay_errors.py`, `replay_control_remote.py`, or adds a test file:
  `-m "slow or not slow" -n 0 tests/replays/test_control_isolation.py tests/replays/test_replay_isolation.py
  tests/test_active_manifest_verifies.py tests/test_impact_manifest.py tests/test_impact_manifest_v4.py
  tests/test_test_catalog.py tests/test_test_lanes.py`.
- Never edit `app/models/match.py`, `round.py`, `kill_event.py`. `site_health.py` imports only the standard library,
  SQLAlchemy, `app.config`, `app.db` and its own model (it is on the isolation list: no engine, detector, numpy).

## W4: table and migration

- New `app/models/site_events.py`: `SiteEvent` (`site_events`: `kind` String(32), `key` String(300), PK (kind, key);
  `error` Text not null default ''; `context` JSONType (as `app/models/replay.py:36`); `count` Integer not null;
  `first_seen`, `last_seen` DateTime(tz) not null; index on (kind, last_seen)) and `SiteEventResolution`
  (`site_event_resolutions`: `entry` String(64) PK = sha256 of the entry's canonical JSON; `kind`, `key` nullable,
  `cutoff` DateTime(tz) not null, `applied_at` DateTime(tz) nullable). Import in `app/models/__init__.py:24`'s block.
- New `alembic/versions/0031_site_events.py` (down_revision = the 0030 file's revision id): create both; downgrade
  drops both.
- Tests: `tests/test_migration_lifecycle.py:22-23` table set gains both tables; a new
  `test_site_events_upgrade_downgrade_upgrade` after `:58` in the same style: upgrade 0030 → head, insert a
  site_events row, downgrade 0030 (tables gone), upgrade head (tables back, empty).
- Check: PostgreSQL lane on `tests/test_migration_lifecycle.py` passes (including the committed demo seed load at
  head, `:100`); pin part P1 passes (the model file is new, digested files untouched).

## W5: `app/services/site_health.py` core

- `scrub(text)`: connection-URL passwords (`scheme://user:pass@` → `user:***@`), and the values of `password=`,
  `token=`, `key=` (case-insensitive, up to `&`, whitespace, `;` or a quote). `error` cut to 2,000 chars; context
  values scrubbed recursively (strings only).
- `record(kind, key, error, context=None, *, session_factory=None, at=None)`: one upsert in its own session from
  `app.db.SessionLocal` (dialect-aware: `sqlalchemy.dialects.postgresql.insert` / `sqlite.insert` with
  `on_conflict_do_update`: count + 1, error, context, last_seen), catches everything and logs; no-op in demo mode.
- `resolve(kind, key)`: deletes the row; keeps an in-memory `_open` set (filled by record/enqueue, and by a one-time
  load of the kind's keys) so a resolve of a key not open issues no statement.
- `enqueue(kind, key, error, context)`: appends to a bounded in-memory queue (1,000; drop oldest, count drops) with
  the occurrence time; never touches the DB.
- `count(kind, key, context_item=None)`: in-memory counter per (kind, key) with the last occurrence time and the last
  5 context items.
- `flush(session_factory)`: writes the queue (one upsert per row, one transaction) and the counters (count + n,
  last_seen = the last occurrence), then retention: delete `last_seen` older than 30 days, and per kind keep the
  newest 500 keys. Failure: logged, counters kept for the next flush.
- `beat(name, phase)` / `register(name, cycle_s, allowed_s=None)` / `loops()`: in-memory only.
- `start(session_factory) -> threading.Event | None`: daemon thread `site-health-flush`, every 60 s
  (`PROVISIONAL(D1)` constant `FLUSH_S`); returns None in demo mode; on stop it flushes once more.
- Tests (new `tests/test_site_health.py`, sqlite like `tests/replays/test_control_store.py`'s `db` fixture):
  upsert counts 1 → 2 with new error/last_seen; scrub of a postgres URL, `password=x&token=y`, `key=abc`; error cut
  to 2,000; record with a factory whose session raises → returns, logs, no raise; enqueue never opens a session
  (factory that fails the test if called); flush writes queued and counted rows with `last_seen` = occurrence time,
  not flush time; retention 30 days and 500 per kind (oldest go); resolve deletes, and a second resolve issues no
  statement (count statements with a `before_cursor_execute` listener); demo mode: record/flush/start do nothing and
  start returns None. New `tests/test_site_health_pg.py` (`pytest.mark.postgres`): the upsert on PostgreSQL (two
  records, count 2, JSONB context round-trips).
- `tests/suites.toml`: a new `[site-health]` suite (tests `tests/test_site_health*.py`; guards
  `app/services/site_health*.py`, `app/models/site_events.py`, `app/data/site_health_resolved.json`,
  `scripts/site_health.py`). `tests/replays/test_control_isolation.py:69-83` import list gains
  `app.services.site_health`.
- Check: the new tests pass (red first), PG lane on `test_site_health_pg.py`, pin part P1.

## W6: clearing by PR, startup wiring, the list script

- New `app/data/site_health_resolved.json` = `[]`.
- `site_health.apply_resolved(session_factory, started_at, entries=None)`: for each entry not in
  `site_event_resolutions`, insert it with `cutoff = started_at + GRACE_S` (600, `PROVISIONAL(D1)`); then for every
  stored entry with `applied_at` NULL and `cutoff <= now`, delete matching `site_events` rows (`kind`, and `key` unless
  null) with `last_seen < cutoff`, set `applied_at`. Called at startup and from each flush. Unknown kinds are skipped
  with a warning.
- `KNOWN_KINDS` in `site_health` (every recorded and checked kind from the spec, plus the replay kinds).
- `app/main.py:27` lifespan: take `started_at` (Python UTC) first; in non-demo mode call `apply_resolved` once, then
  start `site_health.start(SessionLocal)` beside the four loops (`:46`), its stop event in `stops`.
- New `scripts/site_health.py list [--kind K]`: prints kind, key, count, first/last seen, error's first line. No clear
  command. Run through `with_friends_db.py --read-only` (docstring says so).
- Tests (in `tests/test_site_health.py`, and `tests/test_app_lifespan.py:37`'s demo test extended): two simulated
  starts: an entry applies at start 1 (rows before the cutoff deleted once the clock passes it), a row recorded
  after the cutoff survives start 2; a row inside the grace is deleted; `key: null` clears the whole kind once only;
  every entry in the committed file names a known kind; demo lifespan: no `site-health-flush` thread, no
  `site_events` statement, `apply_resolved` not called; the script's `list` prints rows and has no other command.
- `tests/suites.toml` `[site-health]` gains the script test if separate.
- Check: new tests red first; `test_catalog.py plan app/main.py app/services/site_health.py` default lane; pin P1.

## W7: the card becomes Site health

- `app/services/replay_errors.py:28` `KINDS`: each entry gains a group ("Pages", "Background work", "Data",
  "Replays"); the existing 18 kinds are "Replays". New kinds are added here as their items land (this item adds the
  recorded-kind plumbing and no new kind; W8-W15 each add theirs).
- An event row shape `_event_row(kind, key, count, first_seen, last_seen, error, context, link=None, reproduce=None)`
  beside `_row` (`:79`); `collect` (`:238`) reads `site_events` once (`SELECT kind, key, count, first_seen, last_seen,
  error, context`), groups by kind, skipped in demo mode. `compute` (`:423`) output gains `group` and `shape`
  ("event" or "replay") per kind.
- `_rest_query` (`:460`): event kinds get `SELECT kind, key, count, first_seen, last_seen, error, context FROM
  site_events WHERE kind = '<k>' AND key IN (...)`. `prompt` (`:492`): first line "Investigate site health problems
  that need a human."; event rows print kind, key, count, first/last seen, error, context, reproduce.
- `app/templates/replays/upload.html:70-226`: summary "Show site health", status texts renamed, kinds shown under
  group headings in the spec's order; event-shaped kinds render a kind/key/count/first/last/error table (no buttons);
  replay kinds render as today.
- Tests: `tests/replays/test_replay_errors.py`: a stored site_events row of a test kind appears under its group with
  the event shape, and the prompt carries its kind, key and rest query; demo mode issues no site_events query.
  `tests/replays/test_replay_errors_web.py`: the routes keep their paths; no POST/PUT/DELETE route exists under
  `/replays/upload/errors` (scan `app.routes`); the page HTML says "Show site health".
- Check: red first; control-store-web and replay-web default lanes (`test_catalog.py plan`); the browser lane for
  `upload.html` (`tests/browser -n 0 -m browser --require-browser --require-fixtures` filtered by the plan's
  printed command) if the browser fixtures are available, else "not run" logged; pin P1.

## W8: pages that crash (`page_error`)

- `app/main.py`: `@app.exception_handler(Exception)` as a plain `def`: key `f"{method} {scope['route'].path}"` (no route
  → `"{method} (unrouted)"`), context {type, 15 traceback lines (`TRACEBACK_LINES`, `PROVISIONAL(D1)`), path without
  query, logged_in from `request.session`}; `site_health.enqueue("page_error", ...)`; returns
  `PlainTextResponse("Internal Server Error", 500)` (what ServerErrorMiddleware sends today). Demo: no enqueue.
- `KINDS` gains `page_error` ("Pages").
- Tests (new `tests/test_site_health_pages.py`): a test route that raises → 500, queue holds one entry keyed by
  the template; HTTPException 404 → nothing queued; with `SessionLocal` patched to raise, the 500 returns in under
  1 s (the handler never opens a session); demo: nothing queued; the card lists a stored `page_error` row under
  Pages.
- Check: red first; app-shell lane; new file in `[site-health]`; pin P1.

## W9: loops (`loop_quiet`, `loop_failed`)

- In each loop (`replay_control_remote.py:792`, `replay_archive_sync.py:89`, `replay_match_states.py:74`,
  `map_pool_refresh.py:177`): `site_health.register(name, cycle)` in `start()` (`:806`, `:102`, `:94`, `:196`) when it
  starts the thread; `beat(name, "start")` / `beat(name, "end")` round each pass; the outer `except` records
  `loop_failed` key = loop name (`record`, background thread), a finished pass resolves that key.
- Inner keys, each resolved only where its step succeeds: `reparse_pass` (`replay_control_remote.py:733`; resolved
  after the re-parse step returns), `height_rebuild_step` (`:772`; after the heights step returns),
  `archive_sync:worker` (`replay_archive_sync.py:81-83`; a cycle with no worker error), `map_pool:check`
  (`map_pool_refresh.py:151-154` per page, `:171-173` whole check; resolved when the check's errors count is 0).
- `site_health.quiet_loops(now)`: registered loops whose last "end" beat (or registration) is older than 3x cycle +
  120 s (`PROVISIONAL(D1)`); map pool allowed 26 h. `replay_errors.collect` adds them as `loop_quiet` rows
  ("Background work") with "last finished a pass at …" and "in a pass since …".
- Tests (new `tests/test_site_health_loops.py`): for each loop, a pass that raises records `loop_failed:<name>`, the
  next clean pass resolves it; an inner failure records its key and a pass where that step fails again keeps it
  while an unrelated success doesn't resolve it; quiet: a registered loop with no beat for 3x cycle + 120 s shows,
  a beat clears it; unregistered (switched off) loops never show. Follow `tests/replays/test_control_remote.py`'s
  cycle tests and `tests/test_map_pool_refresh.py` for fakes.
- Check: red first; control-store-web, replay-web, map-pool lanes; pin P1.

## W10: Critical Util build (`util_build_failed`, `util_build_stale`)

- `app/services/util_rebuild.py:64` `main`: wraps `_build` (`:93`); on exception `site_health.record(
  "util_build_failed", "build", ...)` and return 3 (`EXIT_RECORDED`); on a finished build resolve the key; the
  lock-held early exit (`:76-81`) resolves nothing.
- `request()` (`:47`): before starting another child, read the previous child's `returncode`; non-zero and not 3 →
  `enqueue("util_build_failed", "build", ..., {exit_code, pid, ran_s})` (keep the start time beside `_child`).
- `util_build_stale` (checked, in `app/services/site_health_checks.py`, new): reads `util_model` key/version/
  computed_at for "values" (columns only, never `data`), the newest `computed_at` of current util rows newer than the
  build, and `pg_locks` for `LOCK_KEY` (skip on non-PostgreSQL); stale and over 3 h (`PROVISIONAL(D1)`) with no lock
  held → one row. The `corpus_key` compare (`replay_util.corpus_key`, lazy import) runs in the card's background
  verifier (W11's pass), not in `compute`.
- Tests (extend `tests/critutil/test_util_rebuild.py`; follow `:18`, `:44`): a failing build records and returns 3;
  a successful build resolves; the lock-held exit resolves nothing; the parent enqueues a code 9 child, not a code 3
  one; the stale check: build older, util row 4 h newer, no lock → row; under 3 h → none.
- `site_health_checks.py` is on the isolation import list (`test_control_isolation.py:69-83`) and in `[site-health]`.
- Check: red first; critical-util lane; pin P1.

## W11: checked data kinds (`unscored_match`, `kill_impact_missing`, `map_pool_note`)

- In `site_health_checks.py`, each a read-only query, called from `replay_errors.collect`, skipped in demo:
  - `unscored_match`: matches with `matches.created_at` older than 1 h and no
    `impact_scores` row for any of their rounds (`NOT EXISTS`). Link: the match page.
  - `kill_impact_missing`: linked replays of scored matches whose `kill_impact` is NULL (one query); the fingerprint
    compare (`kill_impact->>'fingerprint'` vs `replays.db.impact_fingerprint`) runs in a new background pass beside
    `replay_errors._verify_features` (`:181`), with its own pending flag (`_kill_impact_pending`), recomputed each
    pass, results read by `collect`. Reproduce: `scripts/link_replays.py` for the match.
  - `map_pool_note`: `map_pool_patch_notes` rows with `last_error` set: url, miss_count, error.
- Tests (new `tests/test_site_health_checks.py`, rows built from the models' real columns): a match with rounds and
  no impact rows, older than 1 h, shows; one younger than 1 h doesn't; a scored match doesn't; a linked replay with
  NULL kill_impact on a scored match shows, on an unscored match doesn't (it is already `unscored_match`); a stale
  fingerprint shows after the background pass and goes once it matches; a patch-notes row with last_error shows,
  without doesn't; demo: no query.
- Live counts unverified (no local friends DB).
- Check: red first; control-store-web and map-pool lanes; pin P1.

## W12: cache fallbacks (`cache_fallback`, batched)

- `site_health.count("cache_fallback", "<cache>:<failure>", <player or viewer id>)` at: `player_view_cache.py`
  368-404 (`player_view_cache:invalid`), 583 and 604 (`player_view_cache:decode`), 765 (`player_view_cache:prewarm`);
  `site_stats.py` 163/201/220 (`site_stats:<what>`); `viewer_site_stats_cache.py` 81/88; `site_stats_cache.py` 356;
  `account_groups.py` 238 (`account_group_cache:read`), 280/298 (`account_group_cache:prewarm`); `routers/players.py`
  291 (`player_page:write_through`). Each call sits beside the existing log line; nothing else changes.
- Tests (new `tests/test_site_health_caches.py`): for one representative site per module, force the failure (as
  the existing tests in `tests/test_player_view_cache.py`, `tests/test_site_stats.py`, `tests/test_account_groups.py`
  do) and assert the counter holds the key and the id; a flush stores one row with the count. A grep-style test lists
  every `site_health.count("cache_fallback"` call and checks each key is in `KINDS`' documented key list.
- Check: red first; player-page, site-stats, accounts-circle lanes; pin P1.

## W13: worker overruns and refusals

- `replay_control_remote.py` 456 (ran over `_job_stale_s`): `record("worker_overrun", "<map>:<replay>:<round>")`;
  511 (infrastructure failure, not compat at 513): `record("worker_refused", "<map>:infrastructure")`; 666 (refused):
  `record("worker_refused", "<map>:<reason class>")` (class = the exception type name), replay and round in
  context. Resolves: a stored result for a round resolves its `worker_overrun` key and its map's `worker_refused`
  keys (`store_round` success at ~517 and the gaps-only path `_keep_gaps` at 479-484).
- Tests (extend `tests/replays/test_control_remote.py`, following its cycle tests): overrun records and a later
  stored result resolves; refusals of 50 rounds on one map make one row with count 50; compat refusals record
  nothing; the gaps-only path resolves.
- Check: red first; control-store-web lane; pin P1 (isolation scan of replay_control_remote).

## W14: slow pages (`slow_page`, batched)

- `app/site_health_middleware.py` (new): a pure ASGI middleware added in `app/main.py` after the maintenance
  middleware; times to the last body chunk; reads `scope["route"].path` after the call; skips no route, non-GET,
  and routes classified `not_page`; over 5 s (`SLOW_PAGE_S`) → `site_health.count("slow_page", template, {path, s})`
  keeping the slowest. Demo: not added.
- `site_health.PAGE_ROUTES`: every GET `APIRoute` path in `app.routes`, mapped to "page" or "not_page" (R5's list is
  not_page: `/players/{display_name}/career`, `/players/{display_name}/recent-matches`, `/stats/friends/career`,
  `/stats/map-control`, `/stats/all`, every `.json`/`.bin`/`.txt` route, uploads; `/health` not_page).
- Tests (new `tests/test_site_health_slow.py`): every GET route is classified (and none classified that doesn't
  exist); a page route made slow with a fake clock counts, a not_page route doesn't, a fast page doesn't; a
  streaming response is timed to its last chunk; demo: middleware absent.
- Check: red first; app-shell lane; pin P1.

## W15: warm cache history (`warm_vanished` stored) — last

- `replay_control_remote.py:698-704`: where the rising count logs, `record("warm_vanished", identity, ..., {"times":
  n, "map": ...})` only when `n` is above the stored row's `context.times` (read the row once per rise). Off when
  `REPLAY_WARM_BUILDS` is off and in demo.
- `replay_errors`: the existing live `warm_vanished` kind (`:36`, `:259`) also lists stored rows not live.
- Tests (extend `tests/replays/test_control_remote.py` near the warm_vanished tests): a rise records; a site restart
  (empty state) re-reading the same count records nothing new; a further rise updates `times`; demo / warm builds
  off: nothing.
- Check: red first; control-store-web lane; pin P1.

## Checkpoints

Full suite (by the loop, `checkpoint.py request`) after W6, W9, W12, and `final` at wrap-up.

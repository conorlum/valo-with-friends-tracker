# Fast match lists: a mask-decode cache and a stored per-match replay state

Date: 2026-10-14. Status: design, reviewed once (AFK run `2026-10-14-match-list-speed`).

## Goal

`/matches` and `/sessions` load within about 1 s of a warm load on every visit, including the first one after
a deploy. Measured on the live site: cold 15.7-17.3 s, warm 0.6-0.9 s, cold again after the 55 s TTL in
`app/services/replay_progress.py:27`. Locally (22 matches with replays, 462 rounds): cold 6.6 s, warm 0.2 s;
`compute()` 6.3 s.

## Cause

Both pages call `replay_progress.match_states()` (`app/routers/matches.py:45`, `app/routers/sessions.py:82`),
which on a TTL miss runs `replay_progress.compute()` inside the request. A local profile puts 17.3 of its 17.5 s
in `replay_control.resolve_current_geometry` (`app/services/replay_control.py:173`) calling
`map_feature_sources.base_snapshot` (`app/replays/map_feature_sources.py:71`): a pure-Python decode of each
map's sight, walk and barrier mask PNGs plus `pack_mask`, redone for every map on every `plan()` call. Its
answer depends only on the files' bytes, the map's `tags.json` entry and the scale. The dispatcher pays the
same cost on every cycle (`replay_control_remote._submit`, `:558` and `:576`), in the same one-CPU process.

## In scope / out of scope

In: (1) a memo for `base_snapshot`; (2) a table with one row per match that has a stored replay, kept current
by a background refresher; (3) `match_states()` reading that table; (4) `/sessions` gets it through the same
function (R3).

Out (R7): pagination of `/matches` (the page is about 2.35 MB of HTML: a follow-up), any change to the replay
worker, to what the dispatcher sends or its order and pacing, and anything in `app/scoring/`. No feature
switch (R5); `render.yaml` is unchanged. `app/replays/map_feature_sources.py` is also copied into the
replay-worker image (`replay_worker/Dockerfile:36`), but nothing there calls `base_snapshot`, so the worker's
behaviour doesn't change; the cache stays standard-library only, as that folder requires
(`tests/replays/test_control_isolation.py:37-43`).

## 1. The decode cache

- The current body is split in two, unchanged in what it computes: `_decode(map_name, raws, entry, scale)`
  works on bytes already read (`raws`: the sight, walk and barrier PNG bytes, the barrier `None` when the file is
  absent), and `_base_snapshot(map_name, folder, entry, scale)` reads the files then calls `_decode`.
- `base_snapshot(map_name, folder, entry, scale)` keeps its signature. It reads the three files once, builds a
  key from those same bytes, and on a miss decodes **those bytes** (`_decode`), so the stored value is always
  the decode of the hashed bytes, even if a file changes meanwhile. The key: the map name; the sha256 of each
  file's bytes (`None` for an absent barrier); the sha256 of the canonical JSON (`sort_keys`, `(',', ':')`
  separators) of the entry's `cover_paint`, `cant_walk_paint` and `specials`; and `repr(scale)`. These are
  everything `_decode` reads.
- Single flight: one module lock is held across the lookup and the decode, so the refresher, the dispatcher and
  a panel poll that miss together decode once; the others wait and then hit. Decodes are rare (once per map per
  process, and after an asset edit), so serialising them costs nothing in steady state.
- A hit or a miss returns `copy.deepcopy` of the stored value: it holds a list (`specials`) and a dict
  (`legacy`) a caller could mutate; the bytes inside are shared. (Today `base['specials'] is entry['specials']`;
  after this it is an equal copy. Nothing relies on the identity.)
- An edited asset has new bytes, so it is decoded afresh by construction; nothing is invalidated.
- Bound: an LRU of 32 entries (13 maps today, about 0.5 MB each). Errors are not cached: a
  `FeatureInputsError` propagates as now, every call. `base_snapshot.cache_clear()` and
  `base_snapshot.cache_info()` (hits, misses, size) exist for tests.
- Only the decode is cached. `_current_snapshot` and `identify_features` stay as they are unless the
  measurement below says otherwise.
- Both callers (`replay_control.resolve_current_geometry`, `control_feature_artifacts.py:194`) import
  `base_snapshot` at call time, so tests that monkeypatch `map_feature_sources.base_snapshot` keep working.

**Proof that it changes no answer (R2).**
- Every map in `CONTROL_DIR` with a `<map>.sight.png` (13): one uncached `_base_snapshot` compared with the
  miss, and with the hit. Slow (about 1 s a map): its own test file.
- `compute()` equivalence **on the real tagged source**: the shared `factory` fixture strips `map_features`
  (`tests/replays/test_control_store.py:38-49`), so `base_snapshot` would never run. This test restores the real
  `replay_control._current_snapshot` after the fixture, runs `compute()` with the cache cleared and again warm,
  asserts the results equal (minus `checked_at`), and asserts `cache_info()` counted misses on the first run and
  only hits on the second, so it fails if the cache is bypassed.
- On the local data DB (the run's controller, once): `compute()` with the cache bypassed and with it on,
  compared whole, recorded in the run log.
- All pin tests stay green unchanged.

**Measurement.** After the cache, `compute()` on the local data DB is timed and recorded. If it is still over
about 2 s, the next cost (expected: parsing the 1.4 MB `tags.json` twice per map in `_current_snapshot`) is
looked at in its own step, under the same identity rule.

## 2. The table

Migration `0023` (the next unused on the base), additive only: a new table, no foreign keys (deleting a replay
or match never blocks on it or cascades into it), no change to existing tables.

`replay_match_states`:

| column | type | meaning |
| --- | --- | --- |
| `match_uuid` | `Uuid(as_uuid=False)`, primary key | the replay's match (lower-cased, as `compute()` keys it) |
| `done` | integer, not null | rounds whose state is `done` |
| `total` | integer, not null | rounds of the replay |
| `first_step` | text, null | the earliest stuck step's key (`STEPS` order), null when all done |
| `checked_at` | timestamptz, not null | the start of the last successful refresh that confirmed this row |
| `recipe` | text, not null | the site recipe it was judged against |
| `control_revision`, `gaps_revision` | integer, not null | as `compute()["judged_against"]` |

The primary key is the index the page's read uses. The judged-against columns and `checked_at` never decide a
state; `match_states()` uses them only to discard a row it can't trust (section 4).

## 3. The refresher

- New module `app/services/replay_match_states.py`:
  - `refresh(db, now=None) -> dict`: in demo mode returns `{}` and touches nothing. Otherwise takes
    `pg_try_advisory_xact_lock(<its own key>)` on PostgreSQL (returns `{"locked_out": 1}` when another
    instance holds it); notes its start time; runs `replay_progress.compute(db)` itself (R4: the dispatcher's
    plans don't give the same states, see `SCOPE.md`); then in the same transaction inserts new matches, updates
    rows whose `(done, total, first_step, recipe, control_revision, gaps_revision)` differ, deletes rows whose
    match no longer has a replay, and sets `checked_at` to the start time on every remaining row (one
    `UPDATE`); commits. Portable SQL (ORM reads and writes, no `ON CONFLICT`), so the SQLite fixtures run it.
    Returns `{"written", "deleted", "unchanged", "seconds"}`. Then `replay_progress.seed(result, started)`
    hands the fresh result to the upload panel's cache, stamped with the refresh's start, under
    `replay_progress._lock`.
  - `run_forever(session_factory, stop, every_s=REFRESH_S)`: each pass opens a fresh session, calls `refresh`,
    logs its counts and seconds at INFO, and closes the session; any exception is logged, rolled back, and the
    loop goes on. It refreshes at once, then every `REFRESH_S` (60 s), until `stop` is set. It never raises.
  - `start(session_factory) -> threading.Event | None`: None in demo mode; otherwise starts the daemon thread
    (`name="replay-states"`) and returns its stop event.
- `app/main.py`'s lifespan adds `replay_match_states.start(SessionLocal)` to `stops`. It runs whether or not
  `REPLAY_CONTROL_REMOTE` is on (local dev included), and needs no env var (R5).
- Why its own thread and not the dispatcher's cycle: the dispatcher's plans differ from `compute()`'s, it is
  off locally, and R7 forbids changing its pacing. A separate thread with its own lock is the smallest change
  that runs everywhere.
- Interval: 60 s, matching the panel's one-minute poll. The local refresh cost is measured; the live one is
  only known after the merge, from the INFO line in Render's log, and the interval is checked against it then
  (it scales with the corpus's round count).
- Staleness (R4): a row lags the DB by at most one interval plus one refresh. A revision bump, a height rebuild
  or an edited figures file shows after the next refresh, with no manual step, because the refresher judges
  with the same planners as now.
- A one-off script, `scripts/refresh_replay_states.py`, runs `refresh()` once against `DATABASE_URL` and prints
  the counts: for local use and the migration check. Not needed after a deploy.

## 4. `match_states()`

- Keeps its signature and return shape (`{"done", "total", "label", "automatic"}` per lower-cased uuid).
- Demo mode: returns `{}`, as now, with no query.
- Otherwise: the wanted ids are lower-cased and those that aren't valid UUIDs dropped (PostgreSQL rejects a
  non-UUID bind for a `uuid` column; SQLite would pass it), then read with `IN` in chunks of at most 900. It
  never plans and never calls `compute()` or `cached()`. A failure (the table missing before a migration) logs,
  rolls back and returns `{}`, as now.
- A row is used only if its `recipe`, `control_revision` and `gaps_revision` equal this process's
  (`uploads.site_recipe()`, `cf.CONTROL_REVISION`, `replay_gaps.GAPS_REVISION`), its `checked_at` is within
  `STALE_S` (5 × `REFRESH_S`) of now, and its `first_step` is null or a key in `STEPS`. Otherwise the match is
  left out, as if it had no row. This covers the old instance's refresher during a deploy, the previous
  deploy's rows before the first refresh, and a refresher that keeps failing.
- A watchable match with no usable row shows a muted "…" titled "Not checked yet" instead of the "No replay
  for this match" dash. `watchable` is defined wherever the cell is included (`matches/list.html:12`,
  `sessions/detail.html:329`). Site copy: tier 1.
- `/sessions` uses the same function, so it gets the fix with no change of its own (R3). Both routers pass
  only the watchable ids, a few dozen.

## 5. The upload panel (`progress.json`)

Keeps its own `compute()` under the 55 s TTL: its per-step round counts can't be rebuilt from per-match rows,
and R4 requires them identical. It gets faster by itself (the decode cache) and is usually served by the
refresher's seeded result.

## 6. The two cheap wins inside `compute()`

Not built. Once `compute()` runs off the request path every 60 s, loading fewer `Replay` columns or planning
once instead of twice saves little, and both touch `plan()`, which the dispatcher also runs. Left as a
follow-up if the live refresh cost is too high.

## 7. After the deploy

A merge deploys the web service; its build runs `alembic upgrade head`, creating the empty table (the first
deploy) or keeping the last deploy's rows. At startup the refresher's first pass fills or confirms them (one
`compute()` after a cold decode of 13 maps, about 17 s of CPU the first time in that process). Until then
watchable matches show "Not checked yet" (rows under other revisions or not confirmed within `STALE_S` are
ignored). No one-off command is needed. The demo service runs the same migration from its own build and never
writes a row.

## 8. Tests

- Cache: identity on every real map's assets; `compute()` equal cold and warm on the real tagged source with
  hit and miss counts; copy isolation; an edited file is decoded afresh; the decode uses the hashed bytes (a file
  swapped between read and decode can't be stored under the old key: test with a patched `_decode` that sees
  the bytes it was given); errors not cached; single flight (two threads missing together decode once); LRU
  bound.
- Fixtures: the new table is added to `CONTROL_TABLES` (`tests/replays/test_control_store.py:35`), so "empty
  table" tests the empty table and not the exception path. `run_forever` is tested synchronously (a stop event
  set after one or two passes), since each thread sees its own `sqlite:///:memory:` database otherwise.
- Refresher: after `refresh()`, every row equals `compute()["matches"]` for the same DB (R4), over the fixture
  states (nothing computed, partly computed with gap runs, a failed row, an older recipe); a second refresh with
  nothing changed writes no row (`written == 0`) but moves `checked_at`; a deleted replay's row goes; demo mode
  writes nothing; a `refresh` that raises leaves the old rows, and `run_forever` logs it and goes on; `seed` is
  stamped with the refresh's start.
- `match_states()`: with `replay_control.plan`, `replay_progress.compute` and `replay_progress.cached` patched
  to raise, returns the stored rows; an empty table → `{}`; the table missing → `{}`; a non-UUID id is ignored;
  a row with another recipe or revision, older than `STALE_S`, or with an unknown `first_step` is left out;
  the template shows "Not checked yet" for a watchable match with no usable row.
- The existing `test_a_match_is_up_to_date_only_when_every_round_is_done` keeps its expected values; it calls
  `refresh()` where it used to clear `_cached`, because the column now comes from the table.
- `test_control_isolation.py`: the new module and script are added to its list (`:69-80`), since `main.py`
  imports the refresher inside `lifespan`, where the `import app.main` check never reaches.
- Migration up, down, up on the scratch DB and on the local data DB, with the same counts after each fill.
- A timing check of the real `/matches` page: three warm, one first request after a restart, and one within
  about 20 s of a restart (while the refresher's cold decode runs).

## Risks

- A refresh holds a transaction for the length of `compute()`. Reads only, until the writes at the end; it
  takes no row locks the dispatcher needs.
- Two instances during a deploy: the advisory lock makes one skip; rows written by the old code are ignored by
  the new code's `match_states()` when their revisions differ.
- The first refresh after a deploy pays the cold decode (about 17 s of CPU) in the background. Rendering the
  2.35 MB page is Python CPU work too, so page loads in that window share the one CPU with it (and with the
  dispatcher, which already does the same decode today) and are slower until it ends. Measured locally by the
  timing check's "within 20 s of a restart" figure.

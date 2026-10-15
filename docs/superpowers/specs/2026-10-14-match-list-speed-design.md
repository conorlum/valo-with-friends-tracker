# Fast match lists: a mask-decode cache and a stored per-match replay state

Date: 2026-10-14. Status: design (AFK run `2026-10-14-match-list-speed`).

## Goal

`/matches` and `/sessions` load within about 1 s of a warm load on every visit, including the first one after
a deploy. Measured on the live site: cold 15.7-17.3 s, warm 0.6-0.9 s, cold again after the 55 s TTL in
`app/services/replay_progress.py:27`.

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
switch (R5); `render.yaml` is unchanged.

## 1. The decode cache

- `base_snapshot(map_name, folder, entry, scale)` keeps its signature. The current body becomes
  `_base_snapshot`; `base_snapshot` reads the three PNG files' bytes (the barrier may be absent), and looks up a
  key made of: the map name; the sha256 of each file's bytes (or a marker for an absent barrier file); the
  sha256 of the canonical JSON (`sort_keys`, no spaces) of the entry's `cover_paint`, `cant_walk_paint` and
  `specials`; and `repr(scale)`. A hit returns a deep copy of the stored value; a miss decodes with the
  unchanged code, stores it, and returns a deep copy. Reading three small files and hashing them costs
  milliseconds.
- Why the bytes and not mtimes: an edited asset (a rebuilt mask, a new barrier paint) has new bytes, so it is
  decoded afresh by construction, whatever its timestamps. Nothing has to be invalidated.
- Why a deep copy: the value holds a list (`specials`) and a dict (`legacy`) that callers could mutate; the
  bytes inside are immutable and are shared, so the copy is cheap.
- Bound: an LRU of 32 entries under a lock (13 maps today, about 0.5 MB each). Only the decode is cached:
  `_current_snapshot` and `identify_features` stay as they are unless the measurement below says otherwise.
- Errors are not cached: a `FeatureInputsError` from a bad PNG propagates as now, every call.
- Both callers (`replay_control.resolve_current_geometry` and
  `control_feature_artifacts.py:194`) import `base_snapshot` at call time, so tests that monkeypatch
  `map_feature_sources.base_snapshot` keep working.

**Proof that it changes no answer (R2).** A test runs, for every map in `CONTROL_DIR` that has a
`<map>.sight.png` (13 today), `_base_snapshot` and `base_snapshot` twice (miss, then hit), and asserts the three
results are equal; and that mutating a returned value doesn't change the next one. A second test edits a copy
of one map's asset in a temp folder and asserts the next call returns the fresh decode. A `compute()`
equivalence test on the existing fixtures runs `compute()` with the cache cleared and again warm and compares
everything but `checked_at`. All pin tests listed in `PROMPT.md` stay green unchanged.

**Measurement.** After the cache, `compute()` on the local data DB is timed (the run's profiler script) and
recorded in the run log. If it is still over about 2 s, the next cost (expected: parsing the 1.4 MB
`tags.json` twice per map in `_current_snapshot`) is looked at in its own step, under the same identity rule.

## 2. The table

Migration `0023` (the next unused on the base), additive only: a new table, no change to existing ones.

`replay_match_states`:

| column | type | meaning |
| --- | --- | --- |
| `match_uuid` | `Uuid(as_uuid=False)`, primary key | the replay's match (lower-cased, as `compute()` keys it) |
| `done` | integer, not null | rounds whose state is `done` |
| `total` | integer, not null | rounds of the replay |
| `first_step` | text, null | the earliest stuck step's key (`STEPS` order), null when all done |
| `computed_at` | timestamptz, not null | when the refresher computed it |
| `recipe` | text, not null | the site recipe it was judged against |
| `control_revision`, `gaps_revision` | integer, not null | as `compute()["judged_against"]` |

The primary key is the index the page's read uses. The judged-against columns are informational (debugging a
stale-looking row); nothing reads them to decide a state. Model in `app/models/replay.py` beside the others.

## 3. The refresher

- New module `app/services/replay_match_states.py`:
  - `refresh(db) -> dict`: takes `pg_try_advisory_xact_lock(<its own key>)` on PostgreSQL (skips and returns
    `{"locked_out": 1}` when another instance holds it), runs `replay_progress.compute(db)` itself (R4: the
    dispatcher's plans don't give the same states, see `SCOPE.md`), then in the same transaction: upserts
    every match whose `(done, total, first_step)` differs from its row, inserts new ones, deletes rows whose
    match no longer has a replay; commits. Portable SQL (ORM reads and writes, no `ON CONFLICT`), so the
    SQLite test fixtures run it. Returns counts (`written`, `deleted`, `unchanged`). It also hands the fresh
    `compute()` result to `replay_progress` as its cached value (`replay_progress.seed(result)`), so the upload
    panel's next poll in this process doesn't recompute.
  - `run_forever(session_factory, stop, every_s=REFRESH_S)`: refreshes once at once, then every `REFRESH_S`
    (60 s) until `stop` is set. Any exception is logged and the loop goes on, after a `db.rollback()`; it never
    raises.
  - `start(session_factory) -> threading.Event | None`: returns None in demo mode, otherwise starts the daemon
    thread (`name="replay-states"`) and returns its stop event.
- `app/main.py`'s lifespan adds `replay_match_states.start(SessionLocal)` to `stops`. It runs whether or not
  `REPLAY_CONTROL_REMOTE` is on (local dev included), and needs no env var (R5).
- Why its own thread and not the dispatcher's cycle: the dispatcher's plans differ from `compute()`'s, it is
  off locally, and R7 forbids changing its pacing. A separate thread with its own lock is the smallest change
  that runs everywhere.
- Interval: 60 s, matching the panel's one-minute poll. After the cache a refresh is expected to cost well under
  2 s of CPU a minute; the measured figure goes in the run log, and the interval is revisited only if it isn't.
- Staleness (R4): a row lags the DB by at most one interval plus one refresh. A revision bump, a height rebuild
  or an edited figures file shows after the next refresh, with no manual step, because the refresher judges
  with the same planners as now.
- A one-off script, `scripts/refresh_replay_states.py`, runs `refresh()` once against `DATABASE_URL` and prints
  the counts: for local use and the migration check. Not needed after a deploy.

## 4. `match_states()`

- Keeps its signature and return shape (`{"done", "total", "label", "automatic"}` per lower-cased uuid).
- Demo mode: returns `{}`, as now, with no query.
- Otherwise one query: `SELECT match_uuid, done, total, first_step FROM replay_match_states WHERE match_uuid IN
  (...)` for the wanted ids, in chunks of 1000 (the full list is about 3,700 ids, most without a replay). It
  never plans and never calls `compute()` or `cached()`. A failure (the table missing before a migration) logs,
  rolls back and returns `{}`, as now.
- A match with a replay and no row yet: the cell shows a muted "…" titled "Not checked yet" instead of the
  "No replay for this match" dash. The template knows the match is watchable (`watchable`), so it can tell
  these apart. Site copy: tier 1.
- `/sessions` uses the same function, so it gets the fix with no change of its own (R3); its test asserts the
  same no-planning property.

## 5. The upload panel (`progress.json`)

Keeps its own `compute()` under the 55 s TTL: its per-step round counts can't be rebuilt from per-match rows,
and R4 requires them identical. It gets faster by itself (the decode cache) and is usually served by the
refresher's seeded result.

## 6. The two cheap wins inside `compute()`

Not built. Once `compute()` runs off the request path every 60 s, loading fewer `Replay` columns or planning
once instead of twice saves little, and both touch `plan()`, which the dispatcher also runs (R7's spirit). Left
as a follow-up if the measured refresh cost is too high.

## 7. After the deploy

A merge deploys the web service; its build runs `alembic upgrade head`, creating the empty table. At startup
the refresher's first pass fills it within a few seconds (one `compute()` after a cold decode of 13 maps, about
17 s the first time in that process, off the request path). Until then the column shows "Not checked yet" for
watchable matches; the page itself is fast from the first request. No one-off command is needed. The demo
service runs the same migration from its own build and never writes a row.

## 8. Tests

- Cache: identity on every real map's assets (miss, hit and uncached equal), copy isolation, an edited file is
  decoded afresh, errors not cached, LRU bound.
- `compute()` equal with the cache cold and warm on the existing fixtures (`tests/replays/test_replay_progress.py`
  fixtures).
- Refresher: after `refresh()`, every row equals `compute()["matches"]` for the same DB (R4), over the
  existing fixture states (nothing computed, partly computed, a failed row, an older recipe); a second refresh
  with nothing changed writes nothing; a deleted replay's row goes; demo mode writes nothing; a `compute()` that
  raises leaves the old rows and doesn't raise out of `run_forever`'s body.
- `match_states()` with `replay_control.plan`, `replay_progress.compute` and `replay_progress.cached` patched to
  raise returns the stored rows; with the table empty it returns `{}`; the template shows "Not checked yet" for a
  watchable match with no row.
- The existing `test_a_match_is_up_to_date_only_when_every_round_is_done` keeps its expected values; it calls
  `refresh()` where it used to clear `_cached`, because the column now comes from the table.
- Migration up, down, up on the scratch DB and on the local data DB, with the same counts after each fill
  (`PROMPT.md`'s migration check).
- A timing check of the real `/matches` page, three warm and one after a restart (`PROMPT.md`'s timing check).

## Risks

- A refresh holds a transaction for the length of `compute()`. Reads only, until the writes at the end; it
  takes no row locks the dispatcher needs.
- Two instances during a deploy: the advisory lock makes one skip; a skipped refresh is harmless.
- The first refresh after a deploy pays the cold decode (about 17 s of CPU) in the background, competing with
  the first page loads in that minute: they are database reads now, so they don't wait on it.

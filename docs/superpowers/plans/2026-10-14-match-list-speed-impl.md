# Fast match lists: implementation plan

Design: `docs/superpowers/specs/2026-10-14-match-list-speed-design.md`. Steps in order; each is one commit
(`W<n>: …`). All commands run from `webapp/` with the venv's Python (`<python>`). `<pin>` is the pin-test list
below.

Pin tests (checked by hand: each hashes outputs, pins inputs, or scans imports and source), all under
`tests/replays/`: `test_control_pinned_inputs.py`, `test_control_reference.py`, `test_control_format.py`,
`test_control_isolation.py`, `test_map_feature_inputs.py`, `test_map_feature_artifacts.py`,
`test_control_feature_artifacts_db.py`, `test_feature_transport.py`,
`test_control_remote.py`, `test_heights_remote.py`, `test_quiet_routes.py`.
`test_control_feature_artifacts_db.py` needs `VALO_TEST_DATABASE_URL` (the scratch DB) to run rather than skip.
`tests/replays/test_replay_progress.py` is not a pin (S4 edits its setup); every step runs it as a regular test.
No slow marker exists in the repo (no pytest.ini/conftest).

## S1 (W5). The decode cache

Files: `app/replays/map_feature_sources.py`; new `tests/replays/test_map_feature_sources_cache.py` (fast) and
new `tests/replays/test_map_feature_sources_real_assets.py` (every real map; slow).

- Split the current `base_snapshot` body: `_decode(map_name, raws, entry, scale)` (from `masks = …` down, using
  `raws = (sight_bytes, walk_bytes, barrier_bytes_or_None)` instead of reading files) and
  `_base_snapshot(map_name, folder, entry, scale)` = read the files as today (`is_file()` for the barrier) then
  `_decode`. No change to what is computed.
- New `base_snapshot(map_name, folder, entry, scale)`: read the bytes once; key = (map name, sha256 hex of each
  of the three (None for an absent barrier), sha256 hex of
  `json.dumps({k: entry.get(k) for k in ('cover_paint', 'cant_walk_paint', 'specials')}, sort_keys=True, separators=(',', ':'))`,
  `repr(scale)`); under one module `threading.Lock` held across lookup and decode: hit → move to end, count a
  hit; miss → `_decode(map_name, raws, entry, scale)` on those bytes, store, evict the oldest past 32, count a
  miss. Return `copy.deepcopy` of the value. An exception stores nothing. Attach
  `base_snapshot.cache_clear()` (empties and zeroes counts) and `base_snapshot.cache_info()` →
  `{"hits", "misses", "size"}`. Standard library only (`hashlib`, `json`, `copy`, `threading`,
  `collections.OrderedDict`). `base_snapshot` calls `_decode` through the module global, so tests can patch it.
- An autouse fixture in the fast file calls `base_snapshot.cache_clear()` before and after each test.
- Fast tests (tmp folders with copies of real PNGs from `replay_control.CONTROL_DIR`, so each decode is one
  map's): (a) miss then hit both equal `_base_snapshot`, counts 1 miss / 1 hit; (b) mutating a returned
  `specials` list or `legacy` dict doesn't change the next result; (c) replacing the walk PNG with another
  map's gives the fresh decode (a second miss) equal to `_base_snapshot` on the new files; (d) a change to
  `cover_paint` / `specials` / `scale` is a miss; (e) a corrupt PNG raises `FeatureInputsError` on two calls in
  a row, size stays 0; (f) each file is read once per call (patch `pathlib.Path.read_bytes` to count), which
  also shows the decode works on the hashed bytes; (g) with module `_decode` patched to a cheap fake that sets an
  `entered` Event then sleeps ~0.2 s, a second thread started after `entered` on the same cold key gives one
  miss and one hit (deterministic: catches a lock released during decode); (h) with `_decode` patched to a cheap
  fake, 33 distinct keys (vary `scale`) leave 32 entries, and a hit on key 1 just before the 33rd insert keeps
  key 1 and evicts key 2 (LRU order);
  (i) `replay_progress.compute(db)` on the `test_replay_progress.py` fixtures (`factory`, `db`, `linked`) **with
  `replay_control._current_snapshot` restored to the real one** (`monkeypatch.setattr(rc, '_current_snapshot',
  <the function saved at import before the fixture patched it>)`) and the cache cleared. `linked` is on Ascent,
  which has 3 enabled `ascent_replay_v1` bundles in the real tags.json, so `base_snapshot` is reached once per
  `plan()` (twice per `compute()`) and every round comes out `features_pending` (no artifact in the fixture DB;
  probe: `tmp_s1i_test.py` in the run folder, about 1.2 s cold). Assert: the two runs are equal minus
  `checked_at`; after the first, `cache_info()` is 1 miss and 1 hit; after the second, 2 more hits and no new
  miss; and `rc.resolve_current_geometry(db, "Ascent").feature_input.key` is the same with
  `map_feature_sources.base_snapshot` patched to `_base_snapshot`, cold, and warm (the equality of the
  compute results alone can't see a wrong value, since every round is `features_pending`).
- Slow test: for each `<map>.sight.png` in `CONTROL_DIR` with a `tags.json` entry and a `maps.json`
  `xMultiplier`, after `cache_clear()`: `_base_snapshot(...) == base_snapshot(...)` (miss) `== base_snapshot(...)`
  (hit). No marker.
- Check: `<python> -m pytest -q tests/replays/test_map_feature_sources_cache.py tests/replays/test_map_feature_sources_real_assets.py tests/replays/test_replay_progress.py <pin>`
  all pass. Bite: mutants (no deepcopy; key without the walk bytes; key without `specials`; storing on an
  exception; no bound; lock released during decode) each caught.
- Then (controller): time `compute()` on the local data DB, and compare it whole with the cache bypassed
  (patch `base_snapshot` to `_base_snapshot`) vs on: equal minus `checked_at`. Both into `LOG.md`.

## S2 (W6). The table, the writer and the script

Files: `app/models/replay.py` (new `ReplayMatchState`), new `alembic/versions/0023_replay_match_states.py`,
new `app/services/replay_match_states.py` (`refresh`, `LOCK_ID`, `REFRESH_S = 60`, `STALE_S = 5 * REFRESH_S`),
`app/services/replay_progress.py` (`seed`), new `scripts/refresh_replay_states.py`,
`tests/replays/test_control_store.py:35` (`CONTROL_TABLES` += the new table),
`tests/replays/test_control_isolation.py:69-80` (add the module and the script),
new `tests/replays/test_replay_match_states.py`.

- Model and migration as the design's section 2 (`revision = '0023'`, `down_revision = '0022'`; no foreign
  keys; downgrade drops the table).
- `refresh(db, now=None)` as the design's section 3, `now` a `datetime` (UTC) defaulting to the clock at the
  start. `LOCK_ID` a new int distinct from `replay_control_remote.LOCK_ID` (`7_346_120_117`, the only other
  key in `app/`). Every return carries the same keys: `{"written", "deleted", "unchanged", "locked_out",
  "seconds"}` (zeros where nothing happened; demo mode returns all zeros and touches nothing).
- `replay_progress.seed(result, started: float)` sets `_cached = (started, result)` under `_lock`, where
  `result` is `compute()`'s own return (not `refresh`'s counts) and `started` is `time.monotonic()` taken at the
  refresh's start. `REFRESH_S`/`STALE_S` live in `replay_match_states`; `replay_progress` never imports it at
  the top (S4 imports it inside `match_states`), so there is no import cycle.
- Script: `refresh()` once via `app.db.SessionLocal`, prints the returned counts. Docstring: local use; the
  site refreshes by itself.
- Tests (SQLite fixtures from `test_replay_progress.py`): after `refresh`, the rows equal
  `compute()["matches"]` per match (done, total, first) in each fixture state (nothing computed; partly computed
  with gap runs; a failed row; an older recipe); judged-against columns equal `compute()["judged_against"]`; a
  second refresh writes no row (`written == 0`) and moves `checked_at` to its own `now`; a replay deleted → its
  row deleted; demo mode → all-zero counts and no rows; `seed` makes `cached()` return the refresh's compute
  result without calling `compute` (patch `compute` to raise after the refresh), and the stamp is the start:
  with `refresh`'s monotonic clock patched, `cached(db, now=started + TTL_S - 1)` is served and
  `now=started + TTL_S` recomputes.
- PG test (skipped without `VALO_TEST_DATABASE_URL`, following `tests/replays/test_replay_upload_held.py:112`):
  one session holds `pg_advisory_xact_lock(LOCK_ID)`; a `refresh` in a second session returns
  `locked_out == 1` and writes no row.
- Optional PG migration test for 0023 (up, down, up), following the 0022 one, if it exists.
- Check: new tests + `tests/replays/test_replay_progress.py` + `<pin>` pass (with
  `VALO_TEST_DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/afk_match_list_test` on the
  line, so the PG tests run).
- Then (controller): the migration check of `PROMPT.md` on the scratch DB and the local data DB, filling with
  the script.

## S3 (W7). The refresher thread

Files: `app/services/replay_match_states.py` (`run_forever`, `start`), `app/main.py` (lifespan), tests in
`tests/replays/test_replay_match_states.py`.

- `run_forever(session_factory, stop, every_s=REFRESH_S)`: `while True:` open a session; `refresh`; log
  `"replay states: written %d, deleted %d, unchanged %d in %.2fs"` at INFO; on any exception
  `log.exception`, roll back; close; `if stop.wait(every_s): return`.
- `start(session_factory)`: None in demo mode; else a daemon thread `replay-states`, returns the stop event.
- Lifespan: add `replay_match_states.start(SessionLocal)` to `stops`, with a one-line comment like its
  neighbours'.
- Tests (synchronous; no real thread for the DB work): with `stop` set before the call, one pass runs and
  returns; a `refresh` patched to raise on the first pass is logged and the second pass runs (a fake `stop` whose
  `wait` returns False then True); `start` in demo mode returns None and starts no thread; `start` otherwise
  starts a daemon thread named `replay-states` (patch `run_forever` to a no-op); a `refresh` returning the
  locked-out counts is logged without error; the lifespan (run with `asyncio.run` over
  `app.main.lifespan(app)`, every `start` patched) calls `replay_match_states.start(SessionLocal)`.
- Check: new tests + `tests/replays/test_replay_progress.py` + `<pin>` pass.

## S4 (W8). The pages read the table

Files: `app/services/replay_progress.py` (`match_states`), `app/templates/replays/_up_to_date_cell.html`,
`tests/replays/test_replay_progress.py` (the existing match-states test), tests in
`tests/replays/test_replay_match_states.py`.

- `match_states` as the design's section 4. It imports `replay_match_states` (for `STALE_S`) inside the
  function. The staleness filter is in SQL: `ReplayMatchState.checked_at >= cutoff` with
  `cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_S)` (SQLite returns naive datetimes, so no
  Python-side subtraction of stored values). The recipe/revision and known-step filters may be in SQL or Python.
- Template: a watchable match with no usable state shows `<span class="muted" title="Not checked yet">&hellip;</span>`;
  a non-watchable one keeps the dash.
- Existing test `test_a_match_is_up_to_date_only_when_every_round_is_done`: replace each
  `monkeypatch.setattr(progress, "_cached", None)` before a `match_states` call with
  `replay_match_states.refresh(db)`; expected values unchanged. Same for any other `match_states` caller in tests
  (`git grep -l match_states -- tests`).
- New tests: `plan`, `compute` and `cached` patched to raise → the stored rows come back; empty table → `{}`;
  table dropped → `{}` with no exception; demo mode → `{}`; a non-UUID id is ignored with the other ids still
  answered; a row with another recipe, another control or gaps revision, `checked_at` older than `STALE_S`, or an
  unknown `first_step` is left out; the template renders "Not checked yet" for a watchable match with no state
  and the dash for a non-watchable one; `app.routers.sessions.match_states is replay_progress.match_states`.
- The edit to `test_replay_progress.py` is setup only: no expected value changes.
- Check: new tests + `<pin>` + `tests/test_sessions.py` + `tests/replays/test_replay_routes.py` (it renders
  `matches/list.html` with a watchable match, :149-163) + every file from `git grep -l match_states -- tests`
  pass.

## S5 (W9). Measure and write up (controller)

- The timing check of `PROMPT.md`: three warm, the first request after a restart, and one within about 20 s
  of a restart; beside the base figures in `LOG.md`.
- `match_states()` timed for the full watchable list with `plan` patched to raise (well under 100 ms).
- `pr-body.md` in the run folder with the deploy note (design section 7).
- Full suite in parts with the known failures deselected, plus the run's new test files as their own part.

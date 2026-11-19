# Handoff: the Critical Util build's store crashes the friends DB (build-out session)

Start with `/build-out` on this file. Branch from a fresh `origin/main` in a worktree (`EnterWorktree`); local
`main` was 125 commits behind on 2026-11-13. Never merge: open a PR and hand back the link.

## What is happening (found 2026-11-13, read-only)

- `webapp/app/services/util_build_remote.py` `_store()` sends the worker's whole result (~39 MB of JSON text) as
  one bound value and runs `CAST(:data AS JSONB) -> 'values'` inside Postgres (three casts when `state.refit`).
- The friends DB is tiny: `shared_buffers` 64 MB, `effective_cache_size` 192 MB. The backend doing the cast is
  most likely OOM-killed, and Postgres goes through crash recovery.
- Evidence: every background process and `pg_stat_checkpointer.stats_reset` restarted at **2026-11-13 18:46:52**,
  one second after the `remote:store` failure at 18:46:51 (`SSL SYSCALL error: EOF detected`).
  `pg_postmaster_start_time()` is 2026-08-26, so it was a crash restart, not a planned one. Only this one crash
  was lined up; the other 41 `remote:store` failures (07:31–18:46) are assumed to be the same.
- The same crashes explain the `GET /health` page_error (123 since 2026-11-09 23:07: "the database system is
  not yet accepting connections / consistent recovery state"). The first came ~20 min after the `win` product
  was stored on 11-09 22:46, which fits an earlier util write crashing it too.
- The retry limit (`MAX_TRIES` 3) is per build key, and the key changes whenever util rows change, so each new
  upload or re-parse re-dispatches and stores again. It will keep crashing until fixed.
- 39 MB is the normal full-corpus size, not a regression: the last good build (07:29) covered 10 matches
  (5.6 MB of text, `rows` 5.3 MB of it, 905 KB on disk); the corpus is now 90 replays / 1,944 util rows.
- `/util` and the replay tab currently read that 10-match build (the "util build out of date" health item).

## Code touched

- Writers: `util_build_remote._store` (remote, live: `UTIL_BUILD_REMOTE` on in `render.yaml`) and the local
  child build `app/services/util_rebuild.py` (check how it writes; it may do the same JSONB cast).
- Readers of `util_model.data`: `replay_util.py` (~line 52, the stored-build read behind `page_values` /
  `match_payload`), `util_model.load` (~line 236, the `win` row), `util_build_remote.py:162` and
  `coach/snapshot.py:80` (`data["excluded"]` from `win`, small: 9 KB).
- ~15 test files mention `util_model`; run `python scripts/test_catalog.py plan --git` for the lanes.

## Direction proposed (owner to confirm in the build-out)

1. Store `values` compressed (zlib/gzip bytes, ~6 MB) in a new column or table (a migration), decoded in
   Python on read; the server never parses the 39 MB. Keep `win` as JSONB (tiny), or treat it the same way.
   Decide whether the worker should compress before sending, so the site never holds 39 MB as text either.
2. A store failure that loses the connection (OperationalError) gives up on that key at once instead of
   retrying, since each retry crashes the DB again.
3. Consider whether the reader caches the decoded build per process (39 MB JSON decoded per request is heavy).

The owner can also move the friends DB to a bigger plan in the Render dashboard as a stop-gap. Never mention
Render cost (the account is free).

## Checks

- Read-only DB access only, through `webapp/scripts/with_friends_db.py --expect-database valowithfriendsdb
  --read-only`. Prod writes are the owner's to run.
- After the fix deploys: the `util_build_failed` and `util_build_stale` health rows clear, `util_model`
  `values` covers all replays (`data->'matches'`), and `pg_stat_checkpointer.stats_reset` stops moving.

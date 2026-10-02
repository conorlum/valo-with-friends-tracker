# Replay archive: implementation plan review

P4 review of `2026-10-01-replay-archive-impl.md` by a fresh reviewer (AFK run 2026-10-01-control-heights). S1
was built while the review ran; its fixes land in S2's commit.

| # | Sev | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocker | The tombstone lookup in `store_replay` breaks every sqlite fixture that lists its tables (`test_replay_store.py` `TABLES`, reused by upload/control/routes/view tests; `test_ingest_replay.py`) | **Fixed in S3**: `ReplayDeletion.__table__` added to those lists (the one allowed fixture change); every step from S3 on checks `pytest tests/replays -q`. |
| 2 | blocker | The web app can't tell a refused ack from a down worker; refused acks would be re-sent forever; answers for failed / unknown / running jobs undefined | **Fixed.** `WorkerRefused(WorkerError)` for 4xx (recorded as `refused: …`); null only for 5xx/unreachable. Worker answers: unknown or failed job → `200 {"archived": false, "result": "nothing held"}`; a job not finished → `503` (retry); a tombstoned match → `200 {"archived": false, "result": "refused", "reason": "deleted on request"}`. |
| 3 | should-fix | Admin reparse fails `refresh_job`'s sha check (the row has no sha) | **Fixed.** `/reparse` answers `sha256` and `size`; the admin route sets them on the row; tested route to store. |
| 4 | should-fix | Acks must not depend on the in-memory job table or a pending file | **Fixed.** The worker reads the job's meta from `jobs/<id>/job.json` when it has forgotten the job; a reparse ack on the same bytes updates `replay_id`/`played_at` and touches no file (test added). |
| 5 | should-fix | sqlite reuses a deleted row's id, so `replay_id` can repeat | **Already so**: only a strictly smaller id is stale; an equal id with other bytes is archived. Web tests don't assert a growing id. |
| 6 | should-fix | Setting `REPLAY_ARCHIVE_DIR` in `render.yaml` keeps files the moment it merges, before the friends are told | **Fixed (tier 2, D5).** The disk is declared; `REPLAY_ARCHIVE_DIR` is commented out like `REPLAY_CONTROL_REMOTE`; DEPLOY.md sets it in the dashboard after the friends' message. |
| 7 | should-fix | Dockerfile/entrypoint details | **Fixed.** `USER worker` removed; `ENTRYPOINT ["sh", "/srv/replay_worker/entrypoint.sh"]`; `chown` of the mount root only, failure logged not fatal; `exec setpriv … "$@"` with `CMD` kept; `RUN command -v setpriv` guard; `*.sh text eol=lf`. |
| 8 | should-fix | `scripts/reingest_replays.py` doesn't catch `StoreRefused` | **Fixed in S3.** |
| 9 | should-fix | Recovery gaps (half export, thread race, sweep loop, reparse copy race) | **Already so in S2** (export deleted before re-queue; recovery before the thread starts; the archive-off path keeps the blocking `get()`); the copy race now fails the reparse cleanly. |
| 10 | should-fix | Ack after `_finish`'s commit, short timeout, `store_outcome` in the same commit | **Fixed in S4** (5 s ack timeout). |
| 11 | nit | Two live Workers on one dir flake on Windows | **Fixed**: the restart test writes job records by hand and starts one Worker. |
| 12 | nit | Reserve term, `PY`, uuid lowercasing | `parse_reserve` already includes `queue_size x cap`; `PY` = the main checkout's `webapp\.venv\Scripts\python.exe`; admin routes lowercase. |

Open blockers after revision: none.

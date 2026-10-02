# Replay archive (part 1 of the control-heights spec): plan review

P2 review of `docs/superpowers/specs/2026-10-01-control-heights-design.md`, section "### 1. The .vrf archive"
and the Archive testing bullets, by a fresh reviewer with only the spec and the repo (AFK run
2026-10-01-control-heights). The reviewer edited nothing. Dispositions are the author's; the spec's part 1 is
revised to match. Parts 2-5 are untouched.

Constraint for every disposition: the user's build prompt lists what part 1 must cover tonight (the disk
folders, restart recovery from `job.json`, the space reserve, eviction by match date, the idempotent
newer-wins ack with re-sends, `kept_existing` shown as such, the `reparse` job and `reparse_archive.py`,
`delete_replay_data.py` with tombstones a restored disk can't get around, the upload page, `render.yaml`'s
disk, the README) and that **with no archive disk the worker behaves exactly as today**. A finding that
proposed deferring one of those is accepted in a smaller form, not as a deferral.

| # | Sev | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocker | The worker has no match date: not in the result, `link_inputs` or the `.vrf` header (`replay_worker/server.py:263-273`, `app/replays/header.py:3-14`); only `matches.played_at` on the web side | **Fixed.** The ack carries `played_at` from the linked match; unlinked → null, and the worker uses the ack time. Spec says so. |
| 2 | blocker | No on/off switch; an unmounted path would "persist" to ephemeral disk | **Fixed.** `REPLAY_ARCHIVE_DIR`, unset by default; on only if set, a mount point (`os.path.ismount`, a test-only setting skips that) and a create+rename probe passes. Off = today's code path exactly; an ack answers `{"archived": false, "reason": "archive off"}`. Test added. |
| 3 | blocker | A job collected after a deletion re-stores the deleted replay: `store_replay` has no tombstone check (`app/replays/store.py`) | **Fixed.** `store_replay` refuses a tombstoned match under the advisory lock (`StoreRefused("deleted on request")`) for every caller; the upload is acked `failed`. |
| 4 | blocker | No worker token exists; an admin route on the public web service needs a secret; a worker pull needs a URL and a secret on a worker designed to hold none | **Fixed.** Push, not pull: the web app sends deletes and the full tombstone list to the worker. Worker routes stay unauthenticated like `/jobs` (private network only). The web app's admin routes need a new secret `REPLAY_ADMIN_TOKEN` (`sync: false`; 404 when unset; never the friends' upload code). |
| 5 | should-fix | No background collector: collection happens only while the uploader's tab polls; reparse jobs have no poller | **Fixed, smaller than the spec.** The ack is sent inline right after the store. A small web-app thread (the `replay_control_remote` pattern) re-sends missing acks and pushes tombstones when the worker has restarted. Reparse is driven by its own script, which polls an admin status route that runs the same `refresh_job`. No general background collector. |
| 6 | should-fix | "Newer wins" by `accepted_at` has no column (`Replay` has only `created_at`) and can invert under the lock | **Fixed.** The version is `replays.id`: every store or replace inserts a new row inside the advisory lock, so a larger id is the newer file. The ack sends `replay_id` and the DB row's `sha256`. |
| 7 | should-fix | Free-space checks during a live parse misfire; exports kept in `jobs/` pin GB; the cap is 181,035,000 bytes, not 200 MB | **Fixed.** The export is deleted the moment condense returns. Space is budget-based on the worker's own accounting (archive + pending + queued bytes against the disk total), not live free space. Cap figure corrected. |
| 8 | should-fix | Ack, eviction, deletion and sync race on `index.json` | **Fixed.** One archive lock around every index read-modify-write and its file operations; stray temp files are removed on start. |
| 9 | should-fix | Non-root ownership of the mount left open; a pserv has no health check to fail | **Fixed.** Entrypoint runs as root, `chown`s the mount to uid 10001, then `setpriv` drops to it. A failed probe turns the archive off with the reason on `/health`; it never crashes the worker. Unverifiable without Docker: flagged for the first deploy. |
| 10 | should-fix | Restart recovery is hollow because the web app gives up after 20 min | **Rejected as a deferral** (prompt requires it), kept small: `job.json` per job; on start queued/parsing re-queue in creation order, done/failed reload. A deploy takes minutes, inside `STUCK_AFTER`. TTLs bound what nobody collects. |
| 11 | should-fix | Defer reparse and most deletion machinery | **Rejected as a deferral** (prompt requires both), kept small: reparse reuses `replay_uploads` and `refresh_job`; deletion is push-only (no hourly pull, see 4); a restored disk is covered because the web app pushes the full list whenever the worker's boot id changes. |
| 12 | should-fix | Migration, config and secrets missing | **Fixed.** Migration 0015: `replay_uploads.store_outcome`, `replay_uploads.archive_ack`, table `replay_deletions`. `render.yaml`: `disk` + `REPLAY_ARCHIVE_DIR` on the worker, `REPLAY_ADMIN_TOKEN` on the web. |
| 13 | nit | `kept_existing` has two causes | **Fixed.** The page shows the store's own reason. |
| 14 | nit | Docstrings at `replay_upload.py:14-15`, `server.py:24` contradict the new promise | **Fixed** with the code. |
| 15 | nit | An ack-free design is smaller | **Rejected.** Without the ack the archive could hold a recording the DB doesn't; the `replay_id` ack is small. |

Open blockers after revision: none.

# Replay archive: implementation plan

Builds part 1 of `docs/superpowers/specs/2026-10-01-control-heights-design.md` ("### 1. The .vrf archive", as
revised after `2026-10-01-replay-archive-plan-review.md`). One commit per step. Every check runs from
`webapp/` with the venv's Python (`PY`); tests use sqlite and stub parsers, no Postgres, no Docker.

Invariant for every step: **with `REPLAY_ARCHIVE_DIR` unset the worker's behaviour is today's**, and the
existing tests (`test_replay_worker.py`, `test_replay_upload.py`, `test_replay_store.py`,
`test_replay_worker_control.py`, `test_archive_replays.py`) pass unchanged (baseline: 53 passed, 5 skipped,
1 pre-existing Windows failure in `test_control_off_answers_404_and_parsing_is_untouched`, which also fails on
`origin/main`).

## S1. The worker's archive store — `replay_worker/archive.py` (new)

Standard library only (the worker's server imports nothing else). One class, `Archive(root, settings)`:

- `open(root, require_mount, total_bytes=None) -> (Archive | None, reason)`: on only if the dir is set, is a
  mount (skipped when `require_mount` is false, for tests), and a create+rename probe works. Makes `jobs/`,
  `pending/`, `archive/`, `acks/`; removes `archive/*.tmp`.
- One `threading.Lock` around every index read-modify-write and file operation.
- `index.json`: `{"files": {uuid: {sha256, size, map, played_at, accepted_at, replay_id}}}`, rewritten by
  temp + `os.replace`.
- `budget()`, `evict()` (earliest `played_at`, else `accepted_at`, first; only `archive/`), `status()` for
  `/health` (`enabled, kept, bytes, oldest_played_at, budget_bytes, boot_id`).
- `hold_pending(job_id, vrf_path, meta)`: move the upload into `pending/` with `<job>.json` (match uuid, sha,
  size, map); drops it at once if the match is tombstoned.
- `ack(job_id, body, job_meta)`: the spec's rules (checks, newer-wins by `replay_id`, outcomes, tombstone
  refusal), the answer stored in `acks/<job>.json` and returned again for a repeat. Atomic copy-hash-rename
  into `archive/` (the pattern of `webapp/scripts/archive_replays.py`).
- `delete(uuid)`, `set_tombstones(uuids)`: delete pending + archived files of tombstoned matches; persist
  `tombstones.json`.
- `sweep(now)`: `PENDING_TTL` (2 d) on pending, 7 d on acks; returns what it removed.
- Constants: `PARSE_RESERVE_FACTOR = 70`, `ARCHIVE_SLACK = 5 GB`, `PENDING_TTL`, `UNCOLLECTED_TTL`, `ACK_TTL`.

Check: `PY -m pytest tests/replays/test_replay_archive.py -q` passes, covering: probe off for unset / non-mount
/ unwritable; ack archive on `stored`, delete on `kept_existing`/`failed`; mismatched uuid/sha refused; repeat
ack same answer; older `replay_id` doesn't replace; `unchanged` archives only when missing; eviction order and
`jobs/`, `pending/` untouched; tombstone refusal and deletion of a "restored" file; TTL sweep.

## S2. The worker server on the archive — `replay_worker/server.py`

- `Settings`: `archive_dir` (`REPLAY_ARCHIVE_DIR`), `archive_require_mount` (default true; `REPLAY_ARCHIVE_REQUIRE_MOUNT=0`
  only for tests), `archive_total_bytes` (default: `shutil.disk_usage`).
- Archive off: every existing code path unchanged (`temp_root`, `rmtree` in `_run`'s finally).
- Archive on: job folders under `jobs/`; `job.json` written at each state change (`queued`, `parsing`,
  `done`/`failed`, kind, match uuid when known, sha, size, created); `result.json` when done; the export
  deleted right after condense; on success the upload moves to `pending/`; on failure the folder keeps only
  `job.json`. Eviction before each parse and after each archive write.
- Restart: re-queue `queued`/`parsing` jobs by `created`, reload `done`/`failed`; TTL sweep on start and
  every hour from the parse thread's idle loop.
- Intake (`POST /jobs`): `503` when `jobs + pending + upload > total − ARCHIVE_SLACK − 70 x cap`.
- Reparse: a second queue, taken only when no upload waits. `POST /reparse {"match_uuid"}`: `404` not
  archived, `409` tombstoned, `202 {"id", "status"}`; the job copies the archived file in and runs as an upload
  but never makes a pending file.
- Routes: `POST /jobs/<id>/ack`, `POST /reparse`, `POST /archive/delete {"match_uuid"}`,
  `POST /archive/tombstones {"match_uuids": [...]}`, `GET /archive` (the index list), `/health` gains
  `archive`. With the archive off: ack → `200 {"archived": false, "reason": "archive off"}`, the others `404`.
- A job finishing for a tombstoned match: result kept for the web app (which refuses to store it), pending
  dropped.

Check: `PY -m pytest tests/replays/test_replay_worker_archive.py tests/replays/test_replay_worker.py tests/replays/test_replay_worker_control.py -q`
— the new file covers archive-off parity (folder gone, no `pending/`, ack "archive off"), a stored job pending
then archived through HTTP, restart recovery (a new `Worker` on the same dir re-queues and reloads), intake
refusal with a small `archive_total_bytes`, reparse queued behind uploads and leaving the archive in place,
delete and tombstone routes.

## S3. Web: tombstones, migration and the store refusal

- `alembic/versions/0015_replay_archive.py`: `replay_uploads.store_outcome` (String 16), `replay_uploads.archive_ack`
  (String 64), table `replay_deletions(match_uuid Uuid PK, deleted_at timestamptz, reason text)`.
- `app/models/replay.py`: the columns and `ReplayDeletion`.
- `app/replays/store.py`: after the advisory lock, a tombstoned uuid raises `StoreRefused("deleted on request")`.

Check: `PY -m pytest tests/replays/test_replay_store.py tests/replays/test_replay_archive_web.py -q` (new test:
a tombstoned match is refused for `source="upload"` and `"local"`); and the migration imports and chains:
`PY -c "import importlib.util,sys;s=importlib.util.spec_from_file_location('m','alembic/versions/0015_replay_archive.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);assert m.down_revision=='0014';print('ok')"`.

## S4. Web: the inline ack and `kept_existing` shown

- `WorkerClient.ack(job_id, body)`, `WorkerClient.archive()`, `.reparse(uuid)`, `.delete(uuid)`, `.tombstones(list)`.
- `refresh_job`: records `store_outcome` (the store's action, or `failed`); after the commit, a best-effort
  ack (`send_ack(db, upload, client)`) — `played_at` from the linked match, else null; records the answer
  (`archived`, `deleted`, `off`, `refused: …`) in `archive_ack`; a worker error leaves it null. A
  `kept_existing` upload keeps status `stored` (the DB check constraint is unchanged) with the store's own
  reason as its note, and `_status_body` adds `"kept_existing": true`; `upload_job.html` says "Not stored: …"
  for it. A refused store (tombstone) is `failed` and acked `failed`.

Check: `PY -m pytest tests/replays/test_replay_upload.py tests/replays/test_replay_archive_web.py -q` — new
cases: an upload against an archive-on stub worker ends `archived` with the file in `archive/`; a
`kept_existing` second recording is shown as such and its pending file is gone.

## S5. Web: the archive sync thread — `app/services/replay_archive_sync.py` (new)

`cycle(session_factory, client, state)`: `GET /health`; archive off → nothing; a new `boot_id` → push the full
`replay_deletions` list; then re-send acks for uploads with `archive_ack` null, `store_outcome` set,
finished within `PENDING_TTL`. `start(session_factory)` runs it every 300 s in a daemon thread when uploads
are enabled; wired in `app/main.py`'s lifespan beside `replay_control_remote`.

Check: `PY -m pytest tests/replays/test_replay_archive_web.py -q` — a lost ack is re-sent and recorded; a
"restored" archived file for a tombstoned match is deleted after a new boot id.

## S6. Web: admin routes and the two scripts

- `app/config.py`: `replay_admin_token`. `app/routers/replay_admin.py` (new): every route 404s unless the
  token is set and `Authorization: Bearer <token>` matches (`hmac.compare_digest`).
  - `POST /replays/admin/delete {"match_uuid", "reason"}`: tombstone (idempotent), delete the stored replay and
    its rows (`store._delete` under the advisory lock), push the delete to the worker (best effort; the sync
    thread covers a miss).
  - `GET /replays/admin/archive`: the worker's index list.
  - `POST /replays/admin/reparse {"match_uuid"}`: a `replay_uploads` row (`session_key="admin-reparse"`), the
    worker job; `GET /replays/admin/uploads/<id>`: `refresh_job` and the status.
- `scripts/delete_replay_data.py <uuid> [--reason] [--site URL]`, `scripts/reparse_archive.py [--map]
  [--since] [--match] [--site URL]`: standard library HTTP, the token from the `REPLAY_ADMIN_TOKEN`
  environment variable, never an argument or a file.

Check: `PY -m pytest tests/replays/test_replay_archive_web.py -q` — routes 404 without the token or with a
wrong one; delete removes the replay rows and the worker's files and a later store is refused; reparse via
the routes ends `replaced`/`unchanged` with the archived file in place; the scripts' `--help` runs.

## S7. The promise, the deploy config and the docs

- `upload.html`: the new promise (tier 2 wording). `replay_upload.py` and `server.py` docstrings.
- `render.yaml`: worker `disk: {name: replay-archive, mountPath: /var/replay, sizeGB: 50}`, worker env
  `REPLAY_ARCHIVE_DIR=/var/replay`; web env `REPLAY_ADMIN_TOKEN` (`sync: false`).
- `replay_worker/Dockerfile`: an entrypoint (`replay_worker/entrypoint.sh`) run as root: `chown` the mount
  when `REPLAY_ARCHIVE_DIR` is set, then `exec setpriv --reuid=10001 --regid=10001 --init-groups python3 -m
  replay_worker.server`. `REPLAY_WORKER_TMP` stays `/jobs` (used only with the archive off).
- `replay_worker/README.md`: the archive, its disk, the routes.

Check: `PY -c "import yaml;d=yaml.safe_load(open('../render.yaml'));w=[s for s in d['services'] if s['name']=='replay-worker'][0];assert w['disk']['mountPath']=='/var/replay';print('ok')"`;
`PY -m pytest tests/replays/test_replay_upload.py -q -k page` (the page states the new promise);
`grep -n "then deleted" app/templates/replays/upload.html` finds nothing.

## Then

`DEPLOY.md` and `FRIENDS.md` in the run folder (not the repo), and the full check.

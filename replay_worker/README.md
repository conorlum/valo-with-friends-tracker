# replay_worker

The friends-only replay upload worker (Stage 3 of `docs/replay-viewer-plan.md`). It parses one
uploaded `.vrf` at a time with the pinned parser, condenses it with the same code as local ingest
(`webapp/app/replays/`), and returns the condensed output. It holds no DB credentials and no secrets;
the friends web service stores and links the result.

**Status (2026-09-25):** built and tested locally; the `Dockerfile` is written but has never been built
(no Docker on the development machine). Nothing deploys it yet: the Render service, `render.yaml` and the
web-service side are Stage 3's PR.

## HTTP

| Request | Answer |
| --- | --- |
| `POST /jobs` (body: the `.vrf` bytes) | `202 {"id", "status": "queued"}`; `400` not a replay (magic), `411` no length, `413` over the size cap, `503` queue full |
| `GET /jobs/{id}` | `{"id", "status", "error"?, "result"?, "parse_seconds"?}`; `status` is `queued`, `parsing`, `done` or `failed` |
| `GET /health` | `{"ok": true, "queued", "limits", "recipe", "control": {...}, "archive": {...}, "idle", "heights": {...}}`; `recipe` is what a parse here stamps (null if it can't be read); `idle` and `heights` are under "Height builds" |

`result` holds `match_uuid` (from the file's header, never its name), `map_name`, `game_branch`,
`source_sha256`, `recipe`, `hz`, `round_count`, `rounds` (`{"n": base64 of the gzipped JSON v1 blob}`),
`players`, `link_inputs` and a small report. A failed job's `error` is a short user-facing reason:
`parse timed out`, `parse failed`, or `not condensable: <reason>`.

## Behaviour

- One job runs at a time; `REPLAY_QUEUE_SIZE` more wait, and the next upload gets `503`.
- Each job has its own temp folder (the upload plus the export, about 65 times the file). With the archive
  off it is deleted when the job ends, whatever happened, **before** the job reads as finished. With it on,
  see "The .vrf archive" below.
- The parser runs with a timeout that kills its whole process tree, and on Linux an address-space cap
  (`REPLAY_MEMORY_CAP_MB`). Windows has no cap; `/health` says so.
- Up to 50 finished jobs stay readable; older ones are forgotten.

## The .vrf archive

Design: `docs/superpowers/specs/2026-10-01-control-heights-design.md`, part 1. Code: `archive.py` (the store)
and `server.py` (the jobs). Web side: `webapp/app/services/replay_upload.py` (`send_ack`),
`webapp/app/services/replay_archive_sync.py`, `webapp/app/routers/replay_admin.py`.

- **On only when** `REPLAY_ARCHIVE_DIR` is set, is a mount point, and a create+rename probe works
  (`REPLAY_ARCHIVE_REQUIRE_MOUNT=0` skips the mount check, for tests). Otherwise everything above holds
  unchanged and `/health` says why the archive is off.
- On Render: the `replay-archive` disk at `/var/replay` (`render.yaml`, 50 GB). The image starts as root,
  `entrypoint.sh` chowns the mount to `worker`, then drops to it with `setpriv`.
- Layout: `jobs/<id>/` (upload, export, `job.json`, `result.json`), `pending/<id>.vrf` (parsed, waiting for
  the web app's ack), `archive/<match uuid>.vrf` + `archive/index.json`, `acks/`, `tombstones.json`.
- The export is deleted as soon as the condenser returns. A restart re-queues queued/parsing jobs and reloads
  finished ones. Pending files nobody acks go after 2 days, uncollected jobs and ack records after 7.
- Space: the archive may use the disk's total less `70 x REPLAY_MAX_BYTES + REPLAY_QUEUE_SIZE x REPLAY_MAX_BYTES`
  (the parse reserve), 5 GB of slack and what `pending/` holds; the earliest-played match is evicted first.
  An upload that would eat into the running parse's reserve gets `503`.
- Routes (unauthenticated like `/jobs`; the service is private): `POST /jobs/{id}/ack`, `POST /reparse`,
  `GET /archive`, `POST /archive/delete`, `POST /archive/tombstones`. `/health` has an `archive` block
  (`enabled`, `kept`, `bytes`, `budget_bytes`, `oldest_played_at`, `boot_id`).
- Tests: `webapp/tests/replays/test_replay_archive.py` (the store), `test_replay_worker_archive.py` (the server),
  `test_replay_archive_web.py` (the web side).

### Re-parse attempts (the automatic queue's protocol)

`docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md`, task 1. The web app's automatic re-parse queue
(`webapp/app/services/replay_reparse_auto.py`) must be able to lose a reply, restart, or fail to record an
answer without ever causing a second parse. So it names each attempt with a UUID it has already committed,
and the worker gives that id exactly one job.

- `POST /reparse {"match_uuid", "attempt_id", "expected_sha256"}`: the first time, `202` and the attempt's
  job (its id is `auto` + the attempt's hex). Any repeat answers `200` with the same job, whether it is
  queued, parsing, done, failed or already swept (`job_status: "expired"`). It never starts another.
- `GET /reparse/attempts/{id}`: the receipt (`accepted`, `preparing`, `closed`), or `404 unknown_attempt`.
- `POST /reparse/attempts/{id}/close`: fences an id that was never accepted, so a late request for it is
  refused (`409 closed`). If the id already has its job, the answer is that job and nothing is closed.
- Every answer of these routes is JSON with a `code`; callers branch on it, not on the HTTP status. The
  definite refusals (`no_archived_file`, `sha_mismatch`, `deleted`, `identity_conflict`, `bad_request`) leave
  no job; `queue_full` (`503`) leaves nothing at all.
- Unreadable or malformed receipts and automatic job records return `503 state_unavailable`: the caller
  retries the same id, and the worker preserves the files and fences. Only a missing record means absent.
- Once the preparation and job record are durable, a failed final acceptance-receipt write still schedules
  that job once. Lookup or restart can promote the preparation later. Cleanup retains the job and its
  acceptance evidence until the permanent receipt can be written, so expiry cannot allow a second parse.
- A receipt is a file of about 300 bytes in `attempts/` on the archive disk, written before the job and kept
  after the job and its result are swept: an old caller may still ask for its id. They count toward the
  archive's space and are not cleaned up.
- `POST /reparse {"match_uuid"}` with no `attempt_id` is the manual re-parse, unchanged.
- `/health` says `archive.reparse_protocol: 1` (only while the archive is on). The web app sends an
  `attempt_id` only to a worker that says so: an older image would read it as a manual re-parse.

## Map control and timing gaps

`POST /control` and `GET /control/{id}` (`server.py`, `control_job.py`; `docs/map-control-worker-plan.md`). The
web app sends one round at a time and stores the result; the worker has no database.

- Up to `REPLAY_CONTROL_WORKERS` children (2) while nothing is parsing or waiting to parse, and one beside
  a parse. A parse arriving kills every running child but the one started first; a killed round goes back to
  the front of the queue and is not counted as a failure.
- A task with a `gaps` block also returns the round's timing gaps (`webapp/app/gaps`, shipped in the image).
  The web app sends the gap fingerprint and the cache key, because the control interpreter has no SQLAlchemy
  to import the web app's own helpers with. With `gaps_only` the task returns the gaps alone, for a round
  whose control the web app already has.
- The child writes a tick cache file under `CONTROL_CACHE_DIR/gaps` while it runs and deletes it when the
  task ends, along with any file older than six hours.
- Health advertises `control.gaps_protocol=1`; an older worker receives plain control until it updates.
- Every result names the engine's revisions and the hash of the game figures it read, so the web app can
  drop a result from another deploy.
- `POST /heights {map, digest, asset}` keeps a map's height asset (base64 `.npz`) in the control cache, the
  newest 3 per map. A task that names a digest the worker doesn't have gets `409 {error, height}`; the web app
  pushes the asset and asks again. The cache is lost on redeploy, which is harmless.

## Height builds

`server.py` (`HeightBuilds`) and `height_job.py`; `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`
and `docs/map-control-worker-plan.md`, "Height rebuilds". The web app sends a map's stored rounds in batches and
the worker builds the map's heights from them, with the kill-line and must-block checks; the web app checks the
result and decides whether it goes live (`webapp/app/services/replay_heights_remote.py`). Off with map control.

- **The child** is one more control child, `python -m replay_worker.height_job` (`REPLAY_HEIGHT_CMD`, the control
  venv's interpreter in the image), niced, memory-capped and timed out after `REPLAY_HEIGHT_TIMEOUT_S` (3600). It
  reads its rounds from the spool one at a time, computes the input manifest itself from this image's masks,
  rules and check set, and refuses a build whose manifest or rounds aren't the ones it would have computed
  (`error_kind: "inputs"`, "these aren't my inputs").
- **Runs alone.** A build is queued ahead of every control round, starts only when nothing else is running on
  the control side and no parse is running or waiting, and nothing starts beside it. A parse arriving always
  kills it (control keeps one child beside a parse; a build keeps none), and it goes back to `queued` with its
  rounds, not counted as a failure.
- **One build per key** (the input manifest's digest): opening a key again answers the build there is; only a
  failed one is replaced by a fresh, empty one. Opening a build for a map drops any other still collecting for it.
- **Uploads are immutable**: a round is kept by its sha256; the same bytes again cost nothing, other bytes for a
  round already received are a conflict. The rounds are spooled to `<temp>/height_builds/<id>/<match>/<n>.json.gz`.
- **Bounded**: the spool of all builds together stays under 512 MB, at most 2 builds collect at once, a collector
  nobody feeds for an hour is dropped, and the newest 8 ended builds keep their answer. A build's folder goes when
  it ends, and whatever a restart left goes when the server starts.

| Request | Answer |
| --- | --- |
| `POST /heights/build` `{key, map, rounds, manifest}` | `202` the build `{"id", "key", "map", "status", "received", "expected"}`; `400` not a build; `503` too many collecting |
| `POST /heights/build/{id}/rounds` `{"rounds": [{"match", "n", "blob" (base64)}]}` | `200 {"received"}`; `409 {"error": "not collecting", "status"}` or `{"error": "other bytes", "match", "n"}`; `413` the spool is full; `400`; `404` |
| `POST /heights/build/{id}/start` `{"previous"?}` | `202` the build; `409 {"error": "rounds are missing", "received", "expected"}`; `404` |
| `POST /heights/build/{id}/cancel` | `200 {"cancelled": true}`; `409 {"error": "not collecting", "status"}`; `404` |
| `GET /heights/build/{id}` | the build, with `"result"` when done and `"error"` when failed |

A build is always in one of five states, and every answer says which, so the web app picks up wherever it was
after a restart on either side:

| State | What the web app may do |
| --- | --- |
| `collecting` | send rounds, start, cancel |
| `queued`, `running` | wait and ask; sending rounds is a conflict that says the state, starting again answers it |
| `done` | read the result |
| `failed` | open it again: the same key starts a fresh, empty build |
| unknown (`404`) | open it again: the worker restarted, or dropped a collector nobody fed |

`/health` gains `"idle"` (no parse running or waiting) and `"heights": {"collecting", "queued", "running" (map
names), "spool_bytes"}`. Tests: `webapp/tests/replays/test_replay_worker_heights.py`.

## Configuration

`REPLAY_PARSER_CMD` (JSON list with `{vrf}` and `{out}`), `REPLAY_PARSER_BUILD` (a `BUILD.json` to check
against the pin; unset in the image, which is built from the pin itself), `REPLAY_WORKER_TMP`,
`REPLAY_TIMEOUT_S` (180; the image sets 240), `REPLAY_MAX_BYTES` (80 MB; the image sets 200,000,000), `REPLAY_QUEUE_SIZE` (5), `REPLAY_MEMORY_CAP_MB`
(0 = none), `REPLAY_WORKER_HOST`/`REPLAY_WORKER_PORT`, `REPLAY_ARCHIVE_DIR` (unset: no archive),
`REPLAY_ARCHIVE_REQUIRE_MOUNT` (1), `REPLAY_HEIGHT_CMD` (JSON list: the height-build child; default this Python,
`-m replay_worker.height_job`; the image sets the control venv's), `REPLAY_HEIGHT_TIMEOUT_S` (3600).

## Running it locally

From the repository root, with the local parser build (`webapp\scripts\build_replay_parser.ps1`):

```powershell
$env:REPLAY_PARSER_CMD = '["' + ($HOME -replace '\\','/') + '/rp/parser/bin/CliReader.exe", "export", "{vrf}", "--output", "{out}"]'
$env:REPLAY_WORKER_HOST = "127.0.0.1"
.\webapp\.venv313\Scripts\python.exe -m replay_worker.server
```

Measured 2026-09-25 on the Swiftplay test replay (23 MB): parse 33-35 s, job 70-77 s end to end, and the
condensed output byte-identical to local ingest's; the temp folder was empty afterwards.

Tests: `webapp/tests/replays/test_replay_worker.py` (a stub parser command: success, the timeout, failures,
queue overflow, the limits).

Measured 2026-09-27 (AFK run) on a competitive replay (Summit, 60 MB `.vrf`, 20 rounds): parse 52.3 s, job
179 s end to end (the condense now also stores abilities and shots), the stored blobs byte-identical to the
local ingest's (same recipe), the temp folder empty afterwards. The export needs about 3.5 GB of temp disk
for that file, so the service's disk must hold roughly 65 times the largest upload. The container's peak
memory is not measured yet (no Docker here); the Windows condense peaked at 969 MB on this file.

The web side is `webapp/app/services/replay_upload.py` and the upload routes in
`webapp/app/routers/replays.py`; `render.yaml` declares this service (`replay-worker`, a private service).

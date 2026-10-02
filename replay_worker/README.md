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
| `GET /health` | `{"ok": true, "queued", "limits"}` |

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

## Configuration

`REPLAY_PARSER_CMD` (JSON list with `{vrf}` and `{out}`), `REPLAY_PARSER_BUILD` (a `BUILD.json` to check
against the pin; unset in the image, which is built from the pin itself), `REPLAY_WORKER_TMP`,
`REPLAY_TIMEOUT_S` (180; the image sets 240), `REPLAY_MAX_BYTES` (80 MB; the image sets 200,000,000), `REPLAY_QUEUE_SIZE` (5), `REPLAY_MEMORY_CAP_MB`
(0 = none), `REPLAY_WORKER_HOST`/`REPLAY_WORKER_PORT`, `REPLAY_ARCHIVE_DIR` (unset: no archive),
`REPLAY_ARCHIVE_REQUIRE_MOUNT` (1).

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

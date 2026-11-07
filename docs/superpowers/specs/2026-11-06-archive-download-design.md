# Download archived replays (owner only): design

Agreed with the owner on 2026-11-06; revised after an independent plan review (14 findings, no blockers).

## Why

Since PR #186 (parser pinned at 04dcbd9, export schema 9) every new replay is refused by the linker with
`link-blocking: RoundResults fell back to a raw payload in the fold` (`webapp/app/replays/condense.py:716-724`,
`:1349`): 38 replays on 2026-11-06. Fixing the reader needs the parser's real schema-9 output, which needs the
`.vrf` files. They exist only on the replay worker's private archive disk (`archive/<uuid>.vrf`,
`replay_worker/archive.py:10`), and nothing could download them. This adds that, owner only.

## Goal

The owner runs one local command and gets verified copies of archived replays:

    fetch_archived_replay.py <uuid> [<uuid> ...] | --refused   [--out ~/rp/vrf] [--site URL]

## Settled

- The download goes through the existing owner-only admin gate (`replay_admin.require_admin`: 404 without the
  token, on a wrong token, or in demo mode). The worker gets an internal route; it is `type: pserv`, reachable
  only from the web service, and holds no secrets.
- `--refused` reads the list from a new admin route, so the script needs only `REPLAY_ADMIN_TOKEN`, never DB
  credentials. The token is never printed or logged.
- Each file is written to `<uuid>.vrf.part`, checked against the archive's sha256, then renamed; a mismatch
  deletes the part and counts as failed; a present file with the right sha is skipped; one failure never stops
  the rest; non-zero exit if any failed. Default output `~/rp/vrf`, outside every repo.
- A tombstoned (deleted) match is never served, even if its file is still on disk.
- Download only: no change to `condense.py`, the linker or the RoundResults reader.
- No new switch and no `render.yaml` change. One merge deploys both services (`autoDeploy: true`). Nothing
  starts by itself after the deploy.

## Architecture

### 1. Worker: `GET /archive/files/<uuid>` (`replay_worker/server.py`, beside `GET /archive`)

- In one `with archive.lock:` block: tombstone check, index lookup (sha256, size), `open()` of
  `archive/<uuid>.vrf`, and `Content-Length` from `os.fstat` of the open file (the `_run_input` pattern).
  Taking these separately would let a delete land between the check and the open.
- Sets `self.connection.settimeout(30)` before streaming, so a stalled reader can't pin a thread.
- Streams in chunks with `Content-Type: application/octet-stream`, `Content-Length` and
  `X-Archive-Sha256: <index sha>`. Between chunks it re-checks `is_tombstoned` and aborts the connection if a
  delete arrived (the client sees a short body and fails the file).
- Every 404 carries a `code`, so it can't be mistaken for the old image's unknown-path 404
  (`{"error":"not found"}`): `{"error": ..., "code": "archive_off" | "bad_uuid" | "tombstoned" |
  "not_in_archive" | "missing_on_disk"}`. A file in the index but missing on disk is 404 `missing_on_disk` and
  logged.
- On Linux, an eviction or replacement mid-stream doesn't truncate the open file: the stream completes with
  the bytes that were opened. The sha header is the index's sha read under the same lock as the open, so a
  mismatch can only mean the bytes differ from what the archive recorded; the client's check catches it.

### 2. Web: `GET /admin/replays/archive/{uuid}.vrf` (`webapp/app/routers/replay_admin.py`)

- Behind `require_admin`. A sync `def` (urllib blocks; an `async def` would block the event loop).
- Validates the UUID (`UUID_SHAPE`); 404 `{"detail": {"code": "deleted", ...}}` when a `ReplayDeletion` exists
  for the match (tombstone pushes to the worker can fail, `replay_admin.py:84-85`), as `reparse` does.
- Opens a streaming GET through a new `WorkerClient` call (`webapp/app/services/replay_upload.py`) with connect
  and read timeouts, and returns `StreamingResponse(chunks, background=BackgroundTask(upstream.close))`,
  passing `Content-Length` and `X-Archive-Sha256` through. Never the whole file in memory. The upstream is
  closed on every error path before a response is returned.
- Worker 404 with a `code` → 404 `{"detail": {"code": <code>, "reason": ...}}`. Worker 404 without a `code`
  (the old image during the deploy gap) → 503 "the replay worker has no download route yet (old image?)".
  Worker unreachable or timed out → 502 with a plain reason. Never a 500 or a hang.

### 3. Web: `GET /admin/replays/refused`, same gate

Every replay with `link_status == 'refused'` and no `ReplayDeletion`, newest first, as
`{match_uuid, map, check, reason, reasons}`: `map` from `map_name`, `check`/`reason` from `link_report`, and
`reasons` from `link_report["reasons"]` (for the PR #186 refusals `reason` is the generic "not eligible"; the
real text is in `reasons`). DB only.

### 4. Script: `webapp/scripts/fetch_archived_replay.py`

- Built on `webapp/scripts/_admin_client.py` (`DEFAULT_SITE`, `call`, `AdminError`; token from
  `REPLAY_ADMIN_TOKEN`), which gains a streaming download.
- Preflight: `GET /admin/replays/refused` (always, also without `--refused`). A 200 proves the token works and
  the routes are deployed; a bare 404 there is reported as "wrong token, or the site is an older build" and
  exits non-zero before any download.
- Skip check: the sha from the site's `GET /admin/replays/archive` index (`files[].sha256`); if the local file
  matches, skip with no download. No HEAD requests (the worker and FastAPI don't answer HEAD).
- Download to `<uuid>.vrf.part`, hashing while writing; count bytes against `Content-Length` (urllib returns
  `b""` on an early end rather than raising); a missing sha header, a short body or a mismatch deletes the
  part and counts `failed`. A 404 with `code` `not_in_archive`, `deleted` or `tombstoned` is reported as "not in archive" with its reason; any other code (`missing_on_disk`, `archive_off`, `bad_uuid`) is a fault and counts `failed` (branch review B1). A non-JSON or malformed refused list or index exits 2 like an unreadable one.
- Each match is wrapped in `except (AdminError, OSError, http.client.HTTPException)`: one failure never stops
  the rest. Summary: downloaded, skipped, not in archive, failed, each with UUIDs; exit 1 if any failed.

## Tests

- Worker: serves bytes, length and sha; 404 with each `code` (archive off, malformed UUID, tombstoned,
  not in index, in index but missing on disk); a delete between chunks aborts the stream.
- Web (real worker in-process with a temp archive, sqlite store): every 404 test (no token, wrong token, demo)
  is paired with a right-token 200 in the same setup, so it fails before the route exists; streams bytes and
  sha through; a coded worker 404 passes through; a stub worker answering the old image's exact
  `{"error":"not found"}` 404 gives 503; a worker that is down gives 502; a worker that accepts and never
  answers gives 502 within a small timeout; a `ReplayDeletion` gives 404 `deleted`; the refused list returns
  only refused, undeleted rows with `reasons`. A row in `tests/test_demo_http_contract.py` for each new route,
  with the worker client made to raise, proving the gate fires first.
- Script: against a stub site: downloads and verifies; a sha mismatch deletes the part and exits non-zero; a
  short body fails; a missing sha header fails; an existing good file is skipped with no download; one missing
  UUID doesn't stop the next; `--refused` fetches the listed ones; a bare 404 on preflight stops with the
  token/old-site message; the token never appears in output.
- Catalog: the script and new test files go into a suite in `tests/suites.toml`.

## Docs

`replay_admin.py`'s docstring route list, the worker routes line in `replay_worker/README.md` (prose, ~:95),
and a short "Downloading archived replays" note in `webapp/RENDER_DEPLOY.md` beside the other archive tools.

## Risks

- Replay files are friends' private recordings: the only door is the existing token gate; the demo never
  serves one.
- Deploy gap: covered by the coded-404 rule (503 from the web route until the worker's image is live).

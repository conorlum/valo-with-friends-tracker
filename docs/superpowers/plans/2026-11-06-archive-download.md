# Download archived replays: implementation plan

Spec: `docs/superpowers/specs/2026-11-06-archive-download-design.md`. Four items, in order (W4 → W5a → W5b →
W6), one commit each. Source paths are from the repo root; test paths and commands are run from `webapp/`.
Revised after the implementation-plan review (12 findings, no blockers).

Common to every item:
- Python: the test venv (`webapp/.venv313-test`). Tests run from `webapp/`, serially (`-n 0`), timeout 600 s.
- Test bytes are synthetic only (`vrf_bytes()` from `tests/replays/test_replay_worker.py:103`). No real replay
  file, name or token in the repo; tests use a test token (`"test-admin"` / `"s3cret"`).
- No item reads real stored rows: the refused-list test builds `Replay` rows in the model's shape
  (`webapp/app/models/replay.py:39-64`; `link_report` as `link.py:380` writes it:
  `{"check": "eligibility", "reason": "the replay is not eligible to link", "reasons": ["link-blocking: RoundResults fell back to a raw payload in the fold"]}`).
  So no real-data probe.
- Tests are written first and run red before the code (the item's report shows the red run).
- Pin part P1, run when an item touches its paths (`replay_worker/**`, `app/routers/**`,
  `app/services/replay*.py`, `tests/suites.toml`, the demo contract):
  `python -m pytest -m "slow or not slow" -n 0 tests/replays/test_replay_isolation.py tests/test_demo_http_contract.py tests/test_test_catalog.py tests/test_test_lanes.py`
  (~3 min).

## W4: worker `GET /archive/files/<uuid>`

Files:
- `replay_worker/server.py:2363` `do_GET`: a new branch before the final 404 (`:2414`), beside `/archive`
  (`:2394`), calling a new handler method (pattern: `_run_input`, `server.py:2220`, which opens a file and
  sends it with `Content-Length`).
- `replay_worker/archive.py:241-252` (`has`, `path_of`, `is_tombstoned`), `:235` `entries`: add one method that,
  under `self.lock`, checks the tombstone, finds the index entry (sha256, size) and opens the file, returning
  `(handle, sha256, size_from_fstat)` or raising/returning a coded refusal (`tombstoned`, `not_in_archive`,
  `missing_on_disk`; the last one logged through `self.log`).
- The route: archive off → 404 `{"error": "the archive is off", "code": "archive_off"}`; a path tail that isn't
  a lower-case UUID (accept upper case by lowering, as `replay_admin._match_uuid` does) → 404 `bad_uuid`; the
  archive method's refusals → 404 with their code. Success: 200, `Content-Type: application/octet-stream`,
  `Content-Length`, `X-Archive-Sha256`; `self.connection.settimeout(30)` before the body; 1 MiB chunks; between
  chunks `archive.is_tombstoned(uuid)` → stop and close the connection (`self.close_connection = True`, return).
  The handle is closed in a `finally`.
- `replay_worker/README.md:95-96`: add `GET /archive/files/{uuid}` to the routes line.
- Tests in `webapp/tests/replays/test_replay_worker_archive.py`, using `start_archive` (`:36`) and an archived
  upload as `test_a_parsed_upload_waits_for_its_ack_and_is_then_archived` (`:76`) makes one:
  1. serves the archived bytes; `Content-Length` = len; `X-Archive-Sha256` = `hashlib.sha256(vrf_bytes())`;
  2. 404 with `code` for each: archive off (`start(tmp_path, stub)` with no archive dir, as `:48`), malformed
     uuid, not in the index, in the index but the file deleted from disk, tombstoned
     (`POST /archive/tombstones` with the uuid, then GET; and a tombstone set while the file is still on disk:
     write the file back after the tombstone purge and assert `code == "tombstoned"`, proving R4); the
     `missing_on_disk` case also asserts its log line (a recording `log=` on the archive);
  3. an unknown route still answers 404 `{"error": "not found"}` with no `code` (the deploy-gap signature the
     web side relies on);
  4. a delete mid-stream aborts: monkeypatch the chunk size small (a module constant, e.g.
     `ARCHIVE_CHUNK_BYTES`) and the archive's `is_tombstoned` to return False for a set number of calls (the
     open-time check may go through it too) and True after the first chunk; assert the client received
     `len(body) < int(Content-Length)`, accepting either a short read or `IncompleteRead`.
- Out of scope: the web side, any change to ack, eviction or tombstone logic.

Done-check (yes/no: all pass, the new tests were red first):
- `python -m pytest -n 0 -m "not slow and not postgres and not browser and not corpus" tests/replays/test_replay_worker_archive.py tests/replays/test_replay_archive.py tests/replays/test_replay_worker.py`
- pin part P1.

## W5a: web route `GET /admin/replays/archive/{uuid}.vrf` and `WorkerClient.archive_file`

Depends: W4 (its tests run the real worker in-process).

Files:
- `webapp/app/services/replay_upload.py:229` `WorkerClient`: add `archive_file(match_uuid)` returning an open
  response (status 200) for the caller to stream and close, with connect/read timeout `self.timeout`. A worker
  404 whose JSON has a `code` → `WorkerRefused(reason, 404, body)` (body keeps `code`); a 404 with no `code` →
  a new `WorkerError` subclass or flag meaning "no such route" (old image); other HTTP errors, `URLError`,
  `OSError`, timeouts → `WorkerError`. Pattern: `_call` (`:302-314`), `_coded` (`:283-300`).
- `webapp/app/routers/replay_admin.py`: after `archive_index` (`:90-95`):
  - `GET /archive/{match_uuid}.vrf`, `dependencies=[Depends(require_admin)]`, a sync `def`. Lower-case and check
    `UUID_SHAPE` (`:37`) → 404 `{"code": "bad_uuid"}`; `db.get(ReplayDeletion, uuid)` → 404 `deleted` (as
    `reparse`, `:101`); no worker configured (`_worker`, `:57`) → its existing answer; worker refusal with code
    → 404 `{"detail": {"code": ..., "reason": ...}}`; old image → 503 detail "the replay worker has no download
    route yet (an older image?)"; `WorkerError` → 502 "the replay worker is unreachable". Success: a generator
    that reads 1 MiB chunks, counts bytes against the worker's `Content-Length` and raises on a short body
    (`read(amt)` returns `b""` at an early end), inside `try/finally: upstream.close()`; returned as
    `StreamingResponse(gen, media_type="application/octet-stream", headers={Content-Length, X-Archive-Sha256,
    Content-Disposition: attachment; filename=<uuid>.vrf}, background=BackgroundTask(upstream.close))` (the
    finally covers a read error, which skips the background task in starlette 0.38.6; the background task covers
    a client disconnect; a double close is harmless). Header values come from the worker's response.
  - The module docstring's route list gains it.
- Tests in `webapp/tests/replays/test_replay_archive_web.py`, through the real app with `TestClient` (the
  `site` fixture, `tests/support/site.py:18`: add `from tests.support.site import site`, as
  `tests/test_demo_http_contract.py:12` does) and a real worker from `start_archive`
  (`test_replay_worker_archive.py:36`, already imported at `test_replay_archive_web.py:25`), with an archived
  upload via `collect` (`:57`):
  1. right token → 200, body == `vrf_bytes()`, `X-Archive-Sha256` matches, `Content-Length` matches;
     in the same test, no token → 404, wrong token → 404, `replay_admin_token=None` → 404 (each paired with
     the 200 so it fails before the route exists);
  2. a coded worker 404 (uuid not archived) → 404 with `detail.code == "not_in_archive"`; bad uuid → 404
     `bad_uuid`; a `ReplayDeletion` row → 404 `deleted` without contacting the worker;
  3. deploy gap: a stub `http.server` answering every GET with the old worker's exact 404 body
     `{"error": "not found"}` (`replay_worker/server.py:2414`) → 503 with the reason;
  4. worker down (an unused localhost port) → 502; a worker that accepts and never answers (a listening socket
     that never replies) → 502 within ~5 s, with `uploads.client` monkeypatched to
     `lambda: uploads.WorkerClient(url, timeout_s=0.5)` (the default timeout is bound at definition,
     `replay_upload.py:230`, so patching the constant does nothing; the router calls `uploads.client()`,
     `replay_admin.py:57-60`);
  5. the upstream is closed after a full response, and after a short body (a stub worker sending a
     `Content-Length` larger than its bytes; the client sees a failed/short transfer): wrap `archive_file` to
     record `close()`.
- `webapp/tests/test_demo_http_contract.py:15-25`: row `('GET','/admin/replays/archive/<a uuid>.vrf',None,404)`.
  The test points the worker at `must-not-contact.invalid` (`:31`), so a worker call would be a 502/503, not a
  404: the row proves the gate fires first (its pair is test 1's 200 outside demo).

Done-check:
- `python -m pytest -n 0 -m "not slow and not postgres and not browser and not corpus" tests/replays/test_replay_archive.py tests/replays/test_replay_archive_web.py tests/replays/test_replay_match_states.py tests/replays/test_replay_progress.py tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_routes.py tests/replays/test_replay_store.py tests/replays/test_replay_upload.py tests/replays/test_replay_upload_held.py tests/replays/test_replay_view.py tests/replays/test_replay_viewer.py tests/test_archive_replays.py`
  (replay-web default lane, ~2.2 min);
- pin part P1.

## W5b: web route `GET /admin/replays/refused`

Depends: W5a (same files; avoids a conflicting edit).

Files:
- `webapp/app/routers/replay_admin.py`: `GET /refused`, behind `require_admin`: `Replay` rows with
  `link_status == "refused"` and no `ReplayDeletion` (`NOT EXISTS` on `match_uuid`), newest `created_at` first,
  as `{"replays": [{match_uuid, map, check, reason, reasons, created_at}]}` (`map` from `map_name`;
  `check`/`reason`/`reasons` from `link_report`, missing → null / []). Docstring route list gains it.
- Tests in `webapp/tests/replays/test_replay_archive_web.py`: rows in the shape above (build them with
  `tests.support.replays.stored_replay` and then set `link_status`/`link_report`/`created_at`, or set every
  non-null column): two undeleted refused rows with explicit, different `created_at` values (assert newest
  first: sqlite's `now()` has 1 s resolution, so don't rely on defaults), one linked, one refused with a
  `ReplayDeletion` → only the two, in order, with `reasons`; no token → 404, paired with the 200.
- The same refused-list test on `pg_site` (`tests/support/site.py:57`, marked postgres automatically), so the
  `NOT EXISTS` on the `Uuid(as_uuid=False)` columns runs on PostgreSQL.
- `webapp/tests/test_demo_http_contract.py:15-25`: row `('GET','/admin/replays/refused',None,404)`.

Done-check:
- `python -m pytest -n 0 -m "not slow and not postgres and not browser and not corpus" tests/replays/test_replay_archive_web.py`
- pin part P1;
- PostgreSQL lane (controller; local server on 55440):
  `VALO_TEST_DATABASE_URL=postgresql://postgres@localhost:55440/valo_archive_download_test python -m pytest -n 0 -m "postgres and not corpus and not browser and not slow" --require-postgres tests/replays/test_replay_archive_web.py tests/replays/test_replay_routes.py tests/replays/test_replay_upload.py`
  (the new pg test must be among those collected).

## W6: `webapp/scripts/fetch_archived_replay.py`, the client's download, catalog and docs

Depends: W5a, W5b (the routes' JSON shapes).

Files:
- `webapp/scripts/_admin_client.py`: add `download(site, path, timeout=60)` returning the open response (token
  header as `call`, `:29-32`), and make `call`/`download` read a 404's `detail` as a dict with `code` when present.
  Fix the dead `not detail` branch (`:41`): a 404 whose detail is `"Not Found"` (starlette's default) or empty
  is the gate / an older site.
- `webapp/scripts/fetch_archived_replay.py` (pattern: `webapp/scripts/reparse_archive.py` for argparse,
  `main(argv)`, `sys.path` and the client import): `<uuid> ... | --refused`, `--out` (default `~/rp/vrf`,
  created), `--site` (default `DEFAULT_SITE`). Steps per the spec §4: preflight `GET /admin/replays/refused`
  (a bare 404 → message "wrong token, or the site is an older build", exit 2); index `GET /admin/replays/archive`
  for skip shas (if the index call fails, e.g. 502 with the archive off: exit 2 "could not read the archive
  index: <reason>", before any download); per uuid: skip if the local file's sha256 equals the index sha; else
  stream to `<uuid>.vrf.part`, hash while writing, and when `Content-Length` is present count bytes against it
  (a proxy may drop it; the sha check also catches a short body); a missing `X-Archive-Sha256` header, a short
  body or a sha mismatch → delete the part, `failed`; match → `os.replace` to `<uuid>.vrf`. A coded 404 → `not in archive`
  (or `deleted`) with its reason. Per-uuid `except (AdminError, OSError, http.client.HTTPException)`. Summary
  lines (downloaded / skipped / not in archive / failed, each with uuids); exit 1 if any failed. The token
  never appears in any output.
- New test file `webapp/tests/test_fetch_archived_replay.py` with a stub site (`http.server` in a thread on
  127.0.0.1:0) serving `/admin/replays/refused`, `/admin/replays/archive` and `/admin/replays/archive/<uuid>.vrf`
  with configurable bodies/headers: downloads and verifies; sha mismatch → part deleted, exit 1; short body
  (Content-Length larger than the bytes sent) → failed; missing sha header → failed; an existing good file is
  skipped with no download request (the stub counts requests); a coded 404 uuid doesn't stop the next;
  `--refused` fetches the listed ones; a bare 404 `{"detail": "Not Found"}` on the preflight → exit 2 with the
  token/old-site message; a 502 on the index → exit 2 with the index message and no download request; the
  test token string never appears in captured stdout/stderr.
  `REPLAY_ADMIN_TOKEN` set with `monkeypatch.setenv`.
- `webapp/tests/suites.toml:230-245` (`[replay-web]`): add the new test file to `tests` and
  `scripts/fetch_archived_replay.py`, `scripts/_admin_client.py` to `guards`.
- `webapp/RENDER_DEPLOY.md` (~:220, beside `reparse_archive.py --status`): a short "Downloading archived replays"
  paragraph: the command, `--refused`, where files go, that the token comes from the environment.

Done-check:
- `python -m pytest -n 0 -m "not slow and not postgres and not browser and not corpus" tests/test_fetch_archived_replay.py tests/replays/test_replay_archive_web.py tests/replays/test_replay_reparse_auto.py`
  (`test_replay_reparse_auto.py` imports `reparse_archive`, which imports `_admin_client`)
- `python scripts/test_catalog.py check` and pin part P1.

## After W6

Full-suite checkpoint request (4 implementation items), then wrap-up.

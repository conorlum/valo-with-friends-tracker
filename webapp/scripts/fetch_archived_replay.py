"""Download archived .vrf recordings through the site's admin routes and verify their sha256.

Git Bash, from webapp/ (the token is the value from the Render dashboard):

    REPLAY_ADMIN_TOKEN=<token> .venv/Scripts/python.exe scripts/fetch_archived_replay.py <uuid>... [--out ~/rp/vrf] [--site URL]
    REPLAY_ADMIN_TOKEN=<token> .venv/Scripts/python.exe scripts/fetch_archived_replay.py --refused [--out ~/rp/vrf] [--site URL]

PowerShell: `$env:REPLAY_ADMIN_TOKEN='<token>'` first, then `.\\.venv\\Scripts\\python.exe scripts\\fetch_archived_replay.py ...`.

Exit codes: 0 when every file was downloaded, skipped or is absent from the archive (not_in_archive, deleted,
tombstoned); 1 when any download failed, including a fault on the worker (missing_on_disk, archive_off) or a
malformed UUID; 2 when the refused list or the archive index can't be read.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _admin_client import DEFAULT_SITE, AdminError, ArchiveNotFound, call, download, redact  # noqa: E402

CHUNK_SIZE = 1024 * 1024
UUID_SHAPE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Coded 404s that mean the file is legitimately absent; every other code is a fault and counts as failed.
ABSENT_CODES = frozenset({"not_in_archive", "deleted", "tombstoned"})
PREFLIGHT_ERRORS = (AdminError, OSError, http.client.HTTPException)


def _rows(payload: object, key: str) -> list[dict]:
    """The list under `key`, each row a dict with a string match_uuid; AdminError for any other shape."""
    rows = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(
            isinstance(row, dict) and isinstance(row.get("match_uuid"), str) for row in rows):
        raise AdminError(f"unexpected answer shape: expected a '{key}' list of rows with a match_uuid")
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_one(site: str, uuid: str, out: Path, index_sha: str | None) -> str:
    # Reject unsafe filenames even if a caller supplies a path instead of a UUID.
    if not UUID_SHAPE.fullmatch(uuid):
        raise ArchiveNotFound("bad_uuid", "match_uuid must be a match UUID")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{uuid}.vrf"
    if target.exists() and index_sha and file_sha256(target) == index_sha:
        return "skipped"
    part = out / f"{uuid}.vrf.part"
    try:
        with download(site, f"/admin/replays/archive/{uuid}.vrf") as response:
            expected_sha = response.headers.get("X-Archive-Sha256")
            if not expected_sha:
                raise AdminError("missing X-Archive-Sha256 header")
            length = response.headers.get("Content-Length")
            try:
                expected_size = int(length) if length is not None else None
            except ValueError as error:
                raise AdminError("invalid Content-Length header") from error
            digest = hashlib.sha256()
            size = 0
            with part.open("wb") as destination:
                for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                    destination.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if expected_size is not None and size != expected_size:
                raise AdminError(f"Content-Length mismatch: received {size} bytes, expected {expected_size}")
            expected_sha = expected_sha.strip().lower()
            if index_sha and expected_sha != index_sha.strip().lower():
                raise AdminError("the download's sha256 header differs from the archive index")
            if digest.hexdigest() != expected_sha:
                raise AdminError("sha256 mismatch")
        os.replace(part, target)
        return "downloaded"
    finally:
        part.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("uuids", nargs="*", help="match UUIDs to download")
    parser.add_argument("--refused", action="store_true", help="download the site's refused replays")
    parser.add_argument("--out", default="~/rp/vrf", help="destination directory (default ~/rp/vrf)")
    parser.add_argument("--site", default=DEFAULT_SITE, help=f"the site (default {DEFAULT_SITE})")
    args = parser.parse_args(argv)
    if bool(args.uuids) == args.refused:
        parser.error("provide either one or more UUIDs or --refused")

    try:
        refused = _rows(call(args.site, "GET", "/admin/replays/refused"), "replays")
    except PREFLIGHT_ERRORS as error:
        print(redact(f"could not read refused replays: {error}"), file=sys.stderr)
        return 2
    try:
        files = _rows(call(args.site, "GET", "/admin/replays/archive"), "files")
        index = {entry["match_uuid"].lower(): entry.get("sha256") if isinstance(entry.get("sha256"), str)
                 else None for entry in files}
    except PREFLIGHT_ERRORS as error:
        print(redact(f"could not read the archive index: {error}"), file=sys.stderr)
        return 2

    uuids = args.uuids
    if args.refused:
        uuids = [entry["match_uuid"] for entry in refused]
        for entry in refused:
            reasons = entry.get("reasons") or [entry.get("reason") or "no reason reported"]
            print(redact(f"{entry['match_uuid'].lower()}: {entry.get('map')}  " + "; ".join(map(str, reasons))))
    outcomes = {name: [] for name in ("downloaded", "skipped", "not in archive", "failed")}
    out = Path(args.out).expanduser()
    for value in uuids:
        uuid = value.lower()
        try:
            outcome = fetch_one(args.site, uuid, out, index.get(uuid))
            print(redact(f"{uuid}: {outcome}"), flush=True)
        except ArchiveNotFound as error:
            outcome = "not in archive" if error.code in ABSENT_CODES else "failed"
            print(redact(f"{uuid}: {outcome}: {error.code}: {error.reason}"), flush=True)
        except (AdminError, OSError, http.client.HTTPException) as error:
            outcome = "failed"
            print(redact(f"{uuid}: {outcome}: {error}"), flush=True)
        outcomes[outcome].append(uuid)
    for outcome, matches in outcomes.items():
        print(redact(f"{outcome} ({len(matches)}): " + (", ".join(matches) or "none")))
    return 1 if outcomes["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

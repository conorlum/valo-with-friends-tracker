"""Copies new Valorant replays into the long-term archive before Valorant deletes them.

    .\\.venv313\\Scripts\\python.exe scripts\\archive_replays.py
    .\\.venv313\\Scripts\\python.exe scripts\\archive_replays.py --source D:\\Demos --archive E:\\ReplayArchive

Valorant keeps only recent replays in `%LOCALAPPDATA%\\VALORANT\\Saved\\Demos`, so every
`<uuid>.vrf` there is copied to the archive (`--archive`, default `$VALO_REPLAY_ARCHIVE`,
else `%USERPROFILE%\\ValorantReplayArchive`):

- each file is copied to a temporary name in the archive, hashed while it is read, the
  copy is re-hashed from disk, and only a matching copy is renamed into place (the
  rename is atomic on one volume);
- a file already archived with the same bytes is skipped;
- a file with the same name but different bytes is kept beside it as
  `<uuid>.<sha8>.vrf` and reported: an archived file is never overwritten;
- every new file is appended to the archive's `index.json`
  (`{"files": [{"file", "match_uuid", "sha256", "size_bytes", "archived_at"}]}`).

The source is only read. Nothing is ever deleted. The archive is private: never commit it
(this repository is public), and back it up to private storage only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPLAY_NAME = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.vrf$", re.IGNORECASE)
CHUNK = 1 << 20


def default_source() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "VALORANT" / "Saved" / "Demos"


def default_archive() -> Path:
    configured = os.environ.get("VALO_REPLAY_ARCHIVE")
    return Path(configured) if configured else Path.home() / "ValorantReplayArchive"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Outcome:
    source: Path
    status: str  # "archived" | "already_archived" | "kept_beside" | "failed"
    archived_as: str | None = None
    sha256: str | None = None
    detail: str = ""


def _copy_hashing(source: Path, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(CHUNK):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest(), size


def load_index(archive: Path) -> dict:
    path = archive / "index.json"
    if not path.exists():
        return {"files": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_index(archive: Path, index: dict) -> None:
    path = archive / "index.json"
    temporary = path.with_name("index.json.tmp")
    temporary.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def archive_one(source: Path, archive: Path, index: dict, now: datetime) -> Outcome:
    match = REPLAY_NAME.match(source.name)
    if not match:
        return Outcome(source, "failed", detail="not a <uuid>.vrf file name")
    match_uuid = match.group(1).lower()
    target = archive / f"{match_uuid}.vrf"
    temporary = archive / f".{match_uuid}.{os.getpid()}.partial"
    try:
        copied_sha, size = _copy_hashing(source, temporary)
        if sha256_of(temporary) != copied_sha:
            temporary.unlink()
            return Outcome(source, "failed", detail="the copy's hash differs from the bytes read; nothing kept")

        known = {entry["sha256"] for entry in index["files"]}
        if copied_sha in known or (target.exists() and sha256_of(target) == copied_sha):
            temporary.unlink()
            return Outcome(source, "already_archived", target.name, copied_sha)

        status = "archived"
        if target.exists():
            target = archive / f"{match_uuid}.{copied_sha[:8]}.vrf"
            status = "kept_beside"
            if target.exists():
                temporary.unlink()
                return Outcome(source, "failed", detail=f"{target.name} exists with other bytes; nothing kept")
        # os.rename refuses an existing target on Windows, so a race can't overwrite.
        os.rename(temporary, target)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise

    index["files"].append({
        "file": target.name,
        "match_uuid": match_uuid,
        "sha256": copied_sha,
        "size_bytes": size,
        "archived_at": now.isoformat(timespec="seconds"),
    })
    detail = "a different file with this name is already archived" if status == "kept_beside" else ""
    return Outcome(source, status, target.name, copied_sha, detail)


def archive_all(source_dir: Path, archive: Path, now: datetime | None = None) -> list[Outcome]:
    now = now or datetime.now(timezone.utc)
    archive.mkdir(parents=True, exist_ok=True)
    index = load_index(archive)
    outcomes = []
    for source in sorted(source_dir.glob("*.vrf")):
        outcome = archive_one(source, archive, index, now)
        outcomes.append(outcome)
        if outcome.status in ("archived", "kept_beside"):
            write_index(archive, index)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=default_source(), help="Valorant's Demos folder")
    parser.add_argument("--archive", type=Path, default=default_archive(), help="the archive folder")
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        print(f"no replay folder at {args.source}", file=sys.stderr)
        return 2
    outcomes = archive_all(args.source, args.archive)
    for outcome in outcomes:
        line = f"{outcome.status:17} {outcome.source.name}"
        if outcome.archived_as and outcome.archived_as != outcome.source.name:
            line += f" -> {outcome.archived_as}"
        if outcome.detail:
            line += f" ({outcome.detail})"
        print(line)
    new = sum(o.status in ("archived", "kept_beside") for o in outcomes)
    print(f"{new} new, {len(outcomes) - new} not copied. Back up {args.archive} to private storage.")
    return 1 if any(o.status == "failed" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())

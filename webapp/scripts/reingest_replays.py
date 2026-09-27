"""Lists stored replays that are stale or invalid, and what refreshing each needs. Stage 2: `--dry-run` only.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\reingest_replays.py --dry-run

Stale means the stored recipe differs from the current one (docs/replay-viewer-plan.md,
"Freshness: the recipe": inequality, never ordering). Invalid means an incomplete round set or an
unsupported format `v`; the site shows those as "no replay". For each:

- a **local** replay is re-ingested from the archive: the `.vrf` must be there with the stored
  sha256 (a missing or changed file is refused and reported);
- an **upload** can't be re-parsed (its `.vrf` isn't kept): it's listed as "re-upload to refresh".

Reads only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def default_archive() -> Path:
    configured = os.environ.get("VALO_REPLAY_ARCHIVE")
    return Path(configured) if configured else Path.home() / "ValorantReplayArchive"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def plan(session, current_recipe: str, archive: Path) -> list[dict]:
    from app.models.replay import Replay
    from app.replays import db as replay_db

    out = []
    for replay in session.query(Replay).order_by(Replay.id):
        valid = replay_db.is_valid(session, replay)
        stale = replay.recipe != current_recipe
        if valid and not stale:
            continue
        entry = {"match_uuid": str(replay.match_uuid), "source": replay.source, "recipe": replay.recipe,
                 "stale": stale, "valid": valid}
        if replay.source == "upload":
            entry["action"] = "re-upload to refresh"
        else:
            vrf = archive / f"{str(replay.match_uuid).lower()}.vrf"
            if not vrf.is_file():
                entry["action"] = "refused: the archive has no .vrf for it"
            elif _sha256(vrf) != replay.source_sha256:
                entry["action"] = "refused: the archive's .vrf differs from the stored sha256"
            else:
                entry["action"] = "re-ingest from the archive"
        out.append(entry)
    return out


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", required=True, help="list only (Stage 2 has no write mode)")
    parser.add_argument("--archive", type=Path, default=None, help="default $VALO_REPLAY_ARCHIVE or ~/ValorantReplayArchive")
    args = parser.parse_args(argv)

    from app.replays import format as fmt
    from app.replays.contract import load_pin

    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    try:
        entries = plan(session, fmt.recipe(load_pin().commit, fmt.assets_revision()), args.archive or default_archive())
    finally:
        session.rollback()
        session.close()
    print(json.dumps(entries, indent=2))
    print(f"{len(entries)} replay(s) to refresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Freezes a map's stored rounds into a local folder, so two height builds can be shown to have read the same
thing (docs/superpowers/specs/2026-10-05-height-slopes-design.md, "How it is judged").

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\freeze_height_rounds.py --map Sunset --out %TEMP%\\valo-replay\\heights-frozen

It only reads the database (always through `with_friends_db.py --read-only`) and writes under `--out`, which
must be outside the repository: every round of the map at condenser revision 11 or later as its stored bytes,
`<out>/<match>/<n>.json.gz` (the layout `build_control_heights.py --blobs-dir` reads), and
`<out>/<Map>.frozen.json`: when it was frozen and each round's match, number and sha256, with their digest
(`rounds_sha`). A build from that folder records the same digest; `compare_height_builds.py` refuses two builds
whose digests differ. Several maps can share one folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MIN_REVISION = 11


def freeze(session, map_name: str, out: Path, min_revision: int = MIN_REVISION) -> dict:
    """Writes the map's rounds under `out` and returns the manifest it wrote beside them."""
    import build_control_heights as command
    from app.models.replay import Replay, ReplayRound
    from app.services.replay_control import condense_revision

    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for replay in session.query(Replay).filter(Replay.map_name == map_name).order_by(Replay.id):
        if (condense_revision(replay.recipe) or 0) < min_revision:
            continue
        match = str(replay.match_uuid).lower()
        for row in session.query(ReplayRound).filter(ReplayRound.replay_id == replay.id) \
                .order_by(ReplayRound.round_number):
            data = bytes(row.data)
            (out / match).mkdir(parents=True, exist_ok=True)
            (out / match / f"{row.round_number}.json.gz").write_bytes(data)
            rows.append([match, int(row.round_number), hashlib.sha256(data).hexdigest()])
    manifest = {"map": map_name, "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **command.round_identity(rows), "round_list": sorted(rows)}
    (out / f"{map_name}.frozen.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--out", type=Path, required=True, help="the folder to write to, outside the repository")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.is_relative_to(WEBAPP_ROOT.parent.resolve()) or any((f / ".git").exists() for f in (out, *out.parents)):
        print(f"REFUSED: --out {args.out} is inside a repository; stored rounds are never written into one",
              file=sys.stderr)
        return 2
    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    try:
        manifest = freeze(session, args.map, out)
    finally:
        session.rollback()
        session.close()
    print(f"{args.map}: {manifest['rounds']} rounds of {manifest['matches']} matches frozen in {out} "
          f"(rounds {manifest['rounds_sha']})", flush=True)
    return 0 if manifest["rounds"] else 2


if __name__ == "__main__":
    sys.exit(main())

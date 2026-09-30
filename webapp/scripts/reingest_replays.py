"""Lists stored replays that are stale or invalid (`--dry-run`), or re-ingests the local ones (Stage 4).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\reingest_replays.py --dry-run
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\reingest_replays.py

Stale means the stored recipe differs from the current one (docs/replay-viewer-plan.md,
"Freshness: the recipe": inequality, never ordering). Invalid means an incomplete round set or an
unsupported format `v`; the site shows those as "no replay". For each:

- a **local** replay is re-ingested from the archive: the `.vrf` must be there with the stored
  sha256 (a missing or changed file is refused and reported);
- an **upload** can't be re-parsed (its `.vrf` isn't kept): it's listed as "re-upload to refresh".

`--dry-run` reads only. Without it, each local replay marked "re-ingest from the archive" is
condensed again from its export (`--export-root`, default `%TEMP%\\valo-replay\\<uuid>`; the
parser is never run from here: export it first with `scripts\\export_replay.ps1 <uuid>`), checked
against the archive's `.vrf`, and stored with `--replace` semantics through `store.py` (one locked
transaction, relinked), then its per-kill split is recomputed. Uploads and refused ones are only
listed. It writes: run it through `with_friends_db.py` without `--read-only` (the user's step).
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
    parser.add_argument("--dry-run", action="store_true", help="list only; write nothing")
    parser.add_argument("--archive", type=Path, default=None, help="default $VALO_REPLAY_ARCHIVE or ~/ValorantReplayArchive")
    parser.add_argument("--export-root", type=Path, default=None, help="default %%TEMP%%\valo-replay")
    parser.add_argument("--parser-dir", type=Path, default=None, help="the parser build (default $REPLAY_PARSER_DIR)")
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
    print(f"{len(entries)} replay(s) to refresh", flush=True)
    status = 0 if args.dry_run else reingest(entries, args, session_factory)
    print_control_nudge(session_factory)
    return status


def print_control_nudge(session_factory) -> None:
    """Map control is its own command (scripts/compute_control.py); say when rounds need it."""
    from app.services.replay_control import nudge

    session = session_factory()
    try:
        line = nudge(session)
    except Exception as error:  # noqa: BLE001 - a nudge never fails the run (e.g. before migration 0014)
        line = f"Map control: couldn't check ({type(error).__name__})"
    finally:
        session.rollback()
        session.close()
    if line:
        print(line, flush=True)


def default_export_root() -> Path:
    import tempfile

    return Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay"


def reingest(entries: list[dict], args, session_factory) -> int:
    """Re-ingests every entry marked for it; returns 1 if any failed or needs the user first."""
    from app.replays.condense import condense_export_dir
    from app.replays.contract import ContractError, load_manifest
    from app.replays.store import store_replay
    from app.services.replay_impact import refresh_replay_impact

    archive = args.archive or default_archive()
    root = args.export_root or default_export_root()
    parser_dir = args.parser_dir or Path(os.environ.get("REPLAY_PARSER_DIR") or Path.home() / "rp" / "parser")
    build_path = parser_dir / "bin" / "BUILD.json"
    build = json.loads(build_path.read_text(encoding="utf-8-sig")) if build_path.is_file() else None
    problems = 0
    for entry in entries:
        if entry["action"] != "re-ingest from the archive":
            continue
        uuid = entry["match_uuid"].lower()
        export_dir, vrf = root / uuid, archive / f"{uuid}.vrf"
        if build is None:
            print(f"{uuid}: no parser build at {build_path}", flush=True)
            problems += 1
            continue
        try:
            load_manifest(export_dir)
        except (ContractError, OSError):
            print(f"{uuid}: no export at {export_dir}: run scripts\\export_replay.ps1 {uuid} first", flush=True)
            problems += 1
            continue
        try:
            condensed = condense_export_dir(export_dir, source_sha256=_sha256(vrf), build=build, vrf_path=vrf)
        except ContractError as refused:
            print(f"{uuid}: REFUSED at condense: {refused}", flush=True)
            problems += 1
            continue
        session = session_factory()
        try:
            result = store_replay(session, condensed, source="local", replace=True)
        finally:
            session.close()
        split = refresh_replay_impact(session_factory, result.replay_id) if result.link_status == "linked" else "-"
        print(f"{uuid}: {result.action}, {result.link_status}, per-kill Impact {split}", flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

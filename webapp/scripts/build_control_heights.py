"""Builds a map's heights for map control from the stored rounds
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 3; app/control/height_build.py).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\build_control_heights.py --map Ascent
    .\\.venv\\Scripts\\python.exe scripts\\build_control_heights.py --map Lotus --blobs-dir <dir> --preview --out %TEMP%\\valo-replay\\heights-preview

It only reads rounds: from the database it is run against (always through `with_friends_db.py
--read-only`), or from `--blobs-dir`, a folder of local round blobs laid out `<match>/<n>.json.gz`. Only
rounds of the map at condenser revision 11 or later count: older ones carry no heights.

Without `--preview` it writes `app/static/data/control/<Map>.height.npz` and the map's `height_sha` and
`height` (the report) in `index.json`, and only for a map at the readiness bar (60% of walkable cells
supported, no large unresolved area beside a two-floor cell): below it, it refuses and writes nothing.
Committing the asset is what turns the map's heights on; its rounds are then stale until
`compute_control.py --map <Map>` recomputes them.

With `--preview --out <dir>` it builds below the bar too and writes the asset and the report under `<dir>`
only, which must be outside the repository: a preview is for looking, never for committing.
`--preview-min-matches 1` also lets a preview make floors from a single match (the real rule is two, so one
match's Sage wall or boost can't become a floor); the asset records that it was built that way.

Before writing, it runs the two checks on the new heights: the kill lines of the rounds it read (both
ends with z, on resolved cells, clear in 2D; at most 2% may be blocked) and the must-block set
(`tests/replays/control_must_block.json`: sightlines impossible in game, each of which must be blocked).
A map that fails either is refused unless `--accept-failures` (the user has looked at the listed
failures and accepts them). A must-block line whose heights aren't known yet is listed as not checked.

Either way the review picture goes to `%TEMP%\\valo-replay\\heights\\<Map>.height.png` (never committed),
and every unresolved area is printed as a `WARNING` line: those cells keep today's flat sight and walking.
Exits 0 when written, 2 when refused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WEBAPP_ROOT.parent
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402
from app.control import height_build as hb  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.replays import format as fmt  # noqa: E402

MIN_REVISION = 11
MUST_BLOCK = WEBAPP_ROOT / "tests" / "replays" / "control_must_block.json"


def blob_rounds(directory: Path, map_name: str) -> tuple[list, dict]:
    """[(match, n, blob)] from `<directory>/<match>/<n>.json.gz`, and how many were skipped and why."""
    rounds, skipped = [], {"other_map": 0}
    for path in sorted(directory.glob("*/*.json.gz"), key=lambda p: (p.parent.name, int(p.name.split(".")[0]))):
        blob = fmt.decode_blob(path.read_bytes())
        if blob.get("map") != map_name:
            skipped["other_map"] += 1
            continue
        rounds.append((path.parent.name, int(path.name.split(".")[0]), blob))
    return rounds, skipped


def db_rounds(map_name: str, session_factory=None) -> tuple[list, dict]:
    """[(match uuid, n, blob)] of the map's replays at revision 11 or later, read-only."""
    from app.models.replay import Replay, ReplayRound
    from app.services.replay_control import condense_revision

    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    rounds, skipped = [], {"old_revision": 0}
    try:
        for replay in session.query(Replay).filter(Replay.map_name == map_name).order_by(Replay.id):
            if (condense_revision(replay.recipe) or 0) < MIN_REVISION:
                skipped["old_revision"] += 1
                continue
            for row in session.query(ReplayRound).filter(ReplayRound.replay_id == replay.id) \
                    .order_by(ReplayRound.round_number):
                rounds.append((str(replay.match_uuid).lower(), row.round_number, fmt.decode_blob(row.data)))
    finally:
        session.rollback()
        session.close()
    return rounds, skipped


def picture_path(map_name: str) -> Path:
    folder = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay" / "heights"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{map_name}.height.png"


def index_entry(build: hb.HeightBuild) -> dict:
    """What a built map adds to its index.json entry (build_control_geometry.py keeps these keys)."""
    return {"height_sha": build.asset.digest, "height": build.report}


def run_checks(map_name: str, flat_geo, build: hb.HeightBuild, rounds: list) -> dict:
    """The kill-line and must-block checks on the new heights; printed, and stored in the report."""
    import copy

    geo = cg.attach_heights(copy.copy(flat_geo), build.asset)
    kills = hb.kill_line_check(rounds, geo)
    lines = json.loads(MUST_BLOCK.read_text(encoding="utf-8"))["lines"] if MUST_BLOCK.is_file() else []
    must = hb.must_block_check(lines, geo, map_name)
    excluded = ", ".join(f"{why} {n}" for why, n in kills["excluded"].items()) or "none"
    print(f"  kill lines: {kills['blocked']}/{kills['qualifying']} blocked by heights ({kills['share']:.1%}, "
          f"bar {hc.KILL_LINE_BAR:.0%}): {'PASS' if kills['passes'] else 'FAIL'}"
          f"{' (no qualifying kill: nothing was checked)' if not kills['qualifying'] else ''}; excluded: {excluded}",
          flush=True)
    for example in kills["examples"]:
        print(f"    blocked: {example}", flush=True)
    print(f"  must-block: {must['blocked']}/{must['checked']} blocked, {must['unchecked']} not checked (no heights "
          f"yet): {'PASS' if must['passes'] else 'FAIL'}", flush=True)
    for result in must["results"]:
        if result["checked"] and not result["blocked"]:
            print(f"    NOT blocked: {result['source']}", flush=True)
    build.report["kill_lines"], build.report["must_block"] = kills, must
    return {"kill_lines": kills, "must_block": must}


def main(argv: list[str] | None = None, asset_dir: Path | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--blobs-dir", type=Path, help="local round blobs, <match>/<n>.json.gz (default: the database)")
    parser.add_argument("--preview", action="store_true", help="build below the bar too; write only under --out")
    parser.add_argument("--out", type=Path, help="with --preview: the folder to write to, outside the repository")
    parser.add_argument("--accept-failures", action="store_true",
                        help="write even if the kill-line or must-block check fails (after reading its failures)")
    parser.add_argument("--preview-min-matches", type=int,
                        help=f"with --preview: matches a floor needs (default {hc.FLOOR_MIN_MATCHES}), to look at a "
                             f"map that has too few yet")
    args = parser.parse_args(argv)
    if args.preview_min_matches is not None and not args.preview:
        parser.error("--preview-min-matches is for --preview only")
    asset_dir = asset_dir or cg.ASSET_DIR
    if args.preview:
        if args.out is None:
            parser.error("--preview needs --out")
        if args.out.resolve().is_relative_to(REPO_ROOT.resolve()):
            print(f"REFUSED: --out {args.out} is inside the repository; a preview is never committed", file=sys.stderr)
            return 2
    elif args.out is not None:
        parser.error("--out is for --preview only")
    geo = cg.load_geometry(args.map, asset_dir)
    started = time.perf_counter()
    rounds, skipped = blob_rounds(args.blobs_dir, args.map) if args.blobs_dir else db_rounds(args.map, session_factory)
    print(f"{args.map}: {len(rounds)} rounds read in {time.perf_counter() - started:.1f}s"
          + "".join(f", {n} skipped ({why})" for why, n in skipped.items() if n), flush=True)
    started = time.perf_counter()
    rule = hc.FLOOR_MIN_MATCHES
    try:
        if args.preview_min_matches is not None:
            hc.FLOOR_MIN_MATCHES = args.preview_min_matches     # a preview's own, looser rule: never an asset's
            print(f"PREVIEW RULE: a floor needs {args.preview_min_matches} match(es), not {rule}", flush=True)
        build = hb.build(rounds, geo)
    finally:
        hc.FLOOR_MIN_MATCHES = rule
    if args.preview_min_matches is not None:
        build.asset.meta["preview_min_matches"] = args.preview_min_matches
        build.report["preview_min_matches"] = args.preview_min_matches
    for line in hb.report_lines(args.map, build.report):
        print(line, flush=True)
    print(f"  built in {time.perf_counter() - started:.1f}s", flush=True)
    checks = run_checks(args.map, geo, build, rounds)
    png = picture_path(args.map)
    hb.picture(build, geo, png)
    print(f"  picture: {png}", flush=True)
    if args.preview:
        args.out.mkdir(parents=True, exist_ok=True)
        hc.save_asset(args.out / f"{args.map}.height.npz", build.asset)
        (args.out / f"{args.map}.height.json").write_text(json.dumps(index_entry(build), indent=1) + "\n",
                                                          encoding="utf-8")
        print(f"PREVIEW written to {args.out} (digest {build.asset.digest}); nothing in the repository changed")
        return 0
    if not build.ready:
        print(f"REFUSED: {args.map} is below the bar ({'; '.join(build.report['not_ready'])}); nothing written. "
              f"Use --preview --out <dir> to look at it.", file=sys.stderr)
        return 2
    failed = [name for name in ("kill_lines", "must_block") if not checks[name]["passes"]]
    if failed and not args.accept_failures:
        print(f"REFUSED: {args.map} fails {' and '.join(failed)} (above); nothing written. Look at the failures, "
              f"and pass --accept-failures only if you accept them.", file=sys.stderr)
        return 2
    hc.save_asset(asset_dir / f"{args.map}.height.npz", build.asset)
    index_path = asset_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["maps"].setdefault(args.map, {}).update(index_entry(build))
    index_path.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    print(f"WROTE {asset_dir / (args.map + '.height.npz')} (digest {build.asset.digest}) and its index.json entry. "
          f"Review the picture, then commit both; the map's rounds are stale until "
          f"compute_control.py --map {args.map} recomputes them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

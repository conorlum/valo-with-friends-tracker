"""The map heights the replay worker built: look at them, put an earlier one back, turn a map's off, or write
the active one to a folder (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 4).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\control_heights.py list [--map Sunset]
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\control_heights.py export --map Sunset --out %TEMP%\\valo-replay\\heights-db
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\control_heights.py activate --map Sunset --digest 0123456789ab
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\control_heights.py off --map Sunset

`list` prints every build in `control_heights`, newest first: its map, digest, status, when it was built, from
how many matches, its supported share, both checks, how long it took, and for a rejected one why.
`activate` makes an earlier build the map's active heights (activating the one that is active changes nothing);
`off` leaves the map with none (flat). Either way the map's stored rounds turn stale and the replay worker
recomputes them when it is idle; the pages keep showing the stored ones, marked out of date, until then.
Neither stops the next automatic rebuild: for that, unset REPLAY_HEIGHTS_AUTO on the web service. Both emit
fresh feature diagnostics and prepare the selected artifact independently of height activation.
`export` writes the map's active asset and its report to a folder outside the repository, in the layout of a
preview, for `height_viewer.py --dir` and `control_tagger.py --heights-dir`.

Exits 0 when done, 2 when refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def describe(row) -> str:
    from app.services import control_heights

    report = row.report or {}
    kills, must = report.get("kill_lines") or {}, report.get("must_block") or {}
    parts = [f"{row.map_name} {row.digest} {row.status}", f"built {row.built_at:%Y-%m-%d %H:%M}" if row.built_at else "",
             f"{len(row.match_uuids or [])} matches", f"inputs {row.inputs_sha}",
             f"supported {report['supported']:.1%}" if isinstance(report.get("supported"), float) else "",
             f"kill lines {kills.get('blocked', '?')}/{kills.get('qualifying', '?')}",
             f"must-block {'pass' if must.get('passes') else 'FAIL'}",
             f"{report['seconds']:.0f} s" if isinstance(report.get("seconds"), (int, float)) else "",
             f"height version {(row.rules or {}).get('version')}, rules {(row.rules or {}).get('revision')}"]
    if row.status == control_heights.REJECTED:
        try:
            parts.append("rejected: " + "; ".join(control_heights.gate(report)))
        except (KeyError, TypeError, ZeroDivisionError):
            parts.append("rejected")
    diagnostic = report.get('features')
    if isinstance(diagnostic, dict):
        diagnostic = diagnostic.get('authoritative', diagnostic)
        if diagnostic.get('status') == 'ok':
            pending = [f['id'] for f in diagnostic.get('features', []) if f.get('placement') == 'pending']
            parts.append(f"{len(pending)} tagged feature(s) pending: " + ', '.join(pending))
        else:
            parts.append('feature diagnostics unavailable: ' + str(diagnostic.get('reason', 'unknown')))
    elif diagnostic:  # Historical reports from the previous compiler.
        parts.append(f"{len(diagnostic)} tagged feature(s) no longer fit: "
                     + ", ".join(str(f.get("feature")) for f in diagnostic))
    return "; ".join(p for p in parts if p)


def inside_a_repository(path: Path) -> bool:
    out = path.resolve()
    return out.is_relative_to(WEBAPP_ROOT.parent.resolve()) or any((f / ".git").exists() for f in (out, *out.parents))


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list").add_argument("--map")
    activate = commands.add_parser("activate")
    activate.add_argument("--map", required=True)
    activate.add_argument("--digest", required=True)
    commands.add_parser("off").add_argument("--map", required=True)
    export = commands.add_parser("export")
    export.add_argument("--map", required=True)
    export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    from app.services import control_heights

    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    try:
        if args.command == "list":
            found = control_heights.rows(session, args.map)
            for row in found:
                print(describe(row), flush=True)
            if not found:
                print("no heights have been built" + (f" for {args.map}" if args.map else ""), flush=True)
            return 0
        if args.command == "export":
            if inside_a_repository(args.out):
                print(f"REFUSED: --out {args.out} is inside a repository", file=sys.stderr)
                return 2
            row = control_heights.active_rows(session).get(args.map)
            data = None if row is None else control_heights.asset_bytes(session, args.map, row.digest)
            if data is None:
                print(f"REFUSED: {args.map} has no active heights in the database", file=sys.stderr)
                return 2
            report = next(r.report for r in control_heights.rows(session, args.map) if r.id == row.id)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{args.map}.height.npz").write_bytes(data)
            (args.out / f"{args.map}.height.json").write_text(
                json.dumps({"height_sha": row.digest, "height": report}, indent=1) + "\n", encoding="utf-8")
            print(f"{args.map}: {row.digest} written to {args.out}", flush=True)
            return 0
        if args.command == "activate":
            why = control_heights.activate(session, args.map, args.digest)
            if why:
                print(f"REFUSED: {why}", file=sys.stderr)
                return 2
            print(f"{args.map}: {args.digest} is active. Rounds computed with other heights are stale and will be "
                  f"recomputed when the replay worker is idle.", flush=True)
            return 0
        had = control_heights.deactivate(session, args.map)
        print(f"{args.map}: heights off; its rounds are stale and will be recomputed flat." if had
              else f"{args.map}: no active heights; nothing changed.", flush=True)
        return 0
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    sys.exit(main())

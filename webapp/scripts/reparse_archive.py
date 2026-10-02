"""Re-parses archived .vrf files on the worker with today's parser and condenser, one match at a time, and
stores each result like an upload (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1).
Use it after a condenser change so stored replays pick up what the old revision dropped.

    REPLAY_ADMIN_TOKEN=<from the Render dashboard>
    .venv\\Scripts\\python.exe scripts\\reparse_archive.py [--map Ascent] [--since 2026-10-01] [--match UUID]
                                                       [--dry-run] [--site URL]

`--since` compares the match's date (known once the replay linked to a crawled match) or, without one, the
date the file was archived. Reparses share the worker's queue with uploads, behind them.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _admin_client import DEFAULT_SITE, AdminError, call  # noqa: E402

POLL_S = 5
GIVE_UP_S = 30 * 60


def select(files: list[dict], map_name: str | None, since: str | None, match: str | None) -> list[dict]:
    chosen = []
    for entry in files:
        if match and entry["match_uuid"] != match.lower():
            continue
        if map_name and (entry.get("map") or "").lower() != map_name.lower():
            continue
        if since and (entry.get("played_at") or entry.get("accepted_at") or "")[:10] < since:
            continue
        chosen.append(entry)
    return sorted(chosen, key=lambda e: e.get("played_at") or e.get("accepted_at") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--map", help="only this map (as the replay names it, e.g. Ascent)")
    parser.add_argument("--since", help="only matches on or after this date (YYYY-MM-DD)")
    parser.add_argument("--match", help="only this match UUID")
    parser.add_argument("--dry-run", action="store_true", help="list what would be reparsed and stop")
    parser.add_argument("--site", default=DEFAULT_SITE, help=f"the site (default {DEFAULT_SITE})")
    args = parser.parse_args(argv)
    try:
        files = call(args.site, "GET", "/admin/replays/archive").get("files", [])
    except AdminError as error:
        print(f"could not read the archive: {error}", file=sys.stderr)
        return 1
    chosen = select(files, args.map, args.since, args.match)
    print(f"{len(chosen)} of {len(files)} archived matches selected", flush=True)
    if args.dry_run:
        for entry in chosen:
            print(f"  {entry['match_uuid']}  {entry.get('map')}  {entry.get('played_at') or '(no date)'}")
        return 0
    problems = 0
    for entry in chosen:
        uuid = entry["match_uuid"]
        try:
            upload_id = call(args.site, "POST", "/admin/replays/reparse", {"match_uuid": uuid})["upload_id"]
            started = time.time()
            while True:
                time.sleep(POLL_S)
                state = call(args.site, "GET", f"/admin/replays/uploads/{upload_id}")
                if state["status"] not in ("queued", "parsing") or time.time() - started > GIVE_UP_S:
                    break
        except AdminError as error:
            print(f"{uuid}: FAILED: {error}", flush=True)
            problems += 1
            continue
        print(f"{uuid}: {state['status']} ({state.get('store_outcome')}; archive {state.get('archive_ack')})"
              + (f": {state['error']}" if state.get("error") else ""), flush=True)
        problems += state["status"] != "stored"
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

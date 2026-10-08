"""Re-parses archived .vrf files on the worker with today's parser and condenser, one match at a time, and
stores each result like an upload (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1).
Use it after a condenser change so stored replays pick up what the old revision dropped.

    REPLAY_ADMIN_TOKEN=<from the Render dashboard>
    .venv\\Scripts\\python.exe scripts\\reparse_archive.py [--map Ascent] [--since 2026-10-01] [--match UUID]
                                                       [--dry-run] [--site URL]
    .venv\\Scripts\\python.exe scripts\\reparse_archive.py --status [--site URL]

`--since` compares the match's date (known once the replay linked to a crawled match) or, without one, the
date the file was archived. Reparses share the worker's queue with uploads, behind them.

`--status` prints where the site's automatic re-parse queue stands (REPLAY_REPARSE_AUTO;
app/services/replay_reparse_auto.py) and exits: it re-parses nothing and changes nothing.
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


def describe(body: dict) -> list[str]:
    """The lines `--status` prints for the answer of GET /admin/replays/reparse/status."""
    def shown(value) -> str:
        if value is None:
            return "not reported"
        if isinstance(value, bool):   # before the numbers: a protocol 1 is not "on"
            return "on" if value else "off"
        return str(value)

    worker = body.get("worker") or {}
    lines = ["automatic re-parse: " + ("running" if body.get("running") else f"stopped: {body.get('reason')}"),
             f"recipes: site {body.get('site_recipe')}, worker {body.get('worker_recipe') or 'unknown'}"]
    if worker.get("reachable"):
        lines.append(f"worker: archive {shown(worker.get('archive_enabled'))}, re-parse protocol "
                     f"{shown(worker.get('reparse_protocol'))}, map control {shown(worker.get('control_enabled'))}, "
                     f"gaps protocol {shown(worker.get('gaps_protocol'))}")
    else:
        lines.append("worker: unreachable")
    counts = body.get("counts") or {}
    lines.append("replays: " + (", ".join(f"{n} {state.replace('_', ' ')}" for state, n in counts.items() if n)
                                or "none stored"))
    if body.get("waiting_on"):
        lines.append(f"next attempt waits: {body['waiting_on']}")
    for attempt in body.get("attempts") or []:
        lines.append(f"attempt {attempt.get('attempt_id')}: {attempt.get('state')}, match {attempt.get('match_uuid')}, "
                     f"for {attempt.get('target_recipe')}, since {attempt.get('reserved_at')}"
                     + (f" (left as it is: {attempt['deferred']})" if attempt.get("deferred") else ""))
    for entry in body.get("gave_up") or []:
        lines.append(f"gave up: {entry.get('match_uuid')}  {entry.get('map')}  after {entry.get('attempts')} "
                     f"attempt(s): {entry.get('last_error')}")
    pending = body.get("restoration_pending")
    if pending:
        lines.append(f"restoration pending: {pending.get('match_uuid')} still needs {pending.get('control_rounds')} "
                     f"control round(s) and {pending.get('gap_rounds')} gap round(s)")
    rounds = body.get("rounds") or {}
    lines.append(f"rounds waiting: {rounds.get('control_waiting', 0)} for map control, "
                 f"{rounds.get('gaps_waiting', 0)} for timing gaps, {rounds.get('held_back', 0)} held back for a re-parse")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--map", help="only this map (as the replay names it, e.g. Ascent)")
    parser.add_argument("--since", help="only matches on or after this date (YYYY-MM-DD)")
    parser.add_argument("--match", help="only this match UUID")
    parser.add_argument("--dry-run", action="store_true", help="list what would be reparsed and stop")
    parser.add_argument("--site", default=DEFAULT_SITE, help=f"the site (default {DEFAULT_SITE})")
    parser.add_argument("--status", action="store_true",
                        help="print where the automatic re-parse queue stands and stop; changes nothing")
    args = parser.parse_args(argv)
    if args.status:
        try:
            body = call(args.site, "GET", "/admin/replays/reparse/status")
        except AdminError as error:
            print(f"could not read the status: {error}", file=sys.stderr)
            return 1
        print("\n".join(describe(body)), flush=True)
        return 0
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

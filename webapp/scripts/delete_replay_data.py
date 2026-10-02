"""Deletes one match's replay data on request: the stored replay (rounds, players, map control) and the
archived and pending .vrf files on the worker, and tombstones the match so no later upload, reparse or
local ingest can store it again (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 1).

    REPLAY_ADMIN_TOKEN=<from the Render dashboard>
    .venv\\Scripts\\python.exe scripts\\delete_replay_data.py <match uuid> [--reason "asked by X"] [--site URL]

Idempotent: run it again for the same match and nothing changes. Render keeps automatic snapshots of the
worker's disk, so a deleted file can live on in an older snapshot until it expires; if a snapshot is ever
restored, the site re-sends every tombstone to the worker when it restarts, which deletes it again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _admin_client import DEFAULT_SITE, AdminError, call  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("match_uuid", help="the match's UUID (the replay page's address ends with it)")
    parser.add_argument("--reason", default="", help="why, kept with the tombstone")
    parser.add_argument("--site", default=DEFAULT_SITE, help=f"the site (default {DEFAULT_SITE})")
    args = parser.parse_args(argv)
    try:
        answer = call(args.site, "POST", "/admin/replays/delete", {"match_uuid": args.match_uuid,
                                                                    "reason": args.reason})
    except AdminError as error:
        print(f"not deleted: {error}", file=sys.stderr)
        return 1
    print(json.dumps(answer, indent=2))
    if not answer.get("worker", {}).get("told"):
        print("The worker wasn't reached; the site re-sends the tombstone the next time the worker restarts. "
              "Run this again later to make sure.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

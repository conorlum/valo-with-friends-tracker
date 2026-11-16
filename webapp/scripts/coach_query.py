"""Coach questions about rounds, answered from a local snapshot and logged. No database, no network.

    $PY scripts/coach_query.py --run <dir> where <round> <t>
    $PY scripts/coach_query.py --run <dir> knew <round> <t> <A|B|player>
    $PY scripts/coach_query.py --run <dir> distance <round> <t> <player> <player>
    $PY scripts/coach_query.py --run <dir> control <round> <t0> <t1> "<area>"
    $PY scripts/coach_query.py --run <dir> timeline <round> [<t0> <t1>]
    $PY scripts/coach_query.py --run <dir> impact <round> <player>
    $PY scripts/coach_query.py --run <dir> deaths|kills|teammate_deaths [<player>]
    $PY scripts/coach_query.py --run <dir> moment <round> <t>

<dir> holds the snapshot and receives <dir>/log.jsonl. Prints JSON with the call's log id by default;
--compact prints a header and compact tables, with fixed notes supplied in the run prompt. Times are seconds into
the round. Separate questions with a lone + argument. Each answer has its own log id, including error answers.
Empty parts and unexpected faults are reported while later questions still run, and exit with status 1."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError):
        # Embedded callers may provide a text stream with no configurable encoding.
        pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, type=Path, action="append")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("command")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if len(args.run) != 1:
        print("refused: --run may be given only once", file=sys.stderr)
        return 2
    args.run = args.run[0]
    from app.coach import compact, patterns, query, snapfile as snapshot

    parts = [[]]
    for arg in [args.command, *args.args]:
        if arg == "+":
            parts.append([])
        else:
            parts[-1].append(arg)

    snap = snapshot.load(args.run)
    status = 0
    for position, part in enumerate(parts, 1):
        if not part:
            print(f"error: empty query at position {position}")
            status = 1
            continue
        command, *query_args = part
        try:
            if command in patterns.COMMANDS:
                result = patterns.run(snap, args.run, command, query_args)
                if command == "moment":
                    for entry in result["entries"]:
                        print(query.show(entry) if args.compact
                              else json.dumps({"id": entry["id"], **entry["result"]}))
                else:
                    print(compact.render(result, short=True) if args.compact else json.dumps(result))
                continue
            result = query.run(snap, args.run, command, query_args)
            if args.compact:
                print(query.show({"id": result["id"], "cmd": command, "args": query_args,
                                  "result": {k: v for k, v in result.items() if k != "id"}}))
            else:
                print(json.dumps(result))
        except query.QueryError as error:
            print(f"error: {error}")
            status = 1
            continue
        except Exception as error:
            print(f"error: part {position} failed: {type(error).__name__}")
            traceback.print_exc(file=sys.stderr)
            status = 1
            continue
    return status


if __name__ == "__main__":
    raise SystemExit(main())

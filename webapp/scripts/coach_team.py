"""Writes and publishes a coaching report for every player on one player's team: one coach_report.py run each.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_team.py <match uuid> "<player>" [--model <name>] [--dry-run | --prompt-only]

The runs are separate processes, one after another, never two at once; each has its own run directory, so one
player's failure leaves the others' reports alone and the next player still runs. Exits 2 when refused, 1 when
any run failed."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def report_command(match_uuid: str, player: str, model: str | None, mode: str | None) -> list[str]:
    command = [sys.executable, str(WEBAPP_ROOT / "scripts" / "coach_report.py"), match_uuid, player]
    if model:
        command += ["--model", model]
    if mode:
        command.append(mode)
    return command


def run_one(command: list[str]) -> int:
    """One player's run, writing straight to this terminal."""
    try:
        return subprocess.call(command, cwd=WEBAPP_ROOT)
    except OSError as error:
        print(f"could not start: {error}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("player")
    parser.add_argument("--model")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_const", const="--dry-run", dest="mode")
    mode.add_argument("--prompt-only", action="store_const", const="--prompt-only", dest="mode")
    args = parser.parse_args()
    from app.db import SessionLocal
    from scripts.coach_store_report import team_names

    with SessionLocal() as db:
        try:
            names = team_names(db, args.match_uuid, args.player)
        except ValueError as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
    print(f"coaching {len(names)} players, one at a time: {', '.join(names)}", flush=True)
    failed = []
    for at, name in enumerate(names, start=1):
        print(f"\n== {at} of {len(names)}: {name} ==", flush=True)
        code = run_one(report_command(args.match_uuid, name, args.model, args.mode))
        if code != 0:
            print(f"== {name}: FAILED (exit {code}) ==", flush=True)
            failed.append(name)
    print(f"\n{len(names) - len(failed)} of {len(names)} runs succeeded"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Writes and publishes a coaching report for every player on one player's team: one coach_report.py run each.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_team.py <match uuid> "<player>" [--jobs <n>] [--model <name>] [--dry-run | --prompt-only]

The runs are separate processes, `--jobs` at a time (default: the whole team at once); each has its own run
directory, so one player's failure leaves the others' reports alone. A player's output is printed when their run
ends. Exits 2 when refused, 1 when any run failed."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def run_one(command: list[str]) -> tuple[int, str]:
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=WEBAPP_ROOT,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return done.returncode, (done.stdout + done.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("player")
    parser.add_argument("--model")
    parser.add_argument("--jobs", type=int, default=0, help="runs at a time; 0 (the default) is the whole team")
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
    jobs = args.jobs if args.jobs > 0 else len(names)
    print(f"coaching {len(names)} players, {jobs} at a time: {', '.join(names)}", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        runs = {pool.submit(run_one, report_command(args.match_uuid, name, args.model, args.mode)): name
                for name in names}
        for run in as_completed(runs):
            name = runs[run]
            try:
                code, output = run.result()
            except OSError as error:
                code, output = 1, f"could not start: {error}"
            print(f"\n== {name}: {'done' if code == 0 else f'FAILED (exit {code})'} ==\n{output}", flush=True)
            if code != 0:
                failed.append(name)
    print(f"\n{len(names) - len(failed)} of {len(names)} runs succeeded"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

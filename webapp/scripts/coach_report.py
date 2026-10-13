"""Writes and publishes one coaching report: snapshot, brief, headless Claude Code, checker, store.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_report.py <match uuid> "<player>" [--model <name>] [--voice-file <path>] [--dry-run | --prompt-only]

The model runs in the snapshot's own directory under the system temp folder, with an allow-listed environment
(no DATABASE_URL) and one permitted command, ./q (the logged query tool). `--dry-run` prints the payload and stores
nothing. `--prompt-only` writes the exact first prompt to <run dir>/prompt.txt, prints its path and length, and
calls no model. Storing publishes the report on the site. Exits 2 when refused, 1 when the run failed."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

TIMEOUT_S = 1800
MODEL_DIR = "model"   # PROVISIONAL(D12): the model's cwd holds only q; snapshot and log stay one level up, where reads are refused (probe 3g).

# What the model's process may inherit. Everything else (database URL, API keys, any other secret) is dropped.
ENV_ALLOW = frozenset({"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "USERPROFILE",
                       "USERNAME", "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                       "PROGRAMFILES", "PROGRAMFILES(X86)", "TEMP", "TMP", "LANG", "TERM"})


def child_env(environ: dict) -> dict:
    return {k: v for k, v in environ.items() if k.upper() in ENV_ALLOW or k.upper().startswith("CLAUDE_")}


def write_q(run_dir: Path, python: Path) -> Path:
    """The one command the model may run: a bash script (Claude Code's shell on Windows is Git Bash)."""
    posix = lambda p: str(p).replace("\\", "/")
    path = Path(run_dir) / MODEL_DIR / "q"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n"
                    f'exec "{posix(python)}" "{posix(WEBAPP_ROOT / "scripts" / "coach_query.py")}" '
                    f'--run "{posix(Path(run_dir).resolve())}" "$@"\n', encoding="utf-8", newline="\n")
    return path


def claude_command(exe: str, model: str | None, session: str | None) -> list[str]:
    """Headless Claude Code with one tool, Bash, one pre-approved command, ./q, and none of the owner's own
    setup: no user settings, hooks, plugins, skills, CLAUDE.md or MCP servers. `--allowedTools` alone only
    pre-approves; `--tools` is what removes every other tool. Never add a flag that skips permission checks."""
    command = [exe, "-p", "--output-format", "json", "--restricted", "--safe-mode", "--strict-mcp-config",
               "--tools", "Bash", "--allowedTools", "Bash(./q:*)"]
    if model:
        command += ["--model", model]
    if session:
        command += ["--resume", session]
    return command


def claude_call(run_dir: Path, model: str | None):
    exe = shutil.which("claude")
    if exe is None:
        raise SystemExit("refused: the claude command is not on PATH")

    def call(text: str, session: str | None):
        done = subprocess.run(claude_command(exe, model, session), input=text, capture_output=True, text=True,
                              encoding="utf-8", cwd=Path(run_dir) / MODEL_DIR, env=child_env(dict(os.environ)), timeout=TIMEOUT_S)
        if done.returncode != 0:
            raise RuntimeError(f"claude exited {done.returncode}: {done.stderr.strip()[:500]}")
        out = json.loads(done.stdout)
        usage = out.get("modelUsage")
        if isinstance(usage, dict):
            call.models.update(str(name) for name in usage)
        return str(out.get("result") or ""), out.get("session_id")

    call.models = set()
    return call


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("player")
    parser.add_argument("--model")
    parser.add_argument("--voice-file", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--prompt-only", action="store_true",
                      help="write the first prompt to <run dir>/prompt.txt and stop; no model is called")
    args = parser.parse_args()
    from app.coach import playbook, prompt, runner, snapshot
    from app.db import SessionLocal
    from app.services import coach
    from scripts.coach_store_report import resolve

    uuid = args.match_uuid.lower()
    with SessionLocal() as db:
        try:
            book = playbook.load()
            match_id, player_id = resolve(db, uuid, args.player)
            data = snapshot.read(db, uuid)
        except (ValueError, snapshot.SnapshotError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
        voice = ([line.strip() for line in args.voice_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                 if args.voice_file else coach.active_notes(db, "voice"))
    # One directory per match and player: a second player's run must not delete the first's files.
    run_dir = Path(tempfile.gettempdir()) / "valo-coach" / f"{uuid}-p{player_id}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    snapshot.save(data, run_dir)
    write_q(run_dir, Path(sys.executable))
    snap = snapshot.load(run_dir)
    try:
        slot = snap.slot_of(args.player)
    except snapshot.SnapshotError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.prompt_only:
        built, _ = runner._brief_with_flag_ids(snap, slot, run_dir)
        text = prompt.run_prompt(book, voice, built)
        path = run_dir / "prompt.txt"
        path.write_text(text, encoding="utf-8")
        print(f"{path}\n{len(text)} characters")
        return 0
    call = claude_call(run_dir, args.model)
    try:
        payload = runner.generate(snap, slot, run_dir, book, voice, call,
                                  args.model or "claude-code (default model)")
    except (runner.RunError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"run failed: {error}; the run's files are in {run_dir}", file=sys.stderr)
        return 1
    if not args.model and call.models:
        payload["model"] = ", ".join(sorted(call.models))[:64]      # what the CLI says it actually used
    # Written before anything is stored: a report that cost a model run is never lost to a failed store.
    (run_dir / "payload.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    if args.dry_run:
        print(json.dumps(payload, indent=1))
        return 0
    try:
        with SessionLocal() as db:
            report = coach.store_report(db, match_id, player_id, payload)
    except Exception as error:      # noqa: BLE001 - whatever the store raises, the report is still on disk
        print(f"the report was written but not stored: {error}\nIt is in {run_dir / 'payload.json'}; store it with "
              f"scripts/coach_store_report.py once the cause is fixed.", file=sys.stderr)
        return 1
    print(f"published version {report.version} ({len(payload['points'])} points, {payload['dropped_points']} dropped): "
          f"/replays/{uuid}/coach?player={player_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

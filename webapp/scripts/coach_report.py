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
import uuid as uuid_module
from datetime import datetime
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

TIMEOUT_S = 1800
MODEL_DIR = "model"   # the model's cwd holds only q; snapshot and log stay one level up, where reads are refused (probe 3g).

# What the model's process may inherit. Everything else (database URL, API keys, any other secret) is dropped.
# Of Claude Code's own variables only where its config and its shell are: a session that launches this script
# carries CLAUDE_CODE_* tokens and sockets of its own, which the model's process must not inherit.
ENV_ALLOW = frozenset({"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "USERPROFILE",
                       "USERNAME", "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                       "PROGRAMFILES", "PROGRAMFILES(X86)", "TEMP", "TMP", "LANG", "TERM",
                       "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH"})


def child_env(environ: dict) -> dict:
    return {k: v for k, v in environ.items() if k.upper() in ENV_ALLOW}


def write_q(run_dir: Path, python: Path) -> Path:
    """The one command the model may run: a bash script (Claude Code's shell on Windows is Git Bash)."""
    posix = lambda p: str(p).replace("\\", "/")
    path = Path(run_dir) / MODEL_DIR / "q"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n"
                    f'exec "{posix(python)}" "{posix(WEBAPP_ROOT / "scripts" / "coach_query.py")}" '
                    f'--run "{posix(Path(run_dir).resolve())}" -- "$@"\n', encoding="utf-8", newline="\n")
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


_USAGE = {"input_tokens": "input", "output_tokens": "output", "cache_read_input_tokens": "cache_read",
          "cache_creation_input_tokens": "cache_write"}


def add_usage(total: dict, out: dict) -> None:
    """Adds one model call's cost to the run's total: a run is the first call plus any repair."""
    def number(value) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    total["calls"] = total.get("calls", 0) + 1
    total["cost_usd"] = total.get("cost_usd", 0.0) + number(out.get("total_cost_usd"))
    total["turns"] = total.get("turns", 0) + int(number(out.get("num_turns")))
    total["seconds"] = total.get("seconds", 0.0) + number(out.get("duration_ms")) / 1000.0
    tokens = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    for key, name in _USAGE.items():
        total[name] = total.get(name, 0) + int(number(tokens.get(key)))


def usage_line(total: dict) -> str:
    if not total:
        return "usage: no model call finished"
    return (f"usage: ${total['cost_usd']:.2f} at API prices, {total['calls']} call(s), {total['turns']} turns, "
            f"{total['seconds'] / 60:.1f} min; tokens: {total['input']:,} in, {total['output']:,} out, "
            f"{total['cache_read']:,} cache read, {total['cache_write']:,} cache write")


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
        denials = out.get("permission_denials")
        call.denials += len(denials) if isinstance(denials, list) else 0
        add_usage(call.usage, out)
        return str(out.get("result") or ""), out.get("session_id")

    call.usage = {}
    call.models = set()
    call.denials = 0        # commands the model tried and was refused: a clean run has none
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
    from scripts.coach_store_report import on_roster, resolve

    try:
        uuid = str(uuid_module.UUID(args.match_uuid))
    except ValueError:
        print(f"refused: {args.match_uuid!r} is not a match uuid", file=sys.stderr)
        return 2
    if not on_roster(args.player):
        print(f"refused: {args.player!r} is not on the roster (scripts/tracked_players.json)", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        try:
            book = playbook.load()
            match_id, player_id = resolve(db, uuid, args.player)
            data = snapshot.read(db, uuid)
            snapshot.hide_enemy_names(data, args.player)
        except (ValueError, snapshot.SnapshotError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
        voice = ([line.strip() for line in args.voice_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                 if args.voice_file else coach.active_notes(db, "voice"))
    # One directory per match and player: a second player's run must not delete the first's files.
    root = Path(tempfile.gettempdir()) / "valo-coach"
    run_dir = root / f"{uuid}-p{player_id}"
    if run_dir.exists():
        if run_dir.resolve().parent != root.resolve():
            print(f"refused: {run_dir} is not directly under {root}", file=sys.stderr)
            return 2
        for name in ("payload.json", runner.REFUSED_PAYLOAD):
            stored_away = run_dir / name
            if stored_away.exists():       # a finished run whose store failed or was refused: never delete it
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                keep = root / f"{uuid}-p{player_id}-{Path(name).stem}-{stamp}.json"
                shutil.move(str(stored_away), str(keep))
                print(f"an unstored report from an earlier run was moved to {keep}")
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
        print(usage_line(getattr(call, "usage", {})), flush=True)
        return 1
    print(usage_line(getattr(call, "usage", {})), flush=True)
    (run_dir / "usage.json").write_text(json.dumps(getattr(call, "usage", {}), indent=1), encoding="utf-8")
    if call.denials:
        print(f"the model tried {call.denials} command(s) it is not allowed and was refused", file=sys.stderr)
    if not args.model and call.models:
        payload["model"] = ", ".join(sorted(call.models))[:64]      # what the CLI says it actually used
    # Written before anything is stored: a report that cost a model run is never lost to a failed store.
    (run_dir / "payload.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    for weak in payload.get("unchecked") or []:      # published anyway (three a side); the owner sees why here
        print(f"unchecked: {weak['title']!r}: {'; '.join(weak['problems'])}", file=sys.stderr)
    if args.dry_run:
        print(json.dumps(payload, indent=1))
        return 0
    try:
        with SessionLocal() as db:
            version = coach.store_report(db, match_id, player_id, payload).version   # read before the session closes
    except Exception as error:      # noqa: BLE001 - whatever the store raises, the report is still on disk
        print(f"the report was written but not stored: {error}\nIt is in {run_dir / 'payload.json'}; store it with "
              f"scripts/coach_store_report.py once the cause is fixed.", file=sys.stderr)
        return 1
    print(f"published version {version} ({len(payload['points'])} points, {payload['dropped_points']} dropped): "
          f"/replays/{uuid}/coach?player={player_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

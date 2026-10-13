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
        call.usage.append({key: out.get(key) for key in USAGE_KEYS})
        return str(out.get("result") or ""), out.get("session_id")

    call.models = set()
    call.denials = 0        # commands the model tried and was refused: a clean run has none
    call.usage = []         # what the CLI reported for each call (the first prompt, then any follow-up)
    return call


def plan_usage() -> dict | None:
    """The active account's share of its plan limits used so far, in percent, from the owner's `cswap` tool
    (`cswap status --json`). None when the tool is missing or answers anything else: the run never depends on it."""
    exe = shutil.which("cswap")
    if exe is None:
        return None
    try:
        done = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=30)
        usage = json.loads(done.stdout)["active"]["usage"]
        return {"five_hour": float(usage["fiveHour"]["pct"]), "seven_day": float(usage["sevenDay"]["pct"])}
    except Exception:       # noqa: BLE001 - a missing or changed tool must not fail a coach run
        return None


USAGE_KEYS = ("total_cost_usd", "duration_ms", "num_turns", "usage", "modelUsage")
_TOKEN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def usage_summary(calls: list[dict]) -> dict:
    """One run's cost over its model calls, as the CLI reported each. Tokens, minutes and turns are per call and
    are summed. `total_cost_usd` is the session's running total (a follow-up resumes the session: the first real
    run reported 0.29 then 0.33 for a 3 second follow-up), so the cost is the largest, not the sum. It is the
    CLI's figure at API prices; on a subscription nothing is billed per run, so read it as a size."""
    tokens = {key: sum(int(((c.get("usage") or {}).get(key)) or 0) for c in calls) for key in _TOKEN_KEYS}
    return {"calls": len(calls),
            "cost_usd": round(max((float(c.get("total_cost_usd") or 0) for c in calls), default=0.0), 4),
            "minutes": round(sum(float(c.get("duration_ms") or 0) for c in calls) / 60000, 1),
            "turns": sum(int(c.get("num_turns") or 0) for c in calls), "tokens": tokens, "per_call": calls}


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

    try:
        uuid = str(uuid_module.UUID(args.match_uuid))
    except ValueError:
        print(f"refused: {args.match_uuid!r} is not a match uuid", file=sys.stderr)
        return 2
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
    plan_before = plan_usage()

    def report_usage() -> None:
        """Written for a failed run too: it cost the same."""
        if not call.usage:
            return
        used = usage_summary(call.usage)
        plan_after = plan_usage()
        if plan_before and plan_after:
            # Whole percents of the account's limits, and every other session on the account moves them too:
            # a single run is only measured when nothing else is running.
            used["plan_pct"] = {"before": plan_before, "after": plan_after}
            print("plan usage: " + ", ".join(f"{label} {plan_before[key]:g}% -> {plan_after[key]:g}%" for key, label
                                             in (("five_hour", "5h"), ("seven_day", "7d"))), file=sys.stderr)
        (run_dir / "usage.json").write_text(json.dumps(used, indent=1), encoding="utf-8")
        tokens = used["tokens"]
        print(f"usage: {used['calls']} model call(s), {used['turns']} turns, {used['minutes']} min, "
              f"${used['cost_usd']} at API prices; tokens: {tokens['input_tokens']} in, "
              f"{tokens['cache_creation_input_tokens']} cache write, {tokens['cache_read_input_tokens']} cache read, "
              f"{tokens['output_tokens']} out ({run_dir / 'usage.json'})", file=sys.stderr)

    try:
        payload = runner.generate(snap, slot, run_dir, book, voice, call,
                                  args.model or "claude-code (default model)")
    except (runner.RunError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        report_usage()
        print(f"run failed: {error}; the run's files are in {run_dir}", file=sys.stderr)
        return 1
    report_usage()
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
            version = coach.store_report(db, match_id, player_id, payload).version     # read while the session is open
    except Exception as error:      # noqa: BLE001 - whatever the store raises, the report is still on disk
        print(f"the report was written but not stored: {error}\nIt is in {run_dir / 'payload.json'}; store it with "
              f"scripts/coach_store_report.py once the cause is fixed.", file=sys.stderr)
        return 1
    print(f"published version {version} ({len(payload['points'])} points, {payload['dropped_points']} dropped): "
          f"/replays/{uuid}/coach?player={player_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

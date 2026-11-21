"""Writes and publishes one coaching report: snapshot, brief, headless Claude Code, checker, store.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_report.py <match uuid> "<player>" ["<player>" ...] [--dry-run | --prompt-only]

Every run uses MODEL at EFFORT (Opus 5.5, medium); nothing changes them.

Several players are coached one after another, never two at once, from one read of the match (the read takes
about a minute); each keeps its own run directory, and one player's failure leaves the others' runs and reports
alone. Exits 1 when any run failed.

The model runs in the snapshot's own directory under the system temp folder, with an allow-listed environment
(no DATABASE_URL) and one permitted command, ./q (the logged query tool). `--dry-run` prints the payload and stores
nothing. `--prompt-only` writes the exact first prompt to <run dir>/prompt.txt, prints its path and length, and
calls no model. Storing publishes the report on the site. Exits 2 when refused, 1 when the run failed.

Usage: each run prints what its model calls used (tokens in millions, turns, minutes), writes it to <run dir>/usage.json and appends it to ~/.valo-coach/usage.jsonl; a batch prints its total."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid as uuid_module
from datetime import datetime
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

TIMEOUT_S = 1800
# Every coach run is Opus 5.5 at medium effort: the owner's rule, not a default. Left unnamed, the CLI picks its
# own default, which switched to another model mid-batch on 2026-11-17. There is no option that changes these.
MODEL = "claude-opus-5-5"
EFFORT = "medium"
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


def claude_command(exe: str, session: str | None) -> list[str]:
    """Headless Claude Code with one tool, Bash, one pre-approved command, ./q, and none of the owner's own
    setup: no user settings, hooks, plugins, skills, CLAUDE.md or MCP servers. `--allowedTools` alone only
    pre-approves; `--tools` is what removes every other tool. Never add a flag that skips permission checks.
    The model and effort are always MODEL and EFFORT, never the CLI's default (the owner's rule)."""
    command = [exe, "-p", "--output-format", "json", "--restricted", "--safe-mode", "--strict-mcp-config",
               "--tools", "Bash", "--allowedTools", "Bash(./q:*)", "--model", MODEL, "--effort", EFFORT]
    if session:
        command += ["--resume", session]
    return command


def find_claude() -> str | None:
    return shutil.which("claude")


def failure_reason(done, usage: dict) -> str:
    """Why a `claude -p --output-format json` call exited non-zero. The CLI puts its reason (a rate or usage
    limit, an API error) in the JSON on stdout, not on stderr; a failed call's usage still counts."""
    parts = [done.stderr.strip()[:500]] if (done.stderr or "").strip() else []
    try:
        out = json.loads(done.stdout or "")
    except ValueError:
        out = None
    if isinstance(out, dict):
        add_usage(usage, out)
        for key in ("subtype", "api_error_status", "result"):
            if out.get(key) not in (None, ""):
                parts.append(f"{key}={str(out[key])[:300]}")
    elif (done.stdout or "").strip():
        parts.append(done.stdout.strip()[-500:])
    return "; ".join(parts) or "no output"


def claude_call(run_dir: Path):
    exe = find_claude()
    if exe is None:
        raise SystemExit("refused: the claude command is not on PATH")

    def call(text: str, session: str | None):
        done = subprocess.run(claude_command(exe, session), input=text, capture_output=True, text=True,
                              encoding="utf-8", cwd=Path(run_dir) / MODEL_DIR, env=child_env(dict(os.environ)), timeout=TIMEOUT_S)
        if done.returncode != 0:
            raise RuntimeError(f"claude exited {done.returncode}: {failure_reason(done, call.usage)}")
        out = json.loads(done.stdout)
        usage = out.get("modelUsage")
        if isinstance(usage, dict):
            call.models.update(str(name) for name in usage)
        add_usage(call.usage, out)
        denials = out.get("permission_denials")
        call.denials += len(denials) if isinstance(denials, list) else 0
        return str(out.get("result") or ""), out.get("session_id")

    call.models = set()
    call.denials = 0        # commands the model tried and was refused: a clean run has none
    call.usage = new_usage()
    return call


# --- usage: what the model runs cost, summed over every call of a run (the first prompt and each repair) ---

USAGE_TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
# Every run's usage is appended here, one JSON line each, outside the repo (public) and the temp folder (cleared).
USAGE_LOG = Path.home() / ".valo-coach" / "usage.jsonl"


def new_usage() -> dict:
    return {"calls": 0, "num_turns": 0, "duration_ms": 0, "cost_usd": 0.0, **{k: 0 for k in USAGE_TOKENS},
            "by_model": {}}


def _number(value) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def add_usage(total: dict, out: dict) -> dict:
    """Adds one `claude -p --output-format json` result's usage to `total`. Missing or odd fields count 0."""
    total["calls"] += 1
    total["num_turns"] += int(_number(out.get("num_turns")))
    total["duration_ms"] += int(_number(out.get("duration_ms")))
    total["cost_usd"] += float(_number(out.get("total_cost_usd")))
    usage = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    for key in USAGE_TOKENS:
        total[key] += int(_number(usage.get(key)))
    models = out.get("modelUsage") if isinstance(out.get("modelUsage"), dict) else {}
    for name, row in models.items():
        if not isinstance(row, dict):
            continue
        mine = total["by_model"].setdefault(str(name), {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                                                       "cache_read_input_tokens": 0,
                                                       "cache_creation_input_tokens": 0})
        mine["cost_usd"] += float(_number(row.get("costUSD")))
        for key, field in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                           ("cache_read_input_tokens", "cacheReadInputTokens"),
                           ("cache_creation_input_tokens", "cacheCreationInputTokens")):
            mine[key] += int(_number(row.get(field)))
    return total


def merge_usage(total: dict, part: dict) -> dict:
    for key in ("calls", "num_turns", "duration_ms", "cost_usd", *USAGE_TOKENS):
        total[key] += part[key]
    for name, row in part["by_model"].items():
        mine = total["by_model"].setdefault(name, {k: 0 for k in row})
        for key, value in row.items():
            mine[key] = mine.get(key, 0) + value
    return total


def usage_line(usage: dict) -> str:
    """Tokens in millions (M). Cache reads are cheap against plan limits; fresh input, cache writes and output
    are what weigh."""
    m = lambda n: f"{n / 1e6:.2f}M"
    total = sum(usage[k] for k in USAGE_TOKENS)
    return (f"{m(total)} tokens: {m(usage['input_tokens'])} input, {m(usage['cache_creation_input_tokens'])} cache "
            f"writes, {m(usage['cache_read_input_tokens'])} cache reads, {m(usage['output_tokens'])} output; "
            f"{usage['calls']} call(s), {usage['num_turns']} turns, {usage['duration_ms'] / 60000:.1f} min")


def record_usage(run_dir: Path, entry: dict, log: Path | None = None) -> None:
    """Writes the run's usage to <run dir>/usage.json and appends it to the usage log. Best-effort: a cost
    record that cannot be written never fails the run."""
    try:
        (Path(run_dir) / "usage.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
    except OSError as error:
        print(f"usage not written to the run directory: {error}", file=sys.stderr)
    log = Path(log or USAGE_LOG)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as error:
        print(f"usage not appended to {log}: {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("players", nargs="+", metavar="player")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--prompt-only", action="store_true",
                      help="write the first prompt to <run dir>/prompt.txt and stop; no model is called")
    args = parser.parse_args()
    from app.coach import playbook, snapshot
    from app.db import SessionLocal
    from scripts import _admin_client
    from scripts.coach_store_report import on_roster, resolve

    try:
        uuid = str(uuid_module.UUID(args.match_uuid))
    except ValueError:
        print(f"refused: {args.match_uuid!r} is not a match uuid", file=sys.stderr)
        return 2
    off = [name for name in args.players if not on_roster(name)]
    if off:     # before any database, model or run-directory work: the whole batch, like a wrong name
        print(f"refused: not on the roster (scripts/tracked_players.json): {', '.join(off)}", file=sys.stderr)
        return 2
    if not args.prompt_only and find_claude() is None:      # before any database or run-directory work
        print("refused: the claude command is not on PATH", file=sys.stderr)
        return 2
    # Every player is resolved, and the match read once, before any model runs: a wrong name refuses the
    # whole batch at once, not after the earlier players' runs. What is wrong for one player alone (no slot or
    # team in the replay) refuses only that player's run.
    with SessionLocal() as db:
        try:
            book = playbook.load()
            resolved = [(name, *resolve(db, uuid, name)) for name in args.players]
            seen = set()
            for name, _, player_id in resolved:
                if player_id in seen:
                    raise ValueError(f"player {name!r} was requested more than once")
                seen.add(player_id)
            ask = (lambda: _admin_client.request_util_build(_admin_client.DEFAULT_SITE)) \
                if os.environ.get("REPLAY_ADMIN_TOKEN", "").strip() else None
            data = snapshot.read(db, uuid, ask=ask)
        except (ValueError, snapshot.SnapshotError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
    if len(resolved) == 1:
        return run_player(args, uuid, book, *resolved[0], data)
    print(f"coaching {len(resolved)} players, one at a time, from one read of the match: "
          f"{', '.join(name for name, *_ in resolved)}", flush=True)
    failed = []
    args.usage_total = new_usage()      # run_player adds each player's usage
    for at, entry in enumerate(resolved, start=1):
        name = entry[0]
        print(f"\n== {at} of {len(resolved)}: {name} ==", flush=True)
        try:
            code = run_player(args, uuid, book, *entry, data)
        except SystemExit as stop:      # a helper's refusal: that player's failure, not the batch's
            print(stop, file=sys.stderr)
            code = stop.code if isinstance(stop.code, int) else 1
        except Exception:      # noqa: BLE001 - one player's crash must not cost the others their runs
            traceback.print_exc()
            code = 1
        if code != 0:
            print(f"== {name}: FAILED (exit {code}) ==", flush=True)
            failed.append(name)
    print(f"\n{len(resolved) - len(failed)} of {len(resolved)} runs succeeded"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    if args.usage_total["calls"]:
        print(f"usage, all {len(resolved)} players: {usage_line(args.usage_total)}")
    return 1 if failed else 0


def run_player(args, uuid: str, book, player: str, match_id: int, player_id: int, match: dict) -> int:
    """One player's run from the match as that player's run sees it (enemy names hidden). `match` is the shared
    read, never changed: hiding builds new dicts for this player."""
    from app.coach import prompt, runner, snapshot
    from app.db import SessionLocal
    from app.services import coach

    try:
        data = snapshot.hide_enemy_names(match, player_id)
    except snapshot.SnapshotError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2

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
        slot = snap.slot_of(player)
    except snapshot.SnapshotError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.prompt_only:
        built, _ = runner._brief_with_flag_ids(snap, slot, run_dir)
        text = prompt.run_prompt(book, built)
        path = run_dir / "prompt.txt"
        path.write_text(text, encoding="utf-8")
        print(f"{path}\n{len(text)} characters")
        return 0
    call = claude_call(run_dir)
    usage_entry = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "match": uuid,
                   "player_id": player_id, "player": player, "model": f"{MODEL} ({EFFORT})",
                   "outcome": "failed", "usage": call.usage}
    try:
        payload = runner.generate(snap, slot, run_dir, book, call, MODEL)
        usage_entry["outcome"] = "generated"
    except (runner.RunError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"run failed: {error}; the run's files are in {run_dir}", file=sys.stderr)
        return 1
    finally:      # a failed run's calls cost the same: always recorded
        if call.usage["calls"]:
            record_usage(run_dir, usage_entry)
            print(f"usage: {usage_line(call.usage)}", flush=True)
            if isinstance(getattr(args, "usage_total", None), dict):
                merge_usage(args.usage_total, call.usage)
    if call.denials:
        print(f"the model tried {call.denials} command(s) it is not allowed and was refused", file=sys.stderr)
    if call.models:
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

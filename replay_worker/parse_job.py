"""Parse one `.vrf` and condense it (Stage 3, W-c), for the worker's local path and the Workflow `parse` task
(docs/superpowers/specs/2026-10-14-render-workflows-design.md). Standard library and app/replays only."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

from app.replays.condense import condense_export_dir  # noqa: E402

REASON_TIMEOUT = "parse timed out"
REASON_PARSE = "parse failed"
DETAIL_MAX_CHARS = 300   # the parser's last words on a failed parse, kept as the job's `detail`


def parser_detail(log: Path) -> str | None:
    """The last few lines the parser wrote, without stack frames, for a failed parse: None when it wrote nothing."""
    try:
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 8192))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith(("at ", "---"))]
    detail = " | ".join(lines[-3:])
    return detail[-DETAIL_MAX_CHARS:] or None


def kill_tree(process: subprocess.Popen) -> None:
    """Kill a child and everything it started; every wait is bounded. On Linux the child leads its own
    process group (setsid), so the group is killed even when the leader has already exited."""
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
                               check=False, timeout=10)
            except subprocess.TimeoutExpired:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def limit_memory(cap_mb: int):
    """A preexec hook that caps the parser's address space (Linux; `resource` isn't on Windows)."""
    def apply() -> None:
        os.setsid()
        if cap_mb:
            import resource
            limit = cap_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return apply
_limit_memory = limit_memory


class ParseFailed(Exception):
    def __init__(self, reason: str, detail: str | None = None, seconds: float | None = None):
        super().__init__(reason)
        self.reason, self.detail, self.seconds = reason, detail, seconds


def parse_file(vrf: Path, folder: Path, sha256: str, *, parser_cmd: list[str], timeout_s: float,
               memory_cap_mb: int, parser_build: Path | None) -> tuple[dict, float]:
    out = folder / "export"
    command = [part.replace("{vrf}", str(vrf)).replace("{out}", str(out)) for part in parser_cmd]
    started = time.perf_counter()
    log = folder / "parser.log"
    try:
        with log.open("wb") as output:
            kwargs = {"stdout": output, "stderr": subprocess.STDOUT, "cwd": str(folder),
                      "env": {**os.environ, "VALO_MAP_AUDIT_RAW": "1"}}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["preexec_fn"] = limit_memory(memory_cap_mb)
            process = subprocess.Popen(command, **kwargs)
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                kill_tree(process)
                raise ParseFailed(REASON_TIMEOUT)
        seconds = round(time.perf_counter() - started, 1)
        if code != 0 or not (out / "manifest.json").exists():
            raise ParseFailed(REASON_PARSE, parser_detail(log), seconds)
    finally:
        log.unlink(missing_ok=True)
    build = json.loads(parser_build.read_text(encoding="utf-8-sig")) if parser_build is not None else None
    try:
        # The match UUID comes from the file's own header, never its name (check_file_name=False).
        replay = condense_export_dir(out, source_sha256=sha256, build=build, vrf_path=vrf, check_file_name=False)
    finally:
        shutil.rmtree(out, ignore_errors=True)     # the export is ~65x the file; the result is all we need
    return {
        "match_uuid": replay.match_uuid, "map_name": replay.map_name, "game_branch": replay.game_branch,
        "source_sha256": replay.source_sha256, "recipe": replay.recipe, "hz": replay.hz,
        "round_count": replay.round_count,
        "rounds": {str(n): base64.b64encode(data).decode("ascii") for n, data in replay.encoded_rounds().items()},
        "players": replay.players, "link_inputs": replay.link_inputs,
        # The web service stores this (app/replays/store.py): dropped_final_round goes into
        # link_inputs, and the rest is a small summary for the job page.
        "report": {k: replay.report.get(k) for k in ("rounds", "hz", "kills", "sizes", "lifecycle", "sides",
                                                      "dropped_final_round", "extras")},
    }, seconds

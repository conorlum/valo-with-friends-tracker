"""One round's map control, run as a child process by the replay worker (docs/map-control-worker-plan.md).

    <control venv python> -m replay_worker.control_job  < task.json  > result.json

The task (stdin): `{"key", "map", "blob" (base64 of the stored gzip blob), "link": {"sides", "db_deaths"}}`
and optionally `"height"` (the digest of the map's heights, already pushed to this worker's cache).
The result (stdout): app/control/task.py's result with the bytes in base64, plus the engine's
CONTROL_REVISION and DATA_VERSION and the hash of the game figures it read, so the web app can drop a result
from another deploy. A task with a `gaps` block also returns the round's timing gaps; its tick cache file is
deleted when the task ends.

The server (replay_worker/server.py) never imports this module or the engine: it runs this file with the
control venv's interpreter, which has numpy, scipy and Pillow. This file itself imports only app modules,
inside `main`.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
STALE_CACHE_S = 6 * 3600        # a tick cache file older than this was left by a killed child


def _drop_tick_cache(task: dict) -> None:
    """The worker keeps no tick cache: a full run reads back the file it has just written and nothing reads
    it again, and the disk's space accounting (replay_worker/archive.py) doesn't know about it. This round's
    file goes, and any file old enough to be a killed child's."""
    job = task.get("gaps")
    if not job:
        return
    from app.control import geometry

    folder = Path(geometry.cache_dir()) / "gaps"
    cutoff = time.time() - STALE_CACHE_S
    try:
        files = list(folder.iterdir())
    except OSError:
        return
    key = job.get("engine_key")
    mine = None if key is None else f"{job['replay_id']}-r{job['round']}-{key}.ticks.pkl.gz"
    for path in files:
        try:
            owned = mine is not None and path.name in (mine, mine + ".tmp")
            stale_tick = ".ticks.pkl.gz" in path.name and path.stat().st_mtime < cutoff
            if owned or stale_tick:
                path.unlink()
        except OSError:
            pass


def run(task: dict) -> dict:
    if str(WEBAPP) not in sys.path:
        sys.path.insert(0, str(WEBAPP))
    from app.control.task import compute_task
    from app.replays import control_format as cf

    # Worker caches are never reused by another invocation. Distinct task keys for one round
    # can overlap during a deploy or relink, so isolate each child's temporary cache.
    if (task.get("gaps") or {}).get("engine_key"):
        task = {**task, "gaps": {**task["gaps"],
                                 "engine_key": f"{task['gaps']['engine_key']}-{uuid.uuid4().hex}"}}
    try:
        result = compute_task({**task, "blob": base64.b64decode(task["blob"])})
    finally:
        _drop_tick_cache(task)
    for name in ("data", "summary"):
        if result.get(name) is not None:
            result[name] = base64.b64encode(result[name]).decode("ascii")
    # What the web app checks before it stores: the engine's revisions, and the game figures it read
    # (app/control/hearing.json and utility.json), which are in both fingerprints.
    result.update({"revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION, "figures": cf.figures_hash()})
    return result


def main() -> int:
    task = json.loads(sys.stdin.buffer.read())
    try:
        result = run(task)
    except Exception as error:  # noqa: BLE001 - even an import failure answers in JSON
        result = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}",
                  "key": task.get("key")}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

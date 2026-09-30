"""One round's map control, run as a child process by the replay worker (docs/map-control-worker-plan.md).

    <control venv python> -m replay_worker.control_job  < task.json  > result.json

The task (stdin): `{"key", "map", "blob" (base64 of the stored gzip blob), "link": {"sides", "db_deaths"}}`.
The result (stdout): app/control/task.py's result with the bytes in base64, plus the engine's
CONTROL_REVISION and DATA_VERSION, so the web app can drop a result from another deploy.

The server (replay_worker/server.py) never imports this module or the engine: it runs this file with the
control venv's interpreter, which has numpy, scipy and Pillow. This file itself imports only app modules,
inside `main`.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"


def run(task: dict) -> dict:
    if str(WEBAPP) not in sys.path:
        sys.path.insert(0, str(WEBAPP))
    from app.control.task import compute_task
    from app.replays import control_format as cf

    result = compute_task({**task, "blob": base64.b64decode(task["blob"])})
    for name in ("data", "summary"):
        if result.get(name) is not None:
            result[name] = base64.b64encode(result[name]).decode("ascii")
    result.update({"revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION})
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

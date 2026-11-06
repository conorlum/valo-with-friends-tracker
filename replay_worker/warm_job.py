"""A map's visibility cache, built (or found) by itself before any round of its warm identity runs: the replay
worker's warm build (docs/superpowers/plans/2026-11-05-replay-throughput.md). Same task JSON as control_job, same
control venv interpreter; the server never imports this module.

    <control venv python> -m replay_worker.warm_job  < task.json  > result.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"


def main() -> int:
    task = json.loads(sys.stdin.buffer.read())
    try:
        if str(WEBAPP) not in sys.path:
            sys.path.insert(0, str(WEBAPP))
        from app.control.task import warm_task

        result = warm_task(task)
    except Exception as error:  # noqa: BLE001 - even an import failure answers in JSON
        result = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}",
                  "key": task.get("key")}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

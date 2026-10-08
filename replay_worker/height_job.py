"""One map's height build, run as a child process by the replay worker
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 2).

    <control venv python> -m replay_worker.height_job  < task.json  > result.json

The task (stdin): `{"key", "map", "dir", "manifest", "previous"?}`. `dir` holds the map's stored rounds as the
server spooled them, `<match>/<n>.json.gz`; `manifest` is the web app's input manifest for this build
(app/replays/height_inputs.py); `previous` is the digest of the asset being replaced, read from this worker's
control cache when it is there (for the report's comparison only).

Before it builds, the child recomputes the manifest from its own files (its rule revisions, its masks, its
check set) with the replays the web app named, and refuses when the digest differs: the two services deploy
separately, and a build under other rules or masks is not the build that was asked for. After it builds, the
rounds it read must be the manifest's, match by match. Either refusal is `error_kind: "inputs"`.

The result (stdout): app/control/height_job.py's, with the asset's bytes in base64, `inputs_sha` (the
manifest's digest, as this worker computed it) and `rounds`. Like control_job.py, the server never imports
this module or the engine, and every app import is inside `run`.
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
    try:
        from app.control import geometry, height_job
        from app.replays import height_inputs as hi

        theirs = task.get("manifest")
        name = str(task.get("map"))
        if not isinstance(theirs, dict) or theirs.get("map") != name or not isinstance(theirs.get("replays"), list):
            return {"status": "failed", "error_kind": "inputs", "error": "the task has no manifest for this map",
                    "key": task.get("key")}
        mine = hi.manifest(name, theirs["replays"], hi.geometry_identity(name) or {}, hi.must_block_sha())
        if hi.digest(mine) != hi.digest(theirs):
            differ = sorted(k for k in mine if mine[k] != theirs.get(k))
            return {"status": "failed", "error_kind": "inputs", "key": task.get("key"),
                    "error": f"this worker's build inputs are not the web app's: {', '.join(differ)} differ"}
        folder = Path(task["dir"])
        if not folder.is_dir():
            return {"status": "failed", "error_kind": "inputs", "error": f"no rounds at {folder}",
                    "key": task.get("key")}
        source = height_job.BlobDir(folder, name)
        previous = geometry.height_cache_path(name, task["previous"]) if task.get("previous") else None
        result = height_job.run(name, source, previous=previous)
        expected = {row[0]: int(row[3]) for row in theirs["replays"]}
        if source.per_match() != expected:
            return {"status": "failed", "error_kind": "inputs", "key": task.get("key"),
                    "error": f"the rounds read ({source.per_match()}) are not the manifest's ({expected})"}
        result["asset"] = base64.b64encode(result["asset"]).decode("ascii")
        result["rounds"] = sum(expected.values())
        result["inputs_sha"] = hi.digest(mine)
    except Exception as error:  # noqa: BLE001 - the machine's: memory, a broken blob, an import
        result = {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}"[:1500]}
    result["key"] = task.get("key")
    return result


def main() -> int:
    task = json.loads(sys.stdin.buffer.read())
    sys.stdout.write(json.dumps(run(task)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

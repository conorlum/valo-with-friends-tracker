"""Every death of a live replay's rounds at several statue windows (lost control at a death; 0 is the instant
rule it replaced), from the friends site's public responses only: no database, no writes but `<out.json>`.

    .\\.venv\\Scripts\\python.exe scripts\\compare_death_windows.py <match uuid> <out.json> <workers> 5 8 10

One task per (window, round), `workers` at a time; `<out.json>` is rewritten after each, so a partial run can be
read. It names real players: keep it local.
"""

from __future__ import annotations

import functools
import json
import multiprocessing
import sys
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WEBAPP_ROOT), str(WEBAPP_ROOT / "scripts")]

_compute_round = None


def _init():
    global _compute_round
    from app.control import engine
    _compute_round = engine.compute_round


def work(task: dict) -> dict:
    from app.control import engine
    from app.control.task import compute_task
    from app.replays import control_format as cf

    engine.compute_round = functools.partial(_compute_round, death_window_s=task.pop("window"))
    started = time.time()
    r = compute_task(task)
    out = {"round": task["key"], "status": r["status"], "error": r.get("error", "")[:300],
           "seconds": time.time() - started}
    if r["status"] == "ok":
        s = cf.unpack_summary(r["summary"])
        out["deaths"] = [{"slot": int(slot), **d} for slot, p in s["players"].items() for d in p.get("deaths") or []]
    return out


def main() -> int:
    from preview_control_live import SITE, fetch, link_for, page_data
    from app.control import geometry

    uuid, out_path, workers = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    windows = [float(w) for w in sys.argv[4:]]
    ctx = page_data(uuid)
    rounds = sorted(int(n) for n in ctx["rounds"])
    blobs = {n: fetch(f"{SITE}/replays/{uuid}/{n}.json") for n in rounds}
    # the visibility bitsets once, before the workers (as preview_control_live does)
    geometry.visibility(geometry.load_geometry(ctx["match"]["map"]))
    tasks = [{"key": n, "map": ctx["match"]["map"], "blob": blobs[n], "link": link_for(ctx, n), "window": w}
             for w in windows for n in rounds]
    names = {s: p["name"] for s, p in ctx["players"].items()}
    results: dict[str, dict] = {str(w): {} for w in windows}
    with multiprocessing.Pool(workers, initializer=_init) as pool:
        for task, r in zip(tasks, pool.imap(work, [dict(t) for t in tasks])):
            results[str(task["window"])][r["round"]] = r
            print(f"w={task['window']} r{r['round']}: {r['status']} {r['seconds']:.0f}s {r['error']}", flush=True)
            out_path.write_text(json.dumps({"names": names, "results": results}), encoding="utf-8")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

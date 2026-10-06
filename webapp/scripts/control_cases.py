"""Checks the user's judged real-round cases (tests/fixtures/control/unknown_cases.json) against this checkout's
engine: each case's round is recomputed from the friends site's public responses (as
scripts/preview_control_live.py does: no database, no writes) and read at the tick at or before the case's
time. 'unknown' cases fail when any listed cell has been cleared from the side group's unknown, 'clear' cases
when any is still in it, and 'state' cases when any listed cell's state isn't one of the case's `states`. Run it
after an engine change and before a recompute.

    .\\.venv\\Scripts\\python.exe scripts\\control_cases.py                 # every case
    .\\.venv\\Scripts\\python.exe scripts\\control_cases.py --id <case id>   # one (repeatable)

tests/replays/test_control_cases.py runs the checking on a toy round and checks the case file's shape.
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import os
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

CASES = WEBAPP_ROOT / "tests" / "fixtures" / "control" / "unknown_cases.json"


def check_case(case: dict, data: bytes) -> dict:
    """{"passes", "wrong" (the listed cells that aren't as expected), "t_checked"} for one case against a
    round's stored control bytes (gzipped, as compute_task and control.bin give them). 'unknown'/'clear'
    cases read the side group's unknown; 'state' cases read the cell's state, which must be one of the case's
    `states` (control_format.STATE_NAMES). A cell off the round's walkable ground is wrong whatever is
    expected: it can't be checked."""
    from app.control.geometry import CELL, GRID
    from app.replays import control_format as cf

    header, streams = cf.unpack_data(data if data[:2] == b"\x1f\x8b" else gzip.compress(data))
    times = [tk / header["hz"] for tk in header["ticks"]]
    i = max([k for k, t in enumerate(times) if t <= case["t"] + 1e-9], default=0)
    cells = header["cells"]
    index = {c: k for k, c in enumerate(cf.walk_bitmap(header))}
    if case["expect"] == "state":
        frame = cf.decode_states(streams["states"], i + 1, cells)[i]
        allowed = set(case["states"])
        ok = lambda k: cf.STATE_NAMES[frame[k]] in allowed  # noqa: E731
    else:
        name = f"unknown_{case['side'].lower()}"
        if name not in streams:
            raise SystemExit(f"{case['id']}: the round has no {name} stream (computed before the unknown?)")
        unknown = cf.decode_masks(streams[name], i + 1, cells, [c[0] for c in header["unknown_checkpoints"]],
                                  slots=1)[i][0]
        want = case["expect"] == "unknown"
        ok = lambda k: bool(unknown[k]) == want  # noqa: E731
    wrong = []
    for x, y in case["cells"]:
        k = index.get(int(y // CELL) * GRID + int(x // CELL))
        if k is None or not ok(k):
            wrong.append([x, y])
    return {"passes": not wrong, "wrong": wrong, "t_checked": times[i]}


def main(argv: list[str] | None = None) -> int:
    from preview_control_live import SITE, fetch, link_for, page_data

    from app.control import geometry
    from app.control.task import compute_task

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", action="append", help="check only this case (repeatable)")
    args = parser.parse_args(argv)
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.id:
        unknown_ids = set(args.id) - {c["id"] for c in cases}
        if unknown_ids:
            raise SystemExit(f"no such case: {sorted(unknown_ids)}")
        cases = [c for c in cases if c["id"] in args.id]
    rounds = sorted({(c["match"], c["round"]) for c in cases})
    tasks, pages = [], {}
    for match, n in rounds:
        if match not in pages:
            pages[match] = page_data(match)
        ctx = pages[match]
        tasks.append({"key": (match, n), "map": ctx["match"]["map"], "blob": fetch(f"{SITE}/replays/{match}/{n}.json"),
                      "link": link_for(ctx, n)})
    for m in sorted({t["map"] for t in tasks}):      # the visibility bitsets once, before the workers
        geometry.visibility(geometry.load_geometry(m))
    with multiprocessing.Pool(min(len(tasks), os.cpu_count() or 1)) as pool:
        results = {r["key"]: r for r in pool.imap_unordered(compute_task, tasks)}
    failed = 0
    for c in cases:
        r = results[(c["match"], c["round"])]
        if r["status"] != "ok":
            print(f"ERROR {c['id']}: round {c['round']} didn't compute: {r.get('error', '')[:400]}")
            failed += 1
            continue
        got = check_case(c, r["data"])
        verdict = "PASS" if got["passes"] else "FAIL"
        want = "/".join(c["states"]) if c["expect"] == "state" else c["expect"]
        detail = "" if got["passes"] else f"; not {want}: {got['wrong']}"
        print(f"{verdict} {c['id']} (round {c['round']} at {got['t_checked']:.2f} s, side {c['side']}, "
              f"{len(c['cells'])} cells must be {want}{detail})")
        failed += not got["passes"]
    print(f"{len(cases) - failed}/{len(cases)} cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

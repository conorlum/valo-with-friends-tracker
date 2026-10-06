"""Checks the user's judged real-round cases (tests/fixtures/control/unknown_cases.json) against this checkout's
engine: each case's round is recomputed from the friends site's public responses (as
scripts/preview_control_live.py does: no database, no writes) and read at the tick at or before the case's
time. 'unknown' cases fail when any listed cell has been cleared from the side group's unknown, 'clear' cases
when any is still in it, and 'state' cases when any listed cell's state isn't one of the case's `states`. Run it
after an engine change and before a recompute.

    .\\.venv\\Scripts\\python.exe scripts\\control_cases.py                 # every case
    .\\.venv\\Scripts\\python.exe scripts\\control_cases.py --id <case id>   # one (repeatable)
    .\\.venv\\Scripts\\python.exe scripts\\control_cases.py --inputs <manifest.json> [--cases <cases.json>]

`--inputs` is the local mode, for cases that judge newly extracted utility: the public blob of such a round
was condensed before the extraction existed, so it can't show it. The manifest names local round blobs:

    {"rounds": [{"match": <uuid>, "round": <n>, "map": <name>, "blob": <path, relative to the manifest>,
                 "blob_sha256": <hex>, "source_sha256": <the .vrf's hex>, "condense_revision": <int>,
                 "link": {"sides": {<slot>: "attack" | "defense"}, "db_deaths": [[slot, t], ...]},
                 "requires": [{"k": "ability", "code": "Cable", "name": "^E_", "min": 1}, ...]}]}

Nothing is fetched in this mode. A blob that is missing, whose bytes, round, map or condenser revision differ
from the manifest, or that lacks a required util row, stops the run (`InputsError`): it never falls back to the
public blob. Only the cases whose (match, round) the manifest lists are checked; a case file given with
`--cases` replaces the committed one. A case marked `"local": true` is skipped by the live mode.

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


class InputsError(ValueError):
    """A local-inputs manifest, or a blob it names, isn't what it says: the run stops."""


def required_missing(blob: dict, requires: list[dict]) -> list[dict]:
    """The `requires` entries the blob's util doesn't meet. An entry matches a util row when its `k` is equal,
    and its `code`, `name`, `ability` and `status` (regular expressions, searched) all match the row's; at
    least `min` (default 1) rows must match."""
    import re

    missing = []
    for need in requires or []:
        count = 0
        for row in blob.get("util") or []:
            if need.get("k") is not None and row.get("k") != need["k"]:
                continue
            if all(key not in need or re.search(need[key], str(row.get(key) or ""))
                   for key in ("code", "name", "ability", "status", "kind")):
                count += 1
        if count < int(need.get("min", 1)):
            missing.append(need)
    return missing


def load_inputs(manifest_path: Path) -> dict:
    """{(match, round): task} from a local-inputs manifest (see the module's docstring), each task in the
    shape the live mode builds. Raises InputsError on anything missing or mismatched. No network."""
    import hashlib

    from app.replays import format as fmt

    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        rows = manifest["rounds"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InputsError(f"unreadable manifest {manifest_path}: {error}") from error
    tasks = {}
    for row in rows:
        try:
            key = (str(row["match"]), int(row["round"]))
            path = Path(manifest_path).parent / row["blob"]
            want_sha, source, revision = row["blob_sha256"], row["source_sha256"], int(row["condense_revision"])
            link, map_name = row["link"], str(row["map"])
            sides = {str(s): side for s, side in link["sides"].items()}
            deaths = [[int(s), float(t)] for s, t in link.get("db_deaths", [])]
        except (KeyError, TypeError, ValueError) as error:
            raise InputsError(f"manifest row {row.get('match')!r}/{row.get('round')!r}: missing or bad {error}") from error
        where = f"{key[0]} round {key[1]}"
        if key in tasks:
            raise InputsError(f"{where}: listed twice")
        if len(str(source)) != 64:
            raise InputsError(f"{where}: source_sha256 is not a sha256")
        if revision != fmt.CONDENSE_REVISION:
            raise InputsError(f"{where}: condensed at revision {revision}, this checkout is {fmt.CONDENSE_REVISION}")
        if not path.is_file():
            raise InputsError(f"{where}: no blob at {path}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != want_sha:
            raise InputsError(f"{where}: {path.name} is not the blob the manifest names (sha256 differs)")
        try:
            blob = fmt.decode_blob(data)
        except (OSError, ValueError) as error:
            raise InputsError(f"{where}: {path.name} doesn't decode: {error}") from error
        if blob.get("round") != key[1] or blob.get("map") != map_name:
            raise InputsError(f"{where}: the blob is round {blob.get('round')!r} on {blob.get('map')!r}")
        slots = {str(p["slot"]) for p in blob.get("players") or []}
        unsided = sorted(s for s in slots if s not in sides
                         and next(p for p in blob["players"] if str(p["slot"]) == s).get("side") is None)
        if unsided or any(side not in ("attack", "defense") for side in sides.values()):
            raise InputsError(f"{where}: the link's sides don't cover slots {unsided} or name another side")
        absent = required_missing(blob, row.get("requires") or [])
        if absent:
            raise InputsError(f"{where}: the blob lacks required util rows: {absent}")
        tasks[key] = {"key": key, "map": map_name, "blob": data, "link": {"sides": sides, "db_deaths": deaths}}
    return tasks


def main(argv: list[str] | None = None) -> int:
    from app.control import geometry
    from app.control.task import compute_task

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", action="append", help="check only this case (repeatable)")
    parser.add_argument("--inputs", type=Path, help="a local-inputs manifest: check its rounds' cases, fetch nothing")
    parser.add_argument("--cases", type=Path, default=CASES, help="the case file (default: the committed one)")
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    if args.id:
        unknown_ids = set(args.id) - {c["id"] for c in cases}
        if unknown_ids:
            raise SystemExit(f"no such case: {sorted(unknown_ids)}")
        cases = [c for c in cases if c["id"] in args.id]
    if args.inputs:
        try:
            local = load_inputs(args.inputs)
        except InputsError as error:
            raise SystemExit(f"INPUTS {error}") from error
        cases = [c for c in cases if (c["match"], c["round"]) in local]
        if not cases:
            raise SystemExit("INPUTS no case names a round of the manifest")
        tasks = [local[key] for key in sorted({(c["match"], c["round"]) for c in cases})]
    else:
        from preview_control_live import SITE, fetch, link_for, page_data

        cases = [c for c in cases if not c.get("local")]
        rounds = sorted({(c["match"], c["round"]) for c in cases})
        tasks, pages = [], {}
        for match, n in rounds:
            if match not in pages:
                pages[match] = page_data(match)
            ctx = pages[match]
            tasks.append({"key": (match, n), "map": ctx["match"]["map"],
                          "blob": fetch(f"{SITE}/replays/{match}/{n}.json"), "link": link_for(ctx, n)})
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

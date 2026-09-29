"""Stage 0a (docs/replay-map-control-plan.md, Q61/Q71): does placed utility outlive its owner?
And how often is a replay's side null?

Feasibility tooling, not product code. Reads condensed bundles (`bundle.json`, the rounds as
blobs) and, for every ability object with an owner who died while it was out, measures how long
the object stayed after that death. Objects that end within GRACE_S of the death "die with the
owner"; longer ones are listed.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\utility_after_death.py <out_json> <bundle.json>...
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

GRACE_S = 0.5


def main(out: Path, bundles: list[Path]) -> None:
    per_object = defaultdict(lambda: {"died_with": 0, "outlived": 0, "outlived_s": [], "maps": set()})
    sides = {"players": 0, "null": 0, "rounds": 0}
    for path in bundles:
        bundle = json.loads(path.read_text())
        game_map = bundle["match"]["map"]
        for blob in bundle["rounds"].values():
            sides["rounds"] += 1
            for p in blob["players"]:
                sides["players"] += 1
                sides["null"] += p.get("side") is None
            deaths = {}
            for slot, lives in blob["alive"].items():
                for start, end, why in lives:
                    if end is not None and why == "kill":
                        deaths.setdefault(int(slot), []).append(end)
            for a in (blob.get("extras") or {}).get("abilities", []):
                slot = a.get("slot")
                if slot is None or slot not in deaths:
                    continue
                t0, t1 = a.get("t0"), a.get("t1")
                died = [d for d in deaths[slot] if t0 <= d]
                if not died:
                    continue
                d = min(died)
                if t1 is not None and t1 < d:
                    continue  # gone before the owner died
                key = f"{a.get('agent')}: {a.get('code')}.{a.get('name')} ({a.get('kind')})"
                entry = per_object[key]
                entry["maps"].add(game_map)
                lasted = (t1 if t1 is not None else blob["t_end"]) - d
                if lasted <= GRACE_S:
                    entry["died_with"] += 1
                else:
                    entry["outlived"] += 1
                    entry["outlived_s"].append(round(lasted, 1))
    rows = []
    for key, e in sorted(per_object.items()):
        rows.append({"object": key, "died_with_owner": e["died_with"], "outlived_owner": e["outlived"],
                     "outlived_seconds": sorted(e["outlived_s"])[:12], "maps": sorted(e["maps"])})
    out.write_text(json.dumps({"grace_s": GRACE_S, "objects": rows, "sides": sides}, indent=1))
    for r in rows:
        print(f"{r['object']:70s} died-with {r['died_with_owner']:3d}  outlived {r['outlived_owner']:3d}  "
              f"{r['outlived_seconds']}")
    print("sides:", sides)


if __name__ == "__main__":
    main(Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]])

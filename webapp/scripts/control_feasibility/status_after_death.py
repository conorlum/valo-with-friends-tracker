"""Stage 0a (docs/replay-map-control-plan.md, Q61/Q71): does placed utility still *work* after its
owner dies? An object that lingers in the replay may be inert; one that puts a status on an enemy
after its owner died is still working.

Feasibility tooling, not product code. Condenses each export with the current condenser (extras
included, so statuses are present), saves the round blobs to `<out>/blobs/<uuid>.json` for Stage
0b, and lists every status or reveal applied after its applier's death, by object.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\status_after_death.py <out_dir> <export_dir>...
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.replays.condense import condense_export_dir  # noqa: E402

VRF_ARCHIVE = Path.home() / "ValorantReplayArchive"
# A status this soon after the owner's death may be from something already in flight.
IN_FLIGHT_S = 1.5


def main(out: Path, export_dirs: list[Path]) -> None:
    (out / "blobs").mkdir(parents=True, exist_ok=True)
    after = defaultdict(list)
    before = defaultdict(int)
    for export_dir in export_dirs:
        try:
            replay = condense_export_dir(export_dir, source_sha256=None, vrf_path=VRF_ARCHIVE / f"{export_dir.name}.vrf",
                                         check_file_name=False, allow_blocking=True)
        except Exception as error:  # noqa: BLE001 - report and move on
            print(f"{export_dir.name}: FAILED {type(error).__name__}: {error}", flush=True)
            continue
        (out / "blobs" / f"{export_dir.name}.json").write_text(json.dumps(
            {"uuid": export_dir.name, "map": replay.map_name, "rounds": {str(n): b for n, b in replay.rounds.items()}}))
        for n, blob in replay.rounds.items():
            agent = {p["slot"]: p["agent"] for p in blob["players"]}
            death = {}
            for slot, lives in blob["alive"].items():
                ends = [end for _, end, why in lives if end is not None and why == "kill"]
                if ends:
                    death[int(slot)] = min(ends)
            for u in blob.get("util") or []:
                if u.get("k") not in ("status", "reveal") or u.get("by") is None:
                    continue
                key = f"{agent.get(u['by'])}: {u.get('code')}.{u.get('name')} -> {u.get('status', 'revealed')}"
                d = death.get(u["by"])
                if d is not None and u["t"] > d + IN_FLIGHT_S:
                    after[key].append({"replay": export_dir.name[:8], "round": n, "s_after_death": round(u["t"] - d, 1)})
                else:
                    before[key] += 1
        print(f"{export_dir.name} {replay.map_name}: condensed", flush=True)
    rows = [{"object": k, "while_owner_alive": before.get(k, 0), "after_owner_died": len(after.get(k, [])),
             "examples": after.get(k, [])[:6]} for k in sorted(set(before) | set(after))]
    (out / "status_after_death.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        if r["after_owner_died"]:
            print(f"AFTER DEATH  {r['object']:60s} {r['after_owner_died']:3d} (alive: {r['while_owner_alive']}) "
                  f"{r['examples'][:3]}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]])

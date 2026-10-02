"""Stage 0a (docs/replay-map-control-plan.md): kill lines and positions from one replay export.

Feasibility tooling, not product code. For every kill in the export it writes the killer's and
the victim's position at the kill (minimap u/v plus world z), the lethal damage row's
`IsWallPenetration` flag and RPC, and the killer's weapon. It also writes every player position
at 4 Hz, for occupancy counts. Reads the export with the condenser's own streaming loader, so
slots and the map resolve exactly as they do at ingest.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\kill_lines.py <export_dir> <out_dir>
"""

from __future__ import annotations

import bisect
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.replays import condense as cz  # noqa: E402
from app.replays.contract import load_export_streaming  # noqa: E402

POSITION_STEP_MS = 250
# The `.vrf` names the map when the export doesn't, as at ingest.
VRF_ARCHIVE = Path.home() / "ValorantReplayArchive"
# A kill's lethal damage row is within this long of the kill.
LETHAL_MATCH_MS = cz.DAMAGE_KILL_MATCH_MS


def nearest(samples: list, times: list[int], t_ms: int):
    i = bisect.bisect_left(times, t_ms)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(samples) and (best is None or abs(times[j] - t_ms) < abs(times[best] - t_ms)):
            best = j
    return None if best is None else samples[best]


def main(export_dir: Path, out_dir: Path) -> None:
    started = time.time()
    export = load_export_streaming(export_dir)
    maps, agents = cz.load_maps(), cz.load_agents()
    vrf = VRF_ARCHIVE / f"{export_dir.name}.vrf"
    game_map, _ = cz.discover_map(export, maps, cz.map_codes_in_file(vrf) if vrf.is_file() else None)
    if game_map is None:
        raise ValueError("no map named in the export")
    players = cz.build_players(export, agents)
    kills, _ = cz.read_kills(export, players)
    lethal = []
    for row in export.events:
        data = row.data
        if data.get("type") != "rpc_received" or data.get("function_name") not in cz.RPC_DAMAGE:
            continue
        payload = data.get("payload") or {}
        if payload.get("DamageKilledTarget") is not True:
            continue
        victim = players.resolve(int(payload.get("Character") or 0), row.time_ms)
        if victim is None:
            continue
        used = payload.get("EquippableUsed") or {}
        lethal.append({"t_ms": row.time_ms, "victim": victim, "rpc": data.get("function_name"),
                       "wall_pen": payload.get("IsWallPenetration"),
                       "equippable": used.get("Name") or used.get("ClassPath"),
                       "category": used.get("Category")})
    by_slot, _, _ = cz.read_movement(export, players)
    times = {slot: [s.t_ms for s in rows] for slot, rows in by_slot.items()}

    def where(slot: int, t_ms: int):
        s = nearest(by_slot.get(slot, []), times.get(slot, []), t_ms)
        if s is None:
            return None
        u, v = game_map.to_uv(s.x, s.y)
        return {"u": u, "v": v, "z": round(s.z or 0.0), "dt_ms": s.t_ms - t_ms}

    out_kills = []
    for k in kills:
        rows = [r for r in lethal if r["victim"] == k.victim and abs(r["t_ms"] - k.t_ms) <= LETHAL_MATCH_MS]
        row = min(rows, key=lambda r: abs(r["t_ms"] - k.t_ms)) if rows else None
        out_kills.append({"t_ms": k.t_ms, "killer": k.killer, "victim": k.victim, "source": k.source,
                          "rpc": row and row["rpc"], "wall_pen": row and row["wall_pen"],
                          "equippable": row and row["equippable"], "category": row and row["category"],
                          "killer_pos": where(k.killer, k.t_ms), "victim_pos": where(k.victim, k.t_ms)})
    positions = {}
    for slot, rows in by_slot.items():
        pts, last = [], -POSITION_STEP_MS
        for s in rows:
            if s.t_ms - last >= POSITION_STEP_MS:
                u, v = game_map.to_uv(s.x, s.y)
                pts.append([s.t_ms, u, v, round(s.z or 0.0)])  # a missing height is 0 here, as before revision 11
                last = s.t_ms
        positions[str(slot)] = pts
    uuid = export_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{uuid}.json").write_text(json.dumps(
        {"uuid": uuid, "map": game_map.name, "kills": out_kills, "positions": positions}))
    print(f"{uuid} {game_map.name}: {len(out_kills)} kills, "
          f"{sum(len(p) for p in positions.values())} positions, {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    out = Path(sys.argv[2])
    for arg in sys.argv[1].split(","):
        try:
            main(Path(arg), out)
        except Exception as error:  # noqa: BLE001 - one bad export must not stop the rest
            print(f"{Path(arg).name}: FAILED {type(error).__name__}: {error}", flush=True)

"""Writes app/static/data/callouts.json from valorant-api.com (a community site, not Riot's) map data. Run once, and again when a map is added:

    $PY scripts/fetch_callouts.py

No database. A map is matched by the `uuid` in app/static/data/maps.json, never by its display name."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

URL = "https://valorant-api.com/v1/maps"
OUT = WEBAPP_ROOT / "app" / "static" / "data" / "callouts.json"


def convert(api_maps: list[dict], maps: dict, uuid_of: dict[str, str]) -> dict:
    by_uuid = {m["uuid"]: m for m in api_maps}
    out = {}
    for name in sorted(maps):
        rows = []
        for c in (by_uuid.get(uuid_of.get(name), {}).get("callouts") or []):
            u, v = maps[name].to_uv(c["location"]["x"], c["location"]["y"])
            rows.append({"name": f'{c["superRegionName"]} {c["regionName"]}', "super": c["superRegionName"],
                         "u": u, "v": v})
        out[name] = sorted(rows, key=lambda r: (r["name"], r["u"], r["v"]))
    return out


def main() -> int:
    from app.replays.condense import load_maps

    raw = json.loads((WEBAPP_ROOT / "app" / "static" / "data" / "maps.json").read_text(encoding="utf-8"))
    with urllib.request.urlopen(URL, timeout=30) as response:
        api_maps = json.load(response)["data"]
    table = convert(api_maps, load_maps(), {name: entry["uuid"] for name, entry in raw.items()})
    OUT.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for name, rows in table.items():
        print(f"{name}: {len(rows)} callouts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Vendors the minimap images and the map/agent tables the replay viewer needs.

    .\\.venv313\\Scripts\\python.exe scripts\\vendor_map_assets.py

Reads valorant-api.com's public `/v1/maps` and `/v1/agents` once and writes, under
`app/static/`:

- `img/maps/<Map>.png`: each playable map's square minimap (`displayIcon`);
- `data/maps.json`: per map, its display name (the DB's `matches.map_name` spelling),
  the `/Game/Maps/<code>/` folder the replay's actor paths name, and the affine
  world-to-minimap transform `u = y * xMultiplier + xScalarToAdd`,
  `v = x * yMultiplier + yScalarToAdd` (0..1 of the image);
- `data/agents.json`: `developerName` (the code name in a character's archetype path,
  e.g. `Wushu`) -> the display name the DB's `match_players.agent` uses (e.g. `Jett`,
  `KAY/O`).

The files are committed and served locally, never hotlinked. Their hash is the
replay recipe's `ASSETS_REVISION`, so re-running this after a new map or agent
changes the recipe of every replay condensed afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

API = "https://valorant-api.com/v1"
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
TRANSFORM_KEYS = ("xMultiplier", "yMultiplier", "xScalarToAdd", "yScalarToAdd")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "valo-with-friends-tracker asset vendoring"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_json(url: str) -> list[dict]:
    body = json.loads(fetch(url))
    if body.get("status") != 200:
        raise SystemExit(f"{url} returned status {body.get('status')}")
    return body["data"]


def map_code(map_url: str) -> str:
    """`/Game/Maps/Ascent/Ascent` -> `Ascent`: the folder a replay's actor paths contain."""
    parts = map_url.strip("/").split("/")
    if len(parts) < 3 or parts[:2] != ["Game", "Maps"]:
        raise ValueError(f"unexpected mapUrl {map_url!r}")
    return parts[2]


def playable_maps(maps: list[dict]) -> list[dict]:
    """Maps with a minimap transform: the standard 5v5 maps (Swiftplay uses them too)."""
    return [m for m in maps if m.get("displayIcon") and all(m.get(k) for k in TRANSFORM_KEYS)]


def build_maps_table(maps: list[dict]) -> dict:
    table = {}
    for m in sorted(playable_maps(maps), key=lambda m: m["displayName"]):
        name = m["displayName"]
        if name in table:
            raise SystemExit(f"two playable maps are both called {name!r}")
        table[name] = {
            "code": map_code(m["mapUrl"]),
            "map_url": m["mapUrl"],
            "uuid": m["uuid"],
            "image": f"img/maps/{name}.png",
            **{key: m[key] for key in TRANSFORM_KEYS},
        }
    return table


def build_agents_table(agents: list[dict]) -> dict:
    table = {}
    for a in agents:
        if not a.get("isPlayableCharacter"):
            continue
        if a["developerName"] in table:
            raise SystemExit(f"two agents share the code name {a['developerName']!r}")
        table[a["developerName"]] = a["displayName"]
    return dict(sorted(table.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--static-dir", type=Path, default=STATIC)
    args = parser.parse_args(argv)

    maps = fetch_json(f"{API}/maps")
    agents = fetch_json(f"{API}/agents?isPlayableCharacter=true")
    maps_table = build_maps_table(maps)
    agents_table = build_agents_table(agents)

    image_dir = args.static_dir / "img" / "maps"
    data_dir = args.static_dir / "data"
    image_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    by_name = {m["displayName"]: m for m in playable_maps(maps)}
    for name in maps_table:
        (image_dir / f"{name}.png").write_bytes(fetch(by_name[name]["displayIcon"]))
    (data_dir / "maps.json").write_text(json.dumps(maps_table, indent=2) + "\n", encoding="utf-8")
    (data_dir / "agents.json").write_text(json.dumps(agents_table, indent=2) + "\n", encoding="utf-8")
    print(f"{len(maps_table)} maps, {len(agents_table)} agents written under {args.static_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

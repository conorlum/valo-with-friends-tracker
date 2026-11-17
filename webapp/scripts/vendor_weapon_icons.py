"""Vendors every gun's icon for the replay viewer's players panel.

    .\\.venv313\\Scripts\\python.exe scripts\\vendor_weapon_icons.py

Reads valorant-api.com's public `/v1/weapons` once (the same Riot art as the agent and ability
icons; see vendor_map_assets.py and vendor_ability_icons.py) and writes, under `app/static/`:

- `img/weapons/<gun slug>.png`: each gun's kill-feed silhouette (white on transparency, drawn
  facing right), which the viewer uses as a mask filled with the text colour;
- `data/weapons.json`: gun display name (as a replay's shot names it, e.g. "Vandal") -> icon path.

The files are committed and served locally, never hotlinked. Like the ability icons they are not
part of the replay recipe: an icon never changes what a condensed replay holds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vendor_map_assets import API, STATIC, fetch, fetch_json  # noqa: E402


def gun_slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def main() -> int:
    weapons = fetch_json(f"{API}/weapons")
    index: dict[str, str] = {}
    for weapon in sorted(weapons, key=lambda w: w["displayName"]):
        url = weapon.get("killStreamIcon") or weapon.get("displayIcon")
        if not url:
            continue
        slug = gun_slug(weapon["displayName"])
        path = STATIC / "img" / "weapons" / f"{slug}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch(url))
        index[weapon["displayName"]] = f"/static/img/weapons/{slug}.png"
    out = STATIC / "data" / "weapons.json"
    out.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(index)} gun icons -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

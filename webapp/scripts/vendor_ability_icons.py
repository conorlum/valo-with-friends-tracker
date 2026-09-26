"""Vendors every playable agent's ability icons for the replay viewer.

    .\\.venv313\\Scripts\\python.exe scripts\\vendor_ability_icons.py

Reads valorant-api.com's public `/v1/agents` once (the same Riot `displayIcon` art the agent
icons and minimaps come from; see vendor_map_assets.py) and writes, under `app/static/`:

- `img/abilities/<agent slug>/<slot>.png`: each ability's white glyph on transparency, with
  `<slot>` one of `ability1`, `ability2`, `grenade`, `ultimate` (passives have no icon);
- `data/abilities.json`: agent display name -> {ability display name -> icon path}, which
  `static/js/replay.js` looks names up in.

The files are committed and served locally, never hotlinked. Unlike maps.json and agents.json
they are not part of the replay recipe: an icon never changes what a condensed replay holds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vendor_map_assets import API, STATIC, fetch, fetch_json  # noqa: E402


def agent_slug(name: str) -> str:
    """The same slug as the agent icons (`KAY/O` -> `kayo`)."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def main() -> int:
    agents = fetch_json(f"{API}/agents?isPlayableCharacter=true")
    index: dict[str, dict[str, str]] = {}
    written = 0
    for agent in sorted(agents, key=lambda a: a["displayName"]):
        slug = agent_slug(agent["displayName"])
        entries = {}
        for ability in agent.get("abilities") or []:
            if not ability.get("displayIcon") or ability.get("slot") == "Passive":
                continue
            slot = ability["slot"].lower()
            path = STATIC / "img" / "abilities" / slug / f"{slot}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fetch(ability["displayIcon"]))
            entries[ability["displayName"]] = f"/static/img/abilities/{slug}/{slot}.png"
            written += 1
        index[agent["displayName"]] = dict(sorted(entries.items()))
    out = STATIC / "data" / "abilities.json"
    out.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{written} icons for {len(index)} agents -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

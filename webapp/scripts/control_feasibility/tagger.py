"""Stage 0a (docs/replay-map-control-plan.md, Q53): the minimal cover-tagging page.

Feasibility tooling, not product code. Builds one self-contained local page,
`<out>/tagger.html`, with every map's minimap, candidate labels and (where replays exist) the
Risk 1 kill lines embedded. On the page a person clicks a candidate and tags it cover, see-over,
walkable or glyph; the blocked share of kill lines updates live against the 2% bar. Tags are
kept in the browser and exported as `control-tags.json`, which Stage 1 reads.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\tagger.py <out_dir>
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

MAPS_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "img" / "maps"
TEMPLATE = Path(__file__).with_name("tagger.template.html")


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main(out: Path) -> None:
    geo = out / "geometry"
    risk = json.loads((out / "risk1.json").read_text())["maps"] if (out / "risk1.json").is_file() else {}
    maps = {}
    for png in sorted(MAPS_DIR.glob("*.png")):
        name = png.stem
        g = json.loads((geo / f"{name}.json").read_text())
        r = risk.get(name) or {}
        maps[name] = {
            "image": b64(png), "labels": b64(geo / f"{name}.labels.png"), "image_sha": g["image_sha"],
            "params": g["params"],
            "candidates": [{**c, **((r.get("candidates") or {}).get(str(c["id"])) or {})} for c in g["candidates"]],
            "lines": [[*l["a"], *l["b"], int(l["hard"]), l["crossed"]] for l in r.get("lines", [])],
            "replays": r.get("replays", 0),
        }
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", json.dumps(maps))
    (out / "tagger.html").write_text(html, encoding="utf-8")
    print(f"wrote {out / 'tagger.html'} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

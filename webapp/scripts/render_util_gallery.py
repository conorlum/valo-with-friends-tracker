"""Write an offline utility critique gallery from anonymous harvested samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent))

from app.services.replays import _uv_per_unit  # noqa: E402
from render_replay_standalone import agent_icon_name, data_uri  # noqa: E402

APP = SCRIPTS.parent / "app"
GALLERY = SCRIPTS / "util_gallery"


def render(samples: dict) -> str:
    """Inline the viewer, samples and local assets without interpreting them as Jinja."""
    groups = samples.get("keys", samples)
    rows = [sample for group in groups.values()
            for sample in (group if isinstance(group, list) else group.get("samples", []))]
    maps = sorted({s["map"] for s in rows if s.get("map")})
    abilities = json.loads((APP / "static/data/abilities.json").read_text(encoding="utf-8"))
    agents = set(abilities)
    agents.update(row["agent"] for sample in rows
                  for row in [sample["row"], *sample.get("companions", [])] if row.get("agent"))
    urls = {url for kit in abilities.values() for url in kit.values()}
    replay = (APP / "static/js/replay.js").read_text(encoding="utf-8")
    payload = {
        "samples": samples,
        "maps": {name: data_uri(APP / "static/img/maps" / f"{name}.png") for name in maps},
        "icons": {agent: data_uri(APP / "static/img/agents" / f"{agent_icon_name(agent)}.png")
                  for agent in sorted(agents)},
        "abilityIcons": abilities,
        "assets": {url: data_uri(APP / url.removeprefix("/")) for url in sorted(urls)},
        "mapScales": _uv_per_unit(),
        "replay_js_sha256": hashlib.sha256(replay.encode("utf-8")).hexdigest(),
    }
    env = Environment(loader=FileSystemLoader(str(GALLERY)), autoescape=select_autoescape(["html"]))
    shell = env.get_template("gallery.html").render()
    blocks = {
        "__GALLERY_STYLE__": (APP / "static/css/style.css").read_text(encoding="utf-8"),
        "__REPLAY_JS__": replay,
        "__GALLERY_JS__": (GALLERY / "gallery.js").read_text(encoding="utf-8"),
        "__GALLERY_PAYLOAD__": json.dumps(payload, separators=(",", ":")),
    }
    # A single substitution pass also preserves literal template/placeholder text in inputs.
    return re.sub("|".join(map(re.escape, blocks)),
                  lambda match: blocks[match.group()].replace("</", "<\\/"), shell)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--out", type=Path,
                        default=Path(tempfile.gettempdir()) / "valo-util-gallery/util-gallery.html")
    args = parser.parse_args(argv)
    try:
        samples = json.loads(args.samples.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        parser.error(f"Cannot read samples {args.samples}: {error}")
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(samples), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Writes a self-contained page of the replay player, for looking at the viewer before Stage 2's routes.

    .\\.venv313\\Scripts\\python.exe scripts\\render_replay_standalone.py --export-dir %TEMP%\\valo-replay\\<uuid> [--vrf <file>]
    .\\.venv313\\Scripts\\python.exe scripts\\render_replay_standalone.py --blobs <folder of N.json.gz>

Dev-only (W-d): the page inlines the site's `style.css`, `static/js/replay.js`, the player partial
(`templates/replays/_player.html`), the minimap and agent icons, and every round's blob, so it
opens from disk with no server. It shows slots, agents and side groups only: no names,
Subjects or match identifiers. Written under `%TEMP%\\valo-replay\\` by default, never the repo.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from app.replays import format as fmt  # noqa: E402
from app.replays.condense import condense_export_dir  # noqa: E402
from app.replays.contract import ContractError  # noqa: E402

APP = WEBAPP_ROOT / "app"


def agent_icon_name(agent: str) -> str:
    return "".join(ch for ch in agent.lower() if ch.isalpha())


def data_uri(path: Path, mime: str = "image/png") -> str | None:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii") if path.is_file() else None


def load_blobs(folder: Path) -> dict[int, dict]:
    blobs = {}
    for path in sorted(folder.glob("*.json.gz"), key=lambda p: int(p.name.split(".")[0])):
        blobs[int(path.name.split(".")[0])] = fmt.decode_blob(path.read_bytes())
    if not blobs:
        raise SystemExit(f"no N.json.gz blobs in {folder}")
    return blobs


def render(blobs: dict[int, dict]) -> str:
    first = blobs[min(blobs)]
    map_name = first["map"]
    env = Environment(loader=FileSystemLoader(str(APP / "templates")), autoescape=select_autoescape(["html"]))
    player = env.get_template("replays/_player.html").render(
        replay={"map_name": map_name, "round_numbers": sorted(blobs)}, linked=False)
    agents = sorted({p["agent"] for blob in blobs.values() for p in blob["players"]})
    icons = {a: data_uri(APP / "static" / "img" / "agents" / f"{agent_icon_name(a)}.png") for a in agents}
    payload = {"rounds": {str(n): blob for n, blob in blobs.items()},
               "map": data_uri(APP / "static" / "img" / "maps" / f"{map_name}.png"), "icons": icons}
    style = (APP / "static" / "css" / "style.css").read_text(encoding="utf-8")
    script = (APP / "static" / "js" / "replay.js").read_text(encoding="utf-8")
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return PAGE.replace("__TITLE__", f"{map_name} replay (local preview)").replace("__STYLE__", style) \
        .replace("__PLAYER__", player).replace("__SCRIPT__", script).replace("__DATA__", data)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
__STYLE__
</style></head>
<body><main>
<p class="replay-note">Local preview page written by scripts/render_replay_standalone.py (not the site).</p>
__PLAYER__
</main>
<script>
__SCRIPT__
</script>
<script>
  window.REPLAY_DATA = __DATA__;
  (function () {
    var data = window.REPLAY_DATA;
    var rounds = Object.keys(data.rounds).map(Number).sort(function (a, b) { return a - b; });
    window.viewer = new Replay.ReplayViewer(document.querySelector("[data-replay]"), {
      rounds: rounds,
      mapImage: data.map,
      agentIcon: function (agent) { return data.icons[agent] || null; },
      loadRound: function (n) { return data.rounds[String(n)]; }
    });
    window.viewerReady = window.viewer.showRound(rounds[0]);
  })();
</script>
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--export-dir", type=Path, help="condense this export (streaming)")
    source.add_argument("--blobs", type=Path, help="a folder of stored round blobs, N.json.gz")
    parser.add_argument("--vrf", type=Path, help="the export's .vrf (its map and match UUID)")
    parser.add_argument("--out", type=Path, help="the page to write (default under %%TEMP%%\\valo-replay\\)")
    args = parser.parse_args(argv)
    if args.blobs:
        blobs = load_blobs(args.blobs)
        name = args.blobs.name
    else:
        try:
            replay = condense_export_dir(args.export_dir, source_sha256=None, vrf_path=args.vrf)
        except ContractError as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return 3
        blobs = replay.rounds
        name = replay.match_uuid
    out = args.out or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay" / name / "replay-standalone.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(blobs), encoding="utf-8")
    print(f"{len(blobs)} rounds -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

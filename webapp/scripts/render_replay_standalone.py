"""Writes a self-contained page of the replay player, for looking at the viewer before Stage 2's routes.

    .\\.venv313\\Scripts\\python.exe scripts\\render_replay_standalone.py --export-dir %TEMP%\\valo-replay\\<uuid> [--vrf <file>]
    .\\.venv313\\Scripts\\python.exe scripts\\render_replay_standalone.py --blobs <folder of N.json.gz>

Dev-only (W-d): the page inlines the site's `style.css`, `static/js/replay.js`, the player partial
(`templates/replays/_player.html`), the minimap and agent icons, and every round's blob, so it
opens from disk with no server. From blobs alone it shows slots, agents and side groups only: no
names, Subjects or match identifiers. Written under `%TEMP%\\valo-replay\\` by default, never the repo.

A `--blobs` folder written by scripts/export_replay_preview.py also holds the page's site data
(`context.json`) and map control (`N.control.bin`, `control_players.json`, `control_heatmap_*.json`):
then the page is the linked one, with player names, the kill feed and the control layer, table and
heatmap (docs/map-control-stages-4-7-impl.md, S4.5). Such a page names real players: keep it local.

When that folder also holds timing gaps (`N.gaps.json`, written by `scripts/preview_gaps.py
--write-json`), the linked page offers the Gaps layer, tab and list too (inlining `replay_gaps.js`);
a round with no `N.gaps.json` shows as not computed.

The page opens on its first round at 0 s, or where its URL hash says: `replay-standalone.html#round=2&t=7.5`
opens round 2 at 7.5 s (either part may be left out), for pointing someone at a moment.
"""

from __future__ import annotations

import argparse
import base64
import gzip
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


def load_site(folder: Path) -> dict | None:
    """What scripts/export_replay_preview.py wrote beside the blobs: the page's site data and the
    round control (inlined un-gzipped, as a browser receives it) and timing gaps (round -> the
    gaps.json body scripts/preview_gaps.py --write-json wrote), or None."""
    context_path = folder / "context.json"
    if not context_path.is_file():
        return None
    site = {"context": json.loads(context_path.read_text(encoding="utf-8")), "control": {}, "players": None,
            "heatmaps": {}, "gaps": {}}
    for path in folder.glob("*.gaps.json"):
        site["gaps"][path.name.split(".")[0]] = json.loads(path.read_text(encoding="utf-8"))
    for path in folder.glob("*.control.bin"):
        site["control"][path.name.split(".")[0]] = base64.b64encode(gzip.decompress(path.read_bytes())).decode("ascii")
    if (folder / "control_players.json").is_file():
        site["players"] = json.loads((folder / "control_players.json").read_text(encoding="utf-8"))
    for path in folder.glob("control_heatmap_*.json"):
        site["heatmaps"][path.stem.split("_")[-1]] = json.loads(path.read_text(encoding="utf-8"))
    return site


def render(blobs: dict[int, dict], site: dict | None = None) -> str:
    first = blobs[min(blobs)]
    map_name = first["map"]
    env = Environment(loader=FileSystemLoader(str(APP / "templates")), autoescape=select_autoescape(["html"]))
    match = (site or {}).get("context", {}).get("match") or {}
    linked = bool(site and match.get("linked"))
    control = match.get("control") if linked else None
    gaps = bool(linked and site.get("gaps"))
    player = env.get_template("replays/_player.html").render(
        replay={"map_name": map_name, "round_numbers": sorted(blobs)}, linked=linked, control=control, gaps=gaps)
    agents = sorted({p["agent"] for blob in blobs.values() for p in blob["players"]})
    icons = {a: data_uri(APP / "static" / "img" / "agents" / f"{agent_icon_name(a)}.png") for a in agents}
    payload = {"rounds": {str(n): blob for n, blob in blobs.items()},
               "map": data_uri(APP / "static" / "img" / "maps" / f"{map_name}.png"), "icons": icons,
               # the tracer's obstacles, inlined like the map (None for a map without the asset)
               "bullet": data_uri(APP / "static" / "data" / "control" / f"{map_name}.bullet.png")}
    if linked:
        payload["site"] = site["context"]
        payload["control"] = site["control"] if control else {}
        payload["controlPlayers"] = site["players"] if control else None
        payload["heatmaps"] = site["heatmaps"] if control else {}
    if gaps:
        payload["gaps"] = site["gaps"]
    style = (APP / "static" / "css" / "style.css").read_text(encoding="utf-8")
    script = (APP / "static" / "js" / "replay.js").read_text(encoding="utf-8")
    if gaps:   # before replay.js, as the site's pages include it
        script = (APP / "static" / "js" / "replay_gaps.js").read_text(encoding="utf-8") + "\n" + script
    if control:
        script += "\n" + (APP / "static" / "js" / "replay_control.js").read_text(encoding="utf-8")
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
    var site = data.site, match = site ? site.match : null;
    var options = {
      rounds: rounds,
      mapImage: data.map,
      bulletMask: data.bullet || null,
      agentIcon: function (agent) { return data.icons[agent] || null; },
      loadRound: function (n) {
        var blob = JSON.parse(JSON.stringify(data.rounds[String(n)]));
        return site ? Replay.withSiteData(site.rounds[String(n)], blob) : blob;
      }
    };
    if (site && match.linked) {
      options.linked = { players: site.players, uvPerUnit: match.uv_per_unit };
      options.roundWinner = function (n) { var r = site.rounds[String(n)]; return r && r.db && r.db.winner; };
    }
    if (site && match.control && window.ReplayControl) {
      options.control = match.control;
      options.loadControl = function (n) {
        var raw = data.control[String(n)];
        return raw ? { status: "ok", buffer: ReplayControl.base64Bytes(raw), stale: false } : { status: "not_ready" };
      };
      options.loadControlPlayers = function () { return data.controlPlayers; };
      options.loadHeatmap = function (view) { return data.heatmaps[view] || null; };
    }
    if (site && match.linked && data.gaps && window.ReplayGaps) {
      options.gaps = true;
      options.loadGaps = function (n) {
        return Promise.resolve(data.gaps[String(n)] || { status: "not_computed", stale: false, rows: [], chokes: {} });
      };
    }
    // An optional "#round=N&t=T" in the URL opens round N at T seconds.
    var start = {};
    String(window.location.hash || "").replace(/^#/, "").split("&").forEach(function (part) {
      var kv = part.split("=");
      if (kv.length === 2 && kv[1] !== "" && isFinite(Number(kv[1]))) start[kv[0]] = Number(kv[1]);
    });
    var first = rounds.indexOf(start.round) >= 0 ? start.round : rounds[0];
    window.viewer = new Replay.ReplayViewer(document.querySelector("[data-replay]"), options);
    window.viewerReady = window.viewer.showRound(first).then(function (round) {
      if (start.t !== undefined) window.viewer.seek(start.t);
      return round;
    });
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
    site = None
    if args.blobs:
        blobs = load_blobs(args.blobs)
        site = load_site(args.blobs)
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
    out.write_text(render(blobs, site), encoding="utf-8")
    print(f"{len(blobs)} rounds -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

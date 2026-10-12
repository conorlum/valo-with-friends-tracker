"""Computes map control for a few rounds of a live replay and writes the local preview page, from the
friends site's public responses only: no database, no writes anywhere but %TEMP%.

    .\\.venv\\Scripts\\python.exe scripts\\preview_control_live.py <match uuid> <round> [<round> ...] [--tag NAME]

The replay page embeds what the engine's link needs (`replay-data`: clock offset, side_to_team, the
players' side groups, each round's db_deaths), and `/replays/<uuid>/<n>.json` is the stored round blob
as gzip. The rounds are computed with this checkout's engine, so a branch's engine can be looked at
before anything is stored (docs/map-control-unknown-plan.md: check a small sample before recomputing).
The page names real players: keep it local.

`--blobs <dir>` takes the round blobs from `<dir>/<n>.json.gz` (a branch's own re-condense of the export)
instead of the site's; the link data still comes from the public page. A missing local round stops the run.
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control.task import compute_task  # noqa: E402

SITE = "https://valowithfriendstracker.onrender.com"


def fetch(url: str) -> bytes:
    """The response's bytes as sent (a round blob is its stored gzip)."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept-Encoding": "gzip"}), timeout=120) as r:
        return r.read()


def page_data(uuid: str) -> dict:
    raw = fetch(f"{SITE}/replays/{uuid}")
    html = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8")
    found = re.search(r'<script id="replay-data" type="application/json">(.*?)</script>', html, re.S)
    if found is None:
        raise SystemExit(f"no replay data on {SITE}/replays/{uuid}")
    return json.loads(found.group(1))


def local_blobs(folder: Path, rounds: list[int]) -> dict[int, bytes]:
    """`--blobs`: each round's gzip blob from `<folder>/<n>.json.gz`; a missing one stops the run."""
    missing = [str(folder / f"{n}.json.gz") for n in rounds if not (folder / f"{n}.json.gz").is_file()]
    if missing:
        raise SystemExit(f"no local blob: {', '.join(missing)} (--blobs never falls back to the public blob)")
    return {n: (folder / f"{n}.json.gz").read_bytes() for n in rounds}


def link_for(ctx: dict, n: int) -> dict:
    """app/services/replay_control.round_link, from the page's data."""
    from app.scoring.plant_window import attacking_team

    match, attacking = ctx["match"], attacking_team(n)
    sides = {}
    for slot, p in sorted(ctx["players"].items(), key=lambda kv: int(kv[0])):
        team = match["side_to_team"].get(p["side"])
        if team is not None and attacking is not None:
            sides[str(slot)] = "attack" if team == attacking.value else "defense"
    offset = match.get("clock_offset") or 0.0
    deaths = sorted([d["slot"], round(float(d["t_db"]) - offset, 3)]
                    for d in ctx["rounds"][str(n)].get("db_deaths", []) if d.get("slot") is not None)
    return {"sides": sides, "db_deaths": deaths}


def main(argv: list[str] | None = None) -> int:
    from app.replays import control_format as cf
    from app.services import replay_control_views as views

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("uuid")
    parser.add_argument("rounds", type=int, nargs="+")
    parser.add_argument("--tag", default=f"rev{cf.CONTROL_REVISION}", help="the output folder's suffix")
    parser.add_argument("--heights", type=Path,
                        help="a height asset to preview the map with (scripts/build_control_heights.py --preview); "
                             "never a committed one's stand-in")
    parser.add_argument("--blobs", type=Path,
                        help="a folder of locally condensed round blobs, <n>.json.gz, used instead of the site's "
                             "stored ones (the link data still comes from the public page); a missing round is an "
                             "error, never a fallback to the public blob")
    args = parser.parse_args(argv)
    local = local_blobs(args.blobs, args.rounds) if args.blobs else None   # read before the output folder is cleared
    out = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-replay" / f"{args.uuid}-{args.tag}"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        f.unlink()
    ctx = page_data(args.uuid)
    tasks = []
    for n in args.rounds:
        blob = local[n] if local is not None else fetch(f"{SITE}/replays/{args.uuid}/{n}.json")
        (out / f"{n}.json.gz").write_bytes(blob)
        tasks.append({"key": n, "map": ctx["match"]["map"], "blob": blob, "link": link_for(ctx, n),
                      **({"heights": str(args.heights)} if args.heights else {})})
    print(f"{ctx['match']['map']}: rounds {args.rounds} at revision {cf.CONTROL_REVISION}", flush=True)
    from app.control import geometry
    # the visibility bitsets once, before the workers (as compute_control does): after a geometry change
    # every worker would build them at once and race on the cache file
    geo = geometry.visibility(geometry.load_geometry(ctx["match"]["map"], heights=args.heights))
    if args.heights:
        print(f"heights: {args.heights.name} ({geo.height_sha}), {geo.n - geometry.GRID * geometry.GRID} upper "
              f"floors, {int(geo.unresolved.sum())} unresolved cells", flush=True)
    started, results = time.time(), {}
    with multiprocessing.Pool(min(len(tasks), os.cpu_count() or 1)) as pool:
        for r in pool.imap_unordered(compute_task, tasks):
            print(f"  r{r['key']}: {r['status']} {r['seconds']:.0f}s {r.get('error', '')[:600]}", flush=True)
            results[r["key"]] = r
    print(f"computed in {time.time() - started:.0f}s", flush=True)
    loaded, walks = views.RoundSummaries(), {}
    for n, r in sorted(results.items()):
        if r["status"] != "ok":
            continue
        (out / f"{n}.control.bin").write_bytes(r["data"])
        header, streams = cf.unpack_data(r["data"])
        sizes = ", ".join(f"{k} {len(v) // 1024} KB" for k, v in streams.items())
        print(f"  r{n}: {len(r['data']) // 1024} KB gzipped; raw streams: {sizes}", flush=True)
        for key, count in sorted((r.get("missing") or {}).items()):
            if "height" in key or "unresolved" in key:
                print(f"  WARNING r{n}: {key}: {count}", flush=True)
        loaded.summaries[n] = cf.unpack_summary(r["summary"])
        walks[n] = views.read_walk(r["data"])

    class _Replay:                     # what player_tables reads from a Replay
        round_count = max(args.rounds)

    tables = views.player_tables(_Replay, loaded)
    tables["rounds"] = {k: v for k, v in tables["rounds"].items() if int(k) in args.rounds}
    (out / "control_players.json").write_text(json.dumps(tables), encoding="utf-8")
    totals = views._match_totals(loaded.summaries, walks, max(loaded.summaries)) if loaded.summaries else None
    for view in views.HEATMAP_VIEWS:
        body = (views.match_heatmap(totals, view, ctx["match"]["side_to_team"]) if totals
                else {"status": "missing", "view": view, "sections": []})
        (out / f"control_heatmap_{view}.json").write_text(json.dumps(body), encoding="utf-8")
    ctx["match"]["rounds"] = args.rounds
    (out / "context.json").write_text(json.dumps(ctx), encoding="utf-8")
    page = out / "preview.html"
    subprocess.check_call([sys.executable, str(WEBAPP_ROOT / "scripts" / "render_replay_standalone.py"),
                           "--blobs", str(out), "--out", str(page)], cwd=WEBAPP_ROOT)
    print(f"PAGE {page}")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

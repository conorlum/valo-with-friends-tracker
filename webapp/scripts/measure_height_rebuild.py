"""What one height rebuild cycle costs for a map, measured on this machine from frozen rounds
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, "Cost, and what isn't known yet").

    .\\.venv\\Scripts\\python.exe scripts\\measure_height_rebuild.py --map Sunset --blobs-dir %TEMP%\\valo-replay\\heights-frozen

It reads only that folder (`<match>/<n>.json.gz`, as `freeze_height_rounds.py` writes it) and writes only under
a temp folder. It prints the four parts of a cycle:

- **sent**: the rounds' size as stored and as the base64 a request carries, and the batches of BATCH_BYTES;
- **build**: seconds and peak memory of the build with both checks;
- **warm-up**: seconds to build the map's visibility under the new heights (the first round after a rebuild
  pays this);
- **rounds**: seconds per round of control under those heights, over `--rounds` of them (default 3), and so
  the minutes to recompute every round of the map on `--workers` workers (default 2, the replay worker's).

The worker is slower than a desk: these are a floor, and the first live cycle's own numbers replace them.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BATCH_BYTES = 2_000_000       # app/services/replay_heights_remote.py sends at most this much base64 a request


def sizes(directory: Path, map_name: str | None = None) -> dict:
    """{"matches", "rounds", "bytes", "largest"} over `<directory>/<match>/<n>.json.gz`. With `map_name`, only
    that map's rounds (the blobs are decoded to tell)."""
    paths = sorted(Path(directory).glob("*/*.json.gz"))
    if map_name:
        from app.replays import format as fmt

        paths = [p for p in paths if fmt.decode_blob(p.read_bytes()).get("map") == map_name]
    lengths = [p.stat().st_size for p in paths]
    return {"matches": len({p.parent.name for p in paths}), "rounds": len(paths), "bytes": sum(lengths),
            "largest": max(lengths, default=0)}


def estimate(found: dict, build_s: float, warm_s: float, round_s: float, workers: int = 2) -> dict:
    b64 = found["bytes"] * 4 // 3
    rounds_min = found["rounds"] * round_s / workers / 60
    return {"sent_mb": round(b64 / 1e6, 3), "batches": max(1, -(-b64 // BATCH_BYTES)),
            "build_min": round(build_s / 60, 2), "warm_min": round(warm_s / 60, 2), "rounds_min": round(rounds_min, 2),
            "cycle_min": round(build_s / 60 + warm_s / 60 + rounds_min, 2)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--blobs-dir", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3, help="rounds of control to time (default 3)")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    import build_control_heights as command
    from app.control import geometry as cg
    from app.control import height_build as hb
    from app.control import heights as hc
    from app.control.height_job import BlobDir
    from app.control.task import compute_task, peak_memory

    found = sizes(args.blobs_dir, args.map)
    if not found["rounds"]:
        print(f"no rounds of {args.map} in {args.blobs_dir}", flush=True)
        return 2
    with tempfile.TemporaryDirectory(prefix="valo-rebuild-cost-") as folder:
        os.environ["CONTROL_CACHE_DIR"] = folder          # a cold visibility cache, and none left behind
        geo = cg.load_geometry(args.map)
        started = time.perf_counter()
        rounds = BlobDir(args.blobs_dir, args.map)
        build = hb.build(rounds, geo)
        command.run_checks(args.map, geo, build, rounds)
        build_s, peak = time.perf_counter() - started, peak_memory()
        asset = Path(folder) / f"{args.map}.height.npz"
        hc.save_asset(asset, build.asset)
        started = time.perf_counter()
        cg.visibility(cg.load_geometry(args.map, heights=asset))
        warm_s = time.perf_counter() - started
        took = []
        for path in rounds.read[: max(1, args.rounds)]:
            result = compute_task({"key": path.name, "map": args.map, "blob": path.read_bytes(), "heights": str(asset),
                                   "link": {"sides": {}, "db_deaths": []}})
            if result["status"] == "ok":
                took.append(result["seconds"])
        if not took:
            print("no round computed: nothing to time", flush=True)
            return 2
        round_s = sum(took) / len(took)
    est = estimate(found, build_s, warm_s, round_s, args.workers)
    print(f"{args.map}: {found['matches']} matches, {found['rounds']} rounds", flush=True)
    print(f"  sent: {found['bytes'] / 1e6:.1f} MB stored, {est['sent_mb']:.1f} MB as base64, {est['batches']} batches of "
          f"{BATCH_BYTES / 1e6:g} MB; largest round {found['largest'] / 1e3:.0f} KB", flush=True)
    print(f"  build with both checks: {build_s:.0f} s, peak memory {(peak or 0) / 1e9:.2f} GB", flush=True)
    print(f"  visibility warm-up under the new heights: {warm_s:.0f} s", flush=True)
    print(f"  control: {round_s:.0f} s a round over {len(took)} rounds; all {found['rounds']} on {args.workers} workers: "
          f"{est['rounds_min']:.0f} min", flush=True)
    print(f"  one cycle here: {est['cycle_min']:.0f} min (the worker is slower)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

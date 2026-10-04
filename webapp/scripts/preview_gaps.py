"""Timing gaps for a preview folder of real rounds (written by scripts/preview_control_live.py: `<n>.json.gz`
and `context.json`), printed as a table. The tick cache is written into the preview folder only; nothing is
written but under %TEMP%.

    .\\.venv\\Scripts\\python.exe scripts\\preview_control_live.py <uuid> 1 2
    .\\.venv\\Scripts\\python.exe scripts\\preview_gaps.py %TEMP%\\valo-replay\\<uuid>-rev5
    .\\.venv\\Scripts\\python.exe scripts\\preview_gaps.py --from-cache %TEMP%\\valo-replay\\<uuid>-rev5

--from-cache replays each round's `<n>.ticks.pkl.gz` (written by an earlier run) through the detector, with no
engine. --no-merge keeps the pre-merge rows (no stack merge, R1). Each round prints its predicted count before
and after the merge and how many of the folded gaps had the empty choke sequence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(WEBAPP_ROOT / "scripts"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--from-cache", action="store_true",
                        help="replay each round's <n>.ticks.pkl.gz through the detector, without the engine")
    parser.add_argument("--no-merge", action="store_true", help="keep the pre-merge rows (no stack merge)")
    args = parser.parse_args(argv)

    import preview_control_live as preview

    from app.control import engine, geometry
    from app.gaps import cache, detect
    from app.replays import format as fmt

    ctx = json.loads((args.folder / "context.json").read_text(encoding="utf-8"))
    geo = geometry.visibility(geometry.load_geometry(ctx["match"]["map"]))
    # the numbered round files only (`<n>.json.gz`), not any other json.gz in the folder
    paths = sorted((p for p in args.folder.iterdir() if re.fullmatch(r"\d+\.json\.gz", p.name)),
                   key=lambda p: int(p.name.split(".")[0]))
    if args.from_cache:
        missing = [args.folder / f"{p.name.split('.')[0]}.ticks.pkl.gz" for p in paths]
        missing = [m for m in missing if not m.exists()]
        if missing:
            print(f"--from-cache: no tick cache {', '.join(str(m) for m in missing)}; run once without "
                  f"--from-cache to write it", file=sys.stderr)
            return 2
    for path in paths:
        n = int(path.name.split(".")[0])
        blob = fmt.decode_blob(path.read_bytes())
        link = preview.link_for(ctx, n)
        control_link = engine.ControlLink(sides={int(s): side for s, side in link["sides"].items()},
                                          db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
        rnd = engine.RoundInputs(blob, geo, control_link)
        det = detect.GapDetector(geo, rnd, merge=not args.no_merge)
        cache_path = args.folder / f"{n}.ticks.pkl.gz"
        stepped = [0.0]
        started = time.time()
        if args.from_cache:
            for rec, logs in cache.replay(cache_path):
                t0 = time.perf_counter()
                det.step(rec, logs)
                stepped[0] += time.perf_counter() - t0
            source = "tick cache+detector"
        else:
            writer = cache.Writer(cache_path)

            def both(rec, unk):
                writer(rec, unk)
                t0 = time.perf_counter()
                det.step(rec, unk.log)
                stepped[0] += time.perf_counter() - t0

            control = engine.compute_round(blob, geo, control_link, observer=both, knowledge=False)
            writer.close(missing=control.missing_inputs)
            source = "engine+detector"
        t0 = time.perf_counter()
        gaps = det.finish()
        finish_s = time.perf_counter() - t0
        notes = dict(det.notes)
        total = time.time() - started
        size = cache_path.stat().st_size
        counts = {"predicted": 0, "backshot": 0, "flicker": 0}
        folded = folded_empty = 0
        for g in gaps:
            counts[g.kind] = counts.get(g.kind, 0) + 1
            if g.flicker:
                counts["flicker"] += 1
            for m in (g.context or {}).get("merged", ()) if g.kind == "predicted" else ():
                folded += 1
                folded_empty += not m["choke_seq"]
        print(f"round {n}: {len(gaps)} rows (predicted {counts['predicted']}, back-shot {counts['backshot']}, "
              f"flicker {counts['flicker']}) in {total:.0f}s {source}; "
              f"detector own {det.timing['seconds'] + finish_s:.1f}s "
              f"(step {det.timing['seconds']:.1f}s over {det.timing['steps']} ticks, finish {finish_s:.1f}s; "
              f"outer step {stepped[0]:.1f}s); tick cache {size / 1e6:.1f} MB; notes {notes}")
        if args.no_merge:
            print(f"round {n} merge: off (predicted {counts['predicted']}, pre-merge)")
        else:
            print(f"round {n} merge: predicted {counts['predicted'] + folded} before, {counts['predicted']} after; "
                  f"{folded} folded, {folded_empty} of them with the empty sequence")
        for g in gaps:
            merged = len((g.context or {}).get("merged", ()))
            print(f"  {g.kind:9s} t {g.t_open:6.1f}-{(g.t_close or 0):6.1f} victim {g.victim} by {sorted(g.candidates)} "
                  f"seq {g.choke_seq} cause {g.cause} stood {g.stood_by} shot {g.shot_by} killed {g.killed_by} "
                  f"{'FLICKER ' if g.flicker else ''}{f'merged {merged}' if merged else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

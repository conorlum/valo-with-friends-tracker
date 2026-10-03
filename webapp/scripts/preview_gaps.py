"""Timing gaps for a preview folder of real rounds (written by scripts/preview_control_live.py: `<n>.json.gz`
and `context.json`), printed as a table. The tick cache is written into the preview folder only; nothing is
written but under %TEMP%.

    .\\.venv\\Scripts\\python.exe scripts\\preview_control_live.py <uuid> 1 2
    .\\.venv\\Scripts\\python.exe scripts\\preview_gaps.py %TEMP%\\valo-replay\\<uuid>-rev5
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
    for path in paths:
        n = int(path.name.split(".")[0])
        blob = fmt.decode_blob(path.read_bytes())
        link = preview.link_for(ctx, n)
        control_link = engine.ControlLink(sides={int(s): side for s, side in link["sides"].items()},
                                          db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
        rnd = engine.RoundInputs(blob, geo, control_link)
        det = detect.GapDetector(geo, rnd)
        cache_path = args.folder / f"{n}.ticks.pkl.gz"
        writer = cache.Writer(cache_path)
        stepped = [0.0]
        started = time.time()

        def both(rec, unk):
            writer(rec, unk)
            t0 = time.perf_counter()
            det.step(rec, unk.log)
            stepped[0] += time.perf_counter() - t0

        control = engine.compute_round(blob, geo, control_link, observer=both, knowledge=False)
        t0 = time.perf_counter()
        gaps = det.finish()
        finish_s = time.perf_counter() - t0
        writer.close(missing=control.missing_inputs)
        total = time.time() - started
        size = cache_path.stat().st_size
        counts = {"predicted": 0, "backshot": 0, "flicker": 0}
        for g in gaps:
            counts[g.kind] = counts.get(g.kind, 0) + 1
            if g.flicker:
                counts["flicker"] += 1
        print(f"round {n}: {len(gaps)} rows (predicted {counts['predicted']}, back-shot {counts['backshot']}, "
              f"flicker {counts['flicker']}) in {total:.0f}s engine+detector; "
              f"detector own {det.timing['seconds'] + finish_s:.1f}s "
              f"(step {det.timing['seconds']:.1f}s over {det.timing['steps']} ticks, finish {finish_s:.1f}s; "
              f"outer step {stepped[0]:.1f}s); tick cache {size / 1e6:.1f} MB; notes {dict(det.notes)}")
        for g in gaps:
            print(f"  {g.kind:9s} t {g.t_open:6.1f}-{(g.t_close or 0):6.1f} victim {g.victim} by {sorted(g.candidates)} "
                  f"seq {g.choke_seq} cause {g.cause} stood {g.stood_by} shot {g.shot_by} killed {g.killed_by} "
                  f"{'FLICKER' if g.flicker else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

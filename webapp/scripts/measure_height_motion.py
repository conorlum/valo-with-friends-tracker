"""Measures what the slope rules of the height build rest on, from stored rounds
(docs/superpowers/specs/2026-10-05-height-slopes-design.md, "Open questions" 1 and 2, and its start values).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\measure_height_motion.py --map Sunset --rect 26,76,28,81
    .\\.venv\\Scripts\\python.exe scripts\\measure_height_motion.py --map Sunset --blobs-dir <dir>

Read-only, and it writes nothing: rounds come from the database it is run against (through
`with_friends_db.py --read-only`) or from `--blobs-dir` (`<match>/<n>.json.gz`), as build_control_heights.py
reads them. It prints:

- the stored sample rates (`hz`), and how many track segments carry heights;
- every ability cast recorded in `util` for Jett, Waylay and Raze (`<code>_<name>` and its count), beside how
  many rounds each of those agents played: a movement ability with no line here is not recorded;
- speeds over BURST_S, for those three agents and for everyone else: along the ground and upward (what
  BURST_MPS and BURST_UP_MPS have to separate);
- vertical acceleration over WALK_S windows while moving: its histogram (the ground sits near 0, free fall
  near the game's gravity), which is what WALK_ACC_MAX has to separate;
- with `--rect x0,y0,x1,y1` (cells, inclusive): the share of moving windows inside it that pass the walk
  rule, and their slopes (the owner's staircase on Sunset is 26,76,28,81);
- steady descents steeper than SLOPE_MAX, by cell, selected independently of `airborne`: candidates for
  inspecting slides the build would read as falls. This is a diagnostic, not proof of ground contact;
  ropes and movement abilities can also produce candidates.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.control import geometry as cg  # noqa: E402
from app.control import height_motion as hm  # noqa: E402
from app.control import heights as hc  # noqa: E402

MOVERS = ("Jett", "Waylay", "Raze")
PCT = [50, 90, 99, 99.9, 99.99, 100]
ACC_BINS = [0, 1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 30, float("inf")]


def measure(rounds, geo, rect=None) -> dict:
    """Everything the command prints, from [(match, n, blob)] of one map."""
    out = {"hz": Counter(), "segments": 0, "segments_with_z": 0, "agent_rounds": Counter(), "casts": Counter(),
           "speed": defaultdict(list), "up": defaultdict(list), "acc": [], "rect_windows": 0, "rect_walk": 0,
           "rect_slopes": [], "steep": defaultdict(set), "rounds": 0, "moving": 0, "air": 0, "ground": 0,
           "flights": [], "gravity": []}
    for match, n, blob in rounds:
        out["rounds"] += 1
        hz = blob["hz"]
        out["hz"][hz] += 1
        agent_of = {int(p["slot"]): p.get("agent") for p in blob.get("players") or []}
        for agent in set(agent_of.values()) & set(MOVERS):
            out["agent_rounds"][agent] += 1
        for e in blob.get("util") or []:
            if e.get("k") == "ability" and agent_of.get(e.get("by")) in MOVERS:
                out["casts"][(agent_of[e["by"]], f"{e.get('code')}_{e.get('name')}")] += 1
        k = max(1, int(round(hc.BURST_S * hz)))
        w = int(round(hc.WALK_S * hz)) + 1
        for slot, t, x, y, z in hm.tracks(blob):
            out["segments"] += 1
            if z is None or len(t) <= max(k, w):
                continue
            out["segments_with_z"] += 1
            who = "movers" if agent_of.get(slot) in MOVERS else "others"
            out["speed"][who].append(np.hypot(x[k:] - x[:-k], y[k:] - y[:-k]) * geo.m_per_px * hz / k)
            out["up"][who].append((z[k:] - z[:-k]) / 10.0 * hz / k)
            span = (w - 1) / hz
            dxy = np.hypot(x[w - 1:] - x[:-(w - 1)], y[w - 1:] - y[:-(w - 1)]) * geo.m_per_px
            dz = (z[w - 1:] - z[:-(w - 1)]) / 10.0
            acc = np.convolve(z / 10.0, hm._acc_filter(w, hz)[::-1], "valid")
            moving = dxy >= hc.WALK_MIN_MPS * span
            out["acc"].append(np.abs(acc[moving]))
            air = hm.airborne(x, y, z.astype(float), hz, geo.m_per_px)
            ground = hm.on_ground(x, y, z.astype(float), hz, geo.m_per_px)
            mid = np.arange(len(dxy)) + w // 2
            out["moving"] += int(moving.sum())
            out["air"] += int((moving & air[mid]).sum())
            out["ground"] += int((moving & ground[mid]).sum())
            for i, j in hm._runs_of(air):
                out["flights"].append((j - i) / hz)
                if j - i >= w:                     # long enough to read gravity off: a free parabola's curvature
                    tau = np.arange(j - i) / hz
                    out["gravity"].append(-2 * float(np.polyfit(tau, z[i:j] / 10.0, 2)[0]))
            # This diagnostic must not depend on `airborne`: its slope rule is what we are measuring.
            # Low acceleration identifies candidates for inspection, not confirmed ground contact.
            quiet = moving & (np.abs(acc) <= hc.WALK_ACC_MAX)
            cells = hm.cells_of(x[mid], y[mid])
            steep = quiet & (dz < 0) & (-dz > hc.SLOPE_MAX * dxy + 0.1)
            for c in set(cells[steep].tolist()):
                out["steep"][c].add((str(match), n, slot))
            if rect is not None:
                x0, y0, x1, y1 = rect
                inside = moving & (cells % cg.GRID >= x0) & (cells % cg.GRID <= x1) \
                    & (cells // cg.GRID >= y0) & (cells // cg.GRID <= y1)
                walking = inside & ground[mid]
                out["rect_windows"] += int(inside.sum())
                out["rect_walk"] += int(walking.sum())
                out["rect_slopes"].append(np.abs(dz[walking]) / dxy[walking])
    return out


def lines(name: str, m: dict, rect=None) -> list[str]:
    out = [f"{name}: {m['rounds']} rounds; hz {dict(m['hz'])}; {m['segments_with_z']}/{m['segments']} segments with z"]
    for agent in MOVERS:
        casts = {k[1]: v for k, v in sorted(m["casts"].items()) if k[0] == agent}
        out.append(f"  {agent}: {m['agent_rounds'][agent]} rounds; casts recorded: {casts or 'none'}")
    for who in ("movers", "others"):
        if m["speed"][who]:
            sp, up = np.concatenate(m["speed"][who]), np.concatenate(m["up"][who])
            out.append(f"  {who}: along the ground m/s, pct {PCT}: {np.percentile(sp, PCT).round(1).tolist()} "
                       f"(BURST_MPS {hc.BURST_MPS:g})")
            out.append(f"  {who}: upward m/s, pct {PCT}: {np.percentile(up, PCT).round(1).tolist()} "
                       f"(BURST_UP_MPS {hc.BURST_UP_MPS:g})")
    if m["acc"]:
        acc = np.concatenate(m["acc"])
        hist, _ = np.histogram(acc, ACC_BINS)
        out.append(f"  |vertical acceleration| over {hc.WALK_S:g} s while moving, {len(acc)} windows "
                   f"(WALK_ACC_MAX {hc.WALK_ACC_MAX:g} m/s2):")
        out += [f"    {lo:>4g} .. {hi:<4g} {count / len(acc):7.2%}" for count, lo, hi in zip(hist, ACC_BINS, ACC_BINS[1:])]
    if m["moving"]:
        out.append(f"  while moving: {m['ground'] / m['moving']:.1%} of samples are a walk's, {m['air'] / m['moving']:.1%} "
                   f"are in a flight; {len(m['flights'])} flights"
                   + (f", lasting (s) pct 10/50/90: {np.percentile(m['flights'], [10, 50, 90]).round(2).tolist()}"
                      if m["flights"] else ""))
    if m["gravity"]:
        out.append(f"  gravity read off the {len(m['gravity'])} flights of {hc.WALK_S:g} s or more, m/s2 pct 10/50/90: "
                   f"{np.percentile(m['gravity'], [10, 50, 90]).round(1).tolist()} (GRAVITY_MPS2 {hc.GRAVITY_MPS2:g})")
    if rect is not None:
        slopes = np.concatenate(m["rect_slopes"]) if m["rect_slopes"] else np.zeros(0)
        share = m["rect_walk"] / m["rect_windows"] if m["rect_windows"] else 0.0
        out.append(f"  cells {rect}: {m['rect_windows']} moving samples, {share:.1%} are a walk's"
                   + (f"; their slope, pct 50/90/99: {np.percentile(slopes, [50, 90, 99]).round(2).tolist()}"
                      if len(slopes) else ""))
    steep = sorted(m["steep"].items(), key=lambda kv: -len(kv[1]))[:15]
    out.append(f"  steady descent candidates steeper than SLOPE_MAX going down, by cell (x, y, passes): "
               f"{[(c % cg.GRID, c // cg.GRID, len(v)) for c, v in steep]}")
    return out


def main(argv: list[str] | None = None, session_factory=None) -> int:
    import build_control_heights as command

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True)
    parser.add_argument("--blobs-dir", type=Path, help="local round blobs, <match>/<n>.json.gz (default: the database)")
    parser.add_argument("--rect", help="x0,y0,x1,y1 in cells, inclusive: the walk rule's pass rate inside it")
    args = parser.parse_args(argv)
    rect = tuple(int(v) for v in args.rect.split(",")) if args.rect else None
    if rect is not None and len(rect) != 4:
        parser.error("--rect takes x0,y0,x1,y1")
    geo = cg.load_geometry(args.map)
    rounds, _ = command.blob_rounds(args.blobs_dir, args.map) if args.blobs_dir \
        else command.db_rounds(args.map, session_factory)
    for line in lines(args.map, measure(rounds, geo, rect), rect):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

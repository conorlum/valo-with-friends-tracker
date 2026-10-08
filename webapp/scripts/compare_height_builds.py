"""Two height builds of the same maps side by side: how a change of rules is judged
(docs/superpowers/specs/2026-10-05-height-slopes-design.md, "How it is judged").

    .\\.venv\\Scripts\\python.exe scripts\\compare_height_builds.py --old <dir> --new <dir> [--map Sunset]

`--old` and `--new` are folders written by `build_control_heights.py --preview --out <dir>` (a
`<Map>.height.npz` and its `<Map>.height.json` each), built under the two sets of rules **from the same frozen
rounds** (`freeze_height_rounds.py`, then `--blobs-dir`). It reads the files as they are, whatever height
version wrote them, and writes nothing.

A comparison is refused (exit 2, with the reasons) unless both builds say what they read and it is the same:
the same rounds (their identities and blob hashes, as one digest), the same sight and walk masks, the same
preview override; and unless both carry both checks' results. Two builds of different rounds differ for
reasons that have nothing to do with the rules.

For each map it prints:

- each build's supported share, unresolved cells by reason, cells per kind, connections, kill lines blocked
  and must-block result, and whether it is ready;
- **the cells the old build supported that the new one gives no height**, on their own line: they are not in
  the next figure;
- **the flat-ground check**: over the cells supported in the old build that still have a height, the new
  ground minus the old, in metres: the share within FLAT_TOL_M and its percentiles. A wide shift means "the
  lowest wins" is biting on level ground, and LOW_PCT has to move up;
- the cells that gained a height.

Exits 0, or 1 when any map's kill-line share got worse, or 2 when a comparison was refused or no map is in
both folders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

FLAT_TOL_M = 0.2
PCT = [1, 5, 25, 50, 75, 95, 99]
SAME = ("rounds_sha", "rounds", "matches", "sight_sha", "walk_sha", "preview_min_matches")


def read(folder: Path, name: str) -> dict:
    """One build as plain arrays: ground in world dm (NaN for none), supported, unresolved, its report and what
    it says it read (`inputs`, None when the build didn't record it)."""
    with np.load(folder / f"{name}.height.npz") as z:
        meta = json.loads(bytes(z["meta"]).decode("utf-8"))
        floors = z["floors"].astype(float)
        ground = np.where(floors[..., 0] >= 0, floors[..., 0] + meta.get("origin_z", 0), np.nan)
        out = {"ground": ground, "supported": z["supported"].astype(bool), "unresolved": z["unresolved"].astype(bool),
               "version": meta.get("version"), "edges": int(len(z["edges"]))}
    path = folder / f"{name}.height.json"
    wrapper = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    out["report"], out["inputs"] = wrapper.get("height") or {}, wrapper.get("inputs")
    return out


def refusals(old: dict, new: dict) -> list[str]:
    """Why the two builds can't be compared; empty when they can."""
    why = []
    for label, build in (("old", old), ("new", new)):
        inputs = build["inputs"]
        if not isinstance(inputs, dict) or not inputs.get("rounds_sha"):
            why.append(f"the {label} build doesn't say which rounds it read (build it with --blobs-dir on the "
                       f"frozen folder)")
        for check in ("kill_lines", "must_block"):
            if not isinstance(build["report"].get(check), dict) or "passes" not in build["report"][check]:
                why.append(f"the {label} build has no {check} result")
    if not why:
        for key in SAME:
            if old["inputs"].get(key) != new["inputs"].get(key):
                why.append(f"{key} differs: old {old['inputs'].get(key)!r}, new {new['inputs'].get(key)!r}")
    return why


def flat_ground(old: dict, new: dict) -> dict:
    """The new ground minus the old (m) over the old build's supported cells that still have a height, and how
    many of the old build's supported cells have none now."""
    was = old["supported"] & ~np.isnan(old["ground"])
    both = was & ~np.isnan(new["ground"])
    diff = (new["ground"][both] - old["ground"][both]) / 10.0
    out = {"cells": int(len(diff)), "lost": int((was & np.isnan(new["ground"])).sum()), "within": None, "pct": []}
    if len(diff):
        out.update({"within": float((np.abs(diff) <= FLAT_TOL_M + 1e-9).mean()),
                    "pct": np.percentile(diff, PCT).round(2).tolist()})
    return out


def summary(build: dict) -> str:
    r = build["report"]
    kills, must = r.get("kill_lines") or {}, r.get("must_block") or {}
    kinds = r.get("cells_by_kind")
    parts = [f"v{build['version']}", f"supported {r.get('supported', float('nan')):.1%}",
             f"unresolved {r.get('unresolved_cells')} {r.get('unresolved_why')}",
             f"kinds {kinds}" if kinds else f"filled {r.get('filled_cells')}",
             f"connections {build['edges']} ({r.get('one_way_edges')} one-way)",
             f"kill lines {kills.get('blocked')}/{kills.get('qualifying')} ({kills.get('share', 0):.2%})",
             f"must-block {must.get('blocked')}/{must.get('checked')} of {must.get('lines')}",
             "READY" if r.get("ready") else "not ready: " + "; ".join(r.get("not_ready") or [])]
    return "; ".join(parts)


def compare(name: str, old: dict, new: dict) -> tuple[list[str], bool]:
    """(the printed lines, whether the kill-line share got worse). Call `refusals` first."""
    flat = flat_ground(old, new)
    had, has = ~np.isnan(old["ground"]), ~np.isnan(new["ground"])
    inputs = new["inputs"]
    out = [f"{name}: {inputs['rounds']} rounds of {inputs['matches']} matches (rounds {inputs['rounds_sha']}, walk mask "
           f"{inputs['walk_sha']}" + (f", floors from {inputs['preview_min_matches']} match(es)"
                                      if inputs.get("preview_min_matches") else "") + ")",
           f"  old: {summary(old)}", f"  new: {summary(new)}",
           f"  supported before, no height now: {flat['lost']} cells"]
    if flat["cells"]:
        out.append(f"  flat ground: {flat['cells']} cells supported before that still have a height, "
                   f"{flat['within']:.1%} within {FLAT_TOL_M} m; new - old, pct {PCT}: {flat['pct']}")
    else:
        out.append("  flat ground: no cell supported in the old build has a height in the new one")
    out.append(f"  heights gained {int((has & ~had).sum())} cells, lost {int((had & ~has).sum())} in all")
    was, now = old["report"]["kill_lines"].get("share"), new["report"]["kill_lines"].get("share")
    worse = was is not None and now is not None and now > was
    if worse:
        out.append(f"  WORSE: kill lines blocked went from {was:.2%} to {now:.2%}")
    return out, worse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--map", action="append", help="only this map (repeatable)")
    args = parser.parse_args(argv)
    names = sorted({p.name[: -len(".height.npz")] for p in args.old.glob("*.height.npz")}
                   & {p.name[: -len(".height.npz")] for p in args.new.glob("*.height.npz")})
    names = [n for n in names if not args.map or n in args.map]
    if not names:
        print(f"no map has a build in both {args.old} and {args.new}", flush=True)
        return 2
    code = 0
    for name in names:
        old, new = read(args.old, name), read(args.new, name)
        why = refusals(old, new)
        if why:
            print(f"{name}: REFUSED, not comparable", flush=True)
            for reason in why:
                print(f"  {reason}", flush=True)
            code = 2
            continue
        lines, worse = compare(name, old, new)
        if worse and code == 0:
            code = 1
        for line in lines:
            print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

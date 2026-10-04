"""Detects each map's chokes and merges them into its `<Map>.chokes.json`, keeping hand edits
(app/control/chokes.py; docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 2).

    .\\.venv\\Scripts\\python.exe scripts\\build_chokes.py            # every map with control masks
    .\\.venv\\Scripts\\python.exe scripts\\build_chokes.py Ascent     # one map
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def build_chokes(name: str) -> tuple[int, int, Path]:
    """Detect, merge and save one map's chokes. Returns (live count, hand count, path)."""
    from app.control import chokes, geometry
    from app.replays import choke_assets

    geo = geometry.load_geometry(name)
    merged, next_id = choke_assets.merge(
        choke_assets.load(name) or [], chokes.detect(geo), choke_assets.load_next_id(name))
    path = choke_assets.save(name, merged, next_id)
    live = [c for c in merged if not c.deleted]
    return len(live), sum(c.source == "hand" for c in live), path


def main(argv: list[str] | None = None) -> int:
    from app.control import geometry

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("maps", nargs="*")
    args = parser.parse_args(argv)
    names = args.maps or sorted(p.name.split(".")[0] for p in geometry.ASSET_DIR.glob("*.walk.png"))
    for name in names:
        t0 = time.perf_counter()
        count, hand, path = build_chokes(name)
        print(f"{name}: {count} chokes ({hand} by hand) -> {path.name} [{time.perf_counter() - t0:.1f}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

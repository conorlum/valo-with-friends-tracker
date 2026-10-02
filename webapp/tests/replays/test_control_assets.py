"""The committed map-control geometry (app/static/data/control/, built by
scripts/build_control_geometry.py): fresh against its inputs, and passing Risk 1 on every map with
replays (docs/replay-map-control-plan.md: 2% or less of kill lines blocked)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.control import geometry as cg

KILL_LINES = Path(__file__).resolve().parents[1] / "fixtures" / "control" / "kill_lines.json"
LINES = json.loads(KILL_LINES.read_text(encoding="utf-8"))["maps"]
TAGS = cg.load_tags()["maps"]
INDEX = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))
MAPS = sorted(p.stem for p in cg.MINIMAP_DIR.glob("*.png"))


def rgba(name: str) -> np.ndarray:
    return np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))


def blocked_share(sight: np.ndarray, lines: list) -> float:
    return sum(cg.line_blocked(sight, (ax, ay), (bx, by)) for ax, ay, bx, by in lines) / len(lines)


@pytest.mark.parametrize("name", MAPS)
def test_committed_masks_match_a_fresh_build(name):
    fresh = cg.masks(rgba(name), TAGS.get(name, {}))
    assert (cg.read_mask_png(cg.ASSET_DIR / f"{name}.sight.png") == fresh.sight).all()
    assert (cg.read_mask_png(cg.ASSET_DIR / f"{name}.walk.png") == fresh.walk).all()


@pytest.mark.parametrize("name", sorted(LINES))
def test_kill_lines_pass_risk1(name):
    geo = cg.load_geometry(name)
    assert blocked_share(geo.sight, LINES[name]) <= 0.02


def test_abyss_passes_only_with_its_see_across_tags():
    assert blocked_share(cg.load_geometry("Abyss").sight, LINES["Abyss"]) == 0
    assert blocked_share(cg.masks(rgba("Abyss"), {}).sight, LINES["Abyss"]) > 0.02


def test_the_index_lists_every_map_with_the_badge_flag():
    assert sorted(INDEX["maps"]) == MAPS
    for name, row in INDEX["maps"].items():
        assert row["cover_reviewed"] is False   # nothing tagged as cover yet: the badge shows
        assert row["specials"] == []
        assert (row["kill_lines"] is not None) == (name in LINES)
    assert all(INDEX["maps"][n]["kill_lines"]["passes"] for n in LINES)


def test_a_rebuilt_index_entry_keeps_its_height_fields_and_warns_when_the_walk_mask_moved():
    # scripts/build_control_heights.py writes `height_sha` and `height` into a map's entry; rebuilding the
    # masks must not drop them (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 3).
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import build_control_geometry as build

    previous = {"sight_sha": "old", "walk_sha": "aaa", "height_sha": "0123456789ab",
                "height": {"supported": 0.7, "walk_sha": "aaa"}}
    row = {"sight_sha": "new", "walk_sha": "aaa"}
    assert build.keep_heights("Ascent", row, previous) == []
    assert row == {"sight_sha": "new", "walk_sha": "aaa", "height_sha": "0123456789ab",
                   "height": {"supported": 0.7, "walk_sha": "aaa"}}
    moved = {"sight_sha": "new", "walk_sha": "bbb"}
    [warning] = build.keep_heights("Ascent", moved, previous)
    assert warning.startswith("WARNING Ascent") and "aaa" in warning and "bbb" in warning
    assert moved["height_sha"] == "0123456789ab", "kept all the same: the unknown cells fall back to 2D"
    plain = {"sight_sha": "new", "walk_sha": "bbb"}
    assert build.keep_heights("Bind", plain, {"sight_sha": "old"}) == [] and "height_sha" not in plain

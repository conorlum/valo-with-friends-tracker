"""The committed map-control geometry (app/static/data/control/, built by
scripts/build_control_geometry.py): fresh against its inputs, and passing Risk 1 on every map with
replays (docs/replay-map-control-plan.md: 2% or less of kill lines blocked)."""

import hashlib
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


def test_a_kill_line_with_an_end_on_a_cover_box_is_not_judged():
    # 2026-10-26: a player standing on top of a box shoots over it; the flat test can't tell them from one behind it.
    sight = np.zeros((cg.PX, cg.PX), bool)
    sight[100:110, 100:110] = True                       # a box, also cover
    lines = [[90, 105, 120, 105],                        # through the box, both ends on the ground: blocked
             [105, 105, 130, 105],                       # from on top of it: not judged
             [10, 10, 50, 10]]                           # clear
    assert cg.kill_line_result(sight, lines)["blocked"] == 2
    got = cg.kill_line_result(sight, lines, cover=sight)
    assert (got["qualifying"], got["on_cover"], got["blocked"]) == (2, 1, 1)


def test_a_cover_tag_is_the_box_inside_its_drawn_outline():
    # Summit's tagged boxes: the minimap's outline pixels stay sight-open; only what they enclose is cover.
    m = cg.masks(rgba("Summit"), TAGS["Summit"])
    shapes = cg.tag_shapes(rgba("Summit"), TAGS["Summit"])["cover"]
    p = {**cg.DETECTOR_DEFAULTS, **(TAGS["Summit"].get("params") or {})}
    outline = cg._base_masks(rgba("Summit"), p["glyph_saturation"], p["line_lum"])["line"]
    assert (shapes & outline).any() and not (m.cover & outline).any()
    assert (m.cover == (shapes & ~outline)).all()


@pytest.mark.parametrize("name", MAPS)
def test_committed_masks_match_a_fresh_build(name):
    fresh = cg.masks(rgba(name), TAGS.get(name, {}))
    assert (cg.read_mask_png(cg.ASSET_DIR / f"{name}.sight.png") == fresh.sight).all()
    assert (cg.read_mask_png(cg.ASSET_DIR / f"{name}.walk.png") == fresh.walk).all()


# Ascent's traced A11 boxes join wall 11; the A17 boxes form one new piece, separate from wall 17.
COVER_PIECES = {"Summit": 28, "Ascent": 18, "Sunset": 1}


def test_ascent_traced_boxes():
    labels = cg.load_geometry("Ascent", height_mode="flat").cover_px
    assert labels[538, 437] == labels[535, 441] == labels[537, 450] != 0
    assert labels[758, 329] == labels[758, 340] != 0
    assert labels[749, 330] != 0
    assert labels[758, 329] != labels[749, 330]
    assert labels[550, 445] == 0


def test_ascent_paint_is_exactly_the_traced_cells():
    tags = json.loads((cg.ASSET_DIR / "tags.json").read_text(encoding="utf-8"))
    cells = cg.unpack_paint(tags["maps"]["Ascent"]["cover_paint"])[::4, ::4].copy()
    assert cells[133:137, 109].all()
    assert cells[132:136, 111:115].all()
    assert not cells[136:141, 111].any()
    assert cells[189:191, 81:86].all()
    # x86 dropped by owner ruling 2026-11-13: it clipped a real kill line
    assert not cells[189:191, 86].any()
    cells[133:137, 109] = False
    cells[132:136, 111:115] = False
    cells[136:141, 111] = False
    cells[189:191, 81:86] = False
    # Recorded from origin/main before W1's edit by the scratch ascent_paint.py script;
    # recomputed with the amended rectangle (x81-85): unchanged, since x86 was unset.
    assert hashlib.sha256(cells.tobytes()).hexdigest() == (
        "0b5eadb940ca459fee7f3fa84bf87d277b31294e501ab4308de7b6c0ee8ebfd3")
    del tags["maps"]["Ascent"]["cover_paint"]
    assert hashlib.sha256(json.dumps(tags, sort_keys=True).encode("utf-8")).hexdigest() == (
        "a64e8e0d542cdedb58fbe5d6cf66d7924e336734568898fb80ce2fd9d99af0e1")


def test_ascent_without_traced_cells_keeps_original_masks():
    entry = dict(TAGS["Ascent"])
    cells = cg.unpack_paint(entry["cover_paint"])[::4, ::4].copy()
    cells[133:137, 109] = False
    cells[132:136, 111:115] = False
    cells[136:141, 111] = True
    cells[189:191, 81:86] = False
    entry["cover_paint"] = cg.pack_paint(cells)
    image = rgba("Ascent")
    original = cg.masks(image, entry)
    # Committed asset identities before R1: switching off the trace gives the old masks.
    for mask, expected in (
        (original.sight, "06147e83b898"),
        (original.walk, "12861b1023d3"),
        (cg.see_over_cover(image, entry), "48afd9eee5e9"),
    ):
        assert hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()[:12] == expected


def _paint_cells(*cells):
    grid = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    for r, c in cells:
        grid[r, c] = True
    return cg.pack_paint(grid)


@pytest.mark.parametrize("name", MAPS)
def test_cover_is_its_own_asset_and_sight_is_wall_plus_cover(name):
    path = cg.ASSET_DIR / f"{name}.cover.png"
    fresh = cg.see_over_cover(rgba(name), TAGS.get(name, {}))
    m = cg.masks(rgba(name), TAGS.get(name, {}))
    geo = cg.load_geometry(name, height_mode="flat")
    row = INDEX["maps"][name]
    if not fresh.any():
        assert not path.exists() and "cover_sha" not in row
        assert geo.cover_px is None and geo.cover_sha is None
        return
    png = cg.read_mask_png(path)
    assert (png == fresh).all()
    entry = TAGS.get(name, {})
    base = cg._base_masks(rgba(name), cg.GLYPH_SATURATION, cg.DETECTOR_DEFAULTS["line_lum"])
    see_across = cg.tag_shapes(rgba(name), entry)["seeacross"].copy()
    if entry.get("see_across_paint"):
        see_across |= cg.unpack_paint(entry["see_across_paint"])
    wall_without_cover = ~base["opaque"] & ~see_across
    assert (m.sight == (wall_without_cover | png)).all()
    assert row["cover_sha"] == geo.cover_sha
    assert (geo.cover_px > 0).sum() == png.sum() and (geo.cover_px[~png] == 0).all()
    assert geo.cover_px.dtype == np.int16


@pytest.mark.parametrize("name,pieces", sorted(COVER_PIECES.items()))
def test_cover_piece_counts(name, pieces):
    assert cg.load_geometry(name, height_mode="flat").cover_px.max() == pieces


def test_cover_pieces_are_four_connected():
    mask = np.zeros((cg.PX, cg.PX), bool)
    mask[10, 10] = mask[11, 11] = True                    # touch at a corner only
    geo = cg.attach_cover(cg.load_geometry("Ascent", height_mode="flat"), mask, "x")
    assert geo.cover_px.max() == 2
    assert not geo.see_over_mask(0).any()
    assert geo.see_over_mask(1).sum() == 1 and not (geo.see_over_mask(1) & geo.see_over_mask(2)).any()
    mask[10, 11] = True                                   # now joined
    assert cg.attach_cover(geo, mask, "x").cover_px.max() == 1


def test_attach_cover_with_no_mask_leaves_none():
    geo = cg.attach_cover(cg.load_geometry("Bind", height_mode="flat"), None, None)
    assert geo.cover_px is None and geo.cover_sha is None


def test_a_cover_outline_over_a_wall_pixel_is_not_see_over_cover():
    # all transparent = all wall; a cover paint over it adds no see-over pixels.
    wall = np.zeros((cg.PX, cg.PX, 4), np.uint8)
    paint = {"cover_paint": _paint_cells((0, 0))}
    assert not cg.see_over_cover(wall, paint).any()
    assert cg.masks(wall, paint).cover[:4, :4].all()      # the cover mask itself is unchanged
    floor = wall.copy()
    floor[:8, :8] = (60, 60, 60, 255)                     # open ground in the corner
    got = cg.see_over_cover(floor, paint)
    assert got[:4, :4].all() and got.sum() == 16


@pytest.mark.parametrize("name", sorted(LINES))
def test_kill_lines_pass_risk1(name):
    m = cg.masks(rgba(name), TAGS.get(name, {}))
    assert cg.kill_line_result(cg.load_geometry(name).sight, LINES[name], m.cover)["passes"]


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

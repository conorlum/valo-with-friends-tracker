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
    entry = {k: v for k, v in TAGS["Summit"].items() if k != "cover_paint"}
    m = cg.masks(rgba("Summit"), entry)
    shapes = cg.tag_shapes(rgba("Summit"), entry)["cover"]
    p = {**cg.DETECTOR_DEFAULTS, **(TAGS["Summit"].get("params") or {})}
    outline = cg._base_masks(rgba("Summit"), p["glyph_saturation"], p["line_lum"])["line"]
    assert (shapes & outline).any() and not (m.cover & outline).any()
    assert (m.cover == (shapes & ~outline)).all()


@pytest.mark.parametrize("name", MAPS)
def test_committed_masks_match_a_fresh_build(name):
    fresh = cg.masks(rgba(name), TAGS.get(name, {}))
    assert (cg.read_mask_png(cg.ASSET_DIR / f"{name}.sight.png") == fresh.sight).all()
    assert (cg.read_mask_png(cg.ASSET_DIR / f"{name}.walk.png") == fresh.walk).all()


# Padded walls split Ascent's traced A11 cover into three pieces; A17 stays separate from wall 17.
COVER_PIECES = {'Abyss': 36, 'Ascent': 48, 'Bind': 25, 'Breeze': 26, 'Corrode': 29, 'Fracture': 30, 'Haven': 44, 'Lotus': 25, 'Pearl': 25, 'Split': 35, 'Summit': 31, 'Sunset': 29}


def test_ascent_traced_boxes():
    from scipy import ndimage

    entry = {k: v for k, v in TAGS["Ascent"].items() if k != "tags"}
    labels, _ = ndimage.label(cg.see_over_cover(rgba("Ascent"), entry))
    masks = cg.masks(rgba("Ascent"), TAGS["Ascent"])
    assert masks.cover[538, 437] and masks.cover[535, 441] and masks.cover[537, 450]
    assert labels[538, 437] != 0 and labels[537, 450] != 0
    assert labels[538, 437] != labels[537, 450]
    assert labels[535, 441] == 0 and masks.wall[535, 441]
    assert labels[758, 329] == labels[758, 340] != 0
    assert labels[749, 330] != 0
    assert labels[758, 329] != labels[749, 330]
    assert labels[550, 445] == 0
    committed = cg.load_geometry("Ascent", height_mode="flat").cover_px
    assert all(committed[y, x] != 0 for y, x in ((538, 437), (537, 450), (758, 329),
                                                 (758, 340), (749, 330)))


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
    # W1 review paint outside the traced rectangles (including the mixed-shape additions).
    assert hashlib.sha256(cells.tobytes()).hexdigest() == (
        "c0fc187d8ec389641d7fb3f376ff9035e6bb00b50c5d9f87c125d32a3117abe8")
    del tags["maps"]["Ascent"]["cover_paint"]
    # R1.1 permits only Abyss's qualified rope-domain subsets. The owner-review
    # regression pins those; restore them here to retain these original broad pins.
    from tests.replays.test_owner_map_reviews import original_rope_domain
    for route in tags["maps"]["Abyss"]["map_features"]["routes"]:
        if "walking_domain" in route:
            route["walking_domain"] = original_rope_domain(route)
    # R2.1 clears only Summit's Heaven height step. Its owner-review regression
    # pins that change; restore the original paint for these broad Ascent pins.
    summit = cg.unpack_paint(tags["maps"]["Summit"]["cover_paint"])[::4, ::4].copy()
    summit[46:60, 51] = True
    summit[60:64, 41] = True
    tags["maps"]["Summit"]["cover_paint"] = cg.pack_paint(summit)
    assert hashlib.sha256(json.dumps(tags, sort_keys=True).encode("utf-8")).hexdigest() == (
        "ca9b31cea8a20723fc635b05871aa98e927c1b1d0721849cd98ef4280c321d37")
    # Also pin the remaining tag inputs with Haven's review disabled.
    del tags["maps"]["Haven"]["tags"]
    del tags["maps"]["Haven"]["cover_paint"]
    tags["maps"]["Haven"]["cover_reviewed"] = False
    assert hashlib.sha256(json.dumps(tags, sort_keys=True).encode("utf-8")).hexdigest() == (
        "00d3e41503c946df23889d8b404eb42028c6ae241c419c45336e1a90181f1e77")


@pytest.mark.parametrize("padding", [True, False])
def test_ascent_without_traced_cells_keeps_original_masks(monkeypatch, padding):
    if not padding:
        monkeypatch.setattr(cg, "thin_padding", lambda wall, exempt: np.zeros_like(wall))
    entry = dict(TAGS["Ascent"])
    cells = cg.unpack_paint(entry["cover_paint"])[::4, ::4].copy()
    cells[133:137, 109] = False
    cells[132:136, 111:115] = False
    cells[136:141, 111] = True
    cells[189:191, 81:86] = False
    entry["cover_paint"] = cg.pack_paint(cells)
    image = rgba("Ascent")
    original = cg.masks(image, entry)
    # W1 review pins with the traced rectangles disabled; both padding modes are pinned.
    for mask, expected in (
        (original.sight, "3e67a7949e95" if padding else "729e3b48fd78"),
        (original.walk, "b8397298651d"),
        (cg.see_over_cover(image, entry), "eba279d3e56a" if padding else "901d96a71123"),
    ):
        assert hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()[:12] == expected


def _paint_cells(*cells):
    grid = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    for r, c in cells:
        grid[r, c] = True
    return cg.pack_paint(grid)


@pytest.mark.parametrize("name", MAPS)
def test_sight_is_wall_plus_cover(name):
    image, entry = rgba(name), TAGS.get(name, {})
    m = cg.masks(image, entry)
    base = cg._base_masks(image, cg.GLYPH_SATURATION, cg.DETECTOR_DEFAULTS["line_lum"])
    see_across = cg.tag_shapes(image, entry)["seeacross"].copy()
    if entry.get("see_across_paint"):
        see_across |= cg.unpack_paint(entry["see_across_paint"])
    assert (m.sight == (m.wall | m.cover)).all()
    assert (cg.see_over_cover(image, entry) == (m.cover & ~m.wall)).all()
    assert m.wall[~base["opaque"] & ~see_across].all()


@pytest.mark.parametrize("name", MAPS)
def test_committed_cover_matches_a_fresh_build(name):
    path = cg.ASSET_DIR / f"{name}.cover.png"
    fresh = cg.see_over_cover(rgba(name), TAGS.get(name, {}))
    geo = cg.load_geometry(name, height_mode="flat")
    row = INDEX["maps"][name]
    if not fresh.any():
        assert not path.exists() and "cover_sha" not in row
        assert geo.cover_px is None and geo.cover_sha is None
        return
    png = cg.read_mask_png(path)
    assert (png == fresh).all()
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
    geo = cg.attach_cover(cg.load_geometry("Icebox", height_mode="flat"), None, None)
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
    result = cg.kill_line_result(m.gate_wall, LINES[name], m.cover)
    assert result["passes"]
    assert INDEX["maps"][name]["kill_lines"] == result
    assert INDEX["maps"][name]["kill_lines_with_walls"] == cg.kill_line_result(m.wall, LINES[name], m.cover)


def test_abyss_passes_only_with_its_see_across_tags():
    entry = {"tags": [t for t in TAGS["Abyss"]["tags"] if t["tag"] == "seeacross"],
             "see_across_paint": TAGS["Abyss"]["see_across_paint"]}
    assert blocked_share(cg.masks(rgba("Abyss"), entry).gate_wall, LINES["Abyss"]) == 0
    assert blocked_share(cg.masks(rgba("Abyss"), {}).gate_wall, LINES["Abyss"]) > 0.02


def test_the_index_lists_every_map_with_the_badge_flag():
    assert sorted(INDEX["maps"]) == MAPS
    for name, row in INDEX["maps"].items():
        assert row["cover_reviewed"] is (name != "Icebox")
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

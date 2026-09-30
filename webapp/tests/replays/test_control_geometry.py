"""Stage 1 geometry (app/control/geometry.py) on a synthetic minimap: masks from alpha, glyphs and
tags; the kill-line rule; the corner tolerance; the visibility cache."""

import base64

import numpy as np
import pytest

from app.control import geometry as cg
from tests.replays.control_toys import rect_mask


def minimap() -> np.ndarray:
    """Floor 100..900 with: an alpha-0 wall (x 500-520, y 100-600); a light-outlined box
    (200..260); a saturated glyph (700..720); an inner void (300..340, 600..640)."""
    rgba = np.zeros((cg.PX, cg.PX, 4), np.uint8)
    rgba[100:900, 100:900] = (80, 80, 80, 255)
    rgba[100:600, 500:520, 3] = 0
    rgba[200:262, 200:262] = (200, 200, 200, 255)
    rgba[202:260, 202:260] = (80, 80, 80, 255)
    rgba[700:720, 700:720] = (40, 200, 40, 255)
    rgba[600:640, 300:340, 3] = 0
    return rgba


def candidate(rgba, kind, near):
    _, found = cg.candidates(rgba, {})
    x, y = near
    hits = [c for c in found if c["kind"] == kind and c["bbox"][0] <= x <= c["bbox"][2] and c["bbox"][1] <= y <= c["bbox"][3]]
    assert len(hits) == 1, found
    return hits[0]


def tagged(rgba, kind, near, tag):
    c = candidate(rgba, kind, near)
    return {"tags": [{"id": c["id"], "tag": tag, "kind": c["kind"], "bbox": c["bbox"], "px": c["px"]}]}


def test_clear_line_and_a_line_through_a_wall():
    m = cg.masks(minimap())
    assert not cg.line_blocked(m.sight, (150, 150), (450, 150))
    assert cg.line_blocked(m.sight, (450, 300), (600, 300))
    assert not m.walk[300, 510] and m.walk[300, 450]


def test_cover_blocks_and_see_over_does_not():
    rgba = minimap()
    line = ((180, 231), (285, 231))
    assert not cg.line_blocked(cg.masks(rgba).sight, *line)       # untagged: nothing blocks until tagged
    cover = cg.masks(rgba, tagged(rgba, "closed", (230, 230), "cover"))
    assert cg.line_blocked(cover.sight, *line)
    assert not cover.walk[230, 230]
    over = cg.masks(rgba, tagged(rgba, "closed", (230, 230), "seeover"))
    assert not cg.line_blocked(over.sight, *line)
    assert over.walk[230, 230]


def test_see_across_tag_and_paint_open_a_void_but_keep_it_unwalkable():
    rgba = minimap()
    line = ((280, 620), (360, 620))
    assert cg.line_blocked(cg.masks(rgba).sight, *line)
    tag = cg.masks(rgba, tagged(rgba, "void", (320, 620), "seeacross"))
    assert not cg.line_blocked(tag.sight, *line)
    assert not tag.walk[620, 320]
    cells = np.zeros((cg.PAINT_GRID, cg.PAINT_GRID), bool)
    cells[600 // 4:640 // 4, 300 // 4:340 // 4] = True
    paint = base64.b64encode(np.packbits(cells.ravel().astype(np.uint8), bitorder="little").tobytes()).decode()
    painted = cg.masks(rgba, {"see_across_paint": paint})
    assert not cg.line_blocked(painted.sight, *line)
    assert not painted.walk[620, 320]


def test_a_glyph_is_a_wall_and_not_walkable():
    m = cg.masks(minimap())
    assert m.sight[710, 710] and not m.walk[710, 710]


def test_a_tag_whose_shape_moved_refuses():
    rgba = minimap()
    entry = tagged(rgba, "closed", (230, 230), "cover")
    entry["tags"][0]["bbox"] = [0, 0, 1, 1]
    with pytest.raises(cg.GeometryError):
        cg.masks(rgba, entry)
    entry["tags"][0]["tag"] = "door"
    with pytest.raises(cg.GeometryError):
        cg.masks(rgba, entry)


def test_the_corner_tolerance_lets_a_thin_clip_through():
    floor = rect_mask([(100, 100, 400, 300)])
    thin = rect_mask([(200, 100, 201, 300)])         # one pixel: less than 0.3 m
    thick = rect_mask([(200, 100, 212, 300)])        # 12 px, about 1.7 m
    for wall, seen in ((thin, True), (thick, False)):
        geo = cg.geometry_from_masks("T", ~floor | wall, floor & ~wall, 7e-5)
        assert geo.tol_hits >= 1
        view = cg.cast(geo, 150.0, 200.0, np.array([0.0]), [])
        assert bool(view[geo.cell_of_px(300, 200)]) is seen


def test_visibility_is_cached_by_mask_and_parameters(tmp_path):
    floor = rect_mask([(400, 400, 440, 440)])
    geo = cg.geometry_from_masks("T", ~floor, floor, 7e-5)
    cg.visibility(geo, tmp_path)
    assert geo.visibility_source == "built"
    again = cg.geometry_from_masks("T", ~floor, floor, 7e-5)
    cg.visibility(again, tmp_path)
    assert again.visibility_source == "cache"
    assert (again.rows == geo.rows).all() and (again.row_of == geo.row_of).all()
    wall = rect_mask([(420, 400, 421, 440)])
    changed = cg.geometry_from_masks("T", ~floor | wall, floor, 7e-5)
    cg.visibility(changed, tmp_path)
    assert changed.visibility_source == "built"
    # a cell sees itself and its room
    c = geo.cell_of_px(404, 404)
    row = np.unpackbits(geo.rows[geo.row_of[c]])[: cg.GRID * cg.GRID].astype(bool)
    assert row[c] and row[geo.cell_of_px(436, 436)]

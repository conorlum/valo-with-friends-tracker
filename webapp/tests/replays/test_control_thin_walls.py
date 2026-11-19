"""Integer mask-build rules for thin void walls and tagged line walls."""

import json

import numpy as np
import pytest
from PIL import Image

from app.control import geometry as cg
from tests.replays.test_control_geometry import tagged, paint_rect


def floor():
    return np.full((cg.PX, cg.PX, 4), (128, 128, 128, 255), np.uint8)


def rectangle(x0, y0, x1, y1):
    out = np.zeros((cg.PX, cg.PX), bool)
    out[y0:y1, x0:x1] = True
    return out


@pytest.mark.parametrize("width,before,after", [(1, 1, 2), (2, 1, 1), (3, 0, 1), (4, 0, 0)])
@pytest.mark.parametrize("transpose", [False, True])
def test_padding_is_across_the_wall_only(width, before, after, transpose):
    rgba = floor()
    raw = rectangle(100, 100, 100 + width, 140)
    expected = rectangle(100 - before, 100, 100 + width + after, 140)
    if transpose:
        raw, expected = raw.T, expected.T
    rgba[raw] = (0, 0, 0, 0)
    m = cg.masks(rgba)
    assert np.array_equal(m.wall, expected)
    assert np.array_equal(m.gate_wall, expected)
    assert np.array_equal(m.walk, ~raw)


@pytest.mark.parametrize("length,padded", [(10, False), (12, True)])
def test_minimum_piece_length(length, padded):
    rgba = floor()
    raw = rectangle(100, 100, 101, 100 + length)
    rgba[raw] = (0, 0, 0, 0)
    expected = rectangle(99, 100, 103, 100 + length) if padded else raw
    assert np.array_equal(cg.masks(rgba).wall, expected)


@pytest.mark.parametrize("transpose", [False, True])
@pytest.mark.parametrize("edge", [0, cg.PX - 1])
def test_a_run_touching_the_border_is_not_thin(transpose, edge):
    raw = rectangle(edge, 100, edge + 1, 140)
    if transpose:
        raw = raw.T
    assert not cg.thin_padding(raw, []).any()


@pytest.mark.parametrize("last", [119, 131, 139])
def test_exempt_boxes_drop_only_the_pixels_inside_the_inclusive_box(last):
    rgba = floor()
    raw = rectangle(100, 100, 101, 140)
    rgba[raw] = (0, 0, 0, 0)
    expected = raw | rectangle(99, last + 1, 103, 140)
    m = cg.masks(rgba, {"thin_wall_exempt": [[100, 100, 100, last]]})
    assert np.array_equal(m.wall, expected)


def test_directional_pieces_are_eight_connected_and_not_pooled():
    raw = np.zeros((cg.PX, cg.PX), bool)
    coords = np.arange(100, 112)
    raw[coords, coords] = True
    expected = raw.copy()
    for shift in (-1, 1, 2):
        expected[coords, coords + shift] = True
        expected[coords + shift, coords] = True
    assert np.array_equal(raw | cg.thin_padding(raw, []), expected)
    raw = rectangle(100, 100, 101, 110) | rectangle(100, 120, 101, 130)
    assert not cg.thin_padding(raw, []).any()


def test_see_across_paint_removes_padding_but_never_cover():
    rgba = floor()
    rgba[100:140, 100] = (0, 0, 0, 0)
    paint = paint_rect(96, 108, 100, 124)
    entry = {"see_across_paint": paint, "cover_paint": paint}
    m = cg.masks(rgba, entry)
    expected = rectangle(99, 100, 103, 140) & ~cg.unpack_paint(paint)
    assert np.array_equal(m.wall, expected)
    assert np.array_equal(m.sight, m.wall | m.cover)
    assert np.array_equal(cg.see_over_cover(rgba, entry), m.cover & ~m.wall)


@pytest.mark.parametrize("wall_x,end", [(None, 120), (123, 126), (124, 127), (125, 120)])
def test_tag_body_and_join_distance(wall_x, end):
    line = rectangle(100, 50, 120, 51)
    base = np.zeros_like(line) if wall_x is None else rectangle(wall_x, 30, cg.PX, 70)
    out = cg.wall_tag_pixels(line, base)
    assert np.array_equal(out, rectangle(100, 48, end, 53))
    if wall_x == 123:
        assert out.sum() == 130


def test_one_pixel_tag_keeps_its_full_square():
    raw = rectangle(100, 50, 101, 51)
    assert np.array_equal(cg.wall_tag_pixels(raw, np.zeros_like(raw)), rectangle(98, 48, 103, 53))


def test_tied_farthest_pair_uses_row_major_first_end_then_second_end():
    raw = np.zeros((cg.PX, cg.PX), bool)
    raw[[50, 50, 52, 52], [100, 102, 100, 102]] = True
    # Diagonals tie: e1=(100,50), e2=(102,52), rather than (102,50)->(100,52).
    yy, xx = np.indices(raw.shape)
    grown = rectangle(98, 48, 105, 55)
    expected = grown & (xx + yy >= 150) & (xx + yy <= 154)
    assert np.array_equal(cg.wall_tag_pixels(raw, np.zeros_like(raw)), expected)


def test_join_ties_use_lowest_y_then_x_and_integer_bresenham():
    raw = rectangle(100, 50, 120, 51)
    base = np.zeros_like(raw)
    base[46, 122] = base[54, 122] = True  # distance sqrt(25) from the second end
    expected = rectangle(100, 48, 120, 53)
    # Reference Bresenham from (119,50) to the row-major winner (122,46).
    for x, y in [(119, 50), (120, 49), (121, 48), (121, 47), (122, 46)]:
        expected[y - 2:y + 3, x - 2:x + 3] = True
    assert np.array_equal(cg.wall_tag_pixels(raw, base), expected)


def test_diagonal_has_no_end_spill_and_stops_500_sampled_rays():
    raw = np.zeros((cg.PX, cg.PX), bool)
    raw[np.arange(50, 70), np.arange(100, 120)] = True
    out = cg.wall_tag_pixels(raw, np.zeros_like(raw))
    yy, xx = np.nonzero(out)
    assert ((xx - 100) + (yy - 50) >= 0).all()
    assert ((xx - 119) + (yy - 69) <= 0).all()
    rng = np.random.default_rng(812)
    for angle, interior in zip(rng.uniform(0, 2 * np.pi, 500), rng.integers(2, 18, 500)):
        offsets = np.arange(-20, 21, 2)
        xs = np.rint(100 + interior + offsets * np.cos(angle)).astype(int)
        ys = np.rint(50 + interior + offsets * np.sin(angle)).astype(int)
        assert out[ys, xs].sum() > 1


def test_wall_tags_are_walkable_and_do_not_enter_the_gate():
    rgba = floor()
    rgba[50, 100:120] = (230, 230, 230, 255)
    entry = tagged(rgba, "line", (110, 50), "wall")
    expected = cg.wall_tag_pixels(rectangle(100, 50, 120, 51), np.zeros((cg.PX, cg.PX), bool))
    m = cg.masks(rgba, entry)
    assert np.array_equal(m.wall, expected)
    assert m.walk.all() and not m.gate_wall.any()
    entry["see_across_paint"] = paint_rect(108, 48, 112, 52)
    assert np.array_equal(cg.masks(rgba, entry).wall, expected & ~cg.unpack_paint(entry["see_across_paint"]))


def test_closed_candidates_cannot_be_wall_tags():
    rgba = floor()
    rgba[200:232, 200:232] = (230, 230, 230, 255)
    rgba[202:230, 202:230] = (128, 128, 128, 255)
    entry = tagged(rgba, "closed", (210, 210), "wall")
    with pytest.raises(cg.GeometryError, match=str(entry["tags"][0]["id"])):
        cg.masks(rgba, entry)


def test_cover_tag_is_separate_from_wall():
    rgba = floor()
    rgba[200:232, 200:232] = (230, 230, 230, 255)
    rgba[202:230, 202:230] = (128, 128, 128, 255)
    entry = tagged(rgba, "closed", (210, 210), "cover")
    m = cg.masks(rgba, entry)
    assert m.cover.any() and not m.wall.any()
    assert np.array_equal(m.sight, m.wall | m.cover)
    assert np.array_equal(cg.see_over_cover(rgba, entry), m.cover & ~m.wall)


@pytest.mark.parametrize("name", ["Sunset", "Fracture", "Summit"])
def test_maps_without_kept_thin_pieces_retain_committed_masks(name):
    image = np.array(Image.open(cg.MINIMAP_DIR / f"{name}.png").convert("RGBA"))
    m = cg.masks(image, cg.load_tags()["maps"].get(name, {}))
    assert np.array_equal(m.sight, cg.read_mask_png(cg.ASSET_DIR / f"{name}.sight.png"))
    assert np.array_equal(m.walk, cg.read_mask_png(cg.ASSET_DIR / f"{name}.walk.png"))


def test_two_thin_walls_stop_los_without_changing_the_ray_engine():
    rgba = floor()
    rgba[100:140, [201, 221]] = (0, 0, 0, 0)
    raw = rgba[..., 3] == 0
    m = cg.masks(rgba)
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))["Haven"]["xMultiplier"]
    a, b = (180, 120, None), (240, 120, None)
    plain = cg.geometry_from_masks("T", raw, m.walk, scale)
    padded = cg.geometry_from_masks("T", m.sight, m.walk, scale)
    assert plain.m_per_px == pytest.approx(0.13, abs=0.01)
    assert cg.los(plain, a, b)
    assert not cg.los(padded, a, b)

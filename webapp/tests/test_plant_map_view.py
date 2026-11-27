"""Pure helpers behind the plant map view: palette, rotation, zone clip, site crop, label layout."""
from app.services import plant_map_view as pmv
from app.services.plant_position import encode_runs


def test_palette():
    assert pmv.colour(0) == "#4e79a7"
    assert pmv.colour(10) == pmv.colour(0)
    assert len(set(pmv.PALETTE)) == 10


def test_rotate_uv():
    assert pmv.rotate_uv(1000, 2000, 0) == (1000, 2000)
    assert pmv.rotate_uv(1000, 2000, 1) == (8000, 1000)
    assert pmv.rotate_uv(1000, 2000, 2) == (9000, 8000)
    assert pmv.rotate_uv(1000, 2000, 4) == pmv.rotate_uv(1000, 2000, 0)


def _fake(rows):
    cells = [c for row in rows for c in row]
    return {"maps": {"X": {"box": [0, 0, 4, 4], "zone": encode_runs(cells), "walk": encode_runs([0] * 16)}}}


def _cells(rects, cell):
    out = set()
    for x, y, w, h in rects:
        for i in range(round(x / cell), round((x + w) / cell)):
            for j in range(round(y / cell), round((y + h) / cell)):
                assert (i, j) not in out
                out.add((i, j))
    return out


def test_zone_rects_merge_and_cover():
    rows = [[0, 1, 1, 0], [0, 1, 1, 0], [1, 0, 0, 1], [0, 0, 0, 0]]
    rects = pmv.zone_rects("X", _fake(rows))
    assert len(rects) == 3  # merged pair, then two runs on the third row
    expected = {(x, y) for y, row in enumerate(rows) for x, c in enumerate(row) if c}
    cell = 10000 / pmv.GRID_SIZE
    assert _cells(rects, cell) == expected
    assert any(abs(r[3] - 2 * cell) < 1e-6 for r in rects)


def test_zone_rects_unknown_key():
    assert pmv.zone_rects("Nope", _fake([[1] * 4] * 4)) == []


def test_zone_rects_committed_file():
    rects = pmv.zone_rects("Sunset")
    assert rects
    for x, y, w, h in rects:
        assert 0 <= x and 0 <= y and x + w <= 10000 + 1e-6 and y + h <= 10000 + 1e-6


def test_site_crop_minimum_side():
    cx, cy, side = pmv.site_crop([], [(4000, 4000, 4500, 4400)])
    assert (cx, cy) == (4250, 4200)
    assert side == 1500


def test_site_crop_shifted_inside():
    cx, cy, side = pmv.site_crop([], [(9500, 9500, 9900, 9900)])
    assert side == 1500
    assert cx + side / 2 == 10000 and cy + side / 2 == 10000


def test_site_crop_ignores_outlier():
    points = [(3000 + i * 2, 3000 + i * 2) for i in range(100)] + [(9000, 9000)]
    cx, cy, side = pmv.site_crop(points, [])
    assert side == 1500
    assert 3000 <= cx <= 3200 and 3000 <= cy <= 3200


def test_site_crop_nothing():
    assert pmv.site_crop([], []) is None


def test_layout_labels_stack_left():
    out = pmv.layout_labels([("a", 1000, 1000), ("b", 1100, 1010), ("c", 1200, 1020)], (0, 0, 5000), 200)
    assert [(i, s, y) for i, s, _, y in out] == [("a", "left", 1000), ("b", "left", 1200), ("c", "left", 1400)]
    assert all(x == -0.04 * 5000 for _, _, x, _ in out)


def test_layout_labels_right_and_bottom():
    out = pmv.layout_labels([("r", 4000, 4900), ("s", 4100, 4950)], (0, 0, 5000), 200)
    assert all(s == "right" and x == 1.04 * 5000 for _, s, x, _ in out)
    ys = sorted(y for *_, y in out)
    assert ys[1] - ys[0] >= 200 and ys[1] <= 5000

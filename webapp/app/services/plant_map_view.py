"""Pure helpers for the plant map view: palette, rotation, zone clip, site crop and label layout.

Standard library only, no database. All coordinates are map UV units (0..10000, y down).
"""
from functools import lru_cache

from app.services import plant_position
from app.services.plant_position import GRID_SIZE, UV_SCALE, decode_runs

PALETTE = ("#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
           "#9c755f", "#17becf")

MIN_CROP = 1500
MAX_CROP = 10000
CENTRE = UV_SCALE / 2


def colour(index):
    return PALETTE[index % len(PALETTE)]


def rotate_uv(u, v, turns):
    """The point after `turns` clockwise quarter turns about the map centre (y down)."""
    for _ in range(turns % 4):
        u, v = UV_SCALE - v, u
    return u, v


@lru_cache(maxsize=32)
def _cached_rects(asset_key):
    return tuple(_rects(asset_key, plant_position._zones_file()))


def zone_rects(asset_key, zones=None):
    """The plant zone of `asset_key` as (x, y, w, h) rectangles in UV units; [] when it has none.

    Without `zones` the committed file is used and the decoded rectangles are memoised per key.
    """
    if zones is None:
        return list(_cached_rects(asset_key))
    return _rects(asset_key, zones)


def _rects(asset_key, zones):
    entry = (zones.get("maps") or {}).get(asset_key)
    if not entry:
        return []
    x0, y0, x1, y1 = entry["box"]
    width, height = x1 - x0, y1 - y0
    cells = decode_runs(entry["zone"], width * height)
    cell = UV_SCALE / GRID_SIZE
    rows = []
    for row in range(height):
        runs, start = [], None
        for col in range(width):
            on = cells[row * width + col]
            if on and start is None:
                start = col
            elif not on and start is not None:
                runs.append((start, col))
                start = None
        if start is not None:
            runs.append((start, width))
        rows.append(tuple(runs))
    rects, row = [], 0
    while row < height:
        runs, end = rows[row], row + 1
        while end < height and rows[end] == runs:
            end += 1
        if runs:
            for a, b in runs:
                rects.append(((x0 + a) * cell, (y0 + row) * cell, (b - a) * cell, (end - row) * cell))
        row = end
    return rects


def _percentile(sorted_values, fraction):
    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def site_crop(points, boxes, factor=2.6):
    """(cx, cy, side) of the square around a site's spots and plant positions, or None."""
    xs = [b[0] for b in boxes] + [b[2] for b in boxes]
    ys = [b[1] for b in boxes] + [b[3] for b in boxes]
    if len(points) >= 5:
        px = sorted(p[0] for p in points)
        py = sorted(p[1] for p in points)
        xs += [_percentile(px, .02), _percentile(px, .98)]
        ys += [_percentile(py, .02), _percentile(py, .98)]
    if not xs:
        return None
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    side = min(MAX_CROP, max(MIN_CROP, factor * max(right - left, bottom - top)))
    half = side / 2
    cx = min(max((left + right) / 2, half), UV_SCALE - half)
    cy = min(max((top + bottom) / 2, half), UV_SCALE - half)
    return cx, cy, side


def layout_labels(anchors, crop, gap):
    """Place one label per anchor in the gutter nearer its x. Returns [(id, side, x, y)]."""
    x0, y0, side = crop
    centre = x0 + side / 2
    bottom = y0 + side
    groups = {"left": [], "right": []}
    for ident, x, y in anchors:
        groups["left" if x < centre else "right"].append((y, ident))
    out = []
    for name, group in groups.items():
        group.sort(key=lambda item: item[0])
        ys = []
        for y, _ in group:
            ys.append(y if not ys else max(y, ys[-1] + gap))
        if ys and ys[-1] > bottom:
            shift = ys[-1] - bottom
            ys = [y - shift for y in ys]
        label_x = x0 - 0.04 * side if name == "left" else x0 + 1.04 * side
        out.extend((ident, name, label_x, y) for (_, ident), y in zip(group, ys))
    return out

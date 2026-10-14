"""Sunset's moving door, closed (bug 1, 2026-10-14 run): the centre-test raster of its footprint left a 3 px slit
at the south end and a 2 px slit at the north end of the doorway (px rows 446-482 are open in Sunset.sight.png,
the raster covered 448-479), and sight leaked through the south one. A sight occluder now blocks every cell its
footprint touches (`raster_touched`, D5 B, 2026-10-14 review), which seals it with the footprint as tagged."""

from collections import deque

import numpy as np

from app.control import features as cf
from app.control import geometry as cg

X0, X1, Y0, Y1 = 396, 431, 430, 500
START, GOAL = (400, 465), (420, 465)       # (x, y) px, either side of the door


def _closed_sight():
    geo = cg.load_geometry('Sunset', height_mode='flat')
    mf = cg.load_tags(cg.ASSET_DIR)['maps']['Sunset']['map_features']
    feature = mf['features'][0]
    closed = next(s for s in feature['states'] if s['name'] == 'closed')
    sight = geo.sight | cf.to_px(cf.raster_touched(closed['footprint']))
    return geo, mf, sight


def _connected(sight, start, goal):
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        if (x, y) == goal:
            return True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (x + dx, y + dy)
                if n in seen or not (X0 <= n[0] <= X1 and Y0 <= n[1] <= Y1) or sight[n[1], n[0]]:
                    continue
                seen.add(n)
                queue.append(n)
    return False


def test_no_open_sight_path_through_the_closed_door():
    geo, mf, sight = _closed_sight()
    assert not sight[START[1], START[0]] and not sight[GOAL[1], GOAL[0]], "both ends are open ground"
    assert not _connected(sight, START, GOAL)


def test_the_occluder_the_engine_builds_seals_it_too():
    geo, mf, _ = _closed_sight()
    feature = mf['features'][0]
    occluders, _ = cf.sight_occluders(geo, {**mf, 'features': [feature]}, {feature['id']: 'closed'})
    sight = geo.sight.copy()
    for o in occluders:
        sight |= o.mask
    assert occluders, "the closed door yields an occluder on the flat map"
    assert not _connected(sight, START, GOAL)


def _square(u0, v0, u1, v1):
    return {'type': 'polygon', 'uv': [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]}


def test_raster_touched_adds_a_cell_an_edge_crosses_off_centre():
    c = cf.CELL_UV
    shape = _square(10 * c, 10.7 * c, 12 * c, 12 * c)              # top edge in row 10, past its centre
    assert not cf.raster(shape)[10, 10]
    assert cf.raster_touched(shape)[10, 10] and cf.raster_touched(shape)[10, 11]


def test_raster_touched_leaves_a_neighbour_an_edge_only_borders():
    c = cf.CELL_UV
    shape = _square(10 * c, 10 * c, 12 * c, 12 * c)                 # edges on cell borders
    assert (cf.raster_touched(shape) == cf.raster(shape)).all()


def test_door_bundle_reasons_are_only_the_height_asset_one():
    geo, mf, sight = _closed_sight()
    status = cf.bundle_status(geo, mf)['bundle-5']
    assert list(status.reasons) == ['moving panel sight requires a measured height asset']

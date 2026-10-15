"""Lotus feature-2, the rotating door (2026-10-14 review). The replay runtime blocks sight with the rotation's
panel turned to the moment's pose (`cf.rotation_sight`), not with a state's footprint. The panel stopped at px rows
562 and 606 while the doorway's walls end at 559 and start at 609, so a closed door left a slit at either end.
The panel (and every state's footprint) now spans uv v 5461.25-5945, symmetric about the pivot, and its pose blocks
every cell it touches: closed in both rest orientations, open halfway through the turn.

The touched sight mask adds cells over the walls; the floor is still bound under the centre raster, so with real
single-floor heights both Lotus door bundles stay publishable (the review's blocker: the touched mask bound them
off_ground and compilation dropped their blockers)."""

import numpy as np
import pytest

from app.control import feature_geometry as fg
from app.control import features as cf
from app.control import geometry as cg
from app.control import heights as hc
from tests.replays import test_sunset_door_seals as sunset

WINDOW = (320, 368, 540, 630)
START, GOAL = (328, 600), (358, 600)       # (x, y) px, either side of the door, clear of the panel at every pose


@pytest.fixture(scope="module")
def lotus():
    geo = cg.load_geometry('Lotus', height_mode='flat')
    mf = cg.load_tags(cg.ASSET_DIR)['maps']['Lotus']['map_features']
    return geo, mf, next(f for f in mf['features'] if f['id'] == 'feature-2')


def _connected(geo, mf, feature, fraction, monkeypatch):
    blocker, why = cf.rotation_sight(geo, mf, feature, cf.rotation_pose(feature, fraction))
    assert why is None and blocker is not None
    sight = geo.sight | blocker.mask
    for name, value in zip(("X0", "X1", "Y0", "Y1"), WINDOW):
        monkeypatch.setattr(sunset, name, value)
    assert not sight[START[1], START[0]] and not sight[GOAL[1], GOAL[0]], "both ends are open ground"
    return sunset._connected(sight, START, GOAL)


@pytest.mark.parametrize("fraction", [0.0, 1.0])
def test_the_resting_panel_seals_the_doorway(lotus, fraction, monkeypatch):
    assert not _connected(*lotus, fraction, monkeypatch)


def test_the_turning_panel_opens_it(lotus, monkeypatch):
    assert _connected(*lotus, 0.5, monkeypatch)


def test_panel_and_footprints_agree(lotus):
    _, _, feature = lotus
    panel = feature['rotation']['panel']['uv']
    assert {p[1] for p in panel} == {5461.25, 5945.0}
    assert all(s['footprint']['uv'] == panel for s in feature['states'])


def test_both_door_bundles_publish_with_heights(lotus):
    geo, mf, _ = lotus
    floors = np.full((cg.GRID, cg.GRID, hc.MAX_FLOORS), -1, np.int16)
    floors[:, :, 0][geo.walk] = 0
    asset = hc.HeightAsset(floors, np.zeros_like(floors), geo.walk.copy(), np.zeros_like(geo.walk),
                           np.empty((0, 5), np.int32), {'origin_z': 0})
    tall = fg.prepare(cg.attach_heights(cg.load_geometry('Lotus', height_mode='flat'), asset), mf)
    status = cf.bundle_status(tall, mf)
    for bundle in ('bundle-8', 'bundle-9'):
        assert status[bundle].publishable, status[bundle].reasons

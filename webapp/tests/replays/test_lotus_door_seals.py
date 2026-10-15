"""Lotus feature-2, the rotating door (2026-10-14 review): its footprint stopped at px row 607 while the wall below
starts at 609, so even the touched raster left a 1 px row through which 30 cell pairs saw past it. The bottom
edge now reaches uv v 5945 in every state, and no open sight path crosses the doorway."""

import pytest

from app.control import features as cf
from app.control import geometry as cg
from tests.replays import test_sunset_door_seals as sunset

WINDOW = (320, 368, 540, 630)
START, GOAL = (330, 590), (357, 590)       # (x, y) px, either side of the door


@pytest.fixture(scope="module")
def lotus():
    geo = cg.load_geometry('Lotus', height_mode='flat')
    mf = cg.load_tags(cg.ASSET_DIR)['maps']['Lotus']['map_features']
    return geo, next(f for f in mf['features'] if f['id'] == 'feature-2')


@pytest.mark.parametrize("state", ["rest_a", "rotating_a", "rest_b", "rotating_b"])
def test_no_open_sight_path_past_the_rotating_door(lotus, state, monkeypatch):
    geo, feature = lotus
    footprint = next(s for s in feature['states'] if s['name'] == state)['footprint']
    sight = geo.sight | cf.to_px(cf.raster_touched(footprint))
    monkeypatch.setattr(sunset, "X0", WINDOW[0])
    monkeypatch.setattr(sunset, "X1", WINDOW[1])
    monkeypatch.setattr(sunset, "Y0", WINDOW[2])
    monkeypatch.setattr(sunset, "Y1", WINDOW[3])
    assert not sight[START[1], START[0]] and not sight[GOAL[1], GOAL[0]], "both ends are open ground"
    assert not sunset._connected(sight, START, GOAL)

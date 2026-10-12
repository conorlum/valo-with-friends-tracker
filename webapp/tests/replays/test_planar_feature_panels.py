import copy

import numpy as np
import pytest

from map_feature_artifact_toys import geometry_case, source_case
from app.control import features as cf
from app.replays.map_feature_events import CLASSES


def panel():
    mf = source_case()['map_features']
    f = mf['features'][0]
    f['kind'] = 'door'
    f['replay_source'] = {
        'actors': ['RespawningPlummetShootable_Test'],
        'class_path': CLASSES['RespawningPlummetShootable'],
        'reset_rpcs': ['MulticastRoundBegin'], 'rules': [{'rpc': 'OnDie', 'kind': 'shoot'}],
    }
    f['states'][0]['sight_bounds'] = {'ref': 'all_height'}
    mf['features'] = [f]
    mf['bundles'] = mf['bundles'][:1]
    return mf


def test_source_bound_all_height_panel_uses_existing_unmeasured_2d_ground():
    geo, mf = geometry_case(unresolved=True), panel()
    original_z = geo.node_z.copy()
    assert cf.placement(geo, mf)['feature-1'].ok
    assert cf.placement(geo, mf)['feature-1'].ground_m == ()
    closed = cf.movement_blocks(geo, mf)
    assert not closed.pending and closed.blocked[5160]
    blockers, pending = cf.sight_occluders(geo, mf)
    assert not pending and blockers[0].all_height
    assert cf.blocked_lines(blockers, (319., 324., 0.), np.array([[329., 324., 9.]]))[0]
    assert not cf.movement_blocks(geo, mf, {'feature-1': 'open'}).blocked.any()
    assert cf.sight_occluders(geo, mf, {'feature-1': 'open'}) == ([], [])
    assert np.array_equal(original_z, geo.node_z, equal_nan=True), 'no floor is invented'


@pytest.mark.parametrize('change', ['unbound', 'finite_band', 'extra_finite_band', 'sliding', 'rotation', 'multi_floor', 'off_ground'])
def test_planar_fallback_does_not_relax_other_panel_requirements(change):
    geo, mf = geometry_case(unresolved=True), panel()
    f = mf['features'][0]
    if change == 'unbound':
        f.pop('replay_source')
    elif change == 'finite_band':
        f['states'][0]['sight_bounds'] = copy.deepcopy(source_case()['map_features']['features'][0]['states'][0]['sight_bounds'])
    elif change == 'extra_finite_band':
        f['states'][0]['sight'] = [{'geometry': f['states'][0]['footprint'],
            'bounds': copy.deepcopy(source_case()['map_features']['features'][0]['states'][0]['sight_bounds'])}]
    elif change in ('sliding', 'rotation'):
        f[change] = {'closed_state': 'closed'}
    elif change == 'multi_floor':
        geo = geometry_case(multi=True)
    else:
        f['states'][0]['footprint'] = {'type': 'point', 'uv': [100, 100]}
    assert cf.placement(geo, mf)['feature-1'].reasons
    assert not cf.movement_blocks(geo, mf).blocked[5160]


def test_all_height_panel_on_measured_ground_keeps_its_physical_floor():
    geo, mf = geometry_case(), panel()
    assert cf.placement(geo, mf)['feature-1'].ground_m == pytest.approx((-.9,))

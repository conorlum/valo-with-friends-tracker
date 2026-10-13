"""Compile-to-runtime combinations missed by the original per-primitive checks."""
import copy

import numpy as np
import pytest

from app.control import features as cf
from app.control.feature_runtime import FeatureRuntime, prepare_geometry
from app.replays import map_feature_inputs as fi, map_feature_artifacts as fa
from test_adaptive_ground import mixed
from test_ascent_features import runtime_case


@pytest.mark.parametrize('wall_attachment', [False, True])
def test_compiled_descending_door_on_refined_ground_and_wall_attachment(wall_attachment):
    geo = mixed(name='Ascent')
    _, mf, f, evidence = runtime_case()
    f['bundle'] = mf['bundles'][0]['id'] = 'bundle-1'
    f['kind'] = 'door'
    x0 = 188 if wall_attachment else 200
    shape = {'type': 'polygon', 'uv': [[x * 10000 / 1024, y * 10000 / 1024]
        for x, y in [(x0, 200), (204, 200), (204, 208), (x0, 208)]]}
    for state in f['states']:
        if state.get('footprint'):
            state['footprint'] = copy.deepcopy(shape)
    f['sliding']['movement_cutoff'] = {'status': 'known', 'value': .3, 'unit': 'fraction', 'basis': 'conservative'}
    base = dict(sight=cf._packed_mask(geo.sight), walk=cf._packed_mask(geo.walk_px),
                barrier=None, scale=geo.uv_per_unit / 10000, specials=[], legacy={})
    source = fi.capture_source_snapshot('Ascent', fi.canonical_json({'maps': {'Ascent': {'map_features': mf}}}))
    inp = fi.identify_features('Ascent', geo.height_sha, source, base)
    item = cf.compile_artifact(geo, inp, '0' * 40)
    assert item.manifest['active_bundles'] == [mf['bundles'][0]['id']], item.manifest['pending']
    cf.verify_artifact(item, geo)
    geo.features, geo.features_sha = item, item.digest
    runtime = FeatureRuntime(prepare_geometry(geo), {**evidence, 't_end': 9})
    # Source closes at 1 s and destroys at 8 s; cutoff occurs at 2.5 s.
    for t, blocked in [(0, False), (1, False), (2.5, False), (2.55, True), (6.01, True), (8, False)]:
        movement, sight = runtime.sample(t)
        assert movement.any() == blocked
        assert not (movement & ~geo.walk_n).any(), 'wall attachments must never acquire walking nodes'
        if blocked:
            child = geo.node_at_px(202, 202, 0)
            assert movement[child]
            assert any(o.mask[202, 190] for o in sight) if wall_attachment else sight


def test_missing_round_source_is_not_an_infrastructure_retry():
    from app.control.map_pool_runtime import MapPoolRuntime
    from test_map_pool_runtime import runtime_case as pool_case, summit
    geo, blob = pool_case(summit(), 'Summit')
    blob.pop('map_messages')
    with pytest.raises(fa.FeatureSourceUnavailable) as caught:
        MapPoolRuntime(geo, blob)
    assert fa.feature_failure(caught.value)['error_kind'] == 'inputs'
    assert fa.feature_failure(caught.value)['error_code'] == 'features_source_unavailable'

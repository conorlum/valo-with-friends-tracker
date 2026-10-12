import pytest
from map_feature_artifact_toys import geometry_case, snapshot_case


def test_full_catalogue_reports_disabled_placement_separately():
    from app.control.feature_diagnostics import diagnose_features
    report = diagnose_features(geometry_case(), snapshot_case(), consumers={'test': 1})
    entries = {entry['id']: entry for entry in report['features']}
    assert entries['feature-2']['placement'] == 'placeable'
    assert entries['feature-2']['runtime'] == 'disabled'
    assert report['counts']['total_tagged'] == 2
    assert report['comparison']['available'] is False


def test_diagnostics_compare_physical_ground_and_pending_transitions():
    from app.control.feature_diagnostics import diagnose_features
    old = diagnose_features(geometry_case(origin_dm=0), snapshot_case(), consumers={'test': 1})
    shifted = diagnose_features(geometry_case(origin_dm=50), snapshot_case(), previous=old['snapshot'], consumers={'test': 1})
    assert shifted['comparison']['maximum_world_shift_m'] == pytest.approx(5)
    pending = diagnose_features(geometry_case(multi=True), snapshot_case(), previous=old['snapshot'], consumers={'test': 1})
    assert pending['comparison']['newly_pending'] == ['feature-1']
    feature = pending['features'][0]
    assert feature['reasons'][0]['code'] == 'multi_floor'
    assert feature['reasons'][0]['cells'] and feature['reasons'][0]['floor_counts'] == [2]
    recovered = diagnose_features(geometry_case(), snapshot_case(), previous=pending['snapshot'], consumers={'test': 1})
    assert recovered['comparison']['recovered'] == ['feature-1']


def test_phase_panel_without_authored_bounds_is_pending_in_report_and_artifact():
    from map_feature_artifact_toys import source_case, base_case
    from app.control.feature_diagnostics import diagnose_features
    from app.control.features import compile_artifact
    from app.replays.map_feature_inputs import identify_features
    entry = source_case()
    feature = entry['map_features']['features'][0]
    feature['rotation'] = {'phases': [{'at': 0, 'panel': feature['states'][0]['footprint']}]}
    snapshot = snapshot_case(entry)
    geo = geometry_case(flat=True)
    report = diagnose_features(geo, snapshot, consumers={'test': 1})
    assert report['features'][0]['placement'] == 'pending'
    assert any(r['code'] == 'invalid_bounds' and 'phases[0]' in r['path'] for r in report['features'][0]['reasons'])
    inp = identify_features('Summit', None, snapshot, base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inp, '0' * 40)
    assert artifact.manifest['active_bundles'] == []


def test_real_diagnostic_child_uses_exact_snapshot_and_rejects_hash_mismatch():
    import json
    from map_feature_artifact_toys import base_case
    from app.services.control_feature_artifacts import run_feature_child
    from app.replays.map_feature_diagnostics import diagnostic_envelope
    from app.replays.map_feature_artifacts import FeatureArtifactError
    snapshot = snapshot_case()
    request = {'mode': 'diagnose', 'map': 'Summit', 'source': diagnostic_envelope('Summit', snapshot.raw_bytes),
               'base': base_case(), 'height': None}
    result = json.loads(run_feature_child('diagnose', json.dumps(request).encode()))
    assert result['source_sha256'] == snapshot.raw_sha256
    assert result['height'] == 'flat' and result['counts']['total_tagged'] == 2
    assert all(f['runtime'] in {'unregistered', 'disabled'} for f in result['features'])
    request['source']['raw_source_sha256'] = 'f' * 64
    with pytest.raises(FeatureArtifactError):
        run_feature_child('diagnose', json.dumps(request).encode())


def test_oversize_source_keeps_identity_and_explicit_unavailability():
    import json
    from app.replays.map_feature_diagnostics import diagnostic_envelope, MAX_DIAGNOSTIC_BYTES
    raw = json.dumps({'maps': {'Summit': {'notes': 'x' * MAX_DIAGNOSTIC_BYTES}}}).encode()
    envelope = diagnostic_envelope('Summit', raw)
    assert envelope['status'] == 'error' and envelope['code'] == 'diagnostic_unavailable'
    assert len(envelope['raw_source_sha256']) == 64 and envelope['raw_source_b64'] is None


@pytest.mark.parametrize('trigger_type', ['switch', 'shoot'])
def test_event_bound_wall_trigger_does_not_require_walkable_marker_ground(trigger_type):
    from map_feature_artifact_toys import source_case
    from app.control.features import placement
    from app.replays.map_feature_events import CLASSES
    entry = source_case()
    feature = entry['map_features']['features'][0]
    entry['map_features']['triggers'] = [{
        'id': 'switch', 'type': trigger_type, 'geometry': {'type': 'point', 'uv': [100, 100]},
        'targets': [{'feature': feature['id'], 'kind': trigger_type}]}]
    geo = geometry_case()
    assert not placement(geo, entry)[feature['id']].ok
    feature['replay_source'] = {
        'actors': ['Switch_HiddenTemple_Test'], 'class_path': CLASSES['Switch_HiddenTemple'],
        'reset_rpcs': ['MulticastResetAnimation'],
        'rules': [{'rpc': 'MulticastPlayAnimation', 'kind': 'switch'}]}
    if trigger_type == 'shoot':
        feature['replay_source'] = {
            'actors': ['RespawningPlummetShootable_Test'],
            'class_path': CLASSES['RespawningPlummetShootable'],
            'reset_rpcs': ['MulticastRoundBegin'], 'rules': [{'rpc': 'OnDie', 'kind': 'shoot'}]}
    assert placement(geo, entry)[feature['id']].ok
    entry['map_features']['triggers'][0]['geometry']['uv'][0] = -1
    assert not placement(geo, entry)[feature['id']].ok
    entry['map_features']['triggers'][0]['geometry']['uv'][0] = 100
    feature['states'][0]['footprint']['uv'] = [[100, 100], [110, 100], [110, 110]]
    assert not placement(geo, entry)[feature['id']].ok  # panel still needs ground


@pytest.mark.parametrize('flat', [False, True])
@pytest.mark.parametrize('kind', ['door', 'breakable'])
def test_bound_panel_attaches_to_wall_without_opening_ground_or_clipping_sight(flat, kind):
    import copy
    import numpy as np
    from map_feature_artifact_toys import source_case
    from app.control import features as cf
    from app.replays.map_feature_events import CLASSES
    entry = source_case()
    feature = entry['map_features']['features'][0]
    feature['kind'] = kind
    feature['replay_source'] = {
        'actors': ['RespawningPlummetShootable_Test'],
        'class_path': CLASSES['RespawningPlummetShootable'],
        'reset_rpcs': ['MulticastRoundBegin'], 'rules': [{'rpc': 'OnDie', 'kind': 'shoot'}]}
    if kind == 'breakable':
        feature['replay_source'] = {
            'actors': ['RespawningWallPlate_Test'], 'class_path': CLASSES['RespawningWallPlate'],
            'reset_rpcs': ['RoundBeginBroadcast'], 'rules': [{'rpc': 'OnDie', 'kind': 'destroy'}]}
    feature['states'][0]['footprint']['uv'] = [[x*10000/1024, y*10000/1024]
        for x,y in ((316,320),(328,320),(328,328),(316,328))]
    feature['states'][0]['sight_bounds'] = {'ref':'all_height'}
    geo = geometry_case(flat=flat)
    before = geo.walk_px.copy()
    assert cf.placement(geo, entry)[feature['id']].ok
    blocks = cf.movement_blocks(geo, entry['map_features'])
    assert not blocks.pending and blocks.blocked[5160]
    assert not blocks.blocked[5159]  # wall has no traversal node
    assert np.array_equal(before, geo.walk_px)
    occluders, pending = cf.sight_occluders(geo, entry['map_features'])
    assert not pending and occluders[0].mask[324,316]  # full wall attachment retained
    for mutate in ('unbound', 'not_wall', 'deep_wall', 'entire_wall'):
        trial=copy.deepcopy(entry)
        f=trial['map_features']['features'][0]
        g=copy.deepcopy(geo)
        if mutate=='unbound':f.pop('replay_source')
        elif mutate=='not_wall':g.sight[320:328,312:320]=False
        else:
            x0,x1=(312,328) if mutate=='deep_wall' else (312,320)
            f['states'][0]['footprint']['uv']=[[x*10000/1024,y*10000/1024]
                for x,y in ((x0,320),(x1,320),(x1,328),(x0,328))]
        assert not cf.placement(g,trial)[feature['id']].ok,mutate
    for g in (geometry_case(unresolved=True),geometry_case(multi=True)):
        assert not cf.placement(g,entry)[feature['id']].ok


@pytest.mark.parametrize('unknown_child', [False, True])
def test_wall_attachment_keeps_exact_mixed_leaf_floor_requirements(unknown_child):
    from tests.replays.test_adaptive_ground import mixed
    from map_feature_artifact_toys import source_case
    from app.control import features as cf
    from app.replays.map_feature_events import CLASSES
    geo=mixed((0,None if unknown_child else 0,0,0),name='Summit')
    mf=source_case()['map_features']
    f=mf['features'][0]
    mf['features']=[f]
    f['kind']='door'
    f['replay_source']={
        'actors':['RespawningPlummetShootable_Test'],
        'class_path':CLASSES['RespawningPlummetShootable'],
        'reset_rpcs':['MulticastRoundBegin'],'rules':[{'rpc':'OnDie','kind':'shoot'}]}
    f['states'][0]['footprint']['uv']=[[x*10000/1024,y*10000/1024]
        for x,y in ((188,200),(208,200),(208,208),(188,208))]
    f['states'][0]['sight_bounds']={'ref':'all_height'}
    placed=cf.placement(geo,mf)[f['id']]
    assert placed.ok is (not unknown_child)
    if unknown_child:
        assert any(r['code']=='unresolved_height' for r in placed.reasons)
        assert not cf.movement_blocks(geo,mf).blocked.any()
    else:
        effects=cf.movement_blocks(geo,mf)
        assert not effects.pending and effects.blocked[geo.node_at_px(206,202,0)]
        occ,pending=cf.sight_occluders(geo,mf)
        assert not pending and occ[0].mask[202,188]

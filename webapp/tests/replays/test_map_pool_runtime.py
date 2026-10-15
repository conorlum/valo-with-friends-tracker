import copy
import gzip

import numpy as np
import pytest

from app.control.map_pool_runtime import sampled_state, MapPoolRuntime, readiness
from app.replays import map_feature_schema as ms
from app.replays.map_feature_events import CLASSES
from app.replays.map_feature_artifacts import FeatureArtifact, FeatureArtifactCorrupt
from app.replays.map_feature_inputs import FeatureKey, canonical_json
from map_feature_artifact_toys import geometry_case, source_case


def source(cls, actor, rpc, kind):
    from app.replays.map_feature_events import SOURCE_RULES
    return {'actors': [actor], 'class_path': cls, 'reset_rpcs': sorted(SOURCE_RULES[cls][0]),
            'rules': [{'rpc': rpc, 'kind': kind}]}


def summit():
    f = copy.deepcopy(source_case()['map_features']['features'][0])
    closed = f['states'][0]
    f.update(initial_state='open', replay_source=source(CLASSES['RespawningPlummetShootable'],
        'RespawningPlummetShootable1', 'OnDie', 'shoot'))
    f['states'] = [f['states'][1], dict(closed, name='halfway', blocks_sight=False, blocks_movement=False),
                   dict(closed, name='slamming'), dict(closed, terminal=True)]
    f['transitions'] = [
        {'id': 'drop', 'from': ['open'], 'event': 'shoot', 'to': 'halfway',
         'motion': {'state': 'halfway', 'duration': ms.known(1, 's')},
         'follow_up': {'after': ms.known(0, 's'), 'name': 'slam'}, 'mid_motion': 'ignore'},
        {'id': 'slam', 'from': ['halfway'], 'event': 'scheduled', 'name': 'slam', 'to': 'closed',
         'motion': {'state': 'slamming', 'duration': ms.known(.5, 's')}, 'mid_motion': 'ignore'}]
    return f


def lotus():
    f = summit()
    f.update(initial_state='rest_a', replay_source=source(CLASSES['Switch_HiddenTemple'],
        'Switch_HiddenTemple1', 'MulticastPlayAnimation', 'switch'))
    shape = f['states'][-1]['footprint']
    bounds = f['states'][-1]['sight_bounds']
    f['rotation'] = {'pivot': {'type': 'point', 'uv': [320 * 10000 / 1024, 320 * 10000 / 1024]},
        'panel': shape, 'duration': ms.known(10, 's'), 'start_deg': ms.known(0, 'deg'),
        'end_deg': ms.known(180, 'deg'), 'direction': {'status': 'known', 'value': 'cw'}, 'sight_bounds': bounds}
    f['states'], f['transitions'] = [], []
    for side, other in [('a', 'b'), ('b', 'a')]:
        for name, blocked in [(f'rest_{side}', True), (f'rotating_{side}', True)]:
            f['states'].append({'name': name, 'footprint': shape, 'sight_bounds': bounds,
                                'blocks_movement': blocked, 'blocks_sight': True})
        f['transitions'] += [
            {'id': 'turn-' + side, 'from': ['rest_' + side], 'event': 'switch',
             'to': 'rest_' + other, 'motion': {'state': 'rotating_' + side, 'duration': ms.known(10, 's')},
             'mid_motion': 'ignore'}]
    return f


def runtime_case(f, map_name):
    geo = geometry_case(); geo.name = map_name
    mf = {**ms.empty(), 'features': [f], 'bundles': [{'id': 'bundle-1', 'enabled': True,
          'runtime_consumer': 'map_pool_replay_v1', 'members': [f['id']]}]}
    from app.replays.map_feature_inputs import FEATURE_COMPILER_VERSION
    geo.features = FeatureArtifact('a' * 64, FeatureKey(map_name, geo.height_sha, 'b' * 64, FEATURE_COMPILER_VERSION),
        {'active_bundles': ['bundle-1']}, canonical_json({'runtime': mf}), gzip.compress(b'{}'), 'c' * 40)
    source = f['replay_source']
    ledger = [{'actor': source['actors'][0], 'class_path': source['class_path'], 'time_ms': when,
               'rpc': rpc, 'params': {}, 'line': i} for i, (when, rpc) in
              enumerate([(0, source['reset_rpcs'][0]), (2000, source['rules'][0]['rpc'])])]
    blob = {'t_end': 20, 'map_messages': {'v': 1, 'map': map_name, 'report': {'status': 'framed'},
            'start_ms': 1000, 'previous_end_ms': 0, 'end_ms': 21000, 'ledger': ledger}}
    return geo, blob


def test_summit_halfway_clear_slam_blocks_and_closed_persists():
    geo, blob = runtime_case(summit(), 'Summit')
    runtime = MapPoolRuntime(geo, blob)
    assert runtime.transitions == [1, 2, 2.5]
    for t in [0, 1, 1.999]:
        assert not runtime.sample(t)[0].any() and not runtime.sample(t)[1]
    for t in [2, 2.499, 2.5, 20]:
        assert runtime.sample(t)[0].any() and runtime.sample(t)[1]


@pytest.mark.parametrize('time,phase,fraction', [(0, 'rest_a', 0), (1, 'rotating_a', 0),
    (2.5, 'rotating_a', .15), (6, 'rotating_a', .5), (9, 'rotating_a', .8),
    (11, 'rest_b', 1), (13, 'rotating_b', 1), (18, 'rotating_b', 1.5), (23, 'rest_a', 2)])
def test_lotus_rotation_clock_continues_through_passable_and_closing_phases(time, phase, fraction):
    timeline = {'initial': 'rest_a', 'events': [{'t': 1, 'kind': 'switch'}, {'t': 13, 'kind': 'switch'}]}
    state, angle = sampled_state(lotus(), timeline, time)
    assert state['state'] == phase and angle == pytest.approx(fraction)


def test_reset_missing_partial_capture_and_wrong_map_hold_runtime():
    geo, blob = runtime_case(summit(), 'Summit')
    for change in ['reset', 'capture', 'map']:
        bad = copy.deepcopy(blob)
        if change == 'reset': bad['map_messages']['ledger'] = bad['map_messages']['ledger'][1:]
        elif change == 'capture': bad['map_messages']['report']['status'] = 'partial'
        else: bad['map_messages']['map'] = 'Lotus'
        with pytest.raises(FeatureArtifactCorrupt):
            MapPoolRuntime(geo, bad)


def test_unchanged_panel_pose_reuses_sight_mask_without_changing_other_poses():
    from app.control import features as cf
    geo, blob = runtime_case(lotus(), 'Lotus')
    from app.control.geometry import geometry_from_masks
    full = geometry_from_masks('Lotus', np.zeros((1024, 1024), bool), np.ones((1024, 1024), bool), 7e-5)
    full.features = geo.features
    geo = full
    runtime = MapPoolRuntime(geo, blob)
    initial = runtime.sample(0)[1][0]
    turning = runtime.sample(3)[1][0]
    assert initial is runtime.sample(.5)[1][0]
    assert turning is runtime.sample(3.001)[1][0]  # Same 1/16-second physical pose.
    assert not np.array_equal(initial.mask, turning.mask)
    mf, feature, timeline = runtime.items[0]
    from app.control.map_pool_runtime import rotation_fraction
    for t in (0, 3, 6, 10, 11, 19):
        actual = runtime.sample(t)[1][0]
        panel = cf.rotation_pose(feature, rotation_fraction(feature, timeline, t))
        # The pose blocks every cell it touches; its floor is bound under the centre raster (2026-10-14 review).
        expected, why = cf.resolve_bounds(geo, mf, {'bounds':feature['rotation']['sight_bounds']},
                                         cf.to_px(cf.raster_touched(panel)), feature['id'],
                                         bind_px=cf.to_px(cf.raster(panel)))
        assert why is None and np.array_equal(actual.mask, expected.mask)
        assert (actual.bottom, actual.top, actual.all_height) == (expected.bottom, expected.top, expected.all_height)
    closed = runtime.sample(11)[1][0]
    assert closed is runtime.sample(20)[1][0]

    geo, blob = runtime_case(summit(), 'Summit')
    runtime = MapPoolRuntime(geo, blob)
    assert runtime.sample(2.5)[1][0] is runtime.sample(20)[1][0]


def test_polygon_pivot_and_unresolved_panel_cannot_be_enabled():
    f = lotus(); f['rotation']['pivot'] = f['rotation']['panel']; f['rotation']['panel'] = None
    assert 'rotation needs a point pivot and physical panel' in readiness(f)


def test_unresolved_rotating_panel_bounds_cannot_publish_even_with_valid_state_bounds():
    from app.control import features
    f = lotus(); geo, _ = runtime_case(f, 'Lotus')
    mf = {'features': [f], 'routes': [], 'triggers': [], 'bundles': [{'id': 'bundle-1', 'members': [f['id']],
          'enabled': True, 'runtime_consumer': 'map_pool_replay_v1'}]}
    f['rotation']['sight_bounds'] = {'ref': 'unresolved'}
    status = features.bundle_status(geo, mf, consumers={'map_pool_replay_v1'})['bundle-1']
    assert not status.publishable and any('rotation.pose' in reason for reason in status.reasons)


@pytest.mark.parametrize('duration', [None, ms.unresolved(), ms.known(-1, 's'), ms.known(0, 's'), ms.known(float('inf'), 's')])
def test_missing_or_invalid_motion_duration_cannot_be_enabled(duration):
    f = summit(); f['transitions'][0]['motion']['duration'] = duration
    assert 'transition timing unresolved' in readiness(f)


def test_lotus_keeps_a_partial_sight_panel_while_movement_is_passable():
    from tests.replays.control_toys import toy_heights, HALL
    def rectangle(x0, y0, x1, y1):
        return {'type': 'polygon', 'uv': [[x * 10000 / 1024, y * 10000 / 1024]
                for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]}
    f = lotus()
    f['rotation'].update(panel=rectangle(240, 192, 272, 200),
                          pivot={'type': 'point', 'uv': [256 * 10000 / 1024, 196 * 10000 / 1024]})
    for state in f['states']:
        state['footprint'] = rectangle(240, 184, 272, 208)
    geo, blob = runtime_case(f, 'Lotus')
    actual = toy_heights('RotatingPanel', [HALL]); actual.name = 'Lotus'; actual.features = geo.features
    runtime = MapPoolRuntime(actual, blob)
    closed, closed_sight = runtime.sample(1)
    passable, rotating_sight = runtime.sample(6)
    assert closed.any() and not passable.any()  # At 90 degrees each touched cell is exactly half covered.
    vacated = actual.node_at(actual.cell_of_px(268, 196), None)
    assert closed[vacated] and not passable[vacated]
    assert closed_sight and rotating_sight
    assert not np.array_equal(closed_sight[0].mask, rotating_sight[0].mask)


def rotating_hall():
    from app.control.geometry import geometry_from_masks
    from tests.replays.control_toys import rect_mask
    f = lotus()
    shape = {'type': 'polygon', 'uv': [[x*10000/1024, y*10000/1024]
             for x, y in [(208, 96), (216, 96), (216, 128), (208, 128)]]}
    f['rotation'].update(panel=shape, pivot={'type': 'point', 'uv': [212*10000/1024, 112*10000/1024]})
    for state in f['states']:
        state['footprint'] = shape
    old, blob = runtime_case(f, 'Lotus')
    walk = rect_mask([(96, 96, 416, 128)])
    geo = geometry_from_masks('Lotus', ~walk, walk, 7e-5, [])
    geo.features = old.features
    return geo, blob


def test_rotating_wall_admits_one_cell_route_without_disappearing_or_corner_cutting():
    from app.control.topology import FlatTopology
    geo, blob = rotating_hall()
    runtime = MapPoolRuntime(geo, blob)
    topo = FlatTopology(geo)
    reached = np.full(geo.n, np.inf)
    reached[geo.cell_of_px(196, 100)] = 1
    target = geo.cell_of_px(236, 100)
    for t, should_cross in [(1, False), (6, True), (11, False)]:
        blocked, sight = runtime.sample(t)
        assert sight  # The physical slab still occludes sight when no cell is majority covered.
        assert bool(blocked.any()) == (t != 6)
        room = geo.walk_n & ~blocked
        spread = topo.spread(reached, room, np.full(geo.n, -np.inf), 100, .1, [], solid=blocked)
        assert np.isfinite(spread[target]) == should_cross
    # At the midpoint the top route is clear and the adjacent half-covered cell is also open.
    mask = runtime.sample(6)[0].reshape(128, 128)
    assert not mask[12, 26] and not mask[13, 26]


def test_newly_vacated_cell_cannot_be_reached_before_its_movement_release():
    from app.control.feature_runtime import FeatureRuntime
    from app.control.topology import FlatTopology
    geo, blob = rotating_hall()
    runtime = FeatureRuntime(geo, blob)
    cell = geo.cell_of_px(212, 100)
    release = runtime.reopened_by(6)[cell]
    assert 1 < release <= 6
    assert runtime.sample(release-1e-6)[0][cell]
    assert not runtime.sample(release)[0][cell]
    reached = np.full(geo.n, np.inf)
    reached[geo.cell_of_px(204, 100)] = 1
    blocked = runtime.sample(6)[0]
    spread = FlatTopology(geo).spread(reached, geo.walk_n & ~blocked,
        runtime.reopened_by(6), 6, .1, [], solid=blocked)
    assert spread[cell] >= release + .1


def test_thin_panel_edge_does_not_block_a_majority_clear_cell():
    from app.control.map_pool_runtime import rotation_cells
    panel = {'type': 'polygon', 'uv': [[x*10000/1024, y*10000/1024]
             for x,y in [(208.1, 96), (208.2, 96), (208.2, 128), (208.1, 128)]]}
    cells = rotation_cells(panel)
    assert not cells.any()


@pytest.mark.parametrize('width,blocked', [(3.9999, False), (4, False), (4.0001, True), (8, True)])
def test_rotating_panel_blocks_only_more_than_half_of_cell_area(width, blocked):
    from app.control.map_pool_runtime import rotation_cells, rotation_coverage
    panel = {'type': 'polygon', 'uv': [[x*10000/1024, y*10000/1024]
             for x,y in [(208, 96), (208+width, 96), (208+width, 104), (208, 104)]]}
    assert rotation_coverage(panel)[12,26] == pytest.approx(width/8)
    assert bool(rotation_cells(panel)[12,26]) == blocked


def test_diagonal_half_cell_remains_open_in_both_polygon_windings():
    from app.control.map_pool_runtime import rotation_cells, rotation_coverage
    points = [[x*10000/1024, y*10000/1024] for x,y in [(208,96),(216,96),(208,104)]]
    for vertices in (points, list(reversed(points))):
        panel = {'type':'polygon','uv':vertices}
        assert rotation_coverage(panel)[12,26] == pytest.approx(.5)
        assert not rotation_cells(panel).any()


@pytest.mark.parametrize('angle', [17, 45, 81])
def test_rotated_cell_clipping_preserves_total_panel_area(angle):
    from app.control.map_pool_runtime import rotation_coverage
    points = np.array([[-16,-4],[16,-4],[16,4],[-16,4]])
    theta = np.deg2rad(angle)
    rotation = np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
    uv = ((points @ rotation.T + [850.5,527.5]) * 10000/1024).tolist()
    coverage = rotation_coverage({'type':'polygon','uv':uv})
    assert coverage.sum() == pytest.approx(32*8/(8*8))
    assert np.all((coverage >= 0) & (coverage <= 1))


def test_majority_rule_changes_movement_without_erasing_the_sight_panel():
    from app.control import features as cf
    from app.control.map_pool_runtime import rotation_effects
    from tests.replays.control_toys import rect_mask
    from app.control.geometry import geometry_from_masks
    panel = {'type':'polygon','uv':[[x*10000/1024,y*10000/1024]
             for x,y in [(208,96),(211,96),(211,104),(208,104)]]}
    walk = rect_mask([(200,88,224,112)])
    geo = geometry_from_masks('Lotus',~walk,walk,7e-5,[])
    f = lotus()
    assert not rotation_effects(geo,{'features':[f]},f,panel).blocked.any()
    assert cf.raster(panel).any()  # Sight still uses the slab's actual footprint.


def test_site_rotating_panel_never_reports_a_timed_passage_clear_boolean():
    import json
    import subprocess
    from pathlib import Path
    from app.control.feature_runtime import FeatureRuntime
    geo, blob = rotating_hall()
    payload = FeatureRuntime(geo, blob).metadata(blob)['features'][0]
    assert payload['movement_geometry'] == 'rotating_panel'
    script = Path(__file__).resolve().parents[2] / 'app/static/js/replay.js'
    code = f'const r=require({json.dumps(str(script))});process.stdout.write(JSON.stringify(r.mapFeatureState({json.dumps(payload)},6)));'
    current = json.loads(subprocess.run(['node','-e',code],capture_output=True,text=True,check=True).stdout)
    assert current['movement_geometry'] == 'rotating_panel' and current['blocks_movement'] is None


def test_composed_runtime_registers_scheduled_boundaries_and_exact_destruction_release():
    from app.control.feature_runtime import FeatureRuntime
    f = summit()
    f['states'][-1]['terminal'] = False  # synthetic destructible panel
    f['replay_source'] = source(CLASSES['WindowShield'], 'WindowShieldA3', 'PlayDoorSounds', 'switch')
    f['transitions'][0]['event'] = 'switch'
    f['states'] += [{'name': 'broken', 'terminal': True, 'blocks_sight': False, 'blocks_movement': False}]
    f['transitions'] += [{'id': 'break', 'from': '*', 'event': 'destroy', 'to': 'broken'}]
    f['replay_source']['rules'] += [{'rpc': 'HandleDoorDestroyed', 'kind': 'destroy'}]
    geo, blob = runtime_case(f, 'Sunset')
    blob['map_messages']['ledger'].append({'actor': f['replay_source']['actors'][0],
        'class_path': f['replay_source']['class_path'], 'time_ms': 4500, 'rpc': 'HandleDoorDestroyed', 'params': {}, 'line': 3})
    runtime = FeatureRuntime(geo, blob)
    assert runtime.active and runtime.transitions == [1, 2, 2.5, 3.5]
    assert runtime.sample(3.49)[0].any() and not runtime.sample(3.5)[0].any()
    release = runtime.reopened_by(3.5)
    assert np.isfinite(release).any() and (release[np.isfinite(release)] == 3.5).all()


def test_site_trace_uses_the_same_state_boundaries_as_runtime():
    import json
    import subprocess
    from pathlib import Path
    from app.control.feature_runtime import FeatureRuntime
    f = summit(); geo, blob = runtime_case(f, 'Summit')
    payload = FeatureRuntime(geo, blob).metadata(blob)['features'][0]
    times = [0, 1, 1.99, 2, 2.5, 19]
    script = Path(__file__).resolve().parents[2] / 'app/static/js/replay.js'
    code = f'const r=require({json.dumps(str(script))});process.stdout.write(JSON.stringify({json.dumps(times)}.map(t=>r.mapFeatureState({json.dumps(payload)},t))));'
    samples = json.loads(subprocess.run(['node', '-e', code], capture_output=True, text=True, check=True).stdout)
    for time, sampled in zip(times, samples):
        state = sampled_state(f, {'initial': 'open', 'events': [{'t': 1, 'kind': 'shoot'}]}, time)[0]
        assert sampled['state'] == state['state']
        assert sampled['blocks_movement'] == next(s for s in f['states'] if s['name'] == state['state'])['blocks_movement']


def test_site_descending_door_tracks_continuous_thirty_percent_cutoff_and_reopening():
    import json
    import subprocess
    from pathlib import Path
    from app.control.feature_runtime import FeatureRuntime
    from app.replays import map_feature_motion as motion
    from test_sliding_feature_motion import door
    f = door()
    shape = summit()['states'][-1]['footprint']
    motion.closed_state(f).update(footprint=shape,
        sight_bounds={'ref': 'ground', 'bottom': ms.known(0, 'm'), 'top': ms.known(3, 'm')})
    f['sliding'] = {'axis': 'vertical', 'open_state': 'open', 'closed_state': 'closed',
        'open_clearance': ms.known(3, 'm'),
        'movement_cutoff': {**ms.known(.3, 'fraction'), 'basis': 'conservative'}}
    f['replay_source'] = source(CLASSES['WindowShield'], 'WindowShieldA3', 'PlayDoorSounds', 'switch')
    geo, blob = runtime_case(f, 'Sunset')
    blob['map_messages']['ledger'].append({'actor': 'WindowShieldA3',
        'class_path': CLASSES['WindowShield'], 'time_ms': 10000, 'rpc': 'PlayDoorSounds', 'params': {}, 'line': 3})
    runtime = FeatureRuntime(geo, blob)
    payload = runtime.metadata(blob)['features'][0]
    early, later = runtime.sample(2)[1][0], runtime.sample(4)[1][0]
    assert early.mask is later.mask and early.bottom > later.bottom
    old_bottom = early.bottom
    runtime.sample(6)
    assert early.bottom == old_bottom  # Reuse XY without mutating archived height bands.
    times = [0, 1, 2.49, 2.5, 2.51, 6, 9, 12.49, 12.5, 14]
    script = Path(__file__).resolve().parents[2] / 'app/static/js/replay.js'
    code = f'const r=require({json.dumps(str(script))});process.stdout.write(JSON.stringify({json.dumps(times)}.map(t=>r.mapFeatureState({json.dumps(payload)},t))));'
    samples = json.loads(subprocess.run(['node', '-e', code], capture_output=True, text=True, check=True).stdout)
    for time, sampled in zip(times, samples):
        assert sampled['blocks_movement'] == runtime.sample(time)[0].any()


def test_witnessed_open_completion_drives_the_descending_runtime_without_defaulting():
    from app.replays import map_feature_events as events, map_feature_motion as motion
    from test_sliding_feature_motion import door
    f = door()
    f['initial_state'] = 'closed'
    motion.closed_state(f).update(footprint=summit()['states'][-1]['footprint'],
        sight_bounds={'ref': 'ground', 'bottom': ms.known(0, 'm'), 'top': ms.known(3, 'm')})
    f['sliding'] = {'axis': 'vertical', 'open_state': 'open', 'closed_state': 'closed',
        'open_clearance': ms.known(3, 'm'),
        'movement_cutoff': {**ms.known(.3, 'fraction'), 'basis': 'conservative'}}
    f['replay_source'] = source(CLASSES['WindowShield'], 'WindowShieldA3', 'PlayDoorSounds', 'switch')
    f['replay_source']['rules'] = [
        {'rpc': 'PlayDoorSounds', 'kind': 'switch', 'equals': {'New State': 1}},
        {'rpc': 'PlayDoorSounds', 'kind': 'observed', 'state': 'open', 'equals': {'New State': 0}}]
    geo, blob = runtime_case(f, 'Sunset')
    rows = blob['map_messages']['ledger']
    rows[1]['params'] = {'New State': [1, 3], 'Old State': [3, 3], 'isMoveTo': [1, 1]}
    rows.append({**rows[1], 'time_ms': 7007, 'params': {'Old State': [1, 3], 'isMoveTo': [1, 1]}, 'line': 3})
    with pytest.raises(FeatureArtifactCorrupt, match='transition parameters unqualified'):
        MapPoolRuntime(geo, blob)
    rows.append({**rows[1], 'time_ms': 7007, 'rpc': events.DOOR_STATE_PROPERTY,
                 'params': {'DoorState': [0, 3]}, 'line': 4})
    runtime = MapPoolRuntime(geo, blob)
    assert runtime.sample(.5)[0].any()  # closed before opening activation
    assert runtime.sample(4.49)[0].any()
    assert not runtime.sample(4.51)[0].any()  # movement releases after the reviewed 30% threshold
    assert not runtime.sample(6.007)[0].any() and not runtime.sample(6.007)[1]
    timeline = runtime.items[0][2]
    assert timeline['events'][-1]['parameter_evidence'][0]['property_lines'] == [4]


def test_full_control_fresh_gaps_and_cached_gaps_share_source_bound_geometry(tmp_path, monkeypatch):
    from app.control import features, geometry, task, engine
    from app.gaps import cache
    from app.replays import map_feature_inputs as fi, map_feature_artifacts as fa, control_format, format
    from map_feature_artifact_toys import base_case
    from tests.replays.control_toys import blob as round_blob
    f = summit(); geo, evidence = runtime_case(f, 'Summit')
    mf = {'version': 1, 'features': [f], 'triggers': [], 'routes': [], 'bundles': [
          {'id': 'bundle-1', 'enabled': True, 'runtime_consumer': 'map_pool_replay_v1', 'members': [f['id']]}]}
    raw = fi.canonical_json({'maps': {'Summit': {'map_features': mf, 'specials': []}}})
    geo.features = None
    inputs = fi.identify_features('Summit', geo.height_sha, fi.capture_source_snapshot('Summit', raw), base_case())
    artifact = features.compile_artifact(geo, inputs, 'c' * 40)
    assert artifact.manifest['active_bundles'] == ['bundle-1'], artifact.manifest['pending']
    features.verify_artifact(artifact, geo)
    geo.features, geo.features_sha = artifact, artifact.digest
    monkeypatch.setattr(geometry, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr(cache, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr(task, '_load', lambda *a, **kw: geo)
    monkeypatch.setattr(task, '_ARTIFACTS', fa.VerifiedArtifactCache())
    fa.store_cached_artifact(tmp_path / 'features', artifact, lambda a: features.verify_artifact(a, geo))
    data = round_blob({0: ('A', [(0, 324, 324, 0)]), 5: ('B', [(0, 348, 324, 180)])}, t_end=4)
    data.update(map='Summit', map_messages=evidence['map_messages'])
    link = {'sides': {}, 'db_deaths': []}
    actual = task.geometry_used(geo)
    envelope = control_format.input_envelope('test.pool', '0' * 64, link, actual)
    job = {'key': 1, 'map': 'Summit', 'features': artifact.digest, 'height': geo.height_sha,
           'height_mode': 'asset', 'geometry': actual, 'inputs': envelope,
           'blob': format.encode_blob(data), 'link': link,
           'gaps': {'replay_id': 1, 'round': 1, 'fingerprint': 'a' * 16, 'gap_fingerprint': 'b' * 16}}
    full = task.compute_task(job)
    assert full['status'] == 'ok', full
    assert full['gaps']['run']['status'] == 'ok', full['gaps']
    header = control_format.unpack_data(full['data'])[0]
    assert header['map_features']['keys'] == [f['id']]
    assert header['map_features']['features'][0]['mode'] == 'reducer_trace'
    real_compute = engine.compute_round
    monkeypatch.setattr(engine, 'compute_round', lambda *a, **kw: pytest.fail('cache hit recomputed'))
    cached = task.compute_task({**job, 'gaps_only': True})
    assert cached['gaps'] == full['gaps']
    monkeypatch.setattr(engine, 'compute_round', real_compute)
    for path in tmp_path.glob('*.ticks.pkl.gz'):
        path.unlink()
    fresh = task.compute_task({**job, 'gaps_only': True})
    assert fresh['gaps'] == full['gaps']

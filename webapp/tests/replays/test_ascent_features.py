import base64
import copy
import json
from types import SimpleNamespace

import pytest

from app.replays import ascent_features as af


def packed(value):
    out = []
    while True:
        byte = (value & 127) << 1
        value >>= 7
        out += [(byte | bool(value)) >> bit & 1 for bit in range(8)]
        if not value:
            return out


def capture(tmp_path, states, *, actor='WindowShieldB1', guid=99):
    names = ['PlayDoorSounds', 'HandleDoorDestroyed', 'MulticastRoundBegin']
    groups = [{'path': af.DOOR_CLASS + '_ClassNetCache',
               'fields': [{'handle': i, 'name': name} for i, name in enumerate(names)]},
              {'path': af.DOOR_CLASS + ':PlayDoorSounds', 'fields': [{'handle': 0, 'name': 'New State'}]}]
    rows = []
    for time, state in states:
        handle = 1 if state == 'broken' else 2 if state == 'reset' else 0
        body = [] if isinstance(state, str) else [0] + packed(1) + packed(8) + [(state >> i) & 1 for i in range(8)] + packed(0)
        # SerializeInt maximum 3: low bit, then high bit only when it could fit.
        bits = [handle & 1] + ([handle >> 1] if not handle & 1 else []) + packed(len(body)) + body
        raw = sum(bit << i for i, bit in enumerate(bits)).to_bytes((len(bits)+7)//8, 'little')
        rows.append(dict(type='map_audit_raw_content', diagnostic_version=1, is_actor=True, has_rep_layout=False,
                         actor_path=actor, actor_net_guid=guid, channel=7, time_ms=time, truncated=False,
                         payload_bits=len(bits), transformed_base64=base64.b64encode(raw).decode()))
    (tmp_path/'map-audit.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return {'source_sha256': 'a'*64, 'net_field_export_groups': groups}


def test_exact_binding_and_reviewed_states_do_not_depend_on_network_guid(tmp_path):
    manifest = capture(tmp_path, [(0, 'reset'), (1000, 2), (6002, 3), (9000, 'broken')], guid=456)
    signals, report = af.read_capture(tmp_path, 'Ascent', manifest)
    assert report['status'] == 'decoded'
    assert [s['state'] for s in signals] == ['open', 'closing', 'closed', 'broken']
    assert {s['key'] for s in signals} == {'ascent_market'}
    assert af.read_capture(tmp_path, 'Sunset', manifest)[0] == []
    capture(tmp_path, [(1000, 2)], actor='WindowShieldB10')
    assert af.read_capture(tmp_path, 'Ascent', manifest)[0] == []


def test_explicit_opening_and_unknown_values(tmp_path):
    manifest = capture(tmp_path, [(1000, 1), (2000, 255)])
    signals, _ = af.read_capture(tmp_path, 'Ascent', manifest)
    assert [s['state'] for s in signals] == ['opening', 'unknown']


def reopen_capture(tmp_path, *, witness=0, witness_time=2022612, extra_argument=False, new=None):
    """Real export field layout and source lines, with anonymous map actors only."""
    manifest = capture(tmp_path, [])
    manifest['net_field_export_groups'] += [
        {'path': af.DOOR_CLASS, 'fields': [
            {'handle': h, 'name': n} for h, n in
            [(3, '216'), (12, '215'), (16, 'LocalDoorPos'), (17, 'DoorState'), (18, 'Transition')]]}]
    manifest['net_field_export_groups'][1]['fields'] = [
        {'handle': h, 'name': n} for h, n in [(0, 'New State'), (1, 'Old State'), (2, 'isMoveTo')]]

    def row(time, values, *, props=False):
        body = [0]
        for handle, value, width in values:
            body += packed(handle + 1) + packed(width) + [(value >> i) & 1 for i in range(width)]
        body += packed(0)
        bits = body if props else [0, 0] + packed(len(body)) + body
        raw = sum(bit << i for i, bit in enumerate(bits)).to_bytes((len(bits)+7)//8, 'little')
        return dict(type='map_audit_raw_content', diagnostic_version=1, is_actor=True,
                    has_rep_layout=props, actor_path='WindowShieldB1', actor_net_guid=99,
                    channel=7, time_ms=time, truncated=False, payload_bits=len(bits),
                    transformed_base64=base64.b64encode(raw).decode())

    rows = [{} for _ in range(2164)]
    rows[2154] = row(2017612, [(0, 1, 3), (1, 3, 3), (2, 1, 1)])
    rows[2157] = row(2017612, [(17, 1, 3)], props=True)
    args = [(1, 1, 3), (2, 1, 1)]
    if extra_argument:
        args.append((7, 1, 1))
    if new is not None:
        args.insert(0, (0, new, 8))
    rows[2160] = row(2022612, args)
    rows[2161] = row(2022612, [(2, 0, 3)], props=True)  # unknown #2 is not DoorState
    if witness is not None:
        rows[2163] = row(witness_time, [(17, witness, 3)], props=True)
    (tmp_path/'map-audit.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return manifest


def test_real_rows_opening_and_omitted_completion_keep_witness_lines(tmp_path):
    manifest = reopen_capture(tmp_path)
    signals, report = af.read_capture(tmp_path, 'Ascent', manifest)
    assert report['status'] == 'decoded' and report['rows'] == 2164
    assert signals == [
        {'key': 'ascent_market', 'time_ms': 2017612, 'state': 'opening',
         'rpc': 'PlayDoorSounds', 'line': 2155},
        {'key': 'ascent_market', 'time_ms': 2022612, 'state': 'open',
         'rpc': 'PlayDoorSounds', 'line': 2161, 'evidence': [2164]}]
    replay = SimpleNamespace(map_name='Ascent', rounds={1: {}}, report={})
    af.attach(replay, tmp_path, manifest, [(2017000, 2025000, 2030000)])
    assert replay.rounds[1]['map_features']['features'][1]['events'] == [
        {'t': .612, 'state': 'opening', 'rpc': 'PlayDoorSounds', 'line': 2155},
        {'t': 5.612, 'state': 'open', 'rpc': 'PlayDoorSounds', 'line': 2161, 'evidence': [2164]}]


def test_omitted_completion_becomes_open_only_with_shared_rule(tmp_path, monkeypatch):
    from app.replays import map_feature_door_evidence as evidence, map_feature_events

    assert af._door_parameter_evidence is map_feature_events._door_parameter_evidence is evidence._door_parameter_evidence
    assert evidence.DOOR_CLASS == af.DOOR_CLASS == map_feature_events.CLASSES['WindowShield']
    manifest = reopen_capture(tmp_path)
    signals, _ = af.read_capture(tmp_path, 'Ascent', manifest)
    assert signals[-1]['state'] == 'open' and signals[-1]['evidence'] == [2164]
    monkeypatch.setattr(af, '_door_parameter_evidence', lambda row, properties: (row['params'], []))
    signals, _ = af.read_capture(tmp_path, 'Ascent', manifest)
    assert signals[-1] == {'key': 'ascent_market', 'time_ms': 2022612, 'state': 'unknown',
                           'rpc': 'PlayDoorSounds', 'line': 2161}


@pytest.mark.parametrize('options', [
    {'witness': None}, {'witness_time': 2022613}, {'witness': 1},
    {'extra_argument': True}, {'new': 255}])
def test_omitted_completion_requires_exact_evidence(tmp_path, options):
    manifest = reopen_capture(tmp_path, **options)
    signals, report = af.read_capture(tmp_path, 'Ascent', manifest)
    assert report['status'] == 'decoded'
    assert signals[-1] == {'key': 'ascent_market', 'time_ms': 2022612, 'state': 'unknown',
                           'rpc': 'PlayDoorSounds', 'line': 2161}


def test_capture_without_reopen_preserves_serialized_signals_and_attach(tmp_path):
    import hashlib

    manifest = capture(tmp_path, [(0, 'reset'), (1100, 2), (6102, 3), (8000, 'broken')])
    signals, _ = af.read_capture(tmp_path, 'Ascent', manifest)
    expected = [
        {'key': 'ascent_market', 'time_ms': 0, 'state': 'open', 'rpc': 'MulticastRoundBegin', 'line': 1},
        {'key': 'ascent_market', 'time_ms': 1100, 'state': 'closing', 'rpc': 'PlayDoorSounds', 'line': 2},
        {'key': 'ascent_market', 'time_ms': 6102, 'state': 'closed', 'rpc': 'PlayDoorSounds', 'line': 3},
        {'key': 'ascent_market', 'time_ms': 8000, 'state': 'broken', 'rpc': 'HandleDoorDestroyed', 'line': 4}]
    assert json.dumps(signals).encode() == json.dumps(expected).encode()
    replay = SimpleNamespace(map_name='Ascent', rounds={1: {}}, report={})
    af.attach(replay, tmp_path, manifest, [(1000, 9000, 10000)])
    expected_blob = {'v': 1, 'status': 'decoded',
        'sha256': hashlib.sha256((tmp_path/'map-audit.ndjson').read_bytes()).hexdigest(),
        'features': [
            {'key': 'ascent_garden', 'name': 'Garden Door', 'uv': [3940, 2340], 'initial': 'unknown',
             'events': [], 'closing_s': 5},
            {'key': 'ascent_market', 'name': 'Market Door', 'uv': [2880, 5980], 'initial': 'open',
             'events': [
                 {'t': .1, 'state': 'closing', 'rpc': 'PlayDoorSounds', 'line': 2},
                 {'t': 5.102, 'state': 'closed', 'rpc': 'PlayDoorSounds', 'line': 3},
                 {'t': 7.0, 'state': 'broken', 'rpc': 'HandleDoorDestroyed', 'line': 4}], 'closing_s': 5},
            {'key': 'ascent_heaven_glass', 'name': 'Heaven Glass', 'uv': [2680, 2830], 'initial': 'unknown',
             'events': [], 'closing_s': None}], 'calculation': 'pending geometry'}
    assert json.dumps(replay.rounds[1]['map_features']).encode() == json.dumps(expected_blob).encode()


def test_bad_capture_retains_diagnostic_and_disables_complete_status(tmp_path):
    manifest = capture(tmp_path, [(1000, 2)])
    row = json.loads((tmp_path/'map-audit.ndjson').read_text())
    row['truncated'] = True
    (tmp_path/'map-audit.ndjson').write_text(json.dumps(row)+'\n')
    signals, report = af.read_capture(tmp_path, 'Ascent', manifest)
    assert signals == [] and report['status'] == 'partial'
    assert report['error_count'] == 1


def test_round_reset_window_and_exact_rpc_times(tmp_path):
    manifest = capture(tmp_path, [(0, 'reset'), (1100, 2), (6102, 3), (8000, 'broken'), (10000, 'reset')])
    replay = SimpleNamespace(map_name='Ascent', rounds={1:{}, 2:{}, 3:{}}, report={})
    af.attach(replay, tmp_path, manifest, [(1000, 8000, 10000), (11000, 15000, 16000), (20000, 25000, 26000)])
    first = replay.rounds[1]['map_features']['features'][1]
    assert first['initial'] == 'open'
    assert [(e['t'], e['state']) for e in first['events']] == [(0.1,'closing'),(5.102,'closed'),(7,'broken')]
    assert replay.rounds[2]['map_features']['features'][1]['initial'] == 'open'
    assert replay.rounds[3]['map_features']['features'][1]['initial'] == 'unknown'


def test_change_after_reset_before_barrier_drop_is_retained(tmp_path):
    manifest = capture(tmp_path, [(0, 'reset'), (500, 2), (5502, 3)])
    replay = SimpleNamespace(map_name='Ascent', rounds={1:{}}, report={})
    af.attach(replay, tmp_path, manifest, [(1000, 8000, 10000)])
    feature = replay.rounds[1]['map_features']['features'][1]
    assert feature['events'][0]['t'] == -.5
    assert af.state_at(feature, 0)['closure'] == .1


def test_linear_model_terminal_precedence_and_missing_endpoint():
    feature = {'initial':'open', 'closing_s':5, 'events':[{'t':8,'state':'closing'}, {'t':12.3,'state':'broken'}, {'t':13,'state':'closed'}]}
    assert af.state_at(feature, 10.5)['closure'] == .5
    assert af.state_at(feature, 12.3)['state'] == 'broken'
    assert af.state_at(feature, 13)['state'] == 'broken'
    feature['events'] = feature['events'][:1]
    assert not af.state_at(feature, 13.002)['pending']
    assert af.state_at(feature, 13.3)['pending']


def test_python_js_sampling_matches():
    import subprocess
    from pathlib import Path
    feature = {'initial':'open','closing_s':5,'events':[
        {'t':8,'state':'closing'},{'t':13.002,'state':'closed'},
        {'t':15,'state':'opening'},{'t':20,'state':'open'},
        {'t':22,'state':'closing'},{'t':24,'state':'opening'},
        {'t':25,'state':'closing'},{'t':30,'state':'closed'},
        {'t':31,'state':'opening'},{'t':33,'state':'closing'},
        {'t':40,'state':'broken'}]}
    script = Path(__file__).resolve().parents[2]/'app/static/js/replay.js'
    times = [0, 8, 10.5, 13.001, 13.002, 15, 17.5, 20, 22, 24, 25, 29, 30, 31, 33, 39, 40, 100]
    code = f"const r=require({json.dumps(str(script))});process.stdout.write(JSON.stringify({json.dumps(times)}.map(t=>r.mapFeatureState({json.dumps(feature)},t))));"
    result = subprocess.run(['node','-e',code], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [af.state_at(feature,t) for t in times]


def test_opening_model_and_endpoint():
    feature = {'initial': 'closed', 'closing_s': 5,
               'events': [{'t': 8, 'state': 'opening'}, {'t': 13.002, 'state': 'open'}]}
    for t, closure in [(8, 1.0), (10.5, .5), (13, 0.0)]:
        assert af.state_at(feature, t) == {
            'state': 'opening', 'closure': closure, 'modeled': True, 'pending': False}
    assert af.state_at(feature, 14) == {
        'state': 'open', 'closure': 0.0, 'modeled': False, 'pending': False}
    feature['events'].pop()
    assert af.state_at(feature, 13.25)['closure'] == 0
    assert af.state_at(feature, 13.251)['pending']


@pytest.mark.parametrize('duration', [None, 0, -1, True, float('inf'), float('nan'), '5'])
def test_opening_without_usable_duration_is_pending(duration):
    feature = {'initial': 'closed', 'closing_s': duration, 'events': [{'t': 8, 'state': 'opening'}]}
    assert af.state_at(feature, 9) == {
        'state': 'opening', 'closure': None, 'modeled': True, 'pending': True}


@pytest.mark.parametrize('first,reverse', [('opening', 'closing'), ('closing', 'opening')])
@pytest.mark.parametrize('endpoint,closure', [('open', 0.0), ('closed', 1.0), ('broken', None)])
def test_reversal_stays_pending_until_observed_endpoint(first, reverse, endpoint, closure):
    feature = {'initial': 'closed' if first == 'opening' else 'open', 'closing_s': 5,
               'events': [{'t': 1, 'state': first}, {'t': 3, 'state': reverse},
                          {'t': 4, 'state': first}, {'t': 12, 'state': endpoint}]}
    assert not af.state_at(feature, 2)['pending']
    assert af.state_at(feature, 3) == {
        'state': reverse, 'closure': None, 'modeled': True, 'pending': True}
    for t in (4, 8, 11):
        assert af.state_at(feature, t) == {
            'state': first, 'closure': None, 'modeled': True, 'pending': True}
    assert af.state_at(feature, 12) == {
        'state': endpoint, 'closure': closure, 'modeled': False, 'pending': False}


@pytest.mark.parametrize('first,reverse', [('opening', 'closing'), ('closing', 'opening')])
def test_opposite_motion_after_modeled_endpoint_is_not_a_reversal(first, reverse):
    feature = {'initial': 'closed' if first == 'opening' else 'open', 'closing_s': 5,
               'events': [{'t': 1, 'state': first}, {'t': 6, 'state': reverse}]}
    assert not af.state_at(feature, 6)['pending']
    assert af.state_at(feature, 8.5)['closure'] == .5


def test_timeline_without_opening_preserves_literal_states():
    feature = {'initial': 'open', 'closing_s': 5,
               'events': [{'t': 1, 'state': 'closing'}, {'t': 6.002, 'state': 'closed'},
                          {'t': 8, 'state': 'broken'}, {'t': 9, 'state': 'unknown'}]}
    assert [af.state_at(feature, t) for t in (0, 1, 3.5, 6, 6.002, 8, 9)] == [
        {'state': 'open', 'closure': 0.0, 'modeled': False, 'pending': False},
        {'state': 'closing', 'closure': 0.0, 'modeled': True, 'pending': False},
        {'state': 'closing', 'closure': .5, 'modeled': True, 'pending': False},
        {'state': 'closing', 'closure': 1.0, 'modeled': True, 'pending': False},
        {'state': 'closed', 'closure': 1.0, 'modeled': False, 'pending': False},
        {'state': 'broken', 'closure': None, 'modeled': False, 'pending': False},
        {'state': 'broken', 'closure': None, 'modeled': False, 'pending': False}]


def runtime_case():
    import gzip
    from app.replays.map_feature_artifacts import FeatureArtifact
    from app.replays.map_feature_inputs import FeatureKey, canonical_json
    from map_feature_artifact_toys import geometry_case, source_case
    from app.replays.map_feature_schema import known
    mf = source_case()['map_features']
    f = mf['features'][0]
    mf['features'] = [f]
    f['replay_key'] = 'ascent_market'
    f['sliding'] = {'axis':'vertical','open_state':'open','closed_state':'closed',
                    'open_clearance':known(3,'m'),'movement_clearance':known(1,'m')}
    f['states'] += [{'name':'opening','blocks_sight':True,'blocks_movement':True,'terminal':False},
                    {'name':'closing','blocks_sight':True,'blocks_movement':True},
                    {'name':'broken','terminal':True,'blocks_sight':False,'blocks_movement':False}]
    f['transitions'] = [{'id': 'open', 'from': ['closed'], 'event': 'switch', 'to': 'open',
                         'motion': {'state': 'opening', 'duration': known(5, 's')}, 'mid_motion': 'reverse'}]
    mf['bundles'] = [{'id':'ascent','enabled':True,'runtime_consumer':'ascent_replay_v1','members':[f['id']]}]
    geo = geometry_case(); geo.name = 'Ascent'
    # Compiler/transport integrity has its own owning suite; this fixture targets the consumer contract.
    geo.features = FeatureArtifact('b'*64,FeatureKey('Ascent',geo.height_sha,'a'*64,4),
        {'active_bundles':['ascent']}, canonical_json({'runtime':mf}), gzip.compress(b'{}'),'c'*40)
    timeline = {'key':'ascent_market','initial':'open','closing_s':5,
                'events':[{'t':1,'state':'closing'},{'t':6.002,'state':'closed'},{'t':8,'state':'broken'}]}
    blob = {'map_features':{'v':1,'status':'decoded','features':[timeline]}}
    return geo, mf, f, blob


def test_runtime_descending_sight_movement_and_exact_release():
    import numpy as np
    from app.control.feature_runtime import FeatureRuntime
    from app.control.features import blocked_lines
    geo, _, _, blob = runtime_case()
    runtime = FeatureRuntime(geo, blob)
    assert runtime.active and not runtime.sample(1)[0].any()
    half = runtime.sample(3.5)
    assert not half[0].any() and half[1]
    assert not blocked_lines(half[1], (324,310,0), np.array([[324,338,0]])).item()
    assert blocked_lines(runtime.sample(6.002)[1], (324,310,0), np.array([[324,338,0]])).item()
    assert runtime.sample(6.002)[0].any()
    assert not runtime.sample(8)[0].any() and not runtime.sample(8)[1]
    released = runtime.reopened_by(8)
    assert (released[np.isfinite(released)] == 8).all() and np.isfinite(released).any()


def test_runtime_refuses_incomplete_evidence_and_unqualified_parameter():
    from app.control.feature_runtime import FeatureRuntime
    from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
    geo, _, _, blob = runtime_case()
    blob['map_features']['status'] = 'partial'
    with pytest.raises(FeatureArtifactCorrupt, match='complete'):
        FeatureRuntime(geo,blob)
    blob['map_features']['status'] = 'decoded'
    blob['map_features']['features'][0]['events'].insert(1, {'t':2,'state':'unknown'})
    with pytest.raises(FeatureArtifactCorrupt, match='unqualified door-state parameter'):
        FeatureRuntime(geo,blob)


def test_readiness_refuses_door_without_opening():
    from app.control.feature_runtime import readiness
    _, _, feature, _ = runtime_case()
    assert not readiness(feature)
    feature['states'] = [state for state in feature['states'] if state['name'] != 'opening']
    assert 'required replay states missing' in readiness(feature)


@pytest.mark.parametrize('first,reverse', [('opening', 'closing'), ('closing', 'opening')])
def test_runtime_reversal_raises_pending_endpoint_message(first, reverse):
    from app.control.feature_runtime import FeatureRuntime
    from app.replays.map_feature_artifacts import FeatureSourceUnavailable
    geo, _, _, data = runtime_case()
    timeline = data['map_features']['features'][0]
    timeline.update(initial='closed' if first == 'opening' else 'open', events=[
        {'t': 1, 'state': first}, {'t': 3, 'state': reverse}, {'t': 9, 'state': 'open'}])
    runtime = FeatureRuntime(geo, data)
    with pytest.raises(FeatureSourceUnavailable, match='feature endpoint/state unobserved at calculation time'):
        runtime.sample(3)
    assert not runtime.sample(9)[0].any()


def test_unknown_after_destruction_cannot_restore_the_terminal_panel():
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data = runtime_case()
    data['map_features']['features'][0]['events'].append({'t':9,'state':'unknown'})
    runtime = FeatureRuntime(geo, data)
    assert not runtime.sample(10)[0].any() and not runtime.sample(10)[1]

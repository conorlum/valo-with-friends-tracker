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


def test_opening_unknown_values_and_missing_parameter_are_not_guessed(tmp_path):
    manifest = capture(tmp_path, [(1000, 1), (2000, 255)])
    signals, _ = af.read_capture(tmp_path, 'Ascent', manifest)
    assert [s['state'] for s in signals] == ['unknown', 'unknown']


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
    feature = {'initial':'open','closing_s':5,'events':[{'t':8,'state':'closing'},{'t':13.002,'state':'closed'},{'t':20,'state':'broken'}]}
    script = Path(__file__).resolve().parents[2]/'app/static/js/replay.js'
    times = [0, 8, 10.5, 13.001, 13.002, 20, 100]
    code = f"const r=require({json.dumps(str(script))});process.stdout.write(JSON.stringify({json.dumps(times)}.map(t=>r.mapFeatureState({json.dumps(feature)},t))));"
    result = subprocess.run(['node','-e',code], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [af.state_at(feature,t) for t in times]


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
    f['states'] += [{'name':'closing','blocks_sight':True,'blocks_movement':True},
                    {'name':'broken','terminal':True,'blocks_sight':False,'blocks_movement':False}]
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


def test_runtime_refuses_incomplete_evidence_and_unverified_opening():
    from app.control.feature_runtime import FeatureRuntime
    from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
    geo, _, _, blob = runtime_case()
    blob['map_features']['status'] = 'partial'
    with pytest.raises(FeatureArtifactCorrupt, match='complete'):
        FeatureRuntime(geo,blob)
    blob['map_features']['status'] = 'decoded'
    blob['map_features']['features'][0]['events'].insert(1, {'t':2,'state':'unknown'})
    with pytest.raises(FeatureArtifactCorrupt, match='unverified'):
        FeatureRuntime(geo,blob)


def test_unknown_after_destruction_cannot_restore_the_terminal_panel():
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data = runtime_case()
    data['map_features']['features'][0]['events'].append({'t':9,'state':'unknown'})
    runtime = FeatureRuntime(geo, data)
    assert not runtime.sample(10)[0].any() and not runtime.sample(10)[1]

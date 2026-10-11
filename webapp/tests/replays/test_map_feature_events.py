import base64
import json
from types import SimpleNamespace

import pytest

from app.replays import map_feature_events as me
from test_ascent_features import packed


def capture(tmp_path, rows):
    cls = me.CLASSES['Switch_HiddenTemple']
    names = [' MulticastPlayAnimation', 'MulticastResetAnimation']
    groups = [{'path': cls + '_ClassNetCache',
               'fields': [{'handle': i, 'name': n} for i, n in enumerate(names)]}]
    encoded = []
    for time, handle, actor in rows:
        bits = [handle] + packed(0)
        raw = sum(v << i for i, v in enumerate(bits)).to_bytes((len(bits) + 7) // 8, 'little')
        encoded.append(dict(diagnostic_version=1, actor_path=actor, class_path=cls, is_actor=True,
            actor_net_guid=17 if actor.endswith('1') else 18, channel=5, time_ms=time,
            has_rep_layout=False, truncated=False, payload_bits=len(bits),
            transformed_base64=base64.b64encode(raw).decode()))
    (tmp_path / 'map-audit.ndjson').write_text(''.join(json.dumps(r) + '\n' for r in encoded))
    return {'source_sha256': 'a' * 64, 'net_field_export_groups': groups}


def feature():
    return {'initial_state': 'rest', 'replay_source': {'actors': ['Switch_HiddenTemple1', 'Switch_HiddenTemple2'],
        'class_path': me.CLASSES['Switch_HiddenTemple'], 'reset_rpcs': ['MulticastResetAnimation'],
        'rules': [{'rpc': 'MulticastPlayAnimation', 'kind': 'switch'}]}}


def test_two_buttons_share_one_feature_and_duplicate_evidence_is_retained(tmp_path):
    manifest = capture(tmp_path, [(0, 1, 'Switch_HiddenTemple1'), (0, 1, 'Switch_HiddenTemple2'),
        (1200, 0, 'Switch_HiddenTemple1'), (1200, 0, 'Switch_HiddenTemple1'), (9000, 0, 'Switch_HiddenTemple2')])
    ledger, report = me.read_ledger(tmp_path, 'Lotus', manifest)
    assert report['status'] == 'framed' and report['qualification'] == 'framing_only'
    timeline = me.bind_round(feature(), ledger, report, 1000, 10000)
    assert timeline['status'] == 'bound' and timeline['initial'] == 'rest'
    assert [e['t'] for e in timeline['events']] == [.2, 8]
    assert len(timeline['events'][0]['evidence']) == 2
    assert timeline['events'][0]['evidence'][0]['rpc'].startswith(' ')


def test_exact_actor_names_reset_per_round_and_buy_phase_events(tmp_path):
    manifest = capture(tmp_path, [(0, 1, 'Switch_HiddenTemple1'), (500, 0, 'Switch_HiddenTemple1'),
                                (1200, 0, 'Switch_HiddenTemple10')])
    ledger, report = me.read_ledger(tmp_path, 'Lotus', manifest)
    first = me.bind_round(feature(), ledger, report, 1000, 10000)
    assert [e['t'] for e in first['events']] == [-.5]
    second = me.bind_round(feature(), ledger, report, 11000, 20000, 10000)
    assert second['status'] == 'pending' and second['reasons'] == ['round reset unobserved']


def test_capture_conflicts_and_bad_frames_never_yield_bound_status(tmp_path):
    manifest = capture(tmp_path, [(0, 1, 'Switch_HiddenTemple1')])
    path = tmp_path / 'map-audit.ndjson'
    row = json.loads(path.read_text()); row['truncated'] = True
    path.write_text(json.dumps(row) + '\n')
    ledger, report = me.read_ledger(tmp_path, 'Lotus', manifest)
    assert report['status'] == 'partial'
    assert me.bind_round(feature(), ledger, report, 1000, 10000)['status'] == 'pending'
    manifest = capture(tmp_path, [(0, 1, 'Switch_HiddenTemple1')])
    row = json.loads(path.read_text()); row['channel'] = 99
    with path.open('a') as stream:
        stream.write(json.dumps(row) + '\n')
    assert me.read_ledger(tmp_path, 'Lotus', manifest)[1]['ambiguous_actors'] == ['Switch_HiddenTemple1']


def test_missing_transition_parameter_and_ambiguous_rules_hold_feature():
    cls = me.CLASSES['WindowShield']
    f = {'initial_state': 'open', 'replay_source': {'actors': ['WindowShieldA3'], 'class_path': cls,
        'reset_rpcs': ['MulticastRoundBegin'], 'rules': [{'rpc': 'PlayDoorSounds', 'kind': 'observed',
                                                      'state': 'closing', 'equals': {'New State': 2}}]}}
    rows = [{'actor': 'WindowShieldA3', 'class_path': cls, 'time_ms': t, 'rpc': rpc, 'params': {}, 'line': i}
            for i, (t, rpc) in enumerate([(0, 'MulticastRoundBegin'), (1200, 'PlayDoorSounds')])]
    report = {'status': 'framed'}
    assert me.bind_round(f, rows, report, 1000, 2000)['reasons'] == ['transition parameters unqualified']
    pending = me.bind_round(f, rows, report, 1000, 2000)
    assert pending['events'] == [] and pending['coverage_holes'][0]['t'] == .2
    assert pending['coverage_holes'][0]['parameters'] == {}
    assert pending['coverage_holes'][0]['expected_conditions'] == [{'New State': 2}]
    rows[1]['params'] = {'New State': [2, 8]}
    f['replay_source']['rules'] *= 2
    assert me.bind_round(f, rows, report, 1000, 2000)['reasons'] == ['ambiguous source event rules']


def test_explicit_zero_parameter_matches_but_omission_stays_a_reported_hole():
    cls = me.CLASSES['WindowShield']
    f = {'initial_state': 'open', 'replay_source': {'actors': ['WindowShieldA3'], 'class_path': cls,
        'reset_rpcs': ['MulticastRoundBegin'], 'rules': [{'rpc': 'PlayDoorSounds', 'kind': 'observed',
                                                      'state': 'open', 'equals': {'New State': 0}}]}}
    rows = [{'actor': 'WindowShieldA3', 'class_path': cls, 'time_ms': 0,
             'rpc': 'MulticastRoundBegin', 'params': {}, 'line': 1},
            {'actor': 'WindowShieldA3', 'class_path': cls, 'time_ms': 1500,
             'rpc': 'PlayDoorSounds', 'params': {'Old State': [1, 3]}, 'line': 2}]
    pending = me.bind_round(f, rows, {'status': 'framed'}, 1000, 2000)
    assert pending['status'] == 'pending' and pending['events'] == []
    assert pending['coverage_holes'][0]['parameters'] == {'Old State': [1, 3]}
    rows[1]['params']['New State'] = [0, 3]
    bound = me.bind_round(f, rows, {'status': 'framed'}, 1000, 2000)
    assert bound['status'] == 'bound' and bound['events'][0]['state'] == 'open'


def test_round_ledgers_include_own_buy_phase_and_exclude_later_round(tmp_path):
    manifest = capture(tmp_path, [(0, 1, 'Switch_HiddenTemple1'), (500, 0, 'Switch_HiddenTemple1'),
        (10000, 1, 'Switch_HiddenTemple1'), (12000, 0, 'Switch_HiddenTemple1')])
    replay = SimpleNamespace(map_name='Lotus', rounds={1: {}, 2: {}}, report={})
    me.attach(replay, tmp_path, manifest, [(1000, 9000, 10000), (11000, 19000, 20000)])
    first, second = [replay.rounds[n]['map_messages'] for n in (1, 2)]
    assert [r['time_ms'] for r in first['ledger']] == [0, 500]
    assert [r['time_ms'] for r in second['ledger']] == [10000, 12000]


def test_strict_framing_does_not_guess_a_missing_parameter_layout(tmp_path):
    manifest = capture(tmp_path, [(0, 0, 'Switch_HiddenTemple1')])
    row = json.loads((tmp_path / 'map-audit.ndjson').read_text())
    bits = [0] + packed(1) + [0]  # nonempty RPC body with absent descriptor
    raw = sum(v << i for i, v in enumerate(bits)).to_bytes((len(bits) + 7) // 8, 'little')
    row.update(payload_bits=len(bits), transformed_base64=base64.b64encode(raw).decode())
    (tmp_path / 'map-audit.ndjson').write_text(json.dumps(row) + '\n')
    ledger, report = me.read_ledger(tmp_path, 'Lotus', manifest)
    assert not ledger and report['status'] == 'partial'
    assert 'descriptor missing' in report['errors'][0]['reason']


def test_ondie_identity_can_be_bound_without_inventing_inherited_parameter_names(tmp_path):
    cls = me.CLASSES['RespawningWallPlate']
    # One opaque integer property; identity comes from the exported RPC handle.
    body = [0] + packed(1) + packed(8) + [1] * 8 + packed(0)
    bits = [0] + packed(len(body)) + body
    raw = sum(v << i for i, v in enumerate(bits)).to_bytes((len(bits) + 7) // 8, 'little')
    row = dict(diagnostic_version=1, actor_path='RespawningWallPlate1', class_path=cls, is_actor=True,
        actor_net_guid=17, channel=5, time_ms=1200, has_rep_layout=False, truncated=False,
        payload_bits=len(bits), transformed_base64=base64.b64encode(raw).decode())
    path = tmp_path / 'map-audit.ndjson'
    path.write_text(json.dumps(row) + '\n')
    manifest = {'net_field_export_groups': [{'path': cls + '_ClassNetCache',
        'fields': [{'handle': 0, 'name': 'OnDie'}]}]}
    ledger, report = me.read_ledger(tmp_path, 'Abyss', manifest)
    assert report['status'] == 'framed'
    assert ledger[0]['rpc'] == 'OnDie' and ledger[0]['params'] == {'#0': (255, 8)}
    # Opaque handles cannot qualify conditional event semantics.
    binding = {'class_path': cls, 'actors': ['RespawningWallPlate1'],
        'reset_rpcs': ['RoundBeginBroadcast'],
        'rules': [{'rpc': 'OnDie', 'kind': 'destroy', 'equals': {'#0': 255}}]}
    assert me.binding_problems(binding) == ['parameter conditions must name integer values']
    # Identity alone never excuses corrupt property framing.
    row['payload_bits'] -= 8
    path.write_text(json.dumps(row) + '\n')
    assert me.read_ledger(tmp_path, 'Abyss', manifest)[1]['status'] == 'partial'


def test_unreviewed_rpc_cause_or_reset_cannot_be_enabled():
    f = feature()['replay_source']
    f['rules'][0]['rpc'] = 'DamageBroadcast'
    assert me.binding_problems(f) == ['source RPC/cause pair is not qualified for this family']
    f = feature()['replay_source']; f['reset_rpcs'] = ['RoundBeginBroadcast']
    assert me.binding_problems(f) == ['source needs reviewed explicit reset RPCs']


def sunset_open_completion():
    cls = me.CLASSES['WindowShield']
    feature = {'initial_state': 'open', 'replay_source': {'actors': ['WindowShieldA3'], 'class_path': cls,
        'reset_rpcs': ['MulticastRoundBegin'], 'rules': [{'rpc': 'PlayDoorSounds', 'kind': 'observed',
                                                       'state': 'open', 'equals': {'New State': 0}}]}}
    rows = [{'actor': 'WindowShieldA3', 'class_path': cls, 'time_ms': 0,
             'rpc': 'MulticastRoundBegin', 'params': {}, 'line': 1},
            {'actor': 'WindowShieldA3', 'class_path': cls, 'time_ms': 1500,
             'rpc': 'PlayDoorSounds', 'params': {'Old State': [1, 3], 'isMoveTo': [1, 1]}, 'line': 2},
            {'actor': 'WindowShieldA3', 'class_path': cls, 'time_ms': 1500,
             'rpc': me.DOOR_STATE_PROPERTY, 'params': {'DoorState': [0, 3]}, 'line': 3}]
    return feature, rows


def test_open_completion_uses_explicit_same_clock_property_not_a_default():
    feature, rows = sunset_open_completion()
    before = json.loads(json.dumps(rows))
    result = me.bind_round(feature, rows, {'status': 'framed'}, 1000, 2000)
    assert result['status'] == 'bound' and len(result['events']) == 1
    event = result['events'][0]
    assert (event['t'], event['kind'], event['state']) == (.5, 'observed', 'open')
    assert [e['line'] for e in event['evidence']] == [2, 3]
    assert event['parameter_evidence'] == [{'parameter': 'New State', 'value': 0,
        'qualification': 'same_actor_same_clock_explicit_DoorState', 'rpc_line': 2, 'property_lines': [3]}]
    assert rows == before  # raw omission and archived input remain intact
    assert me.bind_round(feature, rows[:2], {'status': 'framed'}, 1000, 2000)['status'] == 'pending'


@pytest.mark.parametrize('change', ['old_state', 'old_width', 'move', 'move_width', 'extra_param',
    'explicit_unknown', 'property_width', 'property_state', 'property_time', 'property_actor',
    'property_class', 'conflict', 'partial'])
def test_open_completion_rejects_unqualified_or_mismatched_witnesses(change):
    feature, rows = sunset_open_completion()
    report = {'status': 'framed'}
    if change == 'old_state': rows[1]['params']['Old State'] = [3, 3]
    elif change == 'old_width': rows[1]['params']['Old State'] = [1, 8]
    elif change == 'move': rows[1]['params']['isMoveTo'] = [0, 1]
    elif change == 'move_width': rows[1]['params']['isMoveTo'] = [1, 8]
    elif change == 'extra_param': rows[1]['params']['Other'] = [1, 1]
    elif change == 'explicit_unknown': rows[1]['params']['New State'] = [7, 3]
    elif change == 'property_width': rows[2]['params']['DoorState'] = [0, 8]
    elif change == 'property_state': rows[2]['params']['DoorState'] = [3, 3]
    elif change == 'property_time': rows[2]['time_ms'] += 1
    elif change == 'property_actor': rows[2]['actor'] = 'WindowShieldB3'
    elif change == 'property_class': rows[2]['class_path'] = me.CLASSES['RespawningWallPlate']
    elif change == 'conflict': rows.append({**rows[2], 'params': {'DoorState': [3, 3]}, 'line': 4})
    elif change == 'partial': report['status'] = 'partial'
    result = me.bind_round(feature, rows, report, 1000, 2000)
    assert result['status'] == 'pending' and not result['events']


def test_property_alone_cannot_activate_a_door_and_unknown_arguments_stay_missing():
    feature, rows = sunset_open_completion()
    assert me.bind_round(feature, [rows[0], rows[2]], {'status': 'framed'}, 1000, 2000)['events'] == []
    rows[1]['params'] = {}  # an empty or unrelated RPC body is not qualified
    assert me.bind_round(feature, rows, {'status': 'framed'}, 1000, 2000)['status'] == 'pending'


def test_raw_capture_retains_explicit_state_witness_and_corruption_disables_binding(tmp_path):
    feature, _ = sunset_open_completion()
    cls = me.CLASSES['WindowShield']
    groups = [{'path': cls, 'fields': [{'handle': 17, 'name': 'DoorState'}]},
        {'path': cls + '_ClassNetCache', 'fields': [{'handle': 0, 'name': 'PlayDoorSounds'},
                                                 {'handle': 1, 'name': 'MulticastRoundBegin'}]},
        {'path': cls + ':PlayDoorSounds', 'fields': [{'handle': 0, 'name': 'New State'},
            {'handle': 1, 'name': 'Old State'}, {'handle': 2, 'name': 'isMoveTo'}]}]
    body = [0] + packed(2) + packed(3) + [1, 0, 0] + packed(3) + packed(1) + [1] + packed(0)
    prop = [0] + packed(18) + packed(3) + [0, 0, 0] + packed(0)
    raw_rows = []
    for time, layout, bits in [(0, False, [1] + packed(0)),
        (1500, False, [0] + packed(len(body)) + body), (1500, True, prop)]:
        raw = sum(v << i for i, v in enumerate(bits)).to_bytes((len(bits) + 7) // 8, 'little')
        raw_rows.append(dict(diagnostic_version=1, actor_path='WindowShieldA3', class_path=cls,
            is_actor=True, actor_net_guid=17, channel=5, time_ms=time, has_rep_layout=layout,
            truncated=False, payload_bits=len(bits), transformed_base64=base64.b64encode(raw).decode()))
    path = tmp_path / 'map-audit.ndjson'
    path.write_text(''.join(json.dumps(r) + '\n' for r in raw_rows))
    manifest = {'net_field_export_groups': groups}
    ledger, report = me.read_ledger(tmp_path, 'Sunset', manifest)
    assert report['status'] == 'framed' and report['calls'] == 2 and report['door_state_properties'] == 1
    assert ledger[1]['params'] == {'Old State': (1, 3), 'isMoveTo': (1, 1)}
    assert me.bind_round(feature, ledger, report, 1000, 2000)['status'] == 'bound'
    unknown_body = body[:-8] + packed(4) + packed(1) + [1] + packed(0)
    unknown_bits = [0] + packed(len(unknown_body)) + unknown_body
    unknown_raw = sum(v << i for i, v in enumerate(unknown_bits)).to_bytes((len(unknown_bits)+7)//8, 'little')
    original_rpc = dict(raw_rows[1])
    raw_rows[1].update(payload_bits=len(unknown_bits), transformed_base64=base64.b64encode(unknown_raw).decode())
    path.write_text(''.join(json.dumps(r) + '\n' for r in raw_rows))
    ledger, report = me.read_ledger(tmp_path, 'Sunset', manifest)
    assert report['status'] == 'framed' and ledger[1]['params']['#3'] == (1, 1)
    assert me.bind_round(feature, ledger, report, 1000, 2000)['status'] == 'pending'
    raw_rows[1] = original_rpc
    raw_rows[-1]['truncated'] = True
    path.write_text(''.join(json.dumps(r) + '\n' for r in raw_rows))
    ledger, report = me.read_ledger(tmp_path, 'Sunset', manifest)
    assert report['status'] == 'partial'
    assert me.bind_round(feature, ledger, report, 1000, 2000)['status'] == 'pending'

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
    rows[1]['params'] = {'New State': [2, 8]}
    f['replay_source']['rules'] *= 2
    assert me.bind_round(f, rows, report, 1000, 2000)['reasons'] == ['ambiguous source event rules']


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

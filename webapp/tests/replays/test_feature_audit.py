"""Synthetic audit evidence exercises isolation, lifetimes, limits and human-review gates."""
import copy
import json
from pathlib import Path

import pytest

from app.replays.feature_audit import (Limits, inventory, private_output, render_report,
                                      worksheet, validate_observations, review_observations)
from app.replays.contract import load_pin


@pytest.fixture
def export(tmp_path):
    path = tmp_path / 'export'
    path.mkdir()
    pin = load_pin()
    manifest = {'schema_version': pin.schema_version, 'parser_version': pin.commit,
                'source_sha256': 'fixture-not-a-real-replay', 'replay_build': sorted(pin.supported_builds)[0],
                'parse_status': 'completed', 'parse_profile': 'synthetic-test', 'stats': {'malformed_packet_count': 0},
                'limitations': ['Undecoded map groups omitted'],
                'net_field_export_groups': [{'path': '/Game/Map/Door', 'fields': [{'name': 'DoorState'}]}],
                'filtered_export_group_summary': [{'path': '/Game/Map/Glass', 'was_decoded': False, 'count': 20}]}
    (path / 'manifest.json').write_text(json.dumps(manifest))
    return path


def write_rows(export, rows):
    (export / 'events.ndjson').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')


def rows():
    return [
        {'type': 'actor_spawned', 'time_ms': 0, 'actor_net_guid': 9, 'channel': 2, 'actor_path': '/Game/Map/Door'},
        {'type': 'export_group_received', 'time_ms': 10, 'actor_net_guid': 9, 'payload': {'DoorState': 0}},
        {'type': 'export_group_received', 'time_ms': 20, 'actor_net_guid': 9, 'payload': {'DoorState': 0}},
        {'type': 'export_group_received', 'time_ms': 30, 'actor_net_guid': 9, 'payload': {'DoorState': 1}},
        {'type': 'actor_closed', 'time_ms': 40, 'actor_net_guid': 9, 'reason': 'relevancy'},
        {'type': 'rpc_received', 'time_ms': 50, 'actor_net_guid': 9, 'function_name': 'PlayDoorSounds'},
        {'type': 'actor_spawned', 'time_ms': 60, 'actor_net_guid': 9, 'channel': 2, 'actor_path': '/Game/Map/Door'},
        {'type': 'export_group_received', 'time_ms': 70, 'actor_net_guid': 9, 'payload': {'DoorState': 0}},
        {'type': 'export_group_received', 'time_ms': 80, 'actor_net_guid': 99, 'payload': {'PlayerName': 'DoorGlassSwitch'}}]


TAGS = {'maps': {'Ascent': {'map_features': {'features': [
    {'id': 'feature-9', 'name': 'Market door', 'states': [{'name': 'open'}, {'name': 'closed'}],
     'transitions': [{'event': 'switch'}]}]}}}}


def test_raw_inventory_tracks_guid_reuse_and_close_without_inferred_destruction(export):
    write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent')
    assert report['scan']['complete'] and report['scan']['rows'] == 9
    assert report['contract']['compatible'] and not report['contract']['source_verified']
    assert report['map']['basis'] == 'owner_assertion'
    first, unbound, second = report['candidates']
    assert first['key'] == 'actor:9:life:1' and second['key'] == 'actor:9:life:2'
    assert first['types']['actor_closed'] == 1
    assert unbound['binding'] == 'unbound' and unbound['lifetime'] is None
    assert first['properties']['DoorState']['changes'] == [
        {'line': 2, 'time_ms': 10, 'value': 0}, {'line': 4, 'time_ms': 30, 'value': 1}]
    assert report['coverage']['filtered_export_groups'][0]['was_decoded'] is False
    assert 'destroy' not in json.dumps(first)
    assert 'absent' in report['conclusion']


def test_truncated_scan_cannot_claim_full_hash_and_caps_samples(export):
    write_rows(export, rows())
    report = inventory(export, limits=Limits(rows=4, samples=2))
    assert report['scan']['complete'] is False and report['identity']['events_sha256'] is None
    assert report['scan']['flags'] == ['row_limit']
    assert len(report['candidates'][0]['samples']) == 2
    assert report['candidates'][0]['samples_omitted'] == 2
    write_rows(export, rows()[:4])
    assert inventory(export, limits=Limits(rows=4))['scan']['complete']


def test_malformed_rows_and_unordered_time_are_reported_without_reordering(export):
    write_rows(export, [rows()[1], rows()[0], {'type': 'rpc_received', 'time_ms': True}])
    report = inventory(export)
    assert report['scan']['complete']
    assert report['scan']['flags'] == ['malformed_rows', 'nonmonotonic_times']
    assert report['scan']['errors'][0]['line'] == 3


def test_oversized_line_stops_scan_and_does_not_hash_as_full(export):
    write_rows(export, rows())
    report = inventory(export, limits=Limits(line_bytes=40))
    assert not report['scan']['complete'] and report['scan']['rows'] == 0
    assert report['scan']['flags'] == ['line_size_limit']


def test_candidate_and_actor_caps_remain_visible(export):
    write_rows(export, [{'type': 'actor_spawned', 'time_ms': i, 'actor_net_guid': i + 1,
                         'actor_path': '/Game/Map/Door'} for i in range(5)])
    report = inventory(export, limits=Limits(candidates=1, actors=1))
    assert len(report['candidates']) == 1
    assert report['scan']['flags'] == ['actor_limit', 'candidate_limit']


def test_contract_mismatch_keeps_evidence_but_labels_it_incompatible(export):
    write_rows(export, rows())
    manifest = json.loads((export / 'manifest.json').read_text())
    manifest['replay_build'] = 'unsupported'
    (export / 'manifest.json').write_text(json.dumps(manifest))
    report = inventory(export)
    assert not report['contract']['compatible'] and 'replay_build' in report['contract']['error']


def test_descriptor_property_names_discover_actor_without_keyword_in_class_name(export):
    manifest = json.loads((export / 'manifest.json').read_text())
    manifest['net_field_export_groups'] = [{'path': '/Game/Map/DescentBox_v5.DescentBox_v5_C',
                                          'fields': [{'name': 'DoorState'}]}]
    (export / 'manifest.json').write_text(json.dumps(manifest))
    write_rows(export, [{'type': 'actor_spawned', 'time_ms': 0, 'actor_net_guid': 9,
                         'actor_path': 'DescentBox_v5_2'}])
    report = inventory(export)
    assert report['coverage']['descriptor_discovery_terms'] == ['DescentBox_v5']
    assert report['candidates'][0]['hints'] == ['descriptor_name_hint: DescentBox_v5']
    assert report['candidates'][0]['binding'] == 'candidate_only'


def test_checksum_verification_and_repeated_inventory_are_deterministic(export, tmp_path):
    import hashlib
    write_rows(export, rows())
    source = tmp_path / 'synthetic.vrf'; source.write_bytes(b'explicit synthetic source')
    manifest = json.loads((export / 'manifest.json').read_text())
    manifest['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    (export / 'manifest.json').write_text(json.dumps(manifest))
    before = (export / 'events.ndjson').read_bytes()
    first = inventory(export, source=source)
    assert first == inventory(export, source=source) and first['contract']['source_verified']
    assert first['provenance']['synthetic_markers']
    source.write_bytes(b'changed source')
    second = inventory(export, source=source)
    assert not second['contract']['source_verified'] and not second['contract']['compatible']
    assert (export / 'events.ndjson').read_bytes() == before


def test_actor_channel_mismatch_is_unbound_instead_of_inheriting_lifetime(export):
    write_rows(export, [rows()[0], {'type': 'rpc_received', 'time_ms': 10, 'actor_net_guid': 9,
                                   'channel': 3, 'function_name': 'PlayDoorSounds'}])
    report = inventory(export)
    assert report['scan']['flags'] == ['actor_channel_mismatch']
    assert report['candidates'][1]['binding'] == 'unbound'


@pytest.mark.parametrize('edit', [{'stats': []}, {'diagnostics': [3]}, {'suppressed_diagnostic_count': []}])
def test_malformed_manifest_is_rejected_cleanly(export, edit):
    write_rows(export, rows())
    manifest = json.loads((export / 'manifest.json').read_text()); manifest.update(edit)
    (export / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='manifest'):
        inventory(export)


def test_worksheet_defaults_unverified_and_time_overlap_never_binds(export):
    write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent')
    value = worksheet(report)
    assert not validate_observations(value, report)
    observation = value['observations'][0]
    assert observation['time_ms'] is None and observation['review'] == 'unverified'
    observation.update(time_ms=30, uncertainty_ms=1, state='closed', action='switch',
                       evidence_lines=[4], candidate_key='actor:9:life:1', review='verified')
    result = review_observations(value, report)
    assert result['valid']
    assert result['scenes'][0]['nearby_retained_samples'][0]['line'] == 4
    assert 'not a feature binding' in result['scenes'][0]['note']


@pytest.mark.parametrize('edit', [
    {'clock': 'round_timer'}, {'audit_sha256': 'wrong'}, {'version': 99},
    {'observations': 'not-a-list'}, {'observations': [{'feature_id': []}]}])
def test_invalid_or_stale_worksheet_rejected(export, edit):
    write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent')
    value = worksheet(report); value.update(edit)
    assert validate_observations(value, report)


@pytest.mark.parametrize('edit', [
    {'review': 'verified'}, {'time_ms': -1}, {'time_ms': float('nan')}, {'time_ms': True},
    {'uncertainty_ms': float('inf')}, {'state': 'broken'}, {'action': 'guess'},
    {'candidate_key': []}, {'evidence_lines': [100]}, {'evidence_lines': [True]}])
def test_invalid_scene_fields_rejected(export, edit):
    write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent')
    value = worksheet(report); value['observations'][0].update(edit)
    assert validate_observations(value, report)


def test_modified_audit_is_not_accepted_as_same_evidence(export):
    write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent')
    value = worksheet(report); report['candidates'][0]['samples'][0]['row']['time_ms'] = 900
    assert 'audit integrity mismatch' in validate_observations(value, report)[0]


def test_scene_time_outside_known_duration_is_rejected(export):
    manifest = json.loads((export / 'manifest.json').read_text()); manifest['duration_ms'] = 100
    (export / 'manifest.json').write_text(json.dumps(manifest)); write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent'); value = worksheet(report)
    value['observations'][0]['time_ms'] = 101
    assert any('outside the replay duration' in e for e in validate_observations(value, report))


def test_multiple_scenes_for_one_feature_need_distinct_observation_ids(export):
    write_rows(export, rows())
    report = inventory(export, tags=TAGS, map_name='Ascent')
    value = worksheet(report)
    value['observations'].append(copy.deepcopy(value['observations'][0]))
    assert any('observation_id' in e for e in validate_observations(value, report))
    value['observations'][1]['observation_id'] = 'scene-2'
    assert not validate_observations(value, report)


def test_private_outputs_refuse_git_source_and_existing_artifacts(tmp_path, export):
    repo = tmp_path / 'repo'; repo.mkdir(); (repo / '.git').write_text('gitdir: elsewhere')
    for path in [repo / 'private', export / 'report', export]:
        with pytest.raises(ValueError):
            private_output(path, export)
    output = tmp_path / 'private'
    assert private_output(output, export) == output.resolve()
    output.mkdir()
    with pytest.raises(ValueError):
        private_output(output, export)


def test_html_escapes_private_payload_and_includes_coverage(export):
    write_rows(export, [{'type': 'actor_spawned', 'time_ms': 0, 'actor_net_guid': 1,
                         'actor_path': 'Door</pre><script>attack()</script>'}])
    page = render_report(inventory(export))
    assert '<script>attack()' not in page and '&lt;script&gt;attack()' in page
    assert 'Undecoded map groups omitted' in page

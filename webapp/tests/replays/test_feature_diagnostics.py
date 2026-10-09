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

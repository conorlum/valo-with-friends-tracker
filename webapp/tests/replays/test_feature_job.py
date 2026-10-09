"""Real isolated feature child: exact archived base/height, no visibility/height rebuild."""
import base64
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from map_feature_artifact_toys import base_case, snapshot_case, geometry_case, synthetic_consumer_runtime
from test_control_feature_artifacts_db import archive_factory


def request_case():
    from app.replays.map_feature_inputs import identify_features
    inp = identify_features('Summit', None, snapshot_case(), base_case(), consumers={'test': 1})
    return inp, {'mode': 'compile', 'inputs': {'key': asdict(inp.key),
                   'canonical_inputs': base64.b64encode(inp.canonical_inputs).decode()}, 'height': None}


def test_real_child_compilation_matches_direct_compiler_bytes():
    from app.services.control_feature_artifacts import run_feature_child
    from app.replays.map_feature_artifacts import decode_artifact, expanded_assets
    from app.control.features import compile_artifact
    inp, request = request_case()
    child = decode_artifact(run_feature_child('compile', json.dumps(request).encode()))
    direct = compile_artifact(geometry_case(flat=True), inp, child.code_commit)
    assert child.digest == direct.digest and child.manifest == direct.manifest
    assert expanded_assets(child.assets) == expanded_assets(direct.assets)


def test_child_verify_detects_tampered_archive_and_does_not_read_current_tags():
    from app.services.control_feature_artifacts import run_feature_child
    from app.replays.map_feature_artifacts import FeatureArtifactError
    _, request = request_case()
    wire = run_feature_child('compile', json.dumps(request).encode())
    verified = run_feature_child('verify', json.dumps({'mode': 'verify', 'artifact': json.loads(wire), 'height': None}).encode())
    assert json.loads(verified)['integrity'] == 'verified'
    artifact = json.loads(wire)
    artifact['digest'] = 'f' * 64
    with pytest.raises(FeatureArtifactError):
        run_feature_child('verify', json.dumps({'mode': 'verify', 'artifact': artifact, 'height': None}).encode())


def test_unknown_mode_and_conflicting_fields_fail_explicitly():
    from app.services.control_feature_artifacts import run_feature_child
    from app.replays.map_feature_artifacts import FeatureArtifactError
    for mode, request in [('unsupported', {'mode': 'unsupported'}),
                          ('compile', {**request_case()[1], 'artifact': {}})]:
        with pytest.raises(FeatureArtifactError):
            run_feature_child(mode, json.dumps(request).encode())


def test_parent_archive_service_import_does_not_load_heavy_modules():
    code = "import sys, app.services.control_feature_artifacts; print([n for n in sys.modules if n.split('.')[0] in ('numpy','scipy','PIL') or n.startswith('app.control')])"
    out = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == '[]'


def test_child_preparation_failure_never_becomes_no_features(archive_factory, monkeypatch):
    from app.services import control_feature_artifacts as service
    inp, _ = request_case()
    def fail(mode, payload):
        raise RuntimeError('compiler unavailable')
    monkeypatch.setattr(service, 'run_feature_child', fail)
    with archive_factory() as db:
        with pytest.raises(service.FeaturePreparationPending):
            service.prepare_artifact(db, inp, None)
        assert service.find_artifact(db, inp.key) is None


def test_verified_cache_rehashes_hits_and_never_caches_failed_correspondence():
    from dataclasses import replace
    from app.replays import map_feature_artifacts as codec
    from app.control.features import compile_artifact
    inp, _ = request_case()
    artifact = compile_artifact(geometry_case(flat=True), inp, '0' * 40)
    cache = codec.VerifiedArtifactCache(max_entries=1)
    verified = []
    def verify(item):
        verified.append(item.digest)
    cache.get_or_verify(artifact, verify)
    cache.get_or_verify(artifact, verify)
    assert verified == [artifact.digest] and cache.entries == 1 and cache.bytes_used > len(artifact.assets)
    with pytest.raises(codec.FeatureArtifactCorrupt):
        cache.get_or_verify(replace(artifact, assets=b'broken'), verify)
    assert cache.entries == 0
    def fail(item):
        raise codec.FeatureArtifactCorrupt('correspondence failed')
    with pytest.raises(codec.FeatureArtifactCorrupt):
        cache.get_or_verify(artifact, fail)
    assert cache.entries == 0
    tiny = codec.VerifiedArtifactCache(max_bytes=1)
    tiny.get_or_verify(artifact, verify)
    assert tiny.entries == 0 and tiny.bytes_used == 0


def test_durable_preparation_hit_skips_child_and_rehashes_archive(archive_factory, monkeypatch):
    from app.services import control_feature_artifacts as service
    inp, _ = request_case()
    with archive_factory() as db:
        artifact = service.prepare_artifact(db, inp, None)
        db.commit()
    def unexpected(*args):
        raise AssertionError('exact durable hit must not compile')
    monkeypatch.setattr(service, 'run_feature_child', unexpected)
    with archive_factory() as db:
        assert service.prepare_artifact(db, inp, None) == artifact

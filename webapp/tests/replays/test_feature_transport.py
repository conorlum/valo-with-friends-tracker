import json
from dataclasses import replace

import pytest

from app.replays import map_feature_artifacts as codec
from app.replays.map_feature_inputs import identify_features
from app.control.features import compile_artifact
from map_feature_artifact_toys import base_case, snapshot_case, geometry_case


def artifact_case():
    inp = identify_features('Summit', None, snapshot_case(), base_case(), consumers={'test': 1})
    return compile_artifact(geometry_case(flat=True), inp, '0' * 40)


def test_atomic_cache_validates_before_replacement_and_removes_corruption(tmp_path):
    artifact = artifact_case()
    seen = []
    codec.store_cached_artifact(tmp_path, artifact, lambda item: seen.append(item.digest))
    assert codec.load_cached_artifact(tmp_path, artifact.digest) == artifact
    with pytest.raises(codec.FeatureArtifactCorrupt):
        codec.store_cached_artifact(tmp_path, replace(artifact, assets=b'bad'), lambda item: None)
    assert codec.load_cached_artifact(tmp_path, artifact.digest) == artifact
    assert seen == [artifact.digest]
    (tmp_path / (artifact.digest + '.json')).write_bytes(b'bad')
    with pytest.raises(codec.FeatureArtifactCorrupt):
        codec.load_cached_artifact(tmp_path, artifact.digest)
    with pytest.raises(codec.FeatureArtifactMissing):
        codec.load_cached_artifact(tmp_path, artifact.digest)


def test_exact_loader_checks_context_and_digestless_never_reads_cache(tmp_path, monkeypatch):
    from app.control import task
    from app.replays import map_feature_inputs as fi
    artifact = artifact_case()
    codec.store_cached_artifact(tmp_path, artifact, lambda item: None)
    geo = geometry_case(flat=True)
    assert task.load_task_features({'map': 'Summit'}, geo, tmp_path) is None
    request = {'map': 'Summit', 'features': artifact.digest, 'geometry': {'features': artifact.digest}}
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', {'test': 1})
    assert task.load_task_features(request, geo, tmp_path) == artifact
    assert geo.features_sha == artifact.digest
    for changed in ({'map': 'Other'}, {'height': 'a' * 12}, {'geometry': {'features': 'b' * 64}}):
        with pytest.raises(codec.FeatureArtifactCorrupt):
            task.load_task_features(request | changed, geometry_case(flat=True), tmp_path)
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', {})
    with pytest.raises(codec.UnsupportedFeatureCompiler):
        task.load_task_features(request, geometry_case(flat=True), tmp_path)


@pytest.mark.parametrize('error,kind,code', [
    (codec.FeatureArtifactMissing('evicted'), 'infra', 'features_missing'),
    (codec.FeatureArtifactCorrupt('corrupt'), 'infra', 'features_corrupt'),
    (codec.UnsupportedFeatureCompiler('unsupported'), 'compat', 'features_unsupported'),
])
def test_feature_failure_is_explicit(error, kind, code):
    assert codec.feature_failure(error) == {'status': 'failed', 'error_kind': kind, 'error_code': code, 'error': str(error)}


def test_real_http_push_miss_corruption_and_client_protocol(tmp_path):
    from test_replay_worker_control import serve, server
    from app.services.replay_control_remote import ControlClient, NeedsFeatures
    import sys
    artifact = artifact_case()
    settings = server.Settings(control_cache_dir=tmp_path / 'cache', control_cmd=[sys.executable, '-m', 'replay_worker.control_job'])
    runner = server.ControlRunner(settings)
    client = ControlClient(serve(tmp_path, runner))
    request = {'key': 'features-cold', 'map': 'Summit', 'blob': '', 'link': {}, 'features': artifact.digest}
    assert client.health()['control']['features'] == codec.protocol_identity()
    with pytest.raises(NeedsFeatures) as missing:
        client.submit(request)
    assert missing.value.digest == artifact.digest
    assert client.push_features(artifact)['digest'] == artifact.digest
    assert client.submit(request)['id']
    (settings.control_cache_dir / 'features' / (artifact.digest + '.json')).write_bytes(b'corrupt')
    with pytest.raises(NeedsFeatures):
        client.submit(request | {'key': 'features-evicted'})


def test_post_admission_missing_artifact_is_infrastructure(monkeypatch, tmp_path):
    from app.control import task
    monkeypatch.setattr(task, '_load', lambda *args, **kwargs: geometry_case(flat=True))
    result = task.compute_task({'map': 'Summit', 'features': 'a' * 64})
    assert result['status'] == 'failed' and result['error_kind'] == 'infra'
    assert result['error_code'] == 'features_missing'


def test_local_collector_does_not_store_retryable_feature_failure():
    import compute_control
    class Planned:
        map_name = 'Summit'
    assert compute_control.store_result(None, Planned(), codec.feature_failure(codec.FeatureArtifactMissing('evicted'))).startswith('skipped:')

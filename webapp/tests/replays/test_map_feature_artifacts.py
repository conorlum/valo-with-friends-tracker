"""Complete archive bytes, bounded decoding, and definition/compiled correspondence."""
import base64
import copy
import gzip
import hashlib
import json
from dataclasses import replace

import pytest
from map_feature_artifact_toys import base_case, geometry_case, snapshot_case, source_case, synthetic_consumer_runtime


def compiled(*, multi=False, ground=0, origin=100, source=None):
    from app.control.features import compile_artifact
    from app.replays.map_feature_inputs import identify_features
    geo = geometry_case(multi=multi, ground_dm=ground, origin_dm=origin)
    inputs = identify_features('Summit', geo.height_sha, snapshot_case(source), base_case(), consumers={'test': 1})
    return geo, compile_artifact(geo, inputs, '0' * 40)


@pytest.mark.parametrize('available', [{}, {'test': 2}])
def test_review_historical_verifier_requires_available_consumer_versions(monkeypatch, available):
    from app.control.features import verify_artifact
    from app.replays import map_feature_inputs as fi
    from app.replays.map_feature_artifacts import check_artifact, UnsupportedFeatureCompiler
    geo, artifact = compiled()
    check_artifact(artifact)
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', available)
    with pytest.raises(UnsupportedFeatureCompiler, match='consumer'):
        verify_artifact(artifact, geo)


@pytest.mark.parametrize('problem', ['off_ground', 'transitive', 'cycle', 'missing', 'behavior', 'bounds', 'route'])
def test_review_parent_dependencies_gate_only_the_dependent_bundle(problem):
    from app.control.features import bundle_status
    mf = source_case()['map_features']
    child, parent = mf['features']
    child['parent'] = parent['id']
    independent = copy.deepcopy(child)
    independent.update(id='feature-4', bundle='bundle-4')
    independent.pop('parent')
    mf['features'].append(independent)
    mf['bundles'].append({'id': 'bundle-4', 'enabled': True, 'runtime_consumer': 'test', 'members': ['feature-4']})
    if problem == 'off_ground':
        parent['states'][0]['footprint'] = {'type': 'point', 'uv': [100, 100]}
    elif problem == 'transitive':
        grandparent = copy.deepcopy(parent)
        grandparent.update(id='feature-3', bundle=None)
        grandparent['states'][0]['footprint'] = {'type': 'point', 'uv': [100, 100]}
        parent['parent'] = grandparent['id']
        mf['features'].append(grandparent)
    elif problem == 'cycle':
        parent['parent'] = child['id']
    elif problem == 'missing':
        parent['parent'] = 'feature-99'
    elif problem == 'behavior':
        parent['initial_state'] = None
    elif problem == 'bounds':
        parent['states'][0]['sight_bounds']['top']['value'] = 0
    else:
        mf['routes'] = [{'id': 'route-1', 'owner': parent['id'], 'endpoints': [{'id': 'a', 'uv': None}]}]
    statuses = bundle_status(geometry_case(flat=True), mf, consumers={'test'})
    assert not statuses['bundle-1'].publishable and statuses['bundle-1'].reasons
    assert statuses['bundle-4'].publishable


def test_archive_contains_loadable_masks_and_pending_is_not_none():
    from app.control.features import verify_artifact
    from app.replays.map_feature_artifacts import check_artifact, decode_artifact, encode_artifact, unpack_mask
    geo, artifact = compiled()
    check_artifact(artifact)
    assert decode_artifact(encode_artifact(artifact)) == artifact
    verify_artifact(artifact, geo)
    parts = json.loads(gzip.decompress(artifact.assets))
    state = parts['states']['feature-1:closed']
    assert state['blocked'] == [5160]
    mask = state['occluders'][0]['mask']
    assert mask['shape'] == [1024, 1024] and sum(unpack_mask(mask)) == 64
    assert parts['bindings']['feature-1']['nodes'] == [5160]
    assert parts['base_domain']['barrier'] is None
    _, pending = compiled(multi=True)
    assert pending.manifest['intended_bundles'] == ['bundle-1']
    assert pending.manifest['active_bundles'] == [] and pending.manifest['pending']
    empty = json.loads(gzip.decompress(pending.assets))
    assert empty['states'] == {} and empty['arcs'] == [] and empty['triggers'] == {}
    assert empty['base_deltas'] == {}


def test_ground_origin_and_audit_metadata_have_distinct_identity_effects():
    from app.replays.map_feature_artifacts import check_artifact
    _, a = compiled()
    _, b = compiled(ground=20, origin=80)
    assert a.digest != b.digest
    band = json.loads(gzip.decompress(b.assets))['states']['feature-1:closed']['occluders'][0]
    assert (band['bottom'], band['top']) == pytest.approx((1.1, 4.1))
    check_artifact(replace(a, code_commit='a' * 40))
    _, c = compiled(source=source_case())
    assert a.digest == c.digest


def test_equivalent_gzip_stream_is_accepted_but_trailing_and_partial_streams_are_not():
    from app.replays.map_feature_artifacts import check_artifact, FeatureArtifactCorrupt
    _, a = compiled()
    other = gzip.compress(gzip.decompress(a.assets), compresslevel=1, mtime=12)
    assert other != a.assets
    check_artifact(replace(a, assets=other))
    for assets in (a.assets + b'x', a.assets[:-1], a.assets + a.assets):
        with pytest.raises(FeatureArtifactCorrupt):
            check_artifact(replace(a, assets=assets))


def test_packed_mask_padding_dimensions_and_values_are_checked_before_allocation():
    from app.replays.map_feature_artifacts import pack_mask, unpack_mask, FeatureArtifactCorrupt
    mask = pack_mask(b'\1\0\1\0\1\0\1\0\1', (3, 3))
    assert mask['data'] == 'VQE='  # little bit order: 0x55, 0x01
    assert unpack_mask(mask) == b'\1\0\1\0\1\0\1\0\1'
    for patch in ({'shape': [3, 4]}, {'bytes': 1}, {'bitorder': 'big'}, {'data': 'bad!'},
                  {'data': base64.b64encode(b'\x55\xff').decode()}, {'shape': [10**12, 10**12]}):
        with pytest.raises(FeatureArtifactCorrupt):
            unpack_mask({**mask, **patch})
    with pytest.raises(FeatureArtifactCorrupt):
        pack_mask(b'\2', (1,))


def test_tampered_input_manifest_and_loaded_parts_fail_integrity():
    from app.replays.map_feature_artifacts import check_artifact, FeatureArtifactCorrupt
    _, a = compiled()
    manifest = copy.deepcopy(a.manifest)
    manifest['active_bundles'] = []
    parts = json.loads(gzip.decompress(a.assets))
    parts['states']['feature-1:closed']['blocked'] = [5161]
    for bad in (replace(a, inputs=a.inputs + b' '), replace(a, manifest=manifest),
                replace(a, digest='f' * 64), replace(a, assets=gzip.compress(json.dumps(parts).encode(), mtime=0))):
        with pytest.raises(FeatureArtifactCorrupt):
            check_artifact(bad)


def test_hash_valid_but_forged_compilation_fails_heavy_correspondence():
    from app.replays.map_feature_artifacts import canonical_json, check_artifact, FeatureArtifactCorrupt
    from app.control.features import verify_artifact
    geo, a = compiled()
    parts = json.loads(gzip.decompress(a.assets))
    parts['states']['feature-1:closed']['blocked'] = [5161]
    manifest = copy.deepcopy(a.manifest)
    manifest['compiled']['states'] = hashlib.sha256(canonical_json(parts['states'])).hexdigest()
    forged = replace(a, digest=hashlib.sha256(canonical_json(manifest)).hexdigest(), manifest=manifest,
                     assets=gzip.compress(canonical_json(parts), mtime=0))
    check_artifact(forged)
    with pytest.raises(FeatureArtifactCorrupt):
        verify_artifact(forged, geo)


def test_decode_rejects_duplicate_keys_nonfinite_bad_base64_and_wire_versions():
    from app.replays.map_feature_artifacts import decode_artifact, encode_artifact, FeatureArtifactCorrupt
    _, a = compiled()
    wire = json.loads(encode_artifact(a))
    for body in (b'{"wire":1,"wire":1}', b'{"wire":NaN}',
                 json.dumps({**wire, 'wire': 99}).encode(), json.dumps({**wire, 'assets': '!'}).encode()):
        with pytest.raises(FeatureArtifactCorrupt):
            decode_artifact(body)


def test_bounded_expansion_rejects_zip_bombs(monkeypatch):
    from app.replays import map_feature_artifacts as codec
    _, a = compiled()
    monkeypatch.setattr(codec, 'MAX_EXPANDED_BYTES', 128)
    with pytest.raises(codec.FeatureArtifactCorrupt, match='limit'):
        codec.check_artifact(a)
    monkeypatch.setattr(codec, 'MAX_WIRE_BYTES', 64)
    with pytest.raises(codec.FeatureArtifactCorrupt, match='limit'):
        codec.decode_artifact(b' ' * 65)


def test_unavailable_compiler_is_separate_from_hash_integrity():
    from app.replays.map_feature_artifacts import canonical_json, check_artifact, UnsupportedFeatureCompiler
    from app.control.features import verify_artifact
    geo, a = compiled()
    key = replace(a.key, compiler_version=99)
    manifest = copy.deepcopy(a.manifest)
    manifest['key']['compiler_version'] = 99
    other = replace(a, key=key, manifest=manifest, digest=hashlib.sha256(canonical_json(manifest)).hexdigest())
    check_artifact(other)
    with pytest.raises(UnsupportedFeatureCompiler):
        verify_artifact(other, geo)


def test_legacy_manifest_entry_point_retains_intended_pending_outcomes():
    from app.control import features
    manifest = features.manifest(geometry_case(multi=True), source_case()['map_features'], consumers=frozenset({'test'}))
    assert manifest is not None and manifest['intended_bundles'] == ['bundle-1']
    assert manifest['bundles'] == [] and manifest['pending']

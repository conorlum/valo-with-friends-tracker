"""Compile-to-runtime combinations missed by the original per-primitive checks."""
import copy

import numpy as np
import pytest

from app.control import features as cf
from app.control.feature_runtime import FeatureRuntime, prepare_geometry
from app.replays import map_feature_inputs as fi, map_feature_artifacts as fa
from test_adaptive_ground import mixed
from test_ascent_features import runtime_case


@pytest.mark.slow
@pytest.mark.parametrize('wall_attachment', [False, True])
def test_compiled_descending_door_on_refined_ground_and_wall_attachment(wall_attachment):
    geo = mixed(name='Ascent')
    _, mf, f, evidence = runtime_case()
    f['bundle'] = mf['bundles'][0]['id'] = 'bundle-1'
    f['kind'] = 'door'
    x0 = 188 if wall_attachment else 200
    shape = {'type': 'polygon', 'uv': [[x * 10000 / 1024, y * 10000 / 1024]
        for x, y in [(x0, 200), (204, 200), (204, 208), (x0, 208)]]}
    for state in f['states']:
        if state.get('footprint'):
            state['footprint'] = copy.deepcopy(shape)
    f['sliding']['movement_cutoff'] = {'status': 'known', 'value': .3, 'unit': 'fraction', 'basis': 'conservative'}
    base = dict(sight=cf._packed_mask(geo.sight), walk=cf._packed_mask(geo.walk_px),
                barrier=None, scale=geo.uv_per_unit / 10000, specials=[], legacy={})
    source = fi.capture_source_snapshot('Ascent', fi.canonical_json({'maps': {'Ascent': {'map_features': mf}}}))
    inp = fi.identify_features('Ascent', geo.height_sha, source, base)
    item = cf.compile_artifact(geo, inp, '0' * 40)
    assert item.manifest['active_bundles'] == [mf['bundles'][0]['id']], item.manifest['pending']
    cf.verify_artifact(item, geo)
    geo.features, geo.features_sha = item, item.digest
    runtime = FeatureRuntime(prepare_geometry(geo), {**evidence, 't_end': 9})
    # Source closes at 1 s and destroys at 8 s; cutoff occurs at 2.5 s.
    for t, blocked in [(0, False), (1, False), (2.5, False), (2.55, True), (6.01, True), (8, False)]:
        movement, sight = runtime.sample(t)
        assert movement.any() == blocked
        assert not (movement & ~geo.walk_n).any(), 'wall attachments must never acquire walking nodes'
        if blocked:
            child = geo.node_at_px(202, 202, 0)
            assert movement[child]
            assert any(o.mask[202, 190] for o in sight) if wall_attachment else sight


def test_missing_round_source_is_not_an_infrastructure_retry():
    from app.control.map_pool_runtime import MapPoolRuntime
    from test_map_pool_runtime import runtime_case as pool_case, summit
    geo, blob = pool_case(summit(), 'Summit')
    blob.pop('map_messages')
    with pytest.raises(fa.FeatureSourceUnavailable) as caught:
        MapPoolRuntime(geo, blob)
    assert fa.feature_failure(caught.value)['error_kind'] == 'inputs'
    assert fa.feature_failure(caught.value)['error_code'] == 'features_source_unavailable'


def top_feature_case(*, remove_sight=False, tops=True):
    from test_top_nodes import stored_topped
    geo = stored_topped(((202, 202, 12),) if tops else None, name='Ascent')
    _, mf, feature, _ = runtime_case()
    feature['bundle'] = mf['bundles'][0]['id'] = 'bundle-1'
    feature['kind'] = 'door'
    shape = {'type': 'polygon', 'uv': [[x*10000/1024, y*10000/1024]
        for x, y in [(192, 200), (200, 200), (200, 208), (192, 208)]]}
    for state in feature['states']:
        if state.get('footprint'):
            state['footprint'] = copy.deepcopy(shape)
    feature['sliding']['movement_cutoff'] = {
        'status': 'known', 'value': .3, 'unit': 'fraction', 'basis': 'conservative'}
    if remove_sight:
        geo.sight = geo.sight.copy()
        geo.sight[200:208, 192:200] = True
        feature['base_edits'] = {'remove_sight': copy.deepcopy(shape)}
    base = dict(sight=cf._packed_mask(geo.sight), walk=cf._packed_mask(geo.walk_px),
        cover=cf._packed_mask(geo.cover_px > 0), cover_sha=geo.cover_sha,
        barrier=None, scale=geo.uv_per_unit/10000, specials=[], legacy={})
    raw = fi.canonical_json({'maps': {'Ascent': {'map_features': mf}}})
    source = fi.capture_source_snapshot('Ascent', raw)
    inp = fi.identify_features('Ascent', geo.height_sha, source, base)
    return geo, inp, base, raw


@pytest.mark.parametrize('remove_sight', [False, True])
def test_top_feature_archive_prepares_with_the_complete_node_count(remove_sight):
    geo, inp, _, _ = top_feature_case(remove_sight=remove_sight)
    artifact = cf.compile_artifact(geo, inp, '0'*40)
    assert artifact.manifest['active_bundles'] == ['bundle-1'], artifact.manifest['pending']
    artifact = fa.decode_artifact(fa.encode_artifact(artifact))
    cf.verify_artifact(artifact, geo)
    assets = fi.read_json(fa.expanded_assets(artifact.assets))
    assert geo.top_nodes.size and assets['nodes'] == geo.n
    geo.features, geo.features_sha = artifact, artifact.digest
    prepared = prepare_geometry(geo)
    assert (prepared is not geo) == remove_sight
    assert prepared.n == assets['nodes']
    assert np.array_equal(prepared.top_nodes, geo.top_nodes)
    assert np.array_equal(prepared.node_cell, geo.node_cell)
    assert np.array_equal(prepared.node_space, geo.node_space)


def test_height_rebuild_selects_a_new_feature_key_and_rebuilds_before_control(tmp_path, monkeypatch):
    import base64
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app.control import heights as hc
    from app.replays import height_inputs, map_feature_sources
    from app.services import control_feature_artifacts as archive, control_heights, replay_control

    old_geo, old_input, base, raw = top_feature_case(tops=False)
    geo, inp, _, _ = top_feature_case()
    assert old_geo.n != geo.n and old_input.key.height_digest != inp.key.height_digest
    assert old_input.key.tags_digest == inp.key.tags_digest  # height alone changes the archive key
    old_artifact = cf.compile_artifact(old_geo, old_input, '0'*40)
    rows, queries, compiled = {}, [], []
    def store(db, artifact, **kwargs):
        key = artifact.key
        rows[key] = SimpleNamespace(map_name=key.map_name, height_digest=key.height_digest,
            tags_digest=key.tags_digest, compiler_version=key.compiler_version, height_asset=None,
            digest=artifact.digest, manifest=artifact.manifest, inputs=artifact.inputs,
            assets=artifact.assets, code_commit=artifact.code_commit)
        return artifact
    store(None, old_artifact)
    class Query:
        def filter_by(self, **fields):
            self.key = fi.FeatureKey(**fields)
            queries.append(self.key)
            return self
        def with_entities(self, *entities):
            return self
        def one_or_none(self):
            return rows.get(self.key)
    db = SimpleNamespace(query=lambda *args: Query(), commit=lambda: None,
                         rollback=lambda: pytest.fail('feature rebuild rolled back'))
    tags = fi.read_json(raw)['maps']
    index = {'Ascent': {'kill_lines': {'passes': True}, 'height_sha': old_geo.height_sha}}
    snapshot = (index, tags, {'Ascent': {'xMultiplier': geo.uv_per_unit/10000}}, raw)
    monkeypatch.setattr(replay_control, '_current_snapshot', lambda: snapshot)
    monkeypatch.setattr(control_heights, 'select_height',
                        lambda *a: height_inputs.HeightSelection('asset', geo.height_sha))
    monkeypatch.setattr(map_feature_sources, 'base_snapshot', lambda *a: base)
    height_path = tmp_path/'height.npz'
    hc.save_asset(height_path, geo.heights)
    height_bytes = height_path.read_bytes()
    monkeypatch.setattr(replay_control, 'exact_height_bytes', lambda *a: height_bytes)
    def compile_here(mode, payload):
        assert mode == 'compile'
        request = fi.read_json(payload)
        assert base64.b64decode(request['height']) == height_bytes
        assert request['inputs']['key']['height_digest'] == geo.height_sha
        compiled.append(inp.key)
        restored = cf.geometry_from_feature_inputs(inp, hc.load_asset(height_path))
        return fa.encode_artifact(cf.compile_artifact(restored, inp, '0'*40))
    monkeypatch.setattr(archive, 'run_feature_child', compile_here)
    monkeypatch.setattr(archive, 'store_artifact', store)
    pending = replay_control.resolve_current_geometry(db, 'Ascent')
    assert pending.state == 'features_pending' and pending.pinned is None
    assert pending.feature_input.key == inp.key
    replay_control.prepare_discovered(lambda: nullcontext(db), ['Ascent'], {}, 0)
    ready = replay_control.resolve_current_geometry(db, 'Ascent')
    assert ready.state == 'ready' and compiled == [inp.key]
    assert queries and all(key == inp.key for key in queries)
    artifact = archive.find_artifact(db, ready.pinned.feature_key)
    assert artifact.digest == ready.pinned.artifact_digest != old_artifact.digest
    geo.features, geo.features_sha = artifact, artifact.digest
    assert prepare_geometry(geo).n == fi.read_json(fa.expanded_assets(artifact.assets))['nodes']

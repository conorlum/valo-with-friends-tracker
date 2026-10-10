"""Qualified Ascent definitions through the real archive, engine, observer and gap cache."""
import copy
import json

import numpy as np
import pytest

from app.control import engine, features, geometry, task
from app.replays import control_format, format, map_feature_artifacts as fa, map_feature_inputs as fi
from map_feature_artifact_toys import base_case, geometry_case
from test_ascent_features import runtime_case
from control_toys import blob


def compiled_case():
    _, mf, feature, evidence = runtime_case()
    mf = copy.deepcopy(mf)
    feature = mf['features'][0]
    mf['bundles'][0]['id'] = feature['bundle'] = 'bundle-1'
    shape = feature['states'][0]['footprint']
    shape['uv'] = [[x + 8 * 10000 / 1024, y] for x, y in shape['uv']]
    feature['base_edits'] = {'remove_sight': copy.deepcopy(shape)}
    geo = geometry_case(); geo.name = 'Ascent'
    # The static asset paints a door as sight-blocking; the archive must remove that permanent paint.
    geo.sight = geo.sight.copy()
    geo.sight[320:328, 328:336] = True
    base = base_case(); base['sight'] = features._packed_mask(geo.sight)
    raw = fi.canonical_json({'maps': {'Ascent': {'map_features': mf, 'specials': []}}})
    source = fi.capture_source_snapshot('Ascent', raw)
    inputs = fi.identify_features('Ascent', geo.height_sha, source, base)
    artifact = features.compile_artifact(geo, inputs, '0' * 40)
    assert artifact.manifest['active_bundles'] == ['bundle-1'], artifact.manifest['pending']
    features.verify_artifact(artifact, geo)
    geo.features, geo.features_sha = artifact, artifact.digest
    evidence['map_features']['sha256'] = 'd' * 64
    data = blob({0: ('A', [(0, 324, 324, 0)]), 5: ('B', [(0, 348, 324, 180)])}, t_end=9)
    data.update(map='Ascent', **evidence)
    return geo, artifact, mf, data, base, source


def test_archive_engine_and_gaps_only_use_the_same_domain_and_timeline(tmp_path, monkeypatch):
    from app.gaps import cache, detect
    from app.control.feature_runtime import prepare_geometry
    geo, artifact, _, data, _, _ = compiled_case()
    original = geo.sight.copy()
    prepared = prepare_geometry(geo)
    assert prepared is not geo and not prepared.sight[324, 332]
    assert np.array_equal(geo.sight, original)  # the shared source is never mutated
    assert np.array_equal(prepared.node_cell, geo.node_cell)
    monkeypatch.setattr(geometry, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr(cache, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr(task, '_load', lambda *args, **kwargs: geo)
    monkeypatch.setattr(task, '_ARTIFACTS', fa.VerifiedArtifactCache())
    fa.store_cached_artifact(tmp_path / 'features', artifact, lambda a: features.verify_artifact(a, geo))
    link = {'sides': {}, 'db_deaths': []}
    envelope = control_format.input_envelope('test.ascent', '0' * 64, link, task.geometry_used(geo))
    job = {'key': 1, 'map': 'Ascent', 'features': artifact.digest, 'height': geo.height_sha,
           'height_mode': 'asset', 'geometry': task.geometry_used(geo), 'inputs': envelope,
           'blob': format.encode_blob(data), 'link': link,
           'gaps': {'replay_id': 1, 'round': 1, 'fingerprint': 'a' * 16, 'gap_fingerprint': 'b' * 16}}
    first = task.compute_task(job)
    assert first['status'] == 'ok', first
    assert first['gaps']['run']['status'] == 'ok', first['gaps']
    assert control_format.unpack_summary(first['summary'])['map_features'] == {
        'artifact': artifact.digest, 'timeline': 'd' * 64, 'keys': ['ascent_market']}
    records = [rec for rec, _ in cache.replay(next(tmp_path.glob('*.ticks.pkl.gz')))]
    target = int(prepared.node_of[prepared.cell_of_px(348, 324), 0])
    at = lambda t: next(rec for rec in records if rec.t == pytest.approx(t))
    assert at(0).players[0].live[target]
    assert not at(6.002).players[0].live[target]
    assert at(8).players[0].live[target]
    rnd = engine.RoundInputs(data, prepared)
    detector = detect.GapDetector(prepared, rnd)
    assert detect.REASONS[detector._reasons(0, False, np.array([target]), at(6.002))[0]] == 'map_feature_closed'
    monkeypatch.setattr(engine, 'compute_round', lambda *a, **kw: pytest.fail('gaps-only cache hit ran engine'))
    again = task.compute_task({**job, 'gaps_only': True})
    assert again['gaps'] == first['gaps']
    # Removing the tick cache exercises the same domain on the live gaps-only path.
    monkeypatch.undo()
    monkeypatch.setattr(geometry, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr(cache, 'cache_dir', lambda: tmp_path)
    monkeypatch.setattr(task, '_load', lambda *args, **kwargs: geo)
    for path in tmp_path.glob('*.ticks.pkl.gz'):
        path.unlink()
    live = task.compute_task({**job, 'gaps_only': True})
    assert live['gaps'] == first['gaps']


def test_ascent_compiler_refuses_duplicate_binding_flat_map_and_unresolved_clearance():
    geo, _, mf, _, _, _ = compiled_case()
    flat = geometry_case(flat=True); flat.name = 'Ascent'
    assert not features.bundle_status(flat, mf)['bundle-1'].publishable
    broken = copy.deepcopy(mf)
    broken['features'][0]['sliding']['movement_clearance'] = {'status': 'unresolved'}
    assert not features.bundle_status(geo, broken)['bundle-1'].publishable
    duplicate = copy.deepcopy(mf['features'][0]); duplicate.update(id='feature-2', bundle='bundle-2')
    mf['features'].append(duplicate)
    mf['bundles'].append({'id': 'bundle-2', 'enabled': True, 'runtime_consumer': 'ascent_replay_v1', 'members': ['feature-2']})
    assert all('duplicate Ascent replay binding' in st.reasons for st in features.bundle_status(geo, mf).values())


def test_private_candidate_preserves_annotations_and_refuses_conflicting_ownership():
    from scripts.prepare_ascent_features import prepare
    from app.replays.map_feature_schema import empty
    _, mf, _, _ = runtime_case()
    mf = copy.deepcopy(mf); mf['bundles'] = []
    feature = mf['features'][0]; feature.pop('bundle', None)
    market = copy.deepcopy(feature); market['id'] = 'feature-9'
    garden = copy.deepcopy(feature); garden['id'] = 'feature-11'
    glass = {'id': 'feature-12', 'initial_state': 'intact', 'transitions': [], 'states': [
        {'name': 'intact', 'blocks_movement': False, 'blocks_sight': False},
        {'name': 'broken', 'terminal': True, 'blocks_movement': False, 'blocks_sight': False}]}
    mf['features'] = [market, garden, glass]
    tags = {'maps': {'Ascent': {'map_features': mf}, 'Summit': {'map_features': empty(), 'note': 'preserve me'}}}
    before = json.dumps(tags, sort_keys=True)
    bindings = {'ascent_market': 'feature-9', 'ascent_garden': 'feature-11', 'ascent_heaven_glass': 'feature-12'}
    candidate, report = prepare(tags, bindings)
    assert report.ok and candidate['maps']['Summit'] == tags['maps']['Summit']
    assert json.dumps(tags, sort_keys=True) == before
    assert [f['bundle'] for f in candidate['maps']['Ascent']['map_features']['features']] == ['bundle-1','bundle-2','bundle-3']
    assert prepare(candidate, bindings)[0] == candidate
    tags['maps']['Ascent']['map_features']['bundles'] = [{'id': 'bundle-1', 'enabled': False, 'members': ['feature-9'], 'runtime_consumer': 'other'}]
    with pytest.raises(ValueError, match='belongs to another bundle'):
        prepare(tags, bindings)

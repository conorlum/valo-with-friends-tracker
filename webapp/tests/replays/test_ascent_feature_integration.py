"""Qualified Ascent definitions through the real archive, engine, observer and gap cache."""
import copy
import json
import math

import numpy as np
import pytest

from app.control import engine, features, geometry, task
from app.replays import control_format, format, map_feature_artifacts as fa, map_feature_inputs as fi
from map_feature_artifact_toys import base_case, geometry_case
from test_ascent_features import runtime_case
from control_toys import blob


def compiled_case(cutoff=None):
    _, mf, feature, evidence = runtime_case()
    mf = copy.deepcopy(mf)
    feature = mf['features'][0]
    if cutoff is not None:
        from app.replays.map_feature_schema import known
        feature['sliding']['movement_cutoff'] = {**known(cutoff, 'fraction'), 'basis': 'conservative'}
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


@pytest.mark.slow
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


@pytest.mark.slow
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


@pytest.mark.slow
def test_archived_conservative_cutoff_is_eligible_and_samples_exact_boundary():
    from app.control.feature_runtime import FeatureRuntime
    from app.replays.map_feature_schema import known
    geo, _, mf, data, base, _ = compiled_case()
    f = mf['features'][0]
    f['sliding']['movement_clearance'] = {'status':'unresolved'}
    f['sliding']['movement_cutoff'] = {**known(.3, 'fraction'), 'basis':'conservative'}
    assert features.bundle_status(geo, mf)['bundle-1'].publishable
    raw = fi.canonical_json({'maps':{'Ascent':{'map_features':mf, 'specials':[]}}})
    inputs = fi.identify_features('Ascent', geo.height_sha, fi.capture_source_snapshot('Ascent', raw), base)
    artifact = features.compile_artifact(geo, inputs, '0'*40)
    features.verify_artifact(artifact, geo)
    geo.features = artifact
    runtime = FeatureRuntime(geo, data)
    assert 2.5 in runtime.transitions  # close began at 1 s, plus 30% of five seconds
    assert not runtime.sample(2.5)[0].any()
    assert runtime.sample(2.501)[0].any()
    assert not runtime.sample(8)[0].any()  # destruction releases the door
    assert not features.bundle_status(geometry_case(flat=True), mf)['bundle-1'].publishable


@pytest.mark.slow
def test_market_reopen_releases_doorway_at_first_nonblocking_float(monkeypatch):
    import math
    from app.control.feature_runtime import FeatureRuntime
    from app.replays.ascent_features import state_at
    from app.replays.map_feature_motion import movement_blocked, movement_cutoff
    geo, _, mf, data, _, _ = compiled_case(cutoff=.3)
    timeline = data['map_features']['features'][0]
    # Market door times from the observed round, including qualified completion evidence.
    timeline['events'] = [
        {'t': 21.743, 'state': 'closing', 'rpc': 'PlayDoorSounds', 'line': 100},
        {'t': 26.748, 'state': 'closed', 'rpc': 'PlayDoorSounds', 'line': 101},
        {'t': 59.475, 'state': 'opening', 'rpc': 'PlayDoorSounds', 'line': 102},
        {'t': 64.475, 'state': 'open', 'rpc': 'PlayDoorSounds', 'line': 103, 'evidence': [104]}]
    data['t_end'] = 70   # the observed round's length; releases past the end are never sampled
    runtime = FeatureRuntime(geo, data)
    doorway = runtime.sample(30)[0]
    assert doorway.any()
    moving = runtime.sample(61)[1]
    assert moving and all(not occ.cache_resting for occ in moving)
    resting = runtime.sample(30)[1]
    assert resting and all(occ.cache_resting for occ in resting)
    assert not runtime.sample(65)[1]  # Fully open panels have no sight bounds.
    feature = mf['features'][0]
    boundary = 59.475 + 5 * (1 - movement_cutoff(feature))
    release = runtime.reopened_by(65)[doorway]
    assert np.all((release > boundary) & (release <= boundary + 1e-6))
    assert len(set(release)) == 1
    when = float(release[0])
    assert movement_blocked(feature, state_at(timeline, when)['closure']) is False
    # On the engine's analytic clock (6 decimals), so the instant it evaluates is the released one.
    assert round(when, 6) == when == 62.975001
    assert movement_blocked(feature, state_at(timeline, round(when - 1e-6, 6))['closure']) is True
    # Retain a blocker at the empty open pose to check the runtime's cache annotation there too.
    from app.replays import map_feature_motion as motion
    sample_effects = motion.sample_effects
    def with_open_blocker(*args):
        effects, occ = sample_effects(*args)
        return effects, occ or resting
    monkeypatch.setattr(motion, 'sample_effects', with_open_blocker)
    cache_runtime = FeatureRuntime(geo, data)
    assert all(not occ.cache_resting for occ in cache_runtime.sample(61)[1])
    assert all(occ.cache_resting for occ in cache_runtime.sample(65)[1])


@pytest.mark.slow
@pytest.mark.parametrize('start', [59.475, 59.5, 53.253])
def test_reopen_release_is_on_the_engine_clock(start):
    # Branch review B1: a float-line release (63.00000000000001) rounded back to a blocked 63.0 in
    # engine.analytic_times, so the doorway first read clear at the next half-second tick.
    from app.control.engine import snap_after
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data, _, _ = compiled_case(cutoff=.3)
    data['map_features']['features'][0]['events'] = [
        {'t': 1, 'state': 'closing'}, {'t': 6.002, 'state': 'closed'},
        {'t': start, 'state': 'opening'}, {'t': start + 5, 'state': 'open'}]
    runtime = FeatureRuntime(geo, data)
    [release] = [t for t in runtime.transitions if start < t < start + 5]
    assert round(release, 6) == release
    doorway = runtime.sample(30)[0]
    assert not runtime.sample(release)[0][doorway].any()
    assert runtime.sample(round(release - 1e-6, 6))[0][doorway].all()
    assert snap_after(release) >= release


@pytest.mark.parametrize('later,expected', [(64.475, 62.975001), (62.975001, None), (60, None)])
def test_opening_release_is_bounded_by_the_next_event(later, expected):
    # Branch review B6: an opening superseded before its release is not searched (nor rescanned per float).
    from app.control.feature_runtime import _opening_release
    calls = []

    def blocked(_feature, closure):
        calls.append(closure)
        return closure > .3
    assert _opening_release({}, 59.475, 5, later, blocked) == expected
    assert len(calls) <= 2 + math.ceil(math.log2(5 * 10 ** 6 + 1000))


def _cut(cutoff):
    return lambda _feature, closure: None if closure is None else closure > cutoff


def test_last_opening_of_the_round_still_releases():
    # Round two R2-1: no next event (the round ends mid-opening) passed math.inf into math.ceil: OverflowError.
    from app.control.feature_runtime import _opening_release
    assert _opening_release({}, 59.475, 5, math.inf, _cut(.3)) == 62.975001


def test_zero_cutoff_releases_just_past_the_nominal_end():
    # Round two R2-3: at 8.008 the closure reads 2.2e-16, so a zero cutoff first clears one tick later.
    from app.control.feature_runtime import _opening_release
    assert _opening_release({}, 3.008, 5, 8.108, _cut(0)) == 8.008001


@pytest.mark.slow
def test_releases_past_the_round_end_are_never_sampled():
    # Round three R3-1: a release projected past the clipped round end (another door's, here appended) made
    # reopened_by sample a door still closing at the end, past its motion grace: the round failed as pending.
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data, _, _ = compiled_case(cutoff=.3)
    data['map_features']['features'][0]['events'] = [{'t': 5, 'state': 'closing'}]
    runtime = FeatureRuntime(geo, data)
    assert data['t_end'] == 9
    runtime.transitions.append(12.500001)
    assert not (runtime.reopened_by(8.9) > 9).any()


def test_live_openings_skip_destruction_and_pair_each_with_its_next_event():
    from app.control.feature_runtime import _live_openings
    timeline = {'initial': 'closed', 'closing_s': 5, 'events': [
        {'t': 3, 'state': 'opening'}, {'t': 8, 'state': 'open'}, {'t': 9, 'state': 'closing'},
        {'t': 14, 'state': 'closed'}, {'t': 20, 'state': 'opening'}]}
    assert list(_live_openings(timeline)) == [(3.0, 8), (20.0, math.inf)]
    timeline['events'][3:3] = [{'t': 12, 'state': 'broken'}]
    assert list(_live_openings(timeline)) == [(3.0, 8)]
    assert list(_live_openings({**timeline, 'initial': 'broken'})) == []


def test_release_search_visits_each_event_once():
    # Round three R3-3: one state_at per opening rescanned the whole prefix, quadratic in the events.
    from app.control import feature_runtime as fr
    events = [{'t': i * .02, 'state': 'opening'} for i in range(4000)]
    seen = []
    original = fr.state_at

    def counting(feature, t):
        seen.append(len(feature['events']))
        return original(feature, t)
    import unittest.mock as um
    with um.patch.object(fr, 'state_at', counting):
        for start, later in fr._live_openings({'initial': 'closed', 'events': events}):
            fr._opening_release({}, start, 5, later, _cut(.3))
    # Two bracket checks per superseded opening; the last one, with no next event, bisects (about 23 steps).
    assert max(seen) == 1 and len(seen) <= 2 * len(events) + 2 + math.ceil(math.log2(5 * 10 ** 6 + 1000))


@pytest.mark.slow
@pytest.mark.parametrize('last', [True, False])
def test_release_one_float_past_its_tick_is_still_recorded(last):
    # Round two R2-2: an opening at 6.501 releases at 10.001001; minus 1e-6 that is the float
    # 10.001000000000001, already clear, so reopened_by saw no change there.
    # R2-1 at runtime level: the opening as the round's last event.
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data, _, _ = compiled_case(cutoff=.3)
    events = [{'t': 1, 'state': 'closing'}, {'t': 6.002, 'state': 'closed'}, {'t': 6.501, 'state': 'opening'}]
    if not last:
        events.append({'t': 11.501, 'state': 'open'})
    data['map_features']['features'][0]['events'] = events
    data['t_end'] = 12
    runtime = FeatureRuntime(geo, data)
    assert 10.001001 in runtime.transitions
    doorway = runtime.sample(6.2)[0]
    assert doorway.any()
    reopened = runtime.reopened_by(11)
    assert set(reopened[doorway]) == {10.001001}


@pytest.mark.slow
@pytest.mark.parametrize('blocked,expected_calls', [(None, 1), (True, 2)])
def test_reopen_release_search_stops_without_adding_an_unqualified_time(monkeypatch, blocked, expected_calls):
    from app.control.feature_runtime import FeatureRuntime
    from app.replays import map_feature_motion as motion
    geo, _, _, data, _, _ = compiled_case(cutoff=.3)
    data['map_features']['features'][0]['events'] = [
        {'t': 1, 'state': 'closing'}, {'t': 6.002, 'state': 'closed'},
        {'t': 59.475, 'state': 'opening'}, {'t': 64.475, 'state': 'open'}]
    calls = []
    def unavailable_release(feature, closure):
        calls.append(closure)
        return blocked
    monkeypatch.setattr(motion, 'movement_blocked', unavailable_release)
    assert FeatureRuntime(geo, data).transitions == [1.0, 2.5, 6.002, 59.475, 64.475]
    assert len(calls) == expected_calls  # The bracket's two ends; undecided or never released adds nothing.


@pytest.mark.slow
def test_reopen_without_conservative_cutoff_releases_at_measured_clearance():
    # Branch review B5: closing already used the measured-clearance fallback; opening now does too.
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data, _, _ = compiled_case()
    # The fixture's door breaks at 8 s; a broken door never reopens, so keep it whole here.
    data['map_features']['features'][0]['events'] = [
        {'t': 1, 'state': 'closing'}, {'t': 6.002, 'state': 'closed'},
        {'t': 59.475, 'state': 'opening'}, {'t': 64.475, 'state': 'open'}]
    data['t_end'] = 70
    runtime = FeatureRuntime(geo, data)
    [release] = [t for t in runtime.transitions if 59.475 < t < 64.475]
    assert round(release, 6) == release
    reopened = runtime.reopened_by(65)
    assert (reopened > 59).any() and set(reopened[reopened > 59]) == {release}


@pytest.mark.slow
def test_ascent_without_reopen_preserves_literal_transitions():
    from app.control.feature_runtime import FeatureRuntime
    geo, _, _, data, _, _ = compiled_case(cutoff=.3)
    assert FeatureRuntime(geo, data).transitions == [1.0, 2.5, 6.002, 8.0]


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

import pytest
from map_feature_artifact_toys import synthetic_consumer_runtime
from dataclasses import replace

from test_control_store import db, factory, linked
from test_replay_store import condensed, pg
from app.replays import control_format as cf
from app.services import replay_control as rc, control_heights as ch


@pytest.fixture
def tagged(db, linked, monkeypatch):
    import copy, json
    from app.replays import map_feature_inputs as fi
    from app.replays import map_feature_sources as sources
    from map_feature_artifact_toys import source_case, base_case
    index, tags, maps, raw = rc._current_snapshot()
    index, tags, maps = copy.deepcopy(index), copy.deepcopy(tags), copy.deepcopy(maps)
    tags[linked.map_name] = source_case()
    maps[linked.map_name]['xMultiplier'] = 7e-5
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', {'test': 1})
    monkeypatch.setattr(sources, 'base_snapshot', lambda *args: base_case())
    monkeypatch.setattr(rc, '_current_snapshot', lambda: (index, tags, maps, json.dumps({'maps': tags}).encode()))
    return tags[linked.map_name]


def test_fingerprint_reconstruction_does_not_read_current_figures(monkeypatch):
    inputs = {'control': 8, 'data': 1, 'summary': 1, 'recipe': 'fixed.c10.f1.a0', 'source': '0' * 64,
              'link': {'slots': [0, 1]}, 'geometry': {}, 'figures': '1' * 16}
    first = cf.fingerprint_from_inputs(inputs)
    monkeypatch.setattr(cf, 'figures_hash', lambda: '2' * 16)
    assert cf.fingerprint_from_inputs(inputs) == first


def test_pinned_inputs_are_immutable_copies():
    geometry = {'specials': [], 'features': 'a' * 64}
    pin = rc.PinnedInputs(geometry, artifact_digest='a' * 64)
    geometry['specials'].append('mutated')
    pin.geometry['specials'].append('also mutated')
    assert pin.geometry == {'specials': [], 'features': 'a' * 64}


def test_source_failure_cannot_reuse_last_valid_snapshot(db, linked, monkeypatch):
    assert rc.resolve_current_geometry(db, linked.map_name).state == 'ready'
    def fail(*args):
        raise OSError('source unavailable')
    monkeypatch.setattr(rc, '_current_snapshot', fail)
    context = rc.resolve_current_geometry(db, linked.map_name)
    assert context.state == 'source_error'
    assert rc.round_fingerprint(linked, rc.side_groups(db, linked), 1, context=context) is None
    assert all(not p.computable and p.reason == 'source_error' for p in rc.plan(db, rounds={1}))


def test_manual_off_selects_flat_even_with_committed_fallback(db, linked):
    from test_control_heights_db import build
    build(db, linked.map_name, 'a' * 12)
    assert ch.select_height(db, linked.map_name, 'b' * 12).digest == 'a' * 12
    ch.deactivate(db, linked.map_name)
    assert ch.select_height(db, linked.map_name, 'b' * 12).mode == 'flat'


def test_pin_result_rejects_unexpected_artifact_and_tampered_provenance():
    pin = rc.PinnedInputs({'sight': 's'})
    with pytest.raises(rc.InvalidControlInputs):
        rc.verify_result_inputs(pin, {'status': 'ok', 'geometry': {'sight': 's', 'features': 'a' * 64}})


def test_pending_is_read_only_and_preparation_is_shared_per_map(factory, db, linked, tagged, monkeypatch):
    import time
    from app.services import control_feature_artifacts as service
    context = rc.resolve_current_geometry(db, linked.map_name)
    assert context.state == 'features_pending'
    def unexpected(*args):
        raise AssertionError('read-only planning cannot compile')
    original = service.run_feature_child
    monkeypatch.setattr(service, 'run_feature_child', unexpected)
    assert not rc.plan(db, rounds={1})[0].computable
    monkeypatch.setattr(service, 'run_feature_child', original)
    rc.prepare_discovered(factory, [linked.map_name], {}, time.time())
    db.expire_all()
    assert rc.resolve_current_geometry(db, linked.map_name).state == 'ready'
    plans = rc.plan(db, rounds={1, 2})
    assert plans[0].inputs.artifact_digest == plans[1].inputs.artifact_digest
    from app.models.replay import ControlFeatureArtifact
    assert db.query(ControlFeatureArtifact).count() == 1


def test_result_provenance_tamper_and_runtime_edit_preserve_old_row(factory, db, linked, tagged):
    from app.services.replay_control_store import store_round
    from app.models.replay import ReplayRoundControl
    rc.prepare_discovered(factory, [linked.map_name], {}, 1)
    [p] = rc.plan(db, rounds={1})
    inputs = p.inputs.envelope
    provenance = {'v': 1, 'inputs': inputs, 'fingerprint': cf.fingerprint_from_inputs(inputs)}
    result = {'status': 'ok', 'geometry': p.inputs.geometry, 'data': b'd',
              'summary': cf.pack_summary({'provenance': provenance})}
    assert rc.verify_result_inputs(p.inputs, result) == provenance
    assert store_round(factory, p.replay_id, 1, p.fingerprint, result, require_current=True, planned_inputs=p.inputs) == 'stored'
    for key in inputs:
        changed = dict(inputs, **{key: None})
        bad = dict(result, summary=cf.pack_summary({'provenance': provenance | {'inputs': changed}}))
        assert store_round(factory, p.replay_id, 1, p.fingerprint, bad, require_current=True, planned_inputs=p.inputs).startswith('skipped')
    tagged['map_features']['features'][0]['name'] = 'editorial change'
    assert rc.resolve_current_geometry(db, linked.map_name).pinned.artifact_digest == p.inputs.artifact_digest
    tagged['map_features']['features'][0]['initial_state'] = 'open'
    assert rc.resolve_current_geometry(db, linked.map_name).state == 'features_pending'
    assert store_round(factory, p.replay_id, 1, p.fingerprint, result, require_current=True, planned_inputs=p.inputs).startswith('skipped')
    db.expire_all()
    assert db.get(ReplayRoundControl, (p.replay_id, 1)).fingerprint == p.fingerprint


def test_parent_mask_snapshot_matches_existing_decoder_and_flat_bypasses_pointer(tmp_path):
    import json
    import numpy as np
    from app.control import geometry as cg
    from app.replays.map_feature_sources import png_mask, base_snapshot
    from app.replays.map_feature_artifacts import unpack_mask
    path = cg.ASSET_DIR / 'Ascent.sight.png'
    assert np.array_equal(np.frombuffer(png_mask(path.read_bytes()), np.uint8).reshape(1024, 1024).astype(bool), cg.read_mask_png(path))
    base = base_snapshot('Ascent', cg.ASSET_DIR, cg.load_tags()['maps']['Ascent'], 7e-5)
    assert np.array_equal(np.frombuffer(unpack_mask(base['walk']), np.uint8).reshape(1024, 1024).astype(bool),
                          cg.read_mask_png(cg.ASSET_DIR / 'Ascent.walk.png'))
    for kind in ('sight', 'walk'):
        (tmp_path / ('Ascent.' + kind + '.png')).write_bytes((cg.ASSET_DIR / ('Ascent.' + kind + '.png')).read_bytes())
    (tmp_path / 'tags.json').write_bytes(b'{"maps": {"Ascent": {}}}')
    (tmp_path / 'index.json').write_text(json.dumps({'maps': {'Ascent': {'height_sha': 'a' * 12}}}))
    assert cg.load_geometry('Ascent', tmp_path, height_mode='flat', load_features=False).height_sha is None
    with pytest.raises(cg.GeometryError):
        cg.load_geometry('Ascent', tmp_path, height_mode='asset', load_features=False)


def test_actual_scale_uses_the_loaded_minimap_multiplier():
    from map_feature_artifact_toys import geometry_case
    from app.control.task import geometry_used
    assert geometry_used(geometry_case(flat=True))['scale'] == 7e-5


@pytest.mark.parametrize('bad', ['bad', [], {'bundles': [None]}, {'bundles': 'bad'}])
def test_review_bad_feature_container_is_source_error(db, linked, tagged, bad):
    tagged['map_features'] = bad
    assert rc.resolve_current_geometry(db, linked.map_name).state == 'source_error'
    assert all(not p.computable and p.reason == 'source_error' for p in rc.plan(db, rounds={1}))


@pytest.mark.parametrize('slot,value', [(0, []), (0, {'maps': []}), (1, []), (1, {'maps': []}), (2, [])])
def test_review_current_snapshot_rejects_bad_json_containers(tmp_path, monkeypatch, slot, value):
    import json
    from app.replays.map_feature_inputs import FeatureInputsError
    paths = [tmp_path / str(i) for i in range(3)]
    for i, path in enumerate(paths):
        path.write_text(json.dumps(value if i == slot else {'maps': {}}))
    monkeypatch.setattr(rc, '_asset_paths', lambda: paths)
    with pytest.raises(FeatureInputsError):
        rc._current_snapshot()


def test_review_match_summary_freshness_uses_current_tagged_context(factory, db, linked, tagged, monkeypatch):
    from test_control_store import put_row
    from app.services.replay_control_views import load_round_summaries
    rc.prepare_discovered(factory, [linked.map_name], {}, 1)
    [p] = rc.plan(db, rounds={1})
    put_row(db, linked, 1, fingerprint=p.fingerprint)
    assert load_round_summaries(db, linked).stale == []
    tagged['map_features']['features'][0]['name'] = 'editorial'
    assert load_round_summaries(db, linked).stale == []
    tagged['map_features']['features'][0]['initial_state'] = 'open'
    assert load_round_summaries(db, linked).stale == [1]
    monkeypatch.setattr(rc, '_current_snapshot', lambda: (_ for _ in ()).throw(OSError('gone')))
    assert load_round_summaries(db, linked).stale == [1]


def test_review_match_summary_explicit_off_beats_committed_fallback(db, linked, monkeypatch):
    import copy
    from test_control_heights_db import build
    from test_control_store import put_row
    from app.services.replay_control_views import load_round_summaries
    index, tags, maps, raw = rc._current_snapshot()
    index = copy.deepcopy(index)
    index[linked.map_name]['height_sha'] = 'b' * 12
    monkeypatch.setattr(rc, '_current_snapshot', lambda: (index, tags, maps, raw))
    monkeypatch.setattr(rc, '_assets', lambda: (index, tags, maps))
    build(db, linked.map_name, 'a' * 12)
    ch.deactivate(db, linked.map_name)
    context = rc.resolve_current_geometry(db, linked.map_name)
    put_row(db, linked, 1, fingerprint=rc.round_fingerprint(linked, rc.side_groups(db, linked), 1, context=context))
    assert load_round_summaries(db, linked).stale == []


@pytest.mark.parametrize('editorial', [False, True])
def test_review_gap_writer_rechecks_tags_after_existing_run_lookup(factory, db, linked, tagged, monkeypatch, editorial):
    from sqlalchemy.orm import Session
    from app.models.replay import ReplayRoundGapRun, ReplayGap
    from app.services import replay_gaps as gaps
    from app.services.replay_gaps_store import store_gaps
    from app.replays import choke_assets
    from test_control_store import put_row
    from test_gaps_store import RUN, ROW
    rc.prepare_discovered(factory, [linked.map_name], {}, 1)
    [p] = rc.plan(db, rounds={1})
    put_row(db, linked, 1, fingerprint=p.fingerprint)
    old = dict(RUN, fingerprint='0' * 16)
    assert store_gaps(factory, linked.id, 1, old, [ROW, dict(ROW, seq=1)]) == 'stored'
    wanted = dict(RUN, fingerprint=gaps.gap_fingerprint(p.fingerprint, linked.map_name),
                  gaps_revision=gaps.GAPS_REVISION, chokes_hash=choke_assets.asset_hash(linked.map_name))
    get = Session.get
    changed = []
    def late_edit(session, entity, *args, **kwargs):
        row = get(session, entity, *args, **kwargs)
        if entity is ReplayRoundGapRun and not changed:
            changed.append(True)
            tagged['map_features']['features'][0]['name' if editorial else 'initial_state'] = 'renamed' if editorial else 'open'
        return row
    monkeypatch.setattr(Session, 'get', late_edit)
    result = store_gaps(factory, linked.id, 1, wanted, [ROW], expected_control_fingerprint=p.fingerprint)
    assert changed
    assert result == 'stored' if editorial else result.startswith('skipped')
    db.expire_all()
    assert db.get(ReplayRoundGapRun, (linked.id, 1)).fingerprint == (wanted if editorial else old)['fingerprint']
    assert db.query(ReplayGap).count() == (1 if editorial else 2)

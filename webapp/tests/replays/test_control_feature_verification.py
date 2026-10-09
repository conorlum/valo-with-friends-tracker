import copy
import pytest
from map_feature_artifact_toys import synthetic_consumer_runtime
from test_control_store import db, factory, linked
from test_control_pinned_inputs import tagged
from test_replay_store import condensed, pg


@pytest.fixture
def historical_row(factory, db, linked, tagged, tmp_path):
    from app.control import heights as hc
    from map_feature_artifact_toys import geometry_case
    from app.models.replay import ControlHeight, ReplayRoundControl
    from app.services import replay_control as rc
    from app.services.replay_control_store import store_round
    from app.replays import control_format as cf
    import hashlib
    import numpy as np
    index = rc._current_snapshot()[0]
    geo = geometry_case()
    for kind, mask in [('sight', geo.sight), ('walk', geo.walk_px)]:
        index[linked.map_name][kind + '_sha'] = hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()[:12]
    index[linked.map_name]['barrier_sha'] = None
    asset = geometry_case().heights
    path = tmp_path / 'old.npz'
    hc.save_asset(path, asset)
    old = ControlHeight(map_name=linked.map_name, digest=asset.digest, asset=path.read_bytes(),
                        status='active', report={}, rules=hc.rules(), inputs={}, inputs_sha='a' * 16, match_uuids=[])
    db.add(old)
    db.commit()
    rc.prepare_discovered(factory, [linked.map_name], {}, 1)
    [p] = rc.plan(db, rounds={1})
    provenance = {'v': 1, 'inputs': p.inputs.envelope, 'fingerprint': p.fingerprint}
    result = {'status': 'ok', 'geometry': p.inputs.geometry, 'data': b'control',
              'summary': cf.pack_summary({'provenance': provenance})}
    assert store_round(factory, p.replay_id, 1, p.fingerprint, result, require_current=True, planned_inputs=p.inputs) == 'stored'
    old.status = 'superseded'
    replacement = geometry_case(origin_dm=200).heights
    hc.save_asset(path, replacement)
    db.add(ControlHeight(map_name=linked.map_name, digest=replacement.digest, asset=path.read_bytes(),
                         status='active', report={}, rules=hc.rules(), inputs={}, inputs_sha='b' * 16, match_uuids=[]))
    tagged['map_features']['features'][0]['states'][0]['sight_bounds']['top']['value'] = 4
    db.commit()
    rc.prepare_discovered(factory, [linked.map_name], {}, 2)
    db.expire_all()
    return db.get(ReplayRoundControl, (linked.id, 1))


def test_historical_round_verifies_old_archive_after_height_and_tag_changes(db, historical_row):
    from app.services.control_feature_verification import verify_stored_round
    result = verify_stored_round(db, historical_row)
    assert (result.integrity, result.recompilation, result.freshness) == ('verified', 'verified', 'stale')


def test_unsupported_recompilation_keeps_independent_integrity(db, historical_row, monkeypatch):
    from app.services import control_feature_verification as service
    from app.replays.map_feature_artifacts import UnsupportedFeatureCompiler
    def unsupported(*args):
        raise UnsupportedFeatureCompiler('recorded compiler unavailable')
    monkeypatch.setattr(service, 'run_feature_child', unsupported)
    result = service.verify_stored_round(db, historical_row)
    assert (result.integrity, result.recompilation, result.freshness) == ('verified', 'unsupported', 'stale')


def test_tampered_provenance_fails_before_unsupported_recompilation(db, historical_row, monkeypatch):
    from app.services import control_feature_verification as service
    from app.replays import control_format as cf
    value = cf.unpack_summary(historical_row.summary)
    value['provenance']['inputs']['source'] = 'f' * 64
    historical_row.summary = cf.pack_summary(value)
    monkeypatch.setattr(service, 'run_feature_child', lambda *a: pytest.fail('corruption must stop first'))
    assert service.verify_stored_round(db, historical_row).integrity == 'failed'


def test_missing_archive_and_legacy_provenance_are_unavailable(db, historical_row):
    from app.services.control_feature_verification import verify_stored_round
    from app.models.replay import ControlFeatureArtifact
    from app.replays import control_format as cf
    provenance = cf.unpack_summary(historical_row.summary)['provenance']
    db.delete(db.get(ControlFeatureArtifact, provenance['inputs']['geometry']['features']))
    db.commit()
    assert verify_stored_round(db, historical_row).integrity == 'unavailable'
    historical_row.summary = cf.pack_summary({})
    result = verify_stored_round(db, historical_row)
    assert result.integrity == result.recompilation == 'unavailable' and result.reasons


@pytest.mark.parametrize('field', ['assets', 'height_asset'])
def test_corrupt_archive_fails_independently_of_compiler_availability(db, historical_row, monkeypatch, field):
    from app.services import control_feature_verification as service
    from app.models.replay import ControlFeatureArtifact
    from app.replays import control_format as cf
    digest = cf.unpack_summary(historical_row.summary)['provenance']['inputs']['geometry']['features']
    archive = db.get(ControlFeatureArtifact, digest)
    setattr(archive, field, b'corrupt')
    db.commit()
    monkeypatch.setattr(service, 'run_feature_child', lambda *a: pytest.fail('corruption must stop first'))
    assert service.verify_stored_round(db, historical_row).integrity == 'failed'


def test_current_source_failure_does_not_prevent_historical_reproduction(db, historical_row, monkeypatch):
    from app.services import replay_control as rc
    from app.services.control_feature_verification import verify_stored_round
    def unreadable():
        raise OSError('current tags unavailable')
    monkeypatch.setattr(rc, '_current_snapshot', unreadable)
    result = verify_stored_round(db, historical_row)
    assert (result.integrity, result.recompilation, result.freshness) == ('verified', 'verified', 'unavailable')


def test_recorded_base_mask_identity_must_match_archive_even_with_rehashed_provenance(db, historical_row):
    from app.services.control_feature_verification import verify_stored_round
    from app.replays import control_format as cf
    value = cf.unpack_summary(historical_row.summary)
    value['provenance']['inputs']['geometry']['sight'] = 'f' * 12
    fingerprint = cf.fingerprint_from_inputs(value['provenance']['inputs'])
    value['provenance']['fingerprint'] = historical_row.fingerprint = fingerprint
    historical_row.summary = cf.pack_summary(value)
    assert verify_stored_round(db, historical_row).integrity == 'failed'


@pytest.mark.parametrize('bad', ['bad', {'bundles': [None]}])
def test_review_bad_current_structure_keeps_historical_verdict_independent(db, historical_row, tagged, bad):
    from app.services.control_feature_verification import verify_stored_round
    tagged['map_features'] = bad
    result = verify_stored_round(db, historical_row)
    assert (result.integrity, result.recompilation, result.freshness) == ('verified', 'verified', 'unavailable')


@pytest.mark.parametrize('available', [{}, {'test': 2}])
def test_review_historical_child_reports_unavailable_consumer_independently(db, historical_row, monkeypatch, available):
    from app.services.control_feature_verification import verify_stored_round
    from app.replays import map_feature_inputs as fi
    monkeypatch.setattr(fi, 'CONSUMER_VERSIONS', available)
    result = verify_stored_round(db, historical_row)
    assert result.integrity == 'verified' and result.recompilation == 'unsupported'

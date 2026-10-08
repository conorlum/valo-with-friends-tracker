"""Identity is permanent source plus compilation context, never editorial/raw formatting."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from app.replays import map_feature_schema as ms
from map_feature_artifact_toys import base_case, snapshot_case, source_case


def identify(source=None, height='a' * 12, base=None, consumers=None):
    from app.replays.map_feature_inputs import identify_features
    return identify_features('Summit', height, snapshot_case(source), base or base_case(),
                             consumers={'test': 1} if consumers is None else consumers)


def test_editorial_and_known_floor_selectors_do_not_move_identity():
    before = source_case()
    after = copy.deepcopy(before)
    mf = after['map_features']
    mf['floors'] = [{'id': 'broken', 'z_band': ['invalid']}]
    mf['features'][0].update(name='Reviewed', floors=['floor-99'])
    mf['features'][0]['states'][0]['sight_bounds']['floor'] = 'floor-99'
    mf['notes'] = 'owner annotation'
    assert identify(before).key == identify(after).key
    assert identify(before).canonical_inputs == identify(after).canonical_inputs
    assert after['map_features']['features'][0]['floors'] == ['floor-99']


def test_whitespace_changes_raw_identity_only():
    from app.replays.map_feature_inputs import diagnostic_identity, identify_features
    a, b = snapshot_case(), snapshot_case(whitespace=True)
    assert a.raw_bytes != b.raw_bytes and a.raw_sha256 != b.raw_sha256
    assert a.raw_sha256 == hashlib.sha256(a.raw_bytes).hexdigest()
    assert diagnostic_identity(a)[0] == diagnostic_identity(b)[0]
    assert identify_features('Summit', None, a, base_case(), consumers={'test': 1}).key == \
           identify_features('Summit', None, b, base_case(), consumers={'test': 1}).key


def test_context_axes_and_unknown_nested_values_change_identity():
    a = identify()
    assert identify(height='b' * 12).key != a.key
    assert identify(height=None).key.height_digest == 'flat'
    assert identify(consumers={'test': 2}).key != a.key
    base = base_case()
    base['scale'] = 8e-5
    assert identify(base=base).key != a.key
    for value in (1, 2):
        source = source_case()
        source['map_features']['features'][0]['future'] = {'floor': value}
        assert identify(source).key != a.key
    source['map_features']['features'].reverse()
    assert identify(source).key == identify({'map_features': {**source['map_features'],
           'features': list(reversed(source['map_features']['features']))}, 'specials': []}).key


def test_disabled_behaviour_is_excluded_but_outside_base_edits_are_retained():
    source = source_case()
    source['map_features']['features'][1]['initial_state'] = 'open'
    assert identify(source).key == identify().key
    source['map_features']['features'][1]['base_edits'] = {
        'remove_sight': source['map_features']['features'][0]['states'][0]['footprint']}
    assert identify(source).key != identify().key
    source['map_features']['features'][0]['initial_state'] = None
    assert identify(source) is not None  # placement/behaviour pending is still intended


def test_empty_registry_means_no_intended_compilation():
    from app.replays.map_feature_inputs import identify_features
    assert identify_features('Summit', None, snapshot_case(), base_case()) is None
    assert identify(consumers={}) is None


@pytest.mark.parametrize('mutation', [
    lambda m: m.update(version=2),
    lambda m: m['features'].append(copy.deepcopy(m['features'][0])),
    lambda m: m['features'][0].update(bundle='bundle-99'),
])
def test_invalid_source_is_an_explicit_error(mutation):
    from app.replays.map_feature_inputs import FeatureInputsError
    source = source_case()
    mutation(source['map_features'])
    with pytest.raises(FeatureInputsError):
        identify(source)


@pytest.mark.parametrize('number', [float('inf'), float('nan'), 9007199254740992, -9007199254740992])
def test_invalid_unknown_number_reports_its_source_path(number):
    source = source_case()
    source['map_features']['features'][0]['future'] = {'floor': number}
    with pytest.raises(ValueError, match=r'future.*floor|nonfinite'):
        identify(source)


def test_canonical_tag_v2_literal_vectors():
    cases = json.loads((Path(__file__).parents[1] / 'fixtures/control/map_features/canonical_v2.json').read_text(encoding='utf-8'))
    for case in cases:
        if 'error' in case:
            with pytest.raises(ValueError):
                ms.tag_canonical_bytes(case['input'])
        else:
            assert ms.tag_canonical_bytes(case['input']) == case['ascii'].encode('ascii')


def test_generic_v1_digest_is_unchanged():
    assert ms.digest({'b': [1, 2.0], 'a': True}) == hashlib.sha256(b'{"a":true,"b":[1,2]}').hexdigest()[:16]

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from analyze_height_refinement import FrozenRounds
from app.replays import format as fmt


MATCH = '00000000-0000-0000-0000-000000000001'


def freeze(tmp_path, blob=None):
    folder = tmp_path / MATCH
    folder.mkdir()
    payload = fmt.encode_blob({'v': 1, **(blob or {'map': 'Lotus', 'hz': 16, 'tracks': {}})})
    (folder / '1.json.gz').write_bytes(payload)
    rows = [[MATCH, 1, hashlib.sha256(payload).hexdigest()]]
    manifest = {'map': 'Lotus', 'round_list': rows, 'rounds': 1, 'matches': 1,
                'rounds_sha': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()[:16]}
    path = tmp_path / 'Lotus.frozen.json'
    path.write_text(json.dumps(manifest))
    return path


def test_frozen_reader_checks_every_blob_on_every_pass(tmp_path):
    path = freeze(tmp_path)
    rounds = FrozenRounds(path)
    assert list(rounds)[0][:2] == (MATCH, 1)
    (tmp_path / MATCH / '1.json.gz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='frozen round changed'):
        list(rounds)


def test_frozen_reader_rejects_a_different_map(tmp_path):
    path = freeze(tmp_path, {'map': 'Sunset', 'hz': 16, 'tracks': {}})
    with pytest.raises(ValueError, match='map disagrees'):
        list(FrozenRounds(path))


@pytest.mark.parametrize('change', ['traversal', 'duplicate', 'counts', 'digest'])
def test_frozen_reader_rejects_untrusted_identity_or_changed_manifest(tmp_path, change):
    path = freeze(tmp_path)
    manifest = json.loads(path.read_text())
    if change == 'traversal':
        manifest['round_list'][0][0] = '../elsewhere'
    elif change == 'duplicate':
        manifest['round_list'] *= 2
    elif change == 'counts':
        manifest['rounds'] = 2
    else:
        manifest['rounds_sha'] = '0' * 16
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        FrozenRounds(path)

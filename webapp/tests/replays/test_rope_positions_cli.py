import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from measure_rope_positions import measure
from test_quiet_routes import rope_case


def inputs(tmp_path):
    _, mf = rope_case()
    mf['features'][0]['name'] = 'Synthetic rope'
    return {'maps': {'Split': {'map_features': mf}}}, {'windows': [[0, 10000, 11000]], 'pawn_slot': {'1': 0}}


@pytest.mark.parametrize('windows', [[], [[0, float('nan'), 11]], [[0, 10, 11], [9, 20, 21]],
    [[10, 20, 21], [0, 9, 10]], [[True, 10, 11]]])
def test_rope_measurement_rejects_invalid_or_overlapping_windows_before_scanning(tmp_path, windows):
    tags, context = inputs(tmp_path)
    context['windows'] = windows
    with pytest.raises(ValueError, match='round windows'):
        measure(tags, 'Split', tmp_path, context)


@pytest.mark.parametrize('pawns', [{}, {'0': 0}, {'1': 10}, {'1': 0, '2': 0}])
def test_rope_measurement_requires_unambiguous_pawn_identity(tmp_path, pawns):
    tags, context = inputs(tmp_path)
    context['pawn_slot'] = pawns
    with pytest.raises(ValueError, match='pawn/slot'):
        measure(tags, 'Split', tmp_path, context)


def test_measurement_hashes_exact_stream_without_certifying_empty_rope_evidence(tmp_path):
    tags, context = inputs(tmp_path)
    raw = b'{"actor_net_guid": 99, "time_ms": 1}\n'
    (tmp_path / 'movement.ndjson').write_bytes(raw)
    result = measure(tags, 'Split', tmp_path, context)
    assert result['movement_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['rows'] == 1 and not result['runtime_enabled']
    assert result['ropes'][0]['status'] == 'insufficient'


@pytest.mark.parametrize('raw', [b'[]\n', b'x' * (8 * 1024 * 1024 + 1)], ids=['nonobject', 'oversize'])
def test_measurement_rejects_nonobject_or_oversize_rows(tmp_path, raw):
    tags, context = inputs(tmp_path)
    (tmp_path / 'movement.ndjson').write_bytes(raw)
    with pytest.raises(ValueError, match='movement row'):
        measure(tags, 'Split', tmp_path, context)

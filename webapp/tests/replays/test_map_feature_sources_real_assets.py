"""Every real map: the cached decode equals the uncached one, on a miss and on a hit (slow: decodes each map)."""

import json

import pytest

from app.replays import map_feature_sources as mfs
from app.services import replay_control as rc


@pytest.mark.slow
def test_every_real_map_cached_equals_uncached():
    tags = json.loads((rc.CONTROL_DIR / 'tags.json').read_text(encoding='utf-8')).get('maps', {})
    maps = json.loads(rc.MAPS_JSON.read_text(encoding='utf-8'))
    mfs.base_snapshot.cache_clear()
    checked = 0
    try:
        for sight in sorted(rc.CONTROL_DIR.glob('*.sight.png')):
            name = sight.name[:-len('.sight.png')]
            scale = (maps.get(name) or {}).get('xMultiplier')
            if name not in tags or scale is None:
                continue
            expected = mfs._base_snapshot(name, rc.CONTROL_DIR, tags[name], scale)
            assert mfs.base_snapshot(name, rc.CONTROL_DIR, tags[name], scale) == expected      # miss
            assert mfs.base_snapshot(name, rc.CONTROL_DIR, tags[name], scale) == expected      # hit
            checked += 1
    finally:
        mfs.base_snapshot.cache_clear()
    assert checked > 0


@pytest.mark.slow
def test_every_real_map_numpy_decode_equals_pure_python(monkeypatch):
    """The vectorised decode is a speed-up only: the same descriptors, byte for byte, as the pure-Python one."""
    if mfs._np is None:
        pytest.skip('numpy not installed')
    tags = json.loads((rc.CONTROL_DIR / 'tags.json').read_text(encoding='utf-8')).get('maps', {})
    maps = json.loads(rc.MAPS_JSON.read_text(encoding='utf-8'))
    fast, checked = {}, 0
    for sight in sorted(rc.CONTROL_DIR.glob('*.sight.png')):
        name = sight.name[:-len('.sight.png')]
        scale = (maps.get(name) or {}).get('xMultiplier')
        if name in tags and scale is not None:
            fast[name] = mfs._base_snapshot(name, rc.CONTROL_DIR, tags[name], scale)
    monkeypatch.setattr(mfs, '_np', None)
    for name, got in fast.items():
        scale = maps[name]['xMultiplier']
        assert got == mfs._base_snapshot(name, rc.CONTROL_DIR, tags[name], scale), name
        checked += 1
    assert checked > 0


def test_numpy_helpers_equal_pure_python_on_edge_inputs(monkeypatch):
    if mfs._np is None:
        pytest.skip('numpy not installed')
    import random

    rng = random.Random(7)
    pixels = bytes(rng.random() < 0.01 for _ in range(1024 * 1024))
    bits = bytes(rng.randrange(256) for _ in range(256 * 256 // 8))
    fast = (mfs._pack(pixels, [1024, 1024]), mfs._barrier_cells(pixels), mfs._legacy_large(bits),
            mfs._pack(bytes(16), [4, 4]))
    with pytest.raises(Exception) as fast_err:
        mfs._pack(b'\x02' + bytes(15), [4, 4])
    monkeypatch.setattr(mfs, '_np', None)
    slow = (mfs._pack(pixels, [1024, 1024]), mfs._barrier_cells(pixels), mfs._legacy_large(bits),
            mfs._pack(bytes(16), [4, 4]))
    assert fast == slow
    with pytest.raises(type(fast_err.value)):
        mfs._pack(b'\x02' + bytes(15), [4, 4])

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

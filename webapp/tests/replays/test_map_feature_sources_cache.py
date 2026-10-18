"""The mask-decode cache in app/replays/map_feature_sources.py: keyed on the asset files' bytes, answers
identical to an uncached decode, callers can't corrupt it, bounded (LRU), safe across threads."""

import base64
import pathlib
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixtures)

from app.replays import map_feature_sources as mfs  # noqa: E402
from app.replays.map_feature_inputs import FeatureInputsError  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_progress as progress  # noqa: E402

REAL_CURRENT_SNAPSHOT = rc._current_snapshot      # saved before the control-store fixture patches it
MAP_A, MAP_B = 'Ascent', 'Bind'


@pytest.fixture(autouse=True)
def _clean_cache():
    mfs.base_snapshot.cache_clear()
    yield
    mfs.base_snapshot.cache_clear()


def _copy_map(tmp_path, name, as_name=None):
    as_name = as_name or name
    for kind in ('sight', 'walk', 'barrier'):
        src = rc.CONTROL_DIR / f'{name}.{kind}.png'
        if src.is_file():
            shutil.copy(src, tmp_path / f'{as_name}.{kind}.png')
    return tmp_path


def _paint(seed):
    return base64.b64encode(bytes((seed + i) % 256 for i in range(256 * 256 // 8))).decode()


def _entry():
    return {'cover_paint': _paint(1), 'specials': [{'kind': 'door', 'at': [1, 2]}]}


def test_miss_then_hit_equal_uncached(tmp_path):
    folder = _copy_map(tmp_path, MAP_A)
    expected = mfs._base_snapshot(MAP_A, folder, _entry(), 1.5)
    assert mfs.base_snapshot(MAP_A, folder, _entry(), 1.5) == expected
    assert mfs.base_snapshot(MAP_A, folder, _entry(), 1.5) == expected
    assert mfs.base_snapshot.cache_info() == {'hits': 1, 'misses': 1, 'size': 1}


def test_mutating_the_input_entry_after_a_miss_leaves_the_cache_alone(tmp_path):
    folder = _copy_map(tmp_path, MAP_A)
    entry = _entry()
    expected = mfs._base_snapshot(MAP_A, folder, _entry(), 1.5)
    mfs.base_snapshot(MAP_A, folder, entry, 1.5)                # a miss: stores the decode
    entry['specials'][0]['at'][0] = 999
    entry['specials'].append('junk')
    assert mfs.base_snapshot(MAP_A, folder, _entry(), 1.5) == expected     # a hit on the original key
    assert mfs.base_snapshot.cache_info()['hits'] == 1


def test_returned_value_is_a_copy(tmp_path):
    folder = _copy_map(tmp_path, MAP_A)
    entry = _entry()
    first = mfs.base_snapshot(MAP_A, folder, entry, 1.5)
    first['specials'].append('junk')
    first['legacy'].clear()
    first['legacy']['x'] = 1
    second = mfs.base_snapshot(MAP_A, folder, entry, 1.5)       # a hit
    third = mfs.base_snapshot(MAP_A, folder, entry, 1.5)
    assert second == mfs._base_snapshot(MAP_A, folder, entry, 1.5)
    assert 'junk' not in second['specials'] and 'cover_paint' in second['legacy']
    second['specials'].append('more')
    assert third == mfs._base_snapshot(MAP_A, folder, entry, 1.5)


def test_replaced_walk_png_is_decoded_afresh(tmp_path):
    folder = _copy_map(tmp_path, MAP_A)
    old = mfs.base_snapshot(MAP_A, folder, {}, 1.0)
    shutil.copy(rc.CONTROL_DIR / f'{MAP_B}.walk.png', folder / f'{MAP_A}.walk.png')
    new = mfs.base_snapshot(MAP_A, folder, {}, 1.0)
    assert mfs.base_snapshot.cache_info()['misses'] == 2
    assert new == mfs._base_snapshot(MAP_A, folder, {}, 1.0)
    assert new['walk'] != old['walk']


@pytest.mark.slow
@pytest.mark.parametrize('change', ['cover_paint', 'cant_walk_paint', 'specials', 'scale'])
def test_entry_or_scale_change_is_a_miss(tmp_path, change):
    folder = _copy_map(tmp_path, MAP_A)
    entry = _entry()
    mfs.base_snapshot(MAP_A, folder, entry, 1.5)
    scale = 1.5
    if change == 'scale':
        scale = 1.6
    elif change == 'specials':
        entry = dict(entry, specials=[{'kind': 'door', 'at': [3, 4]}])
    else:
        entry = dict(entry, **{change: _paint(7)})
    result = mfs.base_snapshot(MAP_A, folder, entry, scale)
    assert mfs.base_snapshot.cache_info() == {'hits': 0, 'misses': 2, 'size': 2}
    assert result == mfs._base_snapshot(MAP_A, folder, entry, scale)


def test_corrupt_png_raises_twice_and_stores_nothing(tmp_path):
    folder = _copy_map(tmp_path, MAP_A)
    (folder / f'{MAP_A}.walk.png').write_bytes(b'not a png at all')
    for _ in range(2):
        with pytest.raises(FeatureInputsError):
            mfs.base_snapshot(MAP_A, folder, {}, 1.0)
        assert mfs.base_snapshot.cache_info() == {'hits': 0, 'misses': 0, 'size': 0}


def test_each_file_read_once_per_call(tmp_path, monkeypatch):
    folder = _copy_map(tmp_path, MAP_A)
    n_files = len(list(folder.iterdir()))
    reads = []
    real = pathlib.Path.read_bytes

    def counting(self):
        reads.append(self.name)
        return real(self)

    monkeypatch.setattr(pathlib.Path, 'read_bytes', counting)
    mfs.base_snapshot(MAP_A, folder, {}, 1.0)               # miss
    assert sorted(reads) == sorted(p.name for p in folder.iterdir()) and len(reads) == n_files
    reads.clear()
    mfs.base_snapshot(MAP_A, folder, {}, 1.0)               # hit
    assert len(reads) == n_files


def test_lock_is_held_across_the_decode(tmp_path, monkeypatch):
    folder = _copy_map(tmp_path, MAP_A)
    entered = threading.Event()

    def slow(map_name, raws, entry, scale):
        entered.set()
        time.sleep(0.2)
        return {'fake': scale}

    monkeypatch.setattr(mfs, '_decode', slow)
    out = []
    first = threading.Thread(target=lambda: out.append(mfs.base_snapshot(MAP_A, folder, {}, 1.0)))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=lambda: out.append(mfs.base_snapshot(MAP_A, folder, {}, 1.0)))
    second.start()
    first.join(10)
    second.join(10)
    assert out == [{'fake': 1.0}, {'fake': 1.0}]
    assert mfs.base_snapshot.cache_info() == {'hits': 1, 'misses': 1, 'size': 1}


def test_bounded_at_32_and_least_recently_used_goes(tmp_path, monkeypatch):
    folder = _copy_map(tmp_path, MAP_A)
    monkeypatch.setattr(mfs, '_decode', lambda map_name, raws, entry, scale: {'scale': scale})
    for scale in range(1, 33):
        mfs.base_snapshot(MAP_A, folder, {}, scale)
    assert mfs.base_snapshot.cache_info()['size'] == 32
    mfs.base_snapshot(MAP_A, folder, {}, 1)                 # hit: key 1 is now the newest
    mfs.base_snapshot(MAP_A, folder, {}, 33)                # the 33rd insert evicts key 2
    info = mfs.base_snapshot.cache_info()
    assert info['size'] == 32 and info['hits'] == 1
    mfs.base_snapshot(MAP_A, folder, {}, 1)
    assert mfs.base_snapshot.cache_info()['hits'] == 2      # still cached
    mfs.base_snapshot(MAP_A, folder, {}, 2)
    assert mfs.base_snapshot.cache_info()['misses'] == 34   # 33 inserts, then key 2 decoded again


def _without_checked_at(body):
    return {k: v for k, v in body.items() if k != 'checked_at'}


@pytest.mark.slow
def test_compute_is_equal_cold_and_warm_and_geometry_key_is_unchanged(factory, db, linked, monkeypatch):
    monkeypatch.setattr(rc, '_current_snapshot', REAL_CURRENT_SNAPSHOT)
    cached = mfs.base_snapshot
    monkeypatch.setattr(mfs, 'base_snapshot', mfs._base_snapshot)
    bypass_key = rc.resolve_current_geometry(db, 'Ascent').feature_input.key
    bypass = _without_checked_at(progress.compute(db))
    monkeypatch.setattr(mfs, 'base_snapshot', cached)
    mfs.base_snapshot.cache_clear()

    first = _without_checked_at(progress.compute(db))
    assert mfs.base_snapshot.cache_info() == {'hits': 1, 'misses': 1, 'size': 1}
    second = _without_checked_at(progress.compute(db))
    info = mfs.base_snapshot.cache_info()
    assert info['misses'] == 1 and info['hits'] == 3
    assert first == second == bypass
    assert rc.resolve_current_geometry(db, 'Ascent').feature_input.key == bypass_key

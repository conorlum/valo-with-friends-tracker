"""Complete door rows and exact-request reuse preserve sampled sight answers."""
import copy
from dataclasses import replace

import numpy as np
import pytest

from app.control import feature_visibility as fv, features as cf
from tests.replays.test_feature_visibility_cache import bridge, panel, reference
from tests.replays.control_toys import open_hall


def context(geo):
    source = int(geo.node_of[geo.cell_of_px(124, 124), 0]) if geo.heights else geo.cell_of_px(124, 124)
    targets = np.flatnonzero(np.unpackbits(geo.rows[geo.row_of[source]])[:geo.n])
    return source, targets


@pytest.mark.parametrize('height_bands', [False, True])
def test_complete_rows_promote_and_keep_batch_spacing(monkeypatch, height_bands):
    geo = copy.copy(bridge() if height_bands else open_hall())
    source, full = context(geo)
    blockers = [panel(185, -.9, 2.3), panel(257, 3.1, 5.6)] if height_bands else [panel(), panel(300)]
    # Subsets deliberately differ from the complete static row's longest ray.
    for targets in (full[:40], full[::3]):
        memo = fv.SightCache(geo)
        expected = ~memo.blocked(geo, source, targets, blockers, None)
        assert memo.clear_row(geo, source, targets, blockers) is None
        assert memo.clear_row(geo, source, targets, blockers) is None
        packed = None
        for _ in range(1000):
            packed = memo.clear_row(geo, source, targets, blockers)
            if packed is not None:
                break
        assert packed is not None
        assert np.array_equal(np.unpackbits(packed)[targets].astype(bool), expected)
        assert memo.row_builds == len(blockers)
        def fail(*a, **kw):
            pytest.fail('complete row lookup must avoid new ray intersections')
        with monkeypatch.context() as m:
            m.setattr(cf, 'blocked_lines', fail)
            # Retaining the farthest target retains spacing while changing the subset.
            farthest = np.linalg.norm(geo.centres[targets].astype(float) - geo.centres[source], axis=1).argmax()
            smaller = targets[[0, farthest]]
            assert np.array_equal(np.unpackbits(memo.clear_row(geo, source, smaller, blockers))[smaller],
                                  np.unpackbits(packed)[smaller])
        assert memo.row_hits == len(blockers)


def test_exact_union_reuse_keys_dynamic_inputs_and_returns_independent_results():
    geo = copy.copy(open_hall())
    source, _ = context(geo)
    src = np.array([source, geo.cell_of_px(300, 124)])
    blockers = [panel()]
    smoke = (180, 180, 35, False)
    skip = np.zeros(geo.n, bool)
    cases = [(src, [], blockers, skip), (src[::-1], [], blockers, skip),
             (src, [smoke], blockers, skip), (src, [], [panel(300)], skip),
             (src, [], [replace(blockers[0], top=0.)], skip)]
    for sources, smokes, panels, omitted in cases:
        expected = reference(geo, sources, smokes, panels, omitted)
        result = cf.seen_from_with(geo, sources, smokes, panels, omitted)
        assert np.array_equal(result, expected)
        result[:] = False
        assert np.array_equal(cf.seen_from_with(geo, sources, smokes, panels, omitted), expected)
    memo = fv.cache_for(geo)
    assert memo.union_hits == len(cases)
    assert len(memo.unions) == len(cases)
    before = memo.union_key(src, [], blockers, skip)
    skip[source] = True
    assert memo.union_key(src, [], blockers, skip) != before
    mutable = replace(blockers[0], mask=blockers[0].mask.copy())
    assert memo.union_key(src, [], [mutable], skip) is None
    assert memo.union_key(src, [object()], blockers, skip) is None
    assert memo.clear_row(geo, source, np.array([source]), [mutable]) is None


def test_row_and_union_memory_limits(monkeypatch):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    monkeypatch.setattr(fv, 'MAX_ROW_BYTES', 7000)
    monkeypatch.setattr(fv, 'MAX_UNION_BYTES', 7000)
    for x in range(180, 220, 4):
        occ = panel(x)
        for _ in range(3):
            memo.clear_row(geo, source, targets, [occ])
        assert memo.row_bytes <= fv.MAX_ROW_BYTES
        key = memo.union_key(np.array([source]), [], [occ], None)
        memo.union_put(key, np.ones(geo.n, bool))
        assert memo.union_bytes <= fv.MAX_UNION_BYTES
    assert len(memo.rows) < 10 and len(memo.unions) < 10


def test_only_resting_rows_persist_and_corruption_rebuilds(tmp_path):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    memo.row_path = tmp_path / 'rows.npz'
    resting, moving = panel(), replace(panel(300), cache_resting=False)
    for _ in range(8):
        memo.clear_row(geo, source, targets, [resting, moving])
    assert len(memo.rows) == 2
    memo.persist()
    assert memo.row_path.exists() and not memo.rows_dirty
    warm = fv.SightCache(geo)
    warm.row_path = memo.row_path
    warm._load_rows(geo)
    assert len(warm.rows) == 1 and warm.row_bytes > 0
    assert not next(iter(warm.rows.values()))[0].flags.writeable
    assert np.array_equal(warm.clear_row(geo, source, targets, [resting]),
                          memo.clear_row(geo, source, targets, [resting]))
    memo.row_path.write_bytes(b'broken cache')
    warm._load_rows(geo)
    assert not warm.rows and warm.row_bytes == 0
    # A previously saved resting snapshot is replaced even after eviction.
    memo.rows.clear()
    memo.rows_dirty = True
    memo.persist()
    with np.load(memo.row_path, allow_pickle=False) as data:
        assert data['rows'].shape == (0, (geo.n + 7) // 8)


def test_persistence_is_optional_and_geometry_identity_invalidates_it(tmp_path, monkeypatch):
    geo = copy.copy(bridge())
    geo.features_sha = 'first-tags'
    geo.visibility_source = 'cache'
    monkeypatch.setenv('CONTROL_FEATURE_ROW_CACHE_DIR', str(tmp_path))
    first = fv.SightCache(geo)
    old = fv.cache_for(geo)
    geo.features_sha = 'second-tags'
    assert fv.SightCache(geo).row_path != first.row_path
    assert fv.cache_for(geo) is not old
    geo.features_sha = 'first-tags'
    geo.height_sha = 'new-height'
    assert fv.SightCache(geo).row_path != first.row_path
    source, targets = context(geo)
    for _ in range(3):
        first.clear_row(geo, source, targets, [panel()])
    def denied(*a, **kw):
        raise PermissionError('unavailable cache directory')
    monkeypatch.setattr(type(tmp_path), 'mkdir', denied)
    first.persist()  # A cache filesystem failure must not fail a round.
    assert first.rows_dirty and not first.row_path.exists()


def test_small_requests_defer_complete_work_and_promotion_reuses_sparse_pairs(monkeypatch):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    occ = panel()
    for _ in range(10):
        assert memo.clear_row(geo, source, targets[:2], [occ]) is None
        memo.blocked(geo, source, targets[:2], [occ], None)
    assert memo.row_builds == 0
    expected = memo.blocked(geo, source, targets, [occ], None)
    def fail(*a, **kw):
        pytest.fail('promotion should reuse pairs already checked at identical spacing')
    # Populate the sparse entry at the full-row spacing before promotion.
    memo.clear_row(geo, source, targets, [occ])
    memo.clear_row(geo, source, targets, [occ])
    with monkeypatch.context() as m:
        m.setattr(cf, 'blocked_lines', fail)
        row = memo.clear_row(geo, source, targets, [occ])
    assert row is not None
    assert np.array_equal(~np.unpackbits(row)[targets].astype(bool), expected)


def test_transient_rows_cannot_evict_resting_rows(monkeypatch):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    resting = panel()
    for _ in range(3):
        memo.clear_row(geo, source, targets, [resting])
    key = next(iter(memo.rows))
    monkeypatch.setattr(fv, 'MAX_MOVING_ROW_BYTES', 4000)
    for x in range(220, 260, 4):
        moving = replace(panel(x), cache_resting=False)
        for _ in range(8):
            memo.clear_row(geo, source, targets, [moving])
        assert key in memo.rows
        assert memo.moving_row_bytes <= fv.MAX_MOVING_ROW_BYTES
    assert memo.row_evictions > 0


def test_ability_wall_union_keys_snapshot_geometry_and_count_storage():
    from app.control.geometry import Wall
    geo = copy.copy(open_hall())
    source, _ = context(geo)
    src = np.array([source, geo.cell_of_px(300, 124)])
    blockers = [panel()]
    wall = Wall.from_points([(140, 220), (340, 240)])
    expected = reference(geo, src, [wall], blockers)
    assert np.array_equal(cf.seen_from_with(geo, src, [wall], blockers), expected)
    clone = Wall(wall.segs.copy())
    assert np.array_equal(cf.seen_from_with(geo, src, [clone], blockers), expected)
    memo = fv.cache_for(geo)
    assert memo.union_hits == 1
    key = memo.union_key(src, [wall], blockers, None)
    wall.segs[0, 0] += 30
    assert memo.union_key(src, [wall], blockers, None) != key
    assert np.array_equal(cf.seen_from_with(geo, src, [wall], blockers),
                          reference(geo, src, [wall], blockers))


def test_evicted_context_must_repay_promotion(monkeypatch):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    moving = replace(panel(), cache_resting=False)
    monkeypatch.setattr(fv, 'MAX_MOVING_ROW_BYTES', 0)
    for _ in range(3):
        memo.clear_row(geo, source, targets, [moving])
    assert memo.row_builds == 1 and not memo.rows
    for _ in range(2):
        assert memo.clear_row(geo, source, targets, [moving]) is None
    assert memo.row_builds == 1


def test_rotations_have_reserved_space_and_stationary_rows_borrow_idle_space(monkeypatch):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    size = (geo.n + 7) // 8 + 1024
    monkeypatch.setattr(fv, 'MAX_ROW_BYTES', 4 * size)
    monkeypatch.setattr(fv, 'MAX_MOVING_ROW_BYTES', 2 * size)
    for x in (180, 200, 220, 240):
        for _ in range(3):
            memo.clear_row(geo, source, targets, [panel(x)])
    assert len(memo.resting_rows) == 4 and not memo.moving_rows
    for x in (260, 280, 300):
        for _ in range(3):
            memo.clear_row(geo, source, targets, [replace(panel(x), cache_resting=False)])
    assert len(memo.resting_rows) == len(memo.moving_rows) == 2
    assert memo.row_bytes == 4 * size and memo.moving_row_bytes == 2 * size


def test_same_raster_becomes_persistent_when_it_reaches_a_resting_pose():
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    moving = replace(panel(), cache_resting=False)
    for _ in range(3):
        memo.clear_row(geo, source, targets, [moving])
    assert len(memo.moving_rows) == 1 and not memo.rows_dirty
    memo.clear_row(geo, source, targets, [replace(moving, cache_resting=True)])
    assert not memo.moving_rows and memo.moving_row_bytes == 0
    assert len(memo.resting_rows) == 1 and memo.rows_dirty


def test_cached_resting_row_is_reused_while_another_panel_still_uses_sparse_checks(monkeypatch):
    geo = copy.copy(open_hall())
    source, targets = context(geo)
    memo = fv.SightCache(geo)
    resting, moving = panel(), replace(panel(300), cache_resting=False)
    for _ in range(3):
        memo.clear_row(geo, source, targets, [resting])
    farthest = np.linalg.norm(geo.centres[targets].astype(float)-geo.centres[source], axis=1).argmax()
    subset = np.unique(np.r_[targets[:12], targets[farthest]])
    expected = ~fv.SightCache(geo).blocked(geo, source, subset, [resting, moving], None)
    original = cf.blocked_lines
    calls = []
    def checked(occluders, *args, **kwargs):
        assert len(occluders) == 1 and occluders[0].mask_digest == moving.mask_digest
        calls.append(occluders)
        return original(occluders, *args, **kwargs)
    monkeypatch.setattr(cf, 'blocked_lines', checked)
    packed = memo.clear_row(geo, source, subset, [resting, moving])
    assert packed is not None and len(calls) == 1
    assert np.array_equal(np.unpackbits(packed)[subset].astype(bool), expected)
    assert memo.row_builds == 1

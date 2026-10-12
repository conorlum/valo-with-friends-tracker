"""Sight memo must retain the old batch sampling, smoke and union semantics."""
import copy
from dataclasses import replace

import numpy as np
import pytest

from app.control import features as cf, heights as hc
from app.control.feature_visibility import SightCache, SourceArea, cache_for, source_area
from app.replays import map_feature_schema as ms
from tests.replays.control_toys import open_hall, two_rooms


def reference(geo, src, smokes, occluders, skip=None, record=None):
    """Pre-optimization algorithm; independent of the new cache/source hints."""
    from app.control import engine
    static = engine.seen_from(geo, src, smokes, skip)
    src = src[geo.row_of[src] >= 0]
    targets = np.flatnonzero(static & ~skip) if skip is not None else np.flatnonzero(static)
    out = static.copy()
    out[targets] = False
    z = np.array([cf._eye_body(geo, int(t), hc.BODY_M) for t in targets])
    b = np.column_stack((geo.centres[targets].astype(float), z))
    rows = np.unpackbits(geo.rows[geo.row_of[src]], axis=1)[:, :geo.n][:, targets].astype(bool)
    for i, s in enumerate(src.tolist()):
        cand = rows[i] & ~out[targets]
        for smoke in smokes:
            cand[cand] &= ~engine.smoke_blocks(geo.centres[[s]], geo.centres[targets[cand]], smoke)[0]
        if cand.any():
            x, y = geo.centres[s]
            clear = ~cf.blocked_lines(occluders, (float(x), float(y), cf._eye_body(geo, s, hc.EYE_M)), b[cand], record)
            out[targets[cand][clear]] = True
    return out


def panel(x=200, bottom=-np.inf, top=np.inf):
    mask = np.zeros((1024, 1024), bool)
    mask[96:296, x:x+4] = True
    return cf._bounded_occluder('door', mask, bottom, top, bottom == -np.inf)


@pytest.mark.parametrize('with_skip', [False, True])
@pytest.mark.parametrize('sources', [1, 8, 80])
def test_cached_visibility_matches_original_with_smokes_skip_and_source_union(with_skip, sources):
    geo = copy.copy(open_hall())
    rng = np.random.default_rng(6)
    src = rng.choice(np.flatnonzero(geo.row_of >= 0), sources, replace=False)
    skip = rng.random(geo.n) < .3 if with_skip else None
    blockers = [panel(), panel(300)]
    # Smoke representation is engine's own class; test both no smoke and smoke.
    smoke = (180, 180, 35, False)
    for smokes in ([], [smoke]):
        before, after = {}, {}
        expected = reference(geo, src, smokes, blockers, skip, before)
        for _ in range(2):
            after.clear()
            assert np.array_equal(cf.seen_from_with(geo, src, smokes, blockers, skip, after), expected)
            assert after == before


def test_warm_cache_avoids_intersections_and_retains_batch_step_counts(monkeypatch):
    geo = copy.copy(open_hall())
    source = geo.cell_of_px(124, 124)
    targets = np.array([geo.cell_of_px(244, 124), geo.cell_of_px(400, 276)])
    occ = panel()
    calls = []
    real = cf.blocked_lines
    def counted(*args, **kwargs):
        calls.append(kwargs.get('sampling_steps'))
        return real(*args, **kwargs)
    monkeypatch.setattr(cf, 'blocked_lines', counted)
    memo = cache_for(geo)
    first = memo.blocked(geo, source, targets, [occ], {})
    assert calls
    assert np.array_equal(memo.blocked(geo, source, targets, [occ], {}), first)
    assert len(calls) == 1
    assert np.array_equal(memo.blocked(geo, source, targets, [panel()], {}), first)
    assert len(calls) == 1, 'identical stationary walls reuse a row across runtime objects'
    memo.blocked(geo, source, targets[:1], [occ], {})
    assert len(calls) == 2 and calls[0] != calls[1]


def test_mutable_masks_and_changed_heights_cannot_reuse_stale_answers():
    geo = copy.copy(open_hall())
    s, t = [geo.cell_of_px(x, 124) for x in (124, 244)]
    occ = panel()
    mutable = replace(occ, mask=occ.mask.copy())
    memo = cache_for(geo)
    assert memo.blocked(geo, s, np.array([t]), [mutable], {}).item()
    mutable.mask[:] = False
    assert not memo.blocked(geo, s, np.array([t]), [mutable], {}).item()
    assert not memo.entries
    memo.blocked(geo, s, np.array([t]), [occ], {})
    geo.node_z = np.zeros(geo.n)
    assert cache_for(geo) is not memo


def test_cache_memory_is_bounded():
    geo = copy.copy(open_hall())
    memo = SightCache(geo, limit=8000)
    t = np.array([geo.cell_of_px(244, 124)])
    for s in np.flatnonzero(geo.row_of >= 0)[:30]:
        memo.blocked(geo, int(s), t, [panel()], {})
        assert memo.bytes <= memo.limit


def test_an_omitted_source_is_checked_when_it_could_see_the_door():
    geo = open_hall()
    s = geo.cell_of_px(124, 124)
    hint = SourceArea(np.zeros(geo.n, bool), (198, 94, 206, 298), np.full(geo.n, -1, np.int8))
    assert hint.affects(geo, s), 'drawing omissions must not expose a sight line'
    separated = two_rooms()
    s = separated.cell_of_px(124, 124)
    hint = SourceArea(np.zeros(separated.n, bool), (320, 100, 340, 160), np.full(separated.n, -1, np.int8))
    assert not hint.affects(separated, s)


def test_source_paint_touches_whole_coarse_cells_and_all_floors():
    import base64
    geo = open_hall()
    paint = np.zeros((256, 256), np.uint8)
    paint[24, 24] = 1
    shape = {'type': 'paint', 'cells': base64.b64encode(np.packbits(paint.ravel(), bitorder='little')).decode()}
    feature = {'sight_source_area': shape, 'rotation': {'pivot': {'uv': [2000, 2000]},
               'panel': {'uv': [[1900, 1900], [2100, 1900], [2100, 2100], [1900, 2100]]}}}
    hint = source_area(geo, feature)
    assert hint.drawn[geo.cell_of_px(100, 100)]


def test_source_area_is_validated_and_is_part_of_runtime_identity():
    import base64
    mf = ms.empty()
    f = {**ms.preset('rotating_door'), 'id': 'feature-1'}
    mf['features'].append(f)
    f['sight_source_area'] = {'type': 'paint', 'cells': base64.b64encode(bytes(ms.PAINT_BYTES)).decode()}
    before = ms.runtime_digest(mf)
    assert ms.validate(mf).ok
    f['sight_source_area']['cells'] = 'invalid'
    assert not ms.validate(mf).ok
    assert ms.runtime_digest(mf) != before


def test_height_bands_and_changed_poses_do_not_share_cached_answers():
    from tests.replays.test_control_features import bridge
    geo = copy.copy(bridge())
    s, t = [int(geo.node_of[geo.cell_of_px(x, 196), 0]) for x in (300, 244)]
    mask = np.zeros((1024, 1024), bool)
    mask[160:232, 256:264] = True
    low = cf._bounded_occluder('door', mask, -.9, 2.3)
    high = replace(low, bottom=3.1, top=5.6)
    memo = cache_for(geo)
    for occ in [low, high, low]:
        expected = cf.blocked_lines([occ], (*geo.centres[s], cf._eye_body(geo,s,hc.EYE_M)),
                     np.array([[*geo.centres[t],cf._eye_body(geo,t,hc.BODY_M)]]))
        assert np.array_equal(memo.blocked(geo,s,np.array([t]),[occ],{}),expected)
    away = cf._bounded_occluder('door', np.zeros((1024,1024),bool), -.9, 2.3)
    assert not memo.blocked(geo,s,np.array([t]),[away],{}).item()


def test_source_proof_encloses_expanded_raster_cells_at_every_rotation_pose():
    import base64
    geo = open_hall()
    feature = {'sight_source_area': {'type':'paint','cells':base64.b64encode(bytes(ms.PAINT_BYTES)).decode()},
        'rotation': {'pivot':{'type':'point','uv':[3200,3200]},
            'panel':{'type':'polygon','uv':[[2800,3180],[3600,3180],[3600,3220],[2800,3220]]},
            'start_deg':ms.known(0,'deg'),'end_deg':ms.known(180,'deg'),
            'direction':{'status':'known','value':'cw'}}}
    hint = source_area(geo,feature)
    left,top,right,bottom = hint.sweep_bounds
    for fraction in np.linspace(0,1,161):
        ys,xs=np.nonzero(cf.to_px(cf.raster(cf.rotation_pose(feature,float(fraction)))))
        assert xs.min() >= left and xs.max() < right and ys.min() >= top and ys.max() < bottom

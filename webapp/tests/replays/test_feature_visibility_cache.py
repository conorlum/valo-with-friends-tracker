"""Sight memo must retain the old batch sampling, smoke and union semantics."""
import copy
from dataclasses import replace

import numpy as np
import pytest

from app.control import features as cf, heights as hc
from app.control.feature_visibility import SightCache, SourceArea, cache_for, source_area
from app.replays import map_feature_schema as ms
from tests.replays.control_toys import HALL, open_hall, two_rooms, toy_heights


def bridge():
    return toy_heights('SightCacheBridge', [HALL], ground=[((96,96,240,296),4.)],
                       upper=[((240,96,288,296),4.)])


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
        assert np.array_equal(cf.seen_from_with(geo, src, smokes, blockers, skip), expected)


@pytest.mark.parametrize('smokes', [[], [(328, 132, 14, False)]])
@pytest.mark.parametrize('with_skip', [False, True])
def test_consecutive_unaffected_sources_are_batched_without_reordering_later_panel_checks(monkeypatch, smokes, with_skip):
    from app.control import engine
    geo = copy.copy(two_rooms())
    src = np.array([geo.cell_of_px(x, y) for x, y in
                    [(124, 124), (132, 132), (324, 124), (140, 140), (364, 164)]])
    mask = np.zeros((1024, 1024), bool)
    mask[96:176, 344:348] = True
    occ = cf._bounded_occluder('door', mask, all_height=True)
    skip = np.zeros(geo.n, bool) if with_skip else None
    if skip is not None:
        skip[geo.cell_of_px(364, 164)] = True
    before = reference(geo, src, smokes, [occ], skip)
    proof = cache_for(geo).unaffected(geo, src, [occ])
    assert proof.tolist() == [True, True, False, True, False]
    batches = []
    original = engine.seen_from
    def counted(g, sources, smoke, skip=None):
        batches.append(sources.tolist())
        return original(g, sources, smoke, skip)
    monkeypatch.setattr(engine, 'seen_from', counted)
    assert np.array_equal(cf.seen_from_with(geo, src, smokes, [occ], skip), before)
    assert src[:2].tolist() in batches


def test_source_batch_proofs_remain_conservative_for_mutable_and_border_masks():
    geo = copy.copy(two_rooms())
    src = np.array([geo.cell_of_px(124, 124)])
    mask = np.zeros((1024, 1024), bool)
    mask[96:176, 344:348] = True
    immutable = cf._bounded_occluder('door', mask, all_height=True)
    memo = cache_for(geo)
    assert memo.unaffected(geo, src, [immutable]).all()
    assert not memo.unaffected(geo, src, [replace(immutable, mask=mask.copy())]).any()
    border = np.zeros_like(mask)
    border[:, 0] = True
    assert not memo.unaffected(geo, src, [cf._bounded_occluder('border', border, all_height=True)]).any()


def test_unaffected_proofs_do_not_mix_fixed_panel_bounds_or_source_hints():
    geo = copy.copy(two_rooms())
    src = np.array([geo.cell_of_px(124, 124)])
    far = np.zeros((1024, 1024), bool)
    far[96:176, 344:348] = True
    near = np.zeros_like(far)
    near[96:176, 144:148] = True
    memo = cache_for(geo)
    a = cf._bounded_occluder('far', far, all_height=True)
    b = cf._bounded_occluder('near', near, all_height=True)
    assert memo.unaffected(geo, src, [a]).all()
    assert not memo.unaffected(geo, src, [b]).any()
    assert not memo.unaffected(geo, src, [a, b]).any()
    hinted = replace(a, source_area=SourceArea(np.ones(geo.n, bool), a.bounds_px, np.full(geo.n, -1, np.int8)))
    assert not memo.unaffected(geo, src, [hinted]).any()


def test_batched_visibility_keeps_height_band_and_thin_panel_answers():
    geo = copy.copy(bridge())
    rng = np.random.default_rng(21)
    src = rng.choice(np.flatnonzero(geo.row_of >= 0), 50, replace=False)
    skip = rng.random(geo.n) < .4
    for smokes in ([], [(224, 200, 22, False)], [(224, 200, 22, True)]):
        blockers = [panel(257, bottom=3.1, top=5.6), panel(185, bottom=-.9, top=2.3)]
        before = reference(geo, src, smokes, blockers, skip)
        assert np.array_equal(cf.seen_from_with(geo, src, smokes, blockers, skip), before)


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
    geo = bridge()
    paint = np.zeros((256, 256), np.uint8)
    paint[49, 64] = 1
    shape = {'type': 'paint', 'cells': base64.b64encode(np.packbits(paint.ravel(), bitorder='little')).decode()}
    feature = {'sight_source_area': shape, 'rotation': {'pivot': {'uv': [2000, 2000]},
               'panel': {'uv': [[1900, 1900], [2100, 1900], [2100, 2100], [1900, 2100]]}}}
    hint = source_area(geo, feature)
    nodes = geo.node_of[geo.cell_of_px(260,196)]
    nodes = nodes[nodes >= 0]
    assert len(nodes) == 2 and hint.drawn[nodes].all()


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

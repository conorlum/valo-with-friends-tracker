"""Sliding-door coverage and between-event sampling, including the real page's controls."""
import numpy as np
import pytest

from app.control import features as cf
from app.replays import map_feature_motion as motion, map_feature_schema as ms, map_feature_state as fs
from test_map_feature_tagger import page_data, run_node, run_page


def door():
    f = {"id": "feature-1", "name": "Synthetic slider", **ms.preset("switch_door")}
    f["initial_state"] = "open"
    closed = next(s for s in f["states"] if s["name"] == "closed")
    closed.update(footprint={"type": "polygon", "uv": [[4000,4900],[6000,4900],[6000,5100],[4000,5100]]},
                  sight_bounds={"ref": "all_height"})
    f["sliding"] = {"open_state": "open", "closed_state": "closed",
                    "open_center": {"type": "point", "uv": [7000,5000]}}
    for r in f["transitions"]:
        r["motion"]["duration"] = ms.known(5, "s")
        r["mid_motion"] = "reverse"
    return ms.make_breakable(f)


def sample(f, events, t):
    evaluated = fs.evaluate(f, events, t)
    return motion.closure_at(f, evaluated['state'], t), evaluated


def test_five_seconds_closes_linearly_and_masks_are_nested():
    f = door()
    masks = []
    for t in [0, 1.25, 2.5, 3.75, 5]:
        fraction, _ = sample(f, [{"t":0, "kind":"switch"}], t)
        assert fraction == t/5
        masks.append(motion.coverage(f, fraction))
    assert not masks[0].any()
    assert np.array_equal(masks[-1], cf.raster(motion.closed_state(f)['footprint']))
    assert masks[2].sum() == masks[-1].sum()/2
    assert all(not (a & ~b).any() for a,b in zip(masks,masks[1:]))


def test_narrowing_gap_blocks_more_synthetic_sight_lines():
    f = door()
    from app.control.geometry import geometry_from_masks
    geo = geometry_from_masks('Ascent', np.zeros((1024,1024),bool), np.ones((1024,1024),bool), 7e-5, [])
    targets = np.array([[x,350,1.7] for x in range(400,641,10)])
    counts = []
    for fraction in [0,.25,.5,.75,1]:
        effects, occluders = motion.sample_effects(geo,{},f,fraction)
        assert not effects.pending
        counts.append(int(cf.blocked_lines(occluders,(512,650,1.7),targets).sum()))
    assert counts[0] == 0 and counts[-1] == len(targets)
    assert counts == sorted(counts) and 0 < counts[2] < counts[-1]


def test_sampled_effects_keep_unknown_vision_bounds_pending_and_do_not_mutate_base():
    from app.control.geometry import geometry_from_masks
    f = door(); motion.closed_state(f)['sight_bounds'] = {'ref':'unresolved'}
    geo = geometry_from_masks('Ascent',np.zeros((1024,1024),bool),np.ones((1024,1024),bool),7e-5,[])
    before = geo.walk.copy()
    effects, occluders = motion.sample_effects(geo,{},f,.5)
    assert effects.blocked.any() and not occluders and 'sight bounds unresolved' in effects.pending[0]
    assert np.array_equal(before,geo.walk)
    open_effects, open_occluders = motion.sample_effects(geo,{},f,0)
    assert not open_effects.blocked.any() and not open_occluders


def test_consumer_sampler_rejects_a_claimed_open_pose_that_still_covers_the_aperture():
    from app.control.geometry import geometry_from_masks
    f = door(); f['sliding']['open_center']['uv'] = [5500,5000]
    geo = geometry_from_masks('Ascent',np.zeros((1024,1024),bool),np.ones((1024,1024),bool),7e-5,[])
    effects,occluders = motion.sample_effects(geo,{},f,.5)
    assert effects.pending == ['open panel still covers the doorway: open centre needs more travel']
    assert not effects.blocked.any() and not occluders


@pytest.mark.parametrize('ref',['ground','world'])
def test_sampled_occluders_respect_measured_vertical_bounds(ref):
    from map_feature_artifact_toys import source_case, geometry_case
    mf = source_case()['map_features']; f = mf['features'][0]
    cx,cy = [324*10000/1024]*2
    f['sliding'] = {'open_state':'open','closed_state':'closed','open_center':{'type':'point','uv':[cx+8*10000/1024,cy]}}
    closed = motion.closed_state(f)
    origin = 10 if ref=='world' else 0
    closed['sight_bounds'] = {'ref':ref,'bottom':ms.known(origin,'m'),'top':ms.known(origin+3,'m')}
    effects,occluders = motion.sample_effects(geometry_case(),mf,f,.5)
    assert not effects.pending and effects.blocked.any() and len(occluders)==1
    # This fixture's node position is z=0; physical feet/ground are 0.9 m below it.
    lo = -.9 if ref=='ground' else 0
    assert occluders[0].bottom == pytest.approx(lo) and occluders[0].top == pytest.approx(lo+3)
    assert cf.blocked_lines(occluders,(326,310,1.7),np.array([[326,338,1.7]])).tolist()==[True]
    assert cf.blocked_lines(occluders,(326,310,4),np.array([[326,338,4]])).tolist()==[False]


def test_sampled_effects_require_unambiguous_placement_for_the_whole_doorway():
    from map_feature_artifact_toys import source_case, geometry_case
    mf = source_case()['map_features']; f = mf['features'][0]
    f['sliding'] = {'open_state':'open','closed_state':'closed','open_center':{'type':'point','uv':[350*10000/1024,324*10000/1024]}}
    effects,occluders = motion.sample_effects(geometry_case(multi=True),mf,f,.5)
    assert any('multi_floor' in p for p in effects.pending)
    assert not effects.blocked.any() and not occluders


def test_repeated_reversal_preserves_position_and_cancels_stale_completion():
    f = door()
    events = [{"t":0,"kind":"switch"},{"t":2,"kind":"switch"},{"t":3,"kind":"switch"}]
    before, _ = sample(f,events[:1],2)
    after, _ = sample(f,events[:2],2)
    assert before == pytest.approx(.4) == after
    assert sample(f,events[:2],3)[0] == pytest.approx(.2)
    assert sample(f,events,3)[0] == pytest.approx(.2)
    assert sample(f,events,4)[0] == pytest.approx(.4)
    assert sample(f,events,5)[0] == pytest.approx(.6)
    assert sample(f,events,7)[0] == 1


@pytest.mark.parametrize('kind,expected',[('destroy',None),('reset',0)])
def test_break_or_round_reset_during_closure(kind,expected):
    f = door()
    events = [{'t':0,'kind':'switch'},{'t':2,'kind':kind}]
    assert sample(f,events,2)[0] == expected
    assert sample(f,events,6)[0] == expected


def test_unknown_duration_and_observed_moving_state_do_not_guess_progress():
    f = door()
    f['transitions'][1]['motion']['duration'] = ms.unresolved()
    assert sample(f,[{'t':0,'kind':'switch'}],2)[0] is None
    assert sample(f,[{'t':0,'kind':'observed','state':'closing'}],2)[0] is None


@pytest.mark.parametrize('mutation', ['missing', 'coincident', 'edge', 'paint', 'bad_state', 'bad_state_type'])
def test_unresolved_geometry_is_pending_in_python_and_js(mutation):
    f = door()
    if mutation == 'missing': f['sliding']['open_center'] = None
    if mutation == 'coincident': f['sliding']['open_center']['uv'] = [5000,5000]
    if mutation == 'edge': motion.closed_state(f)['sight'] = [{'geometry':{'type':'polyline','uv':[[4000,5000],[6000,5000]]},'bounds':{'ref':'all_height'}}]
    if mutation == 'paint': motion.closed_state(f)['footprint'] = {'type':'paint','cells':''}
    if mutation == 'bad_state': f['sliding']['closed_state'] = 'missing'
    if mutation == 'bad_state_type': f['sliding']['closed_state'] = {}
    got = run_node('''function run(p) {return {problems:F.slidingProblems(p.f),pose:F.slidingPose(p.f,.5)};}''',{'f':f})
    assert got['problems'] == motion.geometry_problems(f)
    assert motion.pose(f,.5) is None and got['pose'] is None


def test_python_js_pose_masks_and_full_reducer_states_match():
    f = door()
    f['sliding']['open_center']['uv'] = [7600,5000]
    events = [{'t':0,'kind':'switch'},{'t':2,'kind':'switch'},{'t':3,'kind':'destroy'},{'t':6,'kind':'reset'}]
    times = [0,1,2,2.5,3,5,6]
    fractions = [-1,0,.1,.25,.5,.875,1,2]
    got = run_node('''function run(p) {
      return {samples:p.times.map(t => {const e=F.evaluate(p.f,p.events,t);return {e:e,c:F.slidingClosureAt(p.f,e.state,t)};}),
              masks:p.fractions.map(c => T.packPaint(F.slidingCoverage(p.f,c))),
              poses:p.fractions.map(c => F.slidingPose(p.f,c))};
    }''',{'f':f,'events':events,'times':times,'fractions':fractions})
    from app.control.geometry import pack_paint
    for t, js in zip(times,got['samples']):
        c,e = sample(f,events,t)
        assert js['e'] == e and js['c'] == c
        assert e['trace'] == fs.run(f,events,t)
    for fraction, mask, pose in zip(fractions,got['masks'],got['poses']):
        cells = motion.coverage(f,fraction)
        assert mask == (pack_paint(cells) if cells.any() else None)
        assert pose == motion.pose(f,fraction)


def test_sliding_cannot_publish_as_a_static_closing_shape():
    f = door()
    assert 'sliding geometry requires a runtime motion consumer (preview only)' in '\n'.join(cf.behaviour_problems(f))
    mf = ms.empty(); mf['features'] = [f]
    assert ms.runtime_digest(mf) != ms.runtime_digest({**mf,'features':[{k:v for k,v in f.items() if k!='sliding'}]})
    from map_feature_artifact_toys import source_case, geometry_case
    ready = source_case()['map_features']; geo = geometry_case()
    assert cf.bundle_status(geo,ready,consumers={'test'})['bundle-1'].publishable
    first = ready['features'][0]
    first['sliding'] = {'open_state':'open','closed_state':'closed','open_center':{'type':'point','uv':[350*10000/1024,324*10000/1024]}}
    blocked = cf.bundle_status(geo,ready,consumers={'test'})['bundle-1']
    assert not blocked.publishable and len(blocked.reasons)==1 and 'runtime motion consumer' in blocked.reasons[0]


def test_page_enable_place_scrub_undo_export_and_sandbox_clock():
    data = page_data(future=False)
    f = door(); del f['sliding']
    data['tags']['maps']['Ascent']['map_features']['features'] = [f]
    data['tags']['maps']['Ascent']['map_features']['triggers'] = []
    data['tags']['maps']['Ascent']['map_features']['routes'] = []
    data['tags']['maps']['Ascent']['map_features']['bundles'] = []
    got = run_page('''function run(p) {
      const page=openPage(p.data,{map:'Ascent'}), A=page.api, F=page.F;
      A.ui.sel='feature-1'; A.enableSliding('feature-1');
      const enabled=JSON.parse(JSON.stringify(F.find(A.fe.Ascent.mf,'feature-1')));
      A.undo(); const undone=!F.find(A.fe.Ascent.mf,'feature-1').sliding;
      A.redo(); A.ui.placing='slide-open'; A.setTool('point');
      A.placePoint([7000*1024/10000,5000*1024/10000]);
      function descendants(el) {return [el,...(el.children || []).flatMap(descendants)];}
      const controls=descendants(page.el('featProps'));
      const slider=controls.find(el => el.id === 'slideSlider'); slider.value='50'; slider.dispatch('input');
      const half=A.slidingPreview(F.find(A.fe.Ascent.mf,'feature-1')).fraction;
      const label=controls.find(el => el.id === 'slideLabel').textContent;
      A.simulate('feature-1','switch'); A.simAdvance(2.5);
      const moving=A.slidingPreview(F.find(A.fe.Ascent.mf,'feature-1')).fraction;
      A.simulate('feature-1','destroy'); const broken=A.slidingPreview(F.find(A.fe.Ascent.mf,'feature-1'));
      A.simReset(); const reset=A.slidingPreview(F.find(A.fe.Ascent.mf,'feature-1')).fraction;
      A.ui.slideClosure={'feature-1':.75}; A.onMap(); const mapCleared=Object.keys(A.ui.slideClosure).length===0;
      const exported=F.exportCatalogue(A.canonical(),page.TG.DATA.maps,page.TG.edits,A.fe);
      return {enabled,undone,half,label,moving,broken,reset,mapCleared,exported};
    }''',{'data':data})
    assert got['enabled']['sliding']['open_center'] is None and got['undone']
    assert got['half'] == .5 and got['label'] == '2.50 / 5 s — 50% closed'
    assert got['moving'] == .5 and got['broken'] is None and got['reset'] == 0
    assert got['mapCleared']
    out = got['exported']['maps']['Ascent']['map_features']['features'][0]
    assert out['sliding']['open_center']['uv'] == [7000,5000]
    assert out['states'] == f['states'] and out['transitions'] == f['transitions']
    assert got['exported']['maps']['Bind'] == data['tags']['maps']['Bind']

"""Descending doors have a fixed footprint and a shrinking vertical clearance, never a guessed metre height."""
import copy

import numpy as np
import pytest

from app.control import features as cf
from app.replays import map_feature_motion as motion, map_feature_schema as ms
from map_feature_artifact_toys import source_case, geometry_case
from test_map_feature_tagger import page_data, run_node, run_page
from test_sliding_feature_motion import door, sample


def vertical():
    mf = source_case()['map_features']; f = mf['features'][0]
    f['sliding'] = {'axis':'vertical','open_state':'open','closed_state':'closed',
                    'open_clearance':ms.known(3,'m'),'movement_clearance':ms.known(1,'m')}
    return mf,f


def test_five_second_descent_keeps_map_geometry_fixed_and_lowers_bottom():
    f = door(); f['sliding'] = {'axis':'vertical','open_state':'open','closed_state':'closed','open_clearance':ms.known(3,'m')}
    motion.closed_state(f)['sight_bounds'] = {'ref':'ground','bottom':ms.known(0,'m'),'top':ms.known(3,'m')}
    expected = cf.raster(motion.closed_state(f)['footprint'])
    for t in [0,1.25,2.5,3.75,5]:
        fraction,_ = sample(f,[{'t':0,'kind':'switch'}],t)
        assert np.array_equal(motion.coverage(f,fraction),expected)
        assert motion.pose(f,fraction)==motion.closed_state(f)['footprint']
        band = motion.vertical_bounds(f,fraction)
        assert band['bottom']['value']==3*(1-t/5) and band['top']['value']==3


def test_descending_panel_blocks_more_elevations_while_preserving_overhead_visibility():
    mf,f = vertical(); geo = geometry_case()
    heights = [-.9+h for h in [.25,1,1.5,2.5,4]]
    counts=[]
    for fraction in [0,.25,.5,.75,1]:
        effects,occluders = motion.sample_effects(geo,mf,f,fraction)
        assert not effects.pending
        results = [cf.blocked_lines(occluders,(324,310,z),np.array([[324,338,z]])).item() for z in heights]
        counts.append(sum(results))
        assert not results[-1]
    assert counts[0]==0 and counts[-1]==4 and counts==sorted(counts)
    assert 0 < counts[2] < counts[-1]


def test_partial_traversal_uses_verified_clearance_and_unknown_is_pending():
    mf,f = vertical(); geo=geometry_case()
    assert not motion.sample_effects(geo,mf,f,.5)[0].blocked.any()
    assert motion.sample_effects(geo,mf,f,.75)[0].blocked.any()
    del f['sliding']['movement_clearance']
    effects,occluders=motion.sample_effects(geo,mf,f,.5)
    assert effects.pending==['partial vertical movement needs a verified movement clearance']
    assert not effects.blocked.any() and occluders
    assert not motion.sample_effects(geo,mf,f,0)[0].blocked.any()
    assert motion.sample_effects(geo,mf,f,1)[0].blocked.any()


def test_approximate_player_heights_do_not_become_metres_or_active_occluders():
    mf,f = vertical()
    f['sliding']['open_clearance']={'status':'unresolved','estimate_player_heights':1.5,'note':'owner observation'}
    assert motion.geometry_problems(f)==[]
    assert motion.vertical_bounds(f,.5) is None
    effects,occluders = motion.sample_effects(geometry_case(),mf,f,.5)
    assert effects.pending and not effects.blocked.any() and not occluders
    assert 'player-height estimate is not a metre measurement' in effects.pending[0]
    report=ms.validate(mf)
    assert any('open clearance in metres unresolved' in w['message'] for w in report.warnings)
    assert not report.errors


def test_flat_mode_cannot_claim_to_test_seeing_under_the_descending_panel():
    mf,f=vertical()
    effects,occluders=motion.sample_effects(geometry_case(flat=True),mf,f,.5)
    assert not occluders and any('flat preview cannot test' in p for p in effects.pending)


def test_additional_sight_edge_keeps_shape_and_moves_its_ground_band():
    mf,f=vertical(); closed=motion.closed_state(f)
    edge={'geometry':{'type':'polyline','uv':[[320*10000/1024,324*10000/1024],[328*10000/1024,324*10000/1024]]},
          'bounds':copy.deepcopy(closed['sight_bounds'])}
    closed['sight']=[edge]; before=copy.deepcopy(f)
    effects,occluders=motion.sample_effects(geometry_case(),mf,f,.5)
    assert not effects.pending and len(occluders)==2
    assert all(o.bottom==pytest.approx(.6) and o.top==pytest.approx(2.1) for o in occluders)
    assert f==before


def test_height_rebuild_changes_floor_elevation_without_changing_authored_clearance():
    mf,f=vertical(); before=copy.deepcopy(f)
    first=motion.sample_effects(geometry_case(ground_dm=0,origin_dm=100),mf,f,.5)[1][0]
    rebuilt=motion.sample_effects(geometry_case(ground_dm=20,origin_dm=180),mf,f,.5)[1][0]
    assert first.bottom==pytest.approx(.6) and rebuilt.bottom==pytest.approx(2.6)
    assert rebuilt.top-first.top==pytest.approx(2)
    assert f==before


@pytest.mark.parametrize('case',['world','all_height','missing','unit','low','nonfinite','bad_reference'])
def test_invalid_vertical_inputs_are_pending_with_python_js_parity(case):
    _,f=vertical()
    if case in ('world','all_height'): motion.closed_state(f)['sight_bounds']['ref']=case
    if case=='missing': f['sliding']['open_clearance']=ms.unresolved()
    if case=='unit': f['sliding']['open_clearance']['unit']='player_heights'
    if case=='low': f['sliding']['open_clearance']['value']=2
    if case=='nonfinite': f['sliding']['open_clearance']['value']=None
    if case=='bad_reference': f['sliding']['closed_state']={}
    got=run_node('''function run(p) {return {geometry:F.slidingProblems(p.f),problems:F.slidingVerticalProblems(p.f),bounds:F.slidingVerticalBounds(p.f,.5)};}''',{'f':f})
    assert got['geometry']==motion.geometry_problems(f) and got['problems']==motion.vertical_problems(f)
    assert got['bounds'] is None and motion.vertical_bounds(f,.5) is None


def test_vertical_pose_and_band_parity_for_clamped_fractions():
    _,f=vertical(); fractions=[-1,0,.125,.5,.875,1,2]
    got=run_node('''function run(p) {return p.fractions.map(c => ({pose:F.slidingPose(p.f,c),bounds:F.slidingVerticalBounds(p.f,c)}));}''',{'f':f,'fractions':fractions})
    assert got==[{'pose':motion.pose(f,c),'bounds':motion.vertical_bounds(f,c)} for c in fractions]


def test_motion_preview_is_excluded_from_static_flat_composition_in_both_languages():
    mf,f=vertical(); sight=np.zeros((1024,1024),bool);walk=~sight
    # Remove the unrelated second feature to isolate the sliding preview contract.
    mf['features']=[f]
    a,b=cf.compose_masks(sight,walk,mf)
    assert not a.any() and b.all()
    got=run_node('''function run(p) {const m=F.composeFeatures(new Uint8Array(1024*1024),new Uint8Array(1024*1024).fill(1),p.mf);return {s:m.sight.some(Boolean),w:m.walk.every(Boolean)};}''',{'mf':mf})
    assert got=={'s':False,'w':True}


def test_page_vertical_sketch_scrubs_estimate_and_preserves_bounds_and_extra_edge():
    data=page_data(future=False);f=door(); closed=next(s for s in f['states'] if s['name']=='closed')
    closed['sight_bounds']={'ref':'unresolved'}
    closed['sight']=[{'geometry':{'type':'polyline','uv':[[4000,5000],[6000,5000]]},'bounds':{'ref':'unresolved'}}]
    f['sliding']={'axis':'vertical','open_state':'open','closed_state':'closed',
                  'open_clearance':{'status':'unresolved','estimate_player_heights':1.5}}
    mf=data['tags']['maps']['Ascent']['map_features']; mf['features']=[f];mf['triggers']=[];mf['routes']=[];mf['bundles']=[]
    got=run_page('''function run(p) {
      const page=openPage(p.data,{map:'Ascent'}),A=page.api,F=page.F; A.ui.sel='feature-1';A.render();
      function all(el) {return [el,...(el.children||[]).flatMap(all)];}
      const els=all(page.el('featProps')),slider=els.find(e=>e.id==='slideSlider'),diagram=els.find(e=>e.id==='slideElevation');
      slider.value='50';slider.dispatch('input');
      const sketch=els.find(e=>e.id==='slideGapLabel').textContent;
      const panelHeight=diagram.children[0].style.height;
      const preview=A.slidingPreview(F.find(A.fe.Ascent.mf,'feature-1'));
      const buttons=els.filter(e=>e.tagName==='BUTTON').map(e=>e.textContent);
      const out=F.exportCatalogue(A.canonical(),page.TG.DATA.maps,page.TG.edits,A.fe);
      return {sketch,panelHeight,vertical:preview.vertical,bounds:preview.bounds,buttons,out,disabled:slider.disabled};
    }''',{'data':data})
    assert not got['disabled'] and got['panelHeight']=='50%' and got['vertical'] and got['bounds'] is None
    assert got['sketch']=='Approximate proportional gap: 0.75 player heights; metres unresolved'
    assert 'Place open panel centre' not in got['buttons']
    exported=got['out']['maps']['Ascent']['map_features']['features'][0]
    assert exported['states']==f['states'] and exported['sliding']==f['sliding']

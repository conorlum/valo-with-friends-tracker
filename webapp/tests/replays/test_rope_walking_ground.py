import copy
import json
import base64

import numpy as np
import pytest

from app.control import geometry as cg, features as cf, rope_ground as rg, route_runtime as rr
from app.replays import map_feature_schema as ms, map_feature_inputs as fi, map_feature_artifacts as fa
from map_feature_artifact_toys import geometry_case, base_case
from tests.replays.test_quiet_routes import rope_case


def case():
    measured, mf = rope_case()
    measured.name = 'Abyss'
    # Keep a missing-height neighbour: this is the formerly free bypass between both levels.
    measured.heights.floors[40,41,:] = -1
    measured.heights.supported[40,41] = False
    # Preserve a real stair exit around the rope. It is slower than the two-second
    # rope, so cutting the unknown-height shortcut must not isolate the upper floor.
    walk = measured.walk_px.copy()
    walk[312:320,320:352] = True
    asset = measured.heights
    asset.floors[39,40:44,0] = [40,30,20,10]
    asset.supported[39,40:44] = True
    asset.unresolved[39,40:44] = False
    from app.control.heights import KIND_STANDS
    asset.kind[39,40:44] = KIND_STANDS
    path = [(40*128+40,1), *[(39*128+c,0) for c in range(40,44)], (40*128+43,0)]
    edges = list(map(tuple,asset.edges))
    for (a,al),(b,bl) in zip(path,path[1:]):
        edges.extend([(a,al,b,bl,0),(b,bl,a,al,0)])
    asset.edges = np.array(edges,np.int32)
    flat = cg.geometry_from_masks('Abyss',~walk,walk,7e-5,[])
    measured = cg.attach_heights(cg.geometry_from_masks('Abyss',~walk,walk,7e-5,[]),asset)
    mf['routes'][0]['walking_domain']=rg.from_asset(flat,asset,[320,312,352,328])
    return flat,measured,mf


def artifact(flat,mf):
    raw=json.dumps({'maps':{'Abyss':{'map_features':mf,'specials':[]}}}).encode()
    base = base_case()
    for key,mask in [('walk',flat.walk_px),('sight',flat.sight)]:
        packed = np.packbits(mask.ravel(),bitorder='little').tobytes()
        base[key]['data'] = base64.b64encode(packed).decode('ascii')
    inp=fi.identify_features('Abyss','flat',fi.capture_source_snapshot('Abyss',raw),base)
    return cf.compile_artifact(flat,inp,'0'*40)


def test_supported_local_ground_is_immutable_and_never_changes_terrain_sight():
    flat,measured,mf=case()
    walking=rg.prepare(flat,mf)
    assert flat.heights is None and flat.n==128**2
    assert walking.movement_only and walking.height_sha is None and walking.movement_height_sha
    assert rg.prepare(walking,mf) is walking
    assert np.isnan(walking.node_z[5161]), 'unsampled neighbours remain unknown'
    assert not cg.los(measured,(324,324,.7),(324,324,4.7))
    assert cg.los(walking,(324,324,.7),(324,324,4.7))
    angles=np.arange(0,360,15)
    assert np.array_equal(walking.to_cells(cg.cast(walking,324,324,angles,[],eye_z=.7)),
                          cg.cast(flat,324,324,angles,[]))
    assert cg.cache_key(flat)!=cg.cache_key(walking)


@pytest.mark.slow
def test_walking_archive_reproduces_after_cache_loss_and_does_not_get_a_global_height_key(tmp_path):
    from app.control.feature_runtime import prepare_geometry
    from app.control.task import load_task_features,geometry_used
    flat,measured,mf=case();item=artifact(flat,mf)
    assert item.key.height_digest=='flat' and item.manifest['active_bundles']==['bundle-3']
    fa.store_cached_artifact(tmp_path,item,lambda a:cf.verify_artifact(a,flat))
    request={'map':'Abyss','features':item.digest,'height_mode':'flat'}
    fresh=case()[0]
    load_task_features(request,fresh,tmp_path)
    walking=prepare_geometry(fresh)
    assert walking.movement_only and 'height' not in geometry_used(walking)
    assert geometry_used(walking)['features']==item.digest
    cf.verify_artifact(fa.decode_artifact(fa.encode_artifact(item)),fresh)
    assert prepare_geometry(walking) is walking
    assert fresh.n==128**2 and fresh.heights is None
    assert fa.read_json(fa.expanded_assets(item.assets))['nodes']==walking.n


def test_quiet_rope_cost_and_reverse_policy_cannot_be_bypassed_by_unknown_ground():
    from app.control.feature_runtime import prepare_geometry
    flat,_,mf=case();flat.features=artifact(flat,mf);walking=prepare_geometry(flat)
    quiet=rr.topology_for(walking);arc=quiet.arcs[0]
    from app.control.engine import UNKNOWN_MPS
    step = walking.cell_m / UNKNOWN_MPS
    free=np.full(walking.n,-np.inf)
    reached=np.full(walking.n,np.inf);reached[arc.src]=0
    assert np.isinf(quiet.spread(reached,walking.walk_n,free,1.999,step,[],quiet=True)[arc.dst])
    after,parent=quiet.spread(reached,walking.walk_n,free,2.,step,[],parents=True,quiet=True)
    assert after[arc.dst]==2. and parent[arc.dst]==arc.src
    reverse=np.full(walking.n,np.inf);reverse[arc.dst]=0
    assert not any(a.src==arc.dst and a.dst==arc.src for a in quiet.arcs)
    assert np.isinf(quiet.spread(reverse,walking.walk_n,free,1.999,step,[],quiet=True)[arc.src])
    assert np.isfinite(quiet.spread(reverse,walking.walk_n,free,20.,step,[],quiet=True)[arc.src])


@pytest.mark.parametrize('change',['version','wide','mask','off_ground','unordered','edge','unbound','foreign_map'])
def test_invalid_domains_do_not_open_ground_or_silently_publish(change):
    flat,_,mf=case();domain=mf['routes'][0]['walking_domain']
    if change=='version':domain['v']=2
    elif change=='wide':domain['box']=[0,0,1024,1024]
    elif change=='mask':domain['walk_sha']='0'*12
    elif change=='off_ground':domain['coarse'].insert(0,[0,[100],1])
    elif change=='unordered':domain['coarse'][0][1]=[140,100]
    elif change=='edge':domain['edges'].append([5160,0,5163,0,0])
    elif change=='unbound':mf['routes'][0]['endpoints'][1]['landing']['world_z']=ms.known(99,'m')
    else:flat.name='Split'
    with pytest.raises(fa.FeatureArtifactCorrupt):rg.prepare(flat,mf)
    assert flat.heights is None and flat.n==128**2


def test_disabled_domain_and_full_selected_height_do_not_apply_the_fallback():
    flat,measured,mf=case()
    assert rg.prepare(measured,mf) is measured and not measured.movement_only
    mf['bundles'][0]['enabled']=False
    assert rg.prepare(flat,mf) is flat and flat.heights is None


def test_local_walking_asset_cannot_pass_the_global_activation_verifier(tmp_path,monkeypatch):
    from app.control import heights as hc,height_verify as hv
    flat,_,mf=case();walking=rg.prepare(flat,mf)
    path=tmp_path/'walking.npz';hc.save_asset(path,walking.heights)
    monkeypatch.setattr(cg,'load_base_geometry',lambda *a,**k:flat)
    assert 'cannot be activated' in hv.describe(path.read_bytes(),'Abyss')['error']


def test_refined_children_keep_exact_footprints_and_unknown_children():
    from tests.replays.test_adaptive_ground import mixed
    measured=mixed((0,40,None,0),name='Abyss')
    flat=cg.geometry_from_masks('Abyss',measured.sight,measured.walk_px,measured.uv_per_unit/10000)
    _,mf=rope_case()
    route=mf['routes'][0]
    route['endpoints']=[{'id':key,'uv':[x*10000/1024,202*10000/1024],
                        'landing':{'world_z':ms.known(z,'m'),'tolerance':ms.known(.2,'m')}}
                       for key,x,z in (('a',202,0),('b',206,4))]
    route['walking_domain']=rg.from_asset(flat,measured.heights,[192,192,224,216])
    walking=rg.prepare(flat,mf)
    assert walking.adaptive and walking.unresolved_at_px(202,206)
    a,_=cf._point_node(walking,mf,route['endpoints'][0]['uv'],landing=route['endpoints'][0]['landing'])
    b,_=cf._point_node(walking,mf,route['endpoints'][1]['uv'],landing=route['endpoints'][1]['landing'])
    assert a!=b and walking.node_z[b]-walking.node_z[a]==4
    assert walking.area(walking.walk_n)==flat.area(flat.walk_n)


@pytest.mark.slow
def test_isolated_compiler_reproduces_walking_archive_without_global_height_bytes():
    import subprocess,sys
    from dataclasses import asdict
    import base64
    flat,_,mf=case();item=artifact(flat,mf)
    request={'mode':'compile','height':None,'inputs':{'key':asdict(item.key),
             'canonical_inputs':base64.b64encode(item.inputs).decode()}}
    result=subprocess.run([sys.executable,'-m','app.control.feature_job'],input=fi.canonical_json(request),
                          capture_output=True,timeout=60,check=True)
    reply=json.loads(result.stdout)
    assert reply['ok'],reply
    rebuilt=fa.decode_artifact(fi.canonical_json(reply['result']))
    assert rebuilt.digest==item.digest and rebuilt.assets==item.assets


@pytest.mark.slow
def test_reviewed_rope_only_platform_compiles_and_restores_without_a_walk_exemption():
    from app.control.feature_runtime import prepare_geometry
    measured, mf = rope_case()
    measured.name = 'Abyss'
    measured.heights.floors[40,41,:] = -1
    measured.heights.supported[40,41] = False
    cg.attach_heights(measured, measured.heights)
    flat = geometry_case(flat=True); flat.name = 'Abyss'
    route = mf['routes'][0]
    route['walking_domain'] = rg.from_asset(flat, measured.heights, [320,320,352,328])
    with pytest.raises(fa.FeatureArtifactCorrupt, match='unqualified rope bundle'):
        artifact(flat, mf)
    route['quiet_rope_only_endpoints'] = ['b']
    item = artifact(flat, mf)
    assert item.manifest['active_bundles'] == ['bundle-3']
    restored = fa.decode_artifact(fa.encode_artifact(item))
    fresh = geometry_case(flat=True); fresh.name = 'Abyss'; fresh.features = restored
    cf.verify_artifact(restored, fresh)
    walking = prepare_geometry(fresh)
    quiet = rr.topology_for(walking)
    assert quiet.arcs and quiet.cuts
    assert fi.read_json(restored.inputs)['runtime']['routes'][0]['quiet_rope_only_endpoints'] == ['b']

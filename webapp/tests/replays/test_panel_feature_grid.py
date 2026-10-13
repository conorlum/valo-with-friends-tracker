"""Moving-wall majority occupancy on local leaves without inventing terrain measurements."""
import copy

import numpy as np
import pytest

from app.control import features as cf, feature_geometry as fg, geometry as cg, map_pool_runtime as pool, topology
from app.control.feature_runtime import prepare_geometry
from app.replays import map_feature_inputs as fi, map_feature_artifacts as fa, map_feature_schema as ms
from test_adaptive_ground import mixed
from test_map_pool_runtime import lotus


def case():
    geo = mixed(name='Lotus')
    f = lotus()
    shape = {'type': 'polygon', 'uv': [[x*10000/1024, y*10000/1024]
        for x, y in [(200, 200), (208, 200), (208, 208), (200, 208)]]}
    f['rotation'].update(panel=shape, pivot={'type': 'point', 'uv': [204*10000/1024]*2})
    for state in f['states']:
        state['footprint'] = copy.deepcopy(shape)
    mf = {**ms.empty(), 'features': [f], 'bundles': [{'id': 'bundle-1', 'enabled': True,
        'runtime_consumer': 'map_pool_replay_v1', 'members': [f['id']]}]}
    return geo, mf, f


def test_swept_area_refines_only_locally_and_preserves_original_measurements():
    geo, mf, f = case()
    before = geo.heights.digest
    fresh = fg.prepare(geo, mf)
    assert fresh is not geo and fresh.adaptive
    assert fresh.height_sha == geo.height_sha and geo.heights.digest == before
    assert fresh.panel_height_source is geo.heights
    assert fg.prepare(fresh, mf) is fresh
    assert cg.cache_key(fresh) != cg.cache_key(geo)
    assert np.array_equal(fresh.walk_px, geo.walk_px)
    assert fresh.area(fresh.walk_n) == geo.area(geo.walk_n)
    assert np.array_equal(fresh.heights.fine_floors[np.searchsorted(fresh.heights.fine_cells,
        geo.heights.fine_cells)], geo.heights.fine_floors)
    # A neighbouring formerly coarse floor has four independently blockable leaves.
    parent = 24*128+25
    nodes = fresh.node_of[parent]; nodes = nodes[nodes >= 0]
    assert len(nodes) == 4 and np.all(fresh.node_z[nodes] == 0)
    source, target = fresh.node_at_px(202, 198, 0), fresh.node_at_px(210, 198, 0)
    assert topology.of(fresh).dist(source)[target] == 1
    assert not fg.prepare(geo, {**mf, 'bundles': []}).panel_grid_digest


def test_half_coverage_is_open_for_each_child_not_the_old_parent():
    geo, mf, f = case(); geo = fg.prepare(geo, mf)
    # At this neighbouring parent, 6/8 width: left children full, right exactly half.
    panel = {'type': 'polygon', 'uv': [[x*10000/1024, y*10000/1024]
        for x, y in [(200, 192), (206, 192), (206, 200), (200, 200)]]}
    effects = pool.rotation_effects(geo, mf, f, panel)
    assert not effects.pending
    assert effects.blocked[geo.node_at_px(202, 194, 0)]
    assert not effects.blocked[geo.node_at_px(206, 194, 0)]
    assert geo.area(effects.blocked) == .5


def test_panel_grid_reproduces_through_archived_inputs_and_original_height():
    geo, mf, _ = case()
    base = dict(sight=cf._packed_mask(geo.sight), walk=cf._packed_mask(geo.walk_px),
        barrier=None, scale=geo.uv_per_unit/10000, specials=[], legacy={})
    source = fi.capture_source_snapshot('Lotus', fi.canonical_json({'maps': {'Lotus': {'map_features': mf}}}))
    inp = fi.identify_features('Lotus', geo.height_sha, source, base)
    artifact = cf.compile_artifact(geo, inp, '0'*40)
    assert artifact.manifest['active_bundles'] == ['bundle-1'], artifact.manifest['pending']
    restored = cf.geometry_from_feature_inputs(inp, geo.heights)
    cf.verify_artifact(fa.decode_artifact(fa.encode_artifact(artifact)), restored)
    restored.features, restored.features_sha = artifact, artifact.digest
    runtime = prepare_geometry(restored)
    assets = fi.read_json(fa.expanded_assets(artifact.assets))
    assert assets['panel_grid']['digest'] == runtime.panel_grid_digest
    assert assets['nodes'] == runtime.n
    assert np.array_equal(runtime.node_bbox, fg.prepare(geo, mf).node_bbox)


def test_molly_hits_refined_children_on_only_its_own_floor():
    from app.control import engine, mollies
    from types import SimpleNamespace
    geo, mf, _ = case(); geo = fg.prepare(geo, mf)
    molly = mollies.Molly(0, 204*10000/1024, 198*10000/1024, .8, 0, 5, through=True, z=0)
    rnd = SimpleNamespace(geo=geo, missing={}, _device_z=lambda z: z/10)
    hit = engine.RoundInputs._molly_nodes(rnd, molly, molly.z)
    expected = np.flatnonzero(geo.walk_n & (geo.node_z == 0) &
        (np.linalg.norm(geo.centres-[204, 198], axis=1)*geo.m_per_px <= .8))
    assert np.array_equal(hit, expected)
    assert len(hit) >= 4


def test_computational_grid_cannot_be_activated_as_measured_terrain(tmp_path):
    from app.control import heights, height_verify
    geo, mf, _ = case(); geo = fg.prepare(geo, mf)
    folder = tmp_path/'Lotus'; folder.mkdir()
    cg.write_mask_png(folder/'Lotus.sight.png', geo.sight)
    cg.write_mask_png(folder/'Lotus.walk.png', geo.walk_px)
    path = folder/'Lotus.height.npz'; heights.save_asset(path, geo.heights)
    report = height_verify.describe(path.read_bytes(), 'Lotus', asset_dir=folder)
    assert 'computational panel tessellation' in report['error']


def test_grid_preserves_unknowns_and_distinct_stacked_floors():
    geo, mf, _ = case()
    asset = copy.deepcopy(geo.heights)
    unknown, stacked = 24*128+25, 24*128+26
    asset.floors.reshape(-1, 3)[unknown] = -1
    asset.supported.ravel()[unknown] = False
    asset.unresolved.ravel()[unknown] = True
    asset.floors.reshape(-1, 3)[stacked, 1] = 40
    for key in ('edges', 'fine_edges'):
        edges = getattr(asset, key)
        setattr(asset, key, edges[(edges[:, 0] != unknown) & (edges[:, 2] != unknown)])
    # This is a geometry preservation check; no cross-floor edges are invented.
    cg.attach_heights(geo, asset)
    fresh = fg.prepare(geo, mf)
    for n in fresh.node_of[unknown]:
        if n >= 0:
            assert np.isnan(fresh.node_z[n])
    nodes = fresh.node_of[stacked]; nodes = nodes[nodes >= 0]
    assert len(nodes) == 8 and set(fresh.node_z[nodes]) == {0, 4}
    for a, _, b, _, _ in fresh.heights.fine_edges:
        if a == b:
            pytest.fail('computational tessellation connected two floors in one footprint')

import copy
import gzip

import numpy as np
import pytest

from app.control import features as cf, topology
from app.control.route_runtime import landing_node, QuietTopology, topology_for
from app.replays import map_feature_schema as ms
from map_feature_artifact_toys import geometry_case


def landing(z):
    return {'world_z': ms.known(z, 'm'), 'tolerance': ms.known(.2, 'm')}


def rope_case():
    geo = geometry_case(multi=True)
    from app.control.geometry import attach_heights
    edges = []
    for col in range(40, 43):
        a, b = 40 * 128 + col, 40 * 128 + col + 1
        edges += [(a, 0, b, 0, 0), (b, 0, a, 0, 0)]
    geo.heights.edges = np.array(edges, np.int32)
    attach_heights(geo, geo.heights)
    geo.name = 'Split'
    uv = [324 / 1024 * 10000] * 2
    feature = {'id': 'feature-1', 'preset': 'vertical_rope', 'initial_state': 'available', 'transitions': [],
               'states': [{'name': 'available', 'blocks_movement': False, 'blocks_sight': False}]}
    direction = {'from': 'a', 'to': 'b', 'entry': ms.known(0, 's'), 'transit': ms.known(2, 's')}
    route = {'id': 'route-2', 'owner': 'feature-1', 'kind': 'rope', 'access': 'endpoint_only',
             'endpoints': [{'id': 'a', 'uv': uv, 'landing': landing(10)},
                           {'id': 'b', 'uv': uv, 'landing': landing(14)}],
             'directions': [direction, {**direction, 'from': 'b', 'to': 'a'}],
             'quiet_directions': [direction], 'quiet_cuts': []}
    mf = {**ms.empty(), 'features': [feature], 'routes': [route],
          'bundles': [{'id': 'bundle-3', 'enabled': True, 'runtime_consumer': 'quiet_rope_v1',
                       'members': ['feature-1']}]}
    return geo, mf


def test_coincident_rope_endpoints_bind_measured_world_heights_and_rebase():
    geo, mf = rope_case()
    arcs, pending = cf.compile_routes(geo, mf)
    assert not pending and len(arcs) == 2
    assert arcs[0].src != arcs[0].dst
    new = geometry_case(multi=True, ground_dm=20, origin_dm=80)
    rebound, pending = cf.compile_routes(new, mf)
    assert not pending
    assert new.node_z[rebound[0].src] + new.heights.origin_z / 10 == 10
    assert new.node_z[rebound[0].dst] + new.heights.origin_z / 10 == 14


@pytest.mark.parametrize('kind', ['flat', 'unresolved', 'wrong', 'ambiguous'])
def test_landings_never_guess_a_floor(kind):
    geo, mf = rope_case()
    if kind == 'flat':
        geo = geometry_case(flat=True)
    elif kind == 'unresolved':
        geo = geometry_case(unresolved=True)
    elif kind == 'wrong':
        mf['routes'][0]['endpoints'][0]['landing'] = landing(99)
    else:
        mf['routes'][0]['endpoints'][0]['landing']['tolerance'] = ms.known(5, 'm')
    arcs, pending = cf.compile_routes(geo, mf)
    assert not arcs and pending


def test_timed_quiet_route_pays_full_seconds_and_retains_parent():
    geo, mf = rope_case()
    arc = cf.compile_routes(geo, mf)[0][0]
    topo = QuietTopology(topology.of(geo), [arc])
    reached = np.full(geo.n, np.inf); reached[arc.src] = 7
    room = geo.walk_n.copy(); free = np.full(geo.n, -np.inf)
    before = topo.spread(reached, room, free, 8.999, .1, [], quiet=True)
    assert np.isinf(before[arc.dst])
    after, parent = topo.spread(reached, room, free, 9, .1, [], parents=True, quiet=True)
    assert after[arc.dst] == 9 and parent[arc.dst] == arc.src
    # Physical movement keeps the original topology and doesn't acquire a quiet teleport.
    assert np.isinf(topo.spread(reached, room, free, 9, .1, [], quiet=False)[arc.dst])
    reverse = np.full(geo.n, np.inf); reverse[arc.dst] = 0
    assert np.isinf(topo.spread(reverse, room, free, 100, .1, [], quiet=True)[arc.src])


def test_connector_cut_changes_only_quiet_movement_and_preserves_alternate_walk():
    geo, mf = rope_case()
    arc = cf.compile_routes(geo, mf)[0][0]
    base = topology.of(geo)
    # A legacy bidirectional connector would otherwise take only one walking step.
    topo = QuietTopology(base, [arc], [(arc.src, arc.dst), (arc.dst, arc.src)])
    reached = np.full(geo.n, np.inf); reached[arc.src] = 0
    room = geo.walk_n.copy(); free = np.full(geo.n, -np.inf)
    links = [(arc.src, arc.dst, False)]
    assert np.isinf(topo.spread(reached, room, free, 1, .1, links, quiet=True)[arc.dst])
    assert topo.spread(reached, room, free, 1, .1, links, quiet=False)[arc.dst] == .1
    neighbor = int(geo.node_of[40 * 128 + 41, 0])
    assert topo.spread(reached, room, free, 1, .1, links, quiet=True)[neighbor] == .1


def test_free_destination_and_blocked_route_do_not_arrive_retroactively():
    geo, mf = rope_case()
    arc = cf.compile_routes(geo, mf)[0][0]
    topo = QuietTopology(topology.of(geo), [arc])
    reached = np.full(geo.n, np.inf); reached[arc.src] = 0
    free = np.full(geo.n, -np.inf); free[arc.dst] = 5
    room = geo.walk_n.copy()
    assert np.isinf(topo.spread(reached, room, free, 6.99, .1, [], quiet=True)[arc.dst])
    assert topo.spread(reached, room, free, 7, .1, [], quiet=True)[arc.dst] == 7
    room[arc.dst] = False
    assert np.isinf(topo.spread(reached, room, free, 100, .1, [], quiet=True)[arc.dst])


def test_runtime_uses_only_active_bundle_and_quiet_direction_contract():
    from app.replays.map_feature_artifacts import FeatureArtifact
    from app.replays.map_feature_inputs import FeatureKey, canonical_json
    geo, mf = rope_case()
    geo.features = FeatureArtifact('a' * 64, FeatureKey('Split', geo.height_sha, 'b' * 64, 6),
        {'active_bundles': []}, canonical_json({'runtime': mf}), gzip.compress(b'{}'), 'c' * 40)
    assert topology_for(geo) is topology.of(geo)
    geo.features.manifest['active_bundles'] = ['bundle-3']
    quiet = topology_for(geo)
    assert len(quiet.arcs) == 1 and quiet.arcs[0].cost_s == 2
    assert len(cf.compile_routes(geo, mf)[0]) == 2  # physical directions survive
    from app.control.engine import Unknown
    assert isinstance(Unknown(geo).topo, QuietTopology)


def test_landing_and_quiet_cost_change_runtime_identity_but_retired_floor_does_not():
    _, mf = rope_case()
    original = ms.runtime_digest(mf)
    changed = copy.deepcopy(mf); changed['routes'][0]['endpoints'][0]['floor'] = 'floor-99'
    assert ms.runtime_digest(changed) == original
    changed['routes'][0]['endpoints'][0]['landing'] = landing(11)
    assert ms.runtime_digest(changed) != original
    changed = copy.deepcopy(mf); changed['routes'][0]['quiet_directions'][0]['transit'] = ms.known(3, 's')
    assert ms.runtime_digest(changed) != original


def test_compiler_archives_quiet_arcs_and_reverifies_against_exact_height_context():
    from app.replays.map_feature_inputs import identify_features, capture_source_snapshot
    from app.control.features import compile_artifact, verify_artifact
    from map_feature_artifact_toys import base_case
    import json
    geo, mf = rope_case()
    source = capture_source_snapshot('Split', json.dumps({'maps': {'Split': {'map_features': mf}}}).encode())
    inputs = identify_features('Split', geo.height_sha, source, base_case())
    artifact = compile_artifact(geo, inputs, 'c' * 40)
    assert artifact.manifest['active_bundles'] == ['bundle-3'], artifact.manifest['pending']
    from app.replays.map_feature_artifacts import expanded_assets
    assets = json.loads(expanded_assets(artifact.assets))
    assert len(assets['arcs']) == 1 and assets['arcs'][0][3:5] == [0, 2]
    verify_artifact(artifact, geo)

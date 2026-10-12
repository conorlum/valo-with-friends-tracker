"""A quiet rope must retain a reviewed stair exit without acquiring a free level change."""
from types import SimpleNamespace
import copy

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from app.control import features as cf, route_runtime as rr
from app.replays import map_feature_schema as ms


def case():
    centres = np.array([(0, 0), (0, 0), (8, 0), (24, 0), (32, 0),
                        (8, -8), (16, -8), (32, 8)], float) + 200
    geo = SimpleNamespace(n=8, node_z=np.array([0., 4., np.nan, 4., np.nan, 4., 4., 0.]),
        centres=centres, walk_n=np.ones(8, bool), m_per_px=.05, adaptive=False,
        heights=SimpleNamespace(origin_z=0), cell_m=1.)
    geo.nodes_at_px = lambda x, y: np.flatnonzero(np.linalg.norm(centres-[x, y], axis=1) < .1)
    pairs = [(0, 2), (1, 2), (1, 5), (5, 6), (6, 3), (3, 4), (4, 7), (7, 0)]
    pairs += [(b, a) for a, b in pairs]
    incoming = np.full((8, 3), 8, int); costs = np.full((8, 3), np.inf)
    for a, b in pairs:
        i = np.flatnonzero(incoming[b] == 8)[0]
        incoming[b, i] = a; costs[b, i] = np.linalg.norm(centres[a]-centres[b])/8
    base = SimpleNamespace(in_from=incoming, in_cost_quiet=costs)
    arcs = [cf.Arc('route-1', a, b, 0., 2., None) for a, b in [(0, 1), (1, 0)]]
    point = lambda n: dict(uv=(centres[n]*10000/1024).tolist(),
                          landing=dict(world_z=ms.known(4, 'm'), tolerance=ms.known(.2, 'm')))
    connection = {'from': point(3), 'to': point(4)}
    route = {'id': 'route-1', 'owner': 'feature-1', 'quiet_walk_connections':
             [connection, {'from': point(4), 'to': point(3)}]}
    return geo, base, arcs, {'routes': [route]}


def test_cut_gate_detects_missing_stair_exit_and_explicit_walk_preserves_rope_cost():
    geo, base, arcs, mf = case()
    raw = rr.local_rope_cuts(geo, base, arcs)
    assert rr.walking_exit_problems(geo, base, arcs, raw)
    cuts, reasons = rr.qualified_cuts(geo, mf, {'feature-1'}, base, arcs)
    assert not reasons and not rr.walking_exit_problems(geo, base, arcs, cuts)
    assert (1, 2) in cuts and (2, 1) in cuts, 'unobserved cheap rope shortcut stays cut'
    assert (3, 4) not in cuts and (4, 3) not in cuts, 'ordinary stair exit is preserved'
    cost = rr.QuietTopology(base, arcs, cuts).cost
    dst = np.broadcast_to(np.arange(8)[:, None], cost.shape)
    keep = (base.in_from < 8) & np.isfinite(cost)
    graph = csr_matrix((cost[keep]*.3, (base.in_from[keep], dst[keep])), shape=(8, 8))
    assert dijkstra(graph, indices=0)[1] > 2
    assert np.isnan(geo.node_z[4]), 'walk evidence does not invent a height'


@pytest.mark.parametrize('bad', ['nonadjacent', 'wrong_height', 'two_unknown', 'invalid', 'too_many'])
def test_walk_records_cannot_create_edges_or_bind_unsupported_ground(bad):
    geo, base, arcs, mf = case(); mf = copy.deepcopy(mf)
    route = mf['routes'][0]; connection = route['quiet_walk_connections'][0]
    if bad == 'nonadjacent':
        connection['from']['uv'] = (geo.centres[5]*10000/1024).tolist()
    elif bad == 'wrong_height':
        connection['from']['landing']['world_z'] = ms.known(100, 'm')
    elif bad == 'two_unknown':
        geo.node_z[3] = np.nan
    elif bad == 'invalid':
        route['quiet_walk_connections'] = [None]
    else:
        route['quiet_walk_connections'] *= 33
    _, reasons = rr.qualified_cuts(geo, mf, {'feature-1'}, base, arcs)
    assert reasons


def test_explicit_quiet_cut_wins_over_walk_record(monkeypatch):
    geo, base, arcs, mf = case()
    mf['routes'][0]['quiet_cuts'] = [mf['routes'][0]['quiet_walk_connections'][0]]
    monkeypatch.setattr(cf, '_point_node', lambda geo, mf, uv, **kwargs:
        (int(geo.nodes_at_px(*(np.asarray(uv)*1024/10000))[0]), None))
    cuts, reasons = rr.qualified_cuts(geo, mf, {'feature-1'}, base, arcs)
    assert not reasons and (3, 4) in cuts


def test_retained_walk_cannot_reopen_a_shortcut_through_an_unknown_leaf():
    geo, base, arcs, mf = case()
    point = lambda n: dict(uv=(geo.centres[n]*10000/1024).tolist(),
        landing=dict(world_z=ms.known(4, 'm'), tolerance=ms.known(.2, 'm')))
    # This looks like a supported upper walk, but its unknown neighbour also
    # joins the lower landing. Retaining it bypasses the two-second rope.
    mf['routes'][0]['quiet_walk_connections'] = [
        {'from': point(1), 'to': point(2)}, {'from': point(2), 'to': point(1)}]
    cuts, reasons = rr.qualified_cuts(geo, mf, {'feature-1'}, base, arcs)
    assert (1, 2) not in cuts
    assert reasons == ['route-1: retained quiet walk bypasses reviewed rope travel time']


def test_one_way_entry_cannot_hide_a_removed_walking_exit():
    geo = SimpleNamespace(n=3, walk_n=np.ones(3, bool))
    base = SimpleNamespace(in_from=np.array([[1, 2], [0, 3], [0, 3]]),
                          in_cost_quiet=np.array([[1., 1.], [1., np.inf], [1., np.inf]]))
    arc = cf.Arc('route-1', 1, 2, 0., 2., None)
    assert rr.walking_exit_problems(geo, base, [arc], {(1, 0)}) == [
        'route-1: quiet rope cuts remove the landing walking exit']
    assert not rr.walking_exit_problems(geo, base, [arc], set())

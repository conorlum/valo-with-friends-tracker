import copy

import pytest

from app.control.rope_positions import landing_pairs


def evidence():
    ride = dict(match='recording-a', round=2, slot=3, start_s=5., end_s=7.,
                uv=[5000, 5000], run_z_range_m=[2.1, 5.9])
    ground = [dict(match='recording-a', round=2, slot=3, t0=4.9, t1=4.9,
                   uv=[5000, 5000], world_z=2., node=1),
              dict(match='recording-a', round=2, slot=3, t0=7.1, t1=7.1,
                   uv=[5000, 5000], world_z=6., node=2)]
    return ride, ground


def test_vertical_rope_can_bind_same_xy_to_distinct_measured_floors():
    ride, ground = evidence()
    pairs = landing_pairs([ride], ground)
    assert len(pairs) == 1
    assert (pairs[0]['a']['node'], pairs[0]['b']['node']) == (1, 2)
    assert (pairs[0]['a']['world_z'], pairs[0]['b']['world_z']) == (2., 6.)
    assert 'transit' not in pairs[0], 'geometry never overrides reviewed travel time'


@pytest.mark.parametrize('key,value', [('match', 'recording-b'), ('round', 3), ('slot', 4), ('node', None)])
def test_landing_evidence_cannot_cross_identity_or_use_unresolved_ground(key, value):
    ride, ground = evidence()
    ground[1][key] = value
    assert not landing_pairs([ride], ground)


@pytest.mark.parametrize('change', ['self_loop', 'far_away', 'late', 'partial_ride'])
def test_landing_evidence_does_not_guess_a_missing_end(change):
    ride, ground = evidence()
    if change == 'self_loop':
        ground[1]['node'] = 1
    elif change == 'far_away':
        ground[1]['uv'] = [8000, 8000]
    elif change == 'late':
        ground[1]['t0'] = ground[1]['t1'] = 20.
    else:
        ride['run_z_range_m'] = [3., 4.]
    assert not landing_pairs([ride], ground)


def test_evidence_proposals_do_not_mutate_source_tracks_or_ground():
    ride, ground = evidence()
    before = copy.deepcopy((ride, ground))
    landing_pairs([ride], ground)
    assert (ride, ground) == before


def test_unknown_height_neighbor_cannot_bypass_a_vertical_rope_cost():
    import numpy as np
    from map_feature_artifact_toys import geometry_case
    from app.control import features as cf, geometry as cg, route_runtime as rr, topology
    geo = geometry_case(multi=True)
    asset = geo.heights
    asset.floors[40, 41, :] = -1
    asset.unresolved[40, 41] = True
    cg.attach_heights(geo, asset)
    base = topology.of(geo)
    low, high = geo.node_of[5160, :2]
    arcs = [cf.Arc('rope', int(a), int(b), 0., 2., None)
            for a, b in ((low, high), (high, low))]
    cuts = rr.local_rope_cuts(geo, base, arcs)
    quiet = rr.QuietTopology(base, arcs, cuts)
    free = np.full(geo.n, -np.inf)
    for start, end in ((low, high), (high, low)):
        reached = np.full(geo.n, np.inf); reached[start] = 0.
        raw = base.spread(reached, geo.walk_n, free, 5., .1, [], quiet=True)
        fixed = quiet.spread(reached, geo.walk_n, free, 5., .1, [], quiet=True)
        assert raw[end] < 2. and fixed[end] == pytest.approx(2.)
        assert np.array_equal(quiet.spread(reached, geo.walk_n, free, 5., .1, [], quiet=False),
                              base.spread(reached, geo.walk_n, free, 5., .1, [], quiet=False))
    # The unknown neighbour is still accessible from the lower level; no height was fabricated.
    reached = np.full(geo.n, np.inf); reached[low] = 0.
    assert np.isfinite(quiet.spread(reached, geo.walk_n, free, 5., .1, [], quiet=True)[5161])
    assert np.isnan(geo.node_z[5161])


def test_rope_shortcut_cuts_preserve_measured_gradual_slopes_and_distant_ground():
    from types import SimpleNamespace
    import numpy as np
    from app.control import features as cf, route_runtime as rr
    geo = SimpleNamespace(n=7, node_z=np.array([0., 1., 2., 3., 4., np.nan, 4.]),
        centres=np.array([[i*4., 0.] for i in range(5)]+[[500., 0.], [504., 0.]]), m_per_px=.2)
    base = SimpleNamespace(in_from=np.array([[7], [0], [1], [2], [3], [6], [5]]),
                           in_cost_quiet=np.ones((7, 1)))
    arc = cf.Arc('rope', 0, 4, 0., 2., None)
    assert not rr.local_rope_cuts(geo, base, [arc])

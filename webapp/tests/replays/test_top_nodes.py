"""Learnt top nodes have their own identity; ground readers keep their old footprints (W8a)."""
import copy
import hashlib
import json
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest

from app.control import adaptive as ag, engine as ce, features as cf, geometry as cg
from app.control import heights as hc, route_runtime as rr, terrain_audit, topology, utility as ut
from tests.replays.test_adaptive_ground import mixed, PARENT, CHILDREN
from tests.replays.test_adaptive_cover import covered
from tests.replays.control_toys import height_blob


def topped(rows=((202, 202, 12),), *, cover=True, name='Split'):
    geo = mixed(name=name)
    if cover:
        pixels = np.zeros((1024, 1024), bool)
        pixels[200:208, 200:216] = True  # four fine leaves plus one unrefined 8 px leaf, one piece
        cg.attach_cover(geo, pixels, 'top-node-toy')
    asset = copy.deepcopy(geo.heights)
    asset.box_tops = np.asarray(rows, np.int32).reshape(-1, 3) if rows is not None else None
    return cg.attach_heights(geo, asset)


def old_key(geo):
    """The pre-W8a height cache recipe, frozen for maps without top nodes."""
    digest = hashlib.sha256()
    digest.update(json.dumps(dict(v=cg.GEOMETRY_VERSION, px=cg.PX, grid=cg.GRID,
        ray_step_deg=cg.RAY_STEP_DEG, ray_step_px=cg.RAY_STEP_PX,
        ray_max_px=cg.RAY_MAX_PX, tol_hits=geo.tol_hits), sort_keys=True).encode('ascii'))
    digest.update(np.packbits(geo.sight).tobytes())
    digest.update(np.packbits(geo.walk_px).tobytes())
    digest.update(json.dumps(dict(height=geo.height_sha, eye=hc.EYE_M, head=hc.HEAD_M,
        feet=hc.FEET_M, stand=hc.STAND_M, ledge=hc.LEDGE_M, snap=hc.NODE_SNAP_M,
        slabs=cg.SLAB_SLOTS), sort_keys=True).encode('ascii'))
    return digest.hexdigest()


def test_one_top_per_majority_leaf_with_separate_ground_identity():
    geo, ground = topped(), topped(None)
    leaves = sorted([ag.BASE + c for c in CHILDREN] + [PARENT + 1])
    assert geo.n == ground.n + 5
    assert geo.top_nodes.tolist() == list(range(ground.n, geo.n))
    assert geo.top_leaf.tolist() == leaves
    assert geo.top_piece.tolist() == [1] * 5
    assert geo.is_top.dtype == bool and geo.is_top.shape == (geo.n,)
    assert np.array_equal(np.flatnonzero(geo.is_top), geo.top_nodes)
    assert geo.walk_n[geo.top_nodes].all() and (geo.node_z[geo.top_nodes] == 1.2).all()
    assert geo.node_area[geo.top_nodes].tolist() == [1, .25, .25, .25, .25]
    assert geo.node_cell[geo.top_nodes].tolist() == [PARENT + 1] + [PARENT] * 4
    assert np.array_equal(geo.node_space[geo.top_nodes], geo.top_leaf)
    assert not np.isin(geo.space_nodes, geo.top_nodes).any()
    assert not np.isin(geo.node_of, geo.top_nodes).any()
    assert np.array_equal(geo.space_nodes, ground.space_nodes)
    assert np.array_equal(geo.node_of, ground.node_of)
    for n in geo.top_nodes:
        x, y = geo.centres[n]
        assert geo.node_at_px(x, y, 20) == ground.node_at_px(x, y, 20)
        assert not geo.is_top[geo.node_at(geo.node_cell[n], 20)]


@pytest.mark.parametrize('rows,cover', [(None, True), ((), True), (((202, 202, 12),), False)])
def test_without_learnt_tops_or_cover_the_ground_is_identical(rows, cover):
    geo, before = topped(rows, cover=cover), mixed()
    assert geo.n == before.n and not geo.is_top.any()
    assert all(getattr(geo, name).size == 0 for name in ('top_nodes', 'top_leaf', 'top_piece'))
    assert np.array_equal(geo.walk_n, before.walk_n)
    assert np.array_equal(geo.node_z, before.node_z, equal_nan=True)
    assert cg.cache_key(geo) == old_key(geo)


def test_two_tops_choose_the_row_nearest_the_leaf_centre_deterministically():
    rows = [(202, 202, 12), (214, 206, 24)]
    geo = topped(rows)
    reverse = topped(rows[::-1])
    for n in geo.top_nodes:
        x, y = geo.centres[n]
        row = min(rows, key=lambda r: ((r[0]-x)**2 + (r[1]-y)**2, r[2], r[1], r[0]))
        assert geo.node_z[n] == row[2] / 10
    assert np.array_equal(geo.node_z[geo.top_nodes], reverse.node_z[reverse.top_nodes])
    # Row order itself is part of the asset's existing digest; isolate the new node contribution.
    reverse.height_sha = geo.height_sha
    assert cg.cache_key(geo) == cg.cache_key(reverse)
    assert cg.cache_key(geo) != old_key(geo)
    changed = copy.copy(geo)
    changed.node_z = geo.node_z.copy(); changed.node_z[geo.top_nodes[0]] += .1
    assert cg.cache_key(changed) != cg.cache_key(geo)
    changed.node_z = geo.node_z
    changed.top_piece = geo.top_piece + 1
    assert cg.cache_key(changed) != cg.cache_key(geo)


def test_a_box_leaf_with_no_ground_keeps_its_disabled_ground_lookup():
    geo, asset = covered()
    asset.box_tops = np.array([[202, 202, 12]], np.int32)
    cg.attach_heights(geo, asset)
    assert len(geo.top_nodes) == 1
    node = geo.node_at_px(202, 202, 1.2)
    assert not geo.walk_n[node] and not geo.is_top[node]
    assert geo.walk_n[geo.top_nodes[0]]


def test_exactly_half_cover_does_not_get_a_top():
    geo = mixed()
    pixels = np.zeros((1024, 1024), bool); pixels[200:204, 200:202] = True
    cg.attach_cover(geo, pixels, 'half-cover')
    geo.heights.box_tops = np.array([[200, 202, 12]], np.int32)
    cg.attach_heights(geo, geo.heights)
    assert not geo.top_nodes.size


@pytest.mark.parametrize('has_tops', [False, True])
def test_coarse_assets_ignore_tops_and_report_the_reason_only_when_present(has_tops):
    geo = mixed()
    asset = copy.deepcopy(geo.heights)
    asset.refinement = None
    # A real asset row's exact shape, with no account or replay identity.
    asset.box_tops = np.array([[351, 310, 40]], np.int32) if has_tops else None
    cg.attach_heights(geo, asset)
    assert not geo.adaptive and not geo.is_top.any() and len(geo.is_top) == geo.n
    assert all(getattr(geo, name).size == 0 for name in ('top_nodes', 'top_leaf', 'top_piece'))
    rnd = ce.RoundInputs(height_blob({}, t_end=1), geo)
    notes = [note for note in rnd.missing if 'tops' in note and 'refinement' in note]
    assert bool(notes) == has_tops
    assert cg.cache_key(geo) == old_key(geo)


@pytest.mark.parametrize('kind', ['hearing', 'mask', 'radius'])
def test_utility_footprints_never_include_a_top(kind):
    geo = topped()
    if kind == 'hearing':
        mask = ut.outside_hearing(SimpleNamespace(geo=geo, team={}), 'A', 0, 1)
    else:
        info = ut.Info(0, 'A', 5, 'restrict', 'toy', x=204, y=204, radius_m=100,
                       mask=np.ones(geo.n, bool) if kind == 'mask' else None)
        mask = ut.footprint(info, geo)
    assert not mask[geo.top_nodes].any()
    assert mask[geo.walk_n & ~geo.is_top].all()


def test_device_area_watcher_passes_only_ground_to_its_walk(monkeypatch):
    geo = topped()
    # W8b will connect tops: make every candidate reachable here so the mask is observable now.
    def dilate(start, **kw):
        return kw['within'].copy()
    monkeypatch.setattr(ce.topology, 'of', lambda g: SimpleNamespace(dilate=dilate))
    monkeypatch.setattr(ce, 'cast', lambda *a, **kw: np.ones(geo.n, bool))
    rnd = ce.RoundInputs.__new__(ce.RoundInputs)
    rnd.geo, rnd.watchers, rnd.missing = geo, [], Counter()
    rnd._add_watcher({'by': 0, 'u': 204/1024*10000, 'v': 204/1024*10000, 'z': 0},
                     next(iter(ce.AREA_TRIPS)), 0, 1)
    assert len(rnd.watchers) == 1 and len(rnd.watchers[0].cells) > 0
    assert not np.isin(rnd.watchers[0].cells, geo.top_nodes).any()


def test_narrow_projects_the_ground_label_without_top_overwrites():
    geo = topped()
    unknown = ce.Unknown.__new__(ce.Unknown); unknown.geo = geo
    lab = np.zeros(geo.n, np.int32); lab[geo.walk_n & ~geo.is_top] = 1; lab[geo.is_top] = 2
    result = unknown._narrow(lab, np.array([0, 100, 100]))
    assert not result[1] and not result[2]


def test_rope_sides_use_only_ground_as_measured_support(monkeypatch):
    from scipy.sparse import csgraph
    geo = topped(((202, 202, 40),))
    low, high = geo.node_at_px(196, 196, 0), geo.node_at_px(220, 196, 0)
    geo.node_z[high] = 3
    original, seeds = csgraph.dijkstra, []
    def dijkstra(*args, **kw):
        seeds.extend(kw['indices'].tolist())
        return original(*args, **kw)
    monkeypatch.setattr(csgraph, 'dijkstra', dijkstra)
    rr.local_rope_cuts(geo, topology.of(geo), [SimpleNamespace(src=low, dst=high, cost_s=2)])
    assert seeds and not np.isin(seeds, geo.top_nodes).any()


def test_rope_anchor_candidates_and_component_sizes_count_ground_only(monkeypatch):
    from scipy.sparse import csgraph
    geo = topped()
    ground = np.flatnonzero(geo.walk_n & ~geo.is_top)
    labels = np.arange(geo.n) + 10
    labels[ground[:2]] = 0; labels[geo.top_nodes] = 1
    monkeypatch.setattr(csgraph, 'connected_components', lambda *a, **kw: (geo.n+10, labels))
    seeds = []
    monkeypatch.setattr(csgraph, 'breadth_first_order', lambda g, seed, **kw: seeds.append(seed) or np.array([seed]))
    assert rr.walking_exit_problems(geo, topology.of(geo), [], set()) == []
    assert seeds == [ground[0]] * 4
    # Tops sharing the winning ground component must still be excluded as anchor candidates.
    labels[geo.top_nodes] = 0
    flatnonzero, candidates = np.flatnonzero, []
    def capture(mask):
        if np.shape(mask) == (geo.n,):
            candidates.append(mask.copy())
        return flatnonzero(mask)
    monkeypatch.setattr(rr.np, 'flatnonzero', capture)
    rr.walking_exit_problems(geo, topology.of(geo), [], set())
    assert candidates and not candidates[0][geo.top_nodes].any()


def test_route_landings_and_feature_bindings_stay_on_the_ground():
    geo = topped()
    sid = ag.BASE + CHILDREN[0]
    ground = int(geo.space_nodes[sid, 0])
    placed = cf.resolve_spaces(geo, [sid], 'toy')
    assert placed.ok and placed.nodes == (ground,)
    geo.node_z[ground] = np.nan; geo.space_unresolved[sid] = True
    panel = {'kind': 'door', 'replay_key': 'ascent_garden', 'states': [{'blocks_movement': True, 'blocks_sight': True,
                         'sight_bounds': {'ref': 'all_height'}}]}
    assert cf._resolve_panel_cells(geo, [sid], 'toy', panel).nodes == (ground,)
    geo.node_z[ground] = 0; geo.space_unresolved[sid] = False
    landing = {'world_z': {'status': 'known', 'value': 0, 'unit': 'm'},
               'tolerance': {'status': 'known', 'value': .2, 'unit': 'm'}}
    node, why = rr.landing_node(geo, PARENT, landing, xy=(202, 202))
    assert not why and node == ground


def test_terrain_audit_counts_only_ground_nodes():
    geo = topped()
    result = terrain_audit.audit(geo, [{'id': 'toy', 'bbox_px': [192, 192, 224, 216]}], context_px=0)
    assert result['regions'][0]['walkable_nodes'] == int((geo.walk_n & ~geo.is_top).sum())


def top_at(geo, x, y):
    leaf = geo.space_at_px(x, y)
    return int(geo.top_nodes[geo.top_leaf == leaf][0])


@pytest.mark.parametrize('rise', [0.3, 1.0, 1.0 + 1e-6, 1.00001, 1.5])
def test_climb_limit_and_loud_drop_are_directional(rise):
    geo = topped(((202, 202, round(rise * 10)),))
    geo.node_z[geo.top_nodes] = rise
    top, ground = top_at(geo, 202, 202), int(geo.node_at_px(196, 204, 0))
    walks, loud = ag.graph(geo)
    assert (top, ground) in walks
    assert ((ground, top) in walks) == (rise <= 1.0 + 1e-6)
    assert ((top, ground) in loud) == (rise > 1.0 + 1e-6)
    assert (ground, top) not in loud
    topo = topology.of(geo)
    reached = np.full(geo.n, np.inf); reached[top] = 0
    quiet = topo.spread(reached, geo.walk_n, np.zeros(geo.n), 100, 1, [], quiet=True)
    assert bool(np.isfinite(quiet[ground])) == (rise <= 1.0 + 1e-6)
    audible = topo.spread(reached, geo.walk_n, np.zeros(geo.n), 100, 1, [])
    assert np.isfinite(audible[ground])


@pytest.mark.parametrize('rise', [0.3, 1.0, 1.00001, 1.5])
def test_a_top_below_the_ground_beside_it_climbs_up_to_it_and_the_drop_onto_it_is_judged(rise):
    # The other way round: the ground is the higher end, so the top climbs and the ground drops.
    geo = topped()
    geo.node_z[geo.top_nodes] = -rise
    top, ground = top_at(geo, 202, 202), int(geo.node_at_px(196, 204, 0))
    walks, loud = ag.graph(geo)
    assert (ground, top) in walks
    assert ((top, ground) in walks) == (rise <= 1.0 + 1e-6)
    assert ((ground, top) in loud) == (rise > 1.0 + 1e-6)
    assert (top, ground) not in loud


def test_same_piece_tops_connect_only_touching_leaves_including_corners():
    geo = topped()
    # Make reach larger than the whole toy: same-piece candidates must still touch.
    geo.m_per_px = .01
    walks, loud = ag.graph(geo)
    pairs = set()
    for a in geo.top_nodes:
        for b in geo.top_nodes:
            if a != b and np.all(np.minimum(geo.node_bbox[a, 2:], geo.node_bbox[b, 2:]) >=
                                 np.maximum(geo.node_bbox[a, :2], geo.node_bbox[b, :2])):
                pairs.add((int(a), int(b)))
    actual = {(a, b) for a, b in walks if geo.is_top[a] and geo.is_top[b]}
    assert actual == pairs and pairs
    assert not actual & loud
    diagonal = (top_at(geo, 202, 202), top_at(geo, 206, 206))
    assert diagonal in actual and diagonal[::-1] in actual
    far = (top_at(geo, 202, 202), top_at(geo, 212, 204))
    assert far not in actual and far[::-1] not in actual


def test_climbs_are_local_to_the_top_leaf_not_the_whole_piece():
    geo = topped(((202, 202, 10),))
    ground = int(geo.node_at_px(220, 204, 0))
    near, far = top_at(geo, 212, 204), top_at(geo, 202, 202)
    walks, _ = ag.graph(geo)
    assert (ground, near) in walks and (near, ground) in walks
    assert (ground, far) not in walks and (far, ground) not in walks


@pytest.mark.parametrize('offset,connected', [(0.0, True), (-1e-5, False), (-5e-7, True)])
def test_rectangle_reach_uses_euclidean_gap_and_pixel_tolerance(offset, connected):
    geo = topped(((202, 202, 10),))
    top = top_at(geo, 202, 202)
    ground = int(geo.node_at_px(220, 212, 0))
    # Rectangles are [200,200,204,204] and [216,208,224,216]: gap hypot(12,4).
    gap = np.hypot(12, 4)
    geo.m_per_px = hc.TOP_REACH_M / (gap + offset)
    walks, _ = ag.graph(geo)
    assert ((top, ground) in walks) == connected
    assert ((ground, top) in walks) == connected


def test_a_permanent_wall_between_a_top_and_the_ground_beside_it_stops_the_climb():
    geo = topped(((202, 202, 10),))
    top, ground = top_at(geo, 202, 202), int(geo.node_at_px(196, 204, 0))
    walks, _ = ag.graph(geo)
    assert (top, ground) in walks and (ground, top) in walks
    geo.sight = geo.sight.copy()
    geo.sight[196:212, 198] = True  # a wall one pixel thick, inside the reach
    geo.__dict__.pop('_sight_without', None)
    walks, loud = ag.graph(geo)
    assert (top, ground) not in walks and (ground, top) not in walks
    assert (top, ground) not in loud and (ground, top) not in loud


@pytest.mark.parametrize('finite', [False, True])
def test_ground_connected_only_by_unresolved_fallback_gets_no_climb(finite):
    geo = topped(((202, 202, 10),))
    leaf = ag.BASE + CHILDREN[0]
    ground = int(geo.space_nodes[leaf, 0]); top = top_at(geo, 202, 202)
    geo.heights.fine_edges = geo.heights.fine_edges[
        ~np.isin(geo.heights.fine_edges[:, (0, 2)], leaf).any(1)]
    geo.space_unresolved[leaf] = True
    geo.node_z[ground] = 0 if finite else np.nan
    walks, _ = ag.graph(geo)
    assert any(a == ground or b == ground for a, b in walks), 'fallback still connects ground'
    assert (ground, top) not in walks and (top, ground) not in walks


@pytest.mark.parametrize('finite', [True, False])
def test_fine_edge_endpoints_can_climb_only_with_finite_height(finite):
    geo = topped(((202, 202, 10),))
    ground = int(geo.node_at_px(206, 202, 0)); top = top_at(geo, 202, 202)
    leaf = int(geo.node_space[ground])
    assert np.isin(geo.heights.fine_edges[:, (0, 2)], leaf).any()
    assert not np.isin(geo.heights.edges[:, (0, 2)], leaf).any()
    if not finite:
        geo.node_z[ground] = np.nan
    walks, _ = ag.graph(geo)
    assert ((ground, top) in walks) == finite
    assert ((top, ground) in walks) == finite


def two_piece_tops():
    geo = topped(None, cover=False)
    pixels = np.zeros((1024, 1024), bool)
    pixels[200:204, 200:204] = True
    pixels[200:208, 208:216] = True
    cg.attach_cover(geo, pixels, 'two-top-pieces')
    geo.heights.box_tops = np.array([[202, 202, 10], [212, 202, 20]], np.int32)
    return cg.attach_heights(geo, geo.heights)


def test_quiet_spread_reaches_a_high_piece_through_a_low_piece():
    geo = two_piece_tops()
    low, high = top_at(geo, 202, 202), top_at(geo, 212, 204)
    assert geo.top_piece[geo.top_nodes == low] != geo.top_piece[geo.top_nodes == high]
    ground = int(geo.node_at_px(196, 204, 0))
    walks, loud = ag.graph(geo)
    assert (ground, high) not in walks
    assert (ground, low) in walks and (low, high) in walks
    assert (low, high) not in loud and (high, low) not in loud
    reached = np.full(geo.n, np.inf); reached[ground] = 0
    arrivals, parents = topology.of(geo).spread(
        reached, geo.walk_n, np.zeros(geo.n), 100, 1, [], quiet=True, parents=True)
    assert np.isfinite(arrivals[high]) and parents[high] == low
    assert parents[low] == ground
    geo.m_per_px = 1.0  # the 4 px gap now exceeds TOP_REACH_M
    walks, _ = ag.graph(geo)
    assert (low, high) not in walks and (high, low) not in walks


def ground_graph_before_climbs(geo):
    """Frozen pre-W8b graph, including fallback and the corner check."""
    walks, loud = set(), set()
    for edges in (geo.heights.edges, geo.heights.fine_edges):
        for a, fa, b, fb, kind in edges:
            na, nb = int(geo.space_nodes[a, fa]), int(geo.space_nodes[b, fb])
            if na < 0 or nb < 0 or not geo.space_walk[a] or not geo.space_walk[b]:
                continue
            walks.add((na, nb))
            if kind == hc.EDGE_FALL and geo.node_z[na] - geo.node_z[nb] > hc.SILENT_DROP_M:
                loud.add((na, nb))
    for a, b in ag.neighbours(geo):
        if not geo.space_unresolved[a] and not geo.space_unresolved[b]:
            continue
        for na in geo.space_nodes[a]:
            for nb in geo.space_nodes[b]:
                if na >= 0 and nb >= 0:
                    walks.update(((int(na), int(nb)), (int(nb), int(na))))
    for na, nb in list(walks):
        sides = ag.corner_sides(geo, int(geo.node_space[na]), int(geo.node_space[nb]))
        if sides is None:
            continue
        if any(s < 0 or not geo.space_walk[s] or not any(n >= 0 and (na, int(n)) in walks and (int(n), nb) in walks
                for n in geo.space_nodes[s]) for s in sides):
            walks.discard((na, nb)); loud.discard((na, nb))
    return walks, loud


@pytest.mark.parametrize('unresolved', [False, True])
def test_no_tops_preserves_the_pre_climb_graph_exactly(unresolved):
    geo = topped(None)
    if unresolved:
        geo.space_unresolved[ag.BASE + CHILDREN[0]] = True
        geo.node_z[geo.space_nodes[ag.BASE + CHILDREN[0], 0]] = np.nan
    assert not geo.top_nodes.size
    assert ag.graph(geo) == ground_graph_before_climbs(geo)


def test_top_edges_leave_the_ground_graph_unchanged():
    geo, ground = topped(), topped(None)
    walks, loud = ag.graph(geo)
    ground_walks = {(a, b) for a, b in walks if not geo.is_top[a] and not geo.is_top[b]}
    ground_loud = {(a, b) for a, b in loud if not geo.is_top[a] and not geo.is_top[b]}
    assert (ground_walks, ground_loud) == ag.graph(ground)


# W9a: top sight is an exact-line addition to the existing ground fan.
def sight_topped(rows=((202, 202, 12),), *, cover=True):
    """The graph toy with its cover also painted into the sight mask, as on deployed maps."""
    geo = topped(rows, cover=cover)
    if geo.cover_px is not None:
        geo.sight |= geo.cover_px > 0
    return geo


def sight_from(geo, x, y, angles, smokes=(), *, own=None, see_over=()):
    if own is None:
        own = int(geo.node_at_px(x, y, 0))
    return cg.cast(geo, x, y, np.asarray(angles), list(smokes),
                   eye_z=geo.node_z[own] + hc.EYE_M, own=own, see_over=see_over)


@pytest.mark.parametrize('rise,x,visible', [(10, 196, True), (40, 199, False)])
def test_top_sight_low_box_at_distance_and_tall_box_pressed_viewer(rise, x, visible):
    geo = sight_topped(((202, 202, rise),))
    # 16 px to the coarse top centre is about 5 m; 1 px to the tall box's edge is pressed against it.
    geo.m_per_px = .3
    top = top_at(geo, 212, 204)
    seen = sight_from(geo, x, 204, [0.0])
    assert bool(seen[top]) == visible


def test_top_sight_tall_box_centre_is_seen_from_far_enough_back():
    geo = sight_topped(((202, 202, 40),))
    geo.m_per_px = .3
    # From the west corner, the rising line reaches the box above its surface.
    x, y = 192.0, 196.0
    top = top_at(geo, 212, 204)
    angle = np.degrees(np.arctan2(204-y, 212-x))
    assert sight_from(geo, x, y, [angle])[top]


def test_top_sight_surface_helper_is_required_to_hide_the_tall_box(monkeypatch):
    geo = sight_topped(((202, 202, 40),))
    top = top_at(geo, 212, 204)
    assert not sight_from(geo, 199, 204, [0.0])[top]
    monkeypatch.setattr(cg, '_over_own_top', lambda *a, **kw: True)
    assert sight_from(geo, 199, 204, [0.0])[top]


def test_top_sight_surface_check_judges_head_and_feet_separately(monkeypatch):
    geo = sight_topped(((202, 202, 10),))
    target = top_at(geo, 212, 204)
    ends = []
    monkeypatch.setattr(cg, 'los', lambda *a, **kw: True)
    def surface(geo, a, b, t, piece):
        if t == target:
            ends.append(b[2])
        return b[2] == geo.node_z[t] + hc.FEET_M
    monkeypatch.setattr(cg, '_over_own_top', surface, raising=False)
    assert sight_from(geo, 196, 204, [0.0])[target]
    assert ends == [1 + hc.HEAD_M, 1 + hc.FEET_M]


@pytest.mark.parametrize('angle,visible', [(360.0, True), (-.25, True), (.250001, False), (180, False)])
def test_top_sight_fan_bearing_wrap_and_half_step(angle, visible):
    geo = sight_topped(((202, 202, 10),))
    assert bool(sight_from(geo, 196, 204, [angle])[top_at(geo, 212, 204)]) == visible


def test_top_sight_smoke_on_the_exact_line_hides_the_top():
    geo = sight_topped(((202, 202, 10),))
    top = top_at(geo, 212, 204)
    assert sight_from(geo, 196, 204, [0.0])[top]
    assert not sight_from(geo, 196, 204, [0.0], [(198, 204, 1.5, True)])[top]


@pytest.mark.parametrize('kind', ['no_rows', 'no_cover', 'flat', '2d'])
def test_top_sight_pass_never_runs_outside_the_height_top_path(monkeypatch, kind):
    geo = (sight_topped(None) if kind == 'no_rows' else sight_topped(cover=False) if kind == 'no_cover'
           else sight_topped())
    if kind == 'flat':
        geo = cg.geometry_from_masks('flat', geo.sight, geo.walk_px, 7e-5)
    angles = np.arange(0, 360, cg.RAY_STEP_DEG)
    kw = {} if kind in ('flat', '2d') else {'eye_z': hc.EYE_M}
    before = cg.cast(geo, 196, 204, angles, [], **kw)
    def forbidden(*a, **kw):
        raise AssertionError('top pass must not run')
    monkeypatch.setattr(cg, '_see_tops', forbidden, raising=False)
    assert np.array_equal(before, cg.cast(geo, 196, 204, angles, [], **kw))


def test_top_sight_keeps_every_ground_fan_bit(monkeypatch):
    geo = sight_topped(((202, 202, 10),))
    angles = np.arange(-90, 90.01, cg.RAY_STEP_DEG)
    with_tops = sight_from(geo, 196, 204, angles)
    assert with_tops[geo.top_nodes].any()
    monkeypatch.setattr(cg, '_see_tops', lambda *a, **kw: None, raising=False)
    without = sight_from(geo, 196, 204, angles)
    assert np.array_equal(with_tops[~geo.is_top], without[~geo.is_top])
    assert not without[geo.top_nodes].any()


def test_top_sight_own_views_are_lazy_reused_and_reset_by_attachment(monkeypatch):
    geo = sight_topped(((202, 202, 10),))
    assert geo.top_view is None
    sight_from(geo, 196, 204, [0.0])
    views = geo.top_view
    assert views.dtype == bool and views.shape == (len(geo.top_nodes), geo.n)
    original = cg._cast_heights
    calls = []
    def capture(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(cg, '_cast_heights', capture)
    sight_from(geo, 196, 204, [0.0])
    assert geo.top_view is views and len(calls) == 1
    cg.attach_heights(geo, geo.heights)
    assert geo.top_view is None


def test_top_sight_visibility_rows_use_the_top_vantage_and_include_top_targets():
    geo = sight_topped(((202, 202, 10),))
    top, far = top_at(geo, 202, 202), int(geo.node_at_px(220, 204, 0))
    ground = int(geo.node_at_px(196, 204, 0))
    rows, row_of = cg.build_visibility(geo)
    assert rows.shape[1] == (geo.n + 7) // 8
    assert np.unpackbits(rows[row_of[top]])[far]
    assert np.unpackbits(rows[row_of[ground]])[top_at(geo, 212, 204)]
    row = np.unpackbits(rows[row_of[top]])[:geo.n].astype(bool)
    view = geo.top_view[np.flatnonzero(geo.top_nodes == top)[0]]
    assert np.array_equal(row[~geo.is_top], view[~geo.is_top]), "the top pass adds tops and leaves ground alone"


def test_cached_top_row_sees_the_other_top_a_live_cast_sees():
    geo = sight_topped()
    own, target = top_at(geo, 202, 202), top_at(geo, 212, 204)
    rows, row_of = cg.build_visibility(geo)
    assert np.unpackbits(rows[row_of[own]])[target]


def test_beside_a_box_on_refined_ground_anchors_on_a_live_node(tmp_path, monkeypatch):
    from tests.replays.test_stance_engine import inputs, still
    monkeypatch.setattr(cg, 'cache_dir', lambda: tmp_path)
    geo = stored_topped(partial=True)
    cg.visibility(geo)
    rnd, _ = inputs(geo, {0: still("A", 202, 202, 0, 0.0)})
    h = ce.Tick(rnd, 5.0).holders[0]
    assert h.stance.kind == "beside"
    assert geo.walk_n[h.cell] and not geo.is_top[h.cell]


def test_piece_ground_reads_refined_floors_like_the_build():
    from app.control import box_tops
    cover = np.zeros((cg.PX, cg.PX), np.int32)
    cover[200:208, 200:208] = 1
    levels = hc.MAX_FLOORS
    floors = np.full((cg.GRID, cg.GRID, levels), -1, np.int16)     # refinement removed every parent floor here
    span = range(40, 64)
    fine_cells = np.array(sorted(y * 2 * cg.GRID + x for y in span for x in span), np.int32)
    fine_floors = np.full((len(fine_cells), levels), -1, np.int16)
    fine_floors[:, 0] = 0
    geo = SimpleNamespace(cover_px=cover, height_sha=None, cover_sha=None,
                          heights=SimpleNamespace(floors=floors, fine_cells=fine_cells, fine_floors=fine_floors))
    assert dict(box_tops.piece_ground(geo)) == {1: 0.0}


def test_top_sight_on_top_viewer_sees_another_top_on_its_piece():
    from app.control import stance
    geo = sight_topped()
    own, target = top_at(geo, 202, 202), top_at(geo, 212, 204)
    x, y = geo.centres[own]
    tx, ty = geo.centres[target]
    angles = [np.degrees(np.arctan2(ty-y, tx-x))]
    over = stance.vantage(geo, 1, geo.node_z[own])
    assert sight_from(geo, x, y, angles, own=own, see_over=over)[target]


def test_top_sight_cache_key_versions_only_maps_with_tops(monkeypatch):
    geo = topped()
    dumps = json.dumps
    def without_top_sight(value, **kw):
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != 'top_sight'}
        return dumps(value, **kw)
    current = cg.cache_key(geo)
    monkeypatch.setattr(cg.json, 'dumps', without_top_sight)
    assert cg.cache_key(geo) != current


def test_top_sight_bounded_occluder_uses_the_top_head_and_feet(monkeypatch):
    geo = sight_topped(((202, 202, 10),))
    top = top_at(geo, 212, 204)
    heads = []
    def blocked(occluders, a, targets, record, **kw):
        heads.append(targets.copy())
        return np.zeros(len(targets), bool)
    monkeypatch.setattr(cf, 'blocked_lines', blocked)
    seen = cf.cast_with(geo, 196, 204, np.array([0.0]), [], [object()], eye_z=hc.EYE_M)
    assert seen[top]
    assert any(np.any(np.all(batch == [212, 204, 1 + hc.HEAD_M], axis=1)) for batch in heads)


def stored_topped(rows=((202, 202, 12),), *, partial=False, name='Split'):
    """Permanent masks exclude the box, as on real maps; optionally cover only one fine leaf."""
    source = topped(rows, name=name)
    cover = source.cover_px > 0
    if partial:
        cover[204:208, 200:208] = False
        cover[200:204, 204:208] = False
    walk = source.walk_px & ~cover
    geo = cg.geometry_from_masks(source.name, ~walk, walk, source.uv_per_unit / 10000)
    cg.attach_cover(geo, cover, 'stored-top-toy')
    asset = copy.deepcopy(source.heights)
    asset.meta['origin_z'] = 37  # control_toys' world-height origin
    cg.attach_heights(geo, asset)
    # Archive the filtered toy asset, rather than its input's discarded cover floors.
    geo.heights.__dict__.pop('digest_pinned', None)
    return cg.attach_heights(geo, geo.heights)


@pytest.mark.parametrize('partial', [False, True])
def test_round_stores_top_parent_state_and_all_masks(tmp_path, monkeypatch, partial):
    from tests.replays.control_toys import on_box_track
    monkeypatch.setattr(cg, 'cache_dir', lambda: tmp_path)
    geo = stored_topped(partial=partial)
    before = geo.walk.copy()
    blob = height_blob({0: on_box_track(202, 202, rise_m=1.2, t_end=8)}, t_end=8)
    blob['map'] = geo.name
    result = ce.compute_round(blob, geo, ticks=np.array([2.]))
    expected = before.ravel().copy()
    expected[geo.node_cell[geo.top_nodes]] = True
    assert np.array_equal(result.walk_cells, np.flatnonzero(expected))
    assert np.array_equal(geo.walk, before), 'stored columns must not mutate permanent walkability'
    for parent in np.unique(geo.node_cell[geo.top_nodes]):
        col = int(np.flatnonzero(result.walk_cells == parent)[0])
        assert result.states[0, col] != ce.NONE
    own_col = int(np.flatnonzero(result.walk_cells == PARENT)[0])
    assert result.coverage_masks[0, 0, own_col]
    assert result.control_masks[0, 0, own_col]
    assert result.knew_states['A'][0, own_col] != ce.NONE
    assert all(mask.shape == result.states.shape for mask in result.unknown.values())


@pytest.mark.parametrize('partial', [False, True])
def test_round_top_seconds_use_exact_parent_columns_and_leaf_areas(tmp_path, monkeypatch, partial):
    from tests.replays.control_toys import on_box_track
    monkeypatch.setattr(cg, 'cache_dir', lambda: tmp_path)
    geo = stored_topped(partial=partial)
    blob = height_blob({0: on_box_track(202, 202, rise_m=1.2, t_end=8)}, t_end=8)
    blob['map'] = geo.name
    states = []
    def observe(tick, unknown):
        assert tick.holders[0].stance.kind == 'on_box'
        assert geo.is_top[tick.holders[0].cell]
        states.append(tick.compose()['state'].copy())
    result = ce.compute_round(blob, geo, ticks=np.array([2.]), knowledge=False, observer=SimpleNamespace(on_tick=observe))
    assert len(states) == 1
    # This toy has one walking node per leaf (ground OR top), with no overlapping floors.
    nodes = np.flatnonzero(geo.walk_n)
    assert len(np.unique(geo.node_space[nodes])) == len(nodes)
    for section in result.sections:
        expected = np.zeros_like(section.totals)
        for node in nodes:
            col = int(np.flatnonzero(result.walk_cells == geo.node_cell[node])[0])
            expected[states[0][node], col] += section.seconds * geo.node_area[node]
        np.testing.assert_allclose(section.totals, expected, atol=1e-6)
        for parent in np.unique(geo.node_cell[geo.top_nodes]):
            col = int(np.flatnonzero(result.walk_cells == parent)[0])
            assert section.totals[1:, col].sum() > 0
            assert section.totals[:, col].sum() == pytest.approx(section.seconds)
        if partial:
            top = top_at(geo, 202, 202)
            assert geo.node_area[top] == .25
            assert not geo.space_walk[geo.top_leaf[geo.top_nodes == top][0]]


@pytest.mark.parametrize('rows,cover', [(None, True), ((), True), (((202, 202, 12),), False)])
def test_round_without_tops_is_byte_identical_when_top_step_is_bypassed(tmp_path, monkeypatch, rows, cover):
    from tests.replays.control_toys import standing
    monkeypatch.setattr(cg, 'cache_dir', lambda: tmp_path)
    geo = topped(rows, cover=cover)
    blob = height_blob({0: standing(196, 204, 0)}, t_end=8)
    blob['map'] = geo.name
    first = ce.compute_round(blob, geo, ticks=np.array([2., 4.]), knowledge=False)
    # Bypass only the storage step's optional top lookup; sight still uses the real empty array.
    import builtins
    def without_tops(obj, name, *default):
        return None if obj is geo and name == 'top_nodes' else builtins.getattr(obj, name, *default)
    monkeypatch.setattr(ce, 'getattr', without_tops, raising=False)
    second = ce.compute_round(blob, geo, ticks=np.array([2., 4.]), knowledge=False)
    for left, right in [(first.walk_cells, second.walk_cells), (first.states, second.states),
                        *[(a.totals, b.totals) for a, b in zip(first.sections, second.sections)]]:
        assert left.dtype == right.dtype and left.shape == right.shape
        assert left.tobytes() == right.tobytes()


def test_top_and_enemy_ground_merge_into_a_contested_parent_picture():
    geo = topped()
    top = top_at(geo, 202, 202)
    ground = int(geo.node_at_px(206, 206, 0))
    assert geo.walk_n[ground] and not geo.is_top[ground]
    assert geo.node_cell[top] == geo.node_cell[ground] == PARENT
    state = np.zeros(geo.n, np.uint8)
    state[top], state[ground] = ce.A_PASSIVE, ce.B_PASSIVE
    assert ce.collapse_states(geo, state)[PARENT] == ce.CONTESTED
    state[top] = ce.A_ACTIVE
    assert ce.collapse_states(geo, state)[PARENT] == ce.CONTESTED_ACTIVE

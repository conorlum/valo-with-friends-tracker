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


def topped(rows=((202, 202, 12),), *, cover=True):
    geo = mixed()
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


def test_topology_has_room_for_tops_but_no_edges_to_them_yet():
    geo = topped(); topo = topology.of(geo)
    assert len(geo.top_nodes) > 0 and topo.n == geo.n
    assert topo.in_from.shape[0] == geo.n
    assert not np.isin(topo.src, geo.top_nodes).any() and not np.isin(topo.dst, geo.top_nodes).any()
    for node in geo.top_nodes:
        assert len(topo.around(int(node))) == 0
        assert (topo.in_from[node] == geo.n).all()

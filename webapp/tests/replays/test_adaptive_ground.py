"""Mixed footprints must preserve physical reach, area, and exact floor identity."""
import copy

import numpy as np
import pytest

from app.control import adaptive as ag, geometry as cg, heights as hc, height_refinement as hr
from app.control import engine as ce, features as cf, map_pool_runtime as pool, topology
from app.control.route_runtime import landing_node
from app.replays import height_inputs as hi, map_feature_schema as ms


PARENT = 25 * 128 + 25
CHILDREN = hr._child_ids(PARENT)


def mixed(levels=(0, 0, 0, 0), *, name='Split'):
    pixels = np.zeros((1024, 1024), bool); pixels[192:216, 192:224] = True
    geo = cg.geometry_from_masks(name, ~pixels, pixels, 7e-5)
    floors = np.full((128, 128, 3), -1, np.int16); floors[geo.walk, 0] = 0
    supported = geo.walk.copy(); supported.ravel()[PARENT] = False
    floors.reshape(-1, 3)[PARENT] = -1
    edges = []
    for p in np.flatnonzero(geo.walk):
        for q in np.flatnonzero(geo.walk):
            if p != PARENT and q != PARENT and p != q and max(abs(p//128-q//128), abs(p%128-q%128)) == 1:
                edges.append((p, 0, q, 0, hc.EDGE_STEP))
    history = hr.history(name, [PARENT])
    receipt = dict(v=1, history=history, history_sha256=hr.identity(history),
                   previous_history_sha256=hr.identity(hr.history(name)), evidence_sha256='a'*64)
    fine = np.full((4, 3), -1, np.int16)
    for i, z in enumerate(levels):
        if z is not None:
            fine[i, 0] = z
    asset = hc.HeightAsset(floors, np.zeros_like(floors), supported, np.zeros_like(supported), edges,
        {'version': 3, 'units': 'dm', 'stand_m': hc.STAND_M, 'origin_z': 0}, refinement=receipt,
        fine_cells=np.array(CHILDREN, np.int32), fine_floors=fine, fine_spread=np.zeros_like(fine),
        fine_supported=fine[:, 0]>=0, fine_unresolved=fine[:, 0]<0,
        fine_kind=np.where(fine[:, 0]>=0, hc.KIND_STANDS, hc.KIND_NONE).astype(np.int8),
        fine_edges=np.empty((0, 5), np.int32))
    cg.attach_heights(geo, asset)
    rows = []
    for a, b in ag.neighbours(geo):
        if a < ag.BASE and b < ag.BASE:
            continue
        na, nb = geo.space_nodes[a, 0], geo.space_nodes[b, 0]
        if np.isfinite(geo.node_z[na]) and np.isfinite(geo.node_z[nb]) and abs(geo.node_z[na]-geo.node_z[nb]) <= hc.STEP_UP_M:
            rows += [(a, 0, b, 0, hc.EDGE_STEP), (b, 0, a, 0, hc.EDGE_STEP)]
    asset.fine_edges = np.array(rows, np.int32).reshape(-1, 5)
    return cg.attach_heights(geo, asset)


def test_exact_xy_and_area_preserve_parent_size():
    geo = mixed((0, 6, None, 0))
    left, right = geo.node_at_px(202, 202, 0), geo.node_at_px(206, 202, .6)
    assert left != right and geo.node_z[right] == .6
    assert not geo.unresolved_at_px(202, 202) and geo.unresolved_at_px(202, 206)
    assert geo.height_unknown == 0  # Removed parent floors and declared sparse children are intentional.
    children = geo.to_nodes(np.arange(16384) == PARENT) & geo.walk_n
    assert geo.area(children) == 1 and not geo.walk_n[PARENT]
    p = cf._place_shape(geo, {'type': 'point', 'uv': [1972.65625, 1972.65625]}, 'point')
    assert p.nodes == (left,) and p.ok


def test_refinement_does_not_double_walking_speed_or_area():
    geo = mixed(); topo = topology.of(geo)
    start = geo.node_at_px(196, 196, 0)
    target = geo.node_at_px(206, 202, 0)
    dist = topo.dist(start)
    assert dist[target] > 1
    mask = np.zeros(geo.n, bool); mask[start] = True
    assert not topo.dilate(mask, eight=True)[target]
    assert topo.dilate(mask, eight=True, iterations=2)[target]
    room = geo.walk_n
    arrivals = np.full(geo.n, np.inf); arrivals[start] = 0
    grown = topo.spread(arrivals, room, np.zeros(geo.n), 100, 1, [])
    assert grown[target] == pytest.approx(dist[target])
    src, dst, metres = cf.walk_edges(geo)
    k = np.flatnonzero((src == geo.node_at_px(202, 202, 0)) & (dst == target))[0]
    assert metres[k] == pytest.approx(geo.cell_m / 2)


def test_height_domain_warning_counts_undeclared_leaf_holes_only():
    geo = mixed((0, 0, None, 0))
    assert geo.height_unknown == 0
    changed = copy.deepcopy(geo.heights)
    changed.fine_unresolved[2] = False
    assert cg.attach_heights(geo, changed).height_unknown == 1


def test_ledge_is_not_a_diagonal_shortcut_and_rope_landing_uses_exact_leaf():
    geo = mixed((0, 30, 0, 30)); topo = topology.of(geo)
    a, b = geo.node_at_px(202, 202, 0), geo.node_at_px(206, 202, 3)
    assert b not in topo.around(a)
    landing = {'world_z': ms.known(0, 'm'), 'tolerance': ms.known(.2, 'm')}
    assert landing_node(geo, PARENT, landing, xy=(202, 202)) == (a, None)
    assert landing_node(geo, PARENT, landing, xy=(206, 202))[0] is None


def test_moving_panel_majority_is_measured_per_leaf():
    geo = mixed()
    # Covers the left children's full width and the right children's exact half.
    panel = {'type': 'polygon', 'uv': [[v*10000/1024 for v in xy] for xy in
        [(200, 200), (206, 200), (206, 208), (200, 208)]]}
    effects = pool.rotation_effects(geo, {}, {'id': 'door'}, panel)
    assert not effects.pending
    assert effects.blocked[geo.node_at_px(202, 202, 0)]
    assert not effects.blocked[geo.node_at_px(206, 202, 0)]
    assert geo.area(effects.blocked) == .5


def test_collapsed_picture_ignores_disabled_parent_but_section_area_is_physical():
    geo = mixed(); state = np.full(geo.n, ce.NONE, np.uint8)
    state[geo.walk_n] = ce.A_ACTIVE
    assert ce.collapse_states(geo, state)[PARENT] == ce.A_ACTIVE
    state[geo.node_at_px(206, 202, 0)] = ce.B_ACTIVE
    assert ce.collapse_states(geo, state)[PARENT] == ce.CONTESTED_ACTIVE
    assert geo.area(state == ce.B_ACTIVE) == .25


def test_archive_digest_history_and_replacement(tmp_path):
    geo = mixed(); original = geo.heights
    path = tmp_path / 'mixed.npz'; hc.save_asset(path, original)
    restored = hc.load_asset(path)
    assert restored.digest == original.digest
    assert np.array_equal(cg.attach_heights(copy.copy(geo), restored).space_grid, geo.space_grid)
    changed = copy.deepcopy(original); changed.fine_floors[0, 0] += 1
    assert changed.digest != original.digest
    changed = copy.deepcopy(original); changed.floors.reshape(-1, 3)[PARENT, 0] = 0
    with pytest.raises(ValueError, match='underneath'):
        cg.attach_heights(copy.copy(geo), changed)
    old = copy.deepcopy(original); old.meta['version'] = 2; old.refinement = None
    hc.save_asset(tmp_path/'old.npz', old)
    assert hc.load_asset(tmp_path/'old.npz').digest == old.digest
    assert not cg.attach_heights(copy.copy(geo), old).adaptive


def test_durable_selection_is_a_manifest_input_and_invalid_history_fails():
    history = hr.history('Split', [PARENT])
    a = hi.manifest('Split', [], {}, 'check', history=history)
    b = hi.manifest('Split', [], {}, 'check')
    assert hi.digest(a) != hi.digest(b)
    with pytest.raises(ValueError, match='history'):
        hi.manifest('Split', [], {}, 'check', history=hr.history('Lotus', [PARENT]))


def test_sight_and_visibility_cache_use_child_nodes(tmp_path):
    geo = mixed((0, 0, None, 0))
    own = geo.node_at_px(202, 202, 0)
    seen = cg.cast(geo, 202, 202, np.arange(0, 360, 1), [], eye_z=hc.EYE_M, own=own)
    assert seen.shape == (geo.n,) and seen[own] and not seen[PARENT]
    cg.visibility(geo, tmp_path)
    assert geo.rows.shape[0] == geo.walk_n.sum()


def test_real_engine_accumulates_multiple_sections_and_preserves_parent_area():
    from tests.replays.control_toys import fast_blob, standing
    geo = mixed()
    blob = fast_blob({0: standing(202, 202, 0), 5: standing(214, 210, 0, 'B')}, t_end=45)
    blob['map'] = geo.name
    result = ce.compute_round(blob, geo, ticks=np.array([0., 19., 31.]), knowledge=False, full_every=1)
    assert len(result.sections) >= 2
    assert result.cf_check['player_ticks'] > 0
    assert result.cf_check['identical'] == result.cf_check['player_ticks']
    col = int(np.flatnonzero(result.walk_cells == PARENT)[0])
    for section in result.sections:
        assert section.totals[:, col].sum() == pytest.approx(section.seconds)


def test_bundle_candidate_keeps_fine_spatial_identity():
    geo = mixed()
    candidate = cf._candidate_geometry(geo, {'features': []}, set())
    assert candidate.adaptive and candidate.n == geo.n
    assert candidate.walk_n.shape == (geo.n,)
    assert np.array_equal(candidate.node_space, geo.node_space)

"""A fine floor on top of see-over cover (a box) is dropped from an adaptive asset, not a reason to refuse the map."""
import numpy as np
import pytest

from app.control import adaptive as ag, geometry as cg, heights as hc, height_refinement as hr

PARENT = 25 * 128 + 25
CHILDREN = hr._child_ids(PARENT)
BOX = CHILDREN[0]                      # the child that stands on a box
BOX_Y, BOX_X = divmod(BOX, 256)


def masks(hole=True):
    pixels = np.zeros((1024, 1024), bool); pixels[192:216, 192:224] = True
    if hole:                           # the box: its whole 4x4 fine cell is unwalkable
        pixels[BOX_Y * 4:BOX_Y * 4 + 4, BOX_X * 4:BOX_X * 4 + 4] = False
    return pixels


def cover_mask(extra=False):
    cover = np.zeros((1024, 1024), bool)
    cover[BOX_Y * 4:BOX_Y * 4 + 4, BOX_X * 4:BOX_X * 4 + 4] = True
    return cover


def geo_of(pixels):
    return cg.geometry_from_masks('Split', ~pixels, pixels, 7e-5)


def asset_for(geo):
    floors = np.full((128, 128, 3), -1, np.int16); floors[geo.walk, 0] = 0
    supported = geo.walk.copy(); supported.ravel()[PARENT] = False
    floors.reshape(-1, 3)[PARENT] = -1
    edges = []
    for p in np.flatnonzero(geo.walk):
        for q in np.flatnonzero(geo.walk):
            if p != PARENT and q != PARENT and p != q and max(abs(p//128-q//128), abs(p%128-q%128)) == 1:
                edges.append((p, 0, q, 0, hc.EDGE_STEP))
    history = hr.history('Split', [PARENT])
    receipt = dict(v=1, history=history, history_sha256=hr.identity(history),
                   previous_history_sha256=hr.identity(hr.history('Split')), evidence_sha256='a'*64)
    fine = np.full((4, 3), -1, np.int16); fine[:, 0] = 0          # every child has a floor, the box's too
    ring = PARENT - 1                                              # a walkable neighbour of the parent
    base = ag.BASE
    fine_edges = [(base + BOX, 0, base + CHILDREN[1], 0, hc.EDGE_STEP),
                  (base + CHILDREN[1], 0, base + BOX, 0, hc.EDGE_STEP),
                  (base + BOX, 0, ring, 0, hc.EDGE_STEP),
                  (ring, 0, base + BOX, 0, hc.EDGE_STEP),
                  (base + CHILDREN[2], 0, base + CHILDREN[3], 0, hc.EDGE_STEP)]
    return hc.HeightAsset(floors, np.zeros_like(floors), supported, np.zeros_like(supported), edges,
        {'version': 3, 'units': 'dm', 'stand_m': hc.STAND_M, 'origin_z': 0}, refinement=receipt,
        fine_cells=np.array(CHILDREN, np.int32), fine_floors=fine, fine_spread=np.zeros_like(fine),
        fine_supported=fine[:, 0] >= 0, fine_unresolved=np.zeros(4, bool),
        fine_kind=np.full(4, hc.KIND_STANDS, np.int8), fine_edges=np.array(fine_edges, np.int32))


def flat_asset_geo():
    """The same toy without the box: the asset to attach, built where every child is walkable."""
    return asset_for(geo_of(masks(hole=False)))


def covered(asset=None):
    geo = geo_of(masks()); cg.attach_cover(geo, cover_mask(), 'x')
    return geo, asset if asset is not None else flat_asset_geo()


def snapshot(asset):
    return [np.array(getattr(asset, n), copy=True) for n in ('fine_floors', 'fine_edges', 'fine_supported')]


def test_box_top_floor_is_dropped_with_its_edges_and_the_asset_is_untouched():
    geo, asset = covered(); before = snapshot(asset)
    assert len(asset.fine_edges) == 5
    cg.attach_heights(geo, asset)
    sid = ag.BASE + BOX
    assert geo.height_cover_dropped == 1
    assert not geo.space_walk[sid] and geo.space_nodes[sid, 0] >= 0      # one node, none walkable
    assert np.isnan(geo.node_z[geo.space_nodes[sid, 0]]) and not geo.walk_n[geo.space_nodes[sid, 0]]
    assert not np.isin(geo.heights.fine_edges[:, (0, 2)], sid).any() and len(geo.heights.fine_edges) == 1
    assert geo.heights is not asset and geo.heights.digest == asset.digest and geo.height_sha == asset.digest
    assert (geo.heights.fine_floors[0] == -1).all()
    for was, now in zip(before, snapshot(asset)):
        assert np.array_equal(was, now)
    assert geo.heights.floors is asset.floors
    ag.graph(geo)                                                        # the later readers cope


def test_same_floor_without_cover_still_refuses():
    geo = geo_of(masks()); asset = flat_asset_geo()
    with pytest.raises(ValueError, match='outside permanent walk mask'):
        cg.attach_heights(geo, asset)
    geo = geo_of(masks()); cg.attach_cover(geo, np.zeros((1024, 1024), bool), 'x')    # a mask with no pixels
    with pytest.raises(ValueError, match='outside permanent walk mask'):
        cg.attach_heights(geo, asset)


def test_cover_elsewhere_does_not_excuse_the_floor():
    geo = geo_of(masks()); cover = np.zeros((1024, 1024), bool); cover[100:110, 100:110] = True
    cg.attach_cover(geo, cover, 'x')
    with pytest.raises(ValueError, match='outside permanent walk mask'):
        cg.attach_heights(geo, flat_asset_geo())


def test_a_walkable_child_of_a_parent_the_box_made_unwalkable_is_dropped_too():
    # Summit 23709: the child's own 4x4 is walkable with no cover, but the box fills most of its 8x8 parent.
    pixels = masks(hole=False); cover = np.zeros((1024, 1024), bool)
    for child in CHILDREN[:3]:
        y, x = divmod(child, 256)
        pixels[y * 4:y * 4 + 4, x * 4:x * 4 + 4] = False
        cover[y * 4:y * 4 + 4, x * 4:x * 4 + 4] = True
    geo = geo_of(pixels)
    assert not geo.walk.ravel()[PARENT]
    with pytest.raises(ValueError, match='outside permanent walk mask'):
        cg.attach_heights(geo_of(pixels), flat_asset_geo())                # no cover: still refused
    cg.attach_cover(geo, cover, 'x')
    cg.attach_heights(geo, flat_asset_geo())
    assert geo.height_cover_dropped == 4 and not geo.space_walk[ag.BASE + CHILDREN[3]]


def test_a_fine_edge_into_an_8px_cell_a_box_made_unwalkable_is_dropped():
    # Summit's box at (518,378): the coarse cell's floor goes silently, the fine edges into it named it.
    ring = PARENT - 1
    y, x = divmod(ring, 128)
    pixels = masks(hole=False); pixels[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = False
    with pytest.raises(ValueError, match='mixed edge'):
        cg.attach_heights(geo_of(pixels), flat_asset_geo())                # no cover: still refused
    cover = np.zeros((1024, 1024), bool); cover[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = True
    geo = geo_of(pixels); cg.attach_cover(geo, cover, 'x')
    asset = flat_asset_geo()
    cg.attach_heights(geo, asset)
    assert geo.height_cover_dropped == 1 and len(geo.heights.fine_edges) == 3
    assert not np.isin(geo.heights.fine_edges[:, (0, 2)], ring).any()
    assert np.array_equal(geo.heights.fine_floors, asset.fine_floors)      # no fine floor was on the box


def test_no_drop_keeps_the_asset_and_the_cache_key():
    # every child walkable: cover present but nothing to drop
    plain = geo_of(masks(hole=False)); asset = flat_asset_geo()
    cg.attach_heights(plain, asset)
    withcover = geo_of(masks(hole=False)); cg.attach_cover(withcover, cover_mask(), 'x')
    cg.attach_heights(withcover, asset)
    assert withcover.height_cover_dropped == 0 and withcover.heights is asset and plain.heights is asset
    assert cg.cache_key(withcover) == cg.cache_key(plain)
    geo, asset = covered(); cg.attach_heights(geo, asset)
    assert geo.height_cover_dropped == 1 and cg.cache_key(geo) != cg.cache_key(plain)


def same_nodes(a, b):
    for name in ('n', 'node_cell', 'node_space', 'node_of', 'space_nodes', 'space_walk', 'walk_n'):
        assert np.array_equal(getattr(a, name), getattr(b, name)), name
    assert np.array_equal(a.node_z, b.node_z, equal_nan=True)


def test_reattaching_the_filtered_asset_gives_the_same_geometry_and_count():
    # load_geometry's order: cover, then heights
    first, asset = covered(); cg.attach_heights(first, asset)
    # prepare_geometry's fresh branch: a new geometry from the masks, cover copied across, then geo.heights
    fresh = geo_of(masks()); fresh.cover_px, fresh.cover_sha = first.cover_px, first.cover_sha
    cg.attach_heights(fresh, first.heights)
    same_nodes(first, fresh)
    assert fresh.height_cover_dropped == first.height_cover_dropped == 1
    assert cg.cache_key(fresh) == cg.cache_key(first)
    assert np.array_equal(fresh.heights.fine_edges, first.heights.fine_edges)
    # geometry_from_feature_inputs' order: masks, cover, then the exact asset it is given (the filtered one)
    rebuilt = geo_of(masks()); cg.attach_cover(rebuilt, cover_mask(), 'x')
    cg.attach_heights(rebuilt, first.heights)
    same_nodes(first, rebuilt)
    assert rebuilt.height_cover_dropped == 1 and cg.cache_key(rebuilt) == cg.cache_key(first)

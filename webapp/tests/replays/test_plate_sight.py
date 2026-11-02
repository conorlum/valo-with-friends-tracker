"""Opt-in learnt box surfaces for airborne sight; default geometry stays frozen (R3, R5)."""
import copy

import numpy as np
import pytest

from app.control import features as cf, geometry as cg, heights as hc
from tests.replays.control_toys import TOY_SCALE, rect_mask

Y = 204
EAST = np.array([0.0])


def box(rows=((222, Y, 22),), west_dm=0):
    """west_dm: the floor (dm) of every cell west of x = 192 px, the viewer's side; the box's ring floor stays 0."""
    floor = rect_mask([(96, 192, 352, 224)])
    cover = rect_mask([(216, 200, 232, 216)])
    geo = cg.geometry_from_masks('PlateToy', ~floor | cover, floor & ~cover, TOY_SCALE, None)
    cg.attach_cover(geo, cover, None)
    floors = np.full((cg.GRID, cg.GRID, hc.MAX_FLOORS), -1, np.int16)
    floors[geo.walk, 0] = 0
    floors[geo.walk & (np.arange(cg.GRID)[None, :] * cg.CELL < 192), 0] = west_dm
    asset = hc.HeightAsset(floors, np.zeros_like(floors), geo.walk.copy(), np.zeros_like(geo.walk),
                           np.empty((0, 5), np.int32), {'origin_z': 0},
                           box_tops=np.asarray(rows, np.int32).reshape(-1, 3))
    return cg.attach_heights(geo, asset)


def test_line_above_a_learnt_box_clears_but_a_grounded_line_does_not():
    geo = box()
    assert cg.los(geo, (164, Y, 2.25), (308, Y, 1.1), plates=True)
    assert not cg.los(geo, (164, Y, 1.05), (308, Y, 1.1), plates=True)
    assert not cg.los(geo, (164, Y, 2.25), (236, Y, 1.1), plates=True)


def test_jump_fan_sees_beyond_box_but_not_the_low_target_just_behind_it():
    geo = box()
    high = cg.cast(geo, 164, Y, EAST, [], eye_z=2.25, plates=True)
    low = cg.cast(geo, 164, Y, EAST, [], eye_z=1.05, plates=True)
    assert high[geo.node_at_px(308, Y, 0)]
    assert not low[geo.node_at_px(308, Y, 0)]
    assert not high[geo.node_at_px(236, Y, 0)]


def test_surface_uses_each_pixels_nearest_top_and_lower_top_on_a_tie():
    rows = [(228, Y, 22), (220, Y, 12)]
    geo = box(rows)
    sight, surface, piece = cg.plate_surface(geo)
    assert surface.dtype == np.float32 and piece.dtype == np.int32
    assert surface.shape == piece.shape == (cg.PX, cg.PX)
    assert surface[Y, 220] == pytest.approx(0.3)
    assert surface[Y, 228] == pytest.approx(1.3)
    assert surface[Y, 224] == pytest.approx(0.3)
    assert np.isnan(surface[geo.cover_px == 0]).all()
    assert not sight[geo.cover_px == 1].any()
    assert np.array_equal(piece, geo.cover_px)
    reverse = cg.plate_surface(box(rows[::-1]))[1]
    assert np.array_equal(surface, reverse, equal_nan=True)
    # The lower top's feet point (1.2 - .7) is clear from above on its side of the stack.
    assert cg.los(geo, (196, Y, 2.25), (220, Y, 0.5), plates=True)


@pytest.mark.parametrize('top_dm,peekable', [(10, True), (20, True), (22, True), (24, False), (25, False), (30, False)])
def test_only_a_box_up_to_2m_above_its_floor_can_be_jump_peeked(top_dm, peekable):
    # The top of a 1 m jump: position 1.0, eye 2.05. It clears 2.0 m plates and would clear 2.5 m by height alone.
    geo = box(((222, Y, top_dm),))
    assert cg.los(geo, (164, Y, 2.05), (308, Y, 1.1), plates=True) is peekable
    assert bool(cg.cast(geo, 164, Y, EAST, [], eye_z=2.05, plates=True)[geo.node_at_px(308, Y, 0)]) is peekable


def test_a_box_too_high_to_peek_counts_as_4m_and_a_taller_one_keeps_its_height():
    for top_dm, surface_m in ((25, 3.1), (40, 3.1), (51, 4.2)):
        assert cg.plate_surface(box(((222, Y, top_dm),)))[1][Y, 222] == pytest.approx(surface_m)
    # Someone already high enough, on a taller box for instance, still sees over it.
    assert cg.los(box(((222, Y, 25),)), (164, Y, 3.6), (308, Y, 3.6), plates=True)


def test_without_a_viewer_floor_the_limit_is_measured_from_the_boxs_own_floor():
    geo = box(((222, Y, 25),))
    geo.heights = copy.deepcopy(geo.heights)
    geo.heights.floors[geo.walk, 0] = 10  # the floor is now 1 m up, so this top is 1.5 m above it
    assert dict(geo.box_ground)[1] == pytest.approx(1.0)
    assert cg.plate_surface(geo)[1][Y, 222] == pytest.approx(1.6)
    assert cg.plate_z(geo, Y, 222) == pytest.approx(1.6)


def test_the_limit_is_measured_from_the_floor_the_viewer_jumps_from():
    # The owner's rule (2026-11-02): a 2.5 m box on a floor at 0 is only 2.0 m above a viewer on a slope 0.5 m up.
    geo = box(((222, Y, 25),), west_dm=5)
    assert dict(geo.box_ground)[1] == pytest.approx(0.0)
    up = geo.node_at_px(164, Y, 0.5)
    assert geo.node_z[up] == pytest.approx(0.5)
    eye, target = (164, Y, 0.5 + 1.0 + hc.EYE_M), (308, Y, 1.1)   # the top of a 1 m jump from that floor
    assert cg.los(geo, eye, target, plates=True)
    assert cg.los(geo, eye, target, plates=True, floor=0.5)
    assert cg.cast(geo, 164, Y, EAST, [], eye_z=eye[2], plates=True)[geo.node_at_px(308, Y, 0)]
    assert cf.los_with(geo, eye, target, plates=True)
    # The same eye over a floor at the box's own level can't peek it: the box counts as 2-height there.
    assert not cg.los(geo, eye, target, plates=True, floor=0.0)
    assert not cg.cast(geo, 164, Y, EAST, [], eye_z=eye[2], plates=True, floor=0.0)[geo.node_at_px(308, Y, 0)]
    assert cg.plate_z(geo, Y, 222, 0.5) == pytest.approx(1.6) and cg.plate_z(geo, Y, 222, 0.0) == pytest.approx(3.1)


@pytest.mark.parametrize('a,b', [((220, Y, 1.05), (308, Y, 1.1)),
                               ((164, Y, 1.05), (228, Y, 1.1))])
def test_skipped_own_or_target_piece_never_blocks(a, b):
    geo = box()
    assert not cg.los(geo, a, b, plates=True)
    assert cg.los(geo, a, b, plates=True, skip=(1,))


def test_cast_does_not_raise_the_horizon_on_the_viewers_own_piece():
    geo = box()
    seen = cg.cast(geo, 220, Y, EAST, [], eye_z=1.05, plates=True)
    assert seen[geo.node_at_px(308, Y, 0)]


def test_unknown_top_remains_an_unlimited_wall():
    geo = box(())
    sight, surface, _ = cg.plate_surface(geo)
    assert sight is geo.sight and np.isnan(surface).all()
    assert not cg.los(geo, (164, Y, 100), (308, Y, 100), plates=True, skip=(1,))
    assert not cg.cast(geo, 164, Y, EAST, [], eye_z=100, plates=True)[geo.node_at_px(308, Y, 0)]


@pytest.mark.parametrize('x,angle,z', [(164, 0, 1.05), (308, 180, 2.25), (164, 7, 3.0)])
def test_default_fans_and_lines_are_byte_identical_and_build_no_plate_arrays(x, angle, z):
    geo = box()
    fan = np.arange(angle - 3, angle + 3.01, .5)
    old = cg.cast(geo, x, Y, fan, [], eye_z=z)
    explicit = cg.cast(geo, x, Y, fan, [], eye_z=z, plates=False)
    assert old.tobytes() == explicit.tobytes()
    a, b = (x, Y, z), (240, Y, 1.1)
    assert cg.los(geo, a, b) == cg.los(geo, a, b, plates=False)
    assert '_plate_surface' not in geo.__dict__


def test_surface_cache_holds_stable_inputs_and_refreshes_replacements():
    geo = box()
    assert geo.box_tops is not geo.box_tops  # property returns a fresh proxy
    first = cg.plate_surface(geo)
    assert cg.plate_surface(geo) is first
    geo.sight = geo.sight.copy()
    second = cg.plate_surface(geo)
    assert second is not first and cg.plate_surface(geo) is second
    geo.heights = copy.deepcopy(geo.heights)
    geo.heights.box_tops = np.array([[222, Y, 12]], np.int32)
    third = cg.plate_surface(geo)
    assert third is not second and third[1][Y, 222] == pytest.approx(0.3)
    geo.cover_px = geo.cover_px.copy()
    geo.cover_px[Y, 222] = 0
    assert np.isnan(cg.plate_surface(geo)[1][Y, 222])


def test_plate_sight_merges_see_over_for_an_unknown_piece():
    geo = box(())
    assert cg.los(geo, (164, Y, 2.25), (308, Y, 1.1), plates=True, see_over=(1,))
    assert cg.cast(geo, 164, Y, EAST, [], eye_z=2.25, plates=True,
                   see_over=(1,))[geo.node_at_px(308, Y, 0)]


def test_feature_wrappers_forward_plate_and_skip_arguments():
    geo = box()
    a, b = (164, Y, 2.25), (308, Y, 1.1)
    assert cf.los_with(geo, a, b, plates=True)
    assert cf.los_with(geo, (164, Y, 1.05), b, plates=True, skip=(1,))
    assert cf.cast_with(geo, 164, Y, EAST, [], [], eye_z=2.25,
                        plates=True)[geo.node_at_px(308, Y, 0)]


def test_top_pass_uses_plate_lines_and_skips_both_end_pieces(monkeypatch):
    from tests.replays.test_top_nodes import topped

    geo = topped(((202, 202, 22), (214, 206, 33)))
    own = geo.node_at_px(196, 204, 0)
    seen = np.zeros(geo.n, bool)
    calls = []
    def line(*args, **kwargs):
        calls.append(kwargs)
        return True
    def reverse(*args):
        pytest.fail('plate mode must not use reverse top views')
    monkeypatch.setattr(cg, 'los', line)
    monkeypatch.setattr(cg, '_top_views', reverse)
    cg._see_tops(geo, 196, 204, np.arange(0, 360, cg.RAY_STEP_DEG), [], 2.25,
                 own, None, (), seen, plates=True)
    assert calls and seen[geo.top_nodes].all()
    assert all(c['plates'] and set(c['skip']) == {0, 1} for c in calls)


def test_probe_row_shape_uses_pixel_coordinates_and_absolute_point_heights():
    row = {'variant': 'predeath', 'plates': {'eye_a': [164, Y, 2.25],
           'eye_b': [308, Y, 1.1], 'own_pieces': [0, 0]}}
    p = row['plates']
    assert cg.los(box(), p['eye_a'], p['eye_b'], plates=True, skip=p['own_pieces'])

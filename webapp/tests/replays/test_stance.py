"""Standing on a box: see-over casts, the stance rule and the anchor rule (plan Stage 3, red first).

The tests marked xfail(strict) pin the rule that does not exist yet (app/control/stance.py is an inert stub);
they fail with AssertionError today and must turn green when the rule lands. The unmarked ones are controls:
behaviour that already holds and must keep holding. Angles: 0 = +x (east), 90 = +y (south), as in `cast`.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import (BOX_PX, BOX_RING, BOX_WITNESS, GRID, THREE_BOXES, WALL_PIECE_PX, box_hall,  # noqa: E402
                          box_hall_tops, height_blob, hugging_track, jump_track, on_box_track, three_boxes,
                          wall_piece_hall)

ANGLES = np.arange(0, 360, 0.25)
FACING = {"E": 0.0, "S": 90.0, "W": 180.0, "N": 270.0}        # the side a yaw faces -> its cast angle
CENTRE = (260.0, 196.0)                                        # the box's centre pixel
CONE = 103.0

red = pytest.mark.xfail(strict=True, raises=AssertionError, reason="the standing-on-a-box rule is not built yet")


def cone(centre_deg, width=CONE):
    return np.arange(centre_deg - width / 2, centre_deg + width / 2 + 1e-9, 0.25) % 360


def ring_xy(geo, side):
    x, y = geo.centres[BOX_RING[side]]
    return float(x), float(y)


def round_of(geo, players, t_end=10.0):
    from app.control.engine import RoundInputs

    return RoundInputs(height_blob(players, t_end=t_end), geo, None, None)


def stance(geo, points, t, extra=None):
    from app.control import stance as st_mod

    players = {0: points}
    players.update(extra or {})
    return st_mod.stance_at(round_of(geo, players), 0, t)


def assert_over_keeps_ground_cell(geo, blob, t, monkeypatch):
    """Compare fresh rounds so switching the rule cannot reuse a memoised stance."""
    from app.control import engine, stance as st_mod

    assert not st_mod.OVER
    before = engine.Tick(engine.RoundInputs(blob, geo, None, None), t).holders[0]
    assert before.stance is None or before.stance.kind == "beside"
    monkeypatch.setattr(st_mod, "OVER", True)
    after = engine.Tick(engine.RoundInputs(blob, geo, None, None), t).holders[0]
    st = after.stance
    assert st is not None and st.kind == "over" and st.piece == 1 and st.see_over == (1,)
    assert st.anchor == (before.stance.anchor if before.stance is not None else None)
    assert after.cell == before.cell


# ------------------------------------------------------------------ 1. the 360 degree view from the box


@pytest.mark.parametrize("side", ["N", "S", "E", "W"])
def test_a_viewer_on_the_box_sees_every_side_witness(side):
    from app.control import geometry as cg

    geo = box_hall()
    seen = cg.cast(geo, *CENTRE, ANGLES, [], eye_z=None, see_over=1)
    assert seen[BOX_WITNESS[side]]


@pytest.mark.parametrize("side", ["N", "S", "E", "W"])
def test_control_a_ground_viewer_does_not_see_the_witness_behind_the_box(side):
    from app.control import geometry as cg

    opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}[side]
    geo = box_hall()
    seen = cg.cast(geo, *ring_xy(geo, opposite), ANGLES, [], eye_z=None, see_over=0)
    assert not seen[BOX_WITNESS[side]]


# ------------------------------------------------------------------ 2. four yaws


@pytest.mark.parametrize("side", ["N", "S", "E", "W"])
def test_a_cone_from_the_box_reaches_the_faced_side_and_not_the_others(side):
    from app.control import geometry as cg

    geo = box_hall()
    seen = cg.cast(geo, *CENTRE, cone(FACING[side]), [], eye_z=None, see_over=1)
    assert seen[BOX_WITNESS[side]]
    for other, cell in BOX_WITNESS.items():
        if other != side:
            assert not seen[cell]


# ------------------------------------------------------------------ 3. one cell off the box


@pytest.mark.parametrize("side", ["N", "S", "E", "W"])
def test_control_a_player_one_cell_off_the_box_has_no_stance_and_todays_cast(side):
    from app.control import geometry as cg
    from app.control import stance as st_mod

    geo = box_hall_tops()
    x, y = ring_xy(geo, side)
    rnd = round_of(geo, {0: ("A", [(0.0, x, y, 0, 0.0), (10.0, x, y, 0, 0.0)])})
    assert st_mod.stance_at(rnd, 0, 5.0) is None
    assert cg.cast(geo, x, y, ANGLES, [], see_over=0).tobytes() == cg.cast(geo, x, y, ANGLES, []).tobytes()


# ------------------------------------------------------------------ 4. two or three boxes


def _labels(geo):
    return (int(geo.cover_px[196, 156]), int(geo.cover_px[196, 252]), int(geo.cover_px[208, 204]))


def test_three_boxes_are_three_pieces():
    geo = three_boxes()
    la, lb, lc = _labels(geo)
    assert len({la, lb, lc}) == 3 and 0 not in (la, lb, lc)
    assert sorted(int(v) for v in np.unique(geo.cover_px)) == [0, *sorted((la, lb, lc))]
    assert set(THREE_BOXES) == {"A", "B", "C"}


def test_a_line_from_box_a_to_box_b_clears_with_both_pieces_exempt():
    from app.control import geometry as cg

    geo = three_boxes()
    la, lb, _ = _labels(geo)
    assert cg.los(geo, (156, 186, None), (252, 186, None), see_over=(la, lb))


def test_control_without_see_over_the_a_to_b_line_is_blocked():
    from app.control import geometry as cg

    geo = three_boxes()
    la, lb, _ = _labels(geo)
    assert not cg.los(geo, (156, 186, None), (252, 186, None), see_over=(0, 0))
    assert not cg.los(geo, (156, 186, None), (252, 186, None))
    assert not cg.los(geo, (156, 186, None), (252, 186, None), see_over=(la, 0))   # B still blocks its own top


def test_control_a_line_from_a_to_b_across_box_c_stays_blocked():
    from app.control import geometry as cg

    geo = three_boxes()
    la, lb, _ = _labels(geo)
    assert not cg.los(geo, (156, 205, None), (252, 205, None), see_over=(la, lb))


# ------------------------------------------------------------------ 5. an over-drawn outline over a real wall


def test_the_wall_piece_toy_is_one_piece_that_leaves_the_wall_out():
    geo = wall_piece_hall()
    x0, y0, x1, y1 = WALL_PIECE_PX
    assert set(np.unique(geo.cover_px[y0:y1, x0:x1])) == {0, 1}
    assert geo.cover_px[188, 252] == 0 and geo.cover_px[200, 252] == 1
    assert geo.sight[188, 252]


def test_a_piece_over_open_floor_beside_the_wall_is_seen_over():
    from app.control import geometry as cg

    geo = wall_piece_hall()
    assert cg.los(geo, (240, 200, None), (290, 200, None), see_over=(1, 0))


def test_control_see_over_does_not_look_through_the_wall_pixels_of_the_piece():
    from app.control import geometry as cg

    geo = wall_piece_hall()
    assert not cg.los(geo, (240, 188, None), (290, 188, None), see_over=(1, 0))


# ------------------------------------------------------------------ 6. the stance z rule on box_hall


def test_steady_on_the_box_is_on_box():
    geo = box_hall_tops()
    st = stance(geo, on_box_track(*CENTRE), 5.0)
    assert st is not None
    assert st.kind == "on_box"
    assert st.see_over == (1,)
    assert st.eye_px == st.px


def test_hugging_the_box_inside_the_piece_is_beside():
    geo = box_hall_tops()
    st = stance(geo, hugging_track(250, 196), 5.0)
    assert st is not None
    assert st.kind == "beside"
    assert st.see_over == ()
    assert st.anchor is not None and geo.walk.ravel()[st.anchor]


def test_a_jump_over_the_box_is_beside_in_mid_air(monkeypatch):
    from app.control import stance as st_mod

    # the on_box/beside rule alone (R4); OVER on is asserted below
    monkeypatch.setattr(st_mod, "OVER", False)
    geo = box_hall_tops()
    st = stance(geo, jump_track(230, 290, 196, 1.5, 2.0), 2.5)
    assert st is not None
    assert st.kind == "beside"
    assert st.see_over == ()


def _lands_on_box():
    return "A", [(0.0, 230, 196, 0, 0.0), (1.9, 236, 196, 0, 0.0), (2.0, 260, 196, 0, 1.0), (10.0, 260, 196, 0, 1.0)]


def test_the_first_half_second_after_landing_is_beside(monkeypatch):
    from app.control import stance as st_mod

    # the on_box/beside rule alone (R4); OVER on is asserted below
    monkeypatch.setattr(st_mod, "OVER", False)
    geo = box_hall_tops()
    st = stance(geo, _lands_on_box(), 2.3)
    assert st is not None
    assert st.kind == "beside"


def test_after_the_settle_time_it_is_on_box():
    geo = box_hall_tops()
    st = stance(geo, _lands_on_box(), 2.6)
    assert st is not None
    assert st.kind == "on_box"
    assert st.see_over == (1,)


def test_a_player_in_the_piece_with_no_z_is_beside():
    geo = box_hall_tops()
    st = stance(geo, ("A", [(0.0, *CENTRE, 0)]), 5.0)
    assert st is not None
    assert st.kind == "beside"
    assert st.see_over == ()


# ------------------------------------------------------------------ 7. anchors on box_hall


def test_a_hugger_at_the_west_edge_anchors_west_of_the_box():
    geo = box_hall_tops()
    st = stance(geo, hugging_track(250, 196), 5.0)
    assert st is not None and st.anchor is not None
    assert geo.walk.ravel()[st.anchor]
    assert float(geo.centres[st.anchor][0]) < BOX_PX[0]


def test_two_equidistant_anchors_take_the_lowest_node_id():
    geo = box_hall_tops()
    st = stance(geo, hugging_track(256, 186), 5.0)         # 4 px from the centres of ring cells (22, 31) and (22, 32)
    assert st is not None
    assert st.anchor == 22 * GRID + 31


def test_the_node_last_stood_on_wins_when_it_still_qualifies():
    geo = box_hall_tops()
    # stands north of the box at ring cell (22, 32), then steps into the piece's north-east corner, where the east
    # ring cell (23, 34) is closer but the north cell is still within range and has a clear line
    walk_in = ("A", [(0.0, 260, 180, 0, 0.0), (2.0, 260, 180, 0, 0.0), (3.0, 267, 190, 0, 0.0),
                     (10.0, 267, 190, 0, 0.0)])
    st = stance(geo, walk_in, 6.0)
    assert st is not None
    assert st.anchor == 22 * GRID + 32


# ------------------------------------------------------------------ 8. an anchor on real Summit


def _summit_stance(px, py):
    from app.control import geometry as cg
    from app.control import stance as st_mod

    geo = cg.load_geometry("Summit")
    cg.visibility(geo)
    rnd = round_of(geo, {0: ("A", [(0.0, px, py, 0)])})              # no z: beside
    return geo, st_mod.stance_at(rnd, 0, 5.0)


def _check_summit(px, py, label):
    geo, st = _summit_stance(px, py)
    assert int(geo.cover_px[py, px]) == label
    assert not geo.walk.ravel()[(py // 8) * GRID + px // 8]
    assert st is not None
    assert st.kind == "beside"
    assert st.anchor is not None and geo.walk.ravel()[st.anchor]
    ax, ay = geo.centres[st.anchor]
    metres_per_px = geo.cell_m / (1024 // GRID)
    assert math.hypot(ax - px, ay - py) * metres_per_px <= 2.0 + 1e-6


def test_summit_piece_14_pixel_with_a_disabled_cell_gets_a_walkable_anchor():
    _check_summit(982, 399, 14)


# The plan's (498, 663) is no longer in a piece; (498, 662) is the nearest in-piece pixel (piece 26) whose own
# coarse cell is disabled (found by a one-off probe).
def test_summit_piece_26_pixel_with_a_disabled_cell_gets_a_walkable_anchor():
    _check_summit(498, 662, 26)


# ------------------------------------------------------------------ 9. controls


@pytest.mark.parametrize("cell", [(100, 150), (200, 250), (260, 160), (230, 196), (300, 240), (130, 120)])
def test_control_see_over_zero_is_byte_identical_to_no_kwarg(cell):
    from app.control import geometry as cg

    geo = box_hall()
    assert cg.cast(geo, *cell, ANGLES, [], see_over=0).tobytes() == cg.cast(geo, *cell, ANGLES, []).tobytes()


def test_control_an_empty_box_blocks_two_ground_players():
    from app.control import geometry as cg

    geo = box_hall()
    nx, ny = ring_xy(geo, "N")
    wx, wy = (float(v) for v in geo.centres[BOX_WITNESS["S"]])
    assert not cg.los(geo, (nx, ny, None), (wx, wy, None), see_over=(0, 0))
    assert not cg.los(geo, (wx, wy, None), (nx, ny, None))


def test_control_a_smoke_between_the_box_viewer_and_a_witness_blocks_it():
    from app.control import geometry as cg

    geo = box_hall()
    seen = cg.cast(geo, *CENTRE, ANGLES, [(282.0, 196.0, 12.0, True)], eye_z=None, see_over=1)
    assert not seen[BOX_WITNESS["E"]]


def test_control_cast_and_los_never_call_stance_at():
    from app.control import geometry as cg
    from app.control import stance as st_mod

    geo = box_hall()
    before = st_mod.CALLS
    cg.cast(geo, *CENTRE, ANGLES, [], see_over=1)
    cg.cast(geo, 100.0, 150.0, ANGLES, [])
    cg.los(geo, (100, 150, None), (300, 150, None), see_over=(0, 0))
    cg.los(geo, (100, 150, None), (300, 150, None))
    assert st_mod.CALLS == before


# ------------------------------------------------------------------ see_over = 0 is today's, and only the named piece is exempt


def _without_cover(geo):
    import copy

    bare = copy.copy(geo)
    bare.cover_px = None
    bare.__dict__.pop("_sight_without", None)
    return bare


def test_see_over_zero_is_todays_cast_and_los_on_box_hall():
    from app.control import geometry as cg

    geo = box_hall()
    bare = _without_cover(geo)
    for x, y in (CENTRE, (200.0, 196.0), (260.0, 150.0)):
        assert cg.cast(geo, x, y, ANGLES, [], see_over=0).tobytes() == cg.cast(bare, x, y, ANGLES, []).tobytes()
    a, b = (200, 196, None), (320, 196, None)
    assert cg.los(geo, a, b, see_over=(0, 0)) == cg.los(bare, a, b)


def test_see_over_is_inert_on_a_map_without_cover():
    from app.control import geometry as cg
    from control_toys import open_hall

    geo = open_hall()
    assert geo.cover_px is None
    assert cg.cast(geo, 100.0, 150.0, ANGLES, [], see_over=3).tobytes() == cg.cast(geo, 100.0, 150.0, ANGLES, []).tobytes()
    assert cg.los(geo, (100, 150, None), (300, 150, None), see_over=(3, 4)) == cg.los(geo, (100, 150, None), (300, 150, None))


def test_another_pieces_label_does_not_see_through_this_box():
    from app.control import geometry as cg

    geo = three_boxes()
    la, lb, lc = _labels(geo)
    assert not cg.los(geo, (156, 186, None), (252, 186, None), see_over=(lc, lc))
    assert not cg.los(geo, (156, 186, None), (252, 186, None), see_over=(lc, 0))


def test_a_line_from_the_box_to_the_ground_behind_it_clears_with_the_eyes_piece_only():
    from app.control import geometry as cg

    geo = box_hall()
    label = int(geo.cover_px[int(CENTRE[1]), int(CENTRE[0])])
    assert label != 0
    x, y = ring_xy(geo, "W")
    assert cg.los(geo, (CENTRE[0], CENTRE[1], None), (x - 10, y, None), see_over=(label, 0))
    assert not cg.los(geo, (CENTRE[0], CENTRE[1], None), (x - 10, y, None), see_over=(0, 0))


# ------------------------------------------------------------------ W12: segments, references, memo, fallback


def test_a_new_track_segment_at_box_height_is_beside_for_half_a_second_then_on_box(monkeypatch):
    from app.control import stance as st_mod
    from control_toys import z_track

    geo = box_hall_tops()
    players = {0: ("A", [(0.0, 230, 196, 0, 0.0), (10.0, 230, 196, 0, 0.0)])}
    blob = height_blob(players, t_end=10.0)
    # a teleport: the first segment ends on the ground, a second starts on the box top at z 1.0 m
    blob["tracks"]["0"] = (z_track([(0.0, 230, 196, 0, 0.0), (2.9, 230, 196, 0, 0.0)], 0.0, 2.9)
                           + z_track([(3.0, *CENTRE, 0, 1.0), (10.0, *CENTRE, 0, 1.0)], 3.0, 10.0))
    from app.control.engine import RoundInputs

    # the on_box/beside rule alone (R4); OVER on is asserted below
    monkeypatch.setattr(st_mod, "OVER", False)
    rnd = RoundInputs(blob, geo, None, None)
    assert st_mod.stance_at(rnd, 0, 3.2).kind == "beside"
    assert st_mod.stance_at(rnd, 0, 3.6).kind == "on_box"
    assert_over_keeps_ground_cell(geo, blob, 3.2, monkeypatch)


def test_a_teleport_from_the_same_height_is_still_beside_for_half_a_second(monkeypatch):
    from app.control import stance as st_mod
    from app.control.engine import RoundInputs
    from control_toys import z_track

    geo = box_hall_tops()
    players = {0: ("A", [(0.0, 300, 196, 0, 1.0), (10.0, 300, 196, 0, 1.0)])}
    blob = height_blob(players, t_end=10.0)
    # an Omen-like teleport between two spots at z 1.0 m: the old segment's samples must not count for the window
    blob["tracks"]["0"] = (z_track([(0.0, 300, 196, 0, 1.0), (2.9, 300, 196, 0, 1.0)], 0.0, 2.9)
                           + z_track([(3.0, *CENTRE, 0, 1.0), (10.0, *CENTRE, 0, 1.0)], 3.0, 10.0))
    # the on_box/beside rule alone (R4); OVER on is asserted below
    monkeypatch.setattr(st_mod, "OVER", False)
    rnd = RoundInputs(blob, geo, None, None)
    assert st_mod.stance_at(rnd, 0, 3.2).kind == "beside"
    assert st_mod.stance_at(rnd, 0, 3.6).kind == "on_box"
    assert_over_keeps_ground_cell(geo, blob, 3.2, monkeypatch)


def test_the_anchor_is_on_the_players_side_of_a_real_wall_not_the_nearer_node_across_it():
    import hashlib

    from app.control import geometry as cg
    from app.control.engine import RoundInputs
    from app.control import stance as st_mod
    from control_toys import _DIR, TOY_SCALE, HALL, geometry_from_masks, rect_mask, visibility

    floor = rect_mask([HALL])
    wall = rect_mask([(253, 96, 259, 200)])                 # 6 px thick: the cells either side stay walkable
    piece = rect_mask([(259, 192, 288, 208)])
    geo = geometry_from_masks("ThinWallPiece", ~floor | wall | piece, floor & ~wall & ~piece, TOY_SCALE, None)
    attach = piece & ~wall
    cg.attach_cover(geo, attach, hashlib.sha256(np.packbits(attach).tobytes()).hexdigest()[:12])
    visibility(geo, _DIR)
    label = int(geo.cover_px[196, 259])
    assert label != 0
    west, east = 24 * GRID + 31, 23 * GRID + 32       # (252, 196) across the wall; (260, 188) own side
    assert geo.walk.ravel()[west] and geo.walk.ravel()[east]
    assert not cg.los(geo, (252.0, 196.0, None), (259.0, 196.0, None), see_over=(label, label))
    assert cg.los(geo, (260.0, 188.0, None), (259.0, 196.0, None), see_over=(label, label))
    # the node across the wall is nearer (7 px against 8.1 px) but not in sight of the player
    rnd = RoundInputs(height_blob({0: ("A", [(0.0, 259.5, 196, 0, 0.0), (10.0, 259.5, 196, 0, 0.0)])}, t_end=10.0),
                      geo, None, None)
    st = st_mod.stance_at(rnd, 0, 5.0)
    assert st is not None and st.anchor == east


def test_a_map_with_no_heights_and_nobody_else_around_is_beside():
    # was test_no_ring_samples_with_z_means_beside (the ring and `_box_ref` are gone)
    from app.control import stance as st_mod

    geo = box_hall()
    rnd = round_of(geo, {0: on_box_track(*CENTRE)})
    st = st_mod.stance_at(rnd, 0, 5.0)
    assert st is not None and st.kind == "beside" and st.see_over == ()


def test_the_top_is_the_reference_so_no_other_player_is_needed():
    # replaces test_a_reference_player_4_px_from_the_box_is_outside_the_band_and_gives_no_reference: with learnt
    # tops nobody else's z matters, and a ground player 4 px away neither helps nor hurts
    geo = box_hall_tops()
    x, y = ring_xy(geo, "W")
    alone = stance(geo, on_box_track(*CENTRE), 5.0)
    near = stance(geo, on_box_track(*CENTRE), 5.0, {5: ("B", [(0.0, x, y, 0, 0.0), (10.0, x, y, 0, 0.0)])})
    assert alone.kind == near.kind == "on_box" and alone.see_over == near.see_over == (1,)


def test_no_anchor_within_range_uses_the_nearest_walkable_node_and_counts_it(monkeypatch):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "ANCHOR_MAX_M", 0.001)
    geo = box_hall_tops()
    rnd = round_of(geo, {0: on_box_track(*CENTRE)})
    st = st_mod.stance_at(rnd, 0, 5.0)
    assert st.anchor is not None and geo.walk.ravel()[st.anchor]
    assert rnd.missing[st_mod.NO_ANCHOR] == 1


def test_the_same_sample_gives_an_equal_stance_and_counts_a_fallback_once(monkeypatch):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "ANCHOR_MAX_M", 0.001)
    geo = box_hall_tops()
    rnd = round_of(geo, {0: on_box_track(*CENTRE)})
    a = st_mod.stance_at(rnd, 0, 5.0)
    b = st_mod.stance_at(rnd, 0, 5.0)
    assert a == b
    assert rnd.missing[st_mod.NO_ANCHOR] == 1


# ------------------------------------------------------------------ W5b: immediate over vision, behind OVER


@pytest.mark.slow
def test_over_off_keeps_the_existing_jump_and_landing_rules(monkeypatch):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", False)
    test_a_jump_over_the_box_is_beside_in_mid_air(monkeypatch)
    test_the_first_half_second_after_landing_is_beside(monkeypatch)


@pytest.mark.slow
def test_over_a_jump_above_the_top_keeps_the_beside_ground_anchor(monkeypatch):
    from app.control import stance as st_mod

    geo = box_hall_tops()
    points = jump_track(230, 290, 196, 1.5, 2.0)
    monkeypatch.setattr(st_mod, "OVER", False)
    beside = stance(geo, points, 2.5)
    monkeypatch.setattr(st_mod, "OVER", True)
    st = stance(geo, points, 2.5)
    assert st is not None and st.kind == "over" and st.see_over == (1,)
    assert st.eye_px == st.px
    assert st.anchor == beside.anchor
    rnd = round_of(geo, {0: points})
    z = rnd.heights[0][rnd._sample(0, 2.5)]
    assert st.own == int(geo.node_at_px(*st.px, z))


@pytest.mark.slow
def test_over_the_first_half_second_after_landing_has_immediate_vision(monkeypatch):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    st = stance(box_hall_tops(), _lands_on_box(), 2.3)
    assert st is not None and st.kind == "over" and st.see_over == (1,)


@pytest.mark.slow
@pytest.mark.parametrize("offset, expected", [(8, "over"), (9, None)])
def test_over_view_reach_is_1_1_metres_and_the_guard_uses_it(monkeypatch, offset, expected):
    import dataclasses
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = dataclasses.replace(box_hall_tops(), m_per_px=0.1302, height_sha=None)
    # Stay within the intended raster column after track-coordinate quantisation.
    x, y = BOX_PX[0] - offset + 0.05, CENTRE[1]
    rnd = round_of(geo, {0: on_box_track(x, y)})
    st = st_mod.stance_at(rnd, 0, 5.0)
    assert (st.kind if st else None) == expected
    guarded = st_mod.boxed_stance(rnd, 0, 5.0)
    assert guarded == st
    if st is not None:
        assert st.anchor is None
        assert st.own == int(geo.node_at_px(*st.px, 1.0))


@pytest.mark.slow
@pytest.mark.parametrize("rise, expected", [(0.4, "beside"), (0.5, "over"), (1.0, "over"), (3.0, "over")])
def test_over_has_a_crouch_limit_and_no_upper_height_or_time_limit(monkeypatch, rise, expected):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    # The first sample cannot satisfy the half-second on_box window.
    st = stance(box_hall_tops(), on_box_track(*CENTRE, rise_m=rise), 0.0)
    assert st is not None and st.kind == expected


@pytest.mark.slow
@pytest.mark.parametrize("missing", ["top", "ground", "heights", "nan"])
def test_over_requires_a_learnt_top_ring_floor_and_valid_z(monkeypatch, missing):
    import dataclasses
    from app.control import geometry as cg
    from app.control import stance as st_mod
    from control_toys import box_hall_no_tops

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = dataclasses.replace(box_hall_tops())
    if missing == "top":
        geo = box_hall_no_tops()
    elif missing == "ground":
        cg.attach_heights(geo, dataclasses.replace(geo.heights, floors=np.full_like(geo.heights.floors, -1)))
    rnd = round_of(geo, {0: on_box_track(*CENTRE)})
    if missing == "heights":
        rnd.heights.pop(0)
    elif missing == "nan":
        rnd.heights[0][rnd._sample(0, 5.0)] = np.nan
    st = st_mod.stance_at(rnd, 0, 5.0)
    assert st is not None and st.kind == "beside" and st.see_over == ()


@pytest.mark.slow
def test_over_does_not_match_below_the_ring_floor_step(monkeypatch):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    st = stance(box_hall_tops(rise_m=0.5), on_box_track(*CENTRE, rise_m=0.2), 0.0)
    assert st is not None and st.kind == "beside"


@pytest.mark.slow
@pytest.mark.parametrize("xy", [CENTRE, (244.0, 196.0)])
def test_over_a_half_second_steady_still_wins_as_on_box(monkeypatch, xy):
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    st = stance(box_hall_tops(), on_box_track(*xy), 0.5)
    assert st is not None and st.kind == "on_box" and st.see_over == (1,)
    assert st.own is None


@pytest.mark.slow
@pytest.mark.parametrize("rises, rise, expected", [((2.0, 4.0), 4.0, 2), ((2.0, 2.0), 3.0, 1)])
def test_over_nearest_top_wins_across_pieces_with_piece_id_as_last_tie(monkeypatch, rises, rise, expected):
    from app.control import stance as st_mod
    from control_toys import tops_hall

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = tops_hall("OverPair", [(144, 184, 168, 208), (172, 184, 196, 208)], rises=rises)
    # Both pieces are within view reach. At the first sample neither can be on_box.
    st = stance(geo, on_box_track(170.5, 196.0, rise_m=rise), 0.0)
    assert st is not None and st.kind == "over" and st.piece == expected


@pytest.mark.slow
def test_over_an_untopped_pixel_piece_blocks_a_neighbours_vantage(monkeypatch):
    from app.control import stance as st_mod
    from control_toys import tops_hall

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = tops_hall("OverBarePair", [(144, 184, 168, 208), (172, 184, 196, 208)], rises=(None, 2.0))
    st = stance(geo, on_box_track(166.5, 196.0, rise_m=2.0), 0.0)
    assert geo.cover_px[int(st.px[1]), int(st.px[0])] == 1
    assert st.kind == "beside" and st.see_over == ()


@pytest.mark.slow
@pytest.mark.parametrize("xy", [(156.0, 196.0), (169.5, 196.0)])
def test_over_on_box_vantage_uses_the_samples_real_height(monkeypatch, xy):
    from app.control import stance as st_mod
    from control_toys import tops_hall

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = tops_hall("OverRealHeight", [(144, 184, 168, 208), (240, 184, 264, 208)], rises=(1.0, 3.0))
    # At the learnt 1.0 top, B's surface reaches the eye; at the sample's 1.3 it is below it.
    st = stance(geo, on_box_track(*xy, rise_m=1.3), 5.0)
    assert st is not None and st.kind == "on_box" and st.piece == 1
    assert st.see_over == (1, 2)


@pytest.mark.slow
def test_over_mid_jump_vantage_uses_the_samples_real_height(monkeypatch):
    from app.control import stance as st_mod
    from control_toys import tops_hall

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = tops_hall("OverJumpHeight", [(144, 184, 168, 208), (240, 184, 264, 208)], rises=(1.0, 4.0))
    st = stance(geo, jump_track(126, 186, 196, 3.0, 2.0), 2.5)
    assert st is not None and st.kind == "over" and st.see_over == (1, 2)


def test_over_vantage_rounds_the_cache_key_and_the_computed_eye(monkeypatch):
    from types import SimpleNamespace
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    geo = SimpleNamespace(box_tops={1: (1.0,), 2: (3.03,)})
    # An unrounded eye at 1.04 would see over piece 2; the rounded 1.0 eye does not.
    assert st_mod.vantage(geo, 1, 1.04) == (1,)
    assert st_mod.vantage(geo, 1, 1.01) == (1,)
    assert list(geo._vantage) == [(1, 1.0)]


def test_over_top_match_chooses_the_nearest_height_and_lower_on_a_tie(monkeypatch):
    from types import SimpleNamespace
    from app.control import stance as st_mod

    monkeypatch.setattr(st_mod, "OVER", True)
    rnd = SimpleNamespace(geo=SimpleNamespace(box_tops={1: (1.0, 1.6)}, box_ground={1: 0.0}),
                          heights={0: np.array([1.3, 1.6])})
    assert st_mod._over_match(rnd, 0, 0, 1) == 1.0
    assert st_mod._over_match(rnd, 0, 1, 1) == 1.6

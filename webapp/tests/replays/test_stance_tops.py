"""The stance rule on learnt box tops (W8a tests, W8b rule): on a box when steady at one of the piece's learnt tops
(TOP_TOL_M), inside its outline or, off it within TOP_REACH_PX, after a stay at that top that entered the outline.

Toys: box_hall_tops / box_hall_no_tops / three_boxes_tops
(control_toys.py), z in metres above the ground (0.0 m), the box's learnt top 1.0 m."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import (BOX_RING, GRID, THREE_BOXES, box_hall, box_hall_no_tops, box_hall_tops,  # noqa: E402
                          on_box_track, three_boxes_tops, z_track)
from test_stance import CENTRE, round_of, stance  # noqa: E402

T_END = 10.0


def path(*pts, side="A"):
    """A player walking straight between (t, x, y, z m) points, yaw 0."""
    return side, [(t, x, y, 0, z) for t, x, y, z in pts]


def stay_then(x, y, z=1.0, via=None):
    """On the box top from the start (inside the outline), walking to (x, y) by t = 5 s and standing there at z."""
    pts = [(0.0, *CENTRE, 1.0), (3.0, *CENTRE, 1.0)]
    pts += via or []
    return path(*pts, (5.0, x, y, z), (T_END, x, y, z))


def labels(geo):
    return {name: int(geo.cover_px[(r[1] + r[3]) // 2, (r[0] + r[2]) // 2]) for name, r in THREE_BOXES.items()}


# ------------------------------------------------------------------ inside the outline

def test_steady_at_the_top_inside_the_outline_is_on_box():
    st = stance(box_hall_tops(), on_box_track(*CENTRE), 5.0)
    assert st is not None and st.kind == "on_box" and st.see_over == (1,) and st.eye_px == st.px


@pytest.mark.parametrize("rise", [
    1.0, 1.25, 0.0,
    pytest.param(0.5, id="0.5"),
    pytest.param(1.35, id="1.35"),
    pytest.param(2.0, id="2.0"),
])
def test_only_a_steady_height_within_the_tolerance_of_the_top_is_on_box(rise):
    st = stance(box_hall_tops(), on_box_track(*CENTRE, rise_m=rise), 5.0)
    assert st is not None
    assert st.kind == ("on_box" if abs(rise - 1.0) <= 0.3 else "beside")
    assert st.see_over == ((1,) if st.kind == "on_box" else ())


def test_a_height_that_is_not_steady_is_not_on_box_even_when_its_median_is_at_the_top():
    zig = path(*[(0.25 * k, *CENTRE, 0.8 if k % 2 else 1.2) for k in range(41)])    # range 0.4 m over any 0.5 s
    st = stance(box_hall_tops(), zig, 8.0)
    assert st is not None and st.kind == "beside"


def test_a_tops_toy_with_no_rows_is_never_on_box():
    geo = box_hall_no_tops()
    assert geo.heights is not None and not geo.box_tops
    st = stance(geo, on_box_track(*CENTRE), 5.0)
    assert st is not None and st.kind == "beside"


def test_a_flat_map_with_no_heights_is_never_on_box_even_with_a_ground_player_for_reference():
    geo = box_hall()
    assert geo.heights is None
    ground = {5: ("B", [(0.0, 236.0, 196.0, 0, 0.0), (T_END, 236.0, 196.0, 0, 0.0)])}
    st = stance(geo, on_box_track(*CENTRE), 5.0, ground)
    assert st is not None and st.kind == "beside" and st.see_over == ()


def test_an_in_outline_on_box_anchor_is_a_walkable_node_beside_the_box():
    geo = box_hall_tops()
    st = stance(geo, on_box_track(*CENTRE), 5.0)
    assert st.kind == "on_box" and st.anchor is not None and geo.walk.ravel()[st.anchor]
    assert st.anchor != geo.node_at_px(*st.px)           # the box cells are never walkable


def test_steady_on_a_three_boxes_piece_names_that_piece():
    geo = three_boxes_tops()
    for name, (x0, y0, x1, y1) in THREE_BOXES.items():
        st = stance(geo, on_box_track((x0 + x1) / 2, (y0 + y1) / 2), 5.0)
        assert st.kind == "on_box" and st.piece == labels(geo)[name] and labels(geo)[name] in st.see_over


# ------------------------------------------------------------------ off the outline, within reach

def test_off_the_outline_after_a_stay_that_entered_it_is_on_box_with_the_node_under_the_pixel():
    geo = box_hall_tops()
    st = stance(geo, stay_then(244.0, 196.0), 8.0)
    assert st is not None and st.kind == "on_box" and st.see_over == (1,)
    assert int(geo.cover_px[int(st.px[1]), int(st.px[0])]) == 0, "the pixel is off the outline"
    assert st.anchor == geo.node_at_px(float(st.px[0]), float(st.px[1])) == BOX_RING["W"]


def test_the_reach_edge_is_36_px_from_the_outline():
    geo = box_hall_tops()
    near = stance(geo, stay_then(212.5, 196.0), 8.0)            # 35.5 px west of the outline
    assert near is not None and near.kind == "on_box"
    assert stance(geo, stay_then(211.5, 196.0), 8.0) is None     # 36.5 px: beyond TOP_REACH_PX


def test_beyond_the_reach_a_stay_at_the_top_height_is_not_on_box():
    assert stance(box_hall_tops(), stay_then(200.0, 196.0), 8.0) is None


def test_a_ground_player_at_the_top_height_who_never_entered_the_outline_is_not_on_box():
    geo = box_hall_tops()
    ground_at_top = path((0.0, 244.0, 196.0, 1.0), (T_END, 244.0, 196.0, 1.0))
    assert stance(geo, ground_at_top, 8.0) is None
    walked_up = path((0.0, 200.0, 196.0, 1.0), (4.0, 244.0, 196.0, 1.0), (T_END, 244.0, 196.0, 1.0))
    assert stance(geo, walked_up, 8.0) is None


def test_a_player_beside_the_box_at_ground_height_is_none_in_the_reach_band():
    geo = box_hall_tops()
    assert stance(geo, path((0.0, 244.0, 196.0, 0.0), (T_END, 244.0, 196.0, 0.0)), 8.0) is None


def test_a_stay_that_dropped_below_the_top_before_the_player_left_is_not_a_stay():
    geo = box_hall_tops()
    dipped = stay_then(244.0, 196.0, via=[(3.5, 250.0, 196.0, 0.0), (4.5, 244.0, 196.0, 0.0)])
    assert stance(geo, dipped, 8.0) is None


def test_a_teleport_off_the_outline_at_top_height_starts_a_new_stay():
    from control_toys import height_blob
    from app.control import stance as st_mod
    from app.control.engine import RoundInputs

    geo = box_hall_tops()
    blob = height_blob({0: ("A", [(0.0, *CENTRE, 0, 1.0), (T_END, *CENTRE, 0, 1.0)])}, t_end=T_END)
    blob["tracks"]["0"] = (z_track([(0.0, *CENTRE, 0, 1.0), (2.9, *CENTRE, 0, 1.0)], 0.0, 2.9)
                           + z_track([(3.0, 244.0, 196.0, 0, 1.0), (T_END, 244.0, 196.0, 0, 1.0)], 3.0, T_END))
    assert st_mod.stance_at(RoundInputs(blob, geo, None, None), 0, 8.0) is None


# ------------------------------------------------------------------ two pieces in reach

def test_the_one_stayed_on_wins_over_a_nearer_piece_that_was_never_entered():
    geo = three_boxes_tops()
    lab = labels(geo)
    a = THREE_BOXES["A"]
    start = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    # (182, 205): box C's edge (x 192) is 10 px away, box A's (x 168) is 14: C is nearer but was never stood on
    walk = path((0.0, *start, 1.0), (3.0, *start, 1.0), (5.0, 182.0, 205.0, 1.0), (T_END, 182.0, 205.0, 1.0))
    st = stance(geo, walk, 8.0)
    assert st is not None and st.kind == "on_box" and st.piece == lab["A"] != lab["C"]


@pytest.mark.parametrize("end,nearest", [((178.0, 205.0), "A"), ((182.0, 205.0), "C")])
def test_with_a_stay_on_both_the_nearest_wins(end, nearest):
    geo = three_boxes_tops()
    lab = labels(geo)
    a, c = THREE_BOXES["A"], THREE_BOXES["C"]
    pa = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    pc = ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)
    walk = path((0.0, *pa, 1.0), (2.0, *pa, 1.0), (3.5, *pc, 1.0), (5.0, *pc, 1.0), (6.5, *end, 1.0),
                (T_END, *end, 1.0))
    st = stance(geo, walk, 9.0)
    assert st is not None and st.kind == "on_box" and st.piece == lab[nearest]


# ------------------------------------------------------------------ the guards

def test_boxed_stance_asks_for_a_stance_on_an_off_outline_pixel_in_reach():
    from app.control import stance as st_mod

    geo = box_hall_tops()
    rnd = round_of(geo, {0: stay_then(244.0, 196.0)})
    st = st_mod.boxed_stance(rnd, 0, 8.0)
    assert st is not None and st.kind == "on_box"


def test_boxed_stance_asks_for_a_stance_on_a_reach_pixel_even_when_the_answer_is_none():
    from app.control import stance as st_mod

    geo = box_hall_tops()
    rnd = round_of(geo, {0: path((0.0, 244.0, 196.0, 0.0), (T_END, 244.0, 196.0, 0.0))})
    before = st_mod.CALLS
    assert st_mod.boxed_stance(rnd, 0, 8.0) is None
    assert st_mod.CALLS == before + 1


def test_boxed_stance_does_not_ask_beyond_the_reach():
    from app.control import stance as st_mod

    geo = box_hall_tops()
    rnd = round_of(geo, {0: path((0.0, 200.0, 196.0, 0.0), (T_END, 200.0, 196.0, 0.0))})
    before = st_mod.CALLS
    assert st_mod.boxed_stance(rnd, 0, 8.0) is None
    assert st_mod.CALLS == before


def test_the_engine_holds_an_off_outline_on_box_player_at_the_node_under_the_pixel():
    from app.control import engine as ce

    geo = box_hall_tops()
    rnd = round_of(geo, {0: stay_then(244.0, 196.0)})
    tk = ce.Tick(rnd, 8.0)
    st = tk.holders[0].stance
    assert st is not None and st.kind == "on_box"
    assert tk.holders[0].cell == BOX_RING["W"] == 24 * GRID + 30

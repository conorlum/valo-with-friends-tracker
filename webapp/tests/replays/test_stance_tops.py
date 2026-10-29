"""On a learnt box top when steady within its crouch window, above its ring floor by at least BOX_STEP_M,
inside its outline or within TOP_REACH_M of it. No earlier entry into the outline is needed.

Toys: box_hall_tops / box_hall_no_tops / three_boxes_tops
(control_toys.py), z in metres above the ground (0.0 m), the box's learnt top 1.0 m."""

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import (BOX_PX, BOX_RING, GRID, THREE_BOXES, box_hall, box_hall_no_tops, box_hall_tops,  # noqa: E402
                          on_box_track, three_boxes_tops, tops_hall, z_track)
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
    1.0, 1.25, 0.0, 0.8, 0.4, 1.3,
    pytest.param(0.5, id="0.5"),
    pytest.param(1.35, id="1.35"),
    pytest.param(2.0, id="2.0"),
])
def test_only_a_steady_height_within_the_tolerance_of_the_top_is_on_box(rise):
    st = stance(box_hall_tops(), on_box_track(*CENTRE, rise_m=rise), 5.0)
    assert st is not None
    assert st.kind == ("on_box" if 0.5 <= rise <= 1.3 else "beside")
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

def test_off_the_outline_without_entry_is_on_box_with_the_node_under_the_pixel():
    geo = box_hall_tops()
    st = stance(geo, on_box_track(244.0, 196.0), 8.0)
    assert st is not None and st.kind == "on_box" and st.see_over == (1,)
    assert int(geo.cover_px[int(st.px[1]), int(st.px[0])]) == 0, "the pixel is off the outline"
    assert st.anchor == geo.node_at_px(float(st.px[0]), float(st.px[1])) == BOX_RING["W"]


@pytest.mark.parametrize("m_per_px", [0.125, 0.05])
def test_reach_in_metres_is_on_at_07_and_off_at_08_without_entry(m_per_px):
    # Two raster scales whose pixels distinguish 0.7 m from 0.8 m; no shared-sha cache for a rescaled toy.
    geo = dataclasses.replace(box_hall_tops(), m_per_px=m_per_px, height_sha=None)
    x0, y = BOX_PX[0], CENTRE[1]
    near = stance(geo, on_box_track(x0 - 0.7 / geo.m_per_px, y), 8.0)
    assert near is not None and near.kind == "on_box"
    assert stance(geo, on_box_track(x0 - 0.8 / geo.m_per_px, y), 8.0) is None


def test_beyond_the_reach_a_stay_at_the_top_height_is_not_on_box():
    assert stance(box_hall_tops(), stay_then(200.0, 196.0), 8.0) is None


def test_a_player_walking_up_at_top_height_without_entry_is_on_box():
    geo = box_hall_tops()
    walked_up = path((0.0, 200.0, 196.0, 1.0), (4.0, 244.0, 196.0, 1.0), (T_END, 244.0, 196.0, 1.0))
    assert stance(geo, walked_up, 8.0).kind == "on_box"


def test_a_ground_stander_in_a_low_tops_crouch_window_is_below_the_ring_floor_step():
    geo = box_hall_tops(rise_m=0.5)
    for xy in (CENTRE, (244.0, 196.0)):
        st = stance(geo, on_box_track(*xy, rise_m=0.2), 8.0)
        if xy == CENTRE:
            assert st is not None and st.kind == "beside"
        else:
            assert st is None
    assert stance(geo, on_box_track(*CENTRE, rise_m=0.5), 8.0).kind == "on_box"


def test_a_piece_without_a_ring_floor_never_matches():
    from app.control.geometry import attach_heights

    geo = dataclasses.replace(box_hall_tops())
    asset = dataclasses.replace(geo.heights, floors=np.full_like(geo.heights.floors, -1))
    attach_heights(geo, asset)
    assert geo.box_tops
    assert stance(geo, on_box_track(*CENTRE), 8.0).kind == "beside"


@pytest.mark.parametrize("tops, expected", [((1.0, 1.6), 1.0), ((1.0, 1.5), 1.5)])
def test_nearest_matching_top_wins_with_a_lower_top_on_a_tie(tops, expected):
    from app.control import stance as st_mod

    rnd = round_of(box_hall_tops(), {0: on_box_track(*CENTRE, rise_m=1.3)})
    assert st_mod._top_match(rnd, 0, rnd._sample(0, 8.0), tops, 0.0) == expected


def test_a_player_beside_the_box_at_ground_height_is_none_in_the_reach_band():
    geo = box_hall_tops()
    assert stance(geo, path((0.0, 244.0, 196.0, 0.0), (T_END, 244.0, 196.0, 0.0)), 8.0) is None


def test_a_previous_drop_does_not_prevent_a_new_steady_window_in_reach():
    geo = box_hall_tops()
    dipped = stay_then(244.0, 196.0, via=[(3.5, 250.0, 196.0, 0.0), (4.5, 244.0, 196.0, 0.0)])
    assert stance(geo, dipped, 8.0).kind == "on_box"


def test_a_teleport_off_the_outline_at_top_height_needs_only_a_new_steady_window():
    from control_toys import height_blob
    from app.control import stance as st_mod
    from app.control.engine import RoundInputs

    geo = box_hall_tops()
    blob = height_blob({0: ("A", [(0.0, *CENTRE, 0, 1.0), (T_END, *CENTRE, 0, 1.0)])}, t_end=T_END)
    blob["tracks"]["0"] = (z_track([(0.0, *CENTRE, 0, 1.0), (2.9, *CENTRE, 0, 1.0)], 0.0, 2.9)
                           + z_track([(3.0, 244.0, 196.0, 0, 1.0), (T_END, 244.0, 196.0, 0, 1.0)], 3.0, T_END))
    rnd = RoundInputs(blob, geo, None, None)
    assert st_mod.stance_at(rnd, 0, 3.2) is None
    assert st_mod.stance_at(rnd, 0, 3.6).kind == "on_box"


# ------------------------------------------------------------------ two pieces in reach

def close_boxes(rises=(1.0, 1.0)):
    return tops_hall("ReachPair", [(144, 184, 168, 208), (172, 184, 196, 208)], rises=rises)


@pytest.mark.parametrize("x,nearest", [(169.5, 1), (170.5, 2)])
def test_the_nearest_matching_piece_wins_without_entry(x, nearest):
    # Pixel centres stay in the intended raster column after replay-coordinate quantisation.
    st = stance(close_boxes(), on_box_track(x, 196.0), 8.0)
    assert st is not None and st.kind == "on_box" and st.piece == nearest


def test_a_pixel_on_a_can_match_bs_top_in_reach():
    geo = close_boxes(rises=(1.0, 2.0))
    st = stance(geo, on_box_track(166.5, 196.0, rise_m=2.0), 8.0)
    assert geo.cover_px[196, 166] == 1
    assert st.kind == "on_box" and st.piece == 2
    assert st.anchor == geo.node_at_px(*st.px)


def test_the_piece_under_the_pixel_matches_before_a_neighbour():
    geo = close_boxes()
    st = stance(geo, on_box_track(166.5, 196.0), 8.0)
    assert st.kind == "on_box" and st.piece == 1


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

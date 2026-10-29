"""The vantage rule (R6, D-E): a player on a box sees over every lower box whose top is known. The eye is the matched
top of the box stood on (position z) + EYE_M; a box Q is lower when its highest learnt top less STAND_M is strictly
under that eye. Boxes with no learnt top block. Lines take the union of their two ends' sets.

Toys: three_boxes_tops (A, B, C all topped at 1.0 m), a variant with B at 3.0 m, a variant where B has no rows."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import THREE_BOXES, on_box_track, three_boxes_tops, tops_hall  # noqa: E402
from test_stance import ANGLES, stance  # noqa: E402
from test_stance_engine import still, tick  # noqa: E402

ON_A = (156.0, 190.0)        # on box A, y 190: the row through B (y 184-208) and clear of C (y 196-220)
BEHIND_B = (290.0, 190.0)    # ground east of B
BESIDE_A = (176.0, 190.0)    # ground just east of A


def three(rises, name):
    return tops_hall(name, list(THREE_BOXES.values()), rises=rises)


def high_b():
    return three([1.0, 3.0, 1.0], "ThreeBoxesHighB")


def bare_b():
    return three([1.0, None, 1.0], "ThreeBoxesBareB")


def labels(geo):
    return {name: int(geo.cover_px[(r[1] + r[3]) // 2, (r[0] + r[2]) // 2]) for name, r in THREE_BOXES.items()}


def on_a(geo):
    return stance(geo, on_box_track(*ON_A), 5.0)


def clear(geo, start, end, over):
    from app.control import geometry as cg

    return cg.los(geo, (*start, None), (*end, None), [], see_over=over)


# ------------------------------------------------------------------ the normaliser

@pytest.mark.parametrize("given,expected", [
    (0, ()), ((0, 0), ()), ((), ()), (3, (3,)), ((2, 2), (2,)), ((3, 1), (1, 3)), ((3, 1, 2), (1, 2, 3)),
    (((2, 1), (3,)), (1, 2, 3)), (((), (4,)), (4,)), (((1, 3), ()), (1, 3)), (((0, 2), (2, 0)), (2,)), ([5, 0], (5,)),
])
def test_see_over_labels_gives_one_sorted_tuple_of_the_non_zero_labels(given, expected):
    from app.control import geometry as cg

    assert cg.see_over_labels(given) == expected


def test_sight_without_nothing_is_the_geometrys_own_sight():
    from app.control import geometry as cg

    geo = three_boxes_tops()
    for none in (0, (), (0, 0)):
        assert cg.sight_without(geo, none) is geo.sight
    assert cg.sight_without(geo) is geo.sight


def test_sight_without_takes_ints_pairs_and_tuples_alike():
    from app.control import geometry as cg

    geo = three_boxes_tops()
    lab = labels(geo)
    want = cg.sight_without(geo, lab["A"], lab["B"])
    for form in (((lab["B"], lab["A"]),), ((lab["A"],), (lab["B"],)), (((lab["A"],), (lab["B"], 0)),)):
        assert (cg.sight_without(geo, *form) == want).all()
    assert not (want == geo.sight).all()


# ------------------------------------------------------------------ the vantage set

def fake_geo(tops):
    return SimpleNamespace(box_tops=tops)


def test_vantage_is_the_own_piece_and_every_lower_topped_piece():
    from app.control import stance as st_mod

    geo = fake_geo({1: (1.0,), 2: (1.0, 1.4), 3: (1.0,)})
    assert st_mod.vantage(geo, 1, 1.0) == (1, 2, 3)


def test_vantage_leaves_out_a_piece_whose_surface_reaches_the_eye():
    from app.control import stance as st_mod
    from app.control import heights as hc

    # on piece 1 at 1.0: eye 2.1. Piece 2's top 2.0 gives a surface of 1.1 (lower: in); 3.0 gives 2.1 (out)
    assert st_mod.vantage(fake_geo({1: (1.0,), 2: (2.0,)}), 1, 1.0) == (1, 2), "a top above the eye, a surface below it"
    assert st_mod.vantage(fake_geo({1: (1.0,), 2: (3.0,)}), 1, 1.0) == (1,)
    # the highest top of a piece counts, not its lowest
    assert st_mod.vantage(fake_geo({1: (1.0,), 2: (1.0, 3.0)}), 1, 1.0) == (1,)
    # surface exactly at the eye is not strictly below it
    z, top = 1.5, 3.5
    assert top - hc.STAND_M == z + hc.EYE_M
    assert st_mod.vantage(fake_geo({1: (z,), 2: (top,)}), 1, z) == (1,)
    assert st_mod.vantage(fake_geo({1: (z,), 2: (float(np.nextafter(top, 0)),)}), 1, z) == (1, 2)


def test_vantage_never_adds_a_piece_with_no_known_top():
    from app.control import stance as st_mod

    assert st_mod.vantage(fake_geo({1: (1.0,), 2: (), 3: (0.5,)}), 1, 1.0) == (1, 3)
    assert st_mod.vantage(fake_geo({1: (1.0,)}), 1, 1.0) == (1,)


def test_vantage_is_cached_on_the_geometry_per_piece_and_height():
    from app.control import stance as st_mod

    geo = fake_geo({1: (1.0,), 2: (1.0,)})
    first = st_mod.vantage(geo, 1, 1.0)
    geo.box_tops[2] = (9.0,)
    assert st_mod.vantage(geo, 1, 1.0) == first == (1, 2)
    assert st_mod.vantage(geo, 1, 1.5) == (1,), "a new height is a new key"


def test_a_player_on_a_box_has_that_box_and_the_other_topped_lower_ones():
    geo = three_boxes_tops()
    st = on_a(geo)
    lab = labels(geo)
    assert st.kind == "on_box" and st.piece == lab["A"]
    assert st.see_over == tuple(sorted(lab.values()))


def test_a_beside_stance_has_no_see_over():
    geo = three_boxes_tops()
    st = stance(geo, (("A", [(0.0, 156.0, 196.0, 0, 0.0), (10.0, 156.0, 196.0, 0, 0.0)])), 5.0)
    assert st is not None and st.kind == "beside" and st.see_over == () and st.piece == 0


def test_a_higher_piece_is_not_in_the_set_and_a_piece_with_no_top_is_not_either():
    lab_high = labels(high_b())
    st = on_a(high_b())
    assert st.see_over == tuple(sorted((lab_high["A"], lab_high["C"])))
    lab_bare = labels(bare_b())
    st = on_a(bare_b())
    assert st.see_over == tuple(sorted((lab_bare["A"], lab_bare["C"])))


# ------------------------------------------------------------------ sight over them

def test_from_on_a_a_point_behind_b_is_clear_and_from_beside_a_it_is_blocked():
    from app.control import geometry as cg

    geo = three_boxes_tops()
    st = on_a(geo)
    assert clear(geo, ON_A, BEHIND_B, (st.see_over, ()))
    assert not clear(geo, BESIDE_A, BEHIND_B, ((), ()))
    assert not clear(geo, ON_A, BEHIND_B, (st.piece, 0)), "the own piece alone does not clear B"
    seen = cg.cast(geo, *ON_A, ANGLES, [], see_over=st.see_over)
    ground = cg.cast(geo, *BESIDE_A, ANGLES, [], see_over=())
    cell = geo.node_at_px(*BEHIND_B)
    assert seen[cell] and not ground[cell]


def test_a_line_takes_the_union_of_its_two_ends():
    geo = three_boxes_tops()
    st = on_a(geo)
    assert clear(geo, ON_A, BEHIND_B, (st.see_over, ()))
    assert clear(geo, ON_A, BEHIND_B, ((), st.see_over))
    assert clear(geo, ON_A, BEHIND_B, ((st.piece,), (labels(geo)["B"],)))
    assert clear(geo, BEHIND_B, ON_A, ((), st.see_over))


def test_a_higher_piece_still_blocks_from_on_a():
    geo = high_b()
    st = on_a(geo)
    assert not clear(geo, ON_A, BEHIND_B, (st.see_over, ()))


def test_a_piece_with_no_learnt_top_blocks_from_on_a():
    geo = bare_b()
    assert not geo.box_tops.get(labels(geo)["B"])
    st = on_a(geo)
    assert not clear(geo, ON_A, BEHIND_B, (st.see_over, ()))


# ------------------------------------------------------------------ the engine

def test_the_engine_tick_from_on_a_sees_the_cell_behind_b_and_a_ground_player_beside_a_does_not():
    geo = three_boxes_tops()
    cell = geo.node_at_px(*BEHIND_B)
    up = tick(geo, {0: on_box_track(*ON_A, yaw=0)})
    down = tick(geo, {0: still("A", *BESIDE_A, 0)})
    assert up.view[0][cell]
    assert not down.view[0][cell]


def test_the_engine_tick_from_on_a_does_not_see_behind_a_higher_b():
    geo = high_b()
    cell = geo.node_at_px(*BEHIND_B)
    assert not tick(geo, {0: on_box_track(*ON_A, yaw=0)}).view[0][cell]


def test_a_holder_on_a_sees_an_enemy_on_b_past_c():
    geo = three_boxes_tops()
    lab = labels(geo)
    viewer, enemy = (156.0, 206.0), (252.0, 206.0)       # y 206: A, C and B all hold it
    players = {5: on_box_track(*viewer, side="B", yaw=0), 0: on_box_track(*enemy, side="A", yaw=180)}
    tk = tick(geo, players)
    assert tk.holders[5].stance.piece == lab["A"] and tk.holders[0].stance.piece == lab["B"]
    assert 0 in tk.sees[5]

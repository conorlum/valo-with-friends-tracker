"""Standing on a box at the engine and its consumers (plan Stage 3, red first): the on-box viewer's 360 degree
view, who sees a player on a box, and every consumer of "can A see B" agreeing with the engine for an on-box viewer.

Tests marked xfail(strict) pin the rule that is not built yet (app/control/stance.py is an inert stub and nothing
calls it); each fails with an AssertionError today. Unmarked tests are controls: behaviour that already holds and
must keep holding. Helpers come from test_stance.py and control_toys.py. Angles: 0 = +x (east), 90 = +y (south).
"""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import (BOX_PX, BOX_RING, BOX_WITNESS, GRID, HALL, TOY_SCALE, _CACHE, _DIR,  # noqa: E402
                          geometry_from_masks, height_blob, hugging_track, on_box_track, rect_mask, box_hall,
                          visibility)
from test_stance import ANGLES, CENTRE, red, ring_ref, ring_xy, stance  # noqa: E402

SIDES = ["N", "S", "E", "W"]
FACING = {"E": 0, "S": 90, "W": 180, "N": 270}
T = 5.0


def centre_of(geo, cell):
    x, y = geo.centres[cell]
    return float(x), float(y)


def bearing(a, b):
    """The yaw (whole degrees) that faces b from a."""
    return int(round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))) % 360


def still(side, x, y, yaw, z=0.0):
    return side, [(0.0, x, y, yaw, z), (10.0, x, y, yaw, z)]


def inputs(geo, players, util=(), deaths=None, with_ref=True):
    """(rnd, blob) of a round: `players` = {slot: (side, z points)}, plus the ring's z reference (slot 6)."""
    from app.control.engine import RoundInputs

    players = dict(players)
    if with_ref:
        players[6] = ring_ref(geo, 6)[1]
    blob = height_blob(players, t_end=10.0, util=list(util), deaths=deaths)
    return RoundInputs(blob, geo, None, None), blob


def tick(geo, players, util=(), smokes=None, t=T):
    from app.control import engine as ce

    rnd, _ = inputs(geo, players, util)
    if smokes is not None:
        rnd.smokes_at = lambda _t: list(smokes)
    return ce.Tick(rnd, t)


# ------------------------------------------------------------------ 1. the viewer's own view

def on_box(yaw):
    return on_box_track(*CENTRE, yaw=yaw)


@red
@pytest.mark.parametrize("side", SIDES)
def test_an_on_box_viewer_has_the_faced_side_witness_in_their_view(side):
    opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}[side]
    tk = tick(box_hall(), {0: on_box(FACING[side])})
    assert tk.view[0][BOX_WITNESS[side]]
    assert not tk.view[0][BOX_WITNESS[opposite]], "behind the viewer is outside the field of view"


@red
@pytest.mark.parametrize("side", SIDES)
def test_an_on_box_viewer_is_held_at_their_anchor_node(side):
    from app.control import stance as st_mod

    geo = box_hall()
    rnd, _ = inputs(geo, {0: on_box(FACING[side])})
    st = st_mod.stance_at(rnd, 0, T)
    assert st is not None and st.kind == "on_box" and st.anchor is not None
    from app.control import engine as ce

    h = ce.Tick(rnd, T).holders[0]
    assert h.cell == st.anchor and geo.walk.ravel()[h.cell]


@red
def test_a_hugger_in_the_piece_is_held_at_the_anchor_and_sees_no_further_than_the_ground_does():
    geo = box_hall()
    rnd, _ = inputs(geo, {0: hugging_track(250, 196, yaw=0)})        # inside the west edge, facing east
    from app.control import engine as ce
    from app.control import stance as st_mod

    st = st_mod.stance_at(rnd, 0, T)
    assert st is not None and st.kind == "beside" and st.see_over == 0
    tk = ce.Tick(rnd, T)
    assert tk.holders[0].cell == st.anchor
    assert not tk.view[0][BOX_WITNESS["E"]], "beside the box, the box is in the way"


def test_control_a_ground_player_beside_the_box_does_not_see_through_it():
    geo = box_hall()
    x, y = ring_xy(geo, "W")
    tk = tick(geo, {0: still("A", x, y, 0)})
    assert not tk.view[0][BOX_WITNESS["E"]]


# ------------------------------------------------------------------ 2. being seen

def walled_box_hall():
    """box_hall plus a real wall strip just west of the box (x 236-244, y 188-208): from the west, the box's top is
    behind it, while the ring cell north of the box (y 180) is still in plain sight."""
    key = ("WalledBoxHall", HALL, BOX_PX)
    if key not in _CACHE:
        from app.control.geometry import attach_cover
        import hashlib

        floor, box, wall = rect_mask([HALL]), rect_mask([BOX_PX]), rect_mask([(236, 188, 244, 208)])
        geo = geometry_from_masks("WalledBoxHall", ~floor | box | wall, floor & ~box & ~wall, TOY_SCALE, None)
        attach_cover(geo, box, hashlib.sha256(np.packbits(box).tobytes()).hexdigest()[:12])
        visibility(geo, _DIR)
        _CACHE[key] = geo
    return _CACHE[key]


def seen_by_enemy(geo, enemy_xy, enemy_yaw=None, target=CENTRE, util=(), smokes=None):
    """Whether the enemy (slot 5) sees the player on the box (slot 0) at T."""
    yaw = bearing(enemy_xy, target) if enemy_yaw is None else enemy_yaw
    players = {0: on_box_track(*target, yaw=0), 5: still("B", *enemy_xy, yaw)}
    return 0 in tick(geo, players, util, smokes).sees[5]


@red
@pytest.mark.parametrize("side", SIDES)
def test_an_enemy_at_a_witness_cell_sees_the_player_on_the_box(side):
    geo = box_hall()
    assert seen_by_enemy(geo, centre_of(geo, BOX_WITNESS[side]))


def test_an_enemy_behind_a_real_wall_does_not_see_the_player_whose_anchor_cell_it_sees():
    geo = walled_box_hall()
    from app.control import engine as ce

    enemy = (200.0, 196.0)
    north_ring = centre_of(geo, BOX_RING["N"])
    assert ce.los(geo, (*enemy, None), (*north_ring, None)), "the north ring cell is in the enemy's sight"
    assert not ce.los(geo, (*enemy, None), (*CENTRE, None), see_over=(0, 1)), "the box's top is behind the wall"
    from app.control import stance as st_mod

    rnd, _ = inputs(geo, {0: on_box(0), 5: still("B", *enemy, 0)})
    assert st_mod.stance_at(rnd, 0, T) is not None
    assert not seen_by_enemy(geo, enemy, 0)


def test_control_the_player_on_the_box_behind_the_wall_is_not_seen_today_and_must_stay_so():
    assert not seen_by_enemy(walled_box_hall(), (200.0, 196.0), 0)


def test_control_a_ground_player_behind_the_same_wall_is_not_seen():
    geo = walled_box_hall()
    players = {0: still("A", 244.0, 196.0, 0), 5: still("B", 200.0, 196.0, 0)}
    assert 0 not in tick(geo, players).sees[5]


@red
def test_the_player_on_the_box_just_inside_the_enemys_field_of_view_is_seen():
    from app.control import engine as ce

    geo = box_hall()
    e = centre_of(geo, BOX_WITNESS["E"])
    assert seen_by_enemy(geo, e, (180 + int(ce.FOV_HALF) - 3) % 360)


def test_control_the_player_on_the_box_just_outside_the_enemys_field_of_view_is_not_seen():
    from app.control import engine as ce

    geo = box_hall()
    e = centre_of(geo, BOX_WITNESS["E"])
    assert not seen_by_enemy(geo, e, (180 + int(ce.FOV_HALF) + 4) % 360)


def flash_row(target, t, dur):
    return {"k": "flash", "t": t - 0.3, "by": 0, "ability": "phoenix_curveball_left", "targets": [target],
            "hits": [[target, t, dur]]}


def nearsight_row(target, t):
    return {"k": "nearsight", "t": t, "by": 0, "ability": "omen_paranoia", "targets": [target],
            "hits": [[target, t, None]]}


def test_control_a_flashed_enemy_does_not_see_the_player_on_the_box():
    geo = box_hall()
    assert not seen_by_enemy(geo, centre_of(geo, BOX_WITNESS["E"]), util=[flash_row(5, 1.0, 8.0)])


@red
def test_a_nearsighted_enemy_within_the_bubble_sees_the_player_on_the_box():
    geo = box_hall()
    near = centre_of(geo, BOX_WITNESS["E"])                     # 40 px = 5.6 m from the box centre
    assert seen_by_enemy(geo, near, util=[nearsight_row(5, 4.9)])


def test_control_a_nearsighted_enemy_beyond_the_bubble_does_not_see_the_player_on_the_box():
    geo = box_hall()
    assert not seen_by_enemy(geo, (330.0, 196.0), util=[nearsight_row(5, 4.9)])      # 70 px = 9.8 m


def test_control_a_smoke_on_the_line_hides_the_player_on_the_box():
    geo = box_hall()
    assert not seen_by_enemy(geo, centre_of(geo, BOX_WITNESS["E"]), smokes=[(284.0, 196.0, 8.0, True)])


# ------------------------------------------------------------------ 3. every consumer agrees

def answers(geo, viewer_xy, viewer_z, target_xy, tmp_path, monkeypatch, with_ref=True):
    """Every consumer's yes/no for 'does the viewer (slot 0) see the enemy (slot 5) standing at target_xy', the
    viewer facing it: the engine's view, the gap detector's _sight from the engine's PlayerView, the same from the
    cached replay, _sees_point, Critical Util's smoke-cover line, and Critical Moments' bare killer->victim line."""
    from app.control import engine as ce
    from app.control.utility import _sees_point
    from app.critutil import extract
    from app.gaps import cache
    from app.gaps import detect as gd
    from app.moments import fights

    toward = bearing(viewer_xy, target_xy)
    away = (toward + 180) % 360

    def players(viewer_yaw, enemy_yaw):
        return {0: ("A", [(0.0, *viewer_xy, viewer_yaw, viewer_z), (10.0, *viewer_xy, viewer_yaw, viewer_z)]),
                5: still("B", *target_xy, enemy_yaw)}

    rnd, blob = inputs(geo, players(toward, away), with_ref=with_ref)
    cell = (int(target_xy[1]) // 8) * GRID + int(target_xy[0]) // 8
    seen = {}
    records, writer = {}, cache.Writer(cache.cache_path(1, 1, "stance", tmp_path))

    def observe(rec, unk):
        if abs(rec.t - T) < 1e-6:
            records["live"] = rec
        writer(rec, unk)

    rc = ce.compute_round(blob, geo, None, observer=observe, knowledge=False)
    writer.close(rc.missing_inputs)
    live = records["live"]
    seen["engine view"] = bool(live.players[0].view[cell])

    def gap_sight(pv, smokes):
        det = gd.GapDetector(geo, ce.RoundInputs(blob, geo, None, None))
        if getattr(pv, "see_over", 0):
            out = det._sight(pv.eye_x, pv.eye_y, pv.eye_z, pv.node, smokes, see_over=pv.see_over)
        else:
            out = det._sight(pv.x, pv.y, pv.eye_z, pv.node, smokes)
        return bool(out[cell])

    seen["gap detector"] = gap_sight(live.players[0], live.smokes)
    cached = next(rec for rec, _ in cache.replay(writer.path) if abs(rec.t - T) < 1e-6)
    for field in ("eye_x", "eye_y", "see_over"):
        assert getattr(cached.players[0], field, None) == getattr(live.players[0], field, None), \
            f"the tick cache drops PlayerView.{field}"
    seen["cached gap replay"] = gap_sight(cached.players[0], cached.smokes)
    seen["_sees_point"] = bool(_sees_point(rnd, 0, T, *target_xy))

    # Critical Util: a smoke on the line, cast by the viewer's side; the line is counted iff the bare line is clear
    smoke = (viewer_xy[0] + 0.7 * (target_xy[0] - viewer_xy[0]), viewer_xy[1] + 0.7 * (target_xy[1] - viewer_xy[1]),
             8.0, True)
    monkeypatch.setattr(extract, "_shapes_of", lambda *_a: [(0.0, 10.0, smoke)])
    cast = SimpleNamespace(i=0, by=0, t=0.0, t_end=10.0)
    out = extract._cover_all(rnd, {0: "A", 5: "B", 6: "B"}, [cast])
    seen["util smoke line"] = out[0]["pairs"] > 0

    # Critical Moments: neither is facing the other, so "measurable" is only the bare killer->victim line
    frnd, fblob = inputs(geo, players(away, toward), deaths={5: 6.0}, with_ref=with_ref,
                         util=[{"k": "damage", "src": "gun", "by": 0, "target": 5, "t": 5.5}])
    seen["fights killer->victim"] = bool(fights._kd(frnd, fblob, 6.0, 0, 5, True)["measurable"])
    return seen


@red
@pytest.mark.parametrize("side", SIDES)
def test_every_consumer_sees_a_ground_enemy_from_a_player_on_the_box(side, tmp_path, monkeypatch):
    geo = box_hall()
    got = answers(geo, CENTRE, 1.0, centre_of(geo, BOX_WITNESS[side]), tmp_path, monkeypatch)
    assert got == {name: True for name in got}


@pytest.mark.parametrize("side,expected", [("W", True), ("E", False)])
def test_control_every_consumer_agrees_for_a_ground_viewer_at_a_ring_cell(side, expected, tmp_path, monkeypatch):
    geo = box_hall()
    got = answers(geo, ring_xy(geo, "W"), 0.0, centre_of(geo, BOX_WITNESS[side]), tmp_path, monkeypatch,
                  with_ref=False)
    assert got == {name: expected for name in got}


# ------------------------------------------------------------------ 4. the stance's z rules on the flat toy
# A heights toy that also carries a cover piece needs the height build plus attach_cover on top of it (a 40 s
# visibility build and over 40 lines), so these run on the flat box_hall: its floors are all unresolved (NaN), which
# is the ring-median branch; the known-floor and four-value cases are in test_stance.py.

def test_the_ring_median_sets_the_floor_where_the_map_has_none():
    geo = box_hall()
    extra = {6: still("B", 284.0, 196.0, 0, 0.2), 7: still("B", 260.0, 172.0, 0, 0.0)}   # 12 px from the box
    st = stance(geo, on_box_track(*CENTRE), T, extra)     # rise 1.0 m over a median of 0.0..0.2 m
    assert st is not None and st.kind == "on_box"


def test_a_player_in_the_piece_with_no_z_while_the_others_have_it_is_beside_and_sees_no_further():
    geo = box_hall()
    tk = tick(geo, {0: ("A", [(0.0, 250.0, 196.0, 0), (10.0, 250.0, 196.0, 0)]),
                    6: ring_ref(geo, 6)[1]})
    from app.control import stance as st_mod

    rnd, _ = inputs(geo, {0: ("A", [(0.0, 250.0, 196.0, 0), (10.0, 250.0, 196.0, 0)])})
    st = st_mod.stance_at(rnd, 0, T)
    assert st is not None and st.kind == "beside"
    assert not tk.view[0][BOX_WITNESS["E"]]


# ------------------------------------------------------------------ 5. Summit's five kills

SUMMIT_CASES = [
    pytest.param("403 r6 Omen", (982, 399), (949, 417), id="403-r6"),
    pytest.param("403 r9 Omen", (982, 407), (933, 401), id="403-r9"),
    # today's plain cast and line already reach this one (before-control): a control, not red
    pytest.param("403 r18 Omen victim", (463, 582), (438, 645), id="403-r18"),
    pytest.param("409 r6 Phoenix", (629, 376), (645, 531), id="409-r6"),
    pytest.param("409 r9 Veto", (498, 663), (493, 423), id="409-r9"),
]


def in_piece(geo, x, y, reach=8):
    """(x, y, label) of the pixel itself if it is in a cover piece, else the nearest in-piece pixel within `reach`."""
    best = None
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if geo.cover_px[y + dy, x + dx] != 0 and (best is None or dx * dx + dy * dy < best[0]):
                best = (dx * dx + dy * dy, x + dx, y + dy)
    assert best is not None, f"no cover piece within {reach} px of {(x, y)}"
    return best[1], best[2], int(geo.cover_px[best[2], best[1]])


@pytest.fixture(scope="module")
def summit():
    from app.control import geometry as cg

    geo = cg.load_geometry("Summit")
    cg.visibility(geo)
    return geo


@pytest.mark.slow
@pytest.mark.parametrize("name,box_px,other_px", SUMMIT_CASES)
def test_summit_the_on_box_player_sees_the_other_party_over_the_box(summit, name, box_px, other_px, capsys):
    from app.control import geometry as cg

    geo = summit
    x, y, label = in_piece(geo, *box_px)
    ox, oy = other_px
    other_label = int(geo.cover_px[oy, ox])
    cell = geo.cell_of_px(ox, oy)
    flat_cast = bool(cg.cast(geo, x, y, ANGLES, [], see_over=0)[cell])
    flat_los = bool(cg.los(geo, (x, y, None), (ox, oy, None), see_over=(0, 0)))
    with capsys.disabled():
        print(f"\n[summit] {name}: on-box px {(x, y)} (asked {box_px}) label {label}; other {other_px} label "
              f"{other_label}; today's see_over=0 cast reaches it: {flat_cast}; los: {flat_los}")
    assert cg.cast(geo, x, y, ANGLES, [], see_over=label)[cell]
    assert cg.los(geo, (x, y, None), (ox, oy, None), see_over=(label, other_label))

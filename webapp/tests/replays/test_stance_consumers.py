"""W16: Critical Util's smoke-cover line, `_sees_point` and Critical Moments' fights see from the stance (R3, D5, D7).
Toys from control_toys.py, built like test_stance_engine.py's answers()."""

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from control_toys import BOX_WITNESS, box_hall_tops, on_box_track  # noqa: E402
from test_stance import CENTRE  # noqa: E402
from test_stance_engine import T, bearing, centre_of, inputs, still  # noqa: E402


def no_cover(geo):
    """The same geometry as the engine would see it with no cover piece (no stance anywhere)."""
    g = copy.copy(geo)
    g.cover_px = None
    return g


def box_viewer_round(geo):
    """Slot 0 on the box facing east; slot 5 a ground enemy at the east witness cell, facing away from the box."""
    e = centre_of(geo, BOX_WITNESS["E"])
    players = {0: on_box_track(*CENTRE, yaw=bearing(CENTRE, e)), 5: still("B", *e, 0)}
    return inputs(geo, players), e


# ------------------------------------------------------------------ a. _sees_point

def test_sees_point_from_a_player_on_the_box_to_a_ground_point_behind_it():
    from app.control.utility import _sees_point

    geo = box_hall_tops()
    (rnd, _), e = box_viewer_round(geo)
    assert _sees_point(rnd, 0, T, *e)
    (flat, _), e = box_viewer_round(no_cover(geo))
    assert not _sees_point(flat, 0, T, *e)


def test_sees_point_with_a_target_on_the_box_uses_the_physical_line():
    from app.control.utility import _sees_point

    geo = box_hall_tops()
    w = centre_of(geo, BOX_WITNESS["W"])
    players = {0: still("A", *w, bearing(w, CENTRE)), 5: on_box_track(*CENTRE, yaw=0)}
    rnd, _ = inputs(geo, players)
    assert _sees_point(rnd, 0, T, *CENTRE, target=5)
    assert not _sees_point(rnd, 0, T, *CENTRE), "a point, not a player: today's answer"


# ------------------------------------------------------------------ b. line_ends

def test_line_ends_for_a_ground_pair_is_their_own_positions_and_asks_for_no_stance():
    from app.control import stance as st_mod

    geo = box_hall_tops()
    a, b = (190.0, 196.0), (330.0, 196.0)       # both beyond TOP_REACH_PX of the box
    rnd, _ = inputs(geo, {0: still("A", *a, 0), 5: still("B", *b, 180)})
    pa, pb = rnd.pos(0, T), rnd.pos(5, T)
    before = st_mod.CALLS
    la, lb, so = st_mod.line_ends(rnd, 0, 5, T, pa, pb)
    assert st_mod.CALLS == before
    assert la == (pa[0], pa[1], None) and lb == (pb[0], pb[1], None) and so == ((), ())


def test_line_ends_puts_an_on_box_end_at_its_eye_with_its_piece():
    from app.control import stance as st_mod

    geo = box_hall_tops()
    (rnd, _), e = box_viewer_round(geo)
    pa, pb = rnd.pos(0, T), rnd.pos(5, T)
    la, lb, so = st_mod.line_ends(rnd, 0, 5, T, pa, pb)
    assert so[0] == (1,) and so[1] == () and lb == (pb[0], pb[1], None)
    assert la[:2] == st_mod.boxed_stance(rnd, 0, T).eye_px


# ------------------------------------------------------------------ c. fights

def _kd_for(geo):
    from app.moments import fights

    e = centre_of(geo, BOX_WITNESS["E"])
    away_k, away_v = (bearing(CENTRE, e) + 180) % 360, bearing(CENTRE, e)
    players = {0: on_box_track(*CENTRE, yaw=away_k), 5: still("B", *e, away_v)}
    rnd, blob = inputs(geo, players, deaths={5: 6.0},
                       util=[{"k": "damage", "src": "gun", "by": 0, "target": 5, "t": 5.5}])
    return fights._kd(rnd, blob, 6.0, 0, 5, True)


def test_a_kill_whose_bare_line_exists_only_over_the_box_is_measurable():
    on = _kd_for(box_hall_tops())
    assert not on["killer_saw"] and not on["victim_saw"]
    assert on["measurable"]
    off = _kd_for(no_cover(box_hall_tops()))
    assert not off["measurable"]


# ------------------------------------------------------------------ d. critutil

def _pairs(geo, monkeypatch):
    from app.critutil import extract

    (rnd, _), e = box_viewer_round(geo)
    smoke = (CENTRE[0] + 0.7 * (e[0] - CENTRE[0]), CENTRE[1] + 0.7 * (e[1] - CENTRE[1]), 8.0, True)
    monkeypatch.setattr(extract, "_shapes_of", lambda *_a: [(0.0, 10.0, smoke)])
    cast = SimpleNamespace(i=0, by=0, t=0.0, t_end=10.0)
    return extract._cover_all(rnd, {0: "A", 5: "B", 6: "B"}, [cast])[0]["pairs"]


def test_a_smoke_on_the_line_from_a_player_on_the_box_counts_a_pair(monkeypatch):
    assert _pairs(box_hall_tops(), monkeypatch) > 0


def test_with_no_stance_the_bare_line_is_blocked_so_no_pair(monkeypatch):
    assert _pairs(no_cover(box_hall_tops()), monkeypatch) == 0


# ------------------------------------------------------------------ fix round: beside viewer, off-line smoke

def test_sees_point_from_a_beside_viewer_answers_from_the_anchor_not_the_pixel():
    from app.control import engine as ce
    from app.control import stance as st_mod
    from app.control.utility import _sees_point
    from control_toys import hugging_track

    geo = box_hall_tops()
    rnd, _ = inputs(geo, {0: hugging_track(252, 196, yaw=90)})        # in the west edge, facing south
    st = st_mod.stance_at(rnd, 0, T)
    assert st is not None and st.kind == "beside" and st.eye_px != st.px
    x, y = 240.0, 210.0
    assert ce.los(geo, (*st.eye_px, None), (x, y, None), []), "the anchor sees the point"
    assert not ce.los(geo, (*st.px, None), (x, y, None), []), "the raw pixel (inside the piece) does not"
    assert _sees_point(rnd, 0, T, x, y)


def test_a_smoke_off_the_line_from_a_player_on_the_box_counts_no_pair(monkeypatch):
    from app.critutil import extract

    geo = box_hall_tops()
    (rnd, _), e = box_viewer_round(geo)
    smoke = (CENTRE[0] - 40.0, CENTRE[1], 8.0, True)                  # behind the viewer, well off the line
    monkeypatch.setattr(extract, "_shapes_of", lambda *_a: [(0.0, 10.0, smoke)])
    cast = SimpleNamespace(i=0, by=0, t=0.0, t_end=10.0)
    assert extract._cover_all(rnd, {0: "A", 5: "B", 6: "B"}, [cast])[0]["pairs"] == 0

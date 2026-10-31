"""W7a: only airborne holders opt in to learnt-box plate sight (R2/R3/R5)."""
from types import SimpleNamespace

import numpy as np
import pytest

from app.control import engine as ce, geometry as cg, heights as hc
from tests.replays.control_toys import box_hall, box_hall_tops, height_blob, on_box_track
from tests.replays.test_plate_sight import Y, box
from tests.replays.test_stance_engine import inputs, still, tick


def jump_tick(geo, z=1.2):
    if geo.row_of is None:
        cg.visibility(geo)
    rnd, _ = inputs(geo, {0: still("A", 164, Y, 0, z)})
    # PlateToy uses world origin 0; height_blob uses the shared toy origin.
    rnd.height = lambda s, t: z
    return ce.Tick(rnd, 5.0)


def test_jumper_sees_beyond_the_box_but_grounded_holder_does_not():
    geo = box()
    high, low = jump_tick(geo), jump_tick(geo, 0.0)
    target = geo.node_at_px(308, Y, 0)
    assert high.holders[0].air and not low.holders[0].air
    assert high.view[0][target] and not low.view[0][target]


def test_switch_off_gives_the_old_cast_byte_for_byte(monkeypatch):
    geo = box()
    monkeypatch.setattr(ce, "JUMP_SIGHT", False)
    tk = jump_tick(geo)
    h = tk.holders[0]
    old = cg.cast(geo, h.x, h.y,
                  np.arange(-ce.FOV_HALF, ce.FOV_HALF + 1e-9, cg.RAY_STEP_DEG), [],
                  eye_z=1.2 + hc.EYE_M, own=h.cell, see_over=())
    assert not h.air and h.raw.tobytes() == old.tobytes()
    old_view = old | tk._presence(h.x, h.y, h.cell)
    assert tk.view[0].tobytes() == old_view.tobytes()


@pytest.mark.slow
def test_on_box_is_never_airborne():
    h = tick(box_hall_tops(), {0: on_box_track(260, 196)}).holders[0]
    assert h.stance.kind == "on_box" and not h.air


@pytest.mark.parametrize("source", ["stance", "pixel", "reach"])
def test_unknown_floor_uses_the_nearby_pieces_ring_floor(source):
    geo = SimpleNamespace(heights=object(), node_z=np.array([np.nan]),
                          cover_px=np.zeros((4, 4), int),
                          box_view_reach=np.zeros((4, 4, 1), int), box_ground={7: 2.0})
    st = SimpleNamespace(kind="beside", piece=7) if source == "stance" else None
    if source == "pixel":
        geo.cover_px[2, 1] = 7
    if source == "reach":
        geo.box_view_reach[2, 1, 0] = 7
    tk = ce.Tick.__new__(ce.Tick)
    tk.geo = geo
    tk.rnd = SimpleNamespace(height=lambda s, t: 2.0 + hc.STAND_TOL_M + .01,
                             pos=lambda s, t: (1.5, 2.5, 0))
    assert tk._airborne(0, 5.0, 0, st)
    geo.box_ground[7] = 3.0
    assert not tk._airborne(0, 5.0, 0, st)


def test_unknown_floor_without_a_piece_is_not_airborne():
    tk = ce.Tick.__new__(ce.Tick)
    tk.geo = SimpleNamespace(heights=object(), node_z=np.array([np.nan]),
                             cover_px=np.zeros((4, 4), int), box_view_reach=None, box_ground={})
    tk.rnd = SimpleNamespace(height=lambda s, t: 3.0, pos=lambda s, t: (1, 2, 0))
    assert not tk._airborne(0, 5.0, 0, None)


@pytest.mark.parametrize("z,air", [(None, False), (hc.STAND_TOL_M, False),
                                  (hc.STAND_TOL_M + .01, True)])
def test_airborne_threshold_is_strict_and_uses_the_anchor_floor(z, air):
    tk = ce.Tick.__new__(ce.Tick)
    tk.geo = SimpleNamespace(heights=object(), node_z=np.array([0.0, 4.0]))
    tk.rnd = SimpleNamespace(height=lambda s, t: z)
    assert tk._airborne(0, 5.0, 0, None) is air
    assert not tk._airborne(0, 5.0, 1, None)


def test_no_airborne_holder_makes_zero_plate_calls(monkeypatch):
    monkeypatch.setattr(ce, "_plate_calls", 0)
    tk = jump_tick(box(), 0.0)
    assert not any(h.air for h in tk.holders.values()) and ce._plate_calls == 0


@pytest.mark.slow
def test_airborne_cast_uses_plates_without_see_over(monkeypatch):
    calls, original = [], ce.cast

    def cast(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(ce, "cast", cast)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    tk = tick(box_hall_tops(), {0: on_box_track(252, 196, rise_m=3.0)})
    assert tk.holders[0].air
    assert ce._plate_calls == 1
    assert calls[0]["plates"] is True and calls[0]["see_over"] == ()


@pytest.mark.slow
def test_flat_map_unchanged(monkeypatch):
    from app.control.encode import encode_data, encode_summary

    geo = box_hall()
    players = {0: ("A", [(0.0, 241, 196, 0, 0.0), (1.0, 241, 196, 0, 3.0),
                          (10.0, 241, 196, 0, 0.0)])}
    blob = height_blob(players, t_end=1.0)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    enabled = tick(geo, players)
    enabled_round = ce.compute_round(blob, geo, knowledge=False)
    monkeypatch.setattr(ce, "JUMP_SIGHT", False)
    disabled = tick(geo, players)
    disabled_round = ce.compute_round(blob, geo, knowledge=False)
    assert geo.heights is None and not enabled.holders[0].air and ce._plate_calls == 0
    for field in ("raw", "body", "active", "passive", "watch"):
        assert getattr(enabled.holders[0], field).tobytes() == getattr(disabled.holders[0], field).tobytes()
    assert enabled.view[0].tobytes() == disabled.view[0].tobytes()
    assert enabled.direct == disabled.direct and enabled.sees == disabled.sees
    for encode in (encode_data, encode_summary):
        assert encode(enabled_round, blob) == encode(disabled_round, blob)

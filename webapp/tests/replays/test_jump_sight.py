"""W7a/W7b1: airborne fans and eye-to-eye plate sight; grounded paths stay frozen (R2-R5)."""
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


def jumper_target_tick(geo, target_z):
    if geo.row_of is None:
        cg.visibility(geo)
    rnd, _ = inputs(geo, {0: still("A", 196, Y, 0, 0.0), 1: still("B", 268, Y, 180, target_z)})
    rnd.height = lambda s, t: {0: 0.0, 1: target_z}[s]
    return ce.Tick(rnd, 5.0)


def test_a_jumper_seen_only_at_the_feet_counts_as_seen():
    from tests.replays.test_control_sight import low_overhang
    tk = jumper_target_tick(low_overhang(), 2.0)
    e, h = tk.holders[0], tk.holders[1]
    assert h.air and not tk._air_line(e, h, target_z=2.0 + hc.HEAD_M, record=False)
    assert tk._air_line(e, h, target_z=2.0 + hc.FEET_M, record=False)
    assert 1 in tk.sees[0] and (0, 1) in tk.direct
    assert (0, 1) in tk.feet_only()


def test_a_pair_touching_the_air_is_judged_eye_to_head_then_eye_to_feet(monkeypatch):
    from tests.replays.test_control_sight import low_overhang
    tk = jumper_target_tick(low_overhang(), 2.0)
    e, h = tk.holders[0], tk.holders[1]
    ends = []

    def line(geo, start, end, smokes, **kw):
        ends.append((start[2], end[2]))
        return False
    monkeypatch.setattr(ce, "los", line)
    assert not tk._air_seen(e, h)
    assert ends == [(hc.EYE_M, 2.0 + hc.HEAD_M), (hc.EYE_M, 2.0 + hc.FEET_M)]
    assert hc.HEAD_M + hc.STAND_M == pytest.approx(2.0), "the head point is feet + 2 m"


def test_a_jumper_whose_eye_and_feet_lines_are_blocked_is_not_seen_through_their_floor(monkeypatch):
    from tests.replays.test_control_sight import low_overhang
    monkeypatch.setattr(ce, "los", lambda *a, **kw: False)      # both exact lines blocked; the fan doesn't use los
    tk = jumper_target_tick(low_overhang(), 2.0)
    e, h = tk.holders[0], tk.holders[1]
    assert h.air and e.body[h.cell], "the floor spot under them is in view"
    assert 1 not in tk.sees[0] and (0, 1) not in tk.direct


def test_gap_sight_of_a_jumper_matches_control_live_and_through_the_cache(tmp_path):
    from app.control.observe import record
    from app.gaps import cache
    from app.gaps.detect import GapDetector

    geo = box()
    tk = jump_tick(geo)
    target = geo.node_at_px(308, Y, 0)
    assert tk.view[0][target]
    rec = record(tk, SimpleNamespace(entry={}, events={}))
    path = cache.cache_path(1, 1, "jump", tmp_path)
    writer = cache.Writer(path)
    writer(rec, SimpleNamespace(geo=geo, log=[]))
    writer.close({})
    (stored, _), = cache.replay(path)
    detector = GapDetector(geo, tk.rnd)
    for p in (rec.players[0], stored.players[0]):
        assert p.air
        sight = detector._sight(*detector._eye(p), p.eye_z, p.node, [], see_over=p.see_over, own=p.own, plates=p.air)
        assert sight[target]
        walled = detector._sight(*detector._eye(p), p.eye_z, p.node, [], see_over=p.see_over, own=p.own)
        assert not walled[target], "the memo keeps the two modes apart"


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


def pair_tick(geo, *, left_z=0.0, right_z=1.2, right_x=308, right_y=Y, yaw=0,
              flashed=False, near=False, cone=None, smokes=()):
    """PlateToy's two opponents, with heights in its own world origin."""
    if geo.row_of is None:
        cg.visibility(geo)
    rnd, _ = inputs(geo, {0: still("A", 164, Y, yaw, left_z or 0),
                          5: still("B", right_x, right_y, 180, right_z or 0)})
    rnd.height = lambda s, t: left_z if s == 0 else right_z
    for s, z in ((0, left_z), (5, right_z)):
        rnd.heights[s] = np.full_like(rnd.heights[s], np.nan if z is None else z)
    rnd.smokes_at = lambda t: list(smokes)
    if flashed:
        rnd.flashed[0] = [(0, 10)]
    if near:
        rnd.nearsight[0] = [(0, 10)]
    if cone is False:
        rnd.downgraded[0] = [(0, 10)]
    return ce.Tick(rnd, 5.0)


@pytest.mark.parametrize("left_z,right_z", [(0.0, 1.2), (1.2, 0.0), (1.2, 1.2)])
def test_eye_lines_see_both_directions_over_a_learnt_box(left_z, right_z):
    tk = pair_tick(box(), left_z=left_z, right_z=right_z)
    assert 5 in tk.sees[0] and 0 in tk.sees[5]
    assert not tk._head_clear, "airborne pairs never populate the ground head-line cache"


def test_a_jumper_with_both_lines_blocked_doesnt_see_a_grounded_enemy_through_their_cell():
    # The fan reaches the enemy's cell, but the body sits behind the box from this eye: the owner's rule is the two lines.
    tk = pair_tick(box(), left_z=1.2, right_z=0.0, right_x=244, right_y=212)
    jumper, enemy = tk.holders[0], tk.holders[5]
    assert jumper.air and not enemy.air and jumper.body[enemy.cell]
    assert not tk._air_seen(jumper, enemy)
    assert (0, 5) not in tk.direct and 5 not in tk.sees[0]
    assert tk._by_lines_only(jumper, enemy), "the knowledge view's sees reads the same rule"


def test_a_rejected_sighting_never_reaches_the_unknown():
    # The same blocked pair: the jumper's active fan covers the enemy's cell, and nothing is spotted.
    tk = pair_tick(box(), left_z=1.2, right_z=0.0, right_x=244, right_y=212)
    assert tk.holders[0].active[tk.holders[5].cell]
    unknown = ce.Unknown(tk.geo)
    unknown.apply(tk)
    assert 5 not in unknown.seen["A"]


def test_a_clear_air_pair_in_the_cone_reaches_the_unknown():
    tk = pair_tick(box(), left_z=1.2, right_z=0.0)
    unknown = ce.Unknown(tk.geo)
    unknown.apply(tk)
    assert unknown.seen["A"][5][0] == tk.holders[5].cell


def test_a_grounded_pair_is_still_spotted_by_the_active_view(monkeypatch):
    # Nobody in the air: an active view of the cell spots them, with no lines asked.
    monkeypatch.setattr(ce, "cast", lambda geo, *a, **kw: np.ones(geo.n, bool))
    tk = pair_tick(box(), left_z=0.0, right_z=0.0)
    assert not tk._air_pair(tk.holders[0], tk.holders[5]) and (0, 5) not in tk.direct
    unknown = ce.Unknown(tk.geo)
    unknown.apply(tk)
    assert 5 in unknown.seen["A"]


@pytest.mark.parametrize("left_z,right_z,right_x,right_y,forward,back", [
    (1.2, 0.0, 308, Y, True, True),    # over the box both ways
    (1.2, 0.0, 244, 212, False, False),  # both lines blocked, whichever end looks
    (0.0, 1.2, 308, Y, True, True),
])
def test_fight_sight_queries_agree_with_the_tick(left_z, right_z, right_x, right_y, forward, back):
    from app.control import utility as ut

    tk = pair_tick(box(), left_z=left_z, right_z=right_z, right_x=right_x, right_y=right_y)
    a, b = tk.holders[0], tk.holders[5]
    assert ut._sees_point(tk.rnd, 0, 5.0, b.x, b.y, target=5) is forward is (5 in tk.sees[0])
    assert ut._sees_point(tk.rnd, 5, 5.0, a.x, a.y, target=0) is back is (0 in tk.sees[5])


def test_a_grounded_fight_sight_query_keeps_its_2d_line():
    from app.control import utility as ut

    tk = pair_tick(box(), left_z=0.0, right_z=0.0)
    assert ut._sees_point(tk.rnd, 0, 5.0, tk.holders[5].x, Y, target=5) is False  # the box is a wall from the ground


def test_a_point_query_never_asks_the_pair_lines(monkeypatch):
    from app.control import utility as ut

    tk = pair_tick(box(), left_z=1.2, right_z=0.0)
    monkeypatch.setattr(ce, "_air_pair_sight", lambda *a, **kw: pytest.fail("a point isn't a player"))
    ut._sees_point(tk.rnd, 0, 5.0, 308, Y)


def test_a_jumper_whose_view_reaches_the_cell_still_sees_by_a_clear_line(monkeypatch):
    monkeypatch.setattr(ce, "cast", lambda geo, *a, **kw: np.ones(geo.n, bool))
    tk = pair_tick(box(), left_z=1.2, right_z=0.0)
    assert tk.holders[0].body[tk.holders[5].cell]
    assert (0, 5) in tk.direct and 5 in tk.sees[0]


def test_jumper_does_not_see_a_low_target_just_behind_the_box():
    tk = pair_tick(box(), left_z=1.2, right_z=0.0, right_x=236)
    assert not tk.holders[5].air
    assert (0, 5) not in tk.direct and 5 not in tk.sees[0]


def test_unknown_box_top_blocks_both_directions():
    tk = pair_tick(box(()))
    assert tk.direct == {} and tk.sees == {0: set(), 5: set()}


def test_grounded_pairs_keep_direct_and_head_cache_and_make_no_plate_calls(monkeypatch):
    geo = box()
    monkeypatch.setattr(ce, "_plate_calls", 0)
    enabled = pair_tick(geo, right_z=0.0)
    assert ce._plate_calls == 0
    monkeypatch.setattr(ce, "JUMP_SIGHT", False)
    disabled = pair_tick(geo, right_z=0.0)
    assert enabled.direct == disabled.direct == {}
    assert enabled._head_clear == disabled._head_clear == set()
    assert enabled.sees == disabled.sees == {0: set(), 5: set()}


def test_switch_off_preserves_old_pair_direct_and_sees(monkeypatch):
    geo = box()
    enabled = pair_tick(geo)
    assert (0, 5) in enabled.direct and 5 in enabled.sees[0]
    monkeypatch.setattr(ce, "JUMP_SIGHT", False)
    disabled = pair_tick(geo)
    assert disabled.direct == {} and disabled.sees == {0: set(), 5: set()}


@pytest.mark.parametrize("limits,expected", [({}, True), ({"yaw": 180}, False),
    ({"flashed": True}, False), ({"near": True}, False), ({"cone": False}, False),
    ({"smokes": [(260, Y, 8, True)]}, False)])
def test_air_viewer_adds_only_a_permitted_eye_line(monkeypatch, limits, expected):
    # Force a fan miss: the exact line pass must work independently of sampled cell sight.
    monkeypatch.setattr(ce, "cast", lambda geo, *a, **kw: np.zeros(geo.n, bool))
    tk = pair_tick(box(), left_z=1.2, right_z=0.0, **limits)
    assert not tk.holders[0].body[tk.holders[5].cell]
    if expected or limits.get("cone") is False:
        assert tk.direct[(0, 5)] is expected and 5 in tk.sees[0]
    else:
        assert (0, 5) not in tk.direct and 5 not in tk.sees[0]


def test_missing_target_height_uses_the_target_floor_for_the_eye(monkeypatch):
    monkeypatch.setattr(ce, "cast", lambda geo, *a, **kw: np.zeros(geo.n, bool))
    tk = pair_tick(box(), left_z=1.2, right_z=None)
    assert not tk.holders[5].air
    assert (0, 5) in tk.direct and 5 in tk.sees[0]


@pytest.mark.parametrize("viewer_air,target_air", [(True, False), (False, True)])
def test_boxed_air_pair_stops_at_a_clear_head_line_and_counts_it(monkeypatch, viewer_air, target_air):
    geo = box()
    tk = ce.Tick.__new__(ce.Tick)
    tk.geo, tk.t, tk.smokes, tk.fallbacks = geo, 5.0, [], {}
    tk.rnd = SimpleNamespace(height=lambda s, t: 1.2 if s == 0 else 0.0)
    cell = geo.node_at_px(164, Y, 0)
    st = SimpleNamespace(kind="on_box" if viewer_air else "over", piece=1,
                         eye_px=(226, Y), see_over=(1,))
    viewer_st = SimpleNamespace(kind="beside", piece=2, eye_px=(166, Y), see_over=())
    tk.holders = {0: SimpleNamespace(slot=0, team="A", cell=cell, x=164, y=Y,
                                    air=viewer_air, stance=viewer_st),
                  5: SimpleNamespace(slot=5, team="B", cell=cell, x=226, y=Y,
                                     air=target_air, stance=st)}
    tk._head_clear = set()
    calls = []
    monkeypatch.setattr(ce, "los", lambda *a, **kw: calls.append((a, kw)) or True)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    assert tk._boxed({0: (0, 2.25, False, False, 30)}) == {(0, 5): True}
    assert len(calls) == ce._plate_calls == 1 and not tk._head_clear
    args, kwargs = calls[0]
    assert args[1] == (166, Y, 2.25) and args[2] == (226, Y, hc.HEAD_M)
    assert kwargs["plates"] is True and set(kwargs["skip"]) == {1, 2}
    assert "see_over" not in kwargs


def test_airborne_teammate_is_never_a_direct_enemy():
    tk = pair_tick(box())
    tk.holders[5].team = "A"
    assert tk._direct({0: (0, hc.EYE_M, False, False, 30)}) == {}


def test_air_line_uses_cover_pixels_without_stances(monkeypatch):
    tk = pair_tick(box())
    a, b = tk.holders[0], tk.holders[5]
    a.stance = b.stance = None
    tk.geo.cover_px = tk.geo.cover_px.copy()
    tk.geo.cover_px[int(a.y), int(a.x)] = 7
    tk.geo.cover_px[int(b.y), int(b.x)] = 9
    calls = []
    monkeypatch.setattr(ce, "los", lambda *a, **kw: calls.append(kw) or True)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    assert tk._air_line(a, b, target_z=1.0)
    assert calls[0]["skip"] == (7, 9) and ce._plate_calls == 1


@pytest.mark.parametrize("left_z,right_z", [(0.0, 1.2), (1.2, 0.0), (1.2, 1.2)])
def test_air_pair_with_a_clear_head_is_not_feet_only(left_z, right_z):
    tk = pair_tick(box(), left_z=left_z, right_z=right_z)
    before = {s: targets.copy() for s, targets in tk.sees.items()}
    assert tk.feet_only() == set()
    assert tk.sees == before


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("viewer_air,target_air", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("head,feet", [(True, True), (False, True), (False, False)])
def test_air_feet_only_checks_its_own_plate_lines(monkeypatch, direct, viewer_air, target_air, head, feet):
    tk = pair_tick(box(), left_z=1.2 if viewer_air else 0.0,
                   right_z=1.2 if target_air else None)
    e, h = tk.holders[0], tk.holders[5]
    e.stance = SimpleNamespace(piece=7, eye_px=(166, Y))
    h.stance = None
    tk.geo.cover_px = tk.geo.cover_px.copy()
    tk.geo.cover_px[int(h.y), int(h.x)] = 9
    tk.sees = {0: {5}, 5: set()}
    tk.direct = {(0, 5): True} if direct else {}
    tk._head_clear = {(0, 5)}  # Metadata must not borrow a ground head-line result either.
    calls = []

    def line(geo, start, end, smokes, **kwargs):
        calls.append((start, end, smokes, kwargs))
        return head if len(calls) == 1 else feet

    monkeypatch.setattr(ce, "los", line)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    before = tk.fallbacks.copy()
    expected = {(0, 5)} if not head and feet else set()
    assert tk.feet_only() == expected
    assert len(calls) == ce._plate_calls == (1 if head else 2)
    z = 1.2 if target_air else tk.geo.node_z[h.cell]
    assert [end[2] for _, end, _, _ in calls] == [z + hc.HEAD_M] + ([] if head else [z + hc.FEET_M])
    for start, end, smokes, kwargs in calls:
        assert start == (166, Y, (1.2 if viewer_air else 0.0) + hc.EYE_M)
        assert end[:2] == (h.x, h.y) and smokes is tk.smokes
        assert kwargs["plates"] is True and kwargs["skip"] == (7, 9)
        assert kwargs.get("record") is None and "see_over" not in kwargs
    assert tk.fallbacks == before and tk.sees == {0: {5}, 5: set()}
    assert tk.feet_only() == expected and len(calls) == ce._plate_calls


@pytest.mark.parametrize("jump_sight", [False, True])
def test_grounded_feet_only_keeps_the_old_head_and_feet_lines(monkeypatch, jump_sight):
    from tests.replays.test_control_feet_only import overhang_blob
    from tests.replays.test_control_sight import low_overhang

    monkeypatch.setattr(ce, "JUMP_SIGHT", jump_sight)
    tk = ce.Tick(ce.RoundInputs(overhang_blob(), low_overhang()), 0.0)
    calls, original = [], ce.los

    def line(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(ce, "los", line)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    assert not any(h.air for h in tk.holders.values())
    assert tk.feet_only() == {(0, 5)} and len(calls) == 2
    assert ce._plate_calls == 0 and all(not kw.get("plates") for kw in calls)


def test_switch_off_keeps_the_old_direct_feet_only_shortcut(monkeypatch):
    tk = pair_tick(box())
    tk.sees = {0: {5}}
    assert (0, 5) in tk.direct
    monkeypatch.setattr(ce, "JUMP_SIGHT", False)
    monkeypatch.setattr(ce, "_plate_calls", 0)
    monkeypatch.setattr(ce, "los", lambda *a, **kw: pytest.fail("legacy direct pair needs no LOS"))
    assert tk.feet_only() == {(0, 5)}
    assert ce._plate_calls == 0

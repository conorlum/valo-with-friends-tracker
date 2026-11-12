"""R10/D7-D9: destroyed trips and cameras anonymously locate a shooter around the device."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.control import engine as ce
from app.control import heights as hc
from app.gaps.detect import judged_against_before
from tests.replays.control_toys import blob, height_blob, open_hall, toy_ability, toy_heights, uv, z_dm
from tests.replays.test_gaps_locating import _runner, _step


GONE = 17.2
DEVICE_PX = (300, 200)


def _shot(by=5, t=GONE, start=None, end=DEVICE_PX):
    start = start or (300 + 6 / open_hall().m_per_px, 200)
    u, v = uv(*start)
    row = {"k": "shot", "t": t, "by": by, "u": u, "v": v, "gun": "Ghost"}
    if end is not None:
        row["u1"], row["v1"] = uv(*end)
    return row


def _devices(device="trip", point=DEVICE_PX, gone_anchor=1, **extra):
    x, y = point
    if device == "trip":
        # Real trips have two rows. Either anchor can carry gone; the locate uses their midpoint.
        rows = [toy_ability("Gumshoe", "4_TripWire", x, y - 4, 0, t1=GONE, kind="GameObject",
                            id=10, end=list(uv(x, y + 4)), **extra),
                toy_ability("Gumshoe", "4_TripWire_SecondWire", x, y + 4, 0, t1=GONE,
                            kind="GameObject", id=11, **extra)]
        rows[gone_anchor]["gone"] = GONE
        return rows
    code, name = {"camera": ("Gumshoe", "E_PossessableCamera"),
                  "alarmbot": ("Killjoy", "Q_StealthAlarmbot")}[device]
    return [toy_ability(code, name, x, y, 0, t1=GONE, gone=GONE, kind="GameObject", id=10, **extra)]


def _data(util, other=None):
    players = {0: ("A", [(0.0, 120, 200, 180)]),
               5: ("B", [(0.0, 300 + 6 / open_hall().m_per_px, 200, 180)])}
    if other is not None:
        players[6] = ("B", [(0.0, *other, 180)])
    return blob(players, t_end=20.0, util=util)


def _run(data, monkeypatch):
    # Keep the shot itself inaudible so only the destroyed-device rule locates the enemy.
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Ghost": 0.0})
    rnd, runner = _runner(open_hall(), data)
    for t in (0.0, 17.0, 17.25):
        _step(rnd, runner, t)
    return rnd, runner


def _destruction_hits(rnd, runner, spotted=frozenset()):
    return [h for h in runner.unknown._locating(rnd, "A", 5, 17.0, 17.25, spotted)
            if h[1] in ("trip_destroyed", "camera_destroyed")]


@pytest.mark.parametrize("gone_anchor", [0, 1])
def test_destroyed_trip_locates_the_only_enemy_at_the_trip(monkeypatch, gone_anchor):
    rnd, runner = _run(_data([*_devices(gone_anchor=gone_anchor), _shot()]), monkeypatch)
    assert (GONE, 5, "locate", "trip_destroyed", "") in runner.unknown.reasons["A"]
    assert runner.unknown.located["A"][5] == pytest.approx(GONE)
    [hit] = _destruction_hits(rnd, runner)
    x, y = np.array(uv(*DEVICE_PX)) * ce.PX / 10000
    assert hit == pytest.approx((GONE, "trip_destroyed", ce.TRIP_DESTROY_REACH_M,
                                 rnd.geo.node_at_px(x, y), x, y))
    assert ce.TRIP_DESTROY_REACH_M == 20.0
    area = runner.unknown._area(hit[3], hit[4], hit[5], hit[2], rnd.geo.walk_n)
    distances = np.hypot(rnd.geo.centres[:, 0] - x, rnd.geo.centres[:, 1] - y) * rnd.geo.m_per_px
    assert not area[distances > ce.TRIP_DESTROY_REACH_M].any()
    inside = rnd.geo.walk_n & (distances < ce.TRIP_DESTROY_REACH_M - rnd.geo.cell_m)
    assert area[inside].all()


def test_another_unseen_enemy_prevents_attribution(monkeypatch):
    rnd, runner = _run(_data([*_devices(), _shot()], other=(400, 260)), monkeypatch)
    assert not _destruction_hits(rnd, runner)
    assert 5 not in runner.unknown.located["A"]
    assert rnd.missing["destroyed device without a shooter"] == 0


def test_another_enemy_in_live_sight_allows_attribution(monkeypatch):
    _, runner = _run(_data([*_devices(), _shot()], other=(100, 200)), monkeypatch)
    assert (GONE, 5, "locate", "trip_destroyed", "") in runner.unknown.reasons["A"]


def test_loader_selects_the_nearest_shooter_using_world_metres(monkeypatch):
    scale = open_hall().m_per_px
    shots = [_shot(by=6, start=(280, 200 + 1.5 / scale), end=(360, 200 + 1.5 / scale)),
             _shot(by=5, start=(280, 200 + 0.5 / scale), end=(360, 200 + 0.5 / scale))]
    rnd, runner = _run(_data([*_devices(), *shots], other=(100, 200)), monkeypatch)
    assert (GONE, 5, "trip_destroyed") in rnd.sounds
    assert (GONE, 6, "trip_destroyed") not in rnd.sounds
    assert (GONE, 5, "locate", "trip_destroyed", "") in runner.unknown.reasons["A"]


@pytest.mark.parametrize("shots", [[], [_shot(t=GONE - 0.6)]])
def test_missing_or_old_shot_counts_a_missing_destroyer(monkeypatch, shots):
    rnd, runner = _run(_data([*_devices(), *shots]), monkeypatch)
    assert not _destruction_hits(rnd, runner)
    assert 5 not in runner.unknown.located["A"]
    assert rnd.missing["destroyed device without a shooter"] == 1


def test_camera_uses_its_own_reason_and_reach(monkeypatch):
    rnd, runner = _run(_data([*_devices("camera"), _shot()]), monkeypatch)
    assert (GONE, 5, "locate", "camera_destroyed", "") in runner.unknown.reasons["A"]
    [hit] = _destruction_hits(rnd, runner)
    assert hit[2] == ce.CAMERA_DESTROY_REACH_M == 35.0
    assert hit[4:] == pytest.approx(np.array(uv(*DEVICE_PX)) * ce.PX / 10000)


@pytest.mark.parametrize("device,extra", [("alarmbot", {}), ("trip", {"fx": GONE - 1.0})])
def test_other_devices_and_already_fired_trips_have_no_control_effect(monkeypatch, device, extra):
    rnd, runner = _run(_data([*_devices(device, **extra), _shot()]), monkeypatch)
    assert not _destruction_hits(rnd, runner)
    assert 5 not in runner.unknown.located["A"]
    assert rnd.missing["destroyed device without a shooter"] == 0


def test_off_grid_destruction_is_judged_against_before_at_the_next_tick(monkeypatch):
    monkeypatch.setattr(ce, "GUN_HEARING_M", {"Ghost": 0.0})
    rnd, runner = _runner(open_hall(), _data([*_devices(), _shot()]))
    for t in rnd.tick_times():
        _step(rnd, runner, float(t))
        if t >= GONE:
            break
    assert t > GONE
    assert (5, GONE, "trip_destroyed") in runner.unknown.events["A"]
    rec = SimpleNamespace(t=t, events=runner.unknown.events)
    assert judged_against_before(rec)["A"] == {5}


def test_trip_on_upper_floor_seeds_the_upper_node():
    rect = (96, 96, 160, 128)
    geo = toy_heights("DestroyedTripFloors", [rect], ground=[(rect, -2.0)], upper=[(rect, 4.0)])
    point = (124, 108)
    data = height_blob({0: ("A", [(0.0, 100, 108, 180, -2.0)]),
                        5: ("B", [(0.0, 148, 108, 180, -2.0)])}, t_end=20.0,
                       util=[*_devices(point=point, z=z_dm(4.0)), _shot(start=(148, 108), end=point)])
    rnd, runner = _runner(geo, data)
    [hit] = _destruction_hits(rnd, runner)
    x, y = np.array(uv(*point)) * ce.PX / 10000
    z = rnd._device_z(z_dm(4.0))
    upper = geo.node_at_px(x, y, z + hc.STAND_M)
    lower = geo.node_at_px(x, y, 0.0)
    assert upper != lower
    assert hit[3] == upper
    area = runner.unknown._area(hit[3], x, y, hit[2], geo.walk_n)
    assert area[upper] and not area[lower]


SIDE_OF = {0: "A", 1: "A", 5: "B", 6: "B"}


def _segment(by, offset, t=GONE):
    # World centimetres here, with a device at (1000, 2000).
    return {"k": "shot", "t": t, "by": by, "u": 900, "v": 2000 + offset,
            "u1": 1100, "v1": 2000 + offset}


def _destroyer(shots):
    return ce.destroyer(shots, (1000, 2000), GONE, "A", SIDE_OF, 0.01)


def test_closest_enemy_segment_within_two_metres_wins():
    assert _destroyer([_segment(6, 150), _segment(5, 50)]) == 5


def test_equal_distances_choose_the_lower_slot_in_either_row_order():
    shots = [_segment(6, 50), _segment(5, 50)]
    assert _destroyer(shots) == _destroyer(shots[::-1]) == 5


def test_one_distant_segment_and_two_shooters_have_no_destroyer():
    assert _destroyer([_segment(5, 300), {"k": "shot", "t": GONE, "by": 6, "u": 0, "v": 0}]) is None


def test_teammates_and_unknown_shooters_are_ignored():
    assert _destroyer([_segment(1, 0), _segment(99, 0)]) is None
    assert _destroyer([_segment(1, 0), _segment(5, 150)]) == 5


def test_only_shooter_fallback_accepts_a_row_without_a_segment():
    assert _destroyer([{"k": "shot", "t": GONE, "by": 5, "u": 0, "v": 0}]) == 5


def test_only_shooter_fallback_counts_distinct_slots():
    assert _destroyer([_segment(5, 300), _segment(5, 400)]) == 5
    assert _destroyer([_segment(5, 300), _segment(6, 400)]) is None


@pytest.mark.parametrize("offset,expected", [(-0.5, 5), (-0.501, None), (0.05, 5), (0.051, None)])
def test_destroyer_window_edges(offset, expected):
    assert _destroyer([_segment(5, 0, t=GONE + offset)]) == expected


def test_segment_is_finite_and_includes_the_two_metre_boundary():
    assert _destroyer([_segment(5, 200), _segment(6, 300)]) == 5
    short = _segment(5, 0)
    short["u"], short["u1"] = 500, 600
    assert _destroyer([short, _segment(6, 300)]) is None


def test_zero_length_segment_uses_its_point():
    shot = _segment(5, 0)
    shot["u1"] = shot["u"]
    assert _destroyer([_segment(6, 150), shot]) == 5

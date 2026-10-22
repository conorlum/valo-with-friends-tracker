"""Critical Util's unknown probe (app/critutil/unknown.py), through the real engine on toy rounds."""

from app.control import engine as ce
from app.critutil import extract
from app.critutil.unknown import UnknownProbe
from tests.replays.control_toys import blob, door_hall, toy_ability, uv


def run(util, players, t_end=20.0):
    geo = door_hall()
    data = blob(players, util=util, t_end=t_end)
    rnd = ce.RoundInputs(data, geo)
    probe = UnknownProbe(rnd)
    ce.compute_round(data, geo, observer=lambda record, unknown: probe.step(record), knowledge=False)
    return probe.finish(), rnd, data


def still(side, x, y, yaw):
    return (side, [(0.0, x, y, yaw), (20.0, x, y, yaw)])


def test_a_trip_across_the_door_holds_the_unknown_at_its_line():
    # door_hall: a wall at x 200-216 with a door at its south end (y 288-296). Team A is west, out of sight of the
    # enemy east of the wall; the wire runs across the west side of the door, so the enemy's unknown comes through
    # the door and stops at it.
    trip = toy_ability("Gumshoe", "4_TripWire", 185, 240, 0, t=0.5, t1=20.0, kind="GameObject",
                       end=list(uv(185, 296)))
    # facing north, away from the door: only the wire watches it
    players = {0: still("A", 120, 110, 270), 1: still("A", 105, 105, 270), 5: still("B", 400, 200, 0)}
    holds, rnd, data = run([trip], players)
    assert holds["error"] is None
    (item,) = [i for i in holds["items"] if i["what"] == "device"]
    assert item["by"] == 0 and item["kind"] == "trip" and item["up_s"] > 5
    assert item["front_s"] > 5, "the unknown comes through the door (about 10 s) and stops at the wire"
    assert item["crossed_s"] == 0
    assert item["held_m2s"] > 0, "the wire's cells are ground no teammate sees"
    out = extract.round_util(ce.RoundInputs(data, rnd.geo), data, holds=holds)
    (cast,) = [c for c in out["casts"] if "device" in c["kinds"]]
    assert cast["hold"]["front_s"] == item["front_s"]
    assert out["avail"]["unknown"] is True


def test_the_probe_never_raises():
    probe = UnknownProbe.__new__(UnknownProbe)
    probe.items, probe.prev_t, probe.error = [], None, None
    probe.step(object())                     # a record it can't read
    assert probe.error is not None and probe.finish() == {"error": probe.error, "items": []}


def test_each_probe_item_goes_to_one_cast_of_its_kind():
    from tests.moments.helpers import round_of

    util = [toy_ability("Gumshoe", "4_TripWire", 185, 240, 0, t=-8.0, t1=20.0, kind="GameObject"),
            toy_ability("Gumshoe", "4_TripWire", 120, 240, 0, t=-6.0, t1=20.0, kind="GameObject"),
            toy_ability("Gumshoe", "E_PossessableCamera", 150, 150, 0, t=3.0, t1=20.0, kind="GameObject")]
    players = {0: still("A", 120, 110, 270), 5: still("B", 400, 200, 0)}
    rnd, data = round_of(players, util=util, geo=door_hall(), t_end=20.0)
    item = lambda kind, t0, front: {"what": "device", "kind": kind, "by": 0, "t0": t0, "t1": 20.0,  # noqa: E731
                                    "up_s": 10.0, "front_s": front, "held_m2s": 1.0, "crossed_s": 0.0}
    holds = {"error": None, "items": [item("trip", 0.0, 4.0), item("trip", 0.0, 6.0), item("camera", 3.0, 9.0)]}
    out = extract.round_util(rnd, data, holds=holds)
    fronts = sorted(c["hold"]["front_s"] for c in out["casts"] if c["hold"])
    assert fronts == [4.0, 6.0, 9.0], "one item each, never a sum"
    (cam,) = [c for c in out["casts"] if "E_PossessableCamera" in c["names"]]
    assert cam["hold"]["front_s"] == 9.0

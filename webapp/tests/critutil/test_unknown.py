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

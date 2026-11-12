"""Destroyed devices (app/replays/destroyed.py): an own device row with `gone` that was not set off first."""

import random

import pytest

from app.critutil import constants, extract
from app.replays import destroyed
from app.replays.destroyed import DESTROYABLE, destroyed_devices
from tests.critutil.test_extract import GEO, players
from tests.moments.helpers import round_of
from tests.replays.control_toys import toy_ability

SIDES = {0: "A", 1: "A", 5: "B", 6: "B"}
DECIDED = 30.0


def row(code, name, by=1, t=0.0, t1=20.0, u=100, v=200, **kw):
    return {"k": "ability", "kind": "GameObject", "code": code, "name": name, "by": by, "t": t, "t1": t1,
            "u": u, "v": v, **kw}


def trip(**kw):
    kw.setdefault("id", 10)
    return row("Gumshoe", "4_TripWire", **kw)


def second(**kw):
    kw.setdefault("id", 11)
    return row("Gumshoe", "4_TripWire_SecondWire", **kw)


def tether(t, target=5, by=1, name="4_TripWire", k="status", **kw):
    return {"k": k, "t": t, "t1": t + 2.0, "by": by, "target": target, "code": "Gumshoe", "name": name,
            "status": "tethered", **kw}


def found(util, decided=DECIDED):
    return destroyed_devices(util, SIDES, decided)


def test_constants_equal_critical_utils():
    assert destroyed.ALARMBOT_YAW_WINDOW_S == constants.ALARMBOT_YAW_WINDOW_S
    assert destroyed._EPS == extract._EPS


def test_a_trip_with_gone_and_no_fx_is_one_event():
    (e,) = found([trip(gone=17.219, t1=17.235, z=5)])
    assert e == {"t": 17.219, "owner": 1, "side": "A", "device": "trip", "u": 100, "v": 200, "z": 5, "id": 10}


def test_a_trip_that_fired_first_is_not_destroyed():
    assert found([trip(gone=17.2, fx=15.0)]) == []
    assert found([trip(gone=17.2, fx=[[15.0, 3]])]) == []
    assert found([trip(gone=17.2, raised=[15.0])]) == []


def test_fx_equal_to_gone_is_fired():
    assert found([trip(gone=17.2, fx=17.2)]) == []


def test_fx_after_gone_is_still_destroyed():
    assert len(found([trip(gone=15.0, fx=17.2)])) == 1


def test_a_camera_that_only_closed_is_not_destroyed():
    assert found([row("Gumshoe", "E_PossessableCamera", t1=12.0)]) == []


def test_gone_after_the_round_was_decided_is_not_destroyed():
    assert found([trip(gone=31.0)]) == []
    assert found([trip(gone=29.0)]) != []


def test_an_unknown_owner_is_not_destroyed():
    assert found([trip(gone=10.0, by=None)]) == []
    assert found([trip(gone=10.0, by=9)]) == []


def test_a_smoke_with_gone_is_not_a_device():
    assert found([row("Clay", "E_Boomba", gone=10.0), row("Sequoia", "E_Shield", gone=10.0)]) == []


def test_an_alarmbot_that_chased_before_it_was_shot_fired():
    bot = row("Killjoy", "Q_StealthAlarmbot", t1=13.234, gone=13.234, yaws=[[0.0, 216], [12.303, 47], [12.405, 42]])
    assert found([bot]) == []


def test_an_alarmbot_with_only_its_first_yaw_is_destroyed():
    bot = row("Killjoy", "Q_StealthAlarmbot", t1=13.234, gone=13.234, yaws=[[0.0, 216]])
    (e,) = found([bot])
    assert e["device"] == "alarmbot"


def test_an_alarmbot_yaw_long_before_the_close_is_not_a_chase():
    bot = row("Killjoy", "Q_StealthAlarmbot", t1=20.0, gone=20.0, yaws=[[0.0, 216], [3.0, 47]])
    assert len(found([bot])) == 1


def test_a_trip_that_tethered_an_enemy_first_was_set_off():
    assert found([trip(gone=17.2), tether(15.0)]) == []


def test_a_tether_on_a_teammate_is_no_trigger():
    assert len(found([trip(gone=17.2), tether(15.0, target=0)])) == 1


def test_a_tether_by_someone_else_is_no_trigger():
    assert len(found([trip(gone=17.2), tether(15.0, by=0)])) == 1


def test_a_tether_after_gone_is_no_trigger():
    assert len(found([trip(gone=15.0), tether(17.2)])) == 1


@pytest.mark.parametrize("code,name,device", [
    ("Gumshoe", "4_TripWire", "trip"), ("Gumshoe", "4_TripWire_SecondWire", "trip"),
    ("Gumshoe", "E_PossessableCamera", "camera"), ("Killjoy", "Q_StealthAlarmbot", "alarmbot"),
    ("Killjoy", "E_Turret", "turret"), ("Cable", "Q_SoundSensor", "sound_sensor"),
    ("BountyHunter", "E_Divebomb", "haunt"), ("BountyHunter", "E_LoSReveal_Source_Reactivate", "haunt"),
    ("Hunter", "Q_SonarBolt", "recon"), ("Hunter", "Q_RevealBolt", "recon"),
])
def test_each_destroyable_name_maps_to_its_device(code, name, device):
    (e,) = found([row(code, name, gone=10.0, id=77)])
    assert e["device"] == device and e["t"] == 10.0
    assert DESTROYABLE[(code, name)] == device


def test_a_sonar_ping_is_not_a_device():
    assert found([row("Hunter", "Q_SonarPing", gone=10.0)]) == []


def test_haunt_and_recon_without_a_reveal_are_destroyed_one_each():
    out = found([row("BountyHunter", "E_LoSReveal_Source_Reactivate", gone=10.0, id=1),
                 row("Hunter", "Q_SonarBolt", by=0, gone=11.0, id=2)])
    assert [(e["device"], e["owner"]) for e in out] == [("haunt", 1), ("recon", 0)]


def test_haunt_and_recon_that_revealed_an_enemy_first_are_not_destroyed():
    haunt = row("BountyHunter", "E_LoSReveal_Source_Reactivate", gone=10.0, id=1)
    ping = {"k": "reveal", "t": 8.0, "t1": 9.0, "by": 1, "target": 5, "code": "BountyHunter",
            "name": "E_LoSReveal_Source_Reactivate"}
    assert found([haunt, ping]) == []
    bolt = row("Hunter", "Q_SonarBolt", gone=10.0, id=2)
    reveal = {"k": "reveal", "t": 8.0, "t1": 9.0, "by": 1, "target": 5, "code": "Hunter", "name": "Q_SonarPing"}
    assert found([bolt, reveal]) == []


# --- trips: a main row and its SecondWire are one trip

def paired(gone_on):
    main = trip(id=100, t1=17.3, u=100, v=200, z=9)
    sec = second(id=104, t1=17.3, u=300, v=400, z=7)
    {"main": main, "second": sec}[gone_on]["gone"] = 17.219
    return [main, sec]


@pytest.mark.parametrize("gone_on", ["main", "second"])
@pytest.mark.parametrize("seed", [None, 1, 2, 3])
def test_a_paired_trip_is_one_event_at_the_midpoint(gone_on, seed):
    util = paired(gone_on)
    if seed is not None:
        random.Random(seed).shuffle(util)
    (e,) = found(util)
    assert (e["t"], e["u"], e["v"], e["z"], e["id"], e["device"]) == (17.219, 200.0, 300.0, 9, 100, "trip")
    assert e["wire"] == [[100, 200], [300, 400]]


def test_a_pair_is_matched_by_end_when_the_main_row_has_one():
    main = trip(id=100, end=[300, 400], t1=17.3)
    sec = second(id=104, u=300, v=400, t1=17.3, gone=17.2)
    other = second(id=101, u=999, v=999, t1=17.3)       # nearer id, but not the end
    (e,) = found([main, sec, other])
    assert (e["u"], e["v"], e["id"]) == (200.0, 300.0, 100)


def test_a_main_row_with_an_end_uses_the_midpoint_of_it_and_the_end():
    (e,) = found([trip(id=100, end=[300, 400], gone=17.2)])
    assert (e["u"], e["v"]) == (200.0, 300.0)
    assert e["wire"] == [[100, 200], [300, 400]]


def test_both_rows_with_gone_take_the_earlier():
    main, sec = paired("main")
    sec["gone"] = 17.0
    (e,) = found([main, sec])
    assert e["t"] == 17.0


def test_a_pair_whose_main_row_fired_is_not_destroyed():
    main, sec = paired("second")
    main["fx"] = 15.0
    assert found([main, sec]) == []


def test_two_distinct_trips_of_one_player_are_two_events():
    util = [trip(id=100, t1=17.3, u=100, v=100, gone=17.2), second(id=101, t1=17.3, u=200, v=100),
            trip(id=200, t1=25.3, u=1000, v=1000), second(id=201, t1=25.3, u=1100, v=1000, gone=25.2)]
    out = found(util)
    assert [(e["t"], e["id"], e["u"]) for e in out] == [(17.2, 100, 150.0), (25.2, 200, 1050.0)]


def test_a_trip_pair_t1_far_apart_is_two_trips():
    util = [trip(id=100, t1=17.3, gone=17.2), second(id=101, t1=40.0, gone=39.9)]
    assert len(found(util, decided=60.0)) == 2


def test_an_orphan_second_wire_with_gone_is_one_trip():
    (e,) = found([second(id=104, gone=17.2, u=300, v=400)])
    assert (e["device"], e["id"], e["u"], e["v"]) == ("trip", 104, 300, 400)


@pytest.mark.parametrize("own_second", [False, True])
def test_an_orphan_anchor_and_a_new_trip_reusing_its_endpoint_are_two_events(own_second):
    orphan = second(id=5, t=0.0, t1=10.0, gone=10.0, u=50, v=60)
    main = trip(id=12, t=20.0, t1=30.0, gone=29.0, u=100, v=200, end=[50, 60])
    util = [orphan, main]
    if own_second:
        util.append(second(id=13, t=20.0, t1=30.0, u=50, v=60))
    old, new = found(util)
    assert (old["t"], old["id"], old["u"], old["v"]) == (10.0, 5, 50, 60)
    assert (new["t"], new["id"], new["u"], new["v"]) == (29.0, 12, 75.0, 130.0)
    assert new["wire"] == [[100, 200], [50, 60]]


def test_two_trips_one_tethers_an_enemy_the_other_is_shot():
    t1 = trip(id=100, u=100, v=100, t=0.0, t1=20.0, gone=18.0)
    t2 = trip(id=200, u=500, v=500, t=1.0, t1=20.0, gone=18.0)
    by_id = tether(15.0, id=100)
    (e,) = found([t1, t2, by_id])
    assert e["id"] == 200


def test_two_trips_with_an_unnamed_tether_go_to_the_latest_placed_as_critical_util_does():
    # the effect row has no id: Critical Util gives it to the cast placed latest (max by t), here trip 2, so trip 1
    # is the one destroyed untouched
    t1 = trip(id=100, u=100, v=100, t=0.0, t1=20.0, gone=18.0)
    t2 = trip(id=200, u=500, v=500, t=1.0, t1=20.0, gone=18.0)
    (e,) = found([t1, t2, tether(15.0)])
    assert e["id"] == 100


def test_a_tether_by_id_of_the_second_wire_belongs_to_its_pair():
    main, sec = paired("main")
    assert found([main, sec, tether(15.0, id=104)]) == []


# --- parity with Critical Util's "broken"

def _critutil_broken(util):
    rnd, data = round_of(players(), util=list(util), geo=GEO, t_end=30.0)
    out = extract.round_util(rnd, data)
    devices = [c for c in out["casts"] if "device" in c["kinds"] and c["by"] == 1]
    broken = any(c["set_off_kind"] == "broken" for c in devices)
    # Haunt/recon are sight casts in round_util, not device kinds. Exercise Critical Util's underlying
    # effect matching and set-off rules for these destroyable objects without changing that taxonomy.
    casts = extract.build_casts(rnd, data)
    effects = extract._effects(rnd, data, casts)
    for c in casts:
        if c.by != 1 or not any(DESTROYABLE.get((r.get("code"), r.get("name"))) in ("haunt", "recon")
                                for r in c.rows):
            continue
        enemy_eff = [e for e in effects[c.i] if SIDES.get(e["target"]) not in (None, SIDES[c.by])]
        seen = [t for t in (extract._trigger(c), *(e["t0"] for e in enemy_eff))
                if t is not None and t >= c.t - extract._EPS]
        _, kind = extract._set_off(rnd, c, enemy_eff, min(seen) if seen else None)
        broken |= kind == "broken"
    return broken, data


def ab(code, name, **kw):
    return toy_ability(code, name, 380, 160, 1, t=0.0, t1=kw.pop("t1", 20.0), kind="GameObject", **kw)


PARITY = {
    "trip_gone": [ab("Gumshoe", "4_TripWire", gone=17.2)],
    "trip_fx_first": [ab("Gumshoe", "4_TripWire", gone=17.2, fx=15.0)],
    "trip_fx_tie": [ab("Gumshoe", "4_TripWire", gone=17.2, fx=17.2)],
    "trip_tether_first": [ab("Gumshoe", "4_TripWire", gone=17.2), tether(15.0)],
    "trip_pair": [ab("Gumshoe", "4_TripWire", gone=17.2, id=1), ab("Gumshoe", "4_TripWire_SecondWire", id=2)],
    "camera_gone": [ab("Gumshoe", "E_PossessableCamera", gone=12.0, t1=12.0)],
    "camera_closed": [ab("Gumshoe", "E_PossessableCamera", t1=12.0)],
    "turret_gone": [ab("Killjoy", "E_Turret", gone=12.0, t1=12.0)],
    "turret_damaged_enemy_then_shot": [ab("Killjoy", "E_Turret", gone=12.0, t1=12.0),
        {"k": "damage", "t": 5.0, "by": 1, "target": 5, "code": "Killjoy", "name": "E_Turret", "src": "ability"}],
    "turret_gun_hit_then_shot": [ab("Killjoy", "E_Turret", gone=12.0, t1=12.0, id=77),
        {"k": "damage", "t": 5.0, "by": 1, "target": 5, "id": 77, "src": "weapon"}],
    "haunt_gone": [ab("BountyHunter", "E_Divebomb", gone=12.0, t1=12.0)],
    "haunt_with_seize_status": [ab("BountyHunter", "E_Divebomb", gone=12.0, t1=12.0),
        {"k": "status", "t": 8.0, "t1": 10.0, "by": 1, "target": 5, "code": "BountyHunter",
         "name": "Q_TetherGrenade_SphereExpansion", "status": "tethered", "id": 999}],
    "alarmbot_chased": [ab("Killjoy", "Q_StealthAlarmbot", gone=13.2, t1=13.2,
                           yaws=[[0.0, 216], [12.3, 47]])],
    "alarmbot_shot": [ab("Killjoy", "Q_StealthAlarmbot", gone=13.2, t1=13.2, yaws=[[0.0, 216]])],
    "sound_sensor_gone": [ab("Cable", "Q_SoundSensor", gone=12.0, t1=12.0)],
}


@pytest.mark.parametrize("case", sorted(PARITY))
def test_the_verdict_equals_critical_utils_broken(case):
    broken, data = _critutil_broken(PARITY[case])
    mine = destroyed_devices(data["util"], {0: "A", 1: "A", 5: "B", 6: "B"}, data["t_decided"])
    assert bool(mine) == broken
    assert broken == (case in {"trip_gone", "trip_pair", "camera_gone",
                               "turret_gone", "turret_gun_hit_then_shot", "haunt_gone", "haunt_with_seize_status",
                               "alarmbot_shot", "sound_sensor_gone"}), "the toy no longer exercises it"

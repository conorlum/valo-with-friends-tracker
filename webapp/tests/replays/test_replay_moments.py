import copy
import json

from app.services.replay_moments import (
    attach_deaths,
    build_payload,
    player_info,
    player_summary,
)


def fight(slot=1, i=0, t0=10.0, t1=14.0, died=None, kds=None):
    return {"slot": slot, "i": i, "t0": t0, "t1": t1, "died": died,
            "kill_detail": kds or []}


def kd(clean=True, band="on", ttk_shot=1.0, ttk_spot=1.5, first_shot=3.0, spot=2.0):
    return {"clean": clean, "band": band, "ttk_shot": ttk_shot,
            "ttk_spot": ttk_spot, "first_shot": first_shot, "spot": spot}


def test_death_join_window_and_closest():
    f = [fight(died={"t": 20.0, "killer": 7})]
    out = attach_deaths(f, {"1": [{"t": 20.09, "control_m2": 5, "share_of_team": 0.1}]})
    assert out[0]["died"]["control_m2"] == 5 and out[0]["died"]["share"] == 0.1
    out = attach_deaths(f, {"1": [{"t": 20.11, "control_m2": 5, "share_of_team": 0.1}]})
    assert out[0]["died"]["control_m2"] is None and out[0]["died"]["share"] is None
    two = {1: [{"t": 20.08, "control_m2": 1, "share_of_team": 0.1},
               {"t": 20.02, "control_m2": 2, "share_of_team": 0.2}]}
    assert attach_deaths(f, two)[0]["died"]["control_m2"] == 2


def test_attach_does_not_mutate():
    f = [fight(died={"t": 20.0, "killer": 7}), fight(died=None)]
    before = copy.deepcopy(f)
    attach_deaths(f, {"1": [{"t": 20.0, "control_m2": 5, "share_of_team": 0.1}]})
    assert f == before


def ev(*rows):
    return {"A": [list(r) for r in rows], "B": []}


def test_info_merge():
    e = {1: ev((0.0, 3, "footsteps"), (1.9, 3, "footsteps"), (3.8, 3, "footsteps"),
               (10.0, 3, "footsteps"), (12.1, 3, "footsteps"), (5.0, 4, "footsteps"))}
    r = player_info(3, e, {})
    assert [m["t"] for m in r["heard"]] == [0.0, 10.0, 12.1]
    assert r["heard"][0]["round"] == 1


def test_info_both_sides_and_reasons_separate():
    e = {2: {"A": [[1.0, 3, "recon"]], "B": [[1.5, 3, "footsteps"], [1.2, 3, "shock"]]}}
    r = player_info(3, e, {})
    assert len(r["heard"]) == 1
    assert [(m["reason"]) for m in r["found_by_utility"]] == ["recon", "shock"]


def test_gunfire_seen_and_ignored():
    moments = {1: {"status": "ok", "fights": [fight(slot=3, t0=10, t1=14)]}}
    e = {1: ev((12.0, 3, "gunfire"), (14.5, 3, "gunfire"), (1.0, 3, "damage"),
               (2.0, 3, "kill"), (3.0, 3, "seen"), (6.0, 3, "seen"), (6.5, 3, "seen"))}
    r = player_info(3, e, moments)
    assert [m["t"] for m in r["gunfire_outside"]] == [14.5]
    assert r["seen"] == 2
    assert r["found_by_utility"] == []


def test_medians_threshold_and_buckets():
    def m(n, **k):
        return {1: {"status": "ok", "fights": [fight(slot=3, kds=[kd(**k) for _ in range(n)])]}}
    s = player_summary(3, m(4), {})
    assert s["medians"]["all"] == {"ttk_shot": None, "ttk_spot": None, "spot_to_shot": None, "n": 4}
    s = player_summary(3, m(5), {})
    assert s["medians"]["all"] == {"ttk_shot": 1.0, "ttk_spot": 1.5, "spot_to_shot": 1.0, "n": 5}
    assert s["medians"]["on"]["n"] == 5 and s["medians"]["off"]["n"] == 0
    kds = [kd() for _ in range(5)] + [kd(clean=False)] + [kd(band=None) for _ in range(1)]
    mm = {1: {"status": "ok", "fights": [fight(slot=3, kds=kds)]}}
    s = player_summary(3, mm, {})
    assert s["medians"]["all"]["n"] == 6 and s["medians"]["on"]["n"] == 5


def test_expensive_deaths():
    moments = {1: {"status": "ok", "fights": [fight(slot=3, i=4, died={"t": 30.0, "killer": 1})]}}
    deaths = {1: {"3": [{"t": 30.0, "control_m2": 9, "share_of_team": 0.3},
                        {"t": 50.0, "control_m2": None, "share_of_team": None}]},
              2: {3: [{"t": 5.0, "control_m2": 20, "share_of_team": 0.5},
                      {"t": 6.0, "control_m2": 1, "share_of_team": 0.1}]}}
    d = player_summary(3, moments, deaths)["expensive_deaths"]
    assert [(x["round"], x["control_m2"]) for x in d] == [(2, 20), (1, 9), (2, 1)]
    assert d[1]["fight_i"] == 4 and d[0]["fight_i"] is None


def test_build_payload():
    moments = {1: {"status": "ok", "fights": [fight(slot=3)]},
               2: {"status": "failed", "fights": None}}
    p = build_payload(players={"3": {"name": "x", "team": "A"}, "4": {"name": "y", "team": "B"}},
                      attack_round1=[3], moments=moments, deaths={}, events={}, stale=0)
    assert p["rounds"]["2"] == {"status": "failed", "fights": []}
    assert set(p["summary"]) == {"3", "4"} and "info" in p["summary"]["3"]
    assert json.loads(json.dumps(p)) == p

import copy
import json

from app.services.replay_moments import (
    attach_deaths,
    attach_team_fights,
    build_payload,
    expensive_fights,
    player_info,
    player_summary,
    team_fight_of,
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


def team_fight(deaths, lost, wipe=False):
    return {"t0": deaths[0][1], "t1": deaths[-1][1], "deaths": [list(d) for d in deaths], "wipe": wipe,
            "lost_m2": lost}


def test_team_fight_is_seen_from_the_dying_players_side():
    tf = [team_fight([(3, 30.0, "A"), (7, 31.0, "B"), (4, 33.0, "A")], {"A": 900.0, "B": 100.0})]
    mine = team_fight_of(3, 30.05, tf)
    assert mine == {"t0": 30.0, "t1": 33.0, "ours": 2, "theirs": 1, "swing_m2": -800.0, "wipe": False}
    assert team_fight_of(7, 31.0, tf)["swing_m2"] == 800.0 and team_fight_of(7, 31.0, tf)["ours"] == 1
    assert team_fight_of(3, 30.2, tf) is None and team_fight_of(3, 30.0, None) is None


def test_expensive_fight_tag_needs_the_cut_off_and_no_wipe():
    f = [fight(slot=3, died={"t": 30.0, "killer": 7}), fight(slot=3, i=1, died=None)]
    before = copy.deepcopy(f)
    for lost, wipe, tagged in (({"A": 600.0, "B": 100.0}, False, True), ({"A": 599.0, "B": 100.0}, False, False),
                               ({"A": 900.0, "B": 0.0}, True, False)):
        out = attach_team_fights(f, [team_fight([(3, 30.0, "A")], lost, wipe)])
        assert ("expensive_fight" in out[0].get("tags", [])) is tagged, (lost, wipe)
        assert out[0]["team_fight"]["wipe"] is wipe and "team_fight" not in out[1]
    assert f == before


def test_expensive_fights_summary_order_and_filters():
    moments = {1: {"status": "ok", "fights": [fight(slot=3, i=0, died={"t": 10.0, "killer": 7})]},
               2: {"status": "ok", "fights": [fight(slot=3, i=2, died={"t": 20.0, "killer": 7}),
                                              fight(slot=3, i=3, died={"t": 40.0, "killer": 7})]},
               3: {"status": "ok", "fights": [fight(slot=3, i=0, died={"t": 5.0, "killer": 7})]}}
    tfs = {1: [team_fight([(3, 10.0, "A")], {"A": 300.0, "B": 0.0})],
           2: [team_fight([(3, 20.0, "A")], {"A": 1000.0, "B": 0.0}),
               team_fight([(3, 40.0, "A")], {"A": 0.0, "B": 50.0})],          # positive: not listed
           "3": [team_fight([(3, 5.0, "A")], {"A": 5000.0, "B": 0.0}, wipe=True)]}   # a wipe: not listed
    rows = expensive_fights(3, moments, tfs)
    assert [(r["round"], r["swing_m2"], r["fight_i"]) for r in rows] == [(2, -1000.0, 2), (1, -300.0, 0)]


def test_a_revived_players_two_deaths_in_one_team_fight_list_it_once():
    moments = {1: {"status": "ok", "fights": [fight(slot=3, i=0, died={"t": 10.0, "killer": 7}),
                                              fight(slot=3, i=1, died={"t": 18.0, "killer": 7})]}}
    tfs = {1: [team_fight([(3, 10.0, "A"), (7, 14.0, "B"), (3, 18.0, "A")], {"A": 900.0, "B": 0.0})]}
    assert [(r["round"], r["fight_i"]) for r in expensive_fights(3, moments, tfs)] == [(1, 0)]
    tagged = attach_team_fights(moments[1]["fights"], tfs[1])
    assert all("expensive_fight" in f["tags"] for f in tagged), "each personal fight is still tagged"


def test_build_payload():
    moments = {1: {"status": "ok", "fights": [fight(slot=3)]},
               2: {"status": "failed", "fights": None}}
    p = build_payload(players={"3": {"name": "x", "team": "A"}, "4": {"name": "y", "team": "B"}},
                      attack_round1=[3], moments=moments, deaths={}, events={}, stale=0)
    assert p["rounds"]["2"] == {"status": "failed", "fights": []}
    assert set(p["summary"]) == {"3", "4"} and "info" in p["summary"]["3"]
    assert p["summary"]["3"]["expensive_fights"] == []
    assert json.loads(json.dumps(p)) == p
    died = {1: {"status": "ok", "fights": [fight(slot=3, died={"t": 12.0, "killer": 4})]}}
    p = build_payload(players={"3": {"name": "x", "team": "A"}}, attack_round1=[3], moments=died, deaths={},
                      events={}, stale=0, team_fights={1: [team_fight([(3, 12.0, "A")], {"A": 800.0, "B": 0.0})]})
    assert "expensive_fight" in p["rounds"]["1"]["fights"][0]["tags"]
    assert p["summary"]["3"]["expensive_fights"][0]["swing_m2"] == -800.0

import pytest

from app.coach import areas, brief, snapshot
from app.critutil import info
from tests.coach import toys
from tests.coach.test_areas import painted_object, write_paint
from tests.coach.test_snapshot import CONTEXT, SHARES


def _info_table(n=11, agent="Sova", ability="Owl Drone"):
    return info.summarise([
        {"agent": agent, "ability": ability, "match": i % 3,
         "info": {"cleared_m2": 100.0 * i, "located": 0, "hold_s": float(i)}}
        for i in range(n)
    ], ci=lambda *_: (0.0, 0.0))


def _info_cast(**changes):
    return {"slot": 0, "t": 10.0, "agent": "Sova", "ability": "Owl Drone",
            "info": {"cleared_m2": 340.0, "located": 2, "hold_s": 6.5}, **changes}


def _compact_info_table(n=11, agent="Sova", ability="Owl Drone"):
    table = _info_table(n, agent, ability)
    return {ability_id: {metric: {"n": n, "points": points} for metric, points in metrics.items()}
            for ability_id, metrics in table["percentiles"].items()}


def test_util_info_figures_and_both_ability_percentiles():
    (fact,) = brief.util_facts({"casts": [_info_cast()]}, 0, info_table=_compact_info_table())
    assert {k: fact[k] for k in ("cleared_m2", "located", "held_back_s")} == {
        "cleared_m2": 340.0, "located": 2, "held_back_s": 6.5}
    assert fact["ability_pct"] == {"cleared_m2": 34, "hold_s": 65}
    assert fact["ability_n"] == {"cleared_m2": 11, "hold_s": 11}
    assert "hold_s" not in fact


@pytest.mark.parametrize("n", [9, 10])
def test_util_info_percentile_needs_ten_casts(n):
    (fact,) = brief.util_facts({"casts": [_info_cast()]}, 0, info_table=_compact_info_table(n))
    assert ("ability_pct" in fact) is (n >= 10)
    assert fact.get("ability_n") == ({"cleared_m2": n, "hold_s": n} if n >= 10 else None)
    assert fact["cleared_m2"] == 340.0


@pytest.mark.parametrize("measured", [None, {"cleared_m2": 0.0, "located": 0, "hold_s": 0.0}])
def test_util_info_unknown_is_distinct_from_measured_zero(measured):
    cast = _info_cast(info=measured)
    if measured is None:
        cast.pop("info")  # old stored row
    (fact,) = brief.util_facts({"casts": [cast]}, 0, info_table=_compact_info_table())
    keys = {"cleared_m2", "located", "held_back_s", "ability_pct", "ability_n"}
    assert keys.intersection(fact) == (keys if measured is not None else set())


def test_util_info_without_a_stored_table_still_gives_the_measures():
    (fact,) = brief.util_facts({"casts": [_info_cast()]}, 0)
    assert fact["cleared_m2"] == 340.0 and fact["held_back_s"] == 6.5
    assert "ability_pct" not in fact and "ability_n" not in fact


def test_util_info_keeps_each_metrics_own_sample_size():
    table = _compact_info_table()
    table["Sova/Owl Drone"]["hold_s"]["n"] = 12
    (fact,) = brief.util_facts({"casts": [_info_cast()]}, 0, info_table=table)
    assert fact["ability_n"] == {"cleared_m2": 11, "hold_s": 12}
    assert fact["ability_pct"] == {"cleared_m2": 34, "hold_s": 65}


@pytest.mark.parametrize("value,percentile", [(-1, 0), (340, 34), (1001, 100)])
def test_util_info_percentiles_are_bounded(value, percentile):
    cast = _info_cast(info={"cleared_m2": value, "located": 0, "hold_s": 0})
    (fact,) = brief.util_facts({"casts": [cast]}, 0, info_table=_compact_info_table())
    assert fact["ability_pct"]["cleared_m2"] == percentile


@pytest.mark.parametrize("agent,ability,metrics", [
    ("Fade", "Nightfall", {"cleared_m2"}),
    ("Cypher", "Trapwire", {"hold_s"}),
    ("KAY/O", "ZERO/point", {"cleared_m2"}),
])
def test_util_info_ranks_only_the_abilitys_metrics(agent, ability, metrics):
    cast = _info_cast(agent=agent, ability=ability)
    (fact,) = brief.util_facts({"casts": [cast]}, 0,
                              info_table=_compact_info_table(agent=agent, ability=ability))
    assert set(fact["ability_pct"]) == set(fact["ability_n"]) == metrics


@pytest.mark.parametrize("measured", [None, {"cleared_m2": 0.0, "located": 0, "hold_s": 0.0}])
def test_nightfall_hits_note_only_without_info(measured):
    (fact,) = brief.util_facts({"casts": [_info_cast(agent="Fade", ability="Nightfall", info=measured)]}, 0)
    assert fact.get("hits_recorded") == ("damage only" if measured is None else None)


def test_info_figures_survive_the_round_snapshot_into_the_brief(tmp_path):
    util = {"1": {"casts": [_info_cast()]}}
    context = {**CONTEXT, "util_info": _compact_info_table()}
    data = snapshot.assemble(context, {1: toys.round_blob(toys.ten(), n=1)},
                             {1: ("ok", False, SHARES)}, util=util)
    snapshot.save(data, tmp_path)
    fact = brief.build(snapshot.load(tmp_path), 0)["rounds"][0]["util_facts"][0]
    assert fact["ability_pct"] == {"cleared_m2": 34, "hold_s": 65}


def test_no_gun_deaths_is_in_the_brief_with_its_round_fight_and_time(tmp_path, monkeypatch):
    from tests.coach.test_fights import snapshot as fight_snapshot

    match = snap(tmp_path, {5: 0})
    toy = fight_snapshot(my="Knife")
    monkeypatch.setattr(match, "view", toy.view)
    monkeypatch.setattr(match, "control", toy.control)
    built = brief.build(match, 0)
    assert built["no_gun_deaths"] == {
        "deaths": [{"round": 5, "fight": 0, "t": 10, "item": "Knife"}],
        "count": 1, "of_deaths": 1, "flag": False,
    }
    assert built["rounds"][0]["fights"][0]["t0"] == 10


def test_lone_rifle_death_has_a_fight_and_handover_flag(tmp_path, monkeypatch):
    from tests.coach.test_fights import snapshot as fight_snapshot

    match = snap(tmp_path, {1: 0})
    toy = fight_snapshot()
    monkeypatch.setattr(match, "view", toy.view)
    monkeypatch.setattr(match, "control", toy.control)
    (row,) = brief.build(match, 0)["rounds"]
    (fight,) = row["fights"]
    assert fight["alive"] == {"own": 3, "enemy": 3}
    assert fight["lone"] and fight["death"]["better"]
    assert (fight["my_gun"], fight["enemy_gun"]) == ("Vandal", "Classic")
    assert "flags" not in fight
    flag = next(f for f in row["flags"] if f["kind"] == "gun_handover")
    assert flag["t"] == fight["t0"]
    assert flag["values"]["death"] == fight["death"]


def snap(tmp_path, impacts, kills_by_round=None, controls=None):
    context = {**CONTEXT, "rounds": {}}
    blobs = {}
    for n, impact in impacts.items():
        context["rounds"][str(n)] = {"db": {"winner": "team1" if n % 2 else "team2", "plant": None, "defuse": None},
                                     "stats": {"0": {"kills": 1, "deaths": 0, "impact": impact}}, "kills": {}}
        blobs[n] = toys.round_blob(toys.ten(), kills=(kills_by_round or {}).get(n, ()), n=n)
    context["match"] = {**CONTEXT["match"], "rounds": sorted(impacts)}
    controls = controls or {n: ("ok", False, SHARES) for n in impacts}
    snapshot.save(snapshot.assemble(context, blobs, controls), tmp_path)
    return snapshot.load(tmp_path)


def test_the_brief_names_the_subject_and_every_player(tmp_path):
    b = brief.build(snap(tmp_path, {1: 100}), 0)
    assert b["subject"] == {"slot": 0, "name": "P0#T0", "agent": "Jett", "team": "team1", "side": "A"}
    assert len(b["players"]) == 10 and b["match"]["map"] == "Toy"


def test_rounds_are_ranked_by_the_size_of_the_impact_swing(tmp_path):
    b = brief.build(snap(tmp_path, {1: 50, 2: -400, 3: 120}), 0)
    assert b["ranked"] == [{"round": 2, "impact": -400}, {"round": 3, "impact": 120}, {"round": 1, "impact": 50}]


def test_a_round_carries_its_outcome_stats_and_the_players_kills_and_deaths(tmp_path):
    kills = {1: [{"t": 10.0, "killer": 0, "victim": 5}, {"t": 20.0, "killer": 6, "victim": 0}]}
    (r,) = brief.build(snap(tmp_path, {1: 100}, kills), 0)["rounds"]
    assert r["won"] is True and r["stats"]["impact"] == 100
    assert r["kills"] == [{"t": 10.0, "victim": "P5#T5"}] and r["deaths"] == [20.0]
    assert "first_kill" in [f["kind"] for f in r["flags"]]


def test_a_round_carries_the_role_both_economies_and_the_players_utility(tmp_path):
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {
        "db": {"winner": "team1", "plant": 31.0, "defuse": None}, "kills": {},
        "stats": {str(s): {"loadout": 1000 * (s + 1), "remaining": 100, "impact": 0} for s in range(10)}}}}
    util = [{"k": "flash", "t": 9.0, "by": 0, "u": 0, "v": 0, "hits": [[5, 9.5, 1.5]]},
            {"k": "flash", "t": 11.0, "by": 1, "u": 0, "v": 0, "hits": []}]
    blob = toys.round_blob(toys.ten(), util=util, kills=[{"t": 50.0, "killer": 5, "victim": 0}], t_decided=40.0)
    links = {1: {"sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)}, "db_deaths": []}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {1: ("ok", False, SHARES)}, links), tmp_path)
    (r,) = brief.build(snapshot.load(tmp_path), 0)["rounds"]
    assert r["role"] == "attack" and r["plant"] == 31.0 and r["decided_at"] == 40.0
    assert r["economy"] == {"team1": {"loadout": 15000, "remaining": 500},     # 1000+2000+3000+4000+5000
                            "team2": {"loadout": 40000, "remaining": 500}}     # 6000+...+10000
    assert [u["t"] for u in r["utility"]] == [9.0] and r["utility"][0]["hits"] == [{"name": "P5#T5", "seconds": 1.5}]
    assert r["deaths"] == []                               # the death at 50 s came after the round was decided


def test_gaps_are_listed_per_round_and_only_where_they_exist(tmp_path):
    controls = {1: ("ok", False, SHARES), 2: ("failed", False, None)}
    b = brief.build(snap(tmp_path, {1: 10, 2: 20}, controls=controls), 0)
    assert b["data_gaps"] == {"match": ["areas"], "2": ["control_failed", "team_knowledge"]}


def test_a_map_without_callouts_lists_no_areas_and_says_so(tmp_path):
    b = brief.build(snap(tmp_path, {1: 10}), 0)           # "Toy" is not in the real callouts file
    assert b["areas"] == [] and b["data_gaps"] == {"match": ["areas"]}


def test_brief_lists_the_reviewed_tree_or_approximate_callouts(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    match = snap(tmp_path, {1: 10})
    match.context["match"]["map"] = "Sunset"
    match.asset_key = "Sunset"              # areas follow the layout's asset key, set when the snapshot loads
    assert brief.build(match, 0)["areas"] == [
        {"name": c["name"], "super": c["super"], "parent": None, "approximate": True}
        for c in sorted(areas.callouts("Sunset"), key=lambda c: c["name"])
    ]
    write_paint(tmp_path, painted_object({0: 2}))
    assert brief.build(match, 0)["areas"] == [
        {"name": "Mid", "parent": None, "super": "Mid"},
        {"name": "Top Mid", "parent": "Mid", "super": "Mid"},
        {"name": "A Site", "parent": None, "super": "A"},
    ]

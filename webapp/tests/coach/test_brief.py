from app.coach import brief, snapshot
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT, SHARES


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

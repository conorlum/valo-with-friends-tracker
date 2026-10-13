import pytest

from app.coach import query, snapshot
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT

KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000]]}, "B": {}}
SHARES = {"t": [0.0, 10.0], "cells": {"A Site": 2}, "areas": {"A Site": [[2, 0, 0], [0, 2, 0]]},
          "knew": KNEW, "signals": {"A": [[3.0, 6, "footsteps"], [40.0, 7, "gunfire"]], "B": []}, "group_side": None}
KILLS = [{"t": 20.0, "killer": 5, "victim": 0}]
DAMAGE = {"k": "damage", "t": 13.0, "t1": 13.2, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 3}


@pytest.fixture
def snap(tmp_path):
    context = {**CONTEXT, "rounds": {"1": {"db": {"winner": "team1"}, "kills": {},
                                           "stats": {"0": {"impact": -250, "kill_impact": 0, "death_impact": -250}}},
                                     "2": {"db": {"plant": 12.5, "defuse": 40.0}, "stats": {}, "kills": {}}}}
    hurt = toys.round_blob(toys.ten(), n=2, util=[DAMAGE])
    hurt["player_state"] = {"version": 1, "vitals": {"0": [
        {"t": 13.0, "life": 0, "hp": 61, "sh": 0, "mhp": 100, "msh": 50},
        {"t": 30.0, "life": 0, "hp": 20, "sh": 0, "mhp": 100, "msh": 50}]}}
    first = toys.round_blob(toys.ten(), kills=KILLS, n=1,
                            util=[{"k": "flash", "t": 9.0, "by": 1, "u": 0, "v": 0, "hits": []}])
    first["player_state"] = {"version": 1, "vitals": {"0": [{"t": 13.0, "life": 0, "hp": 61, "sh": 0, "mhp": 100,
                                                             "msh": 50}]}}
    blobs = {1: first, 2: hurt}
    controls = {1: ("ok", False, SHARES), 2: ("not_ready", False, None)}
    snapshot.save(snapshot.assemble(context, blobs, controls), tmp_path)
    return snapshot.load(tmp_path)


def test_where_lists_every_player_with_side_and_state(snap):
    out = query.where(snap, 1, 25.0)
    assert len(out["players"]) == 10
    dead = out["players"][0]
    assert (dead["name"], dead["side"], dead["alive"], dead["died_at"]) == ("P0#T0", "A", False, 20.0)
    assert out["players"][1]["alive"] is True and "u" in out["players"][1]


def test_knew_answers_for_a_side_or_for_a_players_side(snap):
    by_side = query.knew(snap, 1, 15.0, "A")
    assert by_side["side"] == "A" and [e["name"] for e in by_side["enemies"]] == ["P5#T5"]
    assert query.knew(snap, 1, 15.0, "P2")["side"] == "A"
    assert query.knew(snap, 1, 15.0, "B")["enemies"] == []


def test_knew_lists_what_was_heard_apart_from_what_was_seen(snap):
    out = query.knew(snap, 1, 15.0, "A")
    assert out["signals"] == [{"t": 3.0, "name": "P6#T6", "reason": "footsteps"}]      # the 40 s one is later
    assert "no place" in out["note"]


def test_where_gives_health_as_a_word_after_the_last_recorded_hit(snap):
    before, after = query.where(snap, 2, 5.0), query.where(snap, 2, 20.0)
    assert before["players"][0]["health"] is None                  # no hit recorded yet: unknown, not full
    assert after["players"][0]["health"] == "healthy"              # 61 of 100
    assert query.where(snap, 2, 35.0)["players"][0]["health"] == "low"      # 20 of 100
    assert "hp" not in str(after["players"][0]["health"])
    # dead: no health, even though a vitals event exists at 13 s
    assert "health" not in query.where(snap, 1, 25.0)["players"][0]


def test_a_living_player_with_no_position_is_alive_with_no_place(tmp_path):
    blob = toys.round_blob(toys.ten(), n=1, tracks={1: toys.stand(2000, 2000, 0.0, 2.0)})
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {"db": {}, "stats": {}}}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {}), tmp_path)
    out = query.where(snapshot.load(tmp_path), 1, 30.0)["players"][1]
    assert out["alive"] is True and "u" not in out and "v" not in out


def test_knew_without_team_knowledge_says_so(snap):
    assert query.knew(snap, 2, 15.0, "A") == {"available": False, "reason": "team_knowledge"}


def _guarded(tmp_path, group_side):
    shares = {**SHARES, "group_side": group_side}
    links = {1: {"linked": True, "sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)},
                 "db_deaths": []}}
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {"db": {}, "stats": {}}}}
    snapshot.save(snapshot.assemble(context, {1: toys.round_blob(toys.ten(), n=1)}, {1: ("ok", False, shares)},
                                    links), tmp_path)
    return snapshot.load(tmp_path)


def test_knew_and_control_refuse_when_the_side_groups_disagree(tmp_path):
    bad = _guarded(tmp_path, {"A": "defense", "B": "attack"})
    gone = {"available": False, "reason": "side_groups_disagree"}
    assert query.knew(bad, 1, 15.0, "A") == gone
    assert query.control(bad, 1, 0.0, 20.0, "A Site") == gone


def test_agreeing_side_groups_answer_normally(tmp_path):
    good = _guarded(tmp_path / "g", {"A": "attack", "B": "defense"})
    assert query.knew(good, 1, 15.0, "A")["side"] == "A"
    assert "shares" in query.control(good, 1, 0.0, 20.0, "A Site")


def test_distance_between_two_living_players(snap):
    out = query.distance(snap, 1, 10.0, "P0", "P5")
    assert out["metres"] == pytest.approx(121.2, abs=0.1)
    assert query.distance(snap, 1, 25.0, "P0", "P5") == {"available": False, "reason": "P0#T0 was dead at 25.0"}


def test_control_reports_shares_or_says_what_is_missing(snap):
    assert query.control(snap, 1, 0.0, 20.0, "A Site")["shares"] == {"a": 0.5, "b": 0.5, "contested": 0.0, "none": 0.0}
    assert query.control(snap, 2, 0.0, 20.0, "A Site") == {"available": False, "reason": "control_not_ready"}
    with pytest.raises(query.QueryError, match="A Site"):
        query.control(snap, 1, 0.0, 20.0, "Nowhere")


def test_timeline_orders_events_and_can_be_cut_to_a_span(snap):
    events = query.timeline(snap, 1)["events"]
    assert [(e["t"], e["kind"]) for e in events] == [(9.0, "flash"), (20.0, "kill")]
    assert events[1]["killer"] == "P5#T5" and events[1]["victim"] == "P0#T0"
    assert query.timeline(snap, 1, 15.0, 30.0)["events"] == events[1:]


def test_timeline_has_the_plant_the_defuse_and_damage(snap):
    events = query.timeline(snap, 2)["events"]
    assert [(e["t"], e["kind"]) for e in events] == [(12.5, "plant"), (13.0, "damage"), (40.0, "defuse")]
    assert events[1] == {"t": 13.0, "kind": "damage", "by": "P5#T5", "target": "P0#T0", "source": "gun", "hits": 3}


def test_a_kill_after_the_round_was_decided_is_marked(tmp_path):
    blob = toys.round_blob(toys.ten(), kills=[{"t": 50.0, "killer": 5, "victim": 0}], t_decided=40.0)
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {"db": {}, "stats": {}}}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {}), tmp_path)
    (kill,) = query.timeline(snapshot.load(tmp_path), 1)["events"]
    assert kill["after_decided"] is True


def test_impact_is_the_stored_row(snap):
    assert query.impact(snap, 1, "P0")["impact"] == -250
    assert query.impact(snap, 2, "P0") == {"available": False, "reason": "no Impact row for this round"}


def test_bad_rounds_times_and_players_fail_with_the_valid_values(snap):
    with pytest.raises(query.QueryError, match="rounds 1-2"):
        query.where(snap, 9, 1.0)
    with pytest.raises(query.QueryError, match="0.0 to 60.0"):
        query.where(snap, 1, 500.0)
    with pytest.raises(query.QueryError, match="P0#T0"):
        query.distance(snap, 1, 1.0, "nobody", "P5")


def test_every_call_is_logged_with_an_id_errors_included(snap, tmp_path):
    first = query.run(snap, tmp_path, "where", ["1", "25"])
    second = query.run(snap, tmp_path, "where", ["9", "1"])
    third = query.run(snap, tmp_path, "nonsense", [])
    assert (first["id"], second["id"], third["id"]) == ("q1", "q2", "q3")
    assert "error" in second and "error" in third
    log = query.read_log(tmp_path)
    assert log["q1"]["cmd"] == "where" and log["q1"]["args"] == ["1", "25"]
    assert log["q1"]["result"]["players"][0]["died_at"] == 20.0


@pytest.mark.parametrize("command, args", [
    ("where", ["7.0", "10"]),                # a round is a whole number
    ("where", ["1", "0:41"]),                # a time is seconds, not a clock
    ("distance", ["1", "ten", "P0", "P5"]),
    ("where", ["1"]),                        # too few arguments
])
def test_malformed_arguments_are_an_answer_not_a_crash(snap, tmp_path, command, args):
    out = query.run(snap, tmp_path, command, args)
    assert "error" in out and out["id"] == "q1" and query.read_log(tmp_path)["q1"]["args"] == args


def test_a_fault_inside_a_query_raises_instead_of_posing_as_bad_arguments(snap, monkeypatch):
    def broken(snap, n, t):
        raise ValueError("a bug in the query")
    monkeypatch.setitem(query.COMMANDS, "where", broken)
    with pytest.raises(ValueError, match="a bug"):
        query.answer(snap, "where", ["1", "5"])


def test_answer_computes_without_logging(snap, tmp_path):
    assert query.answer(snap, "impact", ["1", "P0"])["impact"] == -250
    assert query.read_log(tmp_path) == {}


def test_distance_without_a_position_says_so(snap):
    snap.view(1).blob["tracks"]["5"] = []                  # alive, and no track sample
    assert query.distance(snap, 1, 10.0, "P0", "P5") == {"available": False, "reason": "no position for one of them at 10.0"}

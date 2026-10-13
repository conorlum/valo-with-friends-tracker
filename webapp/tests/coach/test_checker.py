import pytest

from app.coach import checker, query, snapshot
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT

THEMES = ("trading", "rotation")
KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000]]}, "B": {}}
SHARES = {"t": [0.0], "cells": {}, "areas": {}, "knew": KNEW, "group_side": None}


@pytest.fixture
def snap(tmp_path):
    first = toys.round_blob(toys.ten(), kills=[{"t": 20.0, "killer": 5, "victim": 0}], n=1)
    first["player_state"] = {"version": 1, "vitals": {"0": [
        {"t": 13.0, "life": 0, "hp": 61, "sh": 0, "mhp": 100, "msh": 50}]}}
    context = {**CONTEXT, "rounds": {"1": {"db": {}, "kills": {}, "stats": {
        "0": {"impact": -250, "kill_impact": 0, "death_impact": -250}}}}}
    blobs = {1: first, 2: toys.round_blob(toys.ten(), n=2)}
    controls = {1: ("ok", False, SHARES), 2: ("not_ready", False, None)}
    snapshot.save(snapshot.assemble(context, blobs, controls), tmp_path)
    return snapshot.load(tmp_path)


@pytest.fixture
def log(snap, tmp_path):
    query.run(snap, tmp_path, "distance", ["1", "10", "P0", "P5"])      # q1: 121.2 m
    query.run(snap, tmp_path, "knew", ["1", "15", "A"])                 # q2: knew before the death
    query.run(snap, tmp_path, "where", ["9", "1"])                      # q3: an error answer
    query.run(snap, tmp_path, "knew", ["1", "30", "A"])                 # q4: knew AFTER the death
    query.append(tmp_path, "flag", [1, 20.0, "death_untraded_alone"], FLAG)      # q5: a flag
    query.run(snap, tmp_path, "where", ["1", "10"])                     # q6: all ten alive, slots 0-9 in the answer
    query.run(snap, tmp_path, "knew", ["1", "15", "B"])                 # q7: what the ENEMY side knew
    return checker.trusted_log(query.read_log(tmp_path), snap, {"q5": FLAG})


FLAG = {"round": 1, "t": 20.0, "nearest_teammate_m": 61.0}


def point(**over):
    base = {"polarity": "improve", "theme": "trading", "title": "Died alone",
            "body": "You were 121.2 m from P5#T5 and nobody could trade you.",
            "instances": [{"round": 1, "t": 20.0, "caption": "first death", "evidence": ["q1", "q2"]}]}
    return {**base, **over}


def problems(snap, log, **over):
    return checker.check_point(point(**over), log, snap, THEMES, 0)      # slot 0 is the coached player, side A


def test_a_grounded_point_passes(snap, log):
    assert problems(snap, log) == []


def test_append_continues_the_log_numbering(log):
    assert log["q5"]["cmd"] == "flag" and log["q5"]["result"]["nearest_teammate_m"] == 61.0


@pytest.mark.parametrize("change, expected", [
    ({"polarity": "bad"}, "polarity"),
    ({"theme": "aim"}, "theme"),
    ({"title": "  "}, "title is empty"),
    ({"instances": []}, "no instances"),
    ({"instances": [{"round": 9, "t": 1.0, "caption": "", "evidence": ["q1", "q2"]}]}, "round 9"),
    ({"instances": [{"round": 1, "t": 500.0, "caption": "", "evidence": ["q1", "q2"]}]}, "outside"),
    ({"instances": [{"round": 1, "t": 20.0, "caption": "", "evidence": []}]}, "cites no evidence"),
    ({"instances": [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q2", "q77"]}]}, "q77"),
    ({"instances": [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q2", "q3"]}]}, "error"),
])
def test_structural_problems_are_named(snap, log, change, expected):
    found = problems(snap, log, **change)
    assert any(expected in p for p in found), found


def test_a_number_not_in_the_cited_answers_is_refused(snap, log):
    found = problems(snap, log, body="You were 35 m from P5#T5.")
    assert any("35" in p for p in found)


def test_rounding_the_points_own_round_time_and_count_are_allowed(snap, log):
    assert problems(snap, log, body="About 121 m away in round 1 at 0:20, 1 time.") == []


def test_a_clock_time_far_from_any_cited_time_is_refused(snap, log):
    assert any("1:30" in p for p in problems(snap, log, body="At 1:30 you were alone."))


def test_a_number_from_a_cited_flag_is_allowed(snap, log):
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q5", "q2"]}]
    assert problems(snap, log, body="Nearest teammate 61 m away.", instances=inst) == []


def test_a_name_from_outside_the_match_is_refused(snap, log):
    assert any("Ghost#000" in p for p in problems(snap, log, body="Ghost#000 was 121.2 m away."))


def test_a_mistake_needs_what_the_team_knew_before_the_moment(snap, log):
    only_distance = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    assert any("knew" in p for p in problems(snap, log, instances=only_distance))
    knew_too_late = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q4"]}]
    assert any("knew" in p for p in problems(snap, log, instances=knew_too_late))


def test_a_good_point_does_not_need_team_knowledge(snap, log):
    only_distance = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    assert problems(snap, log, polarity="good", instances=only_distance) == []


def test_a_round_without_team_knowledge_must_say_so(snap, tmp_path):
    query.run(snap, tmp_path, "timeline", ["2"])
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    silent = [{"round": 2, "t": 5.0, "caption": "stayed put", "evidence": ["q1"]}]
    assert any(checker.NO_KNOWLEDGE_NOTE in p for p in
               checker.check_point(point(body="You held B.", instances=silent), log, snap, THEMES, 0))
    said = [{"round": 2, "t": 5.0, "caption": f"stayed put ({checker.NO_KNOWLEDGE_NOTE})", "evidence": ["q1"]}]
    assert checker.check_point(point(body="You held B.", instances=said), log, snap, THEMES, 0) == []


# The holes the external review found in the first draft, one test each.

def test_evidence_from_another_round_is_refused(snap, log):
    other = [{"round": 2, "t": 5.0, "caption": f"({checker.NO_KNOWLEDGE_NOTE})", "evidence": ["q1"]}]
    found = problems(snap, log, body="You held B.", instances=other)
    assert any("q1 is about round 1, not round 2" in p for p in found), found


def test_slots_and_coordinates_ground_no_number(snap, log):
    # q6 (where) holds slots 0-9, yaws and u/v values; none of them is a measurement. 7 is no time, distance or
    # count in q6 or q2 (their times are 10, 15, 5 and 8), so nothing grounds it.
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q6", "q2"]}]
    assert any("number 7" in p for p in problems(snap, log, body="You were 7 m from help.", instances=inst))


def test_a_versus_count_must_match_who_was_alive(snap, log):
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q6", "q2"]}]
    assert any("1v4" in p for p in problems(snap, log, body="You pushed in a 1v4.", instances=inst))
    assert problems(snap, log, body="It was still a 5v5.", instances=inst) == []


def test_knew_for_the_other_side_does_not_count(snap, log):
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q7"]}]
    assert any("knew" in p for p in problems(snap, log, instances=inst))


def test_knew_from_long_before_the_moment_does_not_count(snap, log):
    inst = [{"round": 1, "t": 55.0, "caption": "", "evidence": ["q1", "q2"]}]      # q2 is 40 s before 55
    assert any("knew" in p for p in problems(snap, log, instances=inst))


def test_names_with_spaces_and_names_without_tags_are_recognised(tmp_path):
    spaced = {**CONTEXT["players"]["5"], "name": "Big Dog 7#NA1"}
    context = {**CONTEXT, "players": {**CONTEXT["players"], "5": spaced}}
    blobs = {1: toys.round_blob(toys.ten(), n=1), 2: toys.round_blob(toys.ten(), n=2)}
    snapshot.save(snapshot.assemble(context, blobs, {1: ("ok", False, SHARES)}), tmp_path)
    snap = snapshot.load(tmp_path)
    query.run(snap, tmp_path, "distance", ["1", "10", "0", "5"])                    # q1: 121.2 m
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    for body in ("Big Dog 7#NA1 was 121.2 m away.", "Big Dog 7 was 121.2 m away.",
                 "P0 and big dog 7 were 121.2 m apart."):
        found = checker.check_point(point(polarity="good", body=body, instances=inst), log, snap, THEMES, 0)
        assert found == [], (body, found)             # and the 7 inside the name is not read as a number


def test_malformed_shapes_are_problems_not_crashes(snap, log):
    assert any("not an object" in p for p in problems(snap, log, instances=["oops"]))
    assert any("160" in p for p in problems(snap, log, title="x" * 161))
    whole = [{"round": 1.0, "t": 20.0, "caption": "", "evidence": ["q1", "q2"]}]
    assert any("whole number" in p for p in problems(snap, log, instances=whole))
    text = [{"round": 1, "t": 20.0, "caption": "", "evidence": "q1"}]
    assert any("evidence" in p for p in problems(snap, log, instances=text))


def test_an_entry_that_cannot_be_reproduced_is_not_trusted(snap, tmp_path):
    query.run(snap, tmp_path, "distance", ["1", "10", "P0", "P5"])                  # q1: real
    query.append(tmp_path, "distance", ["1", "10", "P0", "P5"],                     # q2: same call, edited answer
                 {"round": 1, "t": 10.0, "a": "P0#T0", "b": "P5#T5", "metres": 5.0})
    query.append(tmp_path, "flag", [1, 20.0, "death_untraded_alone"], FLAG)         # q3: a flag the runner logged
    query.append(tmp_path, "flag", [1, 20.0, "made_up"], {"round": 1, "t": 20.0})   # q4: a flag it did not
    trusted = checker.trusted_log(query.read_log(tmp_path), snap, {"q3": FLAG})
    assert sorted(trusted) == ["q1", "q3"]


# P4 review amendments R-F1, R-F2, R-F3, R-F8.

GOOD_ONE = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]


def test_a_number_glued_to_a_unit_is_still_checked(snap, log):          # R-F1
    found = problems(snap, log, polarity="good", body="You were 5m from help.", instances=GOOD_ONE)
    assert any("number 5" in p for p in found), found


def test_a_cited_value_written_with_a_unit_passes(snap, log):           # R-F1
    assert problems(snap, log, polarity="good", body="You were 121.2m from help.", instances=GOOD_ONE) == []


def test_a_vs_count_with_no_such_pair_is_refused(snap, log):            # R-F1
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q6"]}]
    found = problems(snap, log, polarity="good", body="You pushed a 1vs4.", instances=inst)
    assert any("1vs4" in p for p in found), found


def test_a_number_with_a_thousands_comma_passes_when_cited(snap, tmp_path):     # R-F1
    query.append(tmp_path, "flag", [1, 10.0, "spent"], {"round": 1, "t": 10.0, "econ": 12000})
    log = checker.trusted_log(query.read_log(tmp_path), snap, {"q1": {"round": 1, "t": 10.0, "econ": 12000}})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    assert checker.check_point(point(polarity="good", body="They held 12,000 credits.", instances=inst),
                               log, snap, THEMES, 0) == []


def test_a_negative_impact_is_grounded_by_its_size(snap, tmp_path):     # R-F2
    query.run(snap, tmp_path, "impact", ["1", "P0"])
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    assert checker.check_point(point(polarity="good", body="Impact was -250", instances=inst),
                               log, snap, THEMES, 0) == []


# Step 5b: two points that passed the first draft of the rules.

def test_a_distance_spelled_out_in_words_is_refused(snap, log):
    found = problems(snap, log, polarity="good", body="You were three metres from help.", instances=GOOD_ONE)
    assert any("write the figure" in p for p in found), found


def test_an_explicit_sign_must_match_the_cited_value(snap, tmp_path):
    query.run(snap, tmp_path, "impact", ["1", "P0"])
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    wrong = checker.check_point(point(polarity="good", body="You gained +250 Impact.", instances=inst),
                                log, snap, THEMES, 0)
    assert any("opposite sign" in p for p in wrong), wrong
    assert checker.check_point(point(polarity="good", body="You lost -250 Impact.", instances=inst),
                               log, snap, THEMES, 0) == []


def test_a_distance_asked_after_the_moment_is_refused(snap, tmp_path):  # R-F3
    query.run(snap, tmp_path, "distance", ["1", "50", "P1", "P5"])
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    found = checker.check_point(point(polarity="good", instances=inst), log, snap, THEMES, 0)
    assert any("outside this moment" in p for p in found), found


def test_a_distance_asked_shortly_before_the_moment_passes(snap, tmp_path):     # R-F3
    query.run(snap, tmp_path, "distance", ["1", "15", "P1", "P5"])
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    assert checker.check_point(point(polarity="good", instances=inst), log, snap, THEMES, 0) == []


def test_where_reports_health_as_a_word_and_it_grounds_nothing(snap):   # R-F8
    row = query.answer(snap, "where", ["1", "15"])["players"][0]
    assert row["health"] in ("healthy", "hurt", "low")
    assert "hp" not in row and "shield" not in row
    assert "health" not in checker._MEASURED and "hp" not in checker._MEASURED and "shield" not in checker._MEASURED

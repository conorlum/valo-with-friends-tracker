import pytest

from app.coach import areas, checker, query, snapshot
from tests.coach import toys
from tests.coach.test_areas import NAMES, painted_object, write_paint
from tests.coach.test_snapshot import CONTEXT

THEMES = ("trading", "rotation")
KNEW = {"A": {"5": [[5.0, 12.0, 8000, 8000]]}, "B": {}}     # side A has P5 in view from 5 s to 12 s
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
    return checker.trusted_log(query.read_log(tmp_path), snap, {"q5": flag_entry([1, 20.0, "death_untraded_alone"], FLAG)})


FLAG = {"round": 1, "t": 20.0, "nearest_teammate_m": 61.0}


def flag_entry(args, result):
    return {"cmd": "flag", "args": args, "result": result}


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
    # count in q6 or q2 (their times are 10, 15, 5 and 12), so nothing grounds it.
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
    trusted = checker.trusted_log(query.read_log(tmp_path), snap, {"q3": flag_entry([1, 20.0, "death_untraded_alone"], FLAG)})
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
    log = checker.trusted_log(query.read_log(tmp_path), snap, {"q1": flag_entry([1, 10.0, "spent"], {"round": 1, "t": 10.0, "econ": 12000})})
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


# W18 fix round: the Opus review's findings 1-9.

def _flag_log(snap, tmp_path, result, args=None):
    args = args or [1, 10.0, "x"]
    query.append(tmp_path, "flag", args, result)
    return checker.trusted_log(query.read_log(tmp_path), snap, {"q1": flag_entry(args, result)})


def _check(snap, log, body, inst, polarity="good"):
    return checker.check_point(point(polarity=polarity, body=body, instances=inst), log, snap, THEMES, 0)


def test_a_flag_whose_args_were_rewritten_is_not_trusted(snap, tmp_path):                   # fix 1
    query.append(tmp_path, "flag", [2, 5.0, "x"], FLAG)                                      # the model moved it to round 2
    raw = query.read_log(tmp_path)
    assert checker.trusted_log(raw, snap, {"q1": flag_entry([1, 20.0, "x"], FLAG)}) == {}
    assert list(checker.trusted_log(raw, snap, {"q1": flag_entry([2, 5.0, "x"], FLAG)})) == ["q1"]


def test_hostile_log_lines_do_not_crash(snap, tmp_path):                                    # fix 2
    path = tmp_path / query.LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        '{"id":"q1","cmd":["x"],"args":[],"result":{}}',
        '{"id":"q2","cmd":"where","args":[Infinity,"10"],"result":{}}',
        "not json at all",
        '{"cmd":"where","args":[],"result":{}}',
        '[1, 2]',
        '{"id":"q3","cmd":"distance","args":["1","10","P0","P5"],"result":{"metres":121.2}}',
    ]) + "\n", encoding="utf-8")
    raw = query.read_log(tmp_path)
    assert sorted(raw) == ["q1", "q2", "q3"]
    assert checker.trusted_log(raw, snap, {}) == {}
    assert checker._round_of({"args": [float("inf")]}) is None


def test_t1_must_stay_within_ten_seconds_of_t(snap, log):                                   # fix 3
    inst = [{"round": 1, "t": 20.0, "t1": 55.0, "caption": "", "evidence": ["q1"]}]
    found = problems(snap, log, polarity="good", instances=inst)
    assert any("t1 must be within 10 s after t" in p for p in found), found
    backwards = [{"round": 1, "t": 20.0, "t1": 19.0, "caption": "", "evidence": ["q1"]}]
    assert any("t1 must be within" in p for p in problems(snap, log, polarity="good", instances=backwards))


def test_t1_grounds_a_clock_time_but_never_a_plain_number(snap, log):                        # fix 3
    inst = [{"round": 1, "t": 20.0, "t1": 28.0, "caption": "", "evidence": ["q1"]}]
    assert problems(snap, log, polarity="good", body="Until 0:28 it held, 121.2 m.", instances=inst) == []
    found = problems(snap, log, polarity="good", body="It held 28 m.", instances=inst)
    assert any("number 28" in p for p in found), found


def test_a_sign_agrees_with_a_rounded_cited_value(snap, tmp_path):                           # fix 4
    log = _flag_log(snap, tmp_path, {"round": 1, "t": 10.0, "impact": -249.7})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    assert _check(snap, log, "Impact was -250.", inst) == []
    assert any("opposite sign" in p for p in _check(snap, log, "Impact was +250.", inst))


def test_a_dash_and_a_signed_percentage_are_not_refused(snap, tmp_path):                     # fix 5
    log = _flag_log(snap, tmp_path, {"round": 1, "t": 10.0, "metres": 121.2, "a": 0.5})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    assert _check(snap, log, "You stood alone -121.2 m from P5.", inst) == []
    assert _check(snap, log, "Gained +50% control.", inst) == []
    assert any("opposite sign" in p for p in _check(snap, log, "Gained -50% control.", inst))


@pytest.mark.parametrize("body", ["You took a 2 against 1.", "It was a one v four.", "A two versus three.",
                                  "A 1 vs. 4 fight."])
def test_more_versus_forms_are_checked(snap, log, body):                                     # fix 6
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q6", "q2"]}]
    found = problems(snap, log, body=body, instances=inst)
    assert any("matches no cited" in p for p in found), found


def test_a_spelled_out_versus_count_that_matches_passes(snap, log):                          # fix 6
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q6", "q2"]}]
    assert problems(snap, log, body="It was five v five, a 5 against 5.", instances=inst) == []


@pytest.mark.parametrize("body", ["You were thirteen metres away.", "Half a second later.", "About ninety seconds in."])
def test_every_spelled_out_measurement_is_refused(snap, log, body):                          # fix 7
    found = problems(snap, log, polarity="good", body=body, instances=GOOD_ONE)
    assert any("write the figure" in p for p in found), found


@pytest.mark.parametrize("body", ["He had two kills.", "A dozen rounds.", "It happened in one round.",
                                  "You did it three times against four enemies."])
def test_a_spelled_out_count_is_not_a_measurement_and_passes(snap, log, body):
    assert problems(snap, log, polarity="good", body=body, instances=GOOD_ONE) == []


def test_a_point_that_is_not_an_object_is_a_problem(snap, log):                              # fix 8
    assert checker.check_point("oops", log, snap, THEMES, 0) == ["the point is not an object"]


def test_a_very_long_body_is_refused_before_the_regexes(snap, log):                          # fix 9
    found = problems(snap, log, polarity="good", body="x" * 5000, instances=GOOD_ONE)
    assert any("4000" in p for p in found), found


# PR 132 review: the cases its reviewer ran, one test per finding.

@pytest.mark.parametrize("body, inst", [
    ("You were 20 m from P5#T5.", [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q2"]}]),
    ("You took 1 damage and dealt 1 kill.", [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q2"]}]),
    ("You were 10 m from P5#T5.", [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q6", "q2"]}]),
    ("It took 121.2 s.", [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q2"]}]),
    ("Same as round 2.", [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1", "q2"]}]),
])
def test_the_points_own_round_time_and_count_ground_no_other_figure(snap, log, body, inst):      # finding 1
    assert problems(snap, log, body=body, instances=inst) != []


def test_the_points_own_figures_pass_in_their_own_form(snap, log):                               # findings 1, 4
    inst = [{"round": 1, "t": 20.0, "t1": 25.0, "caption": "", "evidence": ["q1", "q2"]}]
    for body in ("In round 1, at 20 s, 121.2 m away.", "From 20 to 25 s you stood still.", "It happened 1 time."):
        assert problems(snap, log, body=body, instances=inst) == [], body


@pytest.fixture
def toy_areas(monkeypatch):
    """The toy map's named areas, as callouts.json would hold them for a real map."""
    monkeypatch.setattr(areas, "callouts", lambda map_name, table=None: toys.CALLOUTS["Toy"])


def test_a_named_area_must_be_in_a_cited_answer(toy_areas, snap, tmp_path):                      # finding 2
    query.run(snap, tmp_path, "distance", ["1", "10", "P0", "P5"])      # q1: no place in it
    query.run(snap, tmp_path, "where", ["1", "10"])                     # q2: A Site and B Site
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    assert log["q2"]["result"]["players"][0]["area"]["approximate"] is True
    found = _check(snap, log, "You held Mid Courtyard.", GOOD_ONE)
    assert any("the area Mid Courtyard" in p for p in found), found
    where = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q2"]}]
    assert _check(snap, log, "You held A Site, they held B Site.", where) == []
    assert any("Mid Courtyard" in p for p in _check(snap, log, "You held Mid Courtyard.", where))


@pytest.fixture
def painted_snap(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    match = {**CONTEXT["match"], "map": "Sunset", "rounds": [5, 9]}
    context = {**CONTEXT, "match": match, "rounds": {str(n): {"db": {}, "stats": {}} for n in match["rounds"]}}
    blobs = {n: toys.round_blob(toys.ten(), n=n) for n in match["rounds"]}
    controls = {n: ("ok", False, SHARES) for n in match["rounds"]}
    write_paint(tmp_path, painted_object({areas.cell_at(8000, 8000): 2}))
    return snapshot.Snapshot(snapshot.assemble(context, blobs, controls))


@pytest.mark.parametrize("asked, end, area, accepted", [
    (19.5, None, "Top Mid", True),
    (19.5, None, "Mid", True),
    (19.0, None, "Top Mid", True),
    (18.9, None, "Top Mid", False),
    (14.0, None, "Top Mid", False),
    (20.1, None, "Top Mid", False),
    (23.0, 24.0, "Top Mid", True),
])
def test_a_where_path_grounds_its_names_in_the_moments_time_window(painted_snap, tmp_path, asked, end, area, accepted):
    query.run(painted_snap, tmp_path, "where", ["5", str(asked)])
    log = checker.trusted_log(query.read_log(tmp_path), painted_snap, {})
    inst = [{"round": 5, "t": 20.0, "t1": end, "caption": "", "evidence": ["q1"]}]
    found = _check(painted_snap, log, f"You held {area}.", inst)
    if accepted:
        assert found == []
    else:
        assert f"the area {area} is not in a cited answer" in found


def test_a_where_area_matches_the_time_of_a_moment_in_its_own_round(painted_snap, tmp_path):
    query.run(painted_snap, tmp_path, "timeline", ["5"])
    query.run(painted_snap, tmp_path, "where", ["9", "19.5"])
    log = checker.trusted_log(query.read_log(tmp_path), painted_snap, {})
    first = {"round": 5, "t": 20.0, "caption": "", "evidence": ["q1"]}
    second = {"round": 9, "t": 24.0, "caption": "", "evidence": ["q2"]}
    assert "the area Top Mid is not in a cited answer" in _check(
        painted_snap, log, "You held Top Mid.", [first, second],
    )
    assert _check(painted_snap, log, "You held Top Mid.", [first, {**second, "t": 20.0}]) == []


def test_a_knew_path_grounds_the_enemy_area_at_its_own_sighting_time(painted_snap, tmp_path):
    query.run(painted_snap, tmp_path, "knew", ["5", "15", "A"])
    log = checker.trusted_log(query.read_log(tmp_path), painted_snap, {})
    inst = [{"round": 5, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    assert _check(painted_snap, log, "The enemy was last seen in Top Mid.", inst) == []
    assert _check(painted_snap, log, "The enemy was last seen in Mid.", inst) == []


def test_a_longer_area_match_does_not_imply_a_separate_shorter_name(painted_snap, tmp_path):
    names = [NAMES[0], {**NAMES[1], "parent": 3, "super": "A"}, NAMES[2]]
    write_paint(tmp_path, painted_object({areas.cell_at(8000, 8000): 2}, names=names))
    query.run(painted_snap, tmp_path, "where", ["5", "19.5"])
    log = checker.trusted_log(query.read_log(tmp_path), painted_snap, {})
    inst = [{"round": 5, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    assert _check(painted_snap, log, "You held Top Mid.", inst) == []
    assert _check(painted_snap, log, "You held Top Mid, then moved to Mid.", inst) == [
        "the area Mid is not in a cited answer",
    ]


def test_a_legacy_where_area_without_a_path_grounds_its_name(toy_areas, snap):
    log = {"q1": {"cmd": "where", "args": [1, 19.5],
                  "result": {"t": 19.5, "players": [{"area": {"name": "A Site", "super": "A"}}]}}}
    inst = [{"round": 1, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    assert _check(snap, log, "You held A Site.", inst) == []


def test_where_grounds_only_the_moment_that_cites_it(painted_snap, tmp_path):
    query.run(painted_snap, tmp_path, "where", ["5", "19.5"])
    query.run(painted_snap, tmp_path, "timeline", ["5"])
    log = checker.trusted_log(query.read_log(tmp_path), painted_snap, {})
    first = {"round": 5, "t": 20.0, "caption": "", "evidence": ["q2"]}
    second = {"round": 5, "t": 29.0, "caption": "You held Top Mid.", "evidence": ["q1"]}
    assert "the area Top Mid is not in a cited answer" in _check(painted_snap, log, "Hold the angle.", [first, second])
    timely = {**first, "caption": "You held Top Mid.", "evidence": ["q1"]}
    assert _check(painted_snap, log, "You held Top Mid.", [timely, {**second, "caption": "", "evidence": ["q2"]}]) == []
    # Even a timely citation in another moment cannot support this caption.
    assert "the area Top Mid is not in a cited answer" in _check(
        painted_snap, log, "Hold the angle.", [timely, {**second, "evidence": ["q2"]}],
    )


@pytest.mark.parametrize("text, claim", [
    ("A better trade", False), ("B is open", False),
    ("A site", True), ("site A", True), ("A-site", True), ("A Site", True), ("SITE A", True),
])
def test_single_letter_areas_require_a_site_word(painted_snap, tmp_path, text, claim):
    names = [{**NAMES[0], "name": "A"}, {**NAMES[2], "name": "B"}]
    write_paint(tmp_path, painted_object({}, names=names))
    query.run(painted_snap, tmp_path, "timeline", ["5"])
    log = checker.trusted_log(query.read_log(tmp_path), painted_snap, {})
    inst = [{"round": 5, "t": 20.0, "caption": "", "evidence": ["q1"]}]
    found = _check(painted_snap, log, text, inst)
    assert bool(found) is claim


def test_a_mistake_cannot_rest_on_an_enemy_the_side_could_not_see(toy_areas, tmp_path):          # finding 3
    # P5 stands on B Site and is seen from 5 s to 12 s; P6-P9 stand in Mid Courtyard and are never seen.
    places = {**toys.ten(), **{s: (5000, 5000) for s in range(6, 10)}}
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {"db": {}, "stats": {}}}}
    snapshot.save(snapshot.assemble(context, {1: toys.round_blob(places, n=1)}, {1: ("ok", False, SHARES)}), tmp_path)
    snap = snapshot.load(tmp_path)
    query.run(snap, tmp_path, "knew", ["1", "15", "A"])                 # q1: P5 last seen at 12, on B Site
    query.run(snap, tmp_path, "distance", ["1", "15", "P0", "P5"])      # q2: 3 s after they lost him
    query.run(snap, tmp_path, "where", ["1", "15"])                     # q3: everyone's place
    query.run(snap, tmp_path, "distance", ["1", "15", "P0", "P1"])      # q4: to a teammate
    query.run(snap, tmp_path, "distance", ["1", "10", "P0", "P5"])      # q5: while P5 was in view
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    hidden = [{"round": 1, "t": 15.0, "caption": "", "evidence": ["q1", "q2"]}]
    found = _check(snap, log, "P5#T5 was 121.2 m away; rotate.", hidden, polarity="improve")
    assert any("could not see" in p for p in found), found
    assert _check(snap, log, "P5#T5 was 121.2 m away.", hidden) == []             # praise is not held to it
    seen = [{"round": 1, "t": 15.0, "caption": "", "evidence": ["q1", "q5"]}]
    assert _check(snap, log, "P5#T5 was 121.2 m away; rotate.", seen, polarity="improve") == []
    place = [{"round": 1, "t": 15.0, "caption": "", "evidence": ["q1", "q3"]}]
    found = _check(snap, log, "Four of them sat in Mid Courtyard.", place, polarity="improve")
    assert any("the area Mid Courtyard" in p for p in found), found
    assert _check(snap, log, "Four of them sat in Mid Courtyard.", place) == []   # a good point may say it
    # the last sighting from knew, and the side's own places, still ground a mistake
    assert _check(snap, log, "P5#T5 was last seen on B Site; you stood on A Site.", place, polarity="improve") == []
    mate = [{"round": 1, "t": 15.0, "caption": "", "evidence": ["q1", "q4"]}]
    assert _check(snap, log, "P1#T1 was 0 m from you.", mate, polarity="improve") == []


@pytest.mark.parametrize("body", ["You gave them a 1v1 fight.", "It was a 50/50.", "That is the #1 job."])
def test_figures_of_speech_are_not_counts(snap, log, body):                                      # finding 4
    assert problems(snap, log, polarity="good", body=body, instances=GOOD_ONE) == []


def test_a_player_named_with_digits_does_not_hide_a_number(tmp_path):                            # finding 10
    digits = {**CONTEXT["players"]["5"], "name": "35#NA1"}
    context = {**CONTEXT, "players": {**CONTEXT["players"], "5": digits}}
    blobs = {1: toys.round_blob(toys.ten(), n=1), 2: toys.round_blob(toys.ten(), n=2)}
    snapshot.save(snapshot.assemble(context, blobs, {1: ("ok", False, SHARES)}), tmp_path)
    snap = snapshot.load(tmp_path)
    query.run(snap, tmp_path, "distance", ["1", "10", "0", "5"])
    log = checker.trusted_log(query.read_log(tmp_path), snap, {})
    inst = [{"round": 1, "t": 10.0, "caption": "", "evidence": ["q1"]}]
    found = checker.check_point(point(polarity="good", body="You were 35 metres from 35#NA1.", instances=inst),
                                log, snap, THEMES, 0)
    assert any("number 35" in p for p in found), found


@pytest.fixture
def spike_log(toy_areas, tmp_path):
    """A round whose spike went down at 10 s on B Site, with one 1.9 s tap at 40 s (15 s left on the spike)."""
    bomb = {"k": "ability", "kind": "Bomb", "name": "Spike", "by": 1, "t": 10.0, "t1": None,
            "defuses": [[40.0, 41.9, 2, False]], "owner_by": "planted", "u": 8000, "v": 8000}
    blob = toys.round_blob(toys.ten(), util=[bomb], t_end=60.0, n=1)
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]},
               "rounds": {"1": {"db": {}, "stats": {}, "kills": {}}}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {1: ("ok", False, SHARES)}), tmp_path)
    snap = snapshot.load(tmp_path)
    query.run(snap, tmp_path, "timeline", ["1"])                       # q1
    return snap, checker.trusted_log(query.read_log(tmp_path), snap, {})


SPIKE_INST = [{"round": 1, "t": 40.0, "caption": "", "evidence": ["q1"]}]


@pytest.mark.parametrize("body", ["You made a 1.9 s tap.", "You tapped with 15 s on the spike."])
def test_a_cited_attempt_length_and_time_left_pass(spike_log, body):
    snap, log = spike_log
    assert _check(snap, log, body, SPIKE_INST) == []


def test_a_timeline_spike_area_alone_does_not_ground_a_named_area(spike_log):
    snap, log = spike_log
    assert _check(snap, log, "The spike was planted on B Site.", SPIKE_INST) == [
        "the area B Site is not in a cited answer",
    ]


def test_an_attempt_length_not_in_the_answer_fails(spike_log):
    snap, log = spike_log
    assert _check(snap, log, "You made a 2.5 s tap.", SPIKE_INST) != []


@pytest.fixture
def ability_snap(tmp_path):
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [6, 7]},
               "rounds": {str(n): {"db": {}, "stats": {}, "kills": {}} for n in (6, 7)}}
    blobs = {n: toys.round_blob(toys.ten(), n=n) for n in (6, 7)}
    snapshot.save(snapshot.assemble(context, blobs, {n: ("ok", False, SHARES) for n in blobs}), tmp_path)
    return snapshot.load(tmp_path)


@pytest.fixture
def ability_check(ability_snap):

    def check(body, reason=None, event_class="found", *, cmd="knew", cite=True,
              answer_round=6, rounds=(6,), section="body", signals_missing=False, cited_instances=None,
              side="A", signal_t=10.0, answer_t=15.0, instance_t=20.0, times=None, t1=None):
        signals = [] if reason is None else [
            {"t": signal_t, "name": "P5", "reason": reason, "class": event_class}]
        result = {"round": answer_round, "t": answer_t, "side": side, "enemies": [],
                  "signals": None if signals_missing else signals,
                  "ruled_out": [{"t": 10.0, "name": f"P{s}", "reason": "haunt"} for s in range(5, 10)],
                  "lost_track": []}
        log = {"q1": {"cmd": cmd, "args": [str(answer_round), str(answer_t), side], "result": result}}
        instances = [{"round": n, "t": times[i] if times else instance_t, "t1": t1,
                      "caption": body if section == "caption" else "",
                      "evidence": ["q1"] if cite and (cited_instances is None or i in cited_instances) else []}
                     for i, n in enumerate(rounds)]
        p = point(polarity="good", title="Ability information", body="You pushed afterwards.", instances=instances)
        if section != "caption":
            p[section] = body
        return checker.check_point(p, log, ability_snap, THEMES, 0)

    return check


@pytest.mark.parametrize("ability,reason", [
    ("recon dart", "recon"), ("recon bolt", "recon"), ("camera dart", "camera_dart"),
    ("drone dart", "drone_dart"), ("haunt", "haunt"), ("recon", "recon"),
    ("knife", "knife_hit"), ("flash", "skye_hit"), ("drone", "tejo_drone"), ("dart", "camera_dart"),
])
@pytest.mark.parametrize("supported", [False, True])
def test_ability_claim_requires_its_signal(ability_check, ability, reason, supported):
    errors = ability_check(f"{ability.title()} revealed an enemy.", reason if supported else None)
    assert errors == [] if supported else errors == [
        f"instance 1: says {ability} located enemies in round 6, but no cited knew signal shows it"]


@pytest.mark.parametrize("reason", [None, "haunt"])
def test_haunt_exclusions_do_not_support_a_team_location_claim(ability_check, reason):
    errors = ability_check("Haunt located the whole enemy team", reason)
    assert errors == [
        "instance 1: says haunt located enemies in round 6, but no cited knew signal shows it"]


def test_ability_claim_requires_a_cited_knew_answer(ability_check):
    for kwargs in ({"cite": False}, {"cmd": "where"}, {"answer_round": 7}):
        assert any("no cited knew signal" in e for e in ability_check("Haunt located an enemy", "haunt", **kwargs))


@pytest.mark.parametrize("body", [
    "You pushed after the Haunt.", "Haunt found nobody", "Recon did not reveal an enemy",
    "The knife hit no one", "Your Haunt never located anyone", "Haunt revealed nothing",
    "Nobody was revealed by the Haunt", "Haunt didn't reveal an enemy", "Haunt didn’t reveal an enemy",
    "Haunt revealed no enemy", "Haunt found none", "You pushed without Haunt revealing anyone",
    "Haunt expired. Recon revealed an enemy.",
])
def test_ability_nonclaims_and_negation_need_no_signal(ability_check, body):
    reason = "recon" if body.startswith("Haunt expired.") else None
    assert ability_check(body, reason) == []


@pytest.mark.parametrize("ability,reason", [("Haunt", "haunt"), ("Recon dart", "recon")])
@pytest.mark.parametrize("supported", [False, True])
def test_passive_ability_claim(ability_check, ability, reason, supported):
    errors = ability_check(f"An enemy was revealed by the {ability}", reason if supported else None)
    assert bool(errors) is not supported
    assert all("no cited knew signal" in e for e in errors)


@pytest.mark.parametrize("reason,event_class", [
    ("knife_hit", "roughly_placed"), ("knife_all", "roughly_placed"), ("skye_cue", "roughly_placed"),
    ("drone_dart", "found"),
])
def test_ability_reason_prefixes_and_alternatives(ability_check, reason, event_class):
    ability = "knife" if reason.startswith("knife_") else "flash" if reason.startswith("skye_") else "drone"
    assert ability_check(f"{ability} spotted an enemy", reason, event_class) == []


@pytest.mark.parametrize("reason,event_class,extra", [
    ("haunt", "ruled_out", {}), ("haunt", "lost_track", {}), ("haunt", "other", {}),
    ("recon", "found", {}), ("haunt", "found", {"signals_missing": True}),
])
def test_only_matching_location_signals_support_ability_claims(ability_check, reason, event_class, extra):
    assert any("no cited knew signal" in e for e in ability_check("Haunt found an enemy", reason, event_class, **extra))


@pytest.mark.parametrize("section", ["title", "body", "caption"])
def test_ability_claim_in_any_point_text_needs_support_in_every_round(ability_check, section):
    errors = ability_check("Haunt scanned an enemy", "haunt", rounds=(6, 7), section=section)
    assert "instance 2: says haunt located enemies in round 7, but no cited knew signal shows it" in errors


def test_negation_in_another_clause_does_not_hide_a_positive_claim(ability_check):
    assert any("no cited knew signal" in e for e in ability_check(
        "Haunt found nobody, but Recon revealed an enemy."))


def test_ability_support_is_per_round_across_the_points_citations(ability_check):
    errors = ability_check("Haunt found an enemy", "haunt", rounds=(6, 6), cited_instances={1})
    assert not any("no cited knew signal" in e for e in errors)


@pytest.mark.parametrize("ability,reason", [("Recon dart", "drone_dart"), ("Camera dart", "drone_dart"),
                                          ("Drone dart", "tejo_drone")])
def test_compound_ability_names_do_not_fall_back_to_tokens(ability_check, ability, reason):
    assert any("no cited knew signal" in e for e in ability_check(f"{ability} revealed an enemy", reason))


def test_enemy_side_knowledge_does_not_back_an_ability_claim(ability_check):
    errors = ability_check("Haunt revealed an enemy.", "haunt", side="B")
    assert any("no cited knew signal" in e for e in errors)


@pytest.mark.parametrize("signal_t,supported", [(10.0, False), (19.9, False), (20.0, True),
                                               (50.0, True), (50.1, False), (None, False)])
def test_ability_signal_must_be_inside_the_instances_moment(ability_check, signal_t, supported):
    errors = ability_check("Haunt revealed an enemy.", "haunt", signal_t=signal_t,
                           answer_t=50.0, instance_t=50.0, t1=55.0)
    assert bool(errors) is not supported
    assert all("no cited knew signal" in e for e in errors)


def test_same_round_instances_do_not_share_an_old_ability_signal(ability_check):
    errors = ability_check("Haunt revealed an enemy.", "haunt", rounds=(6, 6), times=(20.0, 50.0), answer_t=20.0)
    assert errors == ["instance 2: says haunt located enemies in round 6, but no cited knew signal shows it"]


def test_caption_claim_only_needs_support_for_its_own_instance(ability_snap):
    log = {f"q{i}": {"cmd": "knew", "args": [str(n), "15.0", "A"],
                     "result": {"round": n, "t": 15.0, "side": "A", "enemies": [],
                                "signals": [{"t": 10.0, "name": "P5", "reason": "haunt", "class": "found"}]
                                if n == 6 else [], "ruled_out": [], "lost_track": []}}
           for i, n in enumerate((6, 7), 1)}
    instances = [{"round": 6, "t": 20.0, "caption": "Haunt revealed an enemy.", "evidence": ["q1"]},
                 {"round": 7, "t": 20.0, "caption": "You pushed with the team.", "evidence": ["q2"]}]
    p = point(polarity="good", title="Ability information", body="You pushed afterwards.", instances=instances)
    assert checker.check_point(p, log, ability_snap, THEMES, 0) == []


@pytest.mark.parametrize("text,claims", [
    ("Haunt revealed an enemy and you never swung", {"haunt"}),
    ("You used Haunt and then found cover", set()),
    ("Haunt revealed an enemy, you never swung", {"haunt"}),
    ("Haunt revealed an enemy then you never swung", {"haunt"}),
    ("You never swung after Haunt revealed an enemy", {"haunt"}),
    ("Haunt did not actually reveal an enemy", set()),
    ("Haunt found cover", set()),
    ("Haunt revealed the path", set()),
    ("Haunt revealed the Jett", {"haunt"}),
    ("Haunt revealed the doorway", set()),
    ("Someone was revealed by the Haunt", {"haunt"}),
    ("Nobody was ever revealed by the Haunt", set()),
    ("Haunt spotted them", {"haunt"}),
    ("Haunt scanned a player", {"haunt"}),
    ("Haunt found an opponent", {"haunt"}),
])
def test_ability_claim_binds_the_verb_object_and_local_negation(text, claims):
    assert set(checker._ability_claims(text)) == claims


def _check_ability_targets(snap, caption, targets, *, ruled_out=(), signal_t=10.0,
                           answer_t=15.0, t=20.0, t1=None, body="You pushed afterwards."):
    players = {s: dict(p) for s, p in snap.context["players"].items()}
    players["5"].update(name="the enemy Jett", agent="Jett")
    players["6"].update(name="the enemy Sova", agent="Sova")
    snap = snapshot.Snapshot({**snap.data, "context": {**snap.context, "players": players}})
    signals = [{"t": signal_t, "name": name, "reason": "haunt", "class": "found"} for name in targets]
    log = {"q1": {"cmd": "knew", "args": ["6", str(answer_t), "A"],
                  "result": {"round": 6, "t": answer_t, "side": "A", "enemies": [],
                             "signals": signals, "ruled_out": list(ruled_out), "lost_track": []}}}
    inst = {"round": 6, "t": t, "caption": caption, "evidence": ["q1"]}
    if t1 is not None:
        inst["t1"] = t1
    p = point(polarity="good", title="Ability information", body=body, instances=[inst])
    return checker.check_point(p, log, snap, THEMES, 0)


@pytest.mark.parametrize("object_text,count", [
    ("two enemies", 2), ("2 players", 2), ("both", 2), ("both enemies", 2),
    ("three of them", 3), ("three of the enemies", 3), ("enemies", 2),
    ("the whole enemy team", 5), ("all enemies", 5),
])
@pytest.mark.parametrize("found", [0, 1, 2, 3, 5])
def test_ability_count_requires_distinct_found_targets(ability_snap, object_text, count, found):
    # Slot names are deliberately synthetic; the first two rows also match anonymized knew answers.
    names = ["the enemy Jett", "the enemy Sova", "P7", "P8", "P9"][:found]
    errors = _check_ability_targets(ability_snap, f"Haunt revealed {object_text}.", names, signal_t=2.0)
    assert bool(errors) is (found < count)
    assert any("no cited knew signal" in e for e in errors) is (found < count)


def test_ability_count_does_not_count_repeated_signals_for_one_enemy(ability_snap):
    errors = _check_ability_targets(ability_snap, "Haunt revealed two enemies.",
                                  ["the enemy Jett", "the enemy Jett"])
    assert any("no cited knew signal" in e for e in errors)


@pytest.mark.parametrize("found,supported", [("the enemy Jett", False), ("the enemy Sova", True), ("6", True)])
def test_named_ability_target_must_be_found_not_only_ruled_out(ability_snap, found, supported):
    errors = _check_ability_targets(ability_snap, "Haunt revealed the Sova.", [found],
                                  ruled_out=[{"t": 10.0, "name": "the enemy Sova", "reason": "haunt"}])
    assert bool(errors) is not supported
    assert all("no cited knew signal" in e for e in errors)


@pytest.mark.parametrize("signal_t,answer_t,supported", [
    (19.9, 54.0, False), (20.0, 54.0, True), (52.0, 54.0, True),
    (54.0, 54.0, True), (54.1, 54.0, False), (55.0, 55.0, True), (55.1, 55.0, False),
])
def test_ability_signal_uses_instance_span_and_answer_time(ability_snap, signal_t, answer_t, supported):
    errors = _check_ability_targets(ability_snap, "Haunt revealed an enemy.", ["the enemy Jett"],
                                  signal_t=signal_t, answer_t=answer_t, t=50.0, t1=55.0)
    assert bool(errors) is not supported
    assert all("no cited knew signal" in e for e in errors)


@pytest.mark.parametrize("body", ["Haunt revealed two enemies.", "Haunt revealed the Sova.",
                                 "Haunt revealed the Sova. Haunt revealed the Jett."])
def test_caption_does_not_weaken_body_ability_count_or_target(ability_snap, body):
    errors = _check_ability_targets(ability_snap, "Haunt revealed an enemy.", ["the enemy Jett"],
                                  body=body)
    assert any("no cited knew signal" in e for e in errors)
@pytest.mark.parametrize("area", ["A", "A Site", "B", "B Site", "C", "C Site"])
@pytest.mark.parametrize("spelling", ["{s} site", "{s_lower} site", "site {s}", "{s}-site"])
def test_site_spellings_count_as_area_mentions(area, spelling):
    text = spelling.format(s=area[0], s_lower=area[0].lower())
    assert checker._area_problems(text, set(), [area], False)
    assert checker._area_problems(text, {area}, [area], False) == []
    assert checker._area_problems("A better trade", set(), [area], False) == []

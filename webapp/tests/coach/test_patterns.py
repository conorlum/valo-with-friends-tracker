import json
import shlex

import pytest

from app.coach import areas, checker, playbook, prompt, query, runner, snapfile, snapshot
from scripts import coach_query, coach_report
from tests.coach import toys
from tests.coach.test_areas import painted_object, write_paint
from tests.coach.test_checker import SHARES
from tests.coach.test_snapshot import CONTEXT


@pytest.fixture
def pattern_snap(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    write_paint(tmp_path, painted_object({areas.cell_at(8000, 8000): 2}))
    match = {**CONTEXT["match"], "map": "Sunset", "rounds": [5, 9]}
    context = {**CONTEXT, "match": match,
               "rounds": {str(n): {"db": {}, "stats": {}} for n in match["rounds"]}}
    positions = {**toys.ten(), 0: (8000, 8000)}
    blobs = {
        5: toys.round_blob(positions, n=5, t_decided=16.0, kills=[
            {"t": 0.25, "killer": None, "victim": 1},
            {"t": 10.03125, "killer": 5, "victim": 0},
            {"t": 15.0, "killer": 2, "victim": 6},
            {"t": 20.0, "killer": 0, "victim": 7},
        ]),
        9: toys.round_blob(positions, n=9, kills=[
            {"t": 7.0, "killer": 0, "victim": 5},
            {"t": 8.0625, "killer": 6, "victim": 0},
        ]),
    }
    for blob in blobs.values():
        blob["player_state"]["vitals"] = {
            str(slot): [{"t": 0.0, "life": 0, "hp": 20 + 8 * slot,
                         "sh": 0, "mhp": 100, "msh": 50}]
            for slot in range(10)
        }
    controls = {n: ("ok", False, SHARES) for n in match["rounds"]}
    snapfile.save(snapshot.assemble(context, blobs, controls), tmp_path)
    return snapfile.load(tmp_path)


def ask(snap, directory, command, args=()):
    from app.coach import patterns

    return patterns.run(snap, directory, command, list(args))


@pytest.mark.parametrize("command, player, expected", [
    ("deaths", "0", [(5, 10.03125), (9, 8.0625)]),
    ("kills", "0", [(9, 7.0)]),
    ("teammate_deaths", "0", [(5, 0.25)]),
    ("deaths", "1", [(5, 0.25)]),
    ("kills", "2", [(5, 15.0)]),
    ("teammate_deaths", "5", [(5, 15.0)]),
    ("kills", "4", []),
])
def test_patterns_select_live_kills_and_log_only_ordinary_answers(pattern_snap, tmp_path, command, player, expected):
    result = ask(pattern_snap, tmp_path, command, [player])
    assert [(row["round"], row["t"]) for row in result["moments"]] == expected
    log = query.read_log(tmp_path)
    assert len(log) == 4 * len(expected)
    assert checker.trusted_log(log, pattern_snap, {}) == log
    for row, (n, t) in zip(result["moments"], expected):
        assert set(row) == {"round", "t", "killer", "victim", "players", "flags",
                            "timeline_id", "where_id", "knew_a_id", "knew_b_id"}
        entries = [log[row[key]] for key in ("timeline_id", "where_id", "knew_a_id", "knew_b_id")]
        assert [(e["cmd"], e["args"]) for e in entries] == [
            ("timeline", [n, t, t]), ("where", [n, max(0.0, t - 0.5)]),
            ("knew", [n, t, "A"]), ("knew", [n, t, "B"]),
        ]
        assert all(e["cmd"] in query.COMMANDS for e in entries)
        assert all("enemies" in e["result"] for e in entries[2:])
        assert entries[0]["result"]["events"][0]["kind"] == "kill"


def leaves(value):
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in leaves(child)]
    if isinstance(value, list):
        return [leaf for child in value for leaf in leaves(child)]
    return [json.dumps(value, sort_keys=True)]


@pytest.mark.parametrize("command, slot", [
    ("deaths", 0), ("kills", 0), ("teammate_deaths", 0), ("deaths", 5),
])
def test_row_values_are_copied_from_the_cited_entries(pattern_snap, tmp_path, command, slot):
    _, flags = runner._brief_with_flag_ids(pattern_snap, slot, tmp_path)
    rows = ask(pattern_snap, tmp_path, command)["moments"]
    log = query.read_log(tmp_path)
    assert checker.trusted_log(log, pattern_snap, flags) == log
    for row in rows:
        timeline = log[row["timeline_id"]]["result"]
        event = next(e for e in timeline["events"] if e["kind"] == "kill")
        assert (row["killer"], row["victim"], row["t"]) == (
            event["killer"], event["victim"], event["t"])
        where = log[row["where_id"]]["result"]
        own = next(p for p in where["players"] if p["slot"] == slot)
        allowed = [p for p in where["players"] if p["slot"] == slot or (
            p["name"] in (event["killer"], event["victim"]) and p["side"] == own["side"])]
        assert [p["name"] for p in row["players"]] == [p["name"] for p in allowed]
        for shown, source in zip(row["players"], allowed):
            expected = {"name": source["name"]}
            if source.get("area") is not None:
                expected["area"] = source["area"]["name"]
            if "health" in source:
                expected["health"] = source["health"]
            assert shown == expected
        expected_flags = [
            {"id": key, **entry["result"]} for key, entry in flags.items()
            if entry["args"][0] == row["round"] and abs(entry["args"][1] - row["t"]) < 0.006
        ]
        assert row["flags"] == expected_flags
        ids = [row[k] for k in ("timeline_id", "where_id", "knew_a_id", "knew_b_id")]
        ids += [f["id"] for f in row["flags"]]
        sources = set(leaves([log[key] for key in ids]))
        assert set(leaves(row)) <= sources


def test_flags_use_logged_values_and_rounded_time_only_for_subject(pattern_snap, tmp_path, monkeypatch):
    flag = {"round": 5, "t": 10.03, "kind": "death_untraded_alone",
            "values": {"killer": "P5#T5", "nearest_teammate_m": 17.3,
                       "teammates_alive": 3, "detail": {"known": False, "delay_s": 0.0}}}
    monkeypatch.setattr(runner.brief_module, "build", lambda *_: {"rounds": [{"round": 5, "flags": [flag]}]})
    _, flags = runner._brief_with_flag_ids(pattern_snap, 0, tmp_path)
    # These entries have the right shape but the wrong time, round, or command.
    for cmd, args in [("flag", [5, 10.04, flag["kind"]]),
                      ("flag", [9, 10.03, flag["kind"]]),
                      ("timeline", [5, 10.03, 10.03])]:
        query.append(tmp_path, cmd, args, {"round": 5, "t": 10.03, "kind": "excluded"})
    row = ask(pattern_snap, tmp_path, "deaths")["moments"][0]
    flag_id = next(iter(flags))
    assert row["flags"] == [{"id": flag_id, **flags[flag_id]["result"]}]
    assert row["t"] == 10.03125
    assert ask(pattern_snap, tmp_path, "deaths", ["5"])["moments"][0]["flags"] == []
    (tmp_path / "subject.json").unlink()
    assert ask(pattern_snap, tmp_path, "deaths", ["0"])["moments"][0]["flags"] == []


def test_same_time_kills_keep_their_parties_and_only_friendly_places(pattern_snap, tmp_path):
    view = pattern_snap.view(9)
    view.kills.extend([{"t": 7.0, "killer": 0, "victim": 2},
                       {"t": 7.0, "killer": 3, "victim": 4}])
    rows = ask(pattern_snap, tmp_path, "kills", ["0"])["moments"]
    assert [r["victim"] for r in rows] == ["P5#T5", "P2#T2"]
    assert [[p["name"] for p in r["players"]] for r in rows] == [["P0#T0"], ["P0#T0", "P2#T2"]]
    row = ask(pattern_snap, tmp_path, "teammate_deaths", ["0"])["moments"][-1]
    assert (row["killer"], row["victim"]) == ("P3#T3", "P4#T4")
    assert [p["name"] for p in row["players"]] == ["P0#T0", "P3#T3", "P4#T4"]


def test_missing_places_and_health_stay_missing(pattern_snap, tmp_path, monkeypatch):
    original = query.COMMANDS["where"]

    def without_details(*args):
        result = original(*args)
        for player in result["players"]:
            player.pop("area", None)
            player.pop("health", None)
        return result

    monkeypatch.setitem(query.COMMANDS, "where", without_details)
    rows = ask(pattern_snap, tmp_path, "deaths", ["0"])["moments"]
    assert all(row["players"] == [{"name": "P0#T0"}] for row in rows)


def test_moment_evidence_satisfies_checker_windows_without_rounding(pattern_snap, tmp_path):
    ask(pattern_snap, tmp_path, "moment", ["5", "10.03125"])
    log = query.read_log(tmp_path)
    assert checker.trusted_log(log, pattern_snap, {}) == log
    where = next(e for e in log.values() if e["cmd"] == "where")
    area = next(p for p in where["result"]["players"] if p["slot"] == 0)["area"]
    assert area["name"] == "Top Mid" and area["approximate"] is False
    assert next(e for e in log.values() if e["cmd"] == "knew" and e["args"][2] == "A")["result"]["enemies"]
    for start, accepted in [(10.03125, True), (10.43125, True), (round(10.03125, 1), False)]:
        point = {"polarity": "improve", "theme": "trading", "title": "Hold position",
                 "body": f"You held {area['name']}.",
                 "instances": [{"round": 5, "t": start, "caption": "", "evidence": list(log)}]}
        problems = checker.check_point(point, log, pattern_snap, ("trading",), 0)
        assert (problems == []) is accepted, problems


@pytest.mark.parametrize("command, slot", [
    ("deaths", 0), ("kills", 0), ("teammate_deaths", 5),
    ("teammate_deaths", 0), ("deaths", 1),
])
def test_pattern_lead_up_uses_start_evidence_with_real_playbook(pattern_snap, tmp_path, command, slot):
    book = playbook.load()
    assert "starts about 5 seconds before the first damage" in book.text
    text = " ".join(prompt.run_prompt(book, {}).split())
    assert "Rows are discovery pointers" in text
    row = ask(pattern_snap, tmp_path, command, [str(slot)])["moments"][0]
    start = max(0.0, row["t"] - 5.0)
    row_ids = [row[key] for key in ("timeline_id", "where_id", "knew_a_id", "knew_b_id")]
    moment = ask(pattern_snap, tmp_path, "moment", [str(row["round"]), str(start)])
    side = pattern_snap.view(row["round"]).side(slot)
    knew = next(e for e in moment["entries"] if e["cmd"] == "knew" and e["result"]["side"] == side)
    where = next(e for e in moment["entries"] if e["cmd"] == "where")
    assert where["result"]["t"] == max(0.0, start - 0.5)
    own = next(p for p in where["result"]["players"] if p["slot"] == slot)
    caption = f"You held {own['area']['name']}." if own.get("area") else "You held position."
    log = query.read_log(tmp_path)
    trusted = checker.trusted_log(log, pattern_snap, {})
    assert trusted == log
    point = {"polarity": "improve", "theme": "trading", "title": "Hold together",
             "body": "Keep support close.", "instances": [
                 {"round": row["round"], "t": start, "t1": row["t"],
                  "caption": caption, "evidence": row_ids + [knew["id"], where["id"]]}]}
    assert checker.check_point(point, trusted, pattern_snap, book.themes, slot) == []
    point["instances"][0]["evidence"] = row_ids
    problems = checker.check_point(point, trusted, pattern_snap, book.themes, slot)
    assert any("must cite a knew answer" in problem for problem in problems), problems
    assert not any("outside this moment" in problem for problem in problems), problems


def test_brief_writes_subject_and_explicit_player_overrides_it(pattern_snap, tmp_path):
    _, flags = runner._brief_with_flag_ids(pattern_snap, 0, tmp_path)
    assert json.loads((tmp_path / "subject.json").read_text(encoding="utf-8")) == {"slot": 0}
    assert len(ask(pattern_snap, tmp_path, "deaths")["moments"]) == 2
    result = ask(pattern_snap, tmp_path, "deaths", ["P1#T1"])
    assert [(r["round"], r["t"]) for r in result["moments"]] == [(5, 0.25)]
    log = query.read_log(tmp_path)
    assert checker.trusted_log(log, pattern_snap, flags) == log


def test_patterns_and_plain_queries_share_a_write_q_batch(pattern_snap, tmp_path, capsys):
    (tmp_path / "subject.json").write_text('{"slot":0}', encoding="utf-8")
    script = coach_report.write_q(tmp_path, tmp_path / "python.exe").read_text(encoding="utf-8")
    words = shlex.split(script.splitlines()[1])
    assert words[2:4] == ["-X", "utf8"]
    options = words[5:-1]
    args = [*options, "deaths", "+", "timeline", "9", "7", "7",
            "+", "moment", "5", "0.25", "+", "kills", "0", "+", "teammate_deaths"]
    assert coach_query.main(args) == 0
    text = capsys.readouterr().out
    log = query.read_log(tmp_path)
    assert len(log) == 21
    assert checker.trusted_log(log, pattern_snap, {}) == log
    assert "10.03125" in text
    assert query.show(log["q9"]) in text
    for i in range(10, 14):
        assert query.show(log[f"q{i}"]) in text


@pytest.mark.parametrize("command, args", [
    ("deaths", []), ("deaths", ["0", "1"]), ("kills", ["missing"]),
    ("moment", ["5"]), ("moment", ["5", "nan"]), ("moment", ["5", "-1"]),
    ("moment", ["99", "1"]), ("moment", ["5", "61"]),
])
def test_bad_pattern_arguments_print_errors_and_continue(pattern_snap, tmp_path, capsys, command, args):
    status = coach_query.main(["--compact", "--run", str(tmp_path), "--", command, *args, "+", "timeline", "9"])
    assert status == 1
    output = capsys.readouterr().out
    assert "error:" in output
    if command == "deaths" and not args:
        assert "player" in output and "subject.json" in output
    assert len(query.read_log(tmp_path)) == 1
    assert "q1 timeline" in output

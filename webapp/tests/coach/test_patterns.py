import json
import shlex

import pytest

from app.coach import areas, checker, query, runner, snapfile, snapshot
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
        assert set(row) == {"round", "t", "timeline_id", "where_id", "knew_a_id", "knew_b_id"}
        entries = [log[row[key]] for key in ("timeline_id", "where_id", "knew_a_id", "knew_b_id")]
        assert [(e["cmd"], e["args"]) for e in entries] == [
            ("timeline", [n, t, t]), ("where", [n, max(0.0, t - 0.5)]),
            ("knew", [n, t, "A"]), ("knew", [n, t, "B"]),
        ]
        assert all(e["cmd"] in query.COMMANDS for e in entries)
        assert all("enemies" in e["result"] for e in entries[2:])
        assert entries[0]["result"]["events"][0]["kind"] == "kill"


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
    options = shlex.split(script.splitlines()[1])[3:-1]
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

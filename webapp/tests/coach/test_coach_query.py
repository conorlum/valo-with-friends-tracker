import json
import shlex
import sys

import pytest

from app.coach import checker, query, snapfile
from scripts import coach_query, coach_report
from tests.coach.test_query import snap


@pytest.fixture
def run_dir(snap, tmp_path):
    yield tmp_path
    log = query.read_log(tmp_path)
    assert checker.trusted_log(log, snap, {}) == log


def invoke(monkeypatch, run_dir, parts, *, compact=True):
    options = ["--run", str(run_dir)]
    if compact:
        options.append("--compact")
    monkeypatch.setattr(sys, "argv", ["coach_query.py", *options, "--", *parts])
    return coach_query.main()


def test_three_queries_keep_order_ids_and_load_snapshot_once(run_dir, monkeypatch, capsys):
    query.run(snapfile.load(run_dir), run_dir, "timeline", ["1"])
    loads = []
    original_load = snapfile.load

    def load(directory):
        loads.append(directory)
        return original_load(directory)

    monkeypatch.setattr(snapfile, "load", load)
    parts = "where 1 5 + knew 1 5 A + distance 1 5 P0 P5".split()
    assert invoke(monkeypatch, run_dir, parts) == 0
    assert loads == [run_dir]
    log = query.read_log(run_dir)
    assert list(log) == ["q1", "q2", "q3", "q4"]
    assert [row["cmd"] for row in log.values()] == ["timeline", "where", "knew", "distance"]
    assert all("error" not in row["result"] for row in log.values())
    assert capsys.readouterr().out == "\n".join(query.show(log[key]) for key in ("q2", "q3", "q4")) + "\n"


@pytest.mark.parametrize("compact", [True, False])
def test_bad_middle_answer_is_logged_and_last_query_runs(run_dir, monkeypatch, capsys, compact):
    parts = "where 1 5 + where 99 5 + knew 1 5 A".split()
    assert invoke(monkeypatch, run_dir, parts, compact=compact) == 0
    log = query.read_log(run_dir)
    assert list(log) == ["q1", "q2", "q3"]
    assert "error" in log["q2"]["result"]
    assert "error" not in log["q3"]["result"]
    output = capsys.readouterr().out
    if compact:
        assert output == "\n".join(query.show(entry) for entry in log.values()) + "\n"
    else:
        assert [json.loads(line) for line in output.splitlines()] == [
            {"id": key, **entry["result"]} for key, entry in log.items()
        ]


@pytest.mark.parametrize("parts, positions", [
    ("+ where 1 5", [1]),
    ("where 1 5 + + knew 1 5 A", [2]),
    ("where 1 5 +", [2]),
    ("+ + where 1 5 +", [1, 2, 4]),
])
def test_empty_parts_are_reported_and_other_parts_run(run_dir, monkeypatch, capsys, parts, positions):
    assert invoke(monkeypatch, run_dir, parts.split()) == 1
    log = query.read_log(run_dir)
    expected_count = 2 if "knew" in parts else 1
    assert list(log) == [f"q{number}" for number in range(1, expected_count + 1)]
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("error:")] == [
        f"error: empty query at position {position}" for position in positions
    ]
    assert all(query.show(entry) in "\n".join(lines) for entry in log.values())


@pytest.mark.parametrize("compact", [True, False])
def test_single_query_keeps_existing_output(run_dir, monkeypatch, capsys, compact):
    assert invoke(monkeypatch, run_dir, ["where", "1", "5"], compact=compact) == 0
    entry = query.read_log(run_dir)["q1"]
    expected = query.show(entry) if compact else json.dumps({"id": "q1", **entry["result"]})
    assert capsys.readouterr().out == expected + "\n"


def test_default_output_is_one_json_object_per_query(run_dir, monkeypatch, capsys):
    parts = "where 1 5 + knew 1 5 A + distance 1 5 P0 P5".split()
    assert invoke(monkeypatch, run_dir, parts, compact=False) == 0
    log = query.read_log(run_dir)
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"id": key, **entry["result"]} for key, entry in log.items()
    ]
    assert len(log) == 3


@pytest.mark.parametrize("source", ["plan", "write_q"])
def test_option_orders_and_explicit_argv(run_dir, source, capsys):
    if source == "plan":
        options = ["--compact", "--run", str(run_dir), "--"]
    else:
        script = coach_report.write_q(run_dir, run_dir / "python.exe").read_text(encoding="utf-8")
        options = shlex.split(script.splitlines()[1])[3:-1]
    assert coach_query.main([*options, *"where 1 5 + knew 1 5 A".split()]) == 0
    log = query.read_log(run_dir)
    assert list(log) == ["q1", "q2"]
    assert capsys.readouterr().out == "\n".join(query.show(entry) for entry in log.values()) + "\n"


@pytest.mark.parametrize("parts", [
    ["control", "1", "0", "10", "A + B"],
    ["distance", "1", "5", "P0+P1", "P5"],
    ["distance", "1", "5", "--run", "P5"],
    ["--run", "somewhere"],
    ["where", "1", "5", "--compact"],
])
def test_only_lone_plus_splits_and_model_options_are_plain_arguments(run_dir, monkeypatch, capsys, parts):
    assert invoke(monkeypatch, run_dir, parts) == 0
    log = query.read_log(run_dir)
    assert list(log) == ["q1"]
    assert log["q1"]["cmd"] == parts[0]
    assert log["q1"]["args"] == parts[1:]
    assert capsys.readouterr().out == query.show(log["q1"]) + "\n"


def test_second_run_before_command_is_refused(run_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["coach_query.py", "--run", str(run_dir),
                                      "--run", str(run_dir / "other"), "where", "1", "5"])
    assert coach_query.main() == 2
    assert "--run may be given only once" in capsys.readouterr().err
    assert query.read_log(run_dir) == {}


def test_options_after_command_are_plain_arguments_without_double_dash(run_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["coach_query.py", "--run", str(run_dir),
                                      "distance", "1", "5", "--run", "P5"])
    assert coach_query.main() == 0
    entry = query.read_log(run_dir)["q1"]
    assert entry["args"] == ["1", "5", "--run", "P5"]
    assert json.loads(capsys.readouterr().out)["id"] == "q1"


@pytest.mark.parametrize("compact", [True, False])
def test_fault_prints_traceback_and_later_queries_still_run(run_dir, monkeypatch, capsys, compact):
    original_run = query.run

    def run(snap, directory, command, args):
        if command == "fault":
            raise RuntimeError("synthetic query fault")
        return original_run(snap, directory, command, args)

    monkeypatch.setattr(query, "run", run)
    parts = "where 1 5 + fault + knew 1 5 A".split()
    assert invoke(monkeypatch, run_dir, parts, compact=compact) == 1
    log = query.read_log(run_dir)
    assert [entry["cmd"] for entry in log.values()] == ["where", "knew"]
    captured = capsys.readouterr()
    assert "error: part 2 failed: RuntimeError\n" in captured.out
    assert "Traceback (most recent call last)" in captured.err
    assert "RuntimeError: synthetic query fault" in captured.err
    if compact:
        assert captured.out.endswith(query.show(log["q2"]) + "\n")
    else:
        assert json.loads(captured.out.splitlines()[-1])["id"] == "q2"

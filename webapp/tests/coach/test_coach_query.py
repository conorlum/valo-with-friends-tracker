import builtins
import io
import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

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
        words = shlex.split(script.splitlines()[1])
        assert words[2:4] == ["-X", "utf8"]
        options = words[5:-1]
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


@pytest.mark.parametrize("parts, expected_commands", [
    ("where 1 5 + timeline 1", ["where", "timeline"]),
    ("deaths 0 + timeline 1", ["timeline", "where", "knew", "knew", "timeline"]),
    ("moment 1 5 + timeline 1", ["timeline", "where", "knew", "knew", "timeline"]),
    ("knew 1 5 SUBJECT + timeline 1", ["knew", "timeline"]),
])
def test_direct_query_writes_utf8_under_filtered_child_env(snap, tmp_path, parts, expected_commands):
    # Synthetic name, deliberately outside cp1252. No inherited Python encoding settings.
    name = "\u6d4b\u8bd5#TOY"
    snap.context["players"]["0"]["name"] = name
    snapfile.save(snap.data, tmp_path)
    parts = parts.replace("SUBJECT", name)
    env = coach_report.child_env(os.environ)
    for key in list(env):
        if key.upper() in {"PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO"}:
            del env[key]
    done = subprocess.run([sys.executable, str(coach_query.WEBAPP_ROOT / "scripts" / "coach_query.py"),
                           "--run", str(tmp_path), "--compact", "--", *parts.split()],
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    output = done.stdout.decode("utf-8").replace("\r\n", "\n")
    assert name in output
    log = query.read_log(tmp_path)
    assert [entry["cmd"] for entry in log.values()] == expected_commands
    if parts.startswith("deaths"):
        assert "timeline_id" in output and "where_id" in output
    else:
        assert all(query.show(entry) in output for entry in log.values())
    assert query.show(next(reversed(log.values()))) in output


@pytest.mark.parametrize("compact", [True, False])
@pytest.mark.parametrize("command", ["where 1 5", "deaths 0", "moment 1 5"])
@pytest.mark.parametrize("fault", ["render", "print"])
def test_output_fault_is_per_part_and_later_queries_run(run_dir, monkeypatch, capsys, compact, command, fault):
    from app.coach import compact as compact_module

    if fault == "print":
        original = builtins.print
        target, attribute = coach_query, "print"
    elif not compact:
        # Patch only this module's JSON rendering, leaving query logging intact.
        original = json.dumps
        target, attribute = coach_query, "json"
    elif command.startswith("deaths"):
        original = compact_module.render
        target, attribute = compact_module, "render"
    else:
        original = query.show
        target, attribute = query, "show"
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise UnicodeEncodeError("cp1252", "\u6d4b", 0, 1, "synthetic output fault")
        return original(*args, **kwargs)

    replacement = SimpleNamespace(dumps=fail_once) if fault == "render" and not compact else fail_once
    monkeypatch.setattr(target, attribute, replacement, raising=False)
    assert invoke(monkeypatch, run_dir, [*command.split(), "+", "timeline", "1"], compact=compact) == 1
    captured = capsys.readouterr()
    assert "error: part 1 failed: UnicodeEncodeError" in captured.out
    assert "UnicodeEncodeError" in captured.err
    last = list(query.read_log(run_dir).values())[-1]
    assert last["cmd"] == "timeline"
    if compact:
        assert captured.out.endswith(query.show(last) + "\n")
    else:
        assert json.loads(captured.out.splitlines()[-1])["id"] == last["id"]


@pytest.mark.parametrize("has_reconfigure", [True, False])
def test_embedded_stdout_can_lack_reconfigure(run_dir, monkeypatch, has_reconfigure):
    class Unconfigurable(io.StringIO):
        def reconfigure(self, **kwargs):
            raise io.UnsupportedOperation("synthetic embedded stream")

    stream = Unconfigurable() if has_reconfigure else io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    assert invoke(monkeypatch, run_dir, ["where", "1", "5"]) == 0
    assert query.show(query.read_log(run_dir)["q1"]) in stream.getvalue()
